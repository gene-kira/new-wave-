#!/usr/bin/env python3
"""
Universal Data Engine v16-GODSWARM-NEURAL-QUANTUM

Modes merged:
- v15-MONSTER
- v15-ULTRA (GPU-everything)
- v15-SWARM (multi-node)
- v16-GODSWARM-NEURAL (neural physics, neural compression, neural anomaly)
- v16-QUANTUM (quantum-inspired probabilistic decision engine)

Key behavior:
- FULL SILENCE by default (no console spam unless debug enabled)
- Hybrid Brain: DQN + PPO + heuristics + GOAP + Behavior Tree + Swarm Consensus
- Quantum-inspired decision layer on top of MetaBrain
- Neural Physics: learned dynamics model (if weights available)
- Neural Compression: learned autoencoder (if weights available)
- Neural Anomaly: learned detector (if weights available)
- Multi-Physics: PyBullet + WorldModel + Kinematic + NeuralPhysics
- Multi-Compression: zstd, lz4, brotli, gzip, zlib + NeuralCompressor
- Multi-Network: UDP + TLS Mesh + Swarm Broadcast
- Multi-Node: roles, consensus, trust mesh, overseer
- Real-world data support: load pretrained weights from /models
"""

import os
import sys
import json
import time
import math
import random
import socket
import ssl
import hmac
import hashlib
import threading
import asyncio
import zlib
import gzip
import importlib
import subprocess
from typing import Any, Dict, List, Optional, Callable, Tuple

# =========================
# Global debug mode
# =========================

DEBUG_MODE = os.environ.get("UDE_DEBUG", "OFF").upper()
# OFF   -> no output
# ERROR -> only errors/anomalies
# LIGHT -> ticks + key decisions
# HEAVY -> full brain dump

def dbg_heavy(*args, **kwargs):
    if DEBUG_MODE == "HEAVY":
        print(*args, **kwargs)

def dbg_light(*args, **kwargs):
    if DEBUG_MODE in ("LIGHT", "HEAVY"):
        print(*args, **kwargs)

def dbg_error(*args, **kwargs):
    if DEBUG_MODE in ("ERROR", "LIGHT", "HEAVY"):
        print(*args, **kwargs)

def dbg_off(*args, **kwargs):
    pass

# =========================
# Build tools detection / installer (Windows only)
# =========================

def check_msvc_build_tools() -> bool:
    if os.name != "nt":
        return True
    try:
        result = subprocess.run(["cl"], capture_output=True, text=True)
        if "Microsoft" in result.stdout or "Microsoft" in result.stderr:
            dbg_light("[SETUP] Detected MSVC via cl.exe")
            return True
    except Exception:
        pass
    try:
        import winreg
        paths = [
            r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
            r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86",
            r"SOFTWARE\Microsoft\VisualStudio\17.0\VC\Runtimes\x64",
            r"SOFTWARE\Microsoft\VisualStudio\17.0\VC\Runtimes\x86",
        ]
        for p in paths:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, p)
                _ = winreg.QueryValueEx(key, "Installed")
                dbg_light(f"[SETUP] Detected MSVC runtime in registry: {p}")
                return True
            except Exception:
                continue
    except Exception:
        pass
    dbg_error("[SETUP] MSVC Build Tools not detected.")
    return False

def auto_install_msvc():
    if os.name != "nt":
        return
    dbg_error("[SETUP] Microsoft Build Tools missing.")
    try:
        import webbrowser
        webbrowser.open("https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio")
        dbg_error("[SETUP] Please install C++ Build Tools + Windows SDK.")
    except Exception as e:
        dbg_error("[SETUP] Failed to open browser for Build Tools:", e)

if os.name == "nt":
    if not check_msvc_build_tools():
        auto_install_msvc()
else:
    dbg_light("[SETUP] Non-Windows OS detected, MSVC check skipped.")

# =========================
# Autoloader
# =========================

def borg_autoload(required: List[str]) -> Dict[str, Any]:
    loaded: Dict[str, Any] = {}
    for lib in required:
        try:
            loaded[lib] = importlib.import_module(lib)
            continue
        except ImportError:
            dbg_light(f"[AUTOLOADER] Missing '{lib}', attempting installation...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", lib],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            loaded[lib] = importlib.import_module(lib)
            dbg_light(f"[AUTOLOADER] Installed and loaded '{lib}'")
        except Exception as e:
            dbg_error(f"[AUTOLOADER] Failed to install '{lib}': {e}")
            loaded[lib] = None
    return loaded

LIBS = borg_autoload([
    "numpy",
    "torch",
    "redis",
    "pybullet",
    "websockets",
    "zstandard",
    "brotli",
    "lz4",
    "lz4.frame",
])

np = LIBS.get("numpy")
torch = LIBS.get("torch")
redis_mod = LIBS.get("redis")
pybullet = LIBS.get("pybullet")
websockets = LIBS.get("websockets")
zstandard = LIBS.get("zstandard")
brotli = LIBS.get("brotli")
lz4 = LIBS.get("lz4")
lz4f = LIBS.get("lz4.frame")

DEVICE = (
    torch.device("cuda" if (torch is not None and torch.cuda.is_available()) else "cpu")
    if torch
    else None
)

# =========================
# Paths / models
# =========================

def safe_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    for c in bad:
        name = name.replace(c, "_")
    return name

def safe_path(base_dir: str, name: str) -> str:
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, safe_filename(name))

MODELS_DIR = "models"

def load_torch_model(path: str, build_fn: Callable[[], Any]) -> Optional[Any]:
    if torch is None:
        return None
    full = os.path.join(MODELS_DIR, path)
    if not os.path.isfile(full):
        dbg_light(f"[MODELS] No weights at {full}, using fresh model.")
        return build_fn()
    try:
        model = build_fn()
        state = torch.load(full, map_location=DEVICE)
        model.load_state_dict(state)
        dbg_light(f"[MODELS] Loaded weights from {full}")
        return model
    except Exception as e:
        dbg_error(f"[MODELS] Failed to load {full}: {e}")
        return build_fn()

# =========================
# Simple compressor
# =========================

class Compressor:
    def encode(self, data: Dict[str, Any]) -> Tuple[bytes, str]:
        raw = json.dumps(data).encode("utf-8")
        try:
            return gzip.compress(raw), "gzip"
        except Exception:
            return zlib.compress(raw), "zlib"

    def decode(self, blob: bytes, codec: str) -> Dict[str, Any]:
        if codec == "gzip":
            raw = gzip.decompress(blob)
        elif codec == "zlib":
            raw = zlib.decompress(blob)
        else:
            raw = blob
        return json.loads(raw.decode("utf-8"))

# =========================
# AdaptiveCompressor
# =========================

class AdaptiveCompressor:
    def __init__(self):
        self.codecs = {
            "zstd": zstandard is not None,
            "brotli": brotli is not None,
            "lz4": lz4f is not None,
            "gzip": True,
            "zlib": True,
        }

    def encode(self, data: Dict[str, Any], data_type: str = "generic") -> Tuple[bytes, str]:
        raw = json.dumps(data).encode("utf-8")
        if data_type in ("telemetry", "game", "video"):
            preferred = ["zstd", "lz4", "brotli", "gzip", "zlib"]
        else:
            preferred = ["zlib", "gzip", "zstd", "brotli", "lz4"]
        for codec in preferred:
            if not self.codecs.get(codec, False):
                continue
            try:
                if codec == "zstd" and zstandard:
                    cctx = zstandard.ZstdCompressor(level=3)
                    return cctx.compress(raw), "zstd"
                if codec == "brotli" and brotli:
                    return brotli.compress(raw), "brotli"
                if codec == "lz4" and lz4f:
                    return lz4f.compress(raw), "lz4"
                if codec == "gzip":
                    return gzip.compress(raw), "gzip"
                if codec == "zlib":
                    return zlib.compress(raw, level=6), "zlib"
            except Exception:
                continue
        return raw, "none"

    def decode(self, blob: bytes, codec: str) -> Dict[str, Any]:
        if codec == "zstd" and zstandard:
            dctx = zstandard.ZstdDecompressor()
            raw = dctx.decompress(blob)
        elif codec == "brotli" and brotli:
            raw = brotli.decompress(blob)
        elif codec == "lz4" and lz4f:
            raw = lz4f.decompress(blob)
        elif codec == "gzip":
            raw = gzip.decompress(blob)
        elif codec == "zlib":
            raw = zlib.decompress(blob)
        else:
            raw = blob
        return json.loads(raw.decode("utf-8"))

# =========================
# NeuralCompressor (v16-NEURAL)
# =========================

