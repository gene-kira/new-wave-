#!/usr/bin/env python3
"""
Universal Data Engine v10
True distributed deep RL, multi-agent planning, swarm-level autopilot, world modeling,
anomaly detection, hot-swappable plugins, fault-tolerant consensus, multi-node memory fabric,
and behavior-tree/GOAP-based decision making.

Single-file organism, split into 3 delivery chunks (this is Part 1 of 3).
"""

import importlib
import subprocess
import sys
import os
import json
import zlib
import gzip
import time
import random
import asyncio
import threading
import socket
from typing import Any, Dict, List, Optional, Callable, Tuple

# =========================
# Autonomous Library Autoloader
# =========================

def borg_autoload(required: List[str]) -> Dict[str, Any]:
    loaded: Dict[str, Any] = {}
    for lib in required:
        try:
            loaded[lib] = importlib.import_module(lib)
            continue
        except ImportError:
            print(f"[AUTOLOADER] Missing '{lib}', attempting installation...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib])
            loaded[lib] = importlib.import_module(lib)
            print(f"[AUTOLOADER] Installed and loaded '{lib}'")
        except Exception as e:
            print(f"[AUTOLOADER] Failed to install '{lib}': {e}")
            loaded[lib] = None
    return loaded


LIBS = borg_autoload([
    "numpy",
    "torch",
    "websockets",
    "zstandard",
    "brotli",
    "lz4",
    "lz4.frame",
    "tkinter",
])

np = LIBS["numpy"]
torch = LIBS["torch"]
websockets = LIBS["websockets"]
zstandard = LIBS["zstandard"]
brotli = LIBS["brotli"]
lz4 = LIBS["lz4"]
lz4f = LIBS["lz4.frame"]
tkinter = LIBS["tkinter"]


# =========================
# Hot-swappable Plugin System
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
                    # hot-swap if file changed
                    if self.last_mtimes.get(mod_name, 0) != mtime:
                        print(f"[PLUGIN] Reloading {mod_name}")
                        self.plugins[mod_name] = importlib.reload(self.plugins[mod_name])
                        self.last_mtimes[mod_name] = mtime
                else:
                    mod = importlib.import_module(mod_name)
                    self.plugins[mod_name] = mod
                    self.last_mtimes[mod_name] = mtime
                    print(f"[PLUGIN] Loaded {mod_name}")
            except Exception as e:
                print(f"[PLUGIN] Failed to load/reload {mod_name}: {e}", file=sys.stderr)

    def start_hot_swap_watcher(self, interval: float = 2.0):
        if self.running:
            return
        self.running = True

        def loop():
            while self.running:
                try:
                    self.load_plugins()
                except Exception as e:
                    print(f"[PLUGIN] Watcher error: {e}")
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True).start()

    def get_domain_rules(self, domain: str) -> Dict[str, Any]:
        for p in self.plugins.values():
            if hasattr(p, "get_domain_rules"):
                rules = p.get_domain_rules(domain)
                if rules:
                    return rules
        return {}

    def register_models(self, engine: "UniversalDataEngine"):
        for p in self.plugins.values():
            if hasattr(p, "register_models"):
                p.register_models(engine)

    def register_compressors(self, engine: "UniversalDataEngine"):
        for p in self.plugins.values():
            if hasattr(p, "register_compressors"):
                p.register_compressors(engine)

    def register_world_models(self, engine: "UniversalDataEngine"):
        for p in self.plugins.values():
            if hasattr(p, "register_world_models"):
                p.register_world_models(engine)


# =========================
# ProbabilisticField
# =========================

class ProbabilisticField:
    def __init__(self, mean: float = 0.0, var: float = 1.0):
        self.mean = float(mean)
        self.var = float(max(var, 1e-6))

    def sample(self) -> float:
        return random.gauss(self.mean, self.var)

    def update(self, observation: float, weight: float = 1.0):
        self.mean = (self.mean + weight * observation) / (1.0 + weight)
        self.var = max(1e-6, self.var * 0.9)

    def to_dict(self) -> Dict[str, float]:
        return {"mean": self.mean, "var": self.var}


# =========================
# AlteredStatesMode
# =========================

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


# =========================
# TransformerPredictor
# =========================

class TransformerPredictor:
    def __init__(self, d_model: int = 32, nhead: int = 4, num_layers: int = 2):
        self.use_torch = torch is not None
        self.d_model = d_model
        self.nhead = nhead
        self.num_layers = num_layers
        self.model = None
        if self.use_torch:
            self._build_model()

    def _build_model(self):
        class SimpleTransformer(torch.nn.Module):
            def __init__(self, d_model, nhead, num_layers):
                super().__init__()
                encoder_layer = torch.nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead)
                self.encoder = torch.nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
                self.out = torch.nn.Linear(d_model, 1)

            def forward(self, seq):
                enc = self.encoder(seq)
                last = enc[-1]
                return self.out(last)

        self.model = SimpleTransformer(self.d_model, self.nhead, self.num_layers)

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
        x = torch.tensor(seq, dtype=torch.float32).unsqueeze(1)
        with torch.no_grad():
            y = self.model(x)
        return float(y.item())


# =========================
# DataImportanceClassifier
# =========================

