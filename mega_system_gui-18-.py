import os
import json
import time
import math
import random
import threading
import asyncio
import logging
import socket
from typing import Any, Dict, List, Tuple, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Optional heavy libs (lazy)
matplotlib = None
np = None
onnxruntime = None
torch = None
transformers = None
psutil = None

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    matplotlib = True
except Exception:
    matplotlib = None

try:
    import psutil as _psutil
    psutil = _psutil
except Exception:
    psutil = None

# =========================
# LOGGING
# =========================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger("MegaSystem")


# =========================
# RUNTIME / HELPERS
# =========================

class RuntimeInfo:
    def __init__(self):
        self.has_cuda = False
        self.vram_gb = 0.0
        try:
            import torch as _torch
            self.has_cuda = _torch.cuda.is_available()
            if self.has_cuda:
                self.vram_gb = _torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        except Exception:
            self.has_cuda = False
            self.vram_gb = 0.0

    def summary(self) -> str:
        return f"CUDA={self.has_cuda}, VRAM={self.vram_gb:.2f} GB"


RUNTIME = RuntimeInfo()


def lazy_np():
    global np
    if np is None:
        import numpy as _np
        np = _np
    return np


def lazy_matplotlib():
    return matplotlib is not None


def ensure_lib_async(*names):
    def _load():
        for n in names:
            try:
                __import__(n)
                LOGGER.info(f"[Preload] Loaded {n}")
            except Exception as e:
                LOGGER.info(f"[Preload] Failed to load {n}: {e}")
    threading.Thread(target=_load, daemon=True).start()


# =========================
# SIMPLE VECTOR MEMORY
# =========================

class VectorMemoryStore:
    def __init__(self, dim: int = 128):
        self.dim = dim
        self.items: List[Tuple[List[float], Dict[str, Any]]] = []

    def add(self, embedding: List[float], payload: Dict[str, Any]):
        if len(embedding) != self.dim:
            return
        self.items.append((embedding, payload))

    def _cosine(self, a: List[float], b: List[float]) -> float:
        s = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) + 1e-9
        nb = math.sqrt(sum(y * y for y in b)) + 1e-9
        return s / (na * nb)

    def search(self, query_embedding: List[float], k: int = 5) -> List[Dict[str, Any]]:
        if not self.items:
            return []
        scored = []
        for emb, payload in self.items:
            score = self._cosine(query_embedding, emb)
            scored.append((score, payload))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:k]]


# =========================
# MLOps / DATA / DRIFT / VERSIONING / BIAS / RUNS
# =========================

class DatasetManager:
    def __init__(self, base_dir: str = "datasets"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.index_path = os.path.join(self.base_dir, "datasets_index.json")
        self.datasets: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    def _load_index(self):
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    self.datasets = json.load(f)
            except Exception:
                self.datasets = {}
        else:
            self.datasets = {}

    def _save_index(self):
        try:
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(self.datasets, f, indent=2)
        except Exception as e:
            LOGGER.warning(f"[DatasetManager] Failed to save index: {e}")

    def list_datasets(self) -> List[str]:
        return sorted(self.datasets.keys())

    def create_dataset(self, name: str, description: str = ""):
        if not name:
            return
        if name in self.datasets:
            return
        ds_dir = os.path.join(self.base_dir, name)
        os.makedirs(ds_dir, exist_ok=True)
        self.datasets[name] = {
            "name": name,
            "description": description,
            "created": time.time(),
            "samples": [],
        }
        self._save_index()

    def add_sample(self, dataset: str, sample: Dict[str, Any]):
        if dataset not in self.datasets:
            return
        self.datasets[dataset]["samples"].append(sample)
        self._save_index()

    def get_dataset(self, name: str) -> Dict[str, Any]:
        return self.datasets.get(name, {})


class DriftMonitor:
    def __init__(self):
        self.baseline_embedding: Optional[List[float]] = None
        self.history: List[float] = []

    def set_baseline(self, embedding: List[float]):
        self.baseline_embedding = embedding
        self.history.clear()

    def _cosine(self, a: List[float], b: List[float]) -> float:
        s = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) + 1e-9
        nb = math.sqrt(sum(y * y for y in b)) + 1e-9
        return s / (na * nb)

    def compute_drift(self, embedding: List[float]) -> float:
        if self.baseline_embedding is None or len(embedding) != len(self.baseline_embedding):
            return 0.0
        sim = self._cosine(self.baseline_embedding, embedding)
        drift = 1.0 - sim
        self.history.append(drift)
        self.history = self.history[-100:]
        return drift

    def drift_summary(self) -> Dict[str, Any]:
        if not self.history:
            return {"avg_drift": 0.0, "max_drift": 0.0}
        avg_d = sum(self.history) / len(self.history)
        max_d = max(self.history)
        return {"avg_drift": avg_d, "max_drift": max_d}


class ModelRegistry:
    def __init__(self, registry_path: str = "model_registry.json"):
        self.registry_path = registry_path
        self.models: Dict[str, List[Dict[str, Any]]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    self.models = json.load(f)
            except Exception:
                self.models = {}
        else:
            self.models = {}

    def _save(self):
        try:
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(self.models, f, indent=2)
        except Exception as e:
            LOGGER.warning(f"[ModelRegistry] Failed to save: {e}")

    def register_model(self, kind: str, path: str, meta: Dict[str, Any]):
        if not kind or not path:
            return
        entry = {
            "path": path,
            "meta": meta,
            "time": time.time(),
        }
        self.models.setdefault(kind, []).append(entry)
        self._save()

    def list_versions(self, kind: str) -> List[Dict[str, Any]]:
        return self.models.get(kind, [])


class BiasAnalyzer:
    def __init__(self):
        self.label_counts: Dict[str, int] = {}

    def record_label(self, label: str):
        if not label:
            return
        self.label_counts[label] = self.label_counts.get(label, 0) + 1

    def bias_summary(self) -> Dict[str, Any]:
        total = sum(self.label_counts.values())
        if total == 0:
            return {"total": 0, "distribution": {}}
        dist = {k: v / total for k, v in self.label_counts.items()}
        return {"total": total, "distribution": dist}


class RunTracker:
    def __init__(self, runs_path: str = "runs_log.json"):
        self.runs_path = runs_path
        self.runs: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        if os.path.exists(self.runs_path):
            try:
                with open(self.runs_path, "r", encoding="utf-8") as f:
                    self.runs = json.load(f)
            except Exception:
                self.runs = []
        else:
            self.runs = []

    def _save(self):
        try:
            with open(self.runs_path, "w", encoding="utf-8") as f:
                json.dump(self.runs, f, indent=2)
        except Exception as e:
            LOGGER.warning(f"[RunTracker] Failed to save: {e}")

    def new_run(self, config: Dict[str, Any], metrics: Dict[str, Any], extras: Dict[str, Any]) -> str:
        run_id = f"run-{int(time.time())}-{random.randint(1000,9999)}"
        entry = {
            "id": run_id,
            "time": time.time(),
            "config": config,
            "metrics": metrics,
            "extras": extras,
        }
        self.runs.append(entry)
        self._save()
        return run_id

    def list_runs(self) -> List[Dict[str, Any]]:
        return list(self.runs)


class AnnotationStore:
    def __init__(self, path: str = "annotations.json"):
        self.path = path
        self.annotations: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.annotations = json.load(f)
            except Exception:
                self.annotations = []
        else:
            self.annotations = []

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.annotations, f, indent=2)
        except Exception as e:
            LOGGER.warning(f"[AnnotationStore] Failed to save: {e}")

    def add_annotation(self, kind: str, content: str, label: str, meta: Dict[str, Any]):
        entry = {
            "kind": kind,
            "content": content,
            "label": label,
            "meta": meta,
            "time": time.time(),
        }
        self.annotations.append(entry)
        self._save()

    def list_annotations(self) -> List[Dict[str, Any]]:
        return list(self.annotations)