class NeuralCompressor:
    def __init__(self, latent_dim: int = 32):
        self.use_torch = torch is not None
        self.latent_dim = latent_dim
        self.model = None
        if self.use_torch:
            self._build()

    def _build(self):
        class AutoEncoder(torch.nn.Module):
            def __init__(self, input_dim, latent_dim):
                super().__init__()
                self.enc = torch.nn.Sequential(
                    torch.nn.Linear(input_dim, 128),
                    torch.nn.ReLU(),
                    torch.nn.Linear(128, latent_dim),
                )
                self.dec = torch.nn.Sequential(
                    torch.nn.Linear(latent_dim, 128),
                    torch.nn.ReLU(),
                    torch.nn.Linear(128, input_dim),
                )

            def encode(self, x):
                return self.enc(x)

            def decode(self, z):
                return self.dec(z)

        input_dim = 128
        def builder():
            return AutoEncoder(input_dim, self.latent_dim).to(DEVICE)
        self.model = load_torch_model("neural_compressor.pt", builder)

    def _encode_vector(self, data: Dict[str, Any]) -> List[float]:
        vals = []
        for v in data.values():
            if isinstance(v, (int, float)):
                vals.append(float(v))
            if len(vals) >= 128:
                break
        while len(vals) < 128:
            vals.append(0.0)
        return vals

    def encode(self, data: Dict[str, Any]) -> Tuple[bytes, str]:
        if not (self.use_torch and self.model):
            raw = json.dumps(data).encode("utf-8")
            return zlib.compress(raw), "zlib"
        vec = self._encode_vector(data)
        x = torch.tensor([vec], dtype=torch.float32, device=DEVICE)
        with torch.no_grad():
            z = self.model.encode(x)[0].cpu().tolist()
        blob = json.dumps(z).encode("utf-8")
        return blob, "neural"

    def decode(self, blob: bytes) -> Dict[str, Any]:
        if not (self.use_torch and self.model):
            raw = zlib.decompress(blob)
            return json.loads(raw.decode("utf-8"))
        z = json.loads(blob.decode("utf-8"))
        zt = torch.tensor([z], dtype=torch.float32, device=DEVICE)
        with torch.no_grad():
            x = self.model.decode(zt)[0].cpu().tolist()
        data = {f"f{i}": float(v) for i, v in enumerate(x)}
        return data

# =========================
# Domain thresholds
# =========================

def domain_thresholds(domain: str) -> Dict[str, float]:
    d = domain.lower()
    if d == "game":
        return {
            "pos_x": 1e6,
            "pos_y": 1e6,
            "vel_x": 1e5,
            "vel_y": 1e5,
            "health": 1e4,
            "score": 1e12,
            "speed": 1e5,
            "temp": 1e3,
        }
    if d == "telemetry":
        return {
            "temp": 500.0,
            "speed": 1e4,
            "pos_x": 1e5,
            "pos_y": 1e5,
        }
    return {
        "pos_x": 1e5,
        "pos_y": 1e5,
        "vel_x": 1e4,
        "vel_y": 1e4,
        "health": 1e4,
        "score": 1e12,
        "temp": 1e3,
        "speed": 1e5,
    }

# =========================
# Trust manager
# =========================

class TrustManager:
    def __init__(self):
        self.trust: Dict[str, float] = {}

    def adjust(self, node_id: str, delta: float):
        v = self.trust.get(node_id, 1.0) + delta
        self.trust[node_id] = max(0.0, min(1.0, v))

    def get(self, node_id: str) -> float:
        return self.trust.get(node_id, 1.0)

# =========================
# Neural anomaly detector (v16-NEURAL)
# =========================

class NeuralAnomalyDetector:
    def __init__(self, domain: str = "generic"):
        self.domain = domain.lower()
        self.use_torch = torch is not None
        self.model = None
        if self.use_torch:
            self._build()

    def _build(self):
        class AnomNet(torch.nn.Module):
            def __init__(self, input_dim=64):
                super().__init__()
                self.net = torch.nn.Sequential(
                    torch.nn.Linear(input_dim, 128),
                    torch.nn.ReLU(),
                    torch.nn.Linear(128, 64),
                    torch.nn.ReLU(),
                    torch.nn.Linear(64, 1),
                    torch.nn.Sigmoid(),
                )

            def forward(self, x):
                return self.net(x)

        def builder():
            return AnomNet().to(DEVICE)
        self.model = load_torch_model(f"neural_anomaly_{self.domain}.pt", builder)

    def _encode_state(self, state: Dict[str, Any]) -> List[float]:
        vals = []
        for v in state.values():
            if isinstance(v, (int, float)):
                vals.append(float(v))
            if len(vals) >= 64:
                break
        while len(vals) < 64:
            vals.append(0.0)
        return vals

    def is_anomaly(self, state: Dict[str, Any]) -> Optional[bool]:
        if not (self.use_torch and self.model):
            return None
        x = torch.tensor([self._encode_state(state)], dtype=torch.float32, device=DEVICE)
        with torch.no_grad():
            p = self.model(x)[0].item()
        return p > 0.8

# =========================
# Classic anomaly detector
# =========================

class AnomalyDetector:
    def __init__(self, trust: TrustManager, node_id: str, domain: str = "generic"):
        self.trust = trust
        self.node_id = node_id
        self.domain = domain.lower()
        self.base = domain_thresholds(self.domain)
        self.stats: Dict[str, Dict[str, float]] = {}
        self.last_state: Dict[str, Any] = {}
        self.game_max_teleport = 5000.0
        self.game_max_speed = 10000.0

    def _update_stats(self, k: str, v: float):
        s = self.stats.get(k)
        if s is None:
            self.stats[k] = {"c": 1.0, "m": v, "m2": 0.0}
            return
        c = s["c"] + 1.0
        d = v - s["m"]
        m = s["m"] + d / c
        m2 = s["m2"] + d * (v - m)
        self.stats[k] = {"c": c, "m": m, "m2": m2}

    def _mean_std(self, k: str) -> Tuple[float, float]:
        s = self.stats.get(k)
        if not s or s["c"] < 5:
            return 0.0, 0.0
        var = s["m2"] / max(1.0, s["c"] - 1.0)
        return s["m"], math.sqrt(max(var, 0.0))

    def _domain_anom(self, k: str, v: float) -> bool:
        lim = self.base.get(k)
        return lim is not None and abs(v) > lim

    def _adaptive_anom(self, k: str, v: float, sigma: float = 5.0) -> bool:
        m, s = self._mean_std(k)
        if s <= 0:
            return False
        return abs(v - m) > sigma * s

    def _game_checks(self, state: Dict[str, Any]) -> bool:
        if self.domain != "game":
            return False
        vx = float(state.get("vel_x", 0.0))
        vy = float(state.get("vel_y", 0.0))
        speed = math.sqrt(vx * vx + vy * vy)
        if speed > self.game_max_speed:
            dbg_error("[ANOMALY] game speed", speed)
            return True
        if self.last_state:
            for k in ("pos_x", "pos_y"):
                if k in state and k in self.last_state:
                    try:
                        dv = abs(float(state[k]) - float(self.last_state[k]))
                        if dv > self.game_max_teleport:
                            dbg_error("[ANOMALY] teleport", k, dv)
                            return True
                    except Exception:
                        pass
        return False

    def check(self, state: Dict[str, Any]) -> bool:
        anom = False
        for k, v in state.items():
            try:
                fv = float(v)
            except Exception:
                continue
            self._update_stats(k, fv)
            if self._domain_anom(k, fv):
                anom = True
            if self._adaptive_anom(k, fv):
                anom = True
        if self._game_checks(state):
            anom = True
        self.last_state = dict(state)
        self.trust.adjust(self.node_id, -0.05 if anom else +0.01)
        return anom

# =========================
# Importance + Delta + Reconstructor
# =========================

