"""
HYBRIDBRAIN v3 – Unified Single File, OS-Adaptive, Headless-Capable, GUI-Optional, Portable-Ready

Includes:
- AutoLoader (self-installing imports)
- OSManager (OS detection + headless/GUI capability)
- DependencyMap
- PersistenceLayer (SQLite)
- NeuralOrganBus
- HybridWeightOrgan (cognitive weight organ)
- PredictionOrgan
- RiskOrgan
- MetaStateEngine
- CognitiveTimeline (weight history)
- HybridBrainCore
- Tkinter GUI Control Panel + Library Health (fallback to headless if unavailable)
- Portable builder stub (PyInstaller-based)
"""

import importlib
import subprocess
import sys
import threading
import time
import sqlite3
import os
import platform
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

# ============================================================
# OS MANAGER
# ============================================================

class OSManager:
    def __init__(self):
        self.system = platform.system().lower()
        self.is_windows = self.system == "windows"
        self.is_linux = self.system == "linux"
        self.is_mac = self.system == "darwin"

    def is_headless(self) -> bool:
        # crude check: if DISPLAY missing on Linux, assume headless
        if self.is_linux and not os.environ.get("DISPLAY"):
            return True
        return False

    def describe(self) -> str:
        return f"OS: {self.system}, headless={self.is_headless()}"

os_manager = OSManager()

# ============================================================
# AUTOLOADER
# ============================================================

class AutoLoader:
    def __init__(self):
        self.cache: Dict[str, Any] = {}
        self.lock = threading.Lock()

    def _install(self, package: str):
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

    def load(self, package: str, import_name: str = None):
        name = import_name or package
        with self.lock:
            if name in self.cache:
                return self.cache[name]
        try:
            module = importlib.import_module(name)
        except ImportError:
            self._install(package)
            try:
                module = importlib.import_module(name)
            except ImportError:
                module = None
        with self.lock:
            self.cache[name] = module
        return module

    def require(self, *packages):
        loaded = {}
        for p in packages:
            if isinstance(p, tuple):
                pkg, imp = p
                loaded[imp] = self.load(pkg, imp)
            else:
                loaded[p] = self.load(p)
        return loaded

    def status(self) -> Dict[str, bool]:
        with self.lock:
            return {name: (mod is not None) for name, mod in self.cache.items()}


autoloader = AutoLoader()

# ============================================================
# DEPENDENCY MAP
# ============================================================

class DependencyMap:
    def __init__(self):
        self.map: Dict[str, List[Tuple[str, Optional[str]]]] = {}

    def register(self, organ_name: str, deps: List[Tuple[str, Optional[str]]]):
        self.map[organ_name] = deps

    def get_deps(self, organ_name: str) -> List[Tuple[str, Optional[str]]]:
        return self.map.get(organ_name, [])

    def all_deps(self) -> Dict[str, List[Tuple[str, Optional[str]]]]:
        return self.map


dependency_map = DependencyMap()

# ============================================================
# PERSISTENCE LAYER (SQLite)
# ============================================================

