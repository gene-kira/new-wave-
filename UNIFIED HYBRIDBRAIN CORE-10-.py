"""
HYBRIDBRAIN v6.0 – PROMETHEUS+Ø SCANNER
Monolithic, OS-adaptive, distributed-ready AI kernel with predictive “scanner” behavior
and a tri-state semantic layer (1 / 0 / Ø) for universal meta-encoding.

Includes ALL v5.1 features plus:

- Tri-state semantic bit (TSB): 0, 1, Ø (hybrid)
- SemanticField / SemanticVector for meta-encoding context
- SemanticOrgan: builds Ø-aware semantic snapshots from system + cognition
- Semantic traces embedded into reasoning_tail + timeline export
- Cleaned/defensive GPU handling, scanner, AI-OS mode, policies, plugins
- Tkinter GUI, Web API, headless mode
- Portable build stubs (PyInstaller / Nuitka)
"""

import importlib
import subprocess
import sys
import threading
import time
import sqlite3
import os
import platform
import shutil
import json
import csv
import socket
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple, Callable

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
        except Exception:
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
# PERSISTENCE LAYER (SQLite) + DB HEALTH ENGINE + COGNITIVE TIMELINE
# ============================================================

class PersistenceLayer:
    CURRENT_SCHEMA_VERSION = 3  # v6.0 adds semantic fields

    def __init__(self, db_path: str = "hybridbrain.db"):
        self.db_path = db_path
        self.backup_dir = "db_backups"
        os.makedirs(self.backup_dir, exist_ok=True)
        self._init_db()
        self._migrate_db()
        self.run_health_cycle(startup=True)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._connect()
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
                meta_state TEXT,
                risk REAL,
                stability REAL,
                entropy REAL,
                semantic_0 REAL,
                semantic_1 REAL,
                semantic_hybrid REAL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER
            )
        """)
        cur.execute("SELECT COUNT(*) FROM schema_version")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO schema_version (version) VALUES (1)")

        conn.commit()
        conn.close()

    def _get_schema_version(self) -> int:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("SELECT version FROM schema_version")
            row = cur.fetchone()
            if row is None:
                version = 1
                cur.execute("INSERT INTO schema_version (version) VALUES (1)")
                conn.commit()
            else:
                version = row[0]
        except sqlite3.OperationalError:
            version = 1
        conn.close()
        return version

    def _set_schema_version(self, version: int):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM schema_version")
        cur.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.commit()
        conn.close()

    def _backup_db(self, reason: str = "auto"):
        if not os.path.exists(self.db_path):
            return
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        base = os.path.basename(self.db_path)
        backup_name = f"{base}.{reason}.{ts}.bak"
        backup_path = os.path.join(self.backup_dir, backup_name)
        try:
            shutil.copy2(self.db_path, backup_path)
        except Exception:
            pass

    def _integrity_check(self) -> bool:
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return False
            return all(r[0].lower() == "ok" for r in rows)
        except Exception:
            return False

    def _compact_db(self):
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute("VACUUM")
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _repair_db(self):
        ok = self._integrity_check()
        if ok:
            return
        self._backup_db(reason="corrupt")
        try:
            corrupted_path = self.db_path + ".corrupt"
            if os.path.exists(corrupted_path):
                os.remove(corrupted_path)
            os.rename(self.db_path, corrupted_path)
        except Exception:
            pass
        self._init_db()
        self._migrate_db()

    def _migrate_db(self):
        current_version = self._get_schema_version()

        if current_version < 2:
            self._backup_db(reason="pre_migration_v1_to_v2")
            conn = self._connect()
            cur = conn.cursor()

            def add_column(name, coltype):
                try:
                    cur.execute(f"ALTER TABLE weight_history ADD COLUMN {name} {coltype}")
                except sqlite3.OperationalError:
                    pass

            add_column("risk", "REAL")
            add_column("stability", "REAL")
            add_column("entropy", "REAL")

            conn.commit()
            conn.close()
            self._set_schema_version(2)
            current_version = 2

        if current_version < 3:
            self._backup_db(reason="pre_migration_v2_to_v3")
            conn = self._connect()
            cur = conn.cursor()

            def add_column(name, coltype):
                try:
                    cur.execute(f"ALTER TABLE weight_history ADD COLUMN {name} {coltype}")
                except sqlite3.OperationalError:
                    pass

            add_column("semantic_0", "REAL")
            add_column("semantic_1", "REAL")
            add_column("semantic_hybrid", "REAL")

            conn.commit()
            conn.close()
            self._set_schema_version(3)

    def run_health_cycle(self, startup: bool = False):
        self._repair_db()
        self._compact_db()

    def log_weights(
        self,
        ts: float,
        weights: "WeightVector",
        stance: str,
        meta_state: str,
        risk: float,
        stability: float,
        entropy: float,
        semantic_0: float,
        semantic_1: float,
        semantic_hybrid: float,
    ):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO weight_history
            (ts, novelty, utility, impact, curiosity,
             stance, meta_state, risk, stability, entropy,
             semantic_0, semantic_1, semantic_hybrid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts,
            weights.novelty_weight,
            weights.utility_weight,
            weights.impact_weight,
            weights.curiosity_weight,
            stance,
            meta_state,
            risk,
            stability,
            entropy,
            semantic_0,
            semantic_1,
            semantic_hybrid,
        ))
        conn.commit()
        conn.close()

    def get_recent_weights(self, limit: int = 50) -> List[Tuple]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT ts, novelty, utility, impact, curiosity,
                   stance, meta_state, risk, stability, entropy,
                   semantic_0, semantic_1, semantic_hybrid
            FROM weight_history
            ORDER BY ts DESC
            LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        return rows

    def export_timeline_json(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.get_recent_weights(limit=limit)
        return [
            {
                "ts": r[0],
                "novelty": r[1],
                "utility": r[2],
                "impact": r[3],
                "curiosity": r[4],
                "stance": r[5],
                "meta_state": r[6],
                "risk": r[7],
                "stability": r[8],
                "entropy": r[9],
                "semantic_0": r[10],
                "semantic_1": r[11],
                "semantic_hybrid": r[12],
            }
            for r in rows
        ]

    def export_timeline_csv(self, path: str, limit: int = 100):
        rows = self.get_recent_weights(limit=limit)
        fieldnames = [
            "ts", "novelty", "utility", "impact", "curiosity",
            "stance", "meta_state", "risk", "stability", "entropy",
            "semantic_0", "semantic_1", "semantic_hybrid"
        ]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in rows:
                    writer.writerow({
                        "ts": r[0],
                        "novelty": r[1],
                        "utility": r[2],
                        "impact": r[3],
                        "curiosity": r[4],
                        "stance": r[5],
                        "meta_state": r[6],
                        "risk": r[7],
                        "stability": r[8],
                        "entropy": r[9],
                        "semantic_0": r[10],
                        "semantic_1": r[11],
                        "semantic_hybrid": r[12],
                    })
        except Exception:
            pass

class CognitiveTimeline:
    def __init__(self, persistence: PersistenceLayer):
        self.persistence = persistence

    def record(
        self,
        ts: float,
        weights: "WeightVector",
        stance: str,
        meta_state: str,
        risk: float,
        stability: float,
        entropy: float,
        semantic_0: float,
        semantic_1: float,
        semantic_hybrid: float,
    ):
        self.persistence.log_weights(
            ts, weights, stance, meta_state, risk, stability, entropy,
            semantic_0, semantic_1, semantic_hybrid
        )

# ============================================================
# NEURAL SPINE BUS v2
# ============================================================

@dataclass
class SpineMessage:
    topic: str
    payload: Dict[str, Any]
    priority: int = 5
    channel: str = "cortex"

class NeuralSpineBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Any]] = {}
        self.lock = threading.Lock()

    def subscribe(self, topic: str, organ: Any):
        with self.lock:
            self.subscribers.setdefault(topic, []).append(organ)

    def publish(self, msg: SpineMessage):
        with self.lock:
            subs = list(self.subscribers.get(msg.topic, []))
        for organ in subs:
            handler = getattr(organ, "on_bus_message", None)
            if callable(handler):
                try:
                    handler(msg)
                except Exception:
                    pass

# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class PerformanceSnapshot:
    completion_rate: float
    avg_objective: float
    feed_entropy: float
    sys_entropy: float
    cpu_load: float = 0.0
    mem_load: float = 0.0
    io_load: float = 0.0
    net_load: float = 0.0
    proc_count: int = 0

@dataclass
class PredictionSnapshot:
    short_term: float
    mid_term: float
    long_term: float
    volatility: float
    short_uncertainty: float = 0.0
    mid_uncertainty: float = 0.0
    long_uncertainty: float = 0.0

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
# TRI-STATE SEMANTIC LAYER (Ø-BIT)
# ============================================================

class TriBitState:
    ZERO = 0
    ONE = 1
    HYBRID = 2  # Ø: 1 and 0 fused / context-dependent

@dataclass
class TriBit:
    state: int  # 0, 1, 2

    def as_vector(self) -> Tuple[float, float, float]:
        if self.state == TriBitState.ZERO:
            return (1.0, 0.0, 0.0)
        if self.state == TriBitState.ONE:
            return (0.0, 1.0, 0.0)
        return (0.0, 0.0, 1.0)

@dataclass
class SemanticVector:
    zero: float
    one: float
    hybrid: float

    def normalized(self) -> "SemanticVector":
        s = self.zero + self.one + self.hybrid
        if s <= 1e-9:
            return SemanticVector(1/3, 1/3, 1/3)
        return SemanticVector(self.zero / s, self.one / s, self.hybrid / s)

class SemanticOrgan:
    """
    Builds a tri-state semantic snapshot from performance, risk, predictions, and meta-state.
    This is your Ø-layer: 0 / 1 / Ø as a universal meta-encoding.
    """
    ORGAN_NAME = "SemanticOrgan"

    @staticmethod
    def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, x))

    def compute(
        self,
        perf: PerformanceSnapshot,
        risk: RiskProfile,
        preds: PredictionSnapshot,
        meta_state: MetaState,
    ) -> SemanticVector:
        # Intuition:
        # - High stability + low risk -> more "1" (assertive)
        # - High risk + high volatility -> more "0" (inhibit)
        # - Mixed / transitional regimes -> more "Ø" (hybrid/context)
        load = (perf.cpu_load + perf.mem_load + perf.io_load + perf.net_load) / 4.0
        stability_hint = 1.0 - preds.volatility
        risk_hint = risk.risk_score
        ent = (perf.feed_entropy + perf.sys_entropy) / 2.0

        one_raw = self._clamp(0.5 * stability_hint + 0.2 * perf.completion_rate - 0.2 * risk_hint)
        zero_raw = self._clamp(0.4 * risk_hint + 0.2 * ent + 0.2 * load)
        hybrid_raw = self._clamp(1.0 - abs(one_raw - zero_raw))

        # Meta-state nudges:
        if meta_state.name == "FLOW":
            one_raw *= 1.1
            hybrid_raw *= 1.05
        elif meta_state.name == "SENTINEL":
            zero_raw *= 1.15
            hybrid_raw *= 1.05
        elif meta_state.name == "RECOVERY":
            hybrid_raw *= 1.2

        vec = SemanticVector(zero_raw, one_raw, hybrid_raw).normalized()
        return vec

# ============================================================
# COGNITIVE GRAPH ENGINE
# ============================================================

class CognitiveGraph:
    def __init__(self):
        self.edges: Dict[Tuple[str, str], float] = {}

    def add_edge(self, src: str, dst: str, weight: float):
        self.edges[(src, dst)] = weight

    def influences_for(self, dst: str) -> List[Tuple[str, float]]:
        return [(s, w) for (s, d), w in self.edges.items() if d == dst]

cognitive_graph = CognitiveGraph()
cognitive_graph.add_edge("PredictionOrganV2", "RiskOrganV2", 0.2)
cognitive_graph.add_edge("GPUPredictionOrgan", "RiskOrganV2", 0.2)
cognitive_graph.add_edge("RiskOrganV2", "HybridWeightOrgan", 0.3)

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
        potential: float = 0.0,
        potential_gradient: float = 0.0,
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

        pot = self._clamp(potential)
        grad = potential_gradient
        if grad > 0:
            w.utility_weight *= (1.0 + 0.3 * pot)
            w.impact_weight *= (1.0 + 0.2 * pot)
            w.novelty_weight *= (1.0 - 0.3 * pot)
            w.curiosity_weight *= (1.0 - 0.2 * pot)
        else:
            w.novelty_weight *= (1.0 + 0.2 * (1.0 - pot))
            w.curiosity_weight *= (1.0 + 0.2 * (1.0 - pot))

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
            "cpu_load": perf.cpu_load,
            "mem_load": perf.mem_load,
            "io_load": perf.io_load,
            "net_load": perf.net_load,
            "proc_count": perf.proc_count,
            "potential": potential,
            "potential_gradient": potential_gradient,
            "short_uncertainty": preds.short_uncertainty,
            "mid_uncertainty": preds.mid_uncertainty,
            "long_uncertainty": preds.long_uncertainty,
        }
        return OrganOutput(w, stability, confidence_delta, reasoning_tail)

dependency_map.register(
    HybridWeightOrgan.ORGAN_NAME,
    [("numpy", "numpy")]
)

# ============================================================
# GPU-ACCELERATED PREDICTION ORGAN (defensive GPU handling)
# ============================================================

class GPUPredictionOrgan:
    ORGAN_NAME = "GPUPredictionOrgan"

    def __init__(self):
        self.torch = None
        self.device = "cpu"
        self.model = None
        self.last: Optional[PredictionSnapshot] = None
        self.alpha = 0.4

        t = autoloader.load("torch", "torch")
        if t is None:
            return

        try:
            cuda_ok = False
            if hasattr(t, "cuda") and hasattr(t.cuda, "is_available"):
                try:
                    cuda_ok = bool(t.cuda.is_available())
                except Exception:
                    cuda_ok = False

            self.torch = t
            self.device = "cuda" if cuda_ok else "cpu"
            self.model = self._build_model()
        except Exception:
            self.torch = None
            self.device = "cpu"
            self.model = None

    def _build_model(self):
        nn = self.torch.nn

        class SimplePred(nn.Module):
            def __init__(self):
                super().__init__()
                self.fc1 = nn.Linear(6, 16)
                self.fc2 = nn.Linear(16, 4)

            def forward(self, x):
                x = self.fc1(x).relu()
                x = self.fc2(x).sigmoid()
                return x

        m = SimplePred().to(self.device)
        return m

    def _ewma(self, new: float, old: Optional[float]) -> float:
        if old is None:
            return new
        return self.alpha * new + (1.0 - self.alpha) * old

    def compute(self, perf: PerformanceSnapshot) -> PredictionSnapshot:
        if self.torch is None or self.model is None:
            return PredictionSnapshot(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)

        base = perf.avg_objective
        sys_load = (perf.cpu_load + perf.mem_load + perf.io_load + perf.net_load) / 4.0
        vol = (perf.feed_entropy + perf.sys_entropy + sys_load) / 3.0

        short_raw = base
        mid_raw = base
        long_raw = base
        vol_raw = vol

        try:
            x = self.torch.tensor([
                perf.completion_rate,
                perf.avg_objective,
                perf.feed_entropy,
                perf.sys_entropy,
                sys_load,
                float(perf.proc_count) / 512.0
            ], dtype=self.torch.float32, device=self.device).unsqueeze(0)
            with self.torch.no_grad():
                y = self.model(x)[0].cpu().numpy()
            short_raw, mid_raw, long_raw, vol_raw = y.tolist()
            vol = (vol + vol_raw) / 2.0
        except Exception:
            pass

        if self.last:
            short = self._ewma(short_raw, self.last.short_term)
            mid = self._ewma(mid_raw, self.last.mid_term)
            long = self._ewma(long_raw, self.last.long_term)
            vol_smooth = self._ewma(vol, self.last.volatility)
        else:
            short, mid, long, vol_smooth = short_raw, mid_raw, long_raw, vol

        short_unc = min(1.0, vol_smooth + perf.sys_entropy)
        mid_unc = min(1.0, vol_smooth + perf.feed_entropy)
        long_unc = min(1.0, vol_smooth + (perf.sys_entropy + perf.feed_entropy) / 2.0)

        snap = PredictionSnapshot(short, mid, long, vol_smooth, short_unc, mid_unc, long_unc)
        self.last = snap
        return snap

# ============================================================
# PREDICTION ORGAN v2 (CPU fallback)
# ============================================================

class PredictionOrganV2:
    ORGAN_NAME = "PredictionOrganV2"

    def __init__(self):
        self.last: Optional[PredictionSnapshot] = None
        self.alpha = 0.4

    def _ewma(self, new: float, old: Optional[float]) -> float:
        if old is None:
            return new
        return self.alpha * new + (1.0 - self.alpha) * old

    def compute(self, perf: PerformanceSnapshot) -> PredictionSnapshot:
        base = perf.avg_objective
        sys_load = (perf.cpu_load + perf.mem_load + perf.io_load + perf.net_load) / 4.0
        vol = (perf.feed_entropy + perf.sys_entropy + sys_load) / 3.0
        short_raw = max(0.0, min(1.0, base + 0.15 - vol * 0.3))
        mid_raw = max(0.0, min(1.0, base))
        long_raw = max(0.0, min(1.0, base - 0.15 - vol * 0.1))

        if self.last:
            short = self._ewma(short_raw, self.last.short_term)
            mid = self._ewma(mid_raw, self.last.mid_term)
            long = self._ewma(long_raw, self.last.long_term)
            vol_smooth = self._ewma(vol, self.last.volatility)
        else:
            short, mid, long, vol_smooth = short_raw, mid_raw, long_raw, vol

        short_unc = min(1.0, vol_smooth + perf.sys_entropy)
        mid_unc = min(1.0, vol_smooth + perf.feed_entropy)
        long_unc = min(1.0, vol_smooth + (perf.sys_entropy + perf.feed_entropy) / 2.0)

        snap = PredictionSnapshot(short, mid, long, vol_smooth, short_unc, mid_unc, long_unc)
        self.last = snap
        return snap

# ============================================================
# RISK ORGAN v2 (reconstructed, v6.0 style)
# ============================================================

class RiskOrganV2:
    ORGAN_NAME = "RiskOrganV2"

    @staticmethod
    def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, x))

    def compute(self, perf: PerformanceSnapshot, preds: PredictionSnapshot) -> RiskProfile:
        # Simple but expressive risk model:
        # - Higher volatility, entropy, and load -> higher risk
        # - Higher completion and objective -> lower risk
        load = (perf.cpu_load + perf.mem_load + perf.io_load + perf.net_load) / 4.0
        ent = (perf.feed_entropy + perf.sys_entropy) / 2.0

        raw_risk = (
            0.35 * preds.volatility +
            0.25 * ent +
            0.20 * load -
            0.10 * perf.completion_rate -
            0.10 * perf.avg_objective
        )
        risk_score = self._clamp(raw_risk + 0.5)  # center around 0.5 then clamp

        integrity = self._clamp(1.0 - ent * 0.7 - preds.volatility * 0.3)
        return RiskProfile(risk_score=risk_score, integrity_score=integrity)

# ============================================================
# META STATE ENGINE v2
# ============================================================

class MetaStateEngineV2:
    ORGAN_NAME = "MetaStateEngineV2"

    def __init__(self):
        pass

    def decide(
        self,
        perf: PerformanceSnapshot,
        risk: RiskProfile,
        preds: PredictionSnapshot,
    ) -> Tuple[MetaState, str]:
        r = risk.risk_score
        vol = preds.volatility

        if r < 0.4 and vol < 0.5:
            meta = MetaState(name="FLOW", aggressiveness=0.7, dampening=0.2, horizon_bias=0.0)
            stance = "FLOW"
        elif r > 0.7 or vol > 0.7:
            meta = MetaState(name="SENTINEL", aggressiveness=0.3, dampening=0.6, horizon_bias=0.2)
            stance = "SENTINEL"
        else:
            meta = MetaState(name="RECOVERY", aggressiveness=0.4, dampening=0.8, horizon_bias=-0.1)
            stance = "RECOVERY"

        return meta, stance

# ============================================================
# CONFIG / LIVE-TUNABLE PARAMETERS
# ============================================================

class LiveConfig:
    def __init__(self):
        self.lock = threading.Lock()
        self.appetite = {
            "novelty": 0.7,
            "utility": 0.5,
            "impact": 0.5,
            "curiosity": 0.8,
        }
        self.reinforcement = {
            "reward": 0.6,
            "penalty": 0.1,
            "trend": 0.2,
            "success_rate": 0.75,
        }
        self.fingerprint = {
            "exploration_bias": 0.5,
            "caution_bias": 0.2,
            "curiosity_bias": 0.7,
            "impact_bias": 0.4,
        }
        self.policies = {
            "high_risk_script": None,
            "recovery_script": None,
        }
        self.ai_os_mode = True
        self.fs_scan_paths: List[str] = []

    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "appetite": dict(self.appetite),
                "reinforcement": dict(self.reinforcement),
                "fingerprint": dict(self.fingerprint),
                "policies": dict(self.policies),
                "ai_os_mode": self.ai_os_mode,
                "fs_scan_paths": list(self.fs_scan_paths),
            }

    def update_from_dict(self, data: Dict[str, Any]):
        with self.lock:
            if "appetite" in data and isinstance(data["appetite"], dict):
                self.appetite.update({k: float(v) for k, v in data["appetite"].items() if k in self.appetite})
            if "reinforcement" in data and isinstance(data["reinforcement"], dict):
                self.reinforcement.update({k: float(v) for k, v in data["reinforcement"].items() if k in self.reinforcement})
            if "fingerprint" in data and isinstance(data["fingerprint"], dict):
                self.fingerprint.update({k: float(v) for k, v in data["fingerprint"].items() if k in self.fingerprint})
            if "policies" in data and isinstance(data["policies"], dict):
                for k, v in data["policies"].items():
                    if k in self.policies:
                        self.policies[k] = v
            if "ai_os_mode" in data:
                self.ai_os_mode = bool(data["ai_os_mode"])
            if "fs_scan_paths" in data and isinstance(data["fs_scan_paths"], list):
                self.fs_scan_paths = [str(p) for p in data["fs_scan_paths"]]

# ============================================================
# DISTRIBUTED ORGAN EXECUTION (BASIC STUB)
# ============================================================

class RemoteOrganProxy:
    def __init__(self, name: str, host: str, port: int):
        self.name = name
        self.host = host
        self.port = port

    def call(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect((self.host, self.port))
            req = json.dumps({"organ": self.name, "method": method, "payload": payload}) + "\n"
            s.sendall(req.encode("utf-8"))
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            s.close()
            line = data.decode("utf-8").strip()
            if not line:
                return {}
            return json.loads(line)
        except Exception:
            return {}

# ============================================================
# PROMETHEUS SCANNER: PROCESS / FS / NETWORK + GRAPH MAP
# ============================================================

class PrometheusScanner:
    def __init__(self, config: LiveConfig):
        self.config = config
        self.last_scan: Dict[str, Any] = {}
        self.psutil = autoloader.load("psutil", "psutil")

    def scan(self) -> Dict[str, Any]:
        scan = {
            "processes": [],
            "fs_events": [],
            "network": {},
            "graph": {
                "nodes": [],
                "edges": [],
            },
        }
        if self.psutil is None:
            self.last_scan = scan
            return scan

        try:
            procs = []
            for p in self.psutil.process_iter(attrs=["pid", "name", "cpu_percent", "memory_percent"]):
                info = p.info
                procs.append({
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "cpu": info.get("cpu_percent", 0.0),
                    "mem": info.get("memory_percent", 0.0),
                })
            scan["processes"] = procs

            net = self.psutil.net_io_counters()
            scan["network"] = {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
            }

            fs_events = []
            cfg = self.config.get_snapshot()
            for path in cfg["fs_scan_paths"]:
                try:
                    count = 0
                    for _, _, files in os.walk(path):
                        count += len(files)
                    fs_events.append({"path": path, "file_count": count})
                except Exception:
                    fs_events.append({"path": path, "file_count": None})
            scan["fs_events"] = fs_events

            nodes = []
            edges = []
            for p in procs:
                nodes.append({"id": f"proc:{p['pid']}", "type": "process", "name": p["name"]})
                if p["cpu"] > 0:
                    edges.append({"src": f"proc:{p['pid']}", "dst": "res:cpu", "weight": p["cpu"] / 100.0})
                if p["mem"] > 0:
                    edges.append({"src": f"proc:{p['pid']}", "dst": "res:mem", "weight": p["mem"] / 100.0})
            nodes.extend([
                {"id": "res:cpu", "type": "resource", "name": "CPU"},
                {"id": "res:mem", "type": "resource", "name": "Memory"},
                {"id": "res:net", "type": "resource", "name": "Network"},
            ])
            scan["graph"]["nodes"] = nodes
            scan["graph"]["edges"] = edges

        except Exception:
            pass

        self.last_scan = scan
        return scan

# ============================================================
# HYBRID BRAIN CORE + ORGAN SCHEDULER + DB HEALTH + PLUGINS + POLICIES
# ============================================================

class HybridBrainCore:
    def __init__(self):
        self.organs: Dict[str, Any] = {}
        self.remote_organs: Dict[str, RemoteOrganProxy] = {}
        self.spine = NeuralSpineBus()
        self.persistence = PersistenceLayer()
        self.timeline = CognitiveTimeline(self.persistence)
        self.config = LiveConfig()
        self.scanner = PrometheusScanner(self.config)

        self.preload_thread: Optional[threading.Thread] = None
        self.preload_stop = threading.Event()

        self.scheduler_thread: Optional[threading.Thread] = None
        self.scheduler_stop = threading.Event()

        self.db_health_thread: Optional[threading.Thread] = None
        self.db_health_stop = threading.Event()

        self._register_organs()
        self._load_plugins()

        self.pred_history: List[Tuple[float, float]] = []
        self.potential_history: List[float] = []

    def _register_organs(self):
        self.register_organ(HybridWeightOrgan.ORGAN_NAME, HybridWeightOrgan())
        gpu_pred = GPUPredictionOrgan()
        if gpu_pred.model is not None:
            self.register_organ(GPUPredictionOrgan.ORGAN_NAME, gpu_pred)
        self.register_organ(PredictionOrganV2.ORGAN_NAME, PredictionOrganV2())
        self.register_organ(RiskOrganV2.ORGAN_NAME, RiskOrganV2())
        self.register_organ(MetaStateEngineV2.ORGAN_NAME, MetaStateEngineV2())
        self.register_organ(SemanticOrgan.ORGAN_NAME, SemanticOrgan())

    def _load_plugins(self):
        try:
            base = os.path.abspath(__file__)
        except NameError:
            base = os.path.abspath(sys.argv[0])
        organs_dir = os.path.join(os.path.dirname(base), "organs")
        if not os.path.isdir(organs_dir):
            return
        sys.path.insert(0, organs_dir)
        for fname in os.listdir(organs_dir):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            mod_name = os.path.splitext(fname)[0]
            try:
                mod = importlib.import_module(mod_name)
                importlib.reload(mod)
            except Exception:
                continue
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type):
                    organ_name = getattr(attr, "ORGAN_NAME", None)
                    if organ_name and organ_name not in self.organs:
                        try:
                            instance = attr()
                            self.register_organ(organ_name, instance)
                        except Exception:
                            pass

    def register_organ(self, name: str, organ: Any):
        self.organs[name] = organ

    def register_remote_organ(self, name: str, host: str, port: int):
        self.remote_organs[name] = RemoteOrganProxy(name, host, port)

    def get_organ(self, name: str) -> Any:
        if name in self.organs:
            return self.organs[name]
        return self.remote_organs.get(name)

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

    def scheduler_loop(self, interval: float = 3.0):
        while not self.scheduler_stop.is_set():
            self.cognition_step()
            time.sleep(interval)

    def start_scheduler(self, interval: float = 3.0):
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            return
        self.scheduler_stop.clear()
        self.scheduler_thread = threading.Thread(
            target=self.scheduler_loop,
            args=(interval,),
            daemon=True,
        )
        self.scheduler_thread.start()

    def stop_scheduler(self):
        self.scheduler_stop.set()
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=1.0)

    def db_health_loop(self, interval: float = 600.0):
        while not self.db_health_stop.is_set():
            self.persistence.run_health_cycle(startup=False)
            time.sleep(interval)

    def start_db_health_daemon(self, interval: float = 600.0):
        if self.db_health_thread and self.db_health_thread.is_alive():
            return
        self.db_health_stop.clear()
        self.db_health_thread = threading.Thread(
            target=self.db_health_loop,
            args=(interval,),
            daemon=True,
        )
        self.db_health_thread.start()

    def stop_db_health_daemon(self):
        self.db_health_stop.set()
        if self.db_health_thread:
            self.db_health_thread.join(timeout=1.0)

    def _collect_system_metrics(self) -> Dict[str, Any]:
        psutil = autoloader.load("psutil", "psutil")
        if psutil is None:
            return {"cpu": 0.2, "mem": 0.3, "io": 0.2, "net": 0.2, "proc_count": 64}
        try:
            cpu = psutil.cpu_percent(interval=0.05) / 100.0
            mem = psutil.virtual_memory().percent / 100.0
            io_c = psutil.disk_io_counters()
            net_c = psutil.net_io_counters()
            io = min(1.0, (io_c.read_bytes + io_c.write_bytes) / (1024 * 1024 * 1024))
            net = min(1.0, (net_c.bytes_sent + net_c.bytes_recv) / (1024 * 1024 * 1024))
            proc_count = len(psutil.pids())
            return {
                "cpu": cpu,
                "mem": mem,
                "io": io,
                "net": net,
                "proc_count": proc_count,
            }
        except Exception:
            return {"cpu": 0.3, "mem": 0.3, "io": 0.3, "net": 0.3, "proc_count": 64}

    def _run_policy_scripts(self, risk: RiskProfile, meta_state: MetaState):
        cfg = self.config.get_snapshot()
        policies = cfg["policies"]
        if risk.risk_score > 0.8 and policies.get("high_risk_script"):
            try:
                subprocess.Popen(policies["high_risk_script"], shell=True)
            except Exception:
                pass
        if meta_state.name == "RECOVERY" and policies.get("recovery_script"):
            try:
                subprocess.Popen(policies["recovery_script"], shell=True)
            except Exception:
                pass

    def _ai_os_supervision(self, perf: PerformanceSnapshot, risk: RiskProfile, potential: float):
        if not self.config.ai_os_mode:
            return
        if risk.risk_score > 0.9 or potential > 0.8:
            print(f"[AI-OS] High risk/potential: risk={risk.risk_score:.3f}, "
                  f"potential={potential:.3f}, CPU={perf.cpu_load:.2f}, "
                  f"MEM={perf.mem_load:.2f}, PROCS={perf.proc_count}")

    def _compute_potential(self, perf: PerformanceSnapshot, risk: RiskProfile, preds: PredictionSnapshot) -> Tuple[float, float]:
        load = (perf.cpu_load + perf.mem_load + perf.io_load + perf.net_load) / 4.0
        potential = (
            0.4 * risk.risk_score +
            0.2 * perf.sys_entropy +
            0.2 * load +
            0.2 * preds.volatility
        )
        potential = max(0.0, min(1.0, potential))
        self.potential_history.append(potential)
        if len(self.potential_history) > 20:
            self.potential_history.pop(0)
        if len(self.potential_history) >= 2:
            gradient = self.potential_history[-1] - self.potential_history[-2]
        else:
            gradient = 0.0
        return potential, gradient

    def _ensemble_prediction(self, perf: PerformanceSnapshot) -> PredictionSnapshot:
        gpu_org: Optional[GPUPredictionOrgan] = self.get_organ(GPUPredictionOrgan.ORGAN_NAME)
        cpu_org: PredictionOrganV2 = self.get_organ(PredictionOrganV2.ORGAN_NAME)

        snaps = []
        weights = []

        if gpu_org and gpu_org.model is not None:
            try:
                s_gpu = gpu_org.compute(perf)
                snaps.append(s_gpu)
                weights.append(1.5)
            except Exception:
                pass

        try:
            s_cpu = cpu_org.compute(perf)
            snaps.append(s_cpu)
            weights.append(1.0)
        except Exception:
            pass

        if not snaps:
            return PredictionSnapshot(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)

        total_w = sum(weights)

        def wavg(attr: str) -> float:
            return sum(getattr(s, attr) * w for s, w in zip(snaps, weights)) / total_w

        short = wavg("short_term")
        mid = wavg("mid_term")
        long = wavg("long_term")
        vol = wavg("volatility")
        short_unc = wavg("short_uncertainty")
        mid_unc = wavg("mid_uncertainty")
        long_unc = wavg("long_uncertainty")

        now = time.time()
        self.pred_history.append((now, perf.avg_objective))
        if len(self.pred_history) > 50:
            self.pred_history.pop(0)

        return PredictionSnapshot(short, mid, long, vol, short_unc, mid_unc, long_unc)

    def cognition_step(self) -> OrganOutput:
        metrics = self._collect_system_metrics()
        perf = PerformanceSnapshot(
            completion_rate=0.8,
            avg_objective=0.7,
            feed_entropy=0.3,
            sys_entropy=0.2,
            cpu_load=metrics["cpu"],
            mem_load=metrics["mem"],
            io_load=metrics["io"],
            net_load=metrics["net"],
            proc_count=metrics["proc_count"],
        )

        scan_map = self.scanner.scan()

        preds = self._ensemble_prediction(perf)
        risk_org: RiskOrganV2 = self.get_organ(RiskOrganV2.ORGAN_NAME)
        meta_engine: MetaStateEngineV2 = self.get_organ(MetaStateEngineV2.ORGAN_NAME)
        weight_org: HybridWeightOrgan = self.get_organ(HybridWeightOrgan.ORGAN_NAME)
        semantic_org: SemanticOrgan = self.get_organ(SemanticOrgan.ORGAN_NAME)

        risk = risk_org.compute(perf, preds)

        for src, w in cognitive_graph.influences_for("RiskOrganV2"):
            if src in ("PredictionOrganV2", "GPUPredictionOrgan"):
                risk.risk_score = max(0.0, min(1.0, risk.risk_score + w * preds.volatility))

        meta_state, stance = meta_engine.decide(perf, risk, preds)

        potential, potential_gradient = self._compute_potential(perf, risk, preds)

        cfg = self.config.get_snapshot()
        appetite = AppetiteProfile(
            novelty_appetite=cfg["appetite"]["novelty"],
            utility_appetite=cfg["appetite"]["utility"],
            impact_appetite=cfg["appetite"]["impact"],
            curiosity_appetite=cfg["appetite"]["curiosity"],
        )
        reinforcement = ReinforcementSignal(
            reward=cfg["reinforcement"]["reward"],
            penalty=cfg["reinforcement"]["penalty"],
            trend=cfg["reinforcement"]["trend"],
            success_rate=cfg["reinforcement"]["success_rate"],
        )
        fingerprint = Fingerprint(
            exploration_bias=cfg["fingerprint"]["exploration_bias"],
            caution_bias=cfg["fingerprint"]["caution_bias"],
            curiosity_bias=cfg["fingerprint"]["curiosity_bias"],
            impact_bias=cfg["fingerprint"]["impact_bias"],
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
            potential=potential,
            potential_gradient=potential_gradient,
        )

        semantic_vec = semantic_org.compute(perf, risk, preds, meta_state)

        entropy = (perf.feed_entropy + perf.sys_entropy) / 2.0
        ts = time.time()
        self.timeline.record(
            ts,
            out.weights,
            stance,
            meta_state.name,
            risk.risk_score,
            out.stability_score,
            entropy,
            semantic_vec.zero,
            semantic_vec.one,
            semantic_vec.hybrid,
        )

        self._run_policy_scripts(risk, meta_state)
        self._ai_os_supervision(perf, risk, potential)

        out.reasoning_tail["scan_map"] = {
            "process_count": len(scan_map.get("processes", [])),
            "fs_paths": [e.get("path") for e in scan_map.get("fs_events", [])],
            "net_bytes": scan_map.get("network", {}).get("bytes_recv", 0),
        }
        out.reasoning_tail["semantic_vector"] = {
            "zero": semantic_vec.zero,
            "one": semantic_vec.one,
            "hybrid": semantic_vec.hybrid,
        }
        return out

# ============================================================
# PORTABLE BUILDERS (PyInstaller + Nuitka stubs)
# ============================================================

def build_portable_pyinstaller():
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
        print("PyInstaller build attempted. Check 'dist' directory.")
    except Exception:
        print("PyInstaller build failed or not available.")

def build_portable_nuitka():
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "nuitka"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        script_name = os.path.basename(__file__)
        subprocess.check_call(
            [sys.executable, "-m", "nuitka", "--onefile", script_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("Nuitka build attempted. Check output directory.")
    except Exception:
        print("Nuitka build failed or not available.")

# ============================================================
# TKINTER GUI + WEB GUI (Flask) + HEADLESS
# ============================================================

def launch_tk_gui(core: HybridBrainCore):
    tk_mod = autoloader.load("tkinter", "tkinter")
    if tk_mod is None or os_manager.is_headless():
        return False

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("HybridBrain v6.0 – Prometheus+Ø Scanner")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

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
        s.set(core.config.appetite[var_name])
        s.grid(row=i, column=1, sticky="ew")
        sliders[var_name] = s

    control_frame.columnconfigure(1, weight=1)

    weights_label = ttk.Label(control_frame, text="Weights: -")
    weights_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

    stance_label = ttk.Label(control_frame, text="Stance / MetaState: -")
    stance_label.grid(row=6, column=0, columnspan=2, sticky="w")

    stability_label = ttk.Label(control_frame, text="Stability: -")
    stability_label.grid(row=7, column=0, columnspan=2, sticky="w")

    potential_label = ttk.Label(control_frame, text="Potential: -")
    potential_label.grid(row=8, column=0, columnspan=2, sticky="w")

    semantic_label = ttk.Label(control_frame, text="Semantic (0/1/Ø): -")
    semantic_label.grid(row=9, column=0, columnspan=2, sticky="w")

    def step_once():
        with core.config.lock:
            core.config.appetite["novelty"] = sliders["novelty"].get()
            core.config.appetite["utility"] = sliders["utility"].get()
            core.config.appetite["impact"] = sliders["impact"].get()
            core.config.appetite["curiosity"] = sliders["curiosity"].get()

        out = core.cognition_step()
        weights_label.config(
            text=f"Weights: N={out.weights.novelty_weight:.3f}, "
                 f"U={out.weights.utility_weight:.3f}, "
                 f"I={out.weights.impact_weight:.3f}, "
                 f"C={out.weights.curiosity_weight:.3f}"
        )
        stance_label.config(text=f"Stance / MetaState: {out.reasoning_tail['stance']} / {out.reasoning_tail['meta_state']}")
        stability_label.config(text=f"Stability: {out.stability_score:.3f}")
        potential_label.config(text=f"Potential: {out.reasoning_tail.get('potential', 0.0):.3f}")
        sv = out.reasoning_tail.get("semantic_vector", {})
        semantic_label.config(
            text=f"Semantic (0/1/Ø): {sv.get('zero', 0.0):.3f} / {sv.get('one', 0.0):.3f} / {sv.get('hybrid', 0.0):.3f}"
        )

    step_button = ttk.Button(control_frame, text="Step", command=step_once)
    step_button.grid(row=4, column=0, columnspan=2, pady=(10, 0))

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
    return True

def launch_web_gui(core: HybridBrainCore, host: str = "127.0.0.1", port: int = 5000):
    flask = autoloader.load("flask", "flask")
    if flask is None:
        print("Flask not available; falling back to headless.")
        run_headless(core)
        return

    from flask import Flask, jsonify, request, Response

    app = Flask(__name__)

    @app.route("/status", methods=["GET"])
    def status():
        rows = core.persistence.get_recent_weights(limit=10)
        history = [
            {
                "ts": r[0],
                "novelty": r[1],
                "utility": r[2],
                "impact": r[3],
                "curiosity": r[4],
                "stance": r[5],
                "meta_state": r[6],
                "risk": r[7],
                "stability": r[8],
                "entropy": r[9],
                "semantic_0": r[10],
                "semantic_1": r[11],
                "semantic_hybrid": r[12],
            }
            for r in rows
        ]
        return jsonify({
            "os": os_manager.describe(),
            "history": history,
            "config": core.config.get_snapshot(),
        })

    @app.route("/step", methods=["POST"])
    def step():
        out = core.cognition_step()
        return jsonify({
            "weights": {
                "novelty": out.weights.novelty_weight,
                "utility": out.weights.utility_weight,
                "impact": out.weights.impact_weight,
                "curiosity": out.weights.curiosity_weight,
            },
            "stability": out.stability_score,
            "reasoning": out.reasoning_tail,
        })

    @app.route("/config", methods=["GET", "POST"])
    def config():
        if request.method == "GET":
            return jsonify(core.config.get_snapshot())
        else:
            try:
                data = request.get_json(force=True, silent=True) or {}
                core.config.update_from_dict(data)
                return jsonify({"status": "ok", "config": core.config.get_snapshot()})
            except Exception:
                return jsonify({"status": "error"}), 400

    @app.route("/timeline/json", methods=["GET"])
    def timeline_json():
        limit = int(request.args.get("limit", 100))
        data = core.persistence.export_timeline_json(limit=limit)
        return jsonify(data)

    @app.route("/timeline/csv", methods=["GET"])
    def timeline_csv():
        limit = int(request.args.get("limit", 100))
        rows = core.persistence.export_timeline_json(limit=limit)
        fieldnames = [
            "ts", "novelty", "utility", "impact", "curiosity",
            "stance", "meta_state", "risk", "stability", "entropy",
            "semantic_0", "semantic_1", "semantic_hybrid"
        ]
        def generate():
            output = []
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            yield "".join(output)
            for r in rows:
                output.clear()
                writer.writerow(r)
                yield "".join(output)
        return Response(generate(), mimetype="text/csv")

    @app.route("/scanmap", methods=["GET"])
    def scanmap():
        scan = core.scanner.last_scan or core.scanner.scan()
        return jsonify(scan)

    print(f"Web GUI/API running at http://{host}:{port}")
    app.run(host=host, port=port)

def run_headless(core: HybridBrainCore):
    print(os_manager.describe())
    print("Running HybridBrain v6.0 Prometheus+Ø in headless mode.")
    out = core.cognition_step()
    print(f"Weights: {out.weights}")
    print(f"Stance / MetaState: {out.reasoning_tail['stance']} / {out.reasoning_tail['meta_state']}")
    print(f"Stability: {out.stability_score:.3f}")
    print(f"Potential: {out.reasoning_tail.get('potential', 0.0):.3f}")
    sv = out.reasoning_tail.get("semantic_vector", {})
    print(f"Semantic (0/1/Ø): {sv.get('zero', 0.0):.3f} / {sv.get('one', 0.0):.3f} / {sv.get('hybrid', 0.0):.3f}")

# ============================================================
# ENTRYPOINT
# ============================================================

def main():
    core = HybridBrainCore()

    # Try Tk GUI first, then Web, then headless
    if launch_tk_gui(core):
        return

    # If Tk fails or headless OS, try web
    try:
        launch_web_gui(core)
    except Exception:
        run_headless(core)

if __name__ == "__main__":
    main()
