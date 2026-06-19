"""
UNIFIED HYBRIDBRAIN CORE + AUTOLOADER + WEIGHT ORGAN + DEP MAP + GUI HEALTH + PRELOAD DAEMON
Single-file, double-click runnable.
"""

import importlib
import subprocess
import sys
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

# ============================================================
# AUTOLOADER
# ============================================================

class AutoLoader:
    """
    Universal autoloader for all necessary libraries.
    Automatically installs missing packages and imports them.
    Designed for double-click execution (no terminal required).
    """

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
            pass  # fail silently

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
    """
    Organs declare dependencies here.
    Each organ has a list of (pip_name, import_name or None).
    """

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
# WEIGHT ORGAN DATA STRUCTURES
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
    """
    Standalone weight-adjustment organ.
    Designed to plug into HybridBrain and Tri-Stance Decision Engine.
    Uses autoloader implicitly via dependency_map if needed.
    """

    ORGAN_NAME = "HybridWeightOrgan"

    def __init__(self):
        self.baseline = WeightVector(
            novelty_weight=0.25,
            utility_weight=0.25,
            impact_weight=0.25,
            curiosity_weight=0.25,
        )

    @staticmethod
    def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, x))

    @staticmethod
    def _normalize_weights(w: WeightVector) -> WeightVector:
        total = (
            w.novelty_weight
            + w.utility_weight
            + w.impact_weight
            + w.curiosity_weight
        )
        if total <= 1e-9:
            return WeightVector(0.25, 0.25, 0.25, 0.25)
        return WeightVector(
            novelty_weight=w.novelty_weight / total,
            utility_weight=w.utility_weight / total,
            impact_weight=w.impact_weight / total,
            curiosity_weight=w.curiosity_weight / total,
        )

    @staticmethod
    def _ewma(current: float, previous: float, alpha: float) -> float:
        return alpha * current + (1.0 - alpha) * previous

    def _apply_stance(
        self,
        w: WeightVector,
        stance: str,
        meta_state: MetaState,
    ) -> WeightVector:
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
            avg = (
                w.novelty_weight
                + w.utility_weight
                + w.impact_weight
                + w.curiosity_weight
            ) / 4.0
            w = WeightVector(avg, avg, avg, avg)
            factor = 1.0 - 0.3 * meta_state.dampening
            w.novelty_weight *= factor
            w.utility_weight *= factor
            w.impact_weight *= factor
            w.curiosity_weight *= factor

        return w

    def _apply_appetite(
        self,
        w: WeightVector,
        appetite: AppetiteProfile,
    ) -> WeightVector:
        w.novelty_weight *= (0.5 + appetite.novelty_appetite)
        w.utility_weight *= (0.5 + appetite.utility_appetite)
        w.impact_weight *= (0.5 + appetite.impact_appetite)
        w.curiosity_weight *= (0.5 + appetite.curiosity_appetite)
        return w

    def _apply_reinforcement(
        self,
        w: WeightVector,
        r: ReinforcementSignal,
    ) -> WeightVector:
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

    def _prediction_stability(
        self,
        p: PredictionSnapshot,
        meta_state: MetaState,
    ) -> float:
        hb = meta_state.horizon_bias
        short_weight = 0.33 * (1.0 - hb)
        long_weight = 0.33 * (1.0 + hb)
        mid_weight = 1.0 - short_weight - long_weight

        stability = (
            short_weight * p.short_term
            + mid_weight * p.mid_term
            + long_weight * p.long_term
        )
        stability *= (1.0 - 0.5 * self._clamp(p.volatility))
        return self._clamp(stability)

    def _apply_risk(
        self,
        w: WeightVector,
        risk: RiskProfile,
    ) -> WeightVector:
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

    def _apply_fingerprint(
        self,
        w: WeightVector,
        fp: Fingerprint,
    ) -> WeightVector:
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
        feed_ent = self._clamp(perf.feed_entropy)

        meta_confidence = (
            0.40 * completion
            + 0.30 * avg_obj
            + 0.20 * stability
            - 0.10 * sys_ent
        )
        meta_confidence = self._clamp(meta_confidence)

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
            prev_total = (
                previous_weights.novelty_weight
                + previous_weights.utility_weight
                + previous_weights.impact_weight
                + previous_weights.curiosity_weight
            )
            new_total = (
                w.novelty_weight
                + w.utility_weight
                + w.impact_weight
                + w.curiosity_weight
            )
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
            "appetite": {
                "novelty": appetite.novelty_appetite,
                "utility": appetite.utility_appetite,
                "impact": appetite.impact_appetite,
                "curiosity": appetite.curiosity_appetite,
            },
            "fingerprint": {
                "exploration_bias": fingerprint.exploration_bias,
                "caution_bias": fingerprint.caution_bias,
                "curiosity_bias": fingerprint.curiosity_bias,
                "impact_bias": fingerprint.impact_bias,
            },
        }

        return OrganOutput(
            weights=w,
            stability_score=stability,
            confidence_delta=confidence_delta,
            reasoning_tail=reasoning_tail,
        )


