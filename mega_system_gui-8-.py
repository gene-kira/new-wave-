import importlib
import subprocess
import sys
import threading
import asyncio
import json
import random
import math
import time
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# =========================
# MODEL PATHS / GGUF CONFIG
# =========================

MODEL_PATHS = {
    "llm_onnx_model_id": "gpt2",
    "llm_onnx_path": r"models/llm/gpt2.onnx",
    "vit_onnx_path": r"models/vit/vit.onnx",
    "vit_onnx_input_name": "input",
    "whisper_onnx_path": r"models/whisper/whisper.onnx",
    "whisper_onnx_input_name": "input",
}

GGUF_CONFIG = {
    "enabled": True,
    "model_path": r"models/gguf/llama-3-8b.gguf",
    "n_ctx": 4096,
    "base_n_gpu_layers": 35,
    "min_n_gpu_layers": 8,
    "n_threads": 8,
    "temperature": 0.7,
    "max_tokens": 128,
    "vram_thresholds": {
        "high": 16,
        "medium": 8,
        "low": 4,
    },
}

PLUGIN_DIR = "plugins"

# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
LOGGER = logging.getLogger("mega-system")

# =========================
# AUTOLOADER
# =========================

np = None
torch = None
transformers = None
whisper_lib = None
onnxruntime = None
matplotlib = None
Figure = None
FigureCanvasTkAgg = None
sf = None
librosa = None


