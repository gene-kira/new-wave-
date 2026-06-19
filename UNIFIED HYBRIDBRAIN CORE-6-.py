"""
HYBRIDBRAIN v4.2 – Monolithic, OS-Adaptive, Headless/GUI/Web, Portable-Ready
with:
- Silent + Automatic DB Health System (Startup + Interval)
- Live-tunable appetites / reinforcement / fingerprints via Web API
- Real system metrics (CPU/RAM/IO/Network) feeding risk/prediction
- Simple plugin loader for external organs (/organs directory)
- Timeline export (JSON/CSV)
- Simple policy engine reacting to risk/meta-state (external scripts)

Drop into hybridbrain_v4_2.py and run with Python 3.x on any OS.
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
from datetime import datetime
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
# PERSISTENCE LAYER (SQLite) + DB HEALTH ENGINE + COGNITIVE TIMELINE v2
# ============================================================

class PersistenceLayer:
    CURRENT_SCHEMA_VERSION = 2

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
                entropy REAL
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
    ):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO weight_history
            (ts, novelty, utility, impact, curiosity, stance, meta_state, risk, stability, entropy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ))
        conn.commit()
        conn.close()

    def get_recent_weights(self, limit: int = 50) -> List[Tuple]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT ts, novelty, utility, impact, curiosity,
                   stance, meta_state, risk, stability, entropy
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
            }
            for r in rows
        ]

    def export_timeline_csv(self, path: str, limit: int = 100):
        rows = self.get_recent_weights(limit=limit)
        fieldnames = [
            "ts", "novelty", "utility", "impact", "curiosity",
            "stance", "meta_state", "risk", "stability", "entropy"
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
    ):
        self.persistence.log_weights(ts, weights, stance, meta_state, risk, stability, entropy)

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
            "cpu_load": perf.cpu_load,
            "mem_load": perf.mem_load,
            "io_load": perf.io_load,
            "net_load": perf.net_load,
            "reinforcement_reward": reinforcement.reward,
            "reinforcement_penalty": reinforcement.penalty,
            "reinforcement_trend": reinforcement.trend,
            "success_rate": reinforcement.success_rate,
        }
        return OrganOutput(w, stability, confidence_delta, reasoning_tail)


dependency_map.register(
    HybridWeightOrgan.ORGAN_NAME,
    [("numpy", "numpy")]
)

# ============================================================
# PREDICTION ORGAN v2 (with system metrics influence)
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

        snap = PredictionSnapshot(short, mid, long, vol_smooth)
        self.last = snap
        return snap

# ============================================================
# RISK ORGAN v2 (with system metrics influence)
# ============================================================

class RiskOrganV2:
    ORGAN_NAME = "RiskOrganV2"

    def __init__(self):
        self.last_risk: Optional[float] = None

    def compute(self, perf: PerformanceSnapshot, preds: PredictionSnapshot) -> RiskProfile:
        entropy = (perf.feed_entropy + perf.sys_entropy) / 2.0
        sys_load = (perf.cpu_load + perf.mem_load + perf.io_load + perf.net_load) / 4.0
        instability = preds.volatility
        base_risk = max(0.0, min(1.0, 0.4 * entropy + 0.3 * instability + 0.3 * sys_load))
        if self.last_risk is not None:
            delta = base_risk - self.last_risk
            base_risk = max(0.0, min(1.0, base_risk + 0.3 * delta))
        self.last_risk = base_risk
        integrity = max(0.0, min(1.0, 1.0 - perf.sys_entropy))
        return RiskProfile(base_risk, integrity)

# ============================================================
# META-STATE ENGINE v2
# ============================================================

class MetaStateEngineV2:
    ORGAN_NAME = "MetaStateEngineV2"

    def __init__(self):
        self.last_state: Optional[str] = None

    def decide(self, perf: PerformanceSnapshot, risk: RiskProfile, preds: PredictionSnapshot) -> Tuple[MetaState, str]:
        high_risk = risk.risk_score > 0.6
        low_perf = perf.completion_rate < 0.4 or perf.avg_objective < 0.4
        stable = preds.volatility < 0.3

        if high_risk:
            ms = MetaState("SENTINEL", aggressiveness=0.7, dampening=0.3, horizon_bias=0.2)
            stance = "SENTINEL"
        elif low_perf and not high_risk:
            ms = MetaState("RECOVERY", aggressiveness=0.2, dampening=0.8, horizon_bias=0.0)
            stance = "RECOVERY"
        elif stable:
            ms = MetaState("FLOW", aggressiveness=0.5, dampening=0.3, horizon_bias=-0.2)
            stance = "FLOW"
        else:
            ms = MetaState("FLOW", aggressiveness=0.6, dampening=0.4, horizon_bias=0.0)
            stance = "FLOW"

        self.last_state = ms.name
        return ms, stance

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

    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "appetite": dict(self.appetite),
                "reinforcement": dict(self.reinforcement),
                "fingerprint": dict(self.fingerprint),
                "policies": dict(self.policies),
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

# ============================================================
# HYBRID BRAIN CORE + ORGAN SCHEDULER + DB HEALTH + PLUGINS + POLICIES
# ============================================================

class HybridBrainCore:
    def __init__(self):
        self.organs: Dict[str, Any] = {}
        self.spine = NeuralSpineBus()
        self.persistence = PersistenceLayer()
        self.timeline = CognitiveTimeline(self.persistence)
        self.config = LiveConfig()

        self.preload_thread: Optional[threading.Thread] = None
        self.preload_stop = threading.Event()

        self.scheduler_thread: Optional[threading.Thread] = None
        self.scheduler_stop = threading.Event()

        self.db_health_thread: Optional[threading.Thread] = None
        self.db_health_stop = threading.Event()

        self._register_organs()
        self._load_plugins()

    def _register_organs(self):
        self.register_organ(HybridWeightOrgan.ORGAN_NAME, HybridWeightOrgan())
        self.register_organ(PredictionOrganV2.ORGAN_NAME, PredictionOrganV2())
        self.register_organ(RiskOrganV2.ORGAN_NAME, RiskOrganV2())
        self.register_organ(MetaStateEngineV2.ORGAN_NAME, MetaStateEngineV2())

    def _load_plugins(self):
        organs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "organs")
        if not os.path.isdir(organs_dir):
            return
        sys.path.insert(0, organs_dir)
        for fname in os.listdir(organs_dir):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            mod_name = os.path.splitext(fname)[0]
            try:
                mod = importlib.import_module(mod_name)
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

    def _collect_system_metrics(self) -> Dict[str, float]:
        psutil = autoloader.load("psutil", "psutil")
        if psutil is None:
            return {"cpu": 0.2, "mem": 0.3, "io": 0.2, "net": 0.2}
        try:
            cpu = psutil.cpu_percent(interval=0.05) / 100.0
            mem = psutil.virtual_memory().percent / 100.0
            io_c = psutil.disk_io_counters()
            net_c = psutil.net_io_counters()
            io = min(1.0, (io_c.read_bytes + io_c.write_bytes) / (1024 * 1024 * 1024))
            net = min(1.0, (net_c.bytes_sent + net_c.bytes_recv) / (1024 * 1024 * 1024))
            return {
                "cpu": cpu,
                "mem": mem,
                "io": io,
                "net": net,
            }
        except Exception:
            return {"cpu": 0.3, "mem": 0.3, "io": 0.3, "net": 0.3}

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
        )
        pred_org: PredictionOrganV2 = self.get_organ(PredictionOrganV2.ORGAN_NAME)
        risk_org: RiskOrganV2 = self.get_organ(RiskOrganV2.ORGAN_NAME)
        meta_engine: MetaStateEngineV2 = self.get_organ(MetaStateEngineV2.ORGAN_NAME)
        weight_org: HybridWeightOrgan = self.get_organ(HybridWeightOrgan.ORGAN_NAME)

        preds = pred_org.compute(perf)
        risk = risk_org.compute(perf, preds)
        meta_state, stance = meta_engine.decide(perf, risk, preds)

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
        )

        entropy = (perf.feed_entropy + perf.sys_entropy) / 2.0
        ts = time.time()
        self.timeline.record(ts, out.weights, stance, meta_state.name, risk.risk_score, out.stability_score, entropy)

        self._run_policy_scripts(risk, meta_state)
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
    root.title("HybridBrain v4.2 – Control Panel")

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
            "stance", "meta_state", "risk", "stability", "entropy"
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

    print(f"Web GUI/API running at http://{host}:{port}")
    app.run(host=host, port=port)

def run_headless(core: HybridBrainCore):
    print(os_manager.describe())
    print("Running HybridBrain v4.2 in headless mode.")
    out = core.cognition_step()
    print(f"Weights: {out.weights}")
    print(f"Stance / MetaState: {out.reasoning_tail['stance']} / {out.reasoning_tail['meta_state']}")
    print(f"Stability: {out.stability_score:.3f}")

# ============================================================
# ENTRYPOINT
# ============================================================

def main():
    core = HybridBrainCore()
    core.start_preload_daemon(interval=5.0)
    core.start_scheduler(interval=5.0)
    core.start_db_health_daemon(interval=600.0)

    if not launch_tk_gui(core):
        if os_manager.is_headless():
            run_headless(core)
        else:
            launch_web_gui(core)

    core.stop_db_health_daemon()
    core.stop_scheduler()
    core.stop_preload_daemon()


if __name__ == "__main__":
    main()