class DeploymentManager:
    def __init__(self, base_dir: str = "deployment"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def generate_dockerfile(self, name: str = "megasystem"):
        dockerfile_path = os.path.join(self.base_dir, "Dockerfile")
        content = f"""# Auto-generated Dockerfile for {name}
FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir tkinter matplotlib onnxruntime torch transformers soundfile librosa psutil
CMD ["python", "mega_system_gui.py"]
"""
        try:
            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(content)
            LOGGER.info(f"[Deployment] Dockerfile generated at {dockerfile_path}")
        except Exception as e:
            LOGGER.warning(f"[Deployment] Failed to write Dockerfile: {e}")

    def export_edge_profile(self, profile_name: str = "edge_profile.json"):
        path = os.path.join(self.base_dir, profile_name)
        profile = {
            "device": "generic-edge",
            "onnx_optimized": True,
            "quantization": "int8",
            "notes": "Placeholder edge deployment profile.",
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2)
            LOGGER.info(f"[Deployment] Edge profile written to {path}")
        except Exception as e:
            LOGGER.warning(f"[Deployment] Failed to write edge profile: {e}")


class AutoReloader:
    def __init__(self, plugins_dir: str = "plugins", interval: float = 2.0):
        self.plugins_dir = plugins_dir
        self.interval = interval
        self.last_mtime = 0.0
        os.makedirs(self.plugins_dir, exist_ok=True)
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        LOGGER.info("[AutoReloader] Started watching plugins directory.")

    def _loop(self):
        while self.running:
            try:
                mtime = 0.0
                for root, _, files in os.walk(self.plugins_dir):
                    for f in files:
                        p = os.path.join(root, f)
                        mtime = max(mtime, os.path.getmtime(p))
                if mtime > self.last_mtime:
                    self.last_mtime = mtime
                    LOGGER.info("[AutoReloader] Detected plugin change; reload suggested.")
                time.sleep(self.interval)
            except Exception:
                time.sleep(self.interval)

    def stop(self):
        self.running = False


# =========================
# MODEL TIER MANAGER
# =========================

MODEL_PATHS = {
    "vit_onnx_input_name": "input",
    "whisper_onnx_input_name": "mel",
}


class ModelTierManager:
    def __init__(self):
        self.llm_name = "LLM-stub"
        self.llm_backend = "stub"
        self.llm_model = None
        self.llm_tokenizer = None

        self.vit_name = "ViT-stub"
        self.vit_backend = "stub"
        self.vit_onnx_session = None
        self.vit_model = None
        self.vit_processor = None

        self.whisper_name = "Whisper-stub"
        self.whisper_backend = "stub"
        self.whisper_onnx_session = None
        self.whisper_model = None

        self.model_paths: Dict[str, str] = {
            "llm": "",
            "vit_onnx": "",
            "whisper_onnx": "",
        }

        self.last_vit_time: Optional[float] = None
        self.last_whisper_time: Optional[float] = None

    # -------- LLM --------

    def load_llm(self, path: str):
        global torch, transformers
        if not path:
            raise ValueError("Empty LLM path")
        try:
            import torch as _torch
            import transformers as _transformers
            torch = _torch
            transformers = _transformers
        except Exception as e:
            raise RuntimeError(f"transformers/torch not available: {e}")

        try:
            LOGGER.info(f"[LLM] Loading from local path: {path}")
            tok = transformers.AutoTokenizer.from_pretrained(path, local_files_only=True)
            model = transformers.AutoModelForCausalLM.from_pretrained(path, local_files_only=True)
            if RUNTIME.has_cuda:
                model = model.cuda()
            self.llm_model = model
            self.llm_tokenizer = tok
            self.llm_backend = "hf"
            self.llm_name = os.path.basename(path) or "LLM-local"
            self.model_paths["llm"] = path
            LOGGER.info(f"[LLM] Loaded: {self.llm_name}")
        except Exception as e:
            self.llm_model = None
            self.llm_tokenizer = None
            self.llm_backend = "stub"
            self.llm_name = "LLM-stub"
            raise RuntimeError(f"Failed to load LLM: {e}")

    def unload_llm(self):
        global torch
        LOGGER.info("[LLM] Unloading model.")
        self.llm_model = None
        self.llm_tokenizer = None
        self.llm_backend = "stub"
        self.llm_name = "LLM-stub"
        if torch is None:
            try:
                import torch as _torch
                torch = _torch
            except Exception:
                torch = None
        if torch is not None and torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass

    def run_llm(self, text: str) -> Dict[str, Any]:
        if self.llm_backend == "hf" and self.llm_model and self.llm_tokenizer:
            LOGGER.info("[LLM] Using HF backend.")
            inputs = self.llm_tokenizer(text, return_tensors="pt")
            if RUNTIME.has_cuda:
                inputs = {k: v.cuda() for k, v in inputs.items()}
            with torch.no_grad():
                out = self.llm_model.generate(**inputs, max_new_tokens=64)
            decoded = self.llm_tokenizer.decode(out[0], skip_special_tokens=True)
            return {"model": self.llm_name, "backend": "hf", "output": decoded}
        LOGGER.info("[LLM] Using stub backend.")
        return {"model": "LLM-stub", "backend": "stub", "output": f"[STUB LLM OUTPUT] {text}"}

    # -------- ViT --------

    def load_vit_onnx(self, path: str):
        global onnxruntime
        if not path:
            raise ValueError("Empty ViT ONNX path")
        try:
            import onnxruntime as _ort
            onnxruntime = _ort
        except Exception as e:
            raise RuntimeError(f"onnxruntime not available: {e}")
        try:
            LOGGER.info(f"[ViT] Loading ONNX from: {path}")
            sess = onnxruntime.InferenceSession(path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            self.vit_onnx_session = sess
            self.vit_backend = "onnx"
            self.vit_name = os.path.basename(path) or "ViT-onnx"
            self.model_paths["vit_onnx"] = path
            LOGGER.info(f"[ViT] ONNX loaded: {self.vit_name}")
        except Exception as e:
            self.vit_onnx_session = None
            self.vit_backend = "stub"
            self.vit_name = "ViT-stub"
            raise RuntimeError(f"Failed to load ViT ONNX: {e}")

    def unload_vit(self):
        LOGGER.info("[ViT] Unloading model.")
        self.vit_onnx_session = None
        self.vit_backend = "stub"
        self.vit_name = "ViT-stub"

    def run_vit(self, image_path: str) -> Dict[str, Any]:
        if self.vit_backend == "onnx" and self.vit_onnx_session:
            LOGGER.info("[ViT] Using ONNX backend.")
            from PIL import Image
            img = Image.open(image_path).convert("RGB")
            np_mod = lazy_np()
            img_arr = np_mod.array(img).astype("float32") / 255.0
            img_arr = img_arr.transpose(2, 0, 1)[None, ...]
            input_name = MODEL_PATHS["vit_onnx_input_name"]
            t0 = time.time()
            outputs = self.vit_onnx_session.run(None, {input_name: img_arr})
            t1 = time.time()
            self.last_vit_time = t1 - t0
            LOGGER.info(f"[ViT] ONNX inference took {self.last_vit_time * 1000:.1f} ms")
            emb = outputs[0].mean(axis=1).tolist()[0]
            return {"model": self.vit_name, "backend": "onnx", "embedding": emb}

        LOGGER.info("[ViT] Using stub backend.")
        return {"model": "ViT-stub", "backend": "stub", "embedding": [0.0] * 16}

    # -------- Whisper --------

    def load_whisper_onnx(self, path: str):
        global onnxruntime
        if not path:
            raise ValueError("Empty Whisper ONNX path")
        try:
            import onnxruntime as _ort
            onnxruntime = _ort
        except Exception as e:
            raise RuntimeError(f"onnxruntime not available: {e}")
        try:
            LOGGER.info(f"[Whisper] Loading ONNX from: {path}")
            sess = onnxruntime.InferenceSession(path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            self.whisper_onnx_session = sess
            self.whisper_backend = "onnx"
            self.whisper_name = os.path.basename(path) or "Whisper-onnx"
            self.model_paths["whisper_onnx"] = path
            LOGGER.info(f"[Whisper] ONNX loaded: {self.whisper_name}")
        except Exception as e:
            self.whisper_onnx_session = None
            self.whisper_backend = "stub"
            self.whisper_name = "Whisper-stub"
            raise RuntimeError(f"Failed to load Whisper ONNX: {e}")

    def unload_whisper(self):
        LOGGER.info("[Whisper] Unloading model.")
        self.whisper_onnx_session = None
        self.whisper_backend = "stub"
        self.whisper_name = "Whisper-stub"

    def _load_audio(self, audio_path: str):
        import soundfile as sf
        audio, sr = sf.read(audio_path)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio, sr

    def _compute_mel(self, audio, sr):
        import librosa
        mel = librosa.feature.melspectrogram(y=audio, sr=sr, n_mels=80)
        np_mod = lazy_np()
        mel_db = librosa.power_to_db(mel, ref=np_mod.max)
        mel_db = mel_db.astype("float32")[None, None, ...]
        return mel_db

    def run_whisper(self, audio_path: str) -> Dict[str, Any]:
        if self.whisper_backend == "onnx" and self.whisper_onnx_session:
            LOGGER.info("[Whisper] Using ONNX backend.")
            audio, sr = self._load_audio(audio_path)
            mel = self._compute_mel(audio, sr)
            input_name = MODEL_PATHS["whisper_onnx_input_name"]
            t0 = time.time()
            _ = self.whisper_onnx_session.run(None, {input_name: mel})
            t1 = time.time()
            self.last_whisper_time = t1 - t0
            LOGGER.info(f"[Whisper] ONNX inference took {self.last_whisper_time * 1000:.1f} ms")
            transcript = "ONNX-Whisper transcript (mel-based best-guess stub)"
            return {"model": self.whisper_name, "backend": "onnx", "transcript": transcript}

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
        if not image_path:
            return {"model": "ViT-none", "backend": "none", "embedding": []}
        return self.tier.run_vit(image_path)

    def speech_recognition(self, audio_path: str) -> Dict[str, Any]:
        if not audio_path:
            return {"model": "Whisper-none", "backend": "none", "transcript": ""}
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


# =========================
# DISTRIBUTED SWARM NODES
# =========================

class DistributedSwarmNode:
    def __init__(self, port: int = 50555, broadcast_port: int = 50556):
        self.port = port
        self.broadcast_port = broadcast_port
        self.running = False
        self.sock = None
        self.thread = None
        self.last_messages: List[Tuple[str, str]] = []

    def start(self):
        if self.running:
            return
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(("0.0.0.0", self.port))
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            LOGGER.info(f"[SwarmNet] Node listening on UDP {self.port}")
        except Exception as e:
            LOGGER.warning(f"[SwarmNet] Failed to start node: {e}")

    def _loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                msg = data.decode("utf-8", errors="ignore")
                self.last_messages.append((addr[0], msg))
                if len(self.last_messages) > 32:
                    self.last_messages = self.last_messages[-32:]
            except Exception:
                time.sleep(0.05)

    def broadcast(self, message: str):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(message.encode("utf-8"), ("255.255.255.255", self.broadcast_port))
            s.close()
        except Exception as e:
            LOGGER.warning(f"[SwarmNet] Broadcast failed: {e}")

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


# =========================
# GODSWARM + QUANTUM + KB
# =========================

class GodSwarmNeural:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.distributed_node = DistributedSwarmNode()
        self.distributed_node.start()

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
        try:
            msg = json.dumps({"decision": decision, "time": time.time()})
            self.distributed_node.broadcast(msg)
        except Exception:
            pass
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
# AUGMENTATION + EMOTIONAL ENGINE + CORTICAL COLUMNS
# =========================

class AugmentationEngine:
    def __init__(self):
        self.history: List[Dict[str, Any]] = []

    def augment_input(self, text: str) -> Dict[str, Any]:
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
        enriched = dict(state)
        enriched["aug_timestamp"] = time.time()
        enriched["stability_score"] = random.uniform(0.0, 1.0)
        self.history.append(enriched)
        return enriched


class EmotionalGradientEngine:
    def __init__(self):
        self.arousal = 0.5
        self.valence = 0.5
        self.focus = 0.5

    def update(self, metrics: Dict[str, Any], risk: float, temp: float) -> Dict[str, float]:
        loss = metrics.get("loss", 0.1)
        epochs = metrics.get("epochs", 1)

        self.arousal += 0.05 * (1.0 - loss) - 0.02 * (temp / 100.0)
        self.valence += 0.03 * (1.0 - risk) - 0.03 * loss
        self.focus += 0.04 * (epochs / 10.0) - 0.02 * risk

        self.arousal = max(0.0, min(1.0, self.arousal))
        self.valence = max(0.0, min(1.0, self.valence))
        self.focus = max(0.0, min(1.0, self.focus))

        return {
            "arousal": self.arousal,
            "valence": self.valence,
            "focus": self.focus,
        }


class CorticalColumn:
    def __init__(self, name: str):
        self.name = name
        self.activation = 0.0
        self.decay = 0.9

    def stimulate(self, strength: float):
        self.activation += strength
        self.activation = max(0.0, min(1.0, self.activation))

    def tick(self):
        self.activation *= self.decay
        return self.activation


class CorticalColumnSimulation:
    def __init__(self):
        self.columns = {
            "language": CorticalColumn("language"),
            "vision": CorticalColumn("vision"),
            "audio": CorticalColumn("audio"),
            "swarm": CorticalColumn("swarm"),
            "meta": CorticalColumn("meta"),
        }

    def stimulate_from_pipeline(self, llm_out: Dict[str, Any], vit_out: Dict[str, Any], whisper_out: Dict[str, Any]):
        if llm_out:
            self.columns["language"].stimulate(0.3)
            self.columns["meta"].stimulate(0.1)
        if vit_out and vit_out.get("embedding"):
            self.columns["vision"].stimulate(0.3)
        if whisper_out and whisper_out.get("transcript"):
            self.columns["audio"].stimulate(0.3)
        self.columns["swarm"].stimulate(0.1)

    def tick(self) -> Dict[str, float]:
        return {name: col.tick() for name, col in self.columns.items()}


# =========================
# ORGANS
# =========================

class OrganBase:
    def __init__(self, name: str):
        self.name = name
        self.health = 1.0
        self.last_check = time.time()
        self.plasticity = 0.5

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self.last_check = time.time()
        return {"name": self.name, "health": self.health, "context": context}

    def adapt(self, stress: float):
        delta = -0.05 * stress + 0.02 * (1.0 - stress) * self.plasticity
        self.health = max(0.0, min(1.0, self.health + delta))

    def self_heal(self):
        self.health = min(1.0, self.health + 0.01 * self.plasticity)


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
        stress = 0.2 if status == "ok" else (0.6 if status == "latent" else 0.9)
        self.adapt(stress)
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

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        total = self.cache_hits + self.cache_misses + 1e-6
        hit_rate = self.cache_hits / total
        stress = 1.0 - hit_rate
        self.adapt(stress)
        return {"name": self.name, "health": self.health, "hit_rate": hit_rate}


class ThermalOrgan(OrganBase):
    def __init__(self):
        super().__init__("Thermal")
        self.temperature = 40.0

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self.temperature += random.uniform(-0.5, 0.5)
        self.temperature = max(30.0, min(90.0, self.temperature))
        stress = max(0.0, (self.temperature - 60.0) / 40.0)
        self.adapt(stress)
        return {"name": self.name, "temp": self.temperature, "health": self.health}


class DiskOrgan(OrganBase):
    def __init__(self):
        super().__init__("Disk")
        self.io_load = 0.1

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self.io_load = max(0.0, min(1.0, self.io_load + random.uniform(-0.05, 0.05)))
        stress = self.io_load
        self.adapt(stress)
        return {"name": self.name, "io_load": self.io_load, "health": self.health}


class VRAMOrgan(OrganBase):
    def __init__(self):
        super().__init__("VRAM")
        self.vram_gb = RUNTIME.vram_gb

    def tick(self, context: Dict[str, Any]) -> Dict[str, Any]:
        stress = 0.0 if self.vram_gb > 8 else 0.5
        self.adapt(stress)
        return {"name": self.name, "vram_gb": self.vram_gb, "health": self.health}


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
        stress = self.last_risk
        self.adapt(stress)
        return self.last_risk


class SelfIntegrityOrgan(OrganBase):
    def __init__(self):
        super().__init__("SelfIntegrity")
        self.integrity_score = 1.0

    def check(self, signals: Dict[str, Any]) -> Dict[str, Any]:
        drift = random.uniform(-0.02, 0.01)
        self.integrity_score = max(0.0, min(1.0, self.integrity_score + drift))
        stress = max(0.0, 1.0 - self.integrity_score)
        self.adapt(stress)
        return {"name": self.name, "integrity": self.integrity_score, "health": self.health, "signals": signals}


# =========================
# HYBRID BRAIN
# =========================

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
        self.emotional_engine = EmotionalGradientEngine()
        self.cortical = CorticalColumnSimulation()

        self.vector_memory = VectorMemoryStore(dim=128)
        self.dream_buffer: List[Dict[str, Any]] = []

    def _regime_detection(self, metrics: Dict[str, Any]) -> str:
        loss = metrics.get("loss", 0.1)
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
        organ_states["back4blood"] = {"risk": risk, "health": self.back4blood.health}
        organ_states["self_integrity"] = integrity

        if risk < 0.3:
            for organ in [
                self.deep_ram,
                self.backup,
                self.network_watcher,
                self.gpu_cache,
                self.thermal,
                self.disk,
                self.vram,
                self.swarm_node,
                self.back4blood,
                self.self_integrity,
            ]:
                organ.self_heal()

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

        risk = self.back4blood.last_risk
        temp = self.thermal.temperature
        emotional = self.emotional_engine.update(metrics, risk, temp)

        return {
            "meta_state": self.current_state,
            "previous_state": self.previous_state,
            "momentum": self.momentum,
            "meta_confidence": self.meta_confidence,
            "organs": organ_states,
            "heatmap": heatmap,
            "coach_advice": advice,
            "emotional": emotional,
        }

    def augment_lifecycle(self, text: str, pipeline_state: Dict[str, Any]) -> Dict[str, Any]:
        aug_in = self.augmentation.augment_input(text)
        aug_state = self.augmentation.augment_state(pipeline_state)
        return {"aug_input": aug_in, "aug_state": aug_state}

    def store_long_term_memory(self, embedding: List[float], payload: Dict[str, Any]):
        self.vector_memory.add(embedding, payload)

    def recall_long_term_memory(self, query_embedding: List[float], k: int = 5) -> List[Dict[str, Any]]:
        return self.vector_memory.search(query_embedding, k=k)

    def dream_mode(self) -> Dict[str, Any]:
        if not self.pattern_memory:
            return {"dreams": []}
        samples = random.sample(self.pattern_memory, min(4, len(self.pattern_memory)))
        dreams = []
        for s in samples:
            mutated = {
                "state": random.choice(self.META_STATES),
                "loss": s["metrics"].get("loss", 0.1) * random.uniform(0.8, 1.2),
                "temp": s["organs"]["thermal"]["temp"] * random.uniform(0.9, 1.1),
                "risk": s["organs"]["back4blood"]["risk"] * random.uniform(0.8, 1.3),
            }
            dreams.append(mutated)
        self.dream_buffer = dreams
        return {"dreams": dreams}

    def predictive_hallucination(self, text_seed: str) -> str:
        if not self.pattern_memory:
            return f"[Hallucination] {text_seed} ... (no patterns yet)"
        last = self.pattern_memory[-1]
        emo = self.emotional_engine
        mood = "calm" if emo.valence > 0.6 else ("tense" if emo.arousal > 0.7 else "neutral")
        return f"[Hallucination-{mood}] {text_seed} :: loss={last['metrics'].get('loss', 0.1):.3f}, risk={last['organs']['back4blood']['risk']:.2f}"

    def cortical_tick(self, llm_out: Dict[str, Any], vit_out: Dict[str, Any], whisper_out: Dict[str, Any]) -> Dict[str, float]:
        self.cortical.stimulate_from_pipeline(llm_out, vit_out, whisper_out)
        return self.cortical.tick()


# =========================
# ORCHESTRATOR
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

        # MLOps / monitoring
        self.dataset_manager = DatasetManager()
        self.drift_monitor = DriftMonitor()
        self.model_registry = ModelRegistry()
        self.bias_analyzer = BiasAnalyzer()
        self.run_tracker = RunTracker()
        self.annotation_store = AnnotationStore()
        self.deployment_manager = DeploymentManager()
        self.auto_reloader = AutoReloader()
        self.auto_reloader.start()

        self.active_dataset: Optional[str] = None
        self.last_run_id: Optional[str] = None

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

        cortical_state = self.brain.cortical_tick(llm_out, vit_out or {}, whisper_out or {})

        score = hub_feat.get("score", 0)
        embedding = [math.tanh(score / 10.0 + i * 0.01) for i in range(128)]
        self.brain.store_long_term_memory(embedding, {"text": text, "time": time.time()})
        recalled = self.brain.recall_long_term_memory(embedding, k=3)

        # Drift monitoring
        if self.drift_monitor.baseline_embedding is None:
            self.drift_monitor.set_baseline(embedding)
        drift_value = self.drift_monitor.compute_drift(embedding)
        drift_summary = self.drift_monitor.drift_summary()

        dreams = self.brain.dream_mode()
        hallucination = self.brain.predictive_hallucination(text[:80])

        # Bias metrics (simple: record length bucket)
        label = "short" if len(text) < 64 else ("medium" if len(text) < 256 else "long")
        self.bias_analyzer.record_label(label)
        bias_summary = self.bias_analyzer.bias_summary()

        # Dataset logging
        if self.active_dataset:
            self.dataset_manager.add_sample(self.active_dataset, {
                "text": text,
                "image_path": image_path,
                "audio_path": audio_path,
                "time": time.time(),
                "metrics": train_status.get("metrics", {}),
            })

        # Model registry entries
        if self.tier.model_paths.get("llm"):
            self.model_registry.register_model("llm", self.tier.model_paths["llm"], {"backend": self.tier.llm_backend})
        if self.tier.model_paths.get("vit_onnx"):
            self.model_registry.register_model("vit_onnx", self.tier.model_paths["vit_onnx"], {"backend": self.tier.vit_backend})
        if self.tier.model_paths.get("whisper_onnx"):
            self.model_registry.register_model("whisper_onnx", self.tier.model_paths["whisper_onnx"], {"backend": self.tier.whisper_backend})

        # Run tracking
        run_id = self.run_tracker.new_run(
            config=self.config,
            metrics=train_status.get("metrics", {}),
            extras={
                "drift": drift_value,
                "bias": bias_summary,
                "dataset": self.active_dataset,
            },
        )
        self.last_run_id = run_id

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
            "cortical_state": cortical_state,
            "ltm_recalled": recalled,
            "dreams": dreams,
            "hallucination": hallucination,
            "drift_value": drift_value,
            "drift_summary": drift_summary,
            "bias_summary": bias_summary,
            "run_id": run_id,
            "active_dataset": self.active_dataset,
        }


# =========================
# TKINTER GUI
# =========================

class MegaSystemApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ALL-IN-ONE MEGA SYSTEM v17 HYBRIDBRAIN++ MLOps")
        self.geometry("1600x950")
        self.configure(bg="#050510")

        self.config_state = {
            "core": {"lr": 0.001, "batch_size": 32, "epochs": 5},
            "swarm": {"swarm_size": 16, "generations": 10},
            "quantum": {"temperature": 0.7},
            "model_paths": {
                "llm": "",
                "vit_onnx": "",
                "whisper_onnx": "",
            },
        }
        self.last_result: Optional[Dict[str, Any]] = None

        self.tier_manager = ModelTierManager()
        self.tier_manager.model_paths.update(self.config_state.get("model_paths", {}))

        self.orchestrator = MegaSystemOrchestrator(self.config_state, self.tier_manager)

        self.vit_times: List[float] = []
        self.whisper_times: List[float] = []

        self._setup_style()
        self._build_nerve_center()
        self._build_dashboard()
        self._build_config_panel()
        self._build_console()
        self._build_status_strip()
        self._build_mlop_tabs()
        self._start_status_updates()

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
            text="Nerve Center – ALL-IN-ONE MEGA SYSTEM v17 (HybridBrain++, MLOps, Altered States, Dreams)",
            style="Cyber.TLabel",
            font=("Segoe UI", 14, "bold"),
        )
        title.pack(pady=5)

        self.main_nb = ttk.Notebook(self)
        self.main_nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        main_frame = ttk.Frame(self.main_nb, style="Cyber.TFrame")
        self.main_nb.add(main_frame, text="Main System")

        left_frame = ttk.Frame(main_frame, style="Cyber.TFrame")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_frame = ttk.Frame(main_frame, style="Cyber.TFrame")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        modules_frame = ttk.Frame(left_frame, style="Cyber.TFrame")
        modules_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        anim_frame = ttk.LabelFrame(left_frame, text="Data Flow & Swarm Visualizer", style="Cyber.TLabelframe")
        anim_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, pady=5)

        self.canvas = tk.Canvas(anim_frame, bg="#050510", highlightthickness=0, height=260)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._build_animation_graph()

        control_frame = ttk.LabelFrame(right_frame, text="Control Panel", style="Cyber.TLabelframe")
        control_frame.pack(fill=tk.X, pady=5)

        ttk.Label(control_frame, text="Text Input:", style="Cyber.TLabel").pack(anchor="w")
        self.text_input = ttk.Entry(control_frame, style="Cyber.TEntry")
        self.text_input.insert(0, "Hello world from HYBRIDBRAIN++ system")
        self.text_input.pack(fill=tk.X, pady=2)

        self.image_path_var = tk.StringVar()
        self.audio_path_var = tk.StringVar()

        img_btn = ttk.Button(control_frame, text="Load Image for ViT", style="Cyber.TButton", command=self.load_image_file)
        img_btn.pack(fill=tk.X, pady=2)

        aud_btn = ttk.Button(control_frame, text="Load Audio for Whisper", style="Cyber.TButton", command=self.load_audio_file)
        aud_btn.pack(fill=tk.X, pady=2)

        run_button = ttk.Button(control_frame, text="Run Full Pipeline (Async)", style="Cyber.TButton", command=self.run_full_pipeline_async)
        run_button.pack(pady=5, fill=tk.X)

        dream_button = ttk.Button(control_frame, text="Trigger Dream Mode", style="Cyber.TButton", command=self.trigger_dream_mode)
        dream_button.pack(pady=2, fill=tk.X)

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

        altered_frame = ttk.Frame(self.main_nb, style="Cyber.TFrame")
        self.main_nb.add(altered_frame, text="Altered States & Brain")

        self._build_altered_states_tab(altered_frame)

    def _build_modules_grid(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill=tk.BOTH, expand=True)

        sections = {
            "Inputs & Models": ["MULTI-MODAL DATA INPUTS", "PRE-TRAINED MODELS"],
            "Processing & Core": ["DATA PROCESSING HUB", "PARALLEL COMPUTATION CORE"],
            "Swarm & Quantum": ["v17-GODSWARM-NEURAL", "v17-QUANTUM"],
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

        # Heartbeat circle
        self.heartbeat_phase = 0.0
        self.heartbeat = self.canvas.create_oval(20, 20, 40, 40, outline="#ff0066", width=2)

        self.swarm_agents_items = {}
        self.swarm_agents_state = {}
        self._start_swarm_animation_loop()
        self._start_heartbeat_loop()

    def _start_swarm_animation_loop(self):
        def loop():
            self._animate_swarm_agents()
            self.after(80, loop)
        loop()

    def _start_heartbeat_loop(self):
        def loop():
            self.heartbeat_phase += 0.2
            scale = 1.0 + 0.2 * math.sin(self.heartbeat_phase)
            cx, cy = 30, 30
            r = 10 * scale
            self.canvas.coords(self.heartbeat, cx - r, cy - r, cx + r, cy + r)
            color = "#ff0066" if math.sin(self.heartbeat_phase) > 0 else "#660033"
            self.canvas.itemconfig(self.heartbeat, outline=color)
            self.after(120, loop)
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

        fig = Figure(figsize=(6, 2.2), dpi=100)
        self.ax_loss = fig.add_subplot(131)
        self.ax_onnx = fig.add_subplot(132)
        self.ax_drift = fig.add_subplot(133)

        fig.patch.set_facecolor("#050510")
        for ax in (self.ax_loss, self.ax_onnx, self.ax_drift):
            ax.set_facecolor("#050510")
            ax.tick_params(colors="#00ffcc")
            for spine in ax.spines.values():
                spine.set_color("#00ffcc")

        self.ax_loss.set_title("Loss over Epochs", color="#00ffcc", fontsize=9)
        self.ax_onnx.set_title("ONNX Timings (ms)", color="#00ffcc", fontsize=9)
        self.ax_drift.set_title("Drift History", color="#00ffcc", fontsize=9)

        self.loss_line, = self.ax_loss.plot([], [], color="#ff00ff", marker="o")
        self.vit_line, = self.ax_onnx.plot([], [], color="#00ffcc", marker="o", label="ViT")
        self.whisper_line, = self.ax_onnx.plot([], [], color="#ffcc00", marker="o", label="Whisper")
        self.ax_onnx.legend(facecolor="#050510", edgecolor="#00ffcc", labelcolor="#00ffcc", fontsize=7)

        self.drift_line, = self.ax_drift.plot([], [], color="#ff6666", marker="o")

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
        self.ax_loss.set_xlim(1, max(1, epochs))
        self.ax_loss.set_ylim(0, max(0.1, max(ys) * 1.2))

        vit_x = list(range(1, len(self.vit_times) + 1))
        whisper_x = list(range(1, len(self.whisper_times) + 1))
        self.vit_line.set_data(vit_x, [t * 1000.0 for t in self.vit_times])
        self.whisper_line.set_data(whisper_x, [t * 1000.0 for t in self.whisper_times])

        max_len = max(len(vit_x), len(whisper_x), 1)
        self.ax_onnx.set_xlim(1, max_len)
        all_vals = [t * 1000.0 for t in self.vit_times + self.whisper_times] or [1.0]
        self.ax_onnx.set_ylim(0, max(all_vals) * 1.2)

        drift_summary = result.get("drift_summary", {})
        drift_hist = self.orchestrator.drift_monitor.history
        dx = list(range(1, len(drift_hist) + 1))
        self.drift_line.set_data(dx, drift_hist)
        self.ax_drift.set_xlim(1, max(1, len(dx)))
        self.ax_drift.set_ylim(0, max(drift_hist) * 1.2 if drift_hist else 0.1)

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

        models_frame = ttk.Frame(cfg_frame, style="Cyber.TFrame")
        models_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Label(models_frame, text="LLM Path", style="Cyber.TLabel").grid(row=0, column=0, sticky="w")
        self.llm_path_var = tk.StringVar(value=self.config_state["model_paths"].get("llm", ""))
        ttk.Entry(models_frame, textvariable=self.llm_path_var, style="Cyber.TEntry", width=28).grid(row=0, column=1, columnspan=2, sticky="we")
        ttk.Button(models_frame, text="Browse", style="Cyber.TButton", command=self.browse_llm_path).grid(row=0, column=3, padx=2)
        ttk.Button(models_frame, text="Load LLM", style="Cyber.TButton", command=self.gui_load_llm).grid(row=0, column=4, padx=2)
        ttk.Button(models_frame, text="Unload LLM", style="Cyber.TButton", command=self.gui_unload_llm).grid(row=0, column=5, padx=2)

        ttk.Label(models_frame, text="ViT ONNX", style="Cyber.TLabel").grid(row=1, column=0, sticky="w")
        self.vit_path_var = tk.StringVar(value=self.config_state["model_paths"].get("vit_onnx", ""))
        ttk.Entry(models_frame, textvariable=self.vit_path_var, style="Cyber.TEntry", width=28).grid(row=1, column=1, columnspan=2, sticky="we")
        ttk.Button(models_frame, text="Browse", style="Cyber.TButton", command=self.browse_vit_path).grid(row=1, column=3, padx=2)
        ttk.Button(models_frame, text="Load ViT", style="Cyber.TButton", command=self.gui_load_vit).grid(row=1, column=4, padx=2)
        ttk.Button(models_frame, text="Unload ViT", style="Cyber.TButton", command=self.gui_unload_vit).grid(row=1, column=5, padx=2)

        ttk.Label(models_frame, text="Whisper ONNX", style="Cyber.TLabel").grid(row=2, column=0, sticky="w")
        self.whisper_path_var = tk.StringVar(value=self.config_state["model_paths"].get("whisper_onnx", ""))
        ttk.Entry(models_frame, textvariable=self.whisper_path_var, style="Cyber.TEntry", width=28).grid(row=2, column=1, columnspan=2, sticky="we")
        ttk.Button(models_frame, text="Browse", style="Cyber.TButton", command=self.browse_whisper_path).grid(row=2, column=3, padx=2)
        ttk.Button(models_frame, text="Load Whisper", style="Cyber.TButton", command=self.gui_load_whisper).grid(row=2, column=4, padx=2)
        ttk.Button(models_frame, text="Unload Whisper", style="Cyber.TButton", command=self.gui_unload_whisper).grid(row=2, column=5, padx=2)

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

        right = ttk.LabelFrame(top_frame, text="Organs, Emotions & Reasoning Heatmap", style="Cyber.TLabelframe")
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

        self.hallucination_label = ttk.Label(left, text="Predictive Hallucination: (none)", style="Cyber.TLabel", wraplength=350)
        self.hallucination_label.pack(anchor="w", padx=5, pady=5)

        self.emotion_label = ttk.Label(left, text="Emotional Gradient: -", style="Cyber.TLabel", wraplength=350)
        self.emotion_label.pack(anchor="w", padx=5, pady=5)

        self.organs_text = tk.Text(right, wrap=tk.WORD, height=12, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.organs_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.heatmap_text = tk.Text(right, wrap=tk.WORD, height=8, bg="#050510", fg="#ffcc66", insertbackground="#ffcc66")
        self.heatmap_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        bottom = ttk.LabelFrame(parent, text="Cortical Columns & Dreams", style="Cyber.TLabelframe")
        bottom.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.cortical_text = tk.Text(bottom, wrap=tk.WORD, height=6, bg="#050510", fg="#66ccff", insertbackground="#66ccff")
        self.cortical_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.dreams_text = tk.Text(bottom, wrap=tk.WORD, height=6, bg="#050510", fg="#ff66cc", insertbackground="#ff66cc")
        self.dreams_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def update_altered_states_view(self, result: Dict[str, Any]):
        brain_state = result.get("brain_state", {})
        best_guess = result.get("best_guess", "")
        hallucination = result.get("hallucination", "")
        cortical_state = result.get("cortical_state", {})
        dreams = result.get("dreams", {}).get("dreams", [])

        meta = brain_state.get("meta_state", "(unknown)")
        prev = brain_state.get("previous_state", None)
        conf = brain_state.get("meta_confidence", 0.0)
        mom = brain_state.get("momentum", 0.0)
        coach = brain_state.get("coach_advice", "-")
        organs = brain_state.get("organs", {})
        heatmap = brain_state.get("heatmap", {})
        emotional = brain_state.get("emotional", {})

        label = meta if not prev else f"{meta} (from {prev})"
        self.meta_state_label.config(text=f"Meta-State: {label}")
        self.meta_conf_label.config(text=f"Meta-Confidence: {conf:.2f}")
        self.momentum_label.config(text=f"Momentum: {mom:.2f}")
        self.coach_label.config(text=f"AI Coach: {coach}")
        self.best_guess_label.config(text=f"Best Guess Output: {best_guess[:200]}")
        self.hallucination_label.config(text=f"Predictive Hallucination: {hallucination[:200]}")

        emo_str = f"Arousal={emotional.get('arousal', 0.0):.2f}, Valence={emotional.get('valence', 0.0):.2f}, Focus={emotional.get('focus', 0.0):.2f}"
        self.emotion_label.config(text=f"Emotional Gradient: {emo_str}")

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

        self.cortical_text.configure(state=tk.NORMAL)
        self.cortical_text.delete("1.0", tk.END)
        for k, v in cortical_state.items():
            bar = "#" * int(v * 20)
            self.cortical_text.insert(tk.END, f"{k:10s}: {v:.2f} {bar}\n")
        self.cortical_text.configure(state=tk.DISABLED)

        self.dreams_text.configure(state=tk.NORMAL)
        self.dreams_text.delete("1.0", tk.END)
        for d in dreams:
            self.dreams_text.insert(tk.END, f"{d}\n")
        self.dreams_text.configure(state=tk.DISABLED)

    def apply_config(self):
        self.config_state["core"]["lr"] = self.core_lr_var.get()
        self.config_state["core"]["batch_size"] = self.core_batch_var.get()
        self.config_state["core"]["epochs"] = self.core_epochs_var.get()
        self.config_state["swarm"]["swarm_size"] = self.swarm_size_var.get()
        self.config_state["swarm"]["generations"] = self.swarm_gen_var.get()
        self.config_state["quantum"]["temperature"] = self.quantum_temp_var.get()

        self.config_state["model_paths"]["llm"] = self.llm_path_var.get()
        self.config_state["model_paths"]["vit_onnx"] = self.vit_path_var.get()
        self.config_state["model_paths"]["whisper_onnx"] = self.whisper_path_var.get()
        self.tier_manager.model_paths.update(self.config_state["model_paths"])

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
        self._log("Preloading heavy libraries in background (no model downloads).")
        ensure_lib_async("torch")
        ensure_lib_async("transformers")
        ensure_lib_async("onnxruntime")
        ensure_lib_async("matplotlib")
        ensure_lib_async("soundfile", "pysoundfile")
        ensure_lib_async("librosa")
        ensure_lib_async("faiss", "faiss-cpu")

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

    def browse_llm_path(self):
        path = filedialog.askdirectory(title="Select LLM model directory")
        if not path:
            return
        self.llm_path_var.set(path)
        self._log(f"LLM path set: {path}")

    def browse_vit_path(self):
        path = filedialog.askopenfilename(filetypes=[("ONNX files", "*.onnx"), ("All files", "*.*")])
        if not path:
            return
        self.vit_path_var.set(path)
        self._log(f"ViT ONNX path set: {path}")

    def browse_whisper_path(self):
        path = filedialog.askopenfilename(filetypes=[("ONNX files", "*.onnx"), ("All files", "*.*")])
        if not path:
            return
        self.whisper_path_var.set(path)
        self._log(f"Whisper ONNX path set: {path}")

    def gui_load_llm(self):
        path = self.llm_path_var.get()
        if not path:
            messagebox.showwarning("LLM", "No LLM path set.")
            return
        try:
            self.tier_manager.load_llm(path)
            self._log(f"[LLM] Loaded from {path}")
        except Exception as e:
            messagebox.showerror("LLM Load Error", str(e))
            self._log(f"[LLM] Load error: {e}")

    def gui_unload_llm(self):
        self.tier_manager.unload_llm()
        self._log("[LLM] Unloaded.")

    def gui_load_vit(self):
        path = self.vit_path_var.get()
        if not path:
            messagebox.showwarning("ViT", "No ViT ONNX path set.")
            return
        try:
            self.tier_manager.load_vit_onnx(path)
            self._log(f"[ViT] ONNX loaded from {path}")
        except Exception as e:
            messagebox.showerror("ViT Load Error", str(e))
            self._log(f"[ViT] Load error: {e}")

    def gui_unload_vit(self):
        self.tier_manager.unload_vit()
        self._log("[ViT] Unloaded.")

    def gui_load_whisper(self):
        path = self.whisper_path_var.get()
        if not path:
            messagebox.showwarning("Whisper", "No Whisper ONNX path set.")
            return
        try:
            self.tier_manager.load_whisper_onnx(path)
            self._log(f"[Whisper] ONNX loaded from {path}")
        except Exception as e:
            messagebox.showerror("Whisper Load Error", str(e))
            self._log(f"[Whisper] Load error: {e}")

    def gui_unload_whisper(self):
        self.tier_manager.unload_whisper()
        self._log("[Whisper] Unloaded.")

    def save_state(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            self.config_state["model_paths"]["llm"] = self.llm_path_var.get()
            self.config_state["model_paths"]["vit_onnx"] = self.vit_path_var.get()
            self.config_state["model_paths"]["whisper_onnx"] = self.whisper_path_var.get()
            self.tier_manager.model_paths.update(self.config_state["model_paths"])

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

            self.core_lr_var.set(self.config_state["core"]["lr"])
            self.core_batch_var.set(self.config_state["core"]["batch_size"])
            self.core_epochs_var.set(self.config_state["core"]["epochs"])
            self.swarm_size_var.set(self.config_state["swarm"]["swarm_size"])
            self.swarm_gen_var.set(self.config_state["swarm"]["generations"])
            self.quantum_temp_var.set(self.config_state["quantum"]["temperature"])

            mp = self.config_state.get("model_paths", {})
            self.llm_path_var.set(mp.get("llm", ""))
            self.vit_path_var.set(mp.get("vit_onnx", ""))
            self.whisper_path_var.set(mp.get("whisper_onnx", ""))
            self.tier_manager.model_paths.update(mp)

            self._log(f"State loaded from {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load state: {e}")

    def _build_status_strip(self):
        self.status_frame = ttk.Frame(self, style="Cyber.TFrame")
        self.status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_label = ttk.Label(
            self.status_frame,
            text="Models: -, -, - | Timings: -, - | CPU: -, RAM: - | Drift: - | Dataset: - | Run: -",
            style="Cyber.TLabel"
        )
        self.status_label.pack(anchor="w", padx=5, pady=2)

    def _start_status_updates(self):
        def update():
            llm_loaded = self.tier_manager.llm_backend != "stub"
            vit_loaded = self.tier_manager.vit_backend != "stub"
            whisper_loaded = self.tier_manager.whisper_backend != "stub"

            llm = "Loaded" if llm_loaded else "None"
            vit = "Loaded" if vit_loaded else "None"
            whisper = "Loaded" if whisper_loaded else "None"

            vit_t = (
                f"{self.tier_manager.last_vit_time * 1000:.1f} ms"
                if self.tier_manager.last_vit_time else "-"
            )
            whisper_t = (
                f"{self.tier_manager.last_whisper_time * 1000:.1f} ms"
                if self.tier_manager.last_whisper_time else "-"
            )

            if psutil:
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
                cpu_str = f"{cpu:.0f}%"
                ram_str = f"{ram:.0f}%"
            else:
                cpu_str = "-"
                ram_str = "-"

            vram_str = f"{RUNTIME.vram_gb:.1f}GB" if RUNTIME.vram_gb > 0 else "-"

            drift_summary = self.orchestrator.drift_monitor.drift_summary()
            avg_drift = drift_summary.get("avg_drift", 0.0)
            drift_str = f"{avg_drift:.3f}"

            dataset = self.orchestrator.active_dataset or "-"
            run_id = self.orchestrator.last_run_id or "-"

            self.status_label.config(
                text=(
                    f"LLM: {llm} | ViT: {vit} | Whisper: {whisper}   "
                    f"|   ViT ONNX: {vit_t}   Whisper ONNX: {whisper_t}   "
                    f"|   CPU: {cpu_str}  RAM: {ram_str}  VRAM: {vram_str}   "
                    f"|   Drift(avg): {drift_str}   Dataset: {dataset}   Run: {run_id}"
                )
            )

            self.after(500, update)

        update()

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

    def trigger_dream_mode(self):
        if not self.last_result:
            self._log("[Dream] No previous result; run pipeline first.")
            return
        dreams = self.last_result.get("dreams", {})
        self._log(f"[Dream] Current dreams: {dreams}")

    def _pipeline_done_callback(self, future):
        try:
            result = future.result()
            self.last_result = result
            self._log(f"[PIPELINE] Async done.")

            if self.tier_manager.last_vit_time is not None:
                self.vit_times.append(self.tier_manager.last_vit_time)
                self.vit_times = self.vit_times[-50:]
                self._log(f"[ViT] Last ONNX inference: {self.tier_manager.last_vit_time * 1000:.1f} ms")
            if self.tier_manager.last_whisper_time is not None:
                self.whisper_times.append(self.tier_manager.last_whisper_time)
                self.whisper_times = self.whisper_times[-50:]
                self._log(f"[Whisper] Last ONNX inference: {self.tier_manager.last_whisper_time * 1000:.1f} ms")

            self.after(0, lambda: self.update_dashboard(result))
            self.after(0, lambda: self.update_swarm_visual(result))
            self.after(0, lambda: self.update_altered_states_view(result))
            self.after(0, lambda: self._update_mlop_views(result))
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

    # =========================
    # MLOps TABS (Datasets, Experiments, Versions, Bias/Drift, Annotations, Deployment)
    # =========================

    def _build_mlop_tabs(self):
        mlops_frame = ttk.Frame(self.main_nb, style="Cyber.TFrame")
        self.main_nb.add(mlops_frame, text="MLOps & Monitoring")

        nb = ttk.Notebook(mlops_frame)
        nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Datasets
        ds_frame = ttk.Frame(nb, style="Cyber.TFrame")
        nb.add(ds_frame, text="Datasets")
        self._build_datasets_tab(ds_frame)

        # Experiments
        exp_frame = ttk.Frame(nb, style="Cyber.TFrame")
        nb.add(exp_frame, text="Experiments")
        self._build_experiments_tab(exp_frame)

        # Model Versions
        ver_frame = ttk.Frame(nb, style="Cyber.TFrame")
        nb.add(ver_frame, text="Model Versions")
        self._build_versions_tab(ver_frame)

        # Bias & Drift
        bd_frame = ttk.Frame(nb, style="Cyber.TFrame")
        nb.add(bd_frame, text="Bias & Drift")
        self._build_bias_drift_tab(bd_frame)

        # Annotations
        ann_frame = ttk.Frame(nb, style="Cyber.TFrame")
        nb.add(ann_frame, text="Annotations")
        self._build_annotations_tab(ann_frame)

        # Deployment
        dep_frame = ttk.Frame(nb, style="Cyber.TFrame")
        nb.add(dep_frame, text="Deployment")
        self._build_deployment_tab(dep_frame)

    def _build_datasets_tab(self, parent):
        top = ttk.Frame(parent, style="Cyber.TFrame")
        top.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left = ttk.LabelFrame(top, text="Datasets", style="Cyber.TLabelframe")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        right = ttk.LabelFrame(top, text="Active Dataset", style="Cyber.TLabelframe")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

        self.dataset_list = tk.Listbox(left, bg="#050510", fg="#00ffcc")
        self.dataset_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        ds_btn_frame = ttk.Frame(left, style="Cyber.TFrame")
        ds_btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(ds_btn_frame, text="Refresh", style="Cyber.TButton", command=self._refresh_datasets).pack(side=tk.LEFT, padx=2)
        ttk.Button(ds_btn_frame, text="Create Dataset", style="Cyber.TButton", command=self._create_dataset_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(ds_btn_frame, text="Set Active", style="Cyber.TButton", command=self._set_active_dataset).pack(side=tk.LEFT, padx=2)

        self.active_dataset_label = ttk.Label(right, text="Active: (none)", style="Cyber.TLabel")
        self.active_dataset_label.pack(anchor="w", padx=5, pady=5)

        self.active_dataset_info = tk.Text(right, wrap=tk.WORD, height=12, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.active_dataset_info.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._refresh_datasets()

    def _refresh_datasets(self):
        self.dataset_list.delete(0, tk.END)
        names = self.orchestrator.dataset_manager.list_datasets()
        for n in names:
            self.dataset_list.insert(tk.END, n)

    def _create_dataset_dialog(self):
        win = tk.Toplevel(self)
        win.title("Create Dataset")
        win.configure(bg="#050510")

        ttk.Label(win, text="Name:", style="Cyber.TLabel").pack(anchor="w", padx=5, pady=2)
        name_var = tk.StringVar()
        ttk.Entry(win, textvariable=name_var, style="Cyber.TEntry").pack(fill=tk.X, padx=5, pady=2)

        ttk.Label(win, text="Description:", style="Cyber.TLabel").pack(anchor="w", padx=5, pady=2)
        desc_var = tk.StringVar()
        ttk.Entry(win, textvariable=desc_var, style="Cyber.TEntry").pack(fill=tk.X, padx=5, pady=2)

        def create():
            name = name_var.get().strip()
            desc = desc_var.get().strip()
            if not name:
                messagebox.showwarning("Dataset", "Name required.")
                return
            self.orchestrator.dataset_manager.create_dataset(name, desc)
            self._log(f"[Dataset] Created: {name}")
            self._refresh_datasets()
            win.destroy()

        ttk.Button(win, text="Create", style="Cyber.TButton", command=create).pack(pady=5)

    def _set_active_dataset(self):
        sel = self.dataset_list.curselection()
        if not sel:
            messagebox.showwarning("Dataset", "Select a dataset.")
            return
        name = self.dataset_list.get(sel[0])
        self.orchestrator.active_dataset = name
        ds = self.orchestrator.dataset_manager.get_dataset(name)
        self.active_dataset_label.config(text=f"Active: {name}")
        self.active_dataset_info.configure(state=tk.NORMAL)
        self.active_dataset_info.delete("1.0", tk.END)
        self.active_dataset_info.insert(tk.END, json.dumps(ds, indent=2))
        self.active_dataset_info.configure(state=tk.DISABLED)
        self._log(f"[Dataset] Active dataset set to {name}")

    def _build_experiments_tab(self, parent):
        frame = ttk.Frame(parent, style="Cyber.TFrame")
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.run_list = tk.Listbox(frame, bg="#050510", fg="#00ffcc")
        self.run_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        right = ttk.LabelFrame(frame, text="Run Details", style="Cyber.TLabelframe")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.run_details_text = tk.Text(right, wrap=tk.WORD, height=20, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.run_details_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        btn_frame = ttk.Frame(frame, style="Cyber.TFrame")
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(btn_frame, text="Refresh Runs", style="Cyber.TButton", command=self._refresh_runs).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Show Selected", style="Cyber.TButton", command=self._show_selected_run).pack(side=tk.LEFT, padx=2)

        self._refresh_runs()

    def _refresh_runs(self):
        self.run_list.delete(0, tk.END)
        runs = self.orchestrator.run_tracker.list_runs()
        for r in runs:
            self.run_list.insert(tk.END, f"{r['id']} | loss={r['metrics'].get('loss', 0.0):.4f}")

    def _show_selected_run(self):
        sel = self.run_list.curselection()
        if not sel:
            return
        idx = sel[0]
        runs = self.orchestrator.run_tracker.list_runs()
        if idx >= len(runs):
            return
        r = runs[idx]
        self.run_details_text.configure(state=tk.NORMAL)
        self.run_details_text.delete("1.0", tk.END)
        self.run_details_text.insert(tk.END, json.dumps(r, indent=2))
        self.run_details_text.configure(state=tk.DISABLED)

    def _build_versions_tab(self, parent):
        frame = ttk.Frame(parent, style="Cyber.TFrame")
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left = ttk.LabelFrame(frame, text="Model Versions", style="Cyber.TLabelframe")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        right = ttk.LabelFrame(frame, text="Details", style="Cyber.TLabelframe")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

        self.version_list = tk.Listbox(left, bg="#050510", fg="#00ffcc")
        self.version_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        btn_frame = ttk.Frame(left, style="Cyber.TFrame")
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(btn_frame, text="Refresh", style="Cyber.TButton", command=self._refresh_versions).pack(side=tk.LEFT, padx=2)

        self.version_details_text = tk.Text(right, wrap=tk.WORD, height=20, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.version_details_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._refresh_versions()

    def _refresh_versions(self):
        self.version_list.delete(0, tk.END)
        reg = self.orchestrator.model_registry.models
        for kind, entries in reg.items():
            for e in entries:
                self.version_list.insert(tk.END, f"{kind}: {e['path']} @ {time.strftime('%H:%M:%S', time.localtime(e['time']))}")

    def _build_bias_drift_tab(self, parent):
        frame = ttk.Frame(parent, style="Cyber.TFrame")
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left = ttk.LabelFrame(frame, text="Bias Summary", style="Cyber.TLabelframe")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        right = ttk.LabelFrame(frame, text="Drift Summary", style="Cyber.TLabelframe")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

        self.bias_text = tk.Text(left, wrap=tk.WORD, height=20, bg="#050510", fg="#ffcc66", insertbackground="#ffcc66")
        self.bias_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.drift_text = tk.Text(right, wrap=tk.WORD, height=20, bg="#050510", fg="#ff6666", insertbackground="#ff6666")
        self.drift_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        btn_frame = ttk.Frame(frame, style="Cyber.TFrame")
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(btn_frame, text="Refresh Bias/Drift", style="Cyber.TButton", command=self._refresh_bias_drift).pack(side=tk.LEFT, padx=2)

        self._refresh_bias_drift()

    def _refresh_bias_drift(self):
        bias_summary = self.orchestrator.bias_analyzer.bias_summary()
        drift_summary = self.orchestrator.drift_monitor.drift_summary()

        self.bias_text.configure(state=tk.NORMAL)
        self.bias_text.delete("1.0", tk.END)
        self.bias_text.insert(tk.END, json.dumps(bias_summary, indent=2))
        self.bias_text.configure(state=tk.DISABLED)

        self.drift_text.configure(state=tk.NORMAL)
        self.drift_text.delete("1.0", tk.END)
        self.drift_text.insert(tk.END, json.dumps(drift_summary, indent=2))
        self.drift_text.configure(state=tk.DISABLED)

    def _build_annotations_tab(self, parent):
        frame = ttk.Frame(parent, style="Cyber.TFrame")
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        top = ttk.LabelFrame(frame, text="Add Annotation", style="Cyber.TLabelframe")
        top.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(top, text="Kind (text/image/audio):", style="Cyber.TLabel").grid(row=0, column=0, sticky="w")
        self.ann_kind_var = tk.StringVar(value="text")
        ttk.Entry(top, textvariable=self.ann_kind_var, style="Cyber.TEntry", width=12).grid(row=0, column=1, sticky="w")

        ttk.Label(top, text="Label:", style="Cyber.TLabel").grid(row=1, column=0, sticky="w")
        self.ann_label_var = tk.StringVar(value="tag")
        ttk.Entry(top, textvariable=self.ann_label_var, style="Cyber.TEntry", width=12).grid(row=1, column=1, sticky="w")

        ttk.Label(top, text="Content:", style="Cyber.TLabel").grid(row=2, column=0, sticky="w")
        self.ann_content_text = tk.Text(top, wrap=tk.WORD, height=4, bg="#050510", fg="#00ffcc", insertbackground="#00ffcc")
        self.ann_content_text.grid(row=2, column=1, columnspan=2, sticky="we")

        ttk.Button(top, text="Add Annotation", style="Cyber.TButton", command=self._add_annotation).grid(row=3, column=1, sticky="e", pady=5)

        bottom = ttk.LabelFrame(frame, text="All Annotations", style="Cyber.TLabelframe")
        bottom.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.annotations_list = tk.Text(bottom, wrap=tk.WORD, height=12, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.annotations_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        ttk.Button(frame, text="Refresh Annotations", style="Cyber.TButton", command=self._refresh_annotations).pack(pady=5)

        self._refresh_annotations()

    def _add_annotation(self):
        kind = self.ann_kind_var.get().strip()
        label = self.ann_label_var.get().strip()
        content = self.ann_content_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("Annotation", "Content required.")
            return
        meta = {
            "dataset": self.orchestrator.active_dataset,
            "run_id": self.orchestrator.last_run_id,
        }
        self.orchestrator.annotation_store.add_annotation(kind, content, label, meta)
        self._log(f"[Annotation] Added ({kind}, {label})")
        self._refresh_annotations()

    def _refresh_annotations(self):
        anns = self.orchestrator.annotation_store.list_annotations()
        self.annotations_list.configure(state=tk.NORMAL)
        self.annotations_list.delete("1.0", tk.END)
        for a in anns:
            self.annotations_list.insert(tk.END, json.dumps(a, indent=2) + "\n\n")
        self.annotations_list.configure(state=tk.DISABLED)

    def _build_deployment_tab(self, parent):
        frame = ttk.Frame(parent, style="Cyber.TFrame")
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        ttk.Label(frame, text="Deployment Tools", style="Cyber.TLabel").pack(anchor="w", padx=5, pady=5)

        btn_frame = ttk.Frame(frame, style="Cyber.TFrame")
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(btn_frame, text="Generate Dockerfile", style="Cyber.TButton", command=self._generate_dockerfile).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Export Edge Profile", style="Cyber.TButton", command=self._export_edge_profile).pack(side=tk.LEFT, padx=2)

        self.deployment_text = tk.Text(frame, wrap=tk.WORD, height=16, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.deployment_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _generate_dockerfile(self):
        self.orchestrator.deployment_manager.generate_dockerfile("megasystem_v17")
        self.deployment_text.configure(state=tk.NORMAL)
        self.deployment_text.delete("1.0", tk.END)
        self.deployment_text.insert(tk.END, "Dockerfile generated in deployment/ directory.\n")
        self.deployment_text.configure(state=tk.DISABLED)
        self._log("[Deployment] Dockerfile generated.")

    def _export_edge_profile(self):
        self.orchestrator.deployment_manager.export_edge_profile("edge_profile.json")
        self.deployment_text.configure(state=tk.NORMAL)
        self.deployment_text.delete("1.0", tk.END)
        self.deployment_text.insert(tk.END, "Edge profile exported to deployment/edge_profile.json.\n")
        self.deployment_text.configure(state=tk.DISABLED)
        self._log("[Deployment] Edge profile exported.")

    def _update_mlop_views(self, result: Dict[str, Any]):
        # Refresh bias/drift tab after pipeline
        self._refresh_bias_drift()
        # Refresh runs list
        self._refresh_runs()
        # Refresh datasets info if active
        if self.orchestrator.active_dataset:
            ds = self.orchestrator.dataset_manager.get_dataset(self.orchestrator.active_dataset)
            self.active_dataset_info.configure(state=tk.NORMAL)
            self.active_dataset_info.delete("1.0", tk.END)
            self.active_dataset_info.insert(tk.END, json.dumps(ds, indent=2))
            self.active_dataset_info.configure(state=tk.DISABLED)

    def mainloop_safe(self):
        try:
            self.mainloop()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    app = MegaSystemApp()
    app.mainloop_safe()