class DataImportanceClassifier:
    def __init__(self):
        self.use_ml = torch is not None
        self.model = None
        if self.use_ml:
            self._build_model()

    def _build_model(self):
        input_dim = 16
        hidden_dim = 32
        output_dim = 4

        class MLP(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.net = torch.nn.Sequential(
                    torch.nn.Linear(input_dim, hidden_dim),
                    torch.nn.ReLU(),
                    torch.nn.Linear(hidden_dim, output_dim)
                )

            def forward(self, x):
                return self.net(x)

        self.model = MLP()

    def _key_to_features(self, key: str):
        vec = [0.0] * 16
        for i, ch in enumerate(key[:16]):
            vec[i] = (ord(ch) % 64) / 64.0
        if torch:
            return torch.tensor(vec, dtype=torch.float32).unsqueeze(0)
        elif np is not None:
            return np.array(vec, dtype=np.float32)[None, :]
        else:
            return vec

    def _heuristic_importance(self, key: str, value: Any) -> str:
        k = key.lower()
        if "id" in k or "pos" in k or "state" in k or "timestamp" in k:
            return "critical"
        if "vel" in k or "score" in k or "health" in k or "temp" in k or "speed" in k:
            return "important"
        if "bg" in k or "color" in k or "shadow" in k or "fx" in k or "ui" in k:
            return "cosmetic"
        return "ignore"

    def predict_importance(self, key: str, value: Any) -> str:
        if not self.use_ml or self.model is None or torch is None:
            return self._heuristic_importance(key, value)
        try:
            with torch.no_grad():
                x = self._key_to_features(key)
                logits = self.model(x)
                pred = int(torch.argmax(logits, dim=1).item())
            mapping = {3: "critical", 2: "important", 1: "cosmetic", 0: "ignore"}
            return mapping.get(pred, self._heuristic_importance(key, value))
        except Exception:
            return self._heuristic_importance(key, value)

    def train_on_synthetic(self, epochs: int = 30, lr: float = 1e-2):
        if not (self.use_ml and self.model and torch):
            print("[ML] Torch not available, skipping training.")
            return
        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        loss_fn = torch.nn.CrossEntropyLoss()

        def label_for_key(k: str) -> int:
            k = k.lower()
            if "id" in k or "pos" in k or "state" in k or "timestamp" in k:
                return 3
            if "vel" in k or "score" in k or "health" in k or "temp" in k or "speed" in k:
                return 2
            if "bg" in k or "color" in k or "shadow" in k or "fx" in k or "ui" in k:
                return 1
            return 0

        keys = [
            "id", "pos_x", "pos_y", "state", "timestamp",
            "vel_x", "vel_y", "score", "health", "temp", "speed",
            "bg_color", "shadow_quality", "fx_level", "ui_theme",
            "debug_log", "misc_field"
        ]

        for epoch in range(epochs):
            random.shuffle(keys)
            total_loss = 0.0
            for k in keys:
                x = self._key_to_features(k)
                y = torch.tensor([label_for_key(k)], dtype=torch.long)
                logits = self.model(x)
                loss = loss_fn(logits, y)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                total_loss += float(loss.item())
            if epoch % 10 == 0:
                print(f"[ML] ImportanceClassifier epoch {epoch}, loss={total_loss:.4f}")


# =========================
# CriticalDataExtractor
# =========================

class CriticalDataExtractor:
    def __init__(self, rules: Optional[Dict[str, Any]] = None, classifier: Optional[DataImportanceClassifier] = None):
        self.rules = rules or {}
        self.classifier = classifier or DataImportanceClassifier()

    def classify_field(self, key: str, value: Any) -> str:
        if key in self.rules.get("critical", []):
            return "critical"
        if key in self.rules.get("important", []):
            return "important"
        if key in self.rules.get("cosmetic", []):
            return "cosmetic"
        if key in self.rules.get("ignore", []):
            return "ignore"
        return self.classifier.predict_importance(key, value)

    def extract(self, state: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        for k, v in state.items():
            level = self.classify_field(k, v)
            if level in ("critical", "important"):
                result[k] = v
        return result


# =========================
# DeltaEngine
# =========================

class DeltaEngine:
    def compute_delta(self, prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, Any]:
        delta = {}
        for k, v in curr.items():
            if k not in prev or prev[k] != v:
                delta[k] = v
        for k in prev.keys() - curr.keys():
            delta[k] = None
        return delta

    def apply_delta(self, prev: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
        new_state = dict(prev)
        for k, v in delta.items():
            if v is None:
                if k in new_state:
                    del new_state[k]
            else:
                new_state[k] = v
        return new_state


# =========================
# PredictiveReconstructor
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
        self.lstm = torch.nn.LSTM(input_dim, hidden_dim, batch_first=True)
        self.lstm_out = torch.nn.Linear(hidden_dim, 1)

    def set_mode(self, mode: str):
        self.mode = AlteredStatesMode(mode)

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
        x = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
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
            device = "cuda" if torch.cuda.is_available() else "cpu"
            t = torch.tensor(vectors, dtype=torch.float32, device=device)
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

    def default_value_for(self, key: str,
                          prob_fields: Dict[str, ProbabilisticField],
                          trend: float) -> Any:
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
# AsyncNetworkEngine (core, reused later)
# =========================

class AsyncNetworkEngine:
    def __init__(self, compressor: AdaptiveCompressor):
        self.compressor = compressor
        self.tcp_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        self.udp_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        self.ws_handler: Optional[Callable[[Dict[str, Any]], None]] = None

    async def start_tcp_server(self, host: str = "127.0.0.1", port: int = 9000):
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                while True:
                    header = await reader.readexactly(5)
                    length = int.from_bytes(header[:4], "big")
                    codec_id = header[4]
                    codec = ["zstd", "brotli", "lz4", "gzip", "zlib", "none"][codec_id]
                    data = await reader.readexactly(length)
                    msg = self.compressor.decode(data, codec)
                    if self.tcp_handler:
                        self.tcp_handler(msg)
            except asyncio.IncompleteReadError:
                pass
            except Exception as e:
                print(f"[TCP] Handler error: {e}")
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, host, port)
        print(f"[TCP] Async server on {host}:{port}")
        async with server:
            await server.serve_forever()

    async def tcp_send(self, host: str, port: int, data: Dict[str, Any], data_type: str = "generic"):
        reader, writer = await asyncio.open_connection(host, port)
        blob, codec = self.compressor.encode(data, data_type)
        codec_id = {"zstd": 0, "brotli": 1, "lz4": 2, "gzip": 3, "zlib": 4, "none": 5}[codec]
        header = len(blob).to_bytes(4, "big") + bytes([codec_id])
        writer.write(header + blob)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def start_udp_server(self, host: str = "127.0.0.1", port: int = 9001):
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: self.UDPProtocol(self.compressor, self.udp_handler),
            local_addr=(host, port),
        )
        print(f"[UDP] Async server on {host}:{port}")
        await asyncio.Future()

    class UDPProtocol(asyncio.DatagramProtocol):
        def __init__(self, compressor: AdaptiveCompressor, handler: Optional[Callable[[Dict[str, Any]], None]]):
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

    async def udp_send(self, host: str, port: int, data: Dict[str, Any], data_type: str = "generic"):
        blob, codec = self.compressor.encode(data, data_type)
        codec_id = {"zstd": 0, "brotli": 1, "lz4": 2, "gzip": 3, "zlib": 4, "none": 5}[codec]
        packet = bytes([codec_id]) + blob
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(),
            remote_addr=(host, port),
        )
        transport.sendto(packet)
        transport.close()

    async def start_ws_server(self, host: str = "127.0.0.1", port: int = 9002):
        if websockets is None:
            print("[WS] websockets not available.")
            return

        async def handler(ws, path):
            async for message in ws:
                if isinstance(message, bytes):
                    blob = message
                else:
                    blob = message.encode("latin1")
                msg = self.compressor.decode(blob, "zlib")
                if self.ws_handler:
                    self.ws_handler(msg)

        server = await websockets.serve(handler, host, port)
        print(f"[WS] Async server on {host}:{port}")
        await server.wait_closed()

    async def ws_send(self, uri: str, data: Dict[str, Any], data_type: str = "generic"):
        if websockets is None:
            return
        blob, _ = self.compressor.encode(data, data_type)
        async with websockets.connect(uri) as ws:
            await ws.send(blob)


