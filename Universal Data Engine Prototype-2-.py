#!/usr/bin/env python3
"""
Universal Data Engine v4 (Unified File)

New upgrades in this version:
- TransformerPredictor: Transformer-based temporal prediction of numeric fields
- AlteredStatesMode: switchable prediction “consciousness” modes (normal / exploratory / hallucinatory)
- ProbabilisticField: quantum-inspired probabilistic representation of key values (mean + variance)
- Integrated into the existing UniversalDataEngine pipeline

This is a conceptual but runnable skeleton. It assumes PyTorch is available for best results,
but will degrade gracefully if not.
"""

import importlib
import sys
import os
import json
import time
import random
import threading
from typing import Any, Dict, List, Optional, Tuple

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
    "PyQt5",
    "tkinter",
])

np = LIBS["numpy"]
torch = LIBS["torch"]
PyQt5 = LIBS["PyQt5"]
tkinter = LIBS["tkinter"]


# =========================
# ProbabilisticField (quantum-inspired)
# =========================

class ProbabilisticField:
    """
    Represents a numeric value as a distribution:
    - mean
    - variance

    You can sample from it, and update it with new observations.
    """

    def __init__(self, mean: float = 0.0, var: float = 1.0):
        self.mean = float(mean)
        self.var = float(max(var, 1e-6))

    def sample(self) -> float:
        return random.gauss(self.mean, self.var)

    def update(self, observation: float, weight: float = 1.0):
        # Simple Bayesian-ish update
        self.mean = (self.mean + weight * observation) / (1.0 + weight)
        self.var = max(1e-6, self.var * 0.9)

    def to_dict(self) -> Dict[str, float]:
        return {"mean": self.mean, "var": self.var}


# =========================
# AlteredStatesMode
# =========================

class AlteredStatesMode:
    """
    Controls how predictions are “distorted” or “expanded”:
    - normal: conservative
    - exploratory: mild noise
    - hallucinatory: aggressive noise / creativity
    """

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
    """
    Transformer-based predictor for numeric fields over time.
    If torch is not available, falls back to simple averaging.
    """

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

            def forward(self, seq):  # seq: [T, B, d_model]
                enc = self.encoder(seq)
                last = enc[-1]        # [B, d_model]
                return self.out(last) # [B, 1]

        self.model = SimpleTransformer(self.d_model, self.nhead, self.num_layers)

    def _encode_state(self, state: Dict[str, Any]) -> List[float]:
        # Simple encoding: take up to d_model numeric fields
        nums = []
        for k, v in state.items():
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
            # Fallback: simple average of last numeric field
            last = history[-1]
            vals = [float(v) for v in last.values() if isinstance(v, (int, float))]
            return sum(vals) / len(vals) if vals else 0.0

        # Build sequence tensor: [T, B=1, d_model]
        seq = [self._encode_state(s) for s in history]
        x = torch.tensor(seq, dtype=torch.float32).unsqueeze(1)  # [T, 1, d_model]
        with torch.no_grad():
            y = self.model(x)  # [1, 1]
        return float(y.item())


# =========================
# ML-based Data Importance Classifier (simple)
# =========================

class DataImportanceClassifier:
    """
    Classifies fields into:
    - critical
    - important
    - cosmetic
    - ignore

    Uses simple heuristics; can be extended with ML.
    """

    def __init__(self):
        pass

    def predict_importance(self, key: str, value: Any) -> str:
        k = key.lower()
        if "id" in k or "pos" in k or "state" in k or "timestamp" in k:
            return "critical"
        if "vel" in k or "score" in k or "health" in k or "temp" in k or "speed" in k:
            return "important"
        if "bg" in k or "color" in k or "shadow" in k or "fx" in k or "ui" in k:
            return "cosmetic"
        return "ignore"


# =========================
# Critical Data Extractor
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
# Predictive Reconstructor (with Transformer + AlteredStates + ProbabilisticField)
# =========================

class PredictiveReconstructor:
    """
    Reconstructs missing or non-critical data using:
    - cached history
    - TransformerPredictor
    - AlteredStatesMode
    - ProbabilisticField for key numeric values
    """

    def __init__(self):
        self.cache: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []
        self.transformer = TransformerPredictor()
        self.mode = AlteredStatesMode("normal")

    def set_mode(self, mode: str):
        self.mode = AlteredStatesMode(mode)

    def cache_state(self, key: str, state: Dict[str, Any]) -> None:
        self.cache[key] = state
        self.history.append(state)
        if len(self.history) > 64:
            self.history.pop(0)

    def reconstruct(self, base_state: Dict[str, Any], missing_keys: List[str]) -> Dict[str, Any]:
        reconstructed = dict(base_state)
        last_full = self.cache.get("last_full", {})

        # Build probabilistic fields for numeric keys in base_state
        prob_fields: Dict[str, ProbabilisticField] = {}
        for k, v in base_state.items():
            if isinstance(v, (int, float)):
                prob_fields[k] = ProbabilisticField(mean=float(v), var=1.0)

        # Use transformer to predict a scalar “trend”
        trend = self.transformer.predict_next_scalar(self.history)
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
            # Use trend + probabilistic sampling
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
# Domain-specific rules
# =========================

