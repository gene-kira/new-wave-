#!/usr/bin/env python3
"""
Universal Data Engine v7
Swarm-capable, memory-enabled, RL-driven autonomous AI (Borg Hive Node)

Upgrades over v6:
- SwarmManager: simple swarm sync (broadcast + receive) over TCP/UDP
- MemoryStore: persistent JSON memory (stats, mode history, rewards)
- RLPolicy: bandit-style mode selector (normal / exploratory / hallucinatory)
- BorgController v2: uses RLPolicy + MemoryStore + SwarmManager

Core stack retained:
- Autonomous library autoloader
- Plugin system
- Hybrid DataImportanceClassifier (MLP + heuristics)
- Hybrid PredictiveReconstructor (LSTM + Transformer + Probabilistic + AlteredStates + GPU blending)
- DeltaEngine
- AdaptiveCompressor (zstd, brotli, lz4, gzip, zlib)
- Async networking (TCP/UDP/WebSockets)
- Live simulators (game + telemetry)
- GUI (PyQt5/Tkinter) for visualization only
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
    "PyQt5",
    "tkinter",
])

np = LIBS["numpy"]
torch = LIBS["torch"]
websockets = LIBS["websockets"]
zstandard = LIBS["zstandard"]
brotli = LIBS["brotli"]
lz4 = LIBS["lz4"]
lz4f = LIBS["lz4.frame"]
PyQt5 = LIBS["PyQt5"]
tkinter = LIBS["tkinter"]


# =========================
# Plugin System
# =========================

class PluginManager:
    def __init__(self, plugin_dir: str = "plugins"):
        self.plugin_dir = plugin_dir
        self.plugins = []

    def load_plugins(self):
        if not os.path.isdir(self.plugin_dir):
            return
        sys.path.insert(0, self.plugin_dir)
        for fname in os.listdir(self.plugin_dir):
            if not fname.endswith(".py"):
                continue
            mod_name = fname[:-3]
            try:
                mod = importlib.import_module(mod_name)
                self.plugins.append(mod)
                print(f"[PLUGIN] Loaded {mod_name}")
            except Exception as e:
                print(f"[PLUGIN] Failed to load {mod_name}: {e}", file=sys.stderr)

    def get_domain_rules(self, domain: str) -> Dict[str, Any]:
        for p in self.plugins:
            if hasattr(p, "get_domain_rules"):
                rules = p.get_domain_rules(domain)
                if rules:
                    return rules
        return {}

    def register_models(self, engine: "UniversalDataEngine"):
        for p in self.plugins:
            if hasattr(p, "register_models"):
                p.register_models(engine)

    def register_compressors(self, engine: "UniversalDataEngine"):
        for p in self.plugins:
            if hasattr(p, "register_compressors"):
                p.register_compressors(engine)


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
        if len(self.cache["history"]) > 32:
            self.cache["history"].pop(0)
        self.cache[key] = state
        self.history.append(state)
        if len(self.history) > 64:
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
# AsyncNetworkEngine
# =========================

class AsyncNetworkEngine:
    def __init__(self, compressor: AdaptiveCompressor):
        self.compressor = compressor
        self.tcp_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        self.udp_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        self.ws_handler: Optional[Callable[[Dict[str, Any]], None]] = None

    async def start_tcp_server(self, host: str = "127.0.0.1", port: int = 9000):
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            while True:
                header = await reader.readexactly(5)
                length = int.from_bytes(header[:4], "big")
                codec_id = header[4]
                codec = ["zstd", "brotli", "lz4", "gzip", "zlib", "none"][codec_id]
                data = await reader.readexactly(length)
                msg = self.compressor.decode(data, codec)
                if self.tcp_handler:
                    self.tcp_handler(msg)
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
            await ws.send(blob.decode("latin1"))


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
# UniversalDataEngine
# =========================

class UniversalDataEngine:
    def __init__(self, domain: str = "game", plugin_manager: Optional[PluginManager] = None, node_id: str = "node-1"):
        self.node_id = node_id
        self.plugin_manager = plugin_manager or PluginManager()
        self.plugin_manager.load_plugins()

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

        self.last_full_state: Dict[str, Any] = {}
        self.total_raw_bytes = 0
        self.total_compressed_bytes = 0

        self.plugin_manager.register_models(self)
        self.plugin_manager.register_compressors(self)

    def set_mode(self, mode: str):
        self.reconstructor.set_mode(mode)

    def process_state(self, new_state: Dict[str, Any], data_type: str = "generic") -> Dict[str, Any]:
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

        return full_reconstructed

    def get_compression_ratio(self) -> float:
        if self.total_raw_bytes == 0:
            return 1.0
        return self.total_compressed_bytes / self.total_raw_bytes


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
# MemoryStore (persistent)
# =========================

class MemoryStore:
    """
    Simple JSON-based memory:
    - stores mode history
    - stores rewards
    - stores compression stats
    """

    def __init__(self, path: str = "borg_memory.json"):
        self.path = path
        self.data = {
            "mode_history": [],
            "rewards": [],
            "compression_ratios": [],
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


# =========================
# RLPolicy (bandit-style)
# =========================

class RLPolicy:
    """
    Very simple bandit over modes:
    - actions: normal, exploratory, hallucinatory
    - keeps running average reward per action
    - epsilon-greedy exploration
    """

    def __init__(self, epsilon: float = 0.1):
        self.actions = ["normal", "exploratory", "hallucinatory"]
        self.epsilon = epsilon
        self.counts = {a: 0 for a in self.actions}
        self.values = {a: 0.0 for a in self.actions}
        self.last_action = "normal"

    def select_action(self) -> str:
        if random.random() < self.epsilon:
            a = random.choice(self.actions)
        else:
            a = max(self.actions, key=lambda x: self.values[x])
        self.last_action = a
        return a

    def update(self, reward: float):
        a = self.last_action
        self.counts[a] += 1
        n = self.counts[a]
        old = self.values[a]
        self.values[a] = old + (reward - old) / n


# =========================
# SwarmManager
# =========================

class SwarmManager:
    """
    Very simple swarm sync:
    - periodically broadcasts local stats
    - listens for other nodes' stats
    """

    def __init__(self, engine: UniversalDataEngine, port: int = 9100):
        self.engine = engine
        self.port = port
        self.running = False
        self.peers: Dict[str, Dict[str, Any]] = {}

    def start(self):
        self.running = True
        threading.Thread(target=self._udp_listener, daemon=True).start()
        threading.Thread(target=self._udp_broadcaster, daemon=True).start()

    def _udp_listener(self):
        import socket
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
        import socket
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
# BorgController v2 (RL + Memory + Swarm)
# =========================

class BorgController:
    def __init__(self, engine: UniversalDataEngine, memory: MemoryStore, swarm: SwarmManager):
        self.engine = engine
        self.memory = memory
        self.swarm = swarm
        self.policy = RLPolicy(epsilon=0.15)
        self.running = False
        self.last_ratio = 1.0

    def start(self, interval: float = 1.0):
        self.running = True

        def loop():
            while self.running:
                ratio = self.engine.get_compression_ratio()
                self.memory.record_compression(ratio)

                # Reward: lower ratio (better compression) is good, but penalize instability
                stability_penalty = abs(ratio - self.last_ratio)
                reward = max(0.0, 1.0 - ratio - 0.5 * stability_penalty)
                self.last_ratio = ratio
                self.memory.record_reward(reward)
                self.policy.update(reward)

                mode = self.policy.select_action()
                self.engine.set_mode(mode)
                self.memory.record_mode(mode)

                # Swarm info is available in self.swarm.peers if you want to use it later

                self.memory.save()
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self.running = False


# =========================
# GUI
# =========================

class EngineGUI:
    def __init__(self, engine: UniversalDataEngine, memory: MemoryStore, controller: BorgController):
        self.engine = engine
        self.memory = memory
        self.controller = controller
        self.mode = None
        if PyQt5 is not None:
            self.mode = "pyqt"
            self._init_pyqt()
        elif tkinter is not None:
            self.mode = "tk"
            self._init_tk()
        else:
            self.mode = None
            print("[GUI] No GUI toolkit available.")

    # PyQt5
    def _init_pyqt(self):
        from PyQt5 import QtWidgets, QtCore

        self.app = QtWidgets.QApplication(sys.argv)
        self.window = QtWidgets.QWidget()
        self.window.setWindowTitle("Universal Data Engine v7 (Swarm RL Borg)")

        layout = QtWidgets.QVBoxLayout()

        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)

        self.stats_label = QtWidgets.QLabel("Stats:")
        layout.addWidget(self.stats_label)

        self.window.setLayout(layout)

        timer = QtCore.QTimer()
        timer.timeout.connect(self._update_pyqt)
        timer.start(500)
        self.timer = timer

    def _update_pyqt(self):
        state_str = json.dumps(self.engine.last_full_state, indent=2)
        self.text.setPlainText(state_str)
        ratio = self.engine.get_compression_ratio()
        saved = 1.0 - ratio
        self.stats_label.setText(
            f"Compression ratio: {ratio:.3f} | Bandwidth saved: {saved*100:.1f}% | Last mode: {self.controller.policy.last_action}"
        )

    def run_pyqt(self):
        self.window.show()
        self.app.exec_()

    # Tkinter
    def _init_tk(self):
        self.root = tkinter.Tk()
        self.root.title("Universal Data Engine v7 (Swarm RL Borg)")

        self.text = tkinter.Text(self.root, height=20, width=80)
        self.text.pack()

        self.stats_label = tkinter.Label(self.root, text="Stats:")
        self.stats_label.pack()

        self.root.after(500, self._update_tk)

    def _update_tk(self):
        self.text.delete("1.0", tkinter.END)
        state_str = json.dumps(self.engine.last_full_state, indent=2)
        self.text.insert(tkinter.END, state_str)
        ratio = self.engine.get_compression_ratio()
        saved = 1.0 - ratio
        self.stats_label.config(
            text=f"Compression ratio: {ratio:.3f} | Bandwidth saved: {saved*100:.1f}% | Last mode: {self.controller.policy.last_action}"
        )
        self.root.after(500, self._update_tk)

    def run_tk(self):
        self.root.mainloop()

    def run(self):
        if self.mode == "pyqt":
            self.run_pyqt()
        elif self.mode == "tk":
            self.run_tk()
        else:
            print("[GUI] No GUI mode; nothing to run.")


# =========================
# Main
# =========================

def main():
    node_id = f"node-{random.randint(1000,9999)}"
    engine = UniversalDataEngine(domain="game", node_id=node_id)

    engine.importance_classifier.train_on_synthetic(epochs=10, lr=1e-2)

    simulator = LiveDataSimulator(engine)
    simulator.start_game_stream(interval=0.2)
    simulator.start_telemetry_stream(interval=0.5)

    memory = MemoryStore(path=f"borg_memory_{node_id}.json")
    swarm = SwarmManager(engine, port=9100)
    swarm.start()

    borg = BorgController(engine, memory, swarm)
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

    gui = EngineGUI(engine, memory, borg)
    gui.run()


if __name__ == "__main__":
    main()