def ensure_lib(module_name: str, pip_name: Optional[str] = None):
    pip_name = pip_name or module_name
    try:
        return importlib.import_module(module_name)
    except ImportError:
        try:
            LOGGER.info(f"[AUTOLOADER] Installing {pip_name} via pip...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
            return importlib.import_module(module_name)
        except Exception as e:
            LOGGER.error(f"[AUTOLOADER] Failed to install {pip_name}: {e}")
            return None


def ensure_lib_async(module_name: str, pip_name: Optional[str] = None, callback=None):
    def worker():
        mod = ensure_lib(module_name, pip_name)
        if callback:
            callback(mod)

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def lazy_np():
    global np
    if np is None:
        np = ensure_lib("numpy")
    return np


def lazy_torch():
    global torch
    if torch is None:
        torch = ensure_lib("torch")
    return torch


def lazy_transformers():
    global transformers
    if transformers is None:
        transformers = ensure_lib("transformers")
    return transformers


def lazy_whisper():
    global whisper_lib
    if whisper_lib is None:
        whisper_lib = ensure_lib("whisper")
    return whisper_lib


def lazy_onnxruntime():
    global onnxruntime
    if onnxruntime is None:
        onnxruntime = ensure_lib("onnxruntime")
    return onnxruntime


def lazy_matplotlib():
    global matplotlib, Figure, FigureCanvasTkAgg
    if matplotlib is None:
        plt_mod = ensure_lib("matplotlib")
        if plt_mod:
            matplotlib = plt_mod
            from matplotlib.figure import Figure as _Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as _Canvas

            Figure = _Figure
            FigureCanvasTkAgg = _Canvas
    return matplotlib


def lazy_soundfile():
    global sf
    if sf is None:
        sf = ensure_lib("soundfile", "pysoundfile")
    return sf


def lazy_librosa():
    global librosa
    if librosa is None:
        librosa = ensure_lib("librosa")
    return librosa


# =========================
# RUNTIME ENV
# =========================

class RuntimeEnv:
    def __init__(self):
        self._init_libs()
        self.has_torch = torch is not None
        self.has_cuda = bool(self.has_torch and torch.cuda.is_available())
        self.onnx_providers = self._detect_onnx_providers()
        self.vram_gb = self._detect_vram_gb()

    def _init_libs(self):
        global torch, transformers, whisper_lib, onnxruntime
        try:
            import torch as _torch
            torch = _torch
        except Exception:
            torch = None
        try:
            import transformers as _tf
            transformers = _tf
        except Exception:
            transformers = None
        try:
            import whisper as _wh
            whisper_lib = _wh
        except Exception:
            whisper_lib = None
        try:
            import onnxruntime as _ort
            onnxruntime = _ort
        except Exception:
            onnxruntime = None

    def _detect_onnx_providers(self):
        if not onnxruntime:
            return []
        try:
            providers = onnxruntime.get_available_providers()
        except Exception:
            return []
        if "CUDAExecutionProvider" in providers:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    def _detect_vram_gb(self):
        if not self.has_torch or not self.has_cuda:
            return 0.0
        try:
            total_bytes = torch.cuda.get_device_properties(0).total_memory
            return round(total_bytes / (1024 ** 3), 2)
        except Exception:
            return 0.0

    def summary(self):
        return {
            "torch": self.has_torch,
            "cuda": self.has_cuda,
            "onnx_providers": self.onnx_providers,
            "vram_gb": self.vram_gb,
        }


RUNTIME = RuntimeEnv()


def auto_tune_n_gpu_layers():
    vram = RUNTIME.vram_gb
    thresholds = GGUF_CONFIG["vram_thresholds"]
    base = GGUF_CONFIG["base_n_gpu_layers"]
    min_layers = GGUF_CONFIG["min_n_gpu_layers"]

    if vram >= thresholds["high"]:
        n_gpu = base
    elif vram >= thresholds["medium"]:
        n_gpu = max(int(base * 0.6), min_layers)
    elif vram >= thresholds["low"]:
        n_gpu = max(int(base * 0.3), min_layers)
    else:
        n_gpu = 0

    LOGGER.info(f"[GGUF] VRAM={vram}GB -> n_gpu_layers={n_gpu}")
    return n_gpu


# =========================
# MODEL TIER MANAGER + PLUGINS
# =========================

class ModelTierManager:
    def __init__(self):
        self.llm_model = None
        self.llm_tokenizer = None
        self.llm_name = None
        self.llm_backend = "stub"

        self.llm_onnx_session = None
        self.llm_onnx_tokenizer = None
        self.gguf_llm = None

        self.vit_model = None
        self.vit_processor = None
        self.vit_name = None
        self.vit_backend = "stub"
        self.vit_onnx_session = None

        self.whisper_model = None
        self.whisper_name = None
        self.whisper_backend = "stub"
        self.whisper_onnx_session = None

        self.plugins: List[Any] = []

        self._init_llm()
        self._init_vit()
        self._init_whisper()
        self._init_gguf_llm()
        self._init_llm_onnx()
        self._init_vit_onnx()
        self._init_whisper_onnx()
        self._discover_plugins()

    def _discover_plugins(self):
        if not os.path.isdir(PLUGIN_DIR):
            LOGGER.info(f"[PLUGINS] No plugin directory found at {PLUGIN_DIR}")
            return
        LOGGER.info(f"[PLUGINS] Scanning for plugins in {PLUGIN_DIR}...")
        for fname in os.listdir(PLUGIN_DIR):
            if not fname.endswith(".py"):
                continue
            mod_name = fname[:-3]
            try:
                spec = importlib.util.spec_from_file_location(
                    mod_name, os.path.join(PLUGIN_DIR, fname)
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    if hasattr(module, "register_models"):
                        module.register_models(self)
                        self.plugins.append(module)
                        LOGGER.info(f"[PLUGINS] Registered plugin: {mod_name}")
                    else:
                        LOGGER.info(f"[PLUGINS] No register_models() in {mod_name}")
            except Exception as e:
                LOGGER.warning(f"[PLUGINS] Failed to load plugin {fname}: {e}")

    # ---- LLM INIT ----
    def _init_llm(self):
        lazy_transformers()
        lazy_torch()
        if not transformers or not torch:
            self.llm_name = "stub"
            self.llm_backend = "stub"
            return

        heavy_models = [
            "meta-llama/Llama-3-8b",
            "mistralai/Mistral-7B-Instruct-v0.2",
        ]
        medium_models = [
            "EleutherAI/gpt-neo-1.3B",
            "EleutherAI/gpt-neo-125M",
        ]
        light_models = ["gpt2"]

        if not RUNTIME.has_cuda:
            search_order = [medium_models, light_models]
        else:
            search_order = [heavy_models, medium_models, light_models]

        for tier in search_order:
            for name in tier:
                try:
                    LOGGER.info(f"[LLM] Trying HF model: {name}")
                    self.llm_tokenizer = transformers.AutoTokenizer.from_pretrained(name)
                    self.llm_model = transformers.AutoModelForCausalLM.from_pretrained(
                        name,
                        torch_dtype=torch.float16 if RUNTIME.has_cuda else torch.float32,
                        device_map="auto" if RUNTIME.has_cuda else None,
                    )
                    self.llm_name = name
                    self.llm_backend = "hf"
                    LOGGER.info(f"[LLM] Loaded HF model: {name}")
                    return
                except Exception as e:
                    LOGGER.warning(f"[LLM] Failed HF model {name}: {e}")
        self.llm_name = "stub"
        self.llm_backend = "stub"

    def _init_llm_onnx(self):
        lazy_onnxruntime()
        lazy_transformers()
        if not onnxruntime or not transformers:
            return
        try:
            model_id = MODEL_PATHS["llm_onnx_model_id"]
            path = MODEL_PATHS["llm_onnx_path"]
            LOGGER.info(f"[LLM-ONNX] Initializing ONNX LLM from {path}...")
            self.llm_onnx_tokenizer = transformers.AutoTokenizer.from_pretrained(model_id)
            sess_options = onnxruntime.SessionOptions()
            self.llm_onnx_session = onnxruntime.InferenceSession(
                path,
                providers=RUNTIME.onnx_providers,
                sess_options=sess_options,
            )
            self.llm_backend = "onnx"
            if not self.llm_name:
                self.llm_name = f"{model_id}.onnx"
            LOGGER.info("[LLM-ONNX] ONNX LLM ready.")
        except Exception as e:
            LOGGER.warning(f"[LLM-ONNX] Failed to init ONNX LLM: {e}")

    def _init_gguf_llm(self):
        if not GGUF_CONFIG.get("enabled", False):
            return
        try:
            import llama_cpp

            LOGGER.info("[LLM-GGUF] Initializing GGUF LLM...")
            n_gpu_layers = auto_tune_n_gpu_layers()
            self.gguf_llm = llama_cpp.Llama(
                model_path=GGUF_CONFIG["model_path"],
                n_ctx=GGUF_CONFIG["n_ctx"],
                n_gpu_layers=n_gpu_layers,
                n_threads=GGUF_CONFIG["n_threads"],
            )
            self.llm_backend = "gguf"
            self.llm_name = GGUF_CONFIG["model_path"].split("/")[-1]
            LOGGER.info("[LLM-GGUF] GGUF LLM ready.")
        except Exception as e:
            LOGGER.warning(f"[LLM-GGUF] GGUF backend not available: {e}")

    # ---- ViT INIT ----
    def _init_vit(self):
        lazy_transformers()
        lazy_torch()
        if not transformers or not torch:
            self.vit_name = "stub"
            self.vit_backend = "stub"
            return
        for name in [
            "google/vit-large-patch32-384",
            "google/vit-base-patch16-224",
            "google/vit-small-patch16-224",
        ]:
            try:
                LOGGER.info(f"[ViT] Trying HF ViT: {name}")
                self.vit_processor = transformers.AutoImageProcessor.from_pretrained(name)
                self.vit_model = transformers.ViTModel.from_pretrained(name)
                if RUNTIME.has_cuda:
                    self.vit_model.cuda()
                self.vit_name = name
                self.vit_backend = "hf"
                LOGGER.info(f"[ViT] Loaded HF ViT: {name}")
                return
            except Exception as e:
                LOGGER.warning(f"[ViT] Failed HF ViT {name}: {e}")
        self.vit_name = "stub"
        self.vit_backend = "stub"

    def _init_vit_onnx(self):
        lazy_onnxruntime()
        if not onnxruntime:
            return
        try:
            path = MODEL_PATHS["vit_onnx_path"]
            LOGGER.info(f"[ViT-ONNX] Initializing ONNX ViT from {path}...")
            sess_options = onnxruntime.SessionOptions()
            self.vit_onnx_session = onnxruntime.InferenceSession(
                path,
                providers=RUNTIME.onnx_providers,
                sess_options=sess_options,
            )
            self.vit_backend = "onnx"
            if not self.vit_name:
                self.vit_name = path.split("/")[-1]
            LOGGER.info("[ViT-ONNX] ONNX ViT ready.")
        except Exception as e:
            LOGGER.warning(f"[ViT-ONNX] Failed to init ONNX ViT: {e}")

    # ---- Whisper INIT ----
    def _init_whisper(self):
        lazy_whisper()
        if not whisper_lib:
            self.whisper_name = "stub"
            self.whisper_backend = "stub"
            return
        for name in ["large", "base", "tiny"]:
            try:
                LOGGER.info(f"[Whisper] Trying Whisper model: {name}")
                self.whisper_model = whisper_lib.load_model(name)
                self.whisper_name = name
                self.whisper_backend = "hf"
                LOGGER.info(f"[Whisper] Loaded Whisper: {name}")
                return
            except Exception as e:
                LOGGER.warning(f"[Whisper] Failed Whisper {name}: {e}")
        self.whisper_name = "stub"
        self.whisper_backend = "stub"

    def _init_whisper_onnx(self):
        lazy_onnxruntime()
        if not onnxruntime:
            return
        try:
            path = MODEL_PATHS["whisper_onnx_path"]
            LOGGER.info(f"[Whisper-ONNX] Initializing ONNX Whisper from {path}...")
            sess_options = onnxruntime.SessionOptions()
            self.whisper_onnx_session = onnxruntime.InferenceSession(
                path,
                providers=RUNTIME.onnx_providers,
                sess_options=sess_options,
            )
            self.whisper_backend = "onnx"
            if not self.whisper_name:
                self.whisper_name = path.split("/")[-1]
            LOGGER.info("[Whisper-ONNX] ONNX Whisper ready.")
        except Exception as e:
            LOGGER.warning(f"[Whisper-ONNX] Failed to init ONNX Whisper: {e}")

    # ---- Audio helpers ----
    def _load_audio(self, audio_path: str):
        lazy_soundfile()
        np_mod = lazy_np()
        if not sf or not np_mod:
            return None, None
        try:
            audio, sr = sf.read(audio_path)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            return audio.astype("float32"), sr
        except Exception as e:
            LOGGER.warning(f"[Whisper-ONNX] Failed to load audio: {e}")
            return None, None

    def _compute_mel(self, audio: Any, sr: int):
        lazy_librosa()
        np_mod = lazy_np()
        if audio is None or sr is None or not librosa:
            return np_mod.zeros((1, 80, 300), dtype="float32")
        try:
            mel = librosa.feature.melspectrogram(
                y=audio,
                sr=sr,
                n_fft=1024,
                hop_length=256,
                n_mels=80,
                fmin=0,
                fmax=sr / 2,
            )
            mel_db = librosa.power_to_db(mel, ref=np_mod.max)
            if mel_db.shape[1] < 300:
                pad_width = 300 - mel_db.shape[1]
                mel_db = np_mod.pad(mel_db, ((0, 0), (0, pad_width)), mode="constant")
            else:
                mel_db = mel_db[:, :300]
            mel_db = mel_db[None, ...].astype("float32")
            return mel_db
        except Exception as e:
            LOGGER.warning(f"[Whisper-ONNX] Mel computation failed: {e}")
            return np_mod.zeros((1, 80, 300), dtype="float32")

    # ---- RUNNERS ----
    def run_llm(self, text: str) -> Dict[str, Any]:
        if self.llm_backend == "gguf" and self.gguf_llm:
            LOGGER.info("[LLM] Using GGUF backend.")
            out = self.gguf_llm(
                text,
                max_tokens=GGUF_CONFIG["max_tokens"],
                temperature=GGUF_CONFIG["temperature"],
            )
            return {
                "model": self.llm_name,
                "backend": "gguf",
                "output": out["choices"][0]["text"],
            }

        if self.llm_backend == "onnx" and self.llm_onnx_session and self.llm_onnx_tokenizer:
            LOGGER.info("[LLM] Using ONNX backend.")
            np_mod = lazy_np()
            tokens = self.llm_onnx_tokenizer(
                text, return_tensors="np", truncation=True, max_length=256
            )
            ort_inputs = {k: v for k, v in tokens.items()}
            outputs = self.llm_onnx_session.run(None, ort_inputs)
            logits = outputs[0]
            next_token_id = int(np_mod.argmax(logits[0, -1]))
            decoded = self.llm_onnx_tokenizer.decode(
                list(tokens["input_ids"][0]) + [next_token_id],
                skip_special_tokens=True,
            )
            return {"model": self.llm_name, "backend": "onnx", "output": decoded}

        if self.llm_backend == "hf" and self.llm_model and self.llm_tokenizer:
            LOGGER.info("[LLM] Using HF backend.")
            inputs = self.llm_tokenizer(text, return_tensors="pt", truncation=True, max_length=256)
            if RUNTIME.has_cuda:
                inputs = {k: v.cuda() for k, v in inputs.items()}
                self.llm_model.cuda()
            with torch.no_grad():
                outputs = self.llm_model.generate(**inputs, max_length=128)
            decoded = self.llm_tokenizer.decode(outputs[0], skip_special_tokens=True)
            return {"model": self.llm_name, "backend": "hf", "output": decoded}

        LOGGER.info("[LLM] Using stub backend.")
        return {"model": "LLM-stub", "backend": "stub", "output": f"Stub response for: {text[:60]}..."}

    def run_vit(self, image_path: str) -> Dict[str, Any]:
        if self.vit_backend == "onnx" and self.vit_onnx_session:
            LOGGER.info("[ViT] Using ONNX backend.")
            from PIL import Image
            img = Image.open(image_path).convert("RGB")
            np_mod = lazy_np()
            img_arr = np_mod.array(img).astype("float32") / 255.0
            img_arr = img_arr.transpose(2, 0, 1)[None, ...]
            input_name = MODEL_PATHS["vit_onnx_input_name"]
            outputs = self.vit_onnx_session.run(None, {input_name: img_arr})
            emb = outputs[0].mean(axis=1).tolist()[0]
            return {"model": self.vit_name, "backend": "onnx", "embedding": emb}

        if self.vit_backend == "hf" and self.vit_model and self.vit_processor:
            LOGGER.info("[ViT] Using HF backend.")
            from PIL import Image
            img = Image.open(image_path).convert("RGB")
            inputs = self.vit_processor(images=img, return_tensors="pt")
            if RUNTIME.has_cuda:
                inputs = {k: v.cuda() for k, v in inputs.items()}
                self.vit_model.cuda()
            with torch.no_grad():
                outputs = self.vit_model(**inputs)
            emb = outputs.last_hidden_state.mean(dim=1).tolist()[0]
            return {"model": self.vit_name, "backend": "hf", "embedding": emb}

        LOGGER.info("[ViT] Using stub backend.")
        return {"model": "ViT-stub", "backend": "stub", "embedding": [0.0] * 16}

    def run_whisper(self, audio_path: str) -> Dict[str, Any]:
        if self.whisper_backend == "onnx" and self.whisper_onnx_session:
            LOGGER.info("[Whisper] Using ONNX backend.")
            audio, sr = self._load_audio(audio_path)
            mel = self._compute_mel(audio, sr)
            input_name = MODEL_PATHS["whisper_onnx_input_name"]
            _ = self.whisper_onnx_session.run(None, {input_name: mel})
            transcript = "ONNX-Whisper transcript (mel-based best-guess stub)"
            return {"model": self.whisper_name, "backend": "onnx", "transcript": transcript}

        if self.whisper_backend == "hf" and self.whisper_model:
            LOGGER.info("[Whisper] Using HF backend.")
            result = self.whisper_model.transcribe(audio_path)
            return {"model": self.whisper_name, "backend": "hf", "transcript": result.get("text", "")}

        LOGGER.info("[Whisper] Using stub backend.")
        return {"model": "Whisper-stub", "backend": "stub", "transcript": "Stub transcript."}


# =========================
# BACKEND ARCHITECTURE
# =========================

class MultiModalInputs:
    def __init__(self):
        self.state: Dict[str, Any] = {}

    def ingest_text(self, text: str) -> Dict[str, Any]:
        tokens = text.split()
        length = len(tokens)
        avg_len = sum(len(t) for t in tokens) / length if length > 0 else 0
        return {
            "type": "text",
            "raw": text,
            "tokens": tokens,
            "length": length,
            "avg_token_length": avg_len,
        }

    def ingest_file(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            content = ""
        return self.ingest_text(content)

    def ingest_sensors(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"type": "sensors", "data": sensor_data}

    def text_normalization(self, text_obj: Dict[str, Any]) -> Dict[str, Any]:
        text = text_obj.get("raw", "").lower()
        return {**text_obj, "normalized": text}

    def feature_extraction(self, data_obj: Dict[str, Any]) -> Dict[str, Any]:
        text = data_obj.get("normalized", data_obj.get("raw", ""))
        vowels = sum(1 for c in text if c in "aeiou")
        consonants = sum(1 for c in text if c.isalpha() and c not in "aeiou")
        return {
            "type": "features",
            "source_type": data_obj.get("type"),
            "vowels": vowels,
            "consonants": consonants,
            "length": len(text),
        }

    def multi_sensor_integration(self, sensor_objs: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged = {}
        for s in sensor_objs:
            merged.update(s.get("data", {}))
        return {"type": "multi_sensor", "merged": merged}


class PreTrainedModels:
    def __init__(self, tier_manager: ModelTierManager):
        self.tier = tier_manager

    def large_language_model(self, text_features: Dict[str, Any]) -> Dict[str, Any]:
        text = text_features.get("normalized", text_features.get("raw", ""))
        return self.tier.run_llm(text)

    def vision_transformer(self, image_path: str) -> Dict[str, Any]:
        return self.tier.run_vit(image_path)

    def speech_recognition(self, audio_path: str) -> Dict[str, Any]:
        return self.tier.run_whisper(audio_path)

    def speech_transfer_recognition(self, audio_input: Dict[str, Any]) -> Dict[str, Any]:
        return {"model": "SpeechTransfer-stub", "representation": [0.1, 0.2, 0.3]}

    def time_series_analysis(self, series_input: Dict[str, Any]) -> Dict[str, Any]:
        series = series_input.get("series", [random.random() for _ in range(10)])
        trend = "up" if series[-1] > series[0] else "down"
        return {"model": "TimeSeries-lite", "trend": trend, "series": series}


class DataProcessingHub:
    def __init__(self):
        pass

    def data_normalization(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {"normalized": True, "data": data}

    def feature_extraction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        base = data.get("data", {})
        score = len(str(base)) % 100
        return {"hub_features": ["hf1", "hf2"], "score": score, "source": data}

    def multi_series_integration(self, series_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"integrated_series": series_list, "count": len(series_list)}


class ParallelComputationCore:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def hyperparameter_optimization(self, config_space: Dict[str, Any]) -> Dict[str, Any]:
        lr = self.config.get("lr", 0.001)
        batch = self.config.get("batch_size", 32)
        return {"best_config": {"lr": lr, "batch_size": batch}}

    def deep_neural_networks(self, features: Dict[str, Any]) -> Dict[str, Any]:
        score = features.get("score", 0)
        latent = math.tanh(score / 10.0)
        return {"dnn_output": latent}

    def dynamic_memory_system(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"memory_state": "updated", "context": context}

    def temporal_modulation(self, sequence: Dict[str, Any]) -> Dict[str, Any]:
        return {"temporal_pattern": "modulated", "sequence": sequence}

    def advanced_training_loop(self, data: Dict[str, Any]) -> Dict[str, Any]:
        epochs = self.config.get("epochs", 5)
        loss = round(1.0 / (epochs + 1), 4)
        return {"training_status": "converged", "metrics": {"loss": loss, "epochs": epochs}}


class GodSwarmNeural:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def neural_network_swarm_architecture(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        swarm_size = self.config.get("swarm_size", 16)
        return {"swarm_state": "initialized", "size": swarm_size, "inputs": inputs}

    def advanced_training(self, swarm_state: Dict[str, Any]) -> Dict[str, Any]:
        return {"swarm_state": "trained", "size": swarm_state.get("size", 0), "details": swarm_state}

    def agents(self, swarm_state: Dict[str, Any]) -> Dict[str, Any]:
        size = swarm_state.get("size", 0)
        agents = [f"agent_{i}" for i in range(size)]
        return {"agents": agents, "swarm_state": swarm_state}

    def evolutionary_algorithms(self, population: Dict[str, Any]) -> Dict[str, Any]:
        gen = self.config.get("generations", 10)
        return {"evolved_population": f"generation_{gen}", "population": population}

    def swarm_intelligence(self, environment: Dict[str, Any]) -> Dict[str, Any]:
        decision = random.choice(["explore", "exploit", "hold"])
        return {"swarm_decision": decision, "environment": environment}

    def quantum_engineered_swarm_architecture(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {"quantum_swarm": "hybrid_architecture", "inputs": inputs}


class QuantumCore:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def quantum_inspired_decision_engine(self, state: Dict[str, Any]) -> Dict[str, Any]:
        temp = self.config.get("temperature", 0.7)
        choice = "accept" if random.random() < temp else "reject"
        return {"decision": choice, "state": state}

    def probabilistic_computing(self, data: Dict[str, Any]) -> Dict[str, Any]:
        probs = [random.random() for _ in range(2)]
        s = sum(probs)
        probs = [p / s for p in probs]
        return {"probabilities": probs, "data": data}

    def quantum_optimization(self, objective: Dict[str, Any]) -> Dict[str, Any]:
        return {"optimized_solution": "quantum_optimum"}

    def computation_optimization(self, pipeline: Dict[str, Any]) -> Dict[str, Any]:
        return {"optimized_pipeline": True, "pipeline": pipeline}

    def entanglement_processing(self, linked_states: Dict[str, Any]) -> Dict[str, Any]:
        return {"entangled_state": "processed", "linked_states": linked_states}

    def stochastic_decision_model(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = random.choice(["action_A", "action_B", "action_C"])
        return {"stochastic_decision": action, "inputs": inputs}

    def quantum_inspired_probabilistic_decision_engine(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return {"probabilistic_decision": "hybrid_quantum", "state": state}


class ContextualKnowledgeBase:
    def __init__(self):
        self.knowledge: List[Dict[str, Any]] = []

    def deep_world_neural_feedbacks(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        self.knowledge.append(signal)
        return {"feedback": "world_model_update", "signal": signal}

    def predictive_reactions(self, context: Dict[str, Any]) -> Dict[str, Any]:
        prediction = random.choice(["good", "bad", "neutral"])
        return {"prediction": prediction, "context": context}

    def outcome_evaluation(self, action: Dict[str, Any]) -> Dict[str, Any]:
        evaluation = random.choice(["success", "failure"])
        return {"evaluation": evaluation, "action": action}

    def real_time_decision_system(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        decision = random.choice(["proceed", "abort", "delay"])
        return {"decision": decision, "inputs": inputs}

    def multi_agent_coordination(self, agents_state: Dict[str, Any]) -> Dict[str, Any]:
        return {"coordination_plan": "coordinated", "agents_state": agents_state}


class EthicalGuidelines:
    def __init__(self):
        pass

    def apply_ethics(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        return {"ethical_decision": decision, "status": "checked"}


class SecurityMitigation:
    def __init__(self):
        pass

    def security_mitigation(self, system_state: Dict[str, Any]) -> Dict[str, Any]:
        return {"secure_state": True, "system_state": system_state}


class SimulationEngine:
    def __init__(self):
        pass

    def simulation_engine(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        return {"simulation_result": "simulated_outcome", "scenario": scenario}


class OutputModules:
    def __init__(self):
        pass

    def image_synthesis(self, representation: Dict[str, Any]) -> Dict[str, Any]:
        return {"image": "synthetic_image.png", "source": representation}

    def audio_creation(self, representation: Dict[str, Any]) -> Dict[str, Any]:
        return {"audio": "synthetic_audio.wav", "source": representation}


class RealWorldTraining:
    def __init__(self):
        pass

    def trained_on_real_world_data(self, dataset_info: Dict[str, Any]) -> Dict[str, Any]:
        return {"training_source": "real_world", "dataset": dataset_info}


# =========================
# HYBRID BRAIN + ORGANS + META-STATES
# =========================

class AugmentationEngine:
    def __init__(self):
        self.history: List[Dict[str, Any]] = []

    def augment_input(self, text: str) -> Dict[str, Any]:
        # Non-madness augmentation: light paraphrase + tagging
        tags = []
        if "water" in text.lower():
            tags.append("fluid-dynamics")
        if "heat" in text.lower():
            tags.append("thermal")
        if len(text) > 200:
            tags.append("long-context")
        aug = {"text": text, "tags": tags}
        self.history.append(aug)
        return aug

    def augment_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # Simple enrichment: add timestamp + stability score
        enriched = dict(state)
        enriched["aug_timestamp"] = time.time()
        enriched["stability_score"] = random.uniform(0.0, 1.0)
        self.history.append(enriched)
        return enriched


class OrganBase:
    def __init__(self, name: str):
        self.name = name
        self.health = 1.0
        self.last_check = time.time()

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self.last_check = time.time()
        return {"name": self.name, "health": self.health, "context": context}


class DeepRamOrgan(OrganBase):
    def __init__(self):
        super().__init__("DeepRam")
        self.memory_buffer: List[str] = []

    def store(self, text: str):
        self.memory_buffer.append(text)
        if len(self.memory_buffer) > 256:
            self.memory_buffer = self.memory_buffer[-256:]


class BackupOrgan(OrganBase):
    def __init__(self):
        super().__init__("Backup")
        self.snapshots: List[Dict[str, Any]] = []

    def snapshot(self, state: Dict[str, Any]):
        self.snapshots.append({"time": time.time(), "state": state})
        if len(self.snapshots) > 32:
            self.snapshots = self.snapshots[-32:]


class NetworkWatcherOrgan(OrganBase):
    def __init__(self):
        super().__init__("NetworkWatcher")

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        status = random.choice(["ok", "latent", "noisy"])
        return {"name": self.name, "health": self.health, "net_status": status}


class GPUCacheOrgan(OrganBase):
    def __init__(self):
        super().__init__("GPUCache")
        self.cache_hits = 0
        self.cache_misses = 0

    def record_hit(self):
        self.cache_hits += 1

    def record_miss(self):
        self.cache_misses += 1


class ThermalOrgan(OrganBase):
    def __init__(self):
        super().__init__("Thermal")
        self.temperature = 40.0

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self.temperature += random.uniform(-0.5, 0.5)
        self.temperature = max(30.0, min(90.0, self.temperature))
        return {"name": self.name, "temp": self.temperature}


class DiskOrgan(OrganBase):
    def __init__(self):
        super().__init__("Disk")
        self.io_load = 0.1

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self.io_load = max(0.0, min(1.0, self.io_load + random.uniform(-0.05, 0.05)))
        return {"name": self.name, "io_load": self.io_load}


class VRAMOrgan(OrganBase):
    def __init__(self):
        super().__init__("VRAM")
        self.vram_gb = RUNTIME.vram_gb

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"name": self.name, "vram_gb": self.vram_gb}


class AICoachOrgan(OrganBase):
    def __init__(self):
        super().__init__("AICoach")

    def advise(self, regime: str) -> str:
        if regime == "Hyper-Flow":
            return "Throttle slightly; maintain clarity."
        if regime == "Sentinel":
            return "Observe; do not overreact."
        if regime == "Recovery-Flow":
            return "Stabilize; avoid heavy loads."
        if regime == "Deep-Dream":
            return "Explore patterns; low-risk experimentation."
        return "Stay adaptive."


class SwarmNodeOrgan(OrganBase):
    def __init__(self):
        super().__init__("SwarmNode")
        self.neighbors = random.randint(1, 8)


class Back4BloodAnalyzerOrgan(OrganBase):
    def __init__(self):
        super().__init__("Back4BloodAnalyzer")
        self.last_risk = 0.0

    def analyze(self, context: Dict[str, Any]) -> float:
        self.last_risk = random.uniform(0.0, 1.0)
        return self.last_risk


class SelfIntegrityOrgan(OrganBase):
    def __init__(self):
        super().__init__("SelfIntegrity")
        self.integrity_score = 1.0

    def check(self, signals: Dict[str, Any]) -> Dict[str, Any]:
        drift = random.uniform(-0.02, 0.01)
        self.integrity_score = max(0.0, min(1.0, self.integrity_score + drift))
        return {"name": self.name, "integrity": self.integrity_score, "signals": signals}


class HybridBrain:
    META_STATES = ["Hyper-Flow", "Sentinel", "Recovery-Flow", "Deep-Dream"]

    def __init__(self):
        self.current_state = "Sentinel"
        self.previous_state = None
        self.momentum = 0.0
        self.meta_confidence = 0.5
        self.pattern_memory: List[Dict[str, Any]] = []
        self.best_guess_trace: List[str] = []

        self.deep_ram = DeepRamOrgan()
        self.backup = BackupOrgan()
        self.network_watcher = NetworkWatcherOrgan()
        self.gpu_cache = GPUCacheOrgan()
        self.thermal = ThermalOrgan()
        self.disk = DiskOrgan()
        self.vram = VRAMOrgan()
        self.ai_coach = AICoachOrgan()
        self.swarm_node = SwarmNodeOrgan()
        self.back4blood = Back4BloodAnalyzerOrgan()
        self.self_integrity = SelfIntegrityOrgan()

        self.augmentation = AugmentationEngine()

    def _regime_detection(self, metrics: Dict[str, Any]) -> str:
        loss = metrics.get("loss", 0.1)
        epochs = metrics.get("epochs", 1)
        temp = self.thermal.temperature
        risk = self.back4blood.last_risk

        if loss < 0.05 and temp < 70 and risk < 0.5:
            return "Hyper-Flow"
        if temp > 80 or risk > 0.8:
            return "Recovery-Flow"
        if loss > 0.2:
            return "Sentinel"
        return "Deep-Dream"

    def _meta_state_transition_allowed(self, target: str) -> bool:
        src = self.current_state
        if src == target:
            return True
        if src == "Hyper-Flow" and target == "Sentinel":
            return self.thermal.temperature > 60
        if src == "Sentinel" and target == "Hyper-Flow":
            return self.self_integrity.integrity_score > 0.7
        if src == "Recovery-Flow" and target == "Deep-Dream":
            return self.back4blood.last_risk < 0.3
        return True

    def _update_momentum(self, target: str):
        if target == self.current_state:
            self.momentum = min(1.0, self.momentum + 0.05)
        else:
            self.momentum = max(0.0, self.momentum - 0.1)

    def _reasoning_heatmap(self, signals: Dict[str, Any]) -> Dict[str, float]:
        # Simple normalized weights for subsystems
        weights = {
            "core": random.uniform(0.2, 1.0),
            "swarm": random.uniform(0.2, 1.0),
            "quantum": random.uniform(0.2, 1.0),
            "kb": random.uniform(0.2, 1.0),
            "ethics": random.uniform(0.2, 1.0),
            "security": random.uniform(0.2, 1.0),
        }
        s = sum(weights.values())
        return {k: v / s for k, v in weights.items()}

    def _best_guess_engine(self, llm_out: Dict[str, Any], whisper_out: Optional[Dict[str, Any]]) -> str:
        text = llm_out.get("output", "")
        transcript = whisper_out.get("transcript", "") if whisper_out else ""
        guess = text
        if transcript and len(transcript) > len(text):
            guess = transcript
        if transcript and text:
            guess = f"[FUSED] {text[:80]} || {transcript[:80]}"
        self.best_guess_trace.append(guess[:120])
        if len(self.best_guess_trace) > 16:
            self.best_guess_trace = self.best_guess_trace[-16:]
        return guess

    def tick_organs(self, context: Dict[str, Any]) -> Dict[str, Any]:
        organ_states = {
            "deep_ram": self.deep_ram.tick(context),
            "backup": self.backup.tick(context),
            "network": self.network_watcher.tick(context),
            "gpu_cache": self.gpu_cache.tick(context),
            "thermal": self.thermal.tick(context),
            "disk": self.disk.tick(context),
            "vram": self.vram.tick(context),
            "swarm_node": self.swarm_node.tick(context),
        }
        risk = self.back4blood.analyze(context)
        integrity = self.self_integrity.check({"risk": risk, "organs": organ_states})
        organ_states["back4blood"] = {"risk": risk}
        organ_states["self_integrity"] = integrity
        return organ_states

    def update_meta_state(self, metrics: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        organ_states = self.tick_organs(context)
        target = self._regime_detection(metrics)
        allowed = self._meta_state_transition_allowed(target)
        self._update_momentum(target)

        if allowed:
            self.previous_state = self.current_state
            self.current_state = target

        self.meta_confidence = max(0.1, min(0.99, self.meta_confidence + random.uniform(-0.05, 0.05)))

        self.pattern_memory.append(
            {
                "time": time.time(),
                "state": self.current_state,
                "metrics": metrics,
                "organs": organ_states,
            }
        )
        if len(self.pattern_memory) > 64:
            self.pattern_memory = self.pattern_memory[-64:]

        heatmap = self._reasoning_heatmap({"metrics": metrics, "organs": organ_states})
        advice = self.ai_coach.advise(self.current_state)

        return {
            "meta_state": self.current_state,
            "previous_state": self.previous_state,
            "momentum": self.momentum,
            "meta_confidence": self.meta_confidence,
            "organs": organ_states,
            "heatmap": heatmap,
            "coach_advice": advice,
        }

    def augment_lifecycle(self, text: str, pipeline_state: Dict[str, Any]) -> Dict[str, Any]:
        aug_in = self.augmentation.augment_input(text)
        aug_state = self.augmentation.augment_state(pipeline_state)
        return {"aug_input": aug_in, "aug_state": aug_state}


# =========================
# ORCHESTRATOR (ASYNC + HYBRID BRAIN)
# =========================

class MegaSystemOrchestrator:
    def __init__(self, config: Dict[str, Any], tier_manager: ModelTierManager):
        self.config = config
        self.tier = tier_manager
        self.inputs = MultiModalInputs()
        self.models = PreTrainedModels(self.tier)
        self.hub = DataProcessingHub()
        self.core = ParallelComputationCore(config.get("core", {}))
        self.swarm = GodSwarmNeural(config.get("swarm", {}))
        self.quantum = QuantumCore(config.get("quantum", {}))
        self.kb = ContextualKnowledgeBase()
        self.ethics = EthicalGuidelines()
        self.security = SecurityMitigation()
        self.sim = SimulationEngine()
        self.outputs = OutputModules()
        self.training = RealWorldTraining()
        self.brain = HybridBrain()

    def _stamp(self, t0, label):
        t1 = time.time()
        LOGGER.info(f"[PROFILE] {label}: {(t1 - t0):.3f}s")
        return t1

    async def run_full_pipeline_async(self, text: str, image_path: Optional[str] = None, audio_path: Optional[str] = None) -> Dict[str, Any]:
        t0 = time.time()
        LOGGER.info("[PIPELINE] Starting async full pipeline.")

        text_raw = self.inputs.ingest_text(text); t0 = self._stamp(t0, "ingest_text")
        text_norm = self.inputs.text_normalization(text_raw); t0 = self._stamp(t0, "text_normalization")
        text_feat = self.inputs.feature_extraction(text_norm); t0 = self._stamp(t0, "feature_extraction")

        loop = asyncio.get_running_loop()

        llm_task = loop.run_in_executor(None, self.models.large_language_model, text_feat)
        vit_task = loop.run_in_executor(None, self.models.vision_transformer, image_path) if image_path else None
        whisper_task = loop.run_in_executor(None, self.models.speech_recognition, audio_path) if audio_path else None

        llm_out = await llm_task; t0 = self._stamp(t0, "llm")
        vit_out = await vit_task if vit_task else None
        if vit_task:
            t0 = self._stamp(t0, "vit")
        whisper_out = await whisper_task if whisper_task else None
        if whisper_task:
            t0 = self._stamp(t0, "whisper")

        hub_norm = self.hub.data_normalization(llm_out); t0 = self._stamp(t0, "hub_normalization")
        hub_feat = self.hub.feature_extraction(hub_norm); t0 = self._stamp(t0, "hub_feature_extraction")

        best_cfg = self.core.hyperparameter_optimization({}); t0 = self._stamp(t0, "hyperparameter_optimization")
        dnn_out = self.core.deep_neural_networks(hub_feat); t0 = self._stamp(t0, "deep_neural_networks")
        mem_state = self.core.dynamic_memory_system(dnn_out); t0 = self._stamp(t0, "dynamic_memory_system")
        temporal = self.core.temporal_modulation(mem_state); t0 = self._stamp(t0, "temporal_modulation")
        train_status = self.core.advanced_training_loop(temporal); t0 = self._stamp(t0, "advanced_training_loop")

        swarm_state = self.swarm.neural_network_swarm_architecture(train_status); t0 = self._stamp(t0, "swarm_architecture")
        swarm_trained = self.swarm.advanced_training(swarm_state); t0 = self._stamp(t0, "swarm_training")
        swarm_agents = self.swarm.agents(swarm_trained); t0 = self._stamp(t0, "swarm_agents")
        swarm_evolved = self.swarm.evolutionary_algorithms(swarm_agents); t0 = self._stamp(t0, "swarm_evolution")
        swarm_decision = self.swarm.swarm_intelligence({"agents": swarm_agents, "evolved": swarm_evolved}); t0 = self._stamp(t0, "swarm_intelligence")

        q_prob = self.quantum.probabilistic_computing(swarm_decision); t0 = self._stamp(t0, "quantum_probabilistic")
        q_decision = self.quantum.quantum_inspired_decision_engine(q_prob); t0 = self._stamp(t0, "quantum_decision_engine")
        q_stoch = self.quantum.stochastic_decision_model(q_decision); t0 = self._stamp(t0, "quantum_stochastic")

        feedback = self.kb.deep_world_neural_feedbacks(q_stoch); t0 = self._stamp(t0, "kb_feedback")
        prediction = self.kb.predictive_reactions(feedback); t0 = self._stamp(t0, "kb_predictive_reactions")
        outcome_eval = self.kb.outcome_evaluation(prediction); t0 = self._stamp(t0, "kb_outcome_evaluation")
        rt_decision = self.kb.real_time_decision_system(outcome_eval); t0 = self._stamp(t0, "kb_real_time_decision")
        coordination = self.kb.multi_agent_coordination({"decision": rt_decision, "agents": swarm_agents}); t0 = self._stamp(t0, "kb_multi_agent_coordination")

        ethical = self.ethics.apply_ethics(coordination); t0 = self._stamp(t0, "ethics")
        secure = self.security.security_mitigation(ethical); t0 = self._stamp(t0, "security")

        sim_result = self.sim.simulation_engine(secure); t0 = self._stamp(t0, "simulation_engine")

        img = self.outputs.image_synthesis(sim_result); t0 = self._stamp(t0, "image_synthesis")
        aud = self.outputs.audio_creation(sim_result); t0 = self._stamp(t0, "audio_creation")

        train_info = self.training.trained_on_real_world_data({"source": "placeholder_dataset"}); t0 = self._stamp(t0, "real_world_training")

        # HybridBrain lifecycle: regime detection, meta-state, augmentation, best-guess
        brain_state = self.brain.update_meta_state(train_status.get("metrics", {}), {
            "llm": llm_out,
            "swarm_decision": swarm_decision,
            "q_decision": q_decision,
        })
        best_guess = self.brain._best_guess_engine(llm_out, whisper_out)
        aug_hooks = self.brain.augment_lifecycle(text, {
            "train_status": train_status,
            "brain_state": brain_state,
        })

        LOGGER.info("[PIPELINE] Async full pipeline complete.")

        return {
            "best_config": best_cfg,
            "llm_out": llm_out,
            "vit_out": vit_out,
            "whisper_out": whisper_out,
            "train_status": train_status,
            "swarm_state": swarm_state,
            "swarm_agents": swarm_agents,
            "swarm_decision": swarm_decision,
            "q_decision": q_decision,
            "q_stoch": q_stoch,
            "coordination": coordination,
            "ethical": ethical,
            "secure": secure,
            "simulation": sim_result,
            "image_output": img,
            "audio_output": aud,
            "training_info": train_info,
            "brain_state": brain_state,
            "best_guess": best_guess,
            "augmentation_hooks": aug_hooks,
        }


# =========================
# TKINTER GUI (NERVOUS SYSTEM + ALTERED STATES)
# =========================

class MegaSystemApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ALL-IN-ONE MEGA SYSTEM v16 HYBRIDBRAIN")
        self.geometry("1500x850")
        self.configure(bg="#050510")

        self.config_state = {
            "core": {"lr": 0.001, "batch_size": 32, "epochs": 5},
            "swarm": {"swarm_size": 16, "generations": 10},
            "quantum": {"temperature": 0.7},
        }
        self.last_result: Optional[Dict[str, Any]] = None

        self.tier_manager = ModelTierManager()
        self.orchestrator = MegaSystemOrchestrator(self.config_state, self.tier_manager)

        self._setup_style()
        self._build_nerve_center()
        self._build_dashboard()
        self._build_config_panel()
        self._build_console()

        self._log(f"System initialized. LLM: {self.tier_manager.llm_name}, ViT: {self.tier_manager.vit_name}, Whisper: {self.tier_manager.whisper_name}")
        self._log(f"Runtime: {RUNTIME.summary()}")

        self.after(1000, self._preload_heavy_libs)

        self.async_loop = asyncio.new_event_loop()
        self.async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.async_thread.start()

    def _run_async_loop(self):
        asyncio.set_event_loop(self.async_loop)
        self.async_loop.run_forever()

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Cyber.TFrame", background="#050510", borderwidth=0)
        style.configure("Cyber.TLabelframe", background="#050510", foreground="#00ffcc", borderwidth=1, relief="solid")
        style.configure("Cyber.TLabelframe.Label", background="#050510", foreground="#00ffcc")
        style.configure("Cyber.TLabel", background="#050510", foreground="#00ffcc")
        style.configure("Cyber.TButton", background="#111122", foreground="#00ffcc", borderwidth=1)
        style.map("Cyber.TButton", background=[("active", "#222244")], foreground=[("active", "#00ffaa")])
        style.configure("Cyber.TEntry", fieldbackground="#111122", foreground="#00ffcc", insertcolor="#00ffcc")

    def _build_nerve_center(self):
        title = ttk.Label(
            self,
            text="Nerve Center – ALL-IN-ONE MEGA SYSTEM v16 (HybridBrain, Altered States)",
            style="Cyber.TLabel",
            font=("Segoe UI", 14, "bold"),
        )
        title.pack(pady=5)

        main_nb = ttk.Notebook(self)
        main_nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Main tab
        main_frame = ttk.Frame(main_nb, style="Cyber.TFrame")
        main_nb.add(main_frame, text="Main System")

        left_frame = ttk.Frame(main_frame, style="Cyber.TFrame")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_frame = ttk.Frame(main_frame, style="Cyber.TFrame")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        modules_frame = ttk.Frame(left_frame, style="Cyber.TFrame")
        modules_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        anim_frame = ttk.LabelFrame(left_frame, text="Data Flow Visualizer", style="Cyber.TLabelframe")
        anim_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, pady=5)

        self.canvas = tk.Canvas(anim_frame, bg="#050510", highlightthickness=0, height=260)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._build_animation_graph()

        control_frame = ttk.LabelFrame(right_frame, text="Control Panel", style="Cyber.TLabelframe")
        control_frame.pack(fill=tk.X, pady=5)

        ttk.Label(control_frame, text="Text Input:", style="Cyber.TLabel").pack(anchor="w")
        self.text_input = ttk.Entry(control_frame, style="Cyber.TEntry")
        self.text_input.insert(0, "Hello world from HYBRIDBRAIN system")
        self.text_input.pack(fill=tk.X, pady=2)

        self.image_path_var = tk.StringVar()
        self.audio_path_var = tk.StringVar()

        img_btn = ttk.Button(control_frame, text="Load Image for ViT", style="Cyber.TButton", command=self.load_image_file)
        img_btn.pack(fill=tk.X, pady=2)

        aud_btn = ttk.Button(control_frame, text="Load Audio for Whisper", style="Cyber.TButton", command=self.load_audio_file)
        aud_btn.pack(fill=tk.X, pady=2)

        run_button = ttk.Button(control_frame, text="Run Full Pipeline (Async)", style="Cyber.TButton", command=self.run_full_pipeline_async)
        run_button.pack(pady=5, fill=tk.X)

        save_btn = ttk.Button(control_frame, text="Save State", style="Cyber.TButton", command=self.save_state)
        save_btn.pack(fill=tk.X, pady=2)

        load_btn = ttk.Button(control_frame, text="Load State", style="Cyber.TButton", command=self.load_state)
        load_btn.pack(fill=tk.X, pady=2)

        clear_button = ttk.Button(control_frame, text="Clear Log", style="Cyber.TButton", command=self.clear_log)
        clear_button.pack(pady=2, fill=tk.X)

        log_frame = ttk.LabelFrame(right_frame, text="System Log", style="Cyber.TLabelframe")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.console_frame = ttk.LabelFrame(right_frame, text="Command Console", style="Cyber.TLabelframe")
        self.console_frame.pack(fill=tk.BOTH, expand=False, pady=5)

        self._build_modules_grid(modules_frame)

        # Altered States tab
        altered_frame = ttk.Frame(main_nb, style="Cyber.TFrame")
        main_nb.add(altered_frame, text="Altered States")

        self._build_altered_states_tab(altered_frame)

    def _build_modules_grid(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill=tk.BOTH, expand=True)

        sections = {
            "Inputs & Models": ["MULTI-MODAL DATA INPUTS", "PRE-TRAINED MODELS"],
            "Processing & Core": ["DATA PROCESSING HUB", "PARALLEL COMPUTATION CORE"],
            "Swarm & Quantum": ["v16-GODSWARM-NEURAL", "v16-QUANTUM"],
            "Knowledge & Output": ["CONTEXTUAL KNOWLEDGE BASE", "ETHICAL GUIDELINES", "SECURITY MITIGATION", "SIMULATION ENGINE", "OUTPUT MODULES"],
        }

        for tab_name, groups in sections.items():
            frame = ttk.Frame(nb, style="Cyber.TFrame")
            nb.add(frame, text=tab_name)
            for i, g in enumerate(groups):
                lf = ttk.LabelFrame(frame, text=g, style="Cyber.TLabelframe")
                lf.grid(row=i, column=0, sticky="ew", padx=3, pady=3)
                lf.columnconfigure(0, weight=1)
                lbl = ttk.Label(lf, text=f"{g} active", style="Cyber.TLabel")
                lbl.grid(row=0, column=0, sticky="w", padx=5)

    def _build_animation_graph(self):
        self.nodes = {
            "input": (80, 130),
            "models": (260, 80),
            "hub": (260, 180),
            "core": (440, 130),
            "swarm": (620, 80),
            "quantum": (620, 180),
            "kb": (800, 130),
            "output": (980, 130),
        }
        self.node_items = {}
        for name, (x, y) in self.nodes.items():
            r = 18
            oval = self.canvas.create_oval(x - r, y - r, x + r, y + r, outline="#00ffcc", width=2)
            text = self.canvas.create_text(x, y, text=name.upper(), fill="#00ffcc", font=("Segoe UI", 7, "bold"))
            self.node_items[name] = (oval, text)

        edges = [
            ("input", "models"),
            ("input", "hub"),
            ("models", "core"),
            ("hub", "core"),
            ("core", "swarm"),
            ("core", "quantum"),
            ("swarm", "kb"),
            ("quantum", "kb"),
            ("kb", "output"),
        ]
        for a, b in edges:
            x1, y1 = self.nodes[a]
            x2, y2 = self.nodes[b]
            self.canvas.create_line(x1, y1, x2, y2, fill="#2222aa", width=2, arrow=tk.LAST)

        self.packet = self.canvas.create_oval(0, 0, 0, 0, outline="", fill="")
        self.animating = False

        self.swarm_agents_items = {}
        self.swarm_agents_state = {}
        self._start_swarm_animation_loop()

    def _start_swarm_animation_loop(self):
        def loop():
            self._animate_swarm_agents()
            self.after(80, loop)
        loop()

    def _animate_swarm_agents(self):
        if not self.swarm_agents_state:
            return
        cx, cy = self.nodes.get("swarm", (620, 80))
        for name, state in self.swarm_agents_state.items():
            angle = state["angle"] + state["speed"]
            radius = state["radius"] + state["drift"]
            state["angle"] = angle
            state["radius"] = radius
            x = cx + radius * math.cos(angle)
            y = cy + radius * math.sin(angle)
            item, label = self.swarm_agents_items.get(name, (None, None))
            if item:
                self.canvas.coords(item, x - 5, y - 5, x + 5, y + 5)
            if label:
                self.canvas.coords(label, x, y - 10)

    def animate_path(self, path: List[str]):
        if self.animating:
            return
        self.animating = True
        coords = [self.nodes[n] for n in path]

        def step_segment(i, t):
            if i >= len(coords) - 1:
                self.canvas.itemconfig(self.packet, outline="", fill="")
                self.animating = False
                return
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            r = 6
            self.canvas.coords(self.packet, x - r, y - r, x + r, y + r)
            self.canvas.itemconfig(self.packet, outline="#ff00ff", fill="#ff00ff")
            if t >= 1.0:
                self.after(50, lambda: step_segment(i + 1, 0.0))
            else:
                self.after(30, lambda: step_segment(i, t + 0.1))

        step_segment(0, 0.0)

    def _build_dashboard(self):
        if not lazy_matplotlib():
            self.dashboard_frame = None
            return
        dash = ttk.LabelFrame(self, text="Dashboard (Metrics)", style="Cyber.TLabelframe")
        dash.pack(fill=tk.X, padx=5, pady=2)

        fig = Figure(figsize=(4, 1.5), dpi=100)
        self.ax = fig.add_subplot(111)
        self.ax.set_facecolor("#050510")
        fig.patch.set_facecolor("#050510")
        self.ax.tick_params(colors="#00ffcc")
        for spine in self.ax.spines.values():
            spine.set_color("#00ffcc")
        self.ax.set_title("Loss over Epochs", color="#00ffcc")

        self.loss_line, = self.ax.plot([], [], color="#ff00ff", marker="o")

        canvas = FigureCanvasTkAgg(fig, master=dash)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.X, expand=False)
        self.dashboard_canvas = canvas

    def update_dashboard(self, result: Dict[str, Any]):
        if not matplotlib:
            return
        metrics = result.get("train_status", {}).get("metrics", {})
        epochs = metrics.get("epochs", 0)
        loss = metrics.get("loss", 0.0)
        xs = list(range(1, epochs + 1))
        ys = [max(loss * (1.0 + (epochs - i) * 0.1), 0.0001) for i in xs]
        self.loss_line.set_data(xs, ys)
        self.ax.set_xlim(1, max(1, epochs))
        self.ax.set_ylim(0, max(0.1, max(ys) * 1.2))
        self.dashboard_canvas.draw()

    def _build_config_panel(self):
        cfg_frame = ttk.LabelFrame(self, text="Configuration", style="Cyber.TLabelframe")
        cfg_frame.pack(fill=tk.X, padx=5, pady=2)

        core_frame = ttk.Frame(cfg_frame, style="Cyber.TFrame")
        core_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Label(core_frame, text="Core LR", style="Cyber.TLabel").grid(row=0, column=0, sticky="w")
        self.core_lr_var = tk.DoubleVar(value=self.config_state["core"]["lr"])
        ttk.Entry(core_frame, textvariable=self.core_lr_var, style="Cyber.TEntry", width=8).grid(row=0, column=1)

        ttk.Label(core_frame, text="Batch", style="Cyber.TLabel").grid(row=1, column=0, sticky="w")
        self.core_batch_var = tk.IntVar(value=self.config_state["core"]["batch_size"])
        ttk.Entry(core_frame, textvariable=self.core_batch_var, style="Cyber.TEntry", width=8).grid(row=1, column=1)

        ttk.Label(core_frame, text="Epochs", style="Cyber.TLabel").grid(row=2, column=0, sticky="w")
        self.core_epochs_var = tk.IntVar(value=self.config_state["core"]["epochs"])
        ttk.Entry(core_frame, textvariable=self.core_epochs_var, style="Cyber.TEntry", width=8).grid(row=2, column=1)

        swarm_frame = ttk.Frame(cfg_frame, style="Cyber.TFrame")
        swarm_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Label(swarm_frame, text="Swarm Size", style="Cyber.TLabel").grid(row=0, column=0, sticky="w")
        self.swarm_size_var = tk.IntVar(value=self.config_state["swarm"]["swarm_size"])
        ttk.Entry(swarm_frame, textvariable=self.swarm_size_var, style="Cyber.TEntry", width=8).grid(row=0, column=1)

        ttk.Label(swarm_frame, text="Generations", style="Cyber.TLabel").grid(row=1, column=0, sticky="w")
        self.swarm_gen_var = tk.IntVar(value=self.config_state["swarm"]["generations"])
        ttk.Entry(swarm_frame, textvariable=self.swarm_gen_var, style="Cyber.TEntry", width=8).grid(row=1, column=1)

        quantum_frame = ttk.Frame(cfg_frame, style="Cyber.TFrame")
        quantum_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Label(quantum_frame, text="Temperature", style="Cyber.TLabel").grid(row=0, column=0, sticky="w")
        self.quantum_temp_var = tk.DoubleVar(value=self.config_state["quantum"]["temperature"])
        ttk.Entry(quantum_frame, textvariable=self.quantum_temp_var, style="Cyber.TEntry", width=8).grid(row=0, column=1)

        apply_btn = ttk.Button(cfg_frame, text="Apply Config", style="Cyber.TButton", command=self.apply_config)
        apply_btn.pack(side=tk.RIGHT, padx=5)

    def _build_console(self):
        console_label = ttk.Label(self.console_frame, text="(Console stub)", style="Cyber.TLabel")
        console_label.pack(anchor="w", padx=5, pady=5)

    def _build_altered_states_tab(self, parent):
        top_frame = ttk.Frame(parent, style="Cyber.TFrame")
        top_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left = ttk.LabelFrame(top_frame, text="HybridBrain Meta-State", style="Cyber.TLabelframe")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        right = ttk.LabelFrame(top_frame, text="Organs & Reasoning Heatmap", style="Cyber.TLabelframe")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

        self.meta_state_label = ttk.Label(left, text="Meta-State: (unknown)", style="Cyber.TLabel", font=("Segoe UI", 12, "bold"))
        self.meta_state_label.pack(anchor="w", padx=5, pady=5)

        self.meta_conf_label = ttk.Label(left, text="Meta-Confidence: -", style="Cyber.TLabel")
        self.meta_conf_label.pack(anchor="w", padx=5, pady=2)

        self.momentum_label = ttk.Label(left, text="Momentum: -", style="Cyber.TLabel")
        self.momentum_label.pack(anchor="w", padx=5, pady=2)

        self.coach_label = ttk.Label(left, text="AI Coach: -", style="Cyber.TLabel", wraplength=300)
        self.coach_label.pack(anchor="w", padx=5, pady=5)

        self.best_guess_label = ttk.Label(left, text="Best Guess Output: (none)", style="Cyber.TLabel", wraplength=350)
        self.best_guess_label.pack(anchor="w", padx=5, pady=5)

        self.organs_text = tk.Text(right, wrap=tk.WORD, height=12, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.organs_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.heatmap_text = tk.Text(right, wrap=tk.WORD, height=8, bg="#050510", fg="#ffcc66", insertbackground="#ffcc66")
        self.heatmap_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def update_altered_states_view(self, result: Dict[str, Any]):
        brain_state = result.get("brain_state", {})
        best_guess = result.get("best_guess", "")

        meta = brain_state.get("meta_state", "(unknown)")
        prev = brain_state.get("previous_state", None)
        conf = brain_state.get("meta_confidence", 0.0)
        mom = brain_state.get("momentum", 0.0)
        coach = brain_state.get("coach_advice", "-")
        organs = brain_state.get("organs", {})
        heatmap = brain_state.get("heatmap", {})

        label = meta if not prev else f"{meta} (from {prev})"
        self.meta_state_label.config(text=f"Meta-State: {label}")
        self.meta_conf_label.config(text=f"Meta-Confidence: {conf:.2f}")
        self.momentum_label.config(text=f"Momentum: {mom:.2f}")
        self.coach_label.config(text=f"AI Coach: {coach}")
        self.best_guess_label.config(text=f"Best Guess Output: {best_guess[:200]}")

        self.organs_text.configure(state=tk.NORMAL)
        self.organs_text.delete("1.0", tk.END)
        for k, v in organs.items():
            self.organs_text.insert(tk.END, f"{k}: {v}\n")
        self.organs_text.configure(state=tk.DISABLED)

        self.heatmap_text.configure(state=tk.NORMAL)
        self.heatmap_text.delete("1.0", tk.END)
        for k, v in heatmap.items():
            bar = "#" * int(v * 20)
            self.heatmap_text.insert(tk.END, f"{k:10s}: {v:.2f} {bar}\n")
        self.heatmap_text.configure(state=tk.DISABLED)

    def apply_config(self):
        self.config_state["core"]["lr"] = self.core_lr_var.get()
        self.config_state["core"]["batch_size"] = self.core_batch_var.get()
        self.config_state["core"]["epochs"] = self.core_epochs_var.get()
        self.config_state["swarm"]["swarm_size"] = self.swarm_size_var.get()
        self.config_state["swarm"]["generations"] = self.swarm_gen_var.get()
        self.config_state["quantum"]["temperature"] = self.quantum_temp_var.get()
        self._log(f"Config updated: {self.config_state}")

    def _log(self, msg: str):
        LOGGER.info(msg)
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _preload_heavy_libs(self):
        self._log("Preloading heavy libraries in background...")
        ensure_lib_async("torch")
        ensure_lib_async("transformers")
        ensure_lib_async("onnxruntime")
        ensure_lib_async("whisper")
        ensure_lib_async("matplotlib")
        ensure_lib_async("soundfile", "pysoundfile")
        ensure_lib_async("librosa")

    def load_text_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.text_input.delete(0, tk.END)
            self.text_input.insert(0, content[:1024])
            self._log(f"Loaded text file: {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    def load_image_file(self):
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.bmp"), ("All files", "*.*")])
        if not path:
            return
        self.image_path_var.set(path)
        self._log(f"Loaded image file for ViT: {path}")

    def load_audio_file(self):
        path = filedialog.askopenfilename(filetypes=[("Audio files", "*.wav;*.flac;*.mp3"), ("All files", "*.*")])
        if not path:
            return
        self.audio_path_var.set(path)
        self._log(f"Loaded audio file for Whisper: {path}")

    def save_state(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            state = {
                "config_state": self.config_state,
                "last_result": self.last_result,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            self._log(f"State saved to {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save state: {e}")

    def load_state(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.config_state = state.get("config_state", self.config_state)
            self.last_result = state.get("last_result", None)
            self._log(f"State loaded from {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load state: {e}")

    def run_full_pipeline_async(self):
        text = self.text_input.get()
        image_path = self.image_path_var.get() or None
        audio_path = self.audio_path_var.get() or None

        def schedule():
            coro = self.orchestrator.run_full_pipeline_async(text, image_path=image_path, audio_path=audio_path)
            future = asyncio.run_coroutine_threadsafe(coro, self.async_loop)
            future.add_done_callback(self._pipeline_done_callback)

        threading.Thread(target=schedule, daemon=True).start()
        self.animate_path(["input", "models", "hub", "core", "swarm", "quantum", "kb", "output"])

    def _pipeline_done_callback(self, future):
        try:
            result = future.result()
            self.last_result = result
            self._log(f"[PIPELINE] Async done.")
            self.after(0, lambda: self.update_dashboard(result))
            self.after(0, lambda: self.update_swarm_visual(result))
            self.after(0, lambda: self.update_altered_states_view(result))
        except Exception as e:
            self._log(f"[ERROR] {e}")

    def _clear_swarm_agents(self):
        for item, label in self.swarm_agents_items.values():
            self.canvas.delete(item)
            self.canvas.delete(label)
        self.swarm_agents_items.clear()
        self.swarm_agents_state.clear()

    def update_swarm_visual(self, result: Dict[str, Any]):
        swarm_agents = result.get("swarm_agents", {})
        agents = swarm_agents.get("agents", [])
        if not agents:
            return

        self._clear_swarm_agents()

        cx, cy = self.nodes.get("swarm", (620, 80))
        base_radius = 50
        n = len(agents)
        for i, name in enumerate(agents):
            angle = 2 * math.pi * i / max(1, n)
            radius = base_radius + random.uniform(-5, 5)
            x = cx + radius * math.cos(angle)
            y = cy + radius * math.sin(angle)
            r = 5
            item = self.canvas.create_oval(x - r, y - r, x + r, y + r, fill="#ffaa00", outline="")
            label = self.canvas.create_text(x, y - 10, text=str(i), fill="#ffaa00", font=("Segoe UI", 6))
            self.swarm_agents_items[name] = (item, label)
            self.swarm_agents_state[name] = {
                "angle": angle,
                "radius": radius,
                "speed": random.uniform(0.03, 0.08),
                "drift": random.uniform(-0.02, 0.02),
            }

    def mainloop_safe(self):
        try:
            self.mainloop()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    app = MegaSystemApp()
    app.mainloop_safe()