def get_domain_rules(domain: str) -> Dict[str, Any]:
    domain = domain.lower()
    if domain == "game":
        return {
            "critical": ["id", "pos_x", "pos_y", "state"],
            "important": ["vel_x", "vel_y", "health", "score"],
            "cosmetic": ["bg_color", "shadow_quality", "fx_level"],
            "ignore": [],
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
    - CriticalDataExtractor
    - DeltaEngine
    - PredictiveReconstructor (Transformer + AlteredStates + ProbabilisticField)
    """

    def __init__(self, domain: str = "game"):
        rules = get_domain_rules(domain)
        self.importance_classifier = DataImportanceClassifier()
        self.extractor = CriticalDataExtractor(rules=rules, classifier=self.importance_classifier)
        self.delta_engine = DeltaEngine()
        self.reconstructor = PredictiveReconstructor()

        self.last_full_state: Dict[str, Any] = {}
        self.total_raw_bytes = 0
        self.total_delta_bytes = 0

    def set_mode(self, mode: str):
        self.reconstructor.set_mode(mode)

    def process_state(self, new_state: Dict[str, Any]) -> Dict[str, Any]:
        # 1. Extract essential data
        essential = self.extractor.extract(new_state)

        # 2. Compute delta
        delta = self.delta_engine.compute_delta(self.last_full_state, essential)

        # 3. Apply delta
        updated_essential = self.delta_engine.apply_delta(self.last_full_state, delta)

        # 4. Reconstruct missing / cosmetic data
        missing_keys = [k for k in new_state.keys() if k not in updated_essential]
        self.reconstructor.cache_state("last_full", self.last_full_state)
        full_reconstructed = self.reconstructor.reconstruct(updated_essential, missing_keys)

        # Update internal last_full_state
        self.last_full_state = dict(full_reconstructed)

        # Track simple “compression” stats (raw vs delta size)
        raw = json.dumps(new_state).encode("utf-8")
        delta_bytes = json.dumps(delta).encode("utf-8")
        self.total_raw_bytes += len(raw)
        self.total_delta_bytes += len(delta_bytes)

        return full_reconstructed

    def get_compression_ratio(self) -> float:
        if self.total_raw_bytes == 0:
            return 1.0
        return self.total_delta_bytes / self.total_raw_bytes


# =========================
# Live Data Simulator
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
                self.engine.process_state(state)
                time.sleep(interval)
                t += interval

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
        self.window.setWindowTitle("Universal Data Engine Dashboard (Transformer + Altered States)")

        layout = QtWidgets.QVBoxLayout()

        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)

        self.stats_label = QtWidgets.QLabel("Stats:")
        layout.addWidget(self.stats_label)

        # Mode buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_normal = QtWidgets.QPushButton("Normal")
        self.btn_exploratory = QtWidgets.QPushButton("Exploratory")
        self.btn_hallucinatory = QtWidgets.QPushButton("Hallucinatory")
        btn_layout.addWidget(self.btn_normal)
        btn_layout.addWidget(self.btn_exploratory)
        btn_layout.addWidget(self.btn_hallucinatory)
        layout.addLayout(btn_layout)

        self.btn_normal.clicked.connect(lambda: self.engine.set_mode("normal"))
        self.btn_exploratory.clicked.connect(lambda: self.engine.set_mode("exploratory"))
        self.btn_hallucinatory.clicked.connect(lambda: self.engine.set_mode("hallucinatory"))

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
        self.root.title("Universal Data Engine Dashboard (Transformer + Altered States)")

        self.text = tkinter.Text(self.root, height=20, width=80)
        self.text.pack()

        self.stats_label = tkinter.Label(self.root, text="Stats:")
        self.stats_label.pack()

        # Mode buttons
        frame = tkinter.Frame(self.root)
        frame.pack()
        btn_normal = tkinter.Button(frame, text="Normal", command=lambda: self.engine.set_mode("normal"))
        btn_exploratory = tkinter.Button(frame, text="Exploratory", command=lambda: self.engine.set_mode("exploratory"))
        btn_hallucinatory = tkinter.Button(frame, text="Hallucinatory", command=lambda: self.engine.set_mode("hallucinatory"))
        btn_normal.pack(side=tkinter.LEFT)
        btn_exploratory.pack(side=tkinter.LEFT)
        btn_hallucinatory.pack(side=tkinter.LEFT)

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

    # Start live game stream
    simulator = LiveDataSimulator(engine)
    simulator.start_game_stream(interval=0.3)

    # GUI
    gui = EngineGUI(engine)
    gui.run()


if __name__ == "__main__":
    main()