# =========================
# Domain Rules
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
# World Modeling (Predictive Environment Model)
# =========================

class WorldModel:
    """
    Simple predictive environment model:
    - Tracks entities with pos/vel
    - Predicts next positions
    - Can be extended via plugins
    """

    def __init__(self):
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.last_update = time.time()

    def update_from_state(self, state: Dict[str, Any]):
        eid = str(state.get("id", "default"))
        pos_x = float(state.get("pos_x", 0.0))
        pos_y = float(state.get("pos_y", 0.0))
        vel_x = float(state.get("vel_x", 0.0))
        vel_y = float(state.get("vel_y", 0.0))
        self.entities[eid] = {
            "pos_x": pos_x,
            "pos_y": pos_y,
            "vel_x": vel_x,
            "vel_y": vel_y,
        }
        self.last_update = time.time()

    def predict_next(self, dt: float = 0.1) -> Dict[str, Dict[str, Any]]:
        preds = {}
        for eid, e in self.entities.items():
            px = e["pos_x"] + e["vel_x"] * dt
            py = e["pos_y"] + e["vel_y"] * dt
            preds[eid] = {
                "pos_x": px,
                "pos_y": py,
                "vel_x": e["vel_x"],
                "vel_y": e["vel_y"],
            }
        return preds


# =========================
# Anomaly Detection
# =========================

class AnomalyDetector:
    """
    Simple anomaly detector:
    - Detects out-of-range values
    - Detects sudden jumps
    - Flags suspicious nodes or states
    """

    def __init__(self):
        self.last_state: Dict[str, Any] = {}
        self.thresholds = {
            "pos_x": 1000.0,
            "pos_y": 1000.0,
            "vel_x": 100.0,
            "vel_y": 100.0,
            "health": 200.0,
            "score": 1e6,
            "temp": 200.0,
            "speed": 1000.0,
        }

    def check_state(self, state: Dict[str, Any]) -> bool:
        # returns True if anomaly detected
        for k, limit in self.thresholds.items():
            v = state.get(k)
            if v is None:
                continue
            try:
                fv = float(v)
            except Exception:
                continue
            if abs(fv) > limit:
                print(f"[ANOMALY] {k}={fv} exceeds limit {limit}")
                return True

        # sudden jump detection
        if self.last_state:
            for k in ("pos_x", "pos_y"):
                if k in state and k in self.last_state:
                    dv = abs(float(state[k]) - float(self.last_state[k]))
                    if dv > 100.0:
                        print(f"[ANOMALY] Sudden jump in {k}: Δ={dv}")
                        return True

        self.last_state = dict(state)
        return False