class PersistenceLayer:
    def __init__(self, db_path: str = "hybridbrain.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS weight_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL,
                novelty REAL,
                utility REAL,
                impact REAL,
                curiosity REAL,
                stance TEXT,
                meta_state TEXT
            )
        """)
        conn.commit()
        conn.close()

    def log_weights(self, ts: float, weights: "WeightVector", stance: str, meta_state: str):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO weight_history (ts, novelty, utility, impact, curiosity, stance, meta_state)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ts, weights.novelty_weight, weights.utility_weight,
              weights.impact_weight, weights.curiosity_weight,
              stance, meta_state))
        conn.commit()
        conn.close()

    def get_recent_weights(self, limit: int = 50) -> List[Tuple]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT ts, novelty, utility, impact, curiosity, stance, meta_state
            FROM weight_history
            ORDER BY ts DESC
            LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        return rows

# ============================================================
# NEURAL ORGAN BUS
# ============================================================

class NeuralOrganBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Any]] = {}

    def subscribe(self, topic: str, organ: Any):
        self.subscribers.setdefault(topic, []).append(organ)

    def publish(self, topic: str, message: Dict[str, Any]):
        for organ in self.subscribers.get(topic, []):
            handler = getattr(organ, "on_bus_message", None)
            if callable(handler):
                handler(topic, message)

# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class PerformanceSnapshot:
    completion_rate: float
    avg_objective: float
    feed_entropy: float
    sys_entropy: float


@dataclass
class PredictionSnapshot:
    short_term: float
    mid_term: float
    long_term: float
    volatility: float


@dataclass
class ReinforcementSignal:
    reward: float
    penalty: float
    trend: float
    success_rate: float


@dataclass
class AppetiteProfile:
    novelty_appetite: float
    utility_appetite: float
    impact_appetite: float
    curiosity_appetite: float


@dataclass
class MetaState:
    name: str
    aggressiveness: float
    dampening: float
    horizon_bias: float


@dataclass
class RiskProfile:
    risk_score: float
    integrity_score: float


@dataclass
class Fingerprint:
    exploration_bias: float
    caution_bias: float
    curiosity_bias: float
    impact_bias: float


@dataclass
class WeightVector:
    novelty_weight: float
    utility_weight: float
    impact_weight: float
    curiosity_weight: float


@dataclass
class OrganOutput:
    weights: WeightVector
    stability_score: float
    confidence_delta: float
    reasoning_tail: Dict[str, Any]

# ============================================================
# HYBRID WEIGHT ORGAN
# ============================================================

class HybridWeightOrgan:
    ORGAN_NAME = "HybridWeightOrgan"

    def __init__(self):
        self.baseline = WeightVector(0.25, 0.25, 0.25, 0.25)

    @staticmethod
    def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, x))

    @staticmethod
    def _normalize_weights(w: WeightVector) -> WeightVector:
        total = w.novelty_weight + w.utility_weight + w.impact_weight + w.curiosity_weight
        if total <= 1e-9:
            return WeightVector(0.25, 0.25, 0.25, 0.25)
        return WeightVector(
            w.novelty_weight / total,
            w.utility_weight / total,
            w.impact_weight / total,
            w.curiosity_weight / total,
        )

    def _apply_stance(self, w: WeightVector, stance: str, meta_state: MetaState) -> WeightVector:
        stance = stance.upper()
        if stance == "FLOW":
            w.novelty_weight *= 1.15
            w.curiosity_weight *= 1.15
            w.impact_weight *= 0.85
            w.utility_weight *= (1.0 + 0.10 * (1.0 - meta_state.aggressiveness))
        elif stance == "SENTINEL":
            w.impact_weight *= 1.25
            w.utility_weight *= 1.20
            w.novelty_weight *= 0.75
            w.curiosity_weight *= 0.80
        elif stance == "RECOVERY":
            avg = (w.novelty_weight + w.utility_weight + w.impact_weight + w.curiosity_weight) / 4.0
            w = WeightVector(avg, avg, avg, avg)
            factor = 1.0 - 0.3 * meta_state.dampening
            w.novelty_weight *= factor
            w.utility_weight *= factor
            w.impact_weight *= factor
            w.curiosity_weight *= factor
        return w

    def _apply_appetite(self, w: WeightVector, appetite: AppetiteProfile) -> WeightVector:
        w.novelty_weight *= (0.5 + appetite.novelty_appetite)
        w.utility_weight *= (0.5 + appetite.utility_appetite)
        w.impact_weight *= (0.5 + appetite.impact_appetite)
        w.curiosity_weight *= (0.5 + appetite.curiosity_appetite)
        return w

    def _apply_reinforcement(self, w: WeightVector, r: ReinforcementSignal) -> WeightVector:
        net = r.reward - r.penalty
        trend = r.trend
        if net > 0:
            w.utility_weight *= (1.0 + 0.15 * r.success_rate)
            w.impact_weight *= (1.0 + 0.10 * r.success_rate)
        else:
            w.novelty_weight *= (1.0 + 0.20 * (1.0 - r.success_rate))
            w.curiosity_weight *= (1.0 + 0.20 * (1.0 - r.success_rate))
        w.novelty_weight *= (1.0 + 0.10 * trend)
        w.curiosity_weight *= (1.0 + 0.10 * trend)
        return w

    def _prediction_stability(self, p: PredictionSnapshot, meta_state: MetaState) -> float:
        hb = meta_state.horizon_bias
        short_weight = 0.33 * (1.0 - hb)
        long_weight = 0.33 * (1.0 + hb)
        mid_weight = 1.0 - short_weight - long_weight
        stability = short_weight * p.short_term + mid_weight * p.mid_term + long_weight * p.long_term
        stability *= (1.0 - 0.5 * self._clamp(p.volatility))
        return self._clamp(stability)

    def _apply_risk(self, w: WeightVector, risk: RiskProfile) -> WeightVector:
        r = self._clamp(risk.risk_score)
        integrity = self._clamp(risk.integrity_score)
        w.impact_weight *= (1.0 + 0.50 * r)
        w.utility_weight *= (1.0 + 0.40 * r)
        w.novelty_weight *= (1.0 - 0.40 * r)
        w.curiosity_weight *= (1.0 - 0.30 * r)
        damp = 1.0 - 0.50 * (1.0 - integrity)
        w.novelty_weight *= damp
        w.utility_weight *= damp
        w.impact_weight *= damp
        w.curiosity_weight *= damp
        return w

    def _apply_fingerprint(self, w: WeightVector, fp: Fingerprint) -> WeightVector:
        w.novelty_weight *= (1.0 + 0.30 * fp.exploration_bias)
        w.curiosity_weight *= (1.0 + 0.30 * fp.curiosity_bias)
        w.impact_weight *= (1.0 + 0.30 * fp.impact_bias)
        w.utility_weight *= (1.0 + 0.20 * (1.0 - fp.exploration_bias))
        w.novelty_weight *= (1.0 - 0.30 * fp.caution_bias)
        w.curiosity_weight *= (1.0 - 0.20 * fp.caution_bias)
        w.impact_weight *= (1.0 + 0.30 * fp.caution_bias)
        w.utility_weight *= (1.0 + 0.20 * fp.caution_bias)
        return w

    def adjust_weights(
        self,
        perf: PerformanceSnapshot,
        preds: PredictionSnapshot,
        reinforcement: ReinforcementSignal,
        appetite: AppetiteProfile,
        meta_state: MetaState,
        risk: RiskProfile,
        fingerprint: Fingerprint,
        stance: str,
        previous_weights: Optional[WeightVector] = None,
    ) -> OrganOutput:
        w = previous_weights or self.baseline
        stability = self._prediction_stability(preds, meta_state)
        completion = self._clamp(perf.completion_rate)
        avg_obj = self._clamp(perf.avg_objective)
        sys_ent = self._clamp(perf.sys_entropy)
        meta_confidence = self._clamp(0.40 * completion + 0.30 * avg_obj + 0.20 * stability - 0.10 * sys_ent)
        w = self._apply_stance(w, stance, meta_state)
        w = self._apply_appetite(w, appetite)
        w = self._apply_reinforcement(w, reinforcement)
        w = self._apply_fingerprint(w, fingerprint)
        w = self._apply_risk(w, risk)
        global_scale = 0.8 + 0.4 * meta_confidence
        w.novelty_weight *= global_scale
        w.utility_weight *= global_scale
        w.impact_weight *= global_scale
        w.curiosity_weight *= global_scale
        w = self._normalize_weights(w)
        if previous_weights:
            prev_total = (previous_weights.novelty_weight + previous_weights.utility_weight +
                          previous_weights.impact_weight + previous_weights.curiosity_weight)
            new_total = w.novelty_weight + w.utility_weight + w.impact_weight + w.curiosity_weight
            confidence_delta = new_total - prev_total
        else:
            confidence_delta = 0.0
        reasoning_tail = {
            "stance": stance,
            "meta_state": meta_state.name,
            "prediction_stability": stability,
            "meta_confidence": meta_confidence,
            "risk_score": risk.risk_score,
            "integrity_score": risk.integrity_score,
            "completion_rate": perf.completion_rate,
            "avg_objective": perf.avg_objective,
            "sys_entropy": perf.sys_entropy,
            "feed_entropy": perf.feed_entropy,
            "reinforcement_reward": reinforcement.reward,
            "reinforcement_penalty": reinforcement.penalty,
            "reinforcement_trend": reinforcement.trend,
            "success_rate": reinforcement.success_rate,
        }
        return OrganOutput(w, stability, confidence_delta, reasoning_tail)


dependency_map.register(
    HybridWeightOrgan.ORGAN_NAME,
    [("numpy", "numpy")]  # optional example
)

# ============================================================
# PREDICTION ORGAN
# ============================================================

class PredictionOrgan:
    ORGAN_NAME = "PredictionOrgan"

    def __init__(self):
        self.last: Optional[PredictionSnapshot] = None

    def compute(self, perf: PerformanceSnapshot) -> PredictionSnapshot:
        base = perf.avg_objective
        vol = (perf.feed_entropy + perf.sys_entropy) / 2.0
        short = max(0.0, min(1.0, base + 0.1 - vol * 0.2))
        mid = max(0.0, min(1.0, base))
        long = max(0.0, min(1.0, base - 0.1 - vol * 0.1))
        snap = PredictionSnapshot(short, mid, long, vol)
        self.last = snap
        return snap

# ============================================================
# RISK ORGAN
# ============================================================

class RiskOrgan:
    ORGAN_NAME = "RiskOrgan"

    def compute(self, perf: PerformanceSnapshot, preds: PredictionSnapshot) -> RiskProfile:
        entropy = (perf.feed_entropy + perf.sys_entropy) / 2.0
        instability = preds.volatility
        risk = max(0.0, min(1.0, 0.5 * entropy + 0.5 * instability))
        integrity = max(0.0, min(1.0, 1.0 - perf.sys_entropy))
        return RiskProfile(risk, integrity)

# ============================================================
# META-STATE ENGINE
# ============================================================

class MetaStateEngine:
    ORGAN_NAME = "MetaStateEngine"

    def decide(self, perf: PerformanceSnapshot, risk: RiskProfile, preds: PredictionSnapshot) -> Tuple[MetaState, str]:
        if risk.risk_score > 0.6 or perf.sys_entropy > 0.6:
            ms = MetaState("SENTINEL", aggressiveness=0.7, dampening=0.3, horizon_bias=0.2)
            stance = "SENTINEL"
        elif perf.completion_rate < 0.4 or perf.avg_objective < 0.4:
            ms = MetaState("RECOVERY", aggressiveness=0.2, dampening=0.8, horizon_bias=0.0)
            stance = "RECOVERY"
        else:
            ms = MetaState("FLOW", aggressiveness=0.5, dampening=0.3, horizon_bias=-0.2)
            stance = "FLOW"
        return ms, stance

# ============================================================
# COGNITIVE TIMELINE
# ============================================================

class CognitiveTimeline:
    def __init__(self, persistence: PersistenceLayer):
        self.persistence = persistence

    def record(self, ts: float, weights: WeightVector, stance: str, meta_state: str):
        self.persistence.log_weights(ts, weights, stance, meta_state)

# ============================================================
# HYBRID BRAIN CORE
# ============================================================

class HybridBrainCore:
    def __init__(self):
        self.organs: Dict[str, Any] = {}
        self.bus = NeuralOrganBus()
        self.persistence = PersistenceLayer()
        self.timeline = CognitiveTimeline(self.persistence)
        self.preload_thread: Optional[threading.Thread] = None
        self.preload_stop = threading.Event()
        self._register_organs()

    def _register_organs(self):
        self.register_organ(HybridWeightOrgan.ORGAN_NAME, HybridWeightOrgan())
        self.register_organ(PredictionOrgan.ORGAN_NAME, PredictionOrgan())
        self.register_organ(RiskOrgan.ORGAN_NAME, RiskOrgan())
        self.register_organ(MetaStateEngine.ORGAN_NAME, MetaStateEngine())

    def register_organ(self, name: str, organ: Any):
        self.organs[name] = organ

    def get_organ(self, name: str) -> Any:
        return self.organs.get(name)

    def preload_dependencies_once(self):
        for organ_name, deps in dependency_map.all_deps().items():
            for pkg, imp in deps:
                autoloader.load(pkg, imp)

    def preload_daemon(self, interval: float = 10.0):
        while not self.preload_stop.is_set():
            self.preload_dependencies_once()
            time.sleep(interval)

    def start_preload_daemon(self, interval: float = 10.0):
        if self.preload_thread and self.preload_thread.is_alive():
            return
        self.preload_stop.clear()
        self.preload_thread = threading.Thread(
            target=self.preload_daemon,
            args=(interval,),
            daemon=True,
        )
        self.preload_thread.start()

    def stop_preload_daemon(self):
        self.preload_stop.set()
        if self.preload_thread:
            self.preload_thread.join(timeout=1.0)

# ============================================================
# PORTABLE APP WRAPPER (PyInstaller stub)
# ============================================================

def build_portable():
    """
    Attempts to build a portable executable using PyInstaller.
    Requires pyinstaller to be installed.
    """
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        script_name = os.path.basename(__file__)
        subprocess.check_call(
            [sys.executable, "-m", "PyInstaller", "--onefile", "--noconsole", script_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("Portable build attempted. Check 'dist' directory.")
    except Exception:
        print("Portable build failed or PyInstaller not available.")

# ============================================================
# TKINTER GUI CONTROL PANEL + LIB HEALTH (fallback to headless)
# ============================================================

def launch_gui(core: HybridBrainCore):
    tk_mod = autoloader.load("tkinter", "tkinter")
    if tk_mod is None or os_manager.is_headless():
        run_headless(core)
        return

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("HybridBrain v3 Control Panel")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    # --- Control Panel Tab ---
    control_frame = ttk.Frame(notebook, padding=10)
    notebook.add(control_frame, text="Control Panel")

    sliders = {}
    for i, (label, var_name) in enumerate([
        ("Novelty Appetite", "novelty"),
        ("Utility Appetite", "utility"),
        ("Impact Appetite", "impact"),
        ("Curiosity Appetite", "curiosity"),
    ]):
        ttk.Label(control_frame, text=label).grid(row=i, column=0, sticky="w")
        s = tk.Scale(control_frame, from_=0.0, to=1.0, orient="horizontal", resolution=0.01)
        s.set(0.7 if var_name in ("novelty", "curiosity") else 0.5)
        s.grid(row=i, column=1, sticky="ew")
        sliders[var_name] = s

    control_frame.columnconfigure(1, weight=1)

    weights_label = ttk.Label(control_frame, text="Weights: -")
    weights_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

    stance_label = ttk.Label(control_frame, text="Stance / MetaState: -")
    stance_label.grid(row=6, column=0, columnspan=2, sticky="w")

    stability_label = ttk.Label(control_frame, text="Stability: -")
    stability_label.grid(row=7, column=0, columnspan=2, sticky="w")

    def step_once():
        perf = PerformanceSnapshot(
            completion_rate=0.8,
            avg_objective=0.7,
            feed_entropy=0.3,
            sys_entropy=0.2,
        )
        pred_org: PredictionOrgan = core.get_organ(PredictionOrgan.ORGAN_NAME)
        risk_org: RiskOrgan = core.get_organ(RiskOrgan.ORGAN_NAME)
        meta_engine: MetaStateEngine = core.get_organ(MetaStateEngine.ORGAN_NAME)
        weight_org: HybridWeightOrgan = core.get_organ(HybridWeightOrgan.ORGAN_NAME)

        preds = pred_org.compute(perf)
        risk = risk_org.compute(perf, preds)
        meta_state, stance = meta_engine.decide(perf, risk, preds)

        appetite = AppetiteProfile(
            novelty_appetite=sliders["novelty"].get(),
            utility_appetite=sliders["utility"].get(),
            impact_appetite=sliders["impact"].get(),
            curiosity_appetite=sliders["curiosity"].get(),
        )
        reinforcement = ReinforcementSignal(
            reward=0.6,
            penalty=0.1,
            trend=0.2,
            success_rate=0.75,
        )
        fingerprint = Fingerprint(
            exploration_bias=0.5,
            caution_bias=0.2,
            curiosity_bias=0.7,
            impact_bias=0.4,
        )

        out = weight_org.adjust_weights(
            perf=perf,
            preds=preds,
            reinforcement=reinforcement,
            appetite=appetite,
            meta_state=meta_state,
            risk=risk,
            fingerprint=fingerprint,
            stance=stance,
            previous_weights=None,
        )

        ts = time.time()
        core.timeline.record(ts, out.weights, stance, meta_state.name)

        weights_label.config(
            text=f"Weights: N={out.weights.novelty_weight:.3f}, "
                 f"U={out.weights.utility_weight:.3f}, "
                 f"I={out.weights.impact_weight:.3f}, "
                 f"C={out.weights.curiosity_weight:.3f}"
        )
        stance_label.config(text=f"Stance / MetaState: {stance} / {meta_state.name}")
        stability_label.config(text=f"Stability: {out.stability_score:.3f}")

    step_button = ttk.Button(control_frame, text="Step", command=step_once)
    step_button.grid(row=4, column=0, columnspan=2, pady=(10, 0))

    # --- Library Health Tab ---
    health_frame = ttk.Frame(notebook, padding=10)
    notebook.add(health_frame, text="Library Health")

    tree = ttk.Treeview(health_frame, columns=("status",), show="headings", height=10)
    tree.heading("status", text="Status")
    tree.column("status", width=300, anchor="w")
    tree.pack(fill="both", expand=True)

    def refresh_health():
        tree.delete(*tree.get_children())
        status = autoloader.status()
        for name, ok in status.items():
            tree.insert("", "end", values=(f"{name}: {'OK' if ok else 'MISSING'}",))
        root.after(2000, refresh_health)

    refresh_health()
    root.mainloop()

# ============================================================
# HEADLESS MODE
# ============================================================

def run_headless(core: HybridBrainCore):
    print(os_manager.describe())
    print("Running HybridBrain in headless mode (no GUI).")
    perf = PerformanceSnapshot(
        completion_rate=0.8,
        avg_objective=0.7,
        feed_entropy=0.3,
        sys_entropy=0.2,
    )
    pred_org: PredictionOrgan = core.get_organ(PredictionOrgan.ORGAN_NAME)
    risk_org: RiskOrgan = core.get_organ(RiskOrgan.ORGAN_NAME)
    meta_engine: MetaStateEngine = core.get_organ(MetaStateEngine.ORGAN_NAME)
    weight_org: HybridWeightOrgan = core.get_organ(HybridWeightOrgan.ORGAN_NAME)

    preds = pred_org.compute(perf)
    risk = risk_org.compute(perf, preds)
    meta_state, stance = meta_engine.decide(perf, risk, preds)

    appetite = AppetiteProfile(
        novelty_appetite=0.7,
        utility_appetite=0.5,
        impact_appetite=0.5,
        curiosity_appetite=0.8,
    )
    reinforcement = ReinforcementSignal(
        reward=0.6,
        penalty=0.1,
        trend=0.2,
        success_rate=0.75,
    )
    fingerprint = Fingerprint(
        exploration_bias=0.5,
        caution_bias=0.2,
        curiosity_bias=0.7,
        impact_bias=0.4,
    )

    out = weight_org.adjust_weights(
        perf=perf,
        preds=preds,
        reinforcement=reinforcement,
        appetite=appetite,
        meta_state=meta_state,
        risk=risk,
        fingerprint=fingerprint,
        stance=stance,
        previous_weights=None,
    )

    ts = time.time()
    core.timeline.record(ts, out.weights, stance, meta_state.name)

    print(f"Weights: {out.weights}")
    print(f"Stance / MetaState: {stance} / {meta_state.name}")
    print(f"Stability: {out.stability_score:.3f}")

# ============================================================
# ENTRYPOINT
# ============================================================

def main():
    core = HybridBrainCore()
    core.start_preload_daemon(interval=5.0)
    launch_gui(core)
    core.stop_preload_daemon()


if __name__ == "__main__":
    main()