# register organ dependencies (example: numpy optional)
dependency_map.register(
    HybridWeightOrgan.ORGAN_NAME,
    [
        ("numpy", "numpy"),  # optional, just an example
    ],
)

# ============================================================
# HYBRID BRAIN CORE + PRELOAD DAEMON
# ============================================================

class HybridBrainCore:
    """
    Minimal core that:
    - Holds organs
    - Uses dependency map
    - Runs preload daemon
    """

    def __init__(self):
        self.organs: Dict[str, Any] = {}
        self.preload_thread: Optional[threading.Thread] = None
        self.preload_stop = threading.Event()

        self.register_organ(HybridWeightOrgan.ORGAN_NAME, HybridWeightOrgan())

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
# GUI: LIBRARY HEALTH PANEL (TKINTER)
# ============================================================

def launch_library_health_gui(core: HybridBrainCore):
    tk = autoloader.load("tkinter", "tkinter")
    if tk is None:
        return  # no GUI possible

    import tkinter as tk_mod
    from tkinter import ttk

    root = tk_mod.Tk()
    root.title("HybridBrain Library Health")

    frame = ttk.Frame(root, padding=10)
    frame.pack(fill="both", expand=True)

    tree = ttk.Treeview(frame, columns=("status",), show="headings", height=10)
    tree.heading("status", text="Status")
    tree.column("status", width=120, anchor="center")
    tree.pack(fill="both", expand=True)

    def refresh():
        tree.delete(*tree.get_children())
        status = autoloader.status()
        for name, ok in status.items():
            tree.insert("", "end", values=(f"{name}: {'OK' if ok else 'MISSING'}",))
        root.after(2000, refresh)

    refresh()
    root.mainloop()


# ============================================================
# DEMO / ENTRYPOINT
# ============================================================

def demo_run():
    core = HybridBrainCore()
    core.start_preload_daemon(interval=5.0)

    organ: HybridWeightOrgan = core.get_organ(HybridWeightOrgan.ORGAN_NAME)

    perf = PerformanceSnapshot(
        completion_rate=0.8,
        avg_objective=0.7,
        feed_entropy=0.3,
        sys_entropy=0.2,
    )
    preds = PredictionSnapshot(
        short_term=0.9,
        mid_term=0.8,
        long_term=0.7,
        volatility=0.2,
    )
    reinforcement = ReinforcementSignal(
        reward=0.6,
        penalty=0.1,
        trend=0.2,
        success_rate=0.75,
    )
    appetite = AppetiteProfile(
        novelty_appetite=0.8,
        utility_appetite=0.6,
        impact_appetite=0.5,
        curiosity_appetite=0.9,
    )
    meta_state = MetaState(
        name="FLOW",
        aggressiveness=0.4,
        dampening=0.2,
        horizon_bias=-0.2,
    )
    risk = RiskProfile(
        risk_score=0.3,
        integrity_score=0.9,
    )
    fingerprint = Fingerprint(
        exploration_bias=0.5,
        caution_bias=0.2,
        curiosity_bias=0.7,
        impact_bias=0.4,
    )

    out = organ.adjust_weights(
        perf=perf,
        preds=preds,
        reinforcement=reinforcement,
        appetite=appetite,
        meta_state=meta_state,
        risk=risk,
        fingerprint=fingerprint,
        stance="FLOW",
        previous_weights=None,
    )

    print("Weights:", out.weights)
    print("Stability:", out.stability_score)
    print("Meta reasoning:", out.reasoning_tail)

    # Launch GUI in main thread
    launch_library_health_gui(core)
    core.stop_preload_daemon()


if __name__ == "__main__":
    demo_run()