class Importance:
    def classify(self, key: str) -> str:
        k = key.lower()
        if k in ("id", "device_id", "frame_id", "timestamp"):
            return "critical"
        if any(s in k for s in ("pos_", "vel_", "health", "score", "temp", "speed", "state")):
            return "important"
        if any(s in k for s in ("bg", "color", "shadow", "fx", "ui")):
            return "cosmetic"
        return "ignore"

    def extract(self, state: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for k, v in state.items():
            lvl = self.classify(k)
            if lvl in ("critical", "important"):
                out[k] = v
        return out

class Delta:
    def diff(self, prev: Dict[str, Any], cur: Dict[str, Any]) -> Dict[str, Any]:
        d = {}
        for k, v in cur.items():
            if k not in prev or prev[k] != v:
                d[k] = v
        for k in prev.keys() - cur.keys():
            d[k] = None
        return d

    def apply(self, prev: Dict[str, Any], d: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(prev)
        for k, v in d.items():
            if v is None:
                out.pop(k, None)
            else:
                out[k] = v
        return out

class Reconstructor:
    def __init__(self):
        self.last_full: Dict[str, Any] = {}

    def cache(self, full: Dict[str, Any]):
        self.last_full = dict(full)

    def reconstruct(self, essential: Dict[str, Any], original: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(essential)
        for k, v in original.items():
            if k not in out:
                out[k] = self.last_full.get(
                    k, v if isinstance(v, (int, float, str)) else None
                )
        return out

# =========================
# ProbabilisticField + AlteredStatesMode + TransformerPredictor
# =========================

class ProbabilisticField:
    def __init__(self, mean: float = 0.0, var: float = 1.0):
        self.mean = float(mean)
        self.var = float(max(var, 1e-6))

    def sample(self) -> float:
        return random.gauss(self.mean, self.var)

class AlteredStatesMode:
    def __init__(self, mode: str = "normal"):
        self.mode = mode

    def apply(self, value: float) -> float:
        if self.mode == "normal":
            return value
        if self.mode == "exploratory":
            return value + random.gauss(0.0, 0.1)
        if self.mode == "hallucinatory":
            return value + random.gauss(0.0, 0.5)
        return value

class TransformerPredictor:
    def __init__(self, d_model: int = 32, nhead: int = 4, num_layers: int = 2):
        self.use_torch = torch is not None
        self.d_model = d_model
        self.nhead = nhead
        self.num_layers = num_layers
        self.model = None
        if self.use_torch:
            self._build()

    def _build(self):
        class SimpleTransformer(torch.nn.Module):
            def __init__(self, d_model, nhead, num_layers):
                super().__init__()
                encoder_layer = torch.nn.TransformerEncoderLayer(
                    d_model=d_model, nhead=nhead
                )
                self.encoder = torch.nn.TransformerEncoder(
                    encoder_layer, num_layers=num_layers
                )
                self.out = torch.nn.Linear(d_model, 1)

            def forward(self, seq):
                enc = self.encoder(seq)
                last = enc[-1]
                return self.out(last)

        def builder():
            return SimpleTransformer(self.d_model, self.nhead, self.num_layers).to(DEVICE)
        self.model = load_torch_model("transformer_predictor.pt", builder)

    def _encode_state(self, state: Dict[str, Any]) -> List[float]:
        nums = []
        for _, v in state.items():
            if isinstance(v, (int, float)):
                nums.append(float(v))
            if len(nums) >= self.d_model:
                break
        while len(nums) < self.d_model:
            nums.append(0.0)
        return nums

    def predict_next_scalar(self, history: List[Dict[str, Any]]) -> float:
        if not history:
            return 0.0
        if not self.use_torch or self.model is None:
            last = history[-1]
            vals = [float(v) for v in last.values() if isinstance(v, (int, float))]
            return sum(vals) / len(vals) if vals else 0.0
        seq = [self._encode_state(s) for s in history]
        x = torch.tensor(seq, dtype=torch.float32, device=DEVICE).unsqueeze(1)
        with torch.no_grad():
            y = self.model(x)
        return float(y.item())

# =========================
# PredictiveReconstructor (ULTRA + NEURAL)
# =========================

class PredictiveReconstructor:
    def __init__(self):
        self.cache: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []
        self.use_ml = torch is not None
        self.lstm = None
        self.lstm_out = None
        self.transformer = TransformerPredictor()
        self.mode = AlteredStatesMode("normal")
        if self.use_ml:
            self._build_lstm()

    def _build_lstm(self):
        input_dim = 8
        hidden_dim = 16
        class LSTMNet(torch.nn.Module):
            def __init__(self, inp, hid):
                super().__init__()
                self.lstm = torch.nn.LSTM(inp, hid, batch_first=True)
                self.out = torch.nn.Linear(hid, 1)
            def forward(self, x):
                o, _ = self.lstm(x)
                last = o[:, -1, :]
                return self.out(last)
        def builder():
            return LSTMNet(input_dim, hidden_dim).to(DEVICE)
        net = load_torch_model("lstm_predictor.pt", builder)
        self.lstm = net.lstm
        self.lstm_out = net.out

    def cache_state(self, key: str, state: Dict[str, Any]) -> None:
        self.cache.setdefault("history", [])
        self.cache["history"].append(state)
        if len(self.cache["history"]) > 64:
            self.cache["history"].pop(0)
        self.cache[key] = state
        self.history.append(state)
        if len(self.history) > 128:
            self.history.pop(0)

    def _context_vector(self, state: Dict[str, Any]) -> List[float]:
        nums = []
        for _, v in state.items():
            if isinstance(v, (int, float)):
                nums.append(float(v))
            if len(nums) >= 8:
                break
        while len(nums) < 8:
            nums.append(0.0)
        return nums

    def _temporal_predict_scalar_lstm(self) -> float:
        if not (self.use_ml and self.lstm and torch and "history" in self.cache):
            return 0.0
        hist = self.cache["history"]
        if not hist:
            return 0.0
        seq = [self._context_vector(s) for s in hist]
        x = torch.tensor(seq, dtype=torch.float32, device=DEVICE).unsqueeze(0)
        with torch.no_grad():
            out, _ = self.lstm(x)
            last = out[:, -1, :]
            y = self.lstm_out(last)
        return float(y.item())

    def _temporal_predict_scalar_transformer(self) -> float:
        return self.transformer.predict_next_scalar(self.history)

    def gpu_blend_vectors(self, vectors: List[List[float]]) -> List[float]:
        if not vectors:
            return []
        if torch:
            t = torch.tensor(vectors, dtype=torch.float32, device=DEVICE)
            mean = t.mean(dim=0)
            return mean.cpu().tolist()
        elif np is not None:
            t = np.array(vectors, dtype=np.float32)
            return t.mean(axis=0).tolist()
        else:
            length = len(vectors[0])
            acc = [0.0] * length
            for v in vectors:
                for i in range(length):
                    acc[i] += v[i]
            return [x / len(vectors) for x in acc]

    def reconstruct(self, base_state: Dict[str, Any], missing_keys: List[str]) -> Dict[str, Any]:
        reconstructed = dict(base_state)
        last_full = self.cache.get("last_full", {})

        prob_fields: Dict[str, ProbabilisticField] = {}
        for k, v in base_state.items():
            if isinstance(v, (int, float)):
                prob_fields[k] = ProbabilisticField(mean=float(v), var=1.0)

        lstm_trend = self._temporal_predict_scalar_lstm()
        transformer_trend = self._temporal_predict_scalar_transformer()
        trends = [[lstm_trend], [transformer_trend]]
        blended_trend_vec = self.gpu_blend_vectors(trends) if any(trends) else [0.0]
        trend = blended_trend_vec[0] if blended_trend_vec else 0.0
        trend = self.mode.apply(trend)

        for k in missing_keys:
            if k in last_full:
                reconstructed[k] = last_full[k]
            else:
                reconstructed[k] = self.default_value_for(k, prob_fields, trend)

        return reconstructed

    def default_value_for(
        self,
        key: str,
        prob_fields: Dict[str, ProbabilisticField],
        trend: float,
    ) -> Any:
        k = key.lower()
        if k.startswith("pos_") or k.startswith("x") or k.startswith("y"):
            pf = prob_fields.get("pos_x") or ProbabilisticField(mean=trend, var=1.0)
            val = pf.sample()
            return self.mode.apply(val)
        if k.startswith("vel_"):
            pf = prob_fields.get("vel_x") or ProbabilisticField(mean=0.0, var=0.1)
            return self.mode.apply(pf.sample())
        if "bg" in k or "color" in k:
            return "synthetic_bg"
        if "shadow" in k:
            return "medium"
        return None

# =========================
# Plugin system
# =========================

class PluginManager:
    def __init__(self, plugin_dir: str = "plugins"):
        self.plugin_dir = plugin_dir
        self.plugins: Dict[str, Any] = {}
        self.last_mtimes: Dict[str, float] = {}
        self.running = False

    def load_plugins(self):
        if not os.path.isdir(self.plugin_dir):
            return
        if self.plugin_dir not in sys.path:
            sys.path.insert(0, self.plugin_dir)
        for fname in os.listdir(self.plugin_dir):
            if not fname.endswith(".py"):
                continue
            mod_name = fname[:-3]
            path = os.path.join(self.plugin_dir, fname)
            mtime = os.path.getmtime(path)
            try:
                if mod_name in self.plugins:
                    if self.last_mtimes.get(mod_name, 0) != mtime:
                        dbg_light(f"[PLUGIN] Reloading {mod_name}")
                        self.plugins[mod_name] = importlib.reload(self.plugins[mod_name])
                        self.last_mtimes[mod_name] = mtime
                else:
                    mod = importlib.import_module(mod_name)
                    self.plugins[mod_name] = mod
                    self.last_mtimes[mod_name] = mtime
                    dbg_light(f"[PLUGIN] Loaded {mod_name}")
            except Exception as e:
                dbg_error(f"[PLUGIN] Failed to load/reload {mod_name}: {e}")

    def start_hot_swap_watcher(self, interval: float = 2.0):
        if self.running:
            return
        self.running = True

        def loop():
            while self.running:
                try:
                    self.load_plugins()
                except Exception as e:
                    dbg_error(f"[PLUGIN] Watcher error: {e}")
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True).start()

    def get_domain_rules(self, domain: str) -> Dict[str, Any]:
        for p in self.plugins.values():
            if hasattr(p, "get_domain_rules"):
                rules = p.get_domain_rules(domain)
                if rules:
                    return rules
        return {}

    def register_v16_extensions(self, engine: "UniversalDataEngineV16GodSwarmNeuralQuantum"):
        for p in self.plugins.values():
            if hasattr(p, "register_v16_extensions"):
                p.register_v16_extensions(engine)

# =========================
# Domain rules
# =========================

def get_builtin_domain_rules(domain: str) -> Dict[str, Any]:
    domain = domain.lower()
    if domain == "game":
        return {
            "critical": ["id", "pos_x", "pos_y", "state"],
            "important": ["vel_x", "vel_y", "health", "score"],
            "cosmetic": ["bg_color", "shadow_quality", "fx_level"],
            "ignore": [],
        }
    if domain == "video":
        return {
            "critical": ["frame_id", "timestamp"],
            "important": ["camera_pos_x", "camera_pos_y"],
            "cosmetic": ["bg_style", "color_grade"],
            "ignore": ["debug_overlay"],
        }
    if domain == "telemetry":
        return {
            "critical": ["device_id", "timestamp", "status"],
            "important": ["temp", "pressure", "speed"],
            "cosmetic": ["ui_theme"],
            "ignore": ["debug_log"],
        }
    return {}

# =========================
# WorldModel physics
# =========================

class WorldModel:
    def __init__(self):
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.gravity = -9.81
        self.friction = 0.1

    def update_from_state(self, state: Dict[str, Any]):
        eid = str(state.get("id", "default"))
        pos_x = float(state.get("pos_x", 0.0))
        pos_y = float(state.get("pos_y", 0.0))
        vel_x = float(state.get("vel_x", 0.0))
        vel_y = float(state.get("vel_y", 0.0))
        heading = float(state.get("heading", 0.0))
        self.entities[eid] = {
            "pos_x": pos_x,
            "pos_y": pos_y,
            "vel_x": vel_x,
            "vel_y": vel_y,
            "heading": heading,
        }

    def step_physics(self, dt: float = 0.05):
        for e in self.entities.values():
            e["vel_y"] += self.gravity * dt
            e["vel_x"] *= (1.0 - self.friction * dt)
            e["pos_x"] += e["vel_x"] * dt
            e["pos_y"] += e["vel_y"] * dt
            if e["pos_y"] < 0.0:
                e["pos_y"] = 0.0
                e["vel_y"] = 0.0

# =========================
# KinematicModel
# =========================

class KinematicModel:
    def __init__(self):
        self.entities: Dict[str, Dict[str, Any]] = {}

    def update_from_state(self, state: Dict[str, Any]):
        eid = str(state.get("id", "default"))
        self.entities.setdefault(eid, {
            "x": float(state.get("pos_x", 0.0)),
            "y": float(state.get("pos_y", 0.0)),
            "heading": float(state.get("heading", 0.0)),
            "speed": float(state.get("speed", 0.0)),
        })

    def step(self, eid: str, throttle: float, steer: float, dt: float = 0.016):
        e = self.entities.get(eid)
        if not e:
            return
        e["speed"] += throttle * 10.0 * dt
        e["speed"] = max(0.0, min(e["speed"], 1000.0))
        e["heading"] += steer * 1.0 * dt
        e["x"] += e["speed"] * math.cos(e["heading"]) * dt
        e["y"] += e["speed"] * math.sin(e["heading"]) * dt

# =========================
# PyBullet physics backend
# =========================

class PhysicsBackend:
    def __init__(self, use_pybullet: bool = True):
        self.use_pybullet = bool(pybullet and use_pybullet)
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.cid = None
        if self.use_pybullet:
            try:
                self._init_pybullet()
                dbg_light("[PHYSICS] PyBullet backend enabled.")
            except Exception as e:
                dbg_error("[PHYSICS] PyBullet init failed, disabling:", e)
                self.use_pybullet = False

    def _init_pybullet(self):
        self.cid = pybullet.connect(pybullet.DIRECT)
        pybullet.setGravity(0, 0, -9.81, physicsClientId=self.cid)
        ground = pybullet.createCollisionShape(
            pybullet.GEOM_PLANE, physicsClientId=self.cid
        )
        pybullet.createMultiBody(0, ground, physicsClientId=self.cid)

    def add_or_update_entity(self, eid: str, state: Dict[str, Any]):
        if not self.use_pybullet:
            return
        if eid not in self.entities:
            col = pybullet.createCollisionShape(
                pybullet.GEOM_SPHERE, radius=0.5, physicsClientId=self.cid
            )
            body = pybullet.createMultiBody(
                baseMass=1.0,
                baseCollisionShapeIndex=col,
                basePosition=[0, 0, 1],
                physicsClientId=self.cid,
            )
            self.entities[eid] = {"body": body}

    def apply_control(self, eid: str, throttle: float, steering: float):
        if not self.use_pybullet:
            return
        body = self.entities.get(eid, {}).get("body")
        if body is None:
            return
        fx = throttle * math.cos(steering)
        fy = throttle * math.sin(steering)
        pybullet.applyExternalForce(
            body,
            -1,
            [fx, fy, 0],
            [0, 0, 0],
            pybullet.WORLD_FRAME,
            physicsClientId=self.cid,
        )

    def step(self, dt: float = 1.0 / 60.0):
        if self.use_pybullet:
            pybullet.stepSimulation(physicsClientId=self.cid)

# =========================
# NeuralPhysics (v16-NEURAL)
# =========================

class NeuralPhysics:
    def __init__(self):
        self.use_torch = torch is not None
        self.model = None
        if self.use_torch:
            self._build()

    def _build(self):
        class DynNet(torch.nn.Module):
            def __init__(self, input_dim=8, output_dim=4):
                super().__init__()
                self.net = torch.nn.Sequential(
                    torch.nn.Linear(input_dim, 64),
                    torch.nn.ReLU(),
                    torch.nn.Linear(64, 64),
                    torch.nn.ReLU(),
                    torch.nn.Linear(64, output_dim),
                )

            def forward(self, x):
                return self.net(x)

        def builder():
            return DynNet().to(DEVICE)
        self.model = load_torch_model("neural_physics.pt", builder)

    def _encode(self, state: Dict[str, Any], throttle: float, steer: float) -> List[float]:
        pos_x = float(state.get("pos_x", 0.0))
        pos_y = float(state.get("pos_y", 0.0))
        vel_x = float(state.get("vel_x", 0.0))
        vel_y = float(state.get("vel_y", 0.0))
        speed = float(state.get("speed", 0.0))
        heading = float(state.get("heading", 0.0))
        return [pos_x, pos_y, vel_x, vel_y, speed, heading, throttle, steer]

    def step(self, state: Dict[str, Any], throttle: float, steer: float) -> Dict[str, Any]:
        if not (self.use_torch and self.model):
            return state
        x = torch.tensor([self._encode(state, throttle, steer)], dtype=torch.float32, device=DEVICE)
        with torch.no_grad():
            dx = self.model(x)[0].cpu().tolist()
        pos_x = float(state.get("pos_x", 0.0)) + dx[0]
        pos_y = float(state.get("pos_y", 0.0)) + dx[1]
        vel_x = float(state.get("vel_x", 0.0)) + dx[2]
        vel_y = float(state.get("vel_y", 0.0)) + dx[3]
        new_state = dict(state)
        new_state["pos_x"] = pos_x
        new_state["pos_y"] = pos_y
        new_state["vel_x"] = vel_x
        new_state["vel_y"] = vel_y
        return new_state

# =========================
# PPO RL
# =========================

class PPOPolicy:
    def __init__(self, obs_dim: int = 16, act_dim: int = 2):
        self.obs_dim = obs_dim
        self.act_dim = act_dim
        self.use_torch = torch is not None
        self.buffer: List[Tuple[List[float], List[float], float]] = []
        if self.use_torch:
            self._build()

    def _build(self):
        class Actor(torch.nn.Module):
            def __init__(self, obs, act):
                super().__init__()
                self.net = torch.nn.Sequential(
                    torch.nn.Linear(obs, 64),
                    torch.nn.Tanh(),
                    torch.nn.Linear(64, 64),
                    torch.nn.Tanh(),
                    torch.nn.Linear(64, act),
                )

            def forward(self, x):
                return self.net(x)

        class Critic(torch.nn.Module):
            def __init__(self, obs):
                super().__init__()
                self.net = torch.nn.Sequential(
                    torch.nn.Linear(obs, 64),
                    torch.nn.Tanh(),
                    torch.nn.Linear(64, 64),
                    torch.nn.Tanh(),
                    torch.nn.Linear(64, 1),
                )

            def forward(self, x):
                return self.net(x)

        def build_actor():
            return Actor(self.obs_dim, self.act_dim).to(DEVICE)

        def build_critic():
            return Critic(self.obs_dim).to(DEVICE)

        self.actor = load_torch_model("ppo_actor.pt", build_actor)
        self.critic = load_torch_model("ppo_critic.pt", build_critic)
        self.opt_actor = torch.optim.Adam(self.actor.parameters(), lr=3e-4)
        self.opt_critic = torch.optim.Adam(self.critic.parameters(), lr=1e-3)

    def _obs_vec(self, state: Dict[str, Any]) -> List[float]:
        vals = []
        for v in state.values():
            if isinstance(v, (int, float)):
                vals.append(float(v))
            if len(vals) >= self.obs_dim:
                break
        while len(vals) < self.obs_dim:
            vals.append(0.0)
        return vals

    def act(self, state: Dict[str, Any]) -> List[float]:
        if not self.use_torch:
            return [random.uniform(-1, 1), random.uniform(-1, 1)]
        x = torch.tensor(
            self._obs_vec(state), dtype=torch.float32, device=DEVICE
        ).unsqueeze(0)
        with torch.no_grad():
            mu = self.actor(x)[0]
        a = torch.tanh(mu).cpu().tolist()
        return a

    def store(self, state: Dict[str, Any], action: List[float], reward: float):
        if not self.use_torch:
            return
        self.buffer.append((self._obs_vec(state), action, reward))
        if len(self.buffer) > 4096:
            self.buffer.pop(0)

    def train_step(self, epochs: int = 2, batch_size: int = 64, gamma: float = 0.99):
        if not (self.use_torch and self.buffer):
            return
        obs = [b[0] for b in self.buffer]
        acts = [b[1] for b in self.buffer]
        rews = [b[2] for b in self.buffer]
        returns = []
        G = 0.0
        for r in reversed(rews):
            G = r + gamma * G
            returns.append(G)
        returns.reverse()
        obs_t = torch.tensor(obs, dtype=torch.float32, device=DEVICE)
        act_t = torch.tensor(acts, dtype=torch.float32, device=DEVICE)
        ret_t = torch.tensor(returns, dtype=torch.float32, device=DEVICE).unsqueeze(1)
        for _ in range(epochs):
            idx = list(range(len(obs)))
            random.shuffle(idx)
            for i in range(0, len(idx), batch_size):
                b = idx[i : i + batch_size]
                o = obs_t[b]
                a = act_t[b]
                R = ret_t[b]
                v = self.critic(o)
                adv = R - v.detach()
                mu = self.actor(o)
                loss_actor = ((mu - a) ** 2 * adv.sign()).mean()
                loss_critic = ((v - R) ** 2).mean()
                self.opt_actor.zero_grad()
                loss_actor.backward()
                self.opt_actor.step()
                self.opt_critic.zero_grad()
                loss_critic.backward()
                self.opt_critic.step()

# =========================
# DQN Meta
# =========================

class DQNMeta:
    def __init__(self, state_dim: int = 6, action_dim: int = 16):
        self.use_torch = torch is not None
        self.state_dim = state_dim
        self.action_dim = action_dim
        if self.use_torch:
            self._build()
        self.memory: List[Tuple[List[float], int, float, List[float]]] = []

    def _build(self):
        class QNet(torch.nn.Module):
            def __init__(self, s, a):
                super().__init__()
                self.net = torch.nn.Sequential(
                    torch.nn.Linear(s, 64),
                    torch.nn.ReLU(),
                    torch.nn.Linear(64, 64),
                    torch.nn.ReLU(),
                    torch.nn.Linear(64, a),
                )

            def forward(self, x):
                return self.net(x)

        def builder():
            return QNet(self.state_dim, self.action_dim).to(DEVICE)
        self.q = load_torch_model("dqn_meta.pt", builder)
        self.opt = torch.optim.Adam(self.q.parameters(), lr=1e-3)
        self.gamma = 0.95

    def act(self, state: List[float], eps: float = 0.1) -> int:
        if not self.use_torch or random.random() < eps:
            return random.randint(0, self.action_dim - 1)
        x = torch.tensor([state], dtype=torch.float32, device=DEVICE)
        with torch.no_grad():
            qv = self.q(x)[0]
        return int(torch.argmax(qv).item())

    def store(self, s: List[float], a: int, r: float, s2: List[float]):
        if not self.use_torch:
            return
        self.memory.append((s, a, r, s2))
        if len(self.memory) > 5000:
            self.memory.pop(0)

    def train_step(self, batch_size: int = 64):
        if not (self.use_torch and self.memory):
            return
        batch = random.sample(self.memory, min(batch_size, len(self.memory)))
        s = torch.tensor([b[0] for b in batch], dtype=torch.float32, device=DEVICE)
        a = torch.tensor([b[1] for b in batch], dtype=torch.long, device=DEVICE)
        r = torch.tensor([b[2] for b in batch], dtype=torch.float32, device=DEVICE)
        s2 = torch.tensor([b[3] for b in batch], dtype=torch.float32, device=DEVICE)
        with torch.no_grad():
            q2 = self.q(s2).max(dim=1)[0]
            target = r + self.gamma * q2
        qv = self.q(s)
        q_a = qv.gather(1, a.unsqueeze(1)).squeeze(1)
        loss = ((q_a - target) ** 2).mean()
        self.opt.zero_grad()
        loss.backward()
        self.opt.step()

# =========================
# Swarm memory
# =========================

class SwarmMemory:
    def __init__(self, node_id: str, use_redis: bool = True):
        self.node_id = node_id
        self.local = {"shared": {}, "node": {}}
        self.use_redis = bool(redis_mod and use_redis)
        self.redis = None
        if self.use_redis:
            try:
                self.redis = redis_mod.Redis(host="127.0.0.1", port=6379, db=0)
                self.redis.ping()
                dbg_light("[MEMORY] Redis connected.")
            except Exception as e:
                dbg_error("[MEMORY] Redis unavailable:", e)
                self.use_redis = False

    def set_shared(self, key: str, value: Any):
        self.local["shared"][key] = value
        if self.use_redis and self.redis:
            try:
                self.redis.set(f"swarm:{key}", json.dumps(value))
            except Exception as e:
                dbg_error("[MEMORY] Redis set error:", e)

    def get_shared(self, key: str, default: Any = None) -> Any:
        if self.use_redis and self.redis:
            try:
                v = self.redis.get(f"swarm:{key}")
                if v is not None:
                    return json.loads(v.decode("utf-8"))
            except Exception as e:
                dbg_error("[MEMORY] Redis get error:", e)
        return self.local["shared"].get(key, default)

    def set_node(self, key: str, value: Any):
        self.local["node"][key] = value

    def get_node(self, key: str, default: Any = None) -> Any:
        return self.local["node"].get(key, default)

    def save_local(self, base_dir: str = "swarm_memory"):
        path = safe_path(base_dir, f"{self.node_id}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.local, f, indent=2)
        except Exception as e:
            dbg_error("[MEMORY] Failed to save local:", e)

# =========================
# TLS mesh networking
# =========================

class SecureMesh:
    def __init__(
        self,
        node_id: str,
        secret_key: bytes,
        ca_cert: Optional[str] = None,
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
    ):
        self.node_id = node_id
        self.secret_key = secret_key
        self.ca_cert = ca_cert
        self.certfile = certfile
        self.keyfile = keyfile
        self.peers: List[Tuple[str, int]] = []

    def add_peer(self, host: str, port: int):
        self.peers.append((host, port))

    def _sign(self, payload: bytes) -> str:
        return hmac.new(self.secret_key, payload, hashlib.sha256).hexdigest()

    def _verify(self, payload: bytes, sig: str) -> bool:
        expected = self._sign(payload)
        return hmac.compare_digest(expected, sig)

    def _make_ssl_context_client(self):
        ctx = (
            ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=self.ca_cert)
            if self.ca_cert
            else ssl.create_default_context()
        )
        if self.certfile and self.keyfile:
            ctx.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        return ctx

    def _make_ssl_context_server(self):
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        if self.ca_cert:
            ctx.load_verify_locations(self.ca_cert)
            ctx.verify_mode = ssl.CERT_OPTIONAL
        if self.certfile and self.keyfile:
            ctx.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        return ctx

    async def send_packet(self, data: Dict[str, Any]):
        payload = json.dumps({"node_id": self.node_id, "data": data}).encode("utf-8")
        sig = self._sign(payload)
        packet = json.dumps(
            {"payload": payload.decode("utf-8"), "sig": sig}
        ).encode("utf-8")
        ctx = self._make_ssl_context_client()
        for host, port in self.peers:
            try:
                reader, writer = await asyncio.open_connection(host, port, ssl=ctx)
                writer.write(packet + b"\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                dbg_error("[MESH] send error to", host, port, ":", e)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        on_packet,
    ):
        try:
            line = await reader.readline()
            if not line:
                return
            obj = json.loads(line.decode("utf-8"))
            payload = obj["payload"].encode("utf-8")
            sig = obj["sig"]
            if not self._verify(payload, sig):
                dbg_error("[MESH] invalid signature")
                return
            msg = json.loads(payload.decode("utf-8"))
            await on_packet(msg)
        except Exception as e:
            dbg_error("[MESH] handle error:", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def serve(self, host: str, port: int, on_packet):
        ctx = self._make_ssl_context_server()
        server = await asyncio.start_server(
            lambda r, w: self._handle_client(r, w, on_packet),
            host,
            port,
            ssl=ctx,
        )
        async with server:
            await server.serve_forever()

# =========================
# Async network engine (UDP)
# =========================

class AsyncNetworkEngine:
    def __init__(self, compressor: AdaptiveCompressor):
        self.compressor = compressor
        self.udp_handler: Optional[Callable[[Dict[str, Any]], None]] = None

    async def start_udp_server(self, host: str = "127.0.0.1", port: int = 9001):
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: self.UDPProtocol(self.compressor, self.udp_handler),
            local_addr=(host, port),
        )
        dbg_light(f"[UDP] Async server on {host}:{port}")
        await asyncio.Future()

    class UDPProtocol(asyncio.DatagramProtocol):
        def __init__(
            self,
            compressor: AdaptiveCompressor,
            handler: Optional[Callable[[Dict[str, Any]], None]],
        ):
            self.compressor = compressor
            self.handler = handler

        def datagram_received(self, data: bytes, addr):
            if not data:
                return
            codec_id = data[0]
            codec = ["zstd", "brotli", "lz4", "gzip", "zlib", "none"][codec_id]
            blob = data[1:]
            msg = self.compressor.decode(blob, codec)
            if self.handler:
                self.handler(msg)

    async def udp_send(
        self, host: str, port: int, data: Dict[str, Any], data_type: str = "generic"
    ):
        blob, codec = self.compressor.encode(data, data_type)
        codec_id = {"zstd": 0, "brotli": 1, "lz4": 2, "gzip": 3, "zlib": 4, "none": 5}[
            codec
        ]
        packet = bytes([codec_id]) + blob
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(),
            remote_addr=(host, port),
        )
        transport.sendto(packet)
        transport.close()

# =========================
# Swarm roles + behavior tree + autopilot
# =========================

ROLES = ["scout", "aggressor", "stabilizer", "support", "overseer"]

def choose_role(node_id: str, memory: SwarmMemory) -> str:
    roles = memory.get_shared("roles", {})
    if node_id in roles:
        return roles[node_id]
    assigned = set(roles.values())
    for r in ROLES:
        if r not in assigned:
            roles[node_id] = r
            memory.set_shared("roles", roles)
            return r
    r = random.choice(ROLES)
    roles[node_id] = r
    memory.set_shared("roles", roles)
    return r

def behavior_tree(role: str, state: Dict[str, Any], anomaly: bool) -> str:
    health = float(state.get("health", 100.0))
    if anomaly and health < 30.0:
        return "retreat"
    if role == "scout":
        return "explore"
    if role == "aggressor":
        return "attack"
    if role == "stabilizer":
        return "hold"
    if role == "support":
        return "assist"
    if role == "overseer":
        return "coordinate"
    return "idle"

def autopilot_from_intent(intent: str, rl_action: List[float]) -> Tuple[float, float]:
    base_throttle = 0.0
    base_steer = 0.0
    if intent == "explore":
        base_throttle = 0.7
    elif intent == "attack":
        base_throttle = 1.0
    elif intent == "retreat":
        base_throttle = -0.8
    elif intent == "hold":
        base_throttle = 0.1
    elif intent == "assist":
        base_throttle = 0.5
    elif intent == "coordinate":
        base_throttle = 0.3
    throttle = base_throttle + 0.3 * rl_action[0]
    steer = base_steer + 0.8 * rl_action[1]
    throttle = max(-1.0, min(1.0, throttle))
    steer = max(-1.0, min(1.0, steer))
    return throttle, steer

# =========================
# Swarm consensus
# =========================

class SwarmConsensus:
    def __init__(self, memory: SwarmMemory):
        self.memory = memory

    def update_trust_view(self, node_id: str, trust: float):
        trusts = self.memory.get_shared("trusts", {})
        trusts[node_id] = trust
        self.memory.set_shared("trusts", trusts)

    def get_global_trust(self) -> float:
        trusts = self.memory.get_shared("trusts", {})
        if not trusts:
            return 1.0
        return sum(trusts.values()) / len(trusts)

    def choose_overseer(self) -> Optional[str]:
        trusts = self.memory.get_shared("trusts", {})
        if not trusts:
            return None
        return max(trusts.items(), key=lambda x: x[1])[0]

# =========================
# MetaBrain
# =========================

class MetaBrain:
    def __init__(self, use_ml: bool = True):
        self.use_ml = use_ml and (torch is not None)
        self.dqn = DQNMeta(state_dim=6, action_dim=16) if self.use_ml else None

    def encode_state(
        self,
        trust: float,
        anomaly: bool,
        compression_ratio: float,
        reward: float,
        global_trust: float,
        health_ok: float,
    ) -> List[float]:
        return [
            float(trust),
            1.0 if anomaly else 0.0,
            float(compression_ratio),
            float(reward),
            float(global_trust),
            float(health_ok),
        ]

    def decode_action(self, a: int) -> Dict[str, str]:
        physics = "pybullet" if (a & 1) else "world"
        compressor = "adaptive" if (a & 2) else "simple"
        network = "udp" if (a & 4) else "mesh"
        recon = "predictive" if (a & 8) else "conservative"
        return {
            "physics": physics,
            "compressor": compressor,
            "network": network,
            "recon": recon,
        }

    def decide(
        self,
        trust: float,
        anomaly: bool,
        compression_ratio: float,
        reward: float,
        global_trust: float,
        health_ok: float,
    ) -> Dict[str, str]:
        s = self.encode_state(trust, anomaly, compression_ratio, reward, global_trust, health_ok)
        if not self.use_ml or self.dqn is None:
            physics = "pybullet" if (trust > 0.4 and not anomaly and health_ok > 0.5) else "world"
            compressor = "adaptive" if (trust > 0.6 and not anomaly) else "simple"
            network = "udp" if global_trust > 0.5 else "mesh"
            recon = "predictive" if not anomaly else "conservative"
            return {
                "physics": physics,
                "compressor": compressor,
                "network": network,
                "recon": recon,
            }
        a = self.dqn.act(s, eps=0.1)
        return self.decode_action(a)

    def train(self, traces: List[Dict[str, Any]]):
        if not self.use_ml or self.dqn is None:
            return
        for t in traces:
            s = self.encode_state(
                t["trust_before"],
                t["anomaly"],
                t["compression_ratio"],
                t["reward"],
                t["global_trust"],
                t["health_ok"],
            )
            s2 = self.encode_state(
                t["trust_after"],
                t["anomaly"],
                t["compression_ratio"],
                t["reward"],
                t["global_trust"],
                t["health_ok"],
            )
            r = t["reward"] + (t["trust_after"] - t["trust_before"]) * 2.0
            a = 0
            self.dqn.store(s, a, r, s2)
        self.dqn.train_step()

# =========================
# Quantum-inspired decision engine (v16-QUANTUM)
# =========================

class QuantumDecisionEngine:
    def __init__(self, meta: MetaBrain):
        self.meta = meta
        self.temperature = 0.7

    def _softmax(self, logits: List[float]) -> List[float]:
        m = max(logits)
        exps = [math.exp((x - m) / max(1e-6, self.temperature)) for x in logits]
        s = sum(exps) or 1.0
        return [e / s for e in exps]

    def _sample(self, probs: List[float]) -> int:
        r = random.random()
        c = 0.0
        for i, p in enumerate(probs):
            c += p
            if r <= c:
                return i
        return len(probs) - 1

    def _enumerate_variants(self, base_decision: Dict[str, str]) -> List[Dict[str, str]]:
        variants = []
        physics_opts = ["pybullet", "world", "neural"]
        comp_opts = ["simple", "adaptive", "neural"]
        net_opts = ["udp", "mesh"]
        recon_opts = ["conservative", "predictive"]
        for ph in physics_opts:
            for co in comp_opts:
                for ne in net_opts:
                    for re in recon_opts:
                        variants.append({
                            "physics": ph,
                            "compressor": co,
                            "network": ne,
                            "recon": re,
                        })
        return variants

    def _score_variant(
        self,
        variant: Dict[str, str],
        trust: float,
        anomaly: bool,
        compression_ratio: float,
        reward: float,
        global_trust: float,
        health_ok: float,
    ) -> float:
        score = 0.0
        if variant["physics"] == "pybullet":
            score += 0.5 * health_ok
        if variant["physics"] == "world":
            score += 0.3
        if variant["physics"] == "neural":
            score += 0.7 * trust
        if variant["compressor"] == "adaptive":
            score += 0.4 * compression_ratio
        if variant["compressor"] == "neural":
            score += 0.6 * trust
        if variant["network"] == "udp":
            score += 0.3
        if variant["network"] == "mesh":
            score += 0.2 * global_trust
        if variant["recon"] == "predictive" and not anomaly:
            score += 0.4
        if variant["recon"] == "conservative" and anomaly:
            score += 0.5
        score += reward * 0.1
        return score

    def decide(
        self,
        trust: float,
        anomaly: bool,
        compression_ratio: float,
        reward: float,
        global_trust: float,
        health_ok: float,
    ) -> Dict[str, str]:
        base = self.meta.decide(trust, anomaly, compression_ratio, reward, global_trust, health_ok)
        variants = self._enumerate_variants(base)
        logits = [
            self._score_variant(v, trust, anomaly, compression_ratio, reward, global_trust, health_ok)
            for v in variants
        ]
        probs = self._softmax(logits)
        idx = self._sample(probs)
        decision = variants[idx]
        return decision

# =========================
# Replay buffer
# =========================

class ReplayBuffer:
    def __init__(self, capacity: int = 20000):
        self.capacity = capacity
        self.data: List[Dict[str, Any]] = []

    def add(self, item: Dict[str, Any]):
        self.data.append(item)
        if len(self.data) > self.capacity:
            self.data.pop(0)

    def sample(self, n: int) -> List[Dict[str, Any]]:
        if not self.data:
            return []
        return random.sample(self.data, min(n, len(self.data)))

# =========================
# GOAP planner
# =========================

class GOAPPlanner:
    def __init__(self):
        self.goals: Dict[str, float] = {}
        self.actions: List[Dict[str, Any]] = []

    def set_goal(self, name: str, priority: float):
        self.goals[name] = priority

    def add_action(self, name: str, cost: float, pre: Dict[str, Any], eff: Dict[str, Any]):
        self.actions.append({"name": name, "cost": cost, "pre": pre, "eff": eff})

    def plan(self, world_state: Dict[str, Any]) -> List[str]:
        if not self.goals or not self.actions:
            return []
        goal = max(self.goals.items(), key=lambda x: x[1])[0]
        candidates = []
        for a in self.actions:
            ok = True
            for k, v in a["pre"].items():
                if world_state.get(k) != v:
                    ok = False
                    break
            if ok:
                candidates.append(a)
        if not candidates:
            return []
        best = min(candidates, key=lambda x: x["cost"])
        return [best["name"]]

# =========================
# UniversalDataEngine v16-GODSWARM-NEURAL-QUANTUM
# =========================

class UniversalDataEngineV16GodSwarmNeuralQuantum:
    def __init__(self, node_id: str, domain: str = "game"):
        self.node_id = node_id
        self.domain = domain

        self.trust = TrustManager()
        self.anomaly_classic = AnomalyDetector(self.trust, node_id, domain)
        self.anomaly_neural = NeuralAnomalyDetector(domain)
        self.importance = Importance()
        self.delta = Delta()
        self.recon_simple = Reconstructor()
        self.recon_predictive = PredictiveReconstructor()

        self.compressor_simple = Compressor()
        self.compressor_adaptive = AdaptiveCompressor()
        self.compressor_neural = NeuralCompressor()

        self.physics_pybullet = PhysicsBackend(use_pybullet=True)
        self.world_model = WorldModel()
        self.kinematic_model = KinematicModel()
        self.neural_physics = NeuralPhysics()

        self.rl = PPOPolicy(obs_dim=16, act_dim=2)

        self.memory = SwarmMemory(node_id, use_redis=True)
        self.role = choose_role(node_id, self.memory)
        self.consensus = SwarmConsensus(self.memory)

        self.mesh: Optional[SecureMesh] = None
        self.async_net = AsyncNetworkEngine(self.compressor_adaptive)

        self.plugin_mgr = PluginManager()
        self.plugin_mgr.start_hot_swap_watcher()
        builtin_rules = get_builtin_domain_rules(domain)
        plugin_rules = self.plugin_mgr.get_domain_rules(domain)
        self.domain_rules = {**builtin_rules, **plugin_rules}
        self.plugin_mgr.register_v16_extensions(self)

        self.meta = MetaBrain(use_ml=True)
        self.quantum = QuantumDecisionEngine(self.meta)
        self.replay = ReplayBuffer()
        self.planner = GOAPPlanner()
        self._init_default_goals_actions()

        self.last_essential: Dict[str, Any] = {}
        self.last_full: Dict[str, Any] = {}
        self.last_decision: Dict[str, Any] = {}
        self.health_flags: Dict[str, bool] = {
            "pybullet": self.physics_pybullet.use_pybullet,
            "redis": self.memory.use_redis,
        }

    def _init_default_goals_actions(self):
        self.planner.set_goal("stay_alive", 1.0)
        self.planner.set_goal("explore", 0.8)
        self.planner.set_goal("coordinate_swarm", 0.9)
        self.planner.add_action(
            "evade",
            cost=1.0,
            pre={"anomaly": True},
            eff={"safe": True},
        )
        self.planner.add_action(
            "advance",
            cost=0.5,
            pre={"anomaly": False},
            eff={"exploring": True},
        )
        self.planner.add_action(
            "sync_swarm",
            cost=0.3,
            pre={"anomaly": False},
            eff={"coordinated": True},
        )

    def check_health(self):
        if self.physics_pybullet.use_pybullet and self.physics_pybullet.cid is None:
            self.health_flags["pybullet"] = False
        if self.memory.use_redis and self.memory.redis is None:
            self.health_flags["redis"] = False

    def attach_mesh(self, mesh: SecureMesh):
        self.mesh = mesh

    def _encode_delta(self, delta: Dict[str, Any], decision: Dict[str, str]) -> Tuple[bytes, str, str]:
        mode = decision["compressor"]
        if mode == "adaptive":
            blob, codec = self.compressor_adaptive.encode(delta, self.domain)
            return blob, codec, "adaptive"
        elif mode == "neural":
            blob, codec = self.compressor_neural.encode(delta)
            return blob, codec, "neural"
        else:
            blob, codec = self.compressor_simple.encode(delta)
            return blob, codec, "simple"

    def _decode_delta(self, blob: bytes, codec: str, mode: str) -> Dict[str, Any]:
        if mode == "adaptive":
            return self.compressor_adaptive.decode(blob, codec)
        elif mode == "neural":
            return self.compressor_neural.decode(blob)
        else:
            return self.compressor_simple.decode(blob, codec)

    def _reconstruct_full(
        self,
        essential: Dict[str, Any],
        original: Dict[str, Any],
        decoded_delta: Dict[str, Any],
        decision: Dict[str, str],
    ) -> Dict[str, Any]:
        base = self.delta.apply(self.last_full, decoded_delta)
        base = self.recon_simple.reconstruct(base, original)
        self.recon_simple.cache(base)

        if decision["recon"] == "predictive":
            self.recon_predictive.cache_state("last_full", base)
            missing = [k for k in original.keys() if k not in base]
            if missing:
                base = self.recon_predictive.reconstruct(base, missing)
        return base

    def _apply_physics(self, eid: str, state: Dict[str, Any], throttle: float, steer: float, decision: Dict[str, str]) -> Dict[str, Any]:
        self.world_model.update_from_state(state)
        self.kinematic_model.update_from_state(state)
        if decision["physics"] == "pybullet" and self.physics_pybullet.use_pybullet:
            self.physics_pybullet.add_or_update_entity(eid, state)
            self.physics_pybullet.apply_control(eid, throttle, steer)
            self.physics_pybullet.step()
            return state
        elif decision["physics"] == "neural":
            new_state = self.neural_physics.step(state, throttle, steer)
            self.world_model.update_from_state(new_state)
            self.kinematic_model.update_from_state(new_state)
            return new_state
        else:
            self.world_model.step_physics(dt=0.016)
            self.kinematic_model.step(eid, throttle, steer, dt=0.016)
            return state

    async def _broadcast(self, summary: Dict[str, Any], decision: Dict[str, str]):
        if decision["network"] == "udp":
            try:
                await self.async_net.udp_send("127.0.0.1", 9001, summary, self.domain)
            except Exception as e:
                dbg_error("[NET] UDP send error:", e)
        else:
            if self.mesh:
                try:
                    await self.mesh.send_packet(summary)
                except Exception as e:
                    dbg_error("[NET] Mesh send error:", e)

    def process_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self.check_health()
        eid = str(state.get("id", self.node_id))

        essential = self.importance.extract(state)
        if not self.last_essential:
            d = essential
        else:
            d = self.delta.diff(self.last_essential, essential)
        self.last_essential = dict(essential)

        trust_before = self.trust.get(self.node_id)
        raw_size = len(json.dumps(d).encode("utf-8")) or 1
        reward_est = 0.0

        global_trust = self.consensus.get_global_trust()
        health_ok = 1.0 if all(self.health_flags.values()) else 0.0

        temp_decision = self.meta.decide(trust_before, False, 1.0, reward_est, global_trust, health_ok)
        blob_temp, _, comp_mode_temp = self._encode_delta(d, temp_decision)
        comp_ratio_temp = raw_size / max(1, len(blob_temp))

        decision = self.quantum.decide(
            trust_before,
            False,
            comp_ratio_temp,
            reward_est,
            global_trust,
            health_ok,
        )

        blob, codec, comp_mode = self._encode_delta(d, decision)
        comp_ratio = raw_size / max(1, len(blob))

        decoded = self._decode_delta(blob, codec, comp_mode)
        full_recon = self._reconstruct_full(essential, state, decoded, decision)
        self.last_full = dict(full_recon)

        is_anom_neural = self.anomaly_neural.is_anomaly(full_recon)
        is_anom_classic = self.anomaly_classic.check(full_recon)
        is_anom = is_anom_classic if is_anom_neural is None else (is_anom_classic or is_anom_neural)
        trust_after = self.trust.get(self.node_id)

        self.consensus.update_trust_view(self.node_id, trust_after)
        global_trust = self.consensus.get_global_trust()
        overseer = self.consensus.choose_overseer()

        rl_action = self.rl.act(full_recon)
        intent = behavior_tree(self.role, full_recon, is_anom)
        throttle, steer = autopilot_from_intent(intent, rl_action)

        reward = 1.0
        if is_anom:
            reward -= 2.0
        speed = abs(float(full_recon.get("speed", 0.0)))
        if 0 < speed < 20000:
            reward += 0.1

        self.rl.store(full_recon, rl_action, reward)

        decision = self.quantum.decide(
            trust_after,
            is_anom,
            comp_ratio,
            reward,
            global_trust,
            health_ok,
        )
        self.last_decision = decision

        full_recon = self._apply_physics(eid, full_recon, throttle, steer, decision)

        self.memory.set_node("last_state", full_recon)
        self.memory.set_node("last_intent", intent)
        self.memory.set_node("last_action", {"throttle": throttle, "steer": steer})
        self.memory.set_node("last_anomaly", is_anom)
        self.memory.set_node("trust", trust_after)
        self.memory.set_node("decision", decision)
        self.memory.set_node("overseer", overseer)

        world_flags = {"anomaly": is_anom}
        plan = self.planner.plan(world_flags)
        self.memory.set_node("last_plan", plan)

        trace = {
            "trust_before": trust_before,
            "trust_after": trust_after,
            "anomaly": is_anom,
            "compression_ratio": comp_ratio,
            "reward": reward,
            "decision": decision,
            "global_trust": global_trust,
            "health_ok": health_ok,
        }
        self.replay.add(trace)

        if DEBUG_MODE in ("LIGHT", "HEAVY"):
            dbg_light("[DEBUG] Decision:", decision)
            dbg_light("[DEBUG] Physics:", decision["physics"])
            dbg_light("[DEBUG] Compressor:", decision["compressor"])
            dbg_light("[DEBUG] Network:", decision["network"])
            dbg_light("[DEBUG] Recon:", decision["recon"])
            dbg_light("[DEBUG] Trust:", trust_after)
            dbg_light("[DEBUG] Global Trust:", global_trust)
            dbg_light("[DEBUG] Overseer:", overseer)
            dbg_light("[DEBUG] Anomaly:", is_anom)
            dbg_light("[DEBUG] RL Action:", rl_action)
            dbg_light("[DEBUG] Intent:", intent)
            dbg_light("[DEBUG] Plan:", plan)
            dbg_light("[DEBUG] Compression ratio:", comp_ratio)

        out = {
            "node_id": self.node_id,
            "role": self.role,
            "intent": intent,
            "plan": plan,
            "anomaly": is_anom,
            "trust": trust_after,
            "global_trust": global_trust,
            "overseer": overseer,
            "throttle": throttle,
            "steer": steer,
            "compressed_bytes": len(blob),
            "codec": codec,
            "compression_mode": comp_mode,
            "decision": decision,
        }
        return out

    def train_rl(self):
        self.rl.train_step()

    def train_meta(self):
        traces = self.replay.sample(128)
        self.meta.train(traces)

    def save_memory(self):
        self.memory.save_local()

# =========================
# Runner
# =========================

async def run_engine_v16_godswarm_neural_quantum(
    node_id: str,
    domain: str = "game",
    mesh_host: Optional[str] = None,
    mesh_port: Optional[int] = None,
    ca_cert: Optional[str] = None,
    certfile: Optional[str] = None,
    keyfile: Optional[str] = None,
    secret_key: bytes = b"supersecret",
):
    engine = UniversalDataEngineV16GodSwarmNeuralQuantum(node_id, domain)

    async def on_packet(msg):
        data = msg.get("data", {})
        nid = msg.get("node_id", "unknown")
        if "trust" in data:
            engine.trust.adjust(nid, 0.01)
            engine.consensus.update_trust_view(nid, float(data["trust"]))

    if mesh_host and mesh_port:
        mesh = SecureMesh(node_id, secret_key, ca_cert, certfile, keyfile)
        mesh.add_peer(mesh_host, mesh_port)
        engine.attach_mesh(mesh)

        async def server_task():
            await mesh.serve("0.0.0.0", mesh_port, on_packet)

        asyncio.create_task(server_task())

    frame = 0
    while True:
        state = {
            "id": node_id,
            "timestamp": time.time(),
            "pos_x": math.sin(frame * 0.01) * 1000.0,
            "pos_y": math.cos(frame * 0.01) * 1000.0,
            "vel_x": math.cos(frame * 0.01) * 100.0,
            "vel_y": -math.sin(frame * 0.01) * 100.0,
            "speed": 100.0 + 10.0 * math.sin(frame * 0.05),
            "health": 100.0 - 0.01 * frame,
            "score": frame * 10,
        }

        summary = engine.process_state(state)
        if frame % 60 == 0:
            if DEBUG_MODE in ("LIGHT", "HEAVY"):
                dbg_light("[V16-GODSWARM-NEURAL-QUANTUM TICK]", frame, summary)
            engine.train_rl()
            engine.train_meta()
            engine.save_memory()
            await engine._broadcast(summary, summary["decision"])

        frame += 1
        await asyncio.sleep(0.016)

# =========================
# Entry point
# =========================

if __name__ == "__main__":
    node_id = os.environ.get("NODE_ID", "node-1")
    domain = os.environ.get("DOMAIN", "game")
    mesh_host = os.environ.get("MESH_HOST")
    mesh_port = int(os.environ["MESH_PORT"]) if "MESH_PORT" in os.environ else None
    ca_cert = os.environ.get("MESH_CA")
    certfile = os.environ.get("MESH_CERT")
    keyfile = os.environ.get("MESH_KEY")
    secret_key = os.environ.get("MESH_SECRET", "supersecret").encode("utf-8")

    try:
        asyncio.run(
            run_engine_v16_godswarm_neural_quantum(
                node_id,
                domain,
                mesh_host,
                mesh_port,
                ca_cert,
                certfile,
                keyfile,
                secret_key,
            )
        )
    except KeyboardInterrupt:
        if DEBUG_MODE != "OFF":
            print("\n[EXIT] v16-GODSWARM-NEURAL-QUANTUM engine stopped by user")