# =========================
# Multi-node Memory Fabric
# =========================

class MemoryStore:
    def __init__(self, path: str = "borg_memory.json"):
        self.path = path
        self.data = {
            "mode_history": [],
            "rewards": [],
            "compression_ratios": [],
            "roles": [],
            "shared": {},  # shared memory fabric
        }
        self._load()

    def _load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                pass

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(f"[MEMORY] Failed to save: {e}")

    def record_mode(self, mode: str):
        self.data.setdefault("mode_history", []).append({"mode": mode, "time": time.time()})

    def record_reward(self, reward: float):
        self.data.setdefault("rewards", []).append({"reward": reward, "time": time.time()})

    def record_compression(self, ratio: float):
        self.data.setdefault("compression_ratios", []).append({"ratio": ratio, "time": time.time()})

    def record_role(self, role: str):
        self.data.setdefault("roles", []).append({"role": role, "time": time.time()})

    def set_shared(self, key: str, value: Any):
        self.data.setdefault("shared", {})[key] = value

    def get_shared(self, key: str, default: Any = None) -> Any:
        return self.data.get("shared", {}).get(key, default)


class MemoryFabric:
    """
    Multi-node memory fabric:
    - Broadcasts memory fragments
    - Merges remote memory into local store
    """

    def __init__(self, memory: MemoryStore, node_id: str, port: int = 9300):
        self.memory = memory
        self.node_id = node_id
        self.port = port
        self.running = False
        self.peers: Dict[str, Dict[str, Any]] = {}

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._listener, daemon=True).start()
        threading.Thread(target=self._broadcaster, daemon=True).start()

    def _listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.port))
        while self.running:
            try:
                data, addr = sock.recvfrom(65535)
                msg = json.loads(data.decode("utf-8"))
                nid = msg.get("node_id", "unknown")
                self.peers[nid] = msg
                shared = msg.get("shared", {})
                for k, v in shared.items():
                    # simple merge: last writer wins
                    self.memory.set_shared(k, v)
            except Exception:
                continue

    def _broadcaster(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.running:
            try:
                payload = {
                    "node_id": self.node_id,
                    "time": time.time(),
                    "shared": self.memory.data.get("shared", {}),
                }
                sock.sendto(json.dumps(payload).encode("utf-8"), ("255.255.255.255", self.port))
            except Exception:
                pass
            time.sleep(3.0)


# =========================
# UniversalDataEngine (core, v10)
# =========================

class UniversalDataEngine:
    def __init__(self, domain: str = "game", plugin_manager: Optional[PluginManager] = None, node_id: str = "node-1"):
        self.node_id = node_id
        self.plugin_manager = plugin_manager or PluginManager()
        self.plugin_manager.load_plugins()
        self.plugin_manager.start_hot_swap_watcher()

        rules = get_builtin_domain_rules(domain)
        plugin_rules = self.plugin_manager.get_domain_rules(domain)
        for k, v in plugin_rules.items():
            rules.setdefault(k, []).extend(v)

        self.importance_classifier = DataImportanceClassifier()
        self.extractor = CriticalDataExtractor(rules=rules, classifier=self.importance_classifier)
        self.delta_engine = DeltaEngine()
        self.reconstructor = PredictiveReconstructor()
        self.compressor = AdaptiveCompressor()
        self.network = AsyncNetworkEngine(self.compressor)
        self.world_model = WorldModel()
        self.anomaly_detector = AnomalyDetector()

        self.last_full_state: Dict[str, Any] = {}
        self.total_raw_bytes = 0
        self.total_compressed_bytes = 0

        self.plugin_manager.register_models(self)
        self.plugin_manager.register_compressors(self)
        self.plugin_manager.register_world_models(self)

        self.mode_lock = threading.Lock()

    def set_mode(self, mode: str):
        with self.mode_lock:
            self.reconstructor.set_mode(mode)

    def process_state(self, new_state: Dict[str, Any], data_type: str = "generic") -> Dict[str, Any]:
        # anomaly detection
        if self.anomaly_detector.check_state(new_state):
            # mark state as suspicious but still process
            new_state["anomaly_flag"] = True

        essential = self.extractor.extract(new_state)
        delta = self.delta_engine.compute_delta(self.last_full_state, essential)
        updated_essential = self.delta_engine.apply_delta(self.last_full_state, delta)

        missing_keys = [k for k in new_state.keys() if k not in updated_essential]
        self.reconstructor.cache_state("last_full", self.last_full_state)
        full_reconstructed = self.reconstructor.reconstruct(updated_essential, missing_keys)

        self.last_full_state = dict(full_reconstructed)

        raw = json.dumps(new_state).encode("utf-8")
        comp, codec = self.compressor.encode(delta, data_type)
        self.total_raw_bytes += len(raw)
        self.total_compressed_bytes += len(comp)

        # update world model
        self.world_model.update_from_state(full_reconstructed)

        return full_reconstructed

    def get_compression_ratio(self) -> float:
        if self.total_raw_bytes == 0:
            return 1.0
        return self.total_compressed_bytes / self.total_raw_bytes

# =========================
# RLPolicy (bandit + deep head)
# =========================

class RLPolicy:
    """
    Local RL policy:
    - Discrete modes: normal, exploratory, hallucinatory
    - Deep value head for richer state → value mapping
    - Supports gradient aggregation from peers
    """

    def __init__(self, epsilon: float = 0.1):
        self.actions = ["normal", "exploratory", "hallucinatory"]
        self.epsilon = epsilon
        self.counts = {a: 0 for a in self.actions}
        self.values = {a: 0.0 for a in self.actions}
        self.last_action = "normal"
        self.use_ml = torch is not None
        self.value_net = None
        if self.use_ml:
            self._build_value_net()

    def _build_value_net(self):
        class ValueNet(torch.nn.Module):
            def __init__(self, in_dim=16, hidden=32, out_dim=3):
                super().__init__()
                self.net = torch.nn.Sequential(
                    torch.nn.Linear(in_dim, hidden),
                    torch.nn.ReLU(),
                    torch.nn.Linear(hidden, out_dim),
                )

            def forward(self, x):
                return self.net(x)

        self.value_net = ValueNet()

    def _state_to_vec(self, state: Dict[str, Any]) -> List[float]:
        nums = []
        for _, v in state.items():
            if isinstance(v, (int, float)):
                nums.append(float(v))
            if len(nums) >= 16:
                break
        while len(nums) < 16:
            nums.append(0.0)
        return nums

    def select_action(self, state: Dict[str, Any], bias: Optional[str] = None) -> str:
        if bias and random.random() < 0.6 and bias in self.actions:
            a = bias
        elif random.random() < self.epsilon:
            a = random.choice(self.actions)
        else:
            if self.use_ml and self.value_net is not None:
                with torch.no_grad():
                    x = torch.tensor(self._state_to_vec(state), dtype=torch.float32).unsqueeze(0)
                    q = self.value_net(x)[0].tolist()
                idx = int(max(range(len(self.actions)), key=lambda i: q[i]))
                a = self.actions[idx]
            else:
                a = max(self.actions, key=lambda x: self.values[x])
        self.last_action = a
        return a

    def update_bandit(self, reward: float):
        a = self.last_action
        self.counts[a] += 1
        n = self.counts[a]
        old = self.values[a]
        self.values[a] = old + (reward - old) / n

    def compute_gradients(self, state: Dict[str, Any], reward: float) -> Optional[Dict[str, Any]]:
        if not (self.use_ml and self.value_net is not None):
            return None
        x = torch.tensor(self._state_to_vec(state), dtype=torch.float32).unsqueeze(0)
        target = torch.zeros((1, len(self.actions)), dtype=torch.float32)
        idx = self.actions.index(self.last_action)
        target[0, idx] = reward
        pred = self.value_net(x)
        loss = torch.nn.functional.mse_loss(pred, target)
        self.value_net.zero_grad()
        loss.backward()
        grads = {}
        for name, p in self.value_net.named_parameters():
            if p.grad is not None:
                grads[name] = p.grad.detach().cpu().tolist()
        return grads

    def apply_gradients(self, grads: Dict[str, Any], lr: float = 1e-3):
        if not (self.use_ml and self.value_net is not None):
            return
        with torch.no_grad():
            for name, p in self.value_net.named_parameters():
                if name in grads:
                    g = torch.tensor(grads[name], dtype=torch.float32, device=p.device)
                    p -= lr * g


# =========================
# DistributedRLCoordinator (gradient sharing)
# =========================

class DistributedRLCoordinator:
    """
    True distributed deep RL:
    - Shares gradients and bandit stats across nodes via UDP broadcast
    """

    def __init__(self, engine: UniversalDataEngine, policy: RLPolicy, port: int = 9200):
        self.engine = engine
        self.policy = policy
        self.port = port
        self.running = False
        self.peer_stats: Dict[str, Dict[str, Any]] = {}

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._listener, daemon=True).start()
        threading.Thread(target=self._broadcaster, daemon=True).start()

    def _listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.port))
        while self.running:
            try:
                data, addr = sock.recvfrom(65535)
                msg = json.loads(data.decode("utf-8"))
                node_id = msg.get("node_id", "unknown")
                self.peer_stats[node_id] = msg
                grads = msg.get("grads")
                if grads:
                    self.policy.apply_gradients(grads)
            except Exception:
                continue

    def _broadcaster(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.running:
            try:
                payload = {
                    "node_id": self.engine.node_id,
                    "time": time.time(),
                    "values": self.policy.values,
                    "counts": self.policy.counts,
                    "grads": None,  # filled by controller when needed
                }
                sock.sendto(json.dumps(payload).encode("utf-8"), ("255.255.255.255", self.port))
            except Exception:
                pass
            time.sleep(3.0)

    def broadcast_gradients(self, grads: Dict[str, Any]):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            payload = {
                "node_id": self.engine.node_id,
                "time": time.time(),
                "values": self.policy.values,
                "counts": self.policy.counts,
                "grads": grads,
            }
            sock.sendto(json.dumps(payload).encode("utf-8"), ("255.255.255.255", self.port))
        except Exception:
            pass

    def get_global_bias(self) -> Optional[str]:
        peers = list(self.peer_stats.values())
        if not peers:
            return None
        agg_values = {a: 0.0 for a in self.policy.actions}
        for p in peers:
            vals = p.get("values", {})
            for a in self.policy.actions:
                agg_values[a] += float(vals.get(a, 0.0))
        best = max(self.policy.actions, key=lambda a: agg_values[a])
        return best


# =========================
# SwarmManager (compression gossip)
# =========================

class SwarmManager:
    def __init__(self, engine: UniversalDataEngine, port: int = 9100):
        self.engine = engine
        self.port = port
        self.running = False
        self.peers: Dict[str, Dict[str, Any]] = {}

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._udp_listener, daemon=True).start()
        threading.Thread(target=self._udp_broadcaster, daemon=True).start()

    def _udp_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.port))
        while self.running:
            try:
                data, addr = sock.recvfrom(65535)
                msg = json.loads(data.decode("utf-8"))
                node_id = msg.get("node_id", "unknown")
                self.peers[node_id] = msg
            except Exception:
                continue

    def _udp_broadcaster(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.running:
            try:
                ratio = self.engine.get_compression_ratio()
                payload = {
                    "node_id": self.engine.node_id,
                    "time": time.time(),
                    "compression_ratio": ratio,
                }
                sock.sendto(json.dumps(payload).encode("utf-8"), ("255.255.255.255", self.port))
            except Exception:
                pass
            time.sleep(2.0)


# =========================
# Fault-tolerant SwarmConsensus (gossip-style)
# =========================

class SwarmConsensus:
    """
    Gossip-based consensus:
    - Uses compression ratios and simple voting
    - Fault-tolerant to a few bad nodes
    """

    def __init__(self, swarm: SwarmManager):
        self.swarm = swarm

    def get_global_mode(self) -> Optional[str]:
        peers = list(self.swarm.peers.values())
        if not peers:
            return None
        ratios = [p.get("compression_ratio", 1.0) for p in peers]
        ratios = [r for r in ratios if 0.0 <= r <= 2.0]  # basic sanity filter
        if not ratios:
            return None
        avg = sum(ratios) / len(ratios)
        if avg > 0.7:
            return "hallucinatory"
        if avg > 0.4:
            return "exploratory"
        return "normal"


# =========================
# SwarmTaskPlanner (multi-agent planning)
# =========================

class SwarmTaskPlanner:
    """
    Multi-agent planning:
    - Nodes negotiate roles and tasks via shared memory fabric
    - Roles: scout, stabilizer, aggressor, support
    """

    def __init__(self, engine: UniversalDataEngine, swarm: SwarmManager, memory_fabric: MemoryFabric):
        self.engine = engine
        self.swarm = swarm
        self.memory_fabric = memory_fabric

    def get_role(self) -> str:
        # read shared role map from memory fabric
        shared_roles = self.memory_fabric.memory.get_shared("roles_map", {})
        if self.engine.node_id in shared_roles:
            return shared_roles[self.engine.node_id]

        # if not assigned, compute deterministic role and write back
        peers = sorted(self.swarm.peers.keys())
        all_nodes = sorted(set(peers + [self.engine.node_id]))
        idx = all_nodes.index(self.engine.node_id)
        if idx == 0:
            role = "scout"
        elif idx == len(all_nodes) - 1:
            role = "aggressor"
        elif idx % 2 == 0:
            role = "stabilizer"
        else:
            role = "support"

        shared_roles[self.engine.node_id] = role
        self.memory_fabric.memory.set_shared("roles_map", shared_roles)
        return role

    def get_swarm_goal(self) -> str:
        goal = self.memory_fabric.memory.get_shared("swarm_goal")
        if goal:
            return goal
        # default goal
        goal = "advance_and_hold"
        self.memory_fabric.memory.set_shared("swarm_goal", goal)
        return goal


# =========================
# Behavior Trees / GOAP
# =========================

class BTNode:
    def tick(self, ctx: Dict[str, Any]) -> bool:
        raise NotImplementedError


class BTSequence(BTNode):
    def __init__(self, children: List[BTNode]):
        self.children = children

    def tick(self, ctx: Dict[str, Any]) -> bool:
        for c in self.children:
            if not c.tick(ctx):
                return False
        return True


class BTSelector(BTNode):
    def __init__(self, children: List[BTNode]):
        self.children = children

    def tick(self, ctx: Dict[str, Any]) -> bool:
        for c in self.children:
            if c.tick(ctx):
                return True
        return False


class BTCondition(BTNode):
    def __init__(self, fn: Callable[[Dict[str, Any]], bool]):
        self.fn = fn

    def tick(self, ctx: Dict[str, Any]) -> bool:
        return self.fn(ctx)


class BTAction(BTNode):
    def __init__(self, fn: Callable[[Dict[str, Any]], bool]):
        self.fn = fn

    def tick(self, ctx: Dict[str, Any]) -> bool:
        return self.fn(ctx)


class BehaviorTreeController:
    """
    Simple behavior tree / GOAP hybrid:
    - Uses conditions on world model, role, and goal
    - Produces high-level intents (advance, hold, flank, retreat)
    """

    def __init__(self, engine: UniversalDataEngine, planner: SwarmTaskPlanner):
        self.engine = engine
        self.planner = planner
        self.tree = self._build_tree()
        self.last_intent = "hold"

    def _build_tree(self) -> BTNode:
        def is_aggressor(ctx):
            return ctx.get("role") == "aggressor"

        def is_scout(ctx):
            return ctx.get("role") == "scout"

        def low_health(ctx):
            state = ctx.get("state", {})
            return float(state.get("health", 100.0)) < 30.0

        def act_retreat(ctx):
            ctx["intent"] = "retreat"
            return True

        def act_flank(ctx):
            ctx["intent"] = "flank"
            return True

        def act_advance(ctx):
            ctx["intent"] = "advance"
            return True

        def act_hold(ctx):
            ctx["intent"] = "hold"
            return True

        root = BTSelector([
            BTSequence([BTCondition(low_health), BTAction(act_retreat)]),
            BTSequence([BTCondition(is_aggressor), BTAction(act_advance)]),
            BTSequence([BTCondition(is_scout), BTAction(act_flank)]),
            BTAction(act_hold),
        ])
        return root

    def tick(self, state: Dict[str, Any], role: str) -> str:
        ctx = {
            "state": state,
            "role": role,
            "goal": self.planner.get_swarm_goal(),
            "intent": self.last_intent,
        }
        self.tree.tick(ctx)
        self.last_intent = ctx["intent"]
        return self.last_intent

# =========================
# Swarm-level Autopilot
# =========================

class AutopilotInterface:
    def __init__(self):
        self.last_command: Dict[str, Any] = {}

    def compute_control(self, state: Dict[str, Any], role: str, mode: str, intent: str,
                        formation_offset: float = 0.0) -> Dict[str, Any]:
        pos_x = float(state.get("pos_x", 0.0))
        vel_x = float(state.get("vel_x", 0.0))
        base_target_speed = 5.0

        # role-based speed
        if role == "scout":
            target_speed = base_target_speed * 1.3
        elif role == "aggressor":
            target_speed = base_target_speed * 1.5
        elif role == "support":
            target_speed = base_target_speed * 0.9
        else:
            target_speed = base_target_speed

        # mode-based multiplier
        if mode == "hallucinatory":
            target_speed *= 1.2
        elif mode == "exploratory":
            target_speed *= 1.1

        # intent-based shaping
        if intent == "retreat":
            target_speed *= -0.5
        elif intent == "hold":
            target_speed *= 0.2
        elif intent == "flank":
            target_speed *= 1.1
        elif intent == "advance":
            target_speed *= 1.0

        # simple 1D control + formation offset
        desired_pos = formation_offset
        pos_error = desired_pos - pos_x
        steering = max(-1.0, min(1.0, pos_error * 0.1))

        throttle = (target_speed - vel_x) / max(abs(target_speed), 0.1)
        throttle = max(-1.0, min(1.0, throttle))

        cmd = {
            "throttle": throttle,
            "steering": steering,
            "role": role,
            "mode": mode,
            "intent": intent,
            "timestamp": time.time(),
        }
        self.last_command = cmd
        return cmd


# =========================
# BorgController v10
# =========================

class BorgController:
    def __init__(self, engine: UniversalDataEngine, memory: MemoryStore,
                 swarm: SwarmManager, consensus: SwarmConsensus,
                 autopilot: AutopilotInterface, rl_coord: DistributedRLCoordinator,
                 planner: SwarmTaskPlanner, behavior_tree: BehaviorTreeController,
                 memory_fabric: MemoryFabric):
        self.engine = engine
        self.memory = memory
        self.swarm = swarm
        self.consensus = consensus
        self.autopilot = autopilot
        self.rl_coord = rl_coord
        self.planner = planner
        self.behavior_tree = behavior_tree
        self.memory_fabric = memory_fabric
        self.policy = RLPolicy(epsilon=0.15)
        self.running = False
        self.last_ratio = 1.0
        self.current_role = "stabilizer"
        self.current_mode = "normal"

    def start(self, interval: float = 1.0):
        if self.running:
            return
        self.running = True

        def loop():
            while self.running:
                ratio = self.engine.get_compression_ratio()
                self.memory.record_compression(ratio)

                stability_penalty = abs(ratio - self.last_ratio)
                reward = max(0.0, 1.0 - ratio - 0.5 * stability_penalty)
                self.last_ratio = ratio
                self.memory.record_reward(reward)

                # bandit update
                self.policy.update_bandit(reward)

                # compute gradients and share
                state_snapshot = dict(self.engine.last_full_state)
                grads = self.policy.compute_gradients(state_snapshot, reward)
                if grads:
                    self.rl_coord.broadcast_gradients(grads)

                # global bias from peers + consensus mode
                global_mode = self.consensus.get_global_mode()
                global_bias = self.rl_coord.get_global_bias()
                bias = global_bias or global_mode

                # multi-agent planning: role + goal
                self.current_role = self.planner.get_role()
                self.memory.record_role(self.current_role)

                # select mode using RL policy
                mode = self.policy.select_action(state_snapshot, bias=bias)
                self.current_mode = mode
                self.engine.set_mode(mode)
                self.memory.record_mode(mode)

                # behavior tree → intent
                intent = self.behavior_tree.tick(state_snapshot, self.current_role)

                # simple formation: offset based on node_id hash
                h = hash(self.engine.node_id) % 21 - 10
                formation_offset = float(h)

                control = self.autopilot.compute_control(
                    state_snapshot, self.current_role, mode, intent, formation_offset
                )

                # share high-level intent + control into memory fabric
                self.memory.set_shared(f"intent_{self.engine.node_id}", intent)
                self.memory.set_shared(f"control_{self.engine.node_id}", control)

                self.memory.save()
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self.running = False


# =========================
# LiveDataSimulator
# =========================

class LiveDataSimulator:
    def __init__(self, engine: UniversalDataEngine):
        self.engine = engine
        self.running = False

    def start_game_stream(self, interval: float = 0.2):
        self.running = True

        def loop():
            t = 0.0
            while self.running:
                state = {
                    "id": 1,
                    "pos_x": t,
                    "pos_y": t * 0.5,
                    "vel_x": 1.0,
                    "vel_y": 0.5,
                    "health": max(0, 100 - int(t)),
                    "score": int(t * 10),
                    "bg_color": "blue" if int(t) % 2 == 0 else "red",
                    "shadow_quality": "high",
                    "fx_level": "max",
                }
                self.engine.process_state(state, data_type="game")
                time.sleep(interval)
                t += interval

        threading.Thread(target=loop, daemon=True).start()

    def start_telemetry_stream(self, interval: float = 0.5):
        self.running = True

        def loop():
            t = 0
            while self.running:
                state = {
                    "device_id": "sensor-001",
                    "timestamp": time.time(),
                    "status": "ok",
                    "temp": 20 + random.random() * 5,
                    "pressure": 1.0 + random.random() * 0.1,
                    "speed": random.random() * 100,
                    "ui_theme": "dark",
                    "debug_log": "noise",
                }
                self.engine.process_state(state, data_type="telemetry")
                time.sleep(interval)
                t += 1

        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self.running = False


# =========================
# GUI (Tkinter-focused)
# =========================

class EngineGUI:
    def __init__(self, engine: UniversalDataEngine, memory: MemoryStore,
                 controller: BorgController, autopilot: AutopilotInterface):
        self.engine = engine
        self.memory = memory
        self.controller = controller
        self.autopilot = autopilot
        self.mode = None
        if tkinter is not None:
            self.mode = "tk"
            self._init_tk()
        else:
            self.mode = None
            print("[GUI] No GUI toolkit available.")

    def _init_tk(self):
        self.root = tkinter.Tk()
        self.root.title("Universal Data Engine v10 (Distributed Deep RL Swarm Borg)")

        self.text = tkinter.Text(self.root, height=20, width=80)
        self.text.pack()

        self.stats_label = tkinter.Label(self.root, text="Stats:")
        self.stats_label.pack()

        self.ctrl_label = tkinter.Label(self.root, text="Control:")
        self.ctrl_label.pack()

        self.intent_label = tkinter.Label(self.root, text="Intent:")
        self.intent_label.pack()

        self.root.after(500, self._update_tk)

    def _update_tk(self):
        self.text.delete("1.0", tkinter.END)
        state_str = json.dumps(self.engine.last_full_state, indent=2)
        self.text.insert(tkinter.END, state_str)
        ratio = self.engine.get_compression_ratio()
        saved = 1.0 - ratio
        mode = self.controller.current_mode
        role = self.controller.current_role
        ctrl = self.autopilot.last_command or {}
        self.stats_label.config(
            text=f"Compression ratio: {ratio:.3f} | Bandwidth saved: {saved*100:.1f}% | Mode: {mode} | Role: {role}"
        )
        self.ctrl_label.config(
            text=f"Control: throttle={ctrl.get('throttle', 0):.2f}, steering={ctrl.get('steering', 0):.2f}"
        )
        intent = ctrl.get("intent", "unknown")
        self.intent_label.config(text=f"Intent: {intent}")
        self.root.after(500, self._update_tk)

    def run(self):
        if self.mode == "tk":
            self.root.mainloop()
        else:
            print("[GUI] No GUI mode; nothing to run.")


# =========================
# Main
# =========================

def main():
    node_id = f"node-{random.randint(1000,9999)}"
    engine = UniversalDataEngine(domain="game", node_id=node_id)

    # train importance classifier a bit
    engine.importance_classifier.train_on_synthetic(epochs=10, lr=1e-2)

    simulator = LiveDataSimulator(engine)
    simulator.start_game_stream(interval=0.2)
    simulator.start_telemetry_stream(interval=0.5)

    memory = MemoryStore(path=f"borg_memory_{node_id}.json")
    memory_fabric = MemoryFabric(memory, node_id=node_id, port=9300)
    memory_fabric.start()

    swarm = SwarmManager(engine, port=9100)
    swarm.start()
    consensus = SwarmConsensus(swarm)
    autopilot = AutopilotInterface()

    policy = RLPolicy(epsilon=0.15)
    rl_coord = DistributedRLCoordinator(engine, policy, port=9200)
    rl_coord.start()

    planner = SwarmTaskPlanner(engine, swarm, memory_fabric)
    behavior_tree = BehaviorTreeController(engine, planner)

    borg = BorgController(engine, memory, swarm, consensus, autopilot,
                          rl_coord, planner, behavior_tree, memory_fabric)
    borg.start(interval=1.0)

    async def net_demo():
        engine.network.tcp_handler = lambda msg: print("[TCP] Received:", msg)
        engine.network.udp_handler = lambda msg: print("[UDP] Received:", msg)
        await asyncio.gather(
            engine.network.start_tcp_server("127.0.0.1", 9000),
        )

    def run_asyncio():
        try:
            asyncio.run(net_demo())
        except Exception as e:
            print("[NET] Async loop ended:", e)

    threading.Thread(target=run_asyncio, daemon=True).start()

    gui = EngineGUI(engine, memory, borg, autopilot)
    gui.run()


if __name__ == "__main__":
    main()
