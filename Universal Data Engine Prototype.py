#!/usr/bin/env python3
"""
Universal Data Engine v3 (Unified File)

Features:
- ML-based DataImportanceClassifier with simple training
- PredictiveReconstructor with temporal memory (LSTM) + GPU kernels
- DeltaEngine (universal delta core)
- Adaptive compression (zstd, brotli, lz4, gzip, zlib)
- Async networking (TCP/UDP/WebSockets via asyncio)
- Domain-specific rules + plugin system
- Live data simulator (game + telemetry)
- Modern GUI (PyQt5 if available, fallback to Tkinter)

This is a conceptual but runnable skeleton. Many parts are extensible.
"""

import importlib
import sys
import os
import json
import zlib
import gzip
import time
import random
import asyncio
import socket
import threading
from typing import Any, Dict, List, Optional, Callable, Tuple

# =========================
# Autoloader for libraries
# =========================

def autoload_libs(required: List[str]) -> Dict[str, Any]:
    loaded = {}
    for name in required:
        try:
            loaded[name] = importlib.import_module(name)
        except ImportError:
            print(f"[WARN] Library '{name}' not found. Some features may be limited.", file=sys.stderr)
            loaded[name] = None
    return loaded


LIBS = autoload_libs([
    "numpy",
    "torch",
    "websockets",
    "zstandard",
    "brotli",
    "lz4.frame",
    "PyQt5",
    "tkinter",
])

np = LIBS["numpy"]
torch = LIBS["torch"]
websockets = LIBS["websockets"]
zstandard = LIBS["zstandard"]
brotli = LIBS["brotli"]
lz4f = LIBS["lz4.frame"]
PyQt5 = LIBS["PyQt5"]
tkinter = LIBS["tkinter"]


# =========================
# Plugin System
# =========================

class PluginManager:
    """
    Simple plugin system.
    Plugins are Python files in ./plugins with:
    - get_domain_rules() -> dict
    - register_models(engine) -> None (optional)
    - register_compressors(engine) -> None (optional)
    """

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
# ML-based Data Importance Classifier
# =========================

class DataImportanceClassifier:
    """
    ML-based classifier that predicts importance level of fields:
    - 0: ignore
    - 1: cosmetic
    - 2: important
    - 3: critical

    If torch is unavailable, falls back to simple rule-based logic.
    """

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
        elif np:
            return np.array(vec, dtype=np.float32)[None, :]
        else:
            return vec

    def predict_importance(self, key: str, value: Any) -> str:
        if not self.use_ml or self.model is None or torch is None:
            k = key.lower()
            if "id" in k or "pos" in k or "state" in k or "timestamp" in k:
                return "critical"
            if "vel" in k or "score" in k or "health" in k or "temp" in k or "speed" in k:
                return "important"
            if "bg" in k or "color" in k or "shadow" in k or "fx" in k or "ui" in k:
                return "cosmetic"
            return "ignore"

        with torch.no_grad():
            x = self._key_to_features(key)
            logits = self.model(x)
            pred = int(torch.argmax(logits, dim=1).item())

        mapping = {
            3: "critical",
            2: "important",
            1: "cosmetic",
            0: "ignore",
        }
        return mapping.get(pred, "ignore")

    def train_on_synthetic(self, epochs: int = 50, lr: float = 1e-2):
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
# Critical Data Extractor
# =========================

class CriticalDataExtractor:
    """
    Extracts minimal, transaction-critical subset of data.
    Uses:
    - explicit rules (per domain)
    - ML-based classifier as fallback
    """

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
# Delta Engine
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
# Predictive Reconstructor with Temporal Memory + GPU Kernels
# =========================

class PredictiveReconstructor:
    """
    Reconstructs missing or non-critical data using:
    - cached frames
    - heuristics
    - temporal memory (LSTM) if torch available
    - GPU vector blending
    """

    def __init__(self):
        self.cache: Dict[str, Any] = {}
        self.use_ml = torch is not None
        self.lstm = None
        self.lstm_hidden = None
        if self.use_ml:
            self._build_lstm()

    def _build_lstm(self):
        input_dim = 8
        hidden_dim = 16
        self.lstm = torch.nn.LSTM(input_dim, hidden_dim, batch_first=True)
        self.lstm_out = torch.nn.Linear(hidden_dim, 1)

    def cache_state(self, key: str, state: Dict[str, Any]) -> None:
        self.cache.setdefault("history", [])
        self.cache["history"].append(state)
        if len(self.cache["history"]) > 32:
            self.cache["history"].pop(0)
        self.cache[key] = state

    def _context_vector(self, state: Dict[str, Any]) -> List[float]:
        nums = []
        for k, v in state.items():
            if isinstance(v, (int, float)):
                nums.append(float(v))
            if len(nums) >= 8:
                break
        while len(nums) < 8:
            nums.append(0.0)
        return nums

    def _temporal_predict_scalar(self) -> float:
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

    def gpu_blend_vectors(self, vectors: List[List[float]]) -> List[float]:
        if not vectors:
            return []
        if torch:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            t = torch.tensor(vectors, dtype=torch.float32, device=device)
            mean = t.mean(dim=0)
            return mean.cpu().tolist()
        elif np:
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
        for k in missing_keys:
            if k in last_full:
                reconstructed[k] = last_full[k]
            else:
                reconstructed[k] = self.default_value_for(k, reconstructed)
        return reconstructed

    def default_value_for(self, key: str, context: Dict[str, Any]) -> Any:
        k = key.lower()
        if k.startswith("pos_") or k.startswith("x") or k.startswith("y"):
            return self._temporal_predict_scalar()
        if k.startswith("vel_"):
            return 0.0
        if "bg" in k or "color" in k:
            return "synthetic_bg"
        if "shadow" in k:
            return "medium"
        return None


# =========================
# Adaptive Compression
# =========================

class AdaptiveCompressor:
    """
    Adaptive compression:
    - zstd
    - brotli
    - lz4
    - gzip
    - zlib (fallback)
    """

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

        # Simple heuristic: choose codec by type
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
# Async Networking (TCP/UDP/WebSockets)
# =========================

class AsyncNetworkEngine:
    """
    Async networking using asyncio:
    - TCP server/client
    - UDP server/client
    - WebSockets (if websockets available)
    """

    def __init__(self, compressor: AdaptiveCompressor):
        self.compressor = compressor
        self.loop = None
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
                # assume zlib for WS demo
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
# Domain-specific rules
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
# Universal Data Engine
# =========================

class UniversalDataEngine:
    """
    Orchestrates:
    - CriticalDataExtractor (with ML classifier)
    - DeltaEngine
    - PredictiveReconstructor (with temporal memory + GPU kernels)
    - AdaptiveCompressor
    - AsyncNetworkEngine
    - Domain-specific rules + plugins
    """

    def __init__(self, domain: str = "game", plugin_manager: Optional[PluginManager] = None):
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
# Live Data Simulator
# =========================

class LiveDataSimulator:
    """
    Generates live data streams for:
    - game
    - telemetry
    """

    def __init__(self, engine: UniversalDataEngine):
        self.engine = engine
        self.running = False

    def start_game_stream(self, interval: float = 0.1):
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

    def start_telemetry_stream(self, interval: float = 0.2):
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
# GUI (PyQt5 if available, fallback to Tkinter)
# =========================

class EngineGUI:
    def __init__(self, engine: UniversalDataEngine):
        self.engine = engine
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

    # ----- PyQt5 -----

    def _init_pyqt(self):
        from PyQt5 import QtWidgets, QtCore

        self.app = QtWidgets.QApplication(sys.argv)
        self.window = QtWidgets.QWidget()
        self.window.setWindowTitle("Universal Data Engine Dashboard")

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
        self.stats_label.setText(f"Compression ratio: {ratio:.3f} | Bandwidth saved: {saved*100:.1f}%")

    def run_pyqt(self):
        self.window.show()
        self.app.exec_()

    # ----- Tkinter -----

    def _init_tk(self):
        self.root = tkinter.Tk()
        self.root.title("Universal Data Engine Dashboard")

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
        self.stats_label.config(text=f"Compression ratio: {ratio:.3f} | Bandwidth saved: {saved*100:.1f}%")
        self.root.after(500, self._update_tk)

    def run_tk(self):
        self.root.mainloop()

    # ----- Run -----

    def run(self):
        if self.mode == "pyqt":
            self.run_pyqt()
        elif self.mode == "tk":
            self.run_tk()
        else:
            print("[GUI] No GUI mode; nothing to run.")


# =========================
# Main / Demo
# =========================

def main():
    engine = UniversalDataEngine(domain="game")

    # Train ML models (quick synthetic training)
    engine.importance_classifier.train_on_synthetic(epochs=20, lr=1e-2)

    # Live data simulator
    simulator = LiveDataSimulator(engine)
    simulator.start_game_stream(interval=0.2)
    simulator.start_telemetry_stream(interval=0.5)

    # Async networking demo (run in background loop)
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

    # GUI
    gui = EngineGUI(engine)
    gui.run()


if __name__ == "__main__":
    main()
