"""
HYBRIDBRAIN v6.3h – Prometheus + Ø-Layer Controller + Self-Build Swarm (Hybrid Merge)
Monolithic, OS-adaptive, distributed-ready AI kernel with:
  - Prometheus-style scanner (process/fs/net + graph + anomaly score)
  - Ø-layer semantic controller with dynamic bias learning + policy-gradient ØController
  - CPU/GPU ensemble prediction (parallel-capable) with volatility-aware risk
  - Redis-backed distributed memory + SQLite cognitive timeline
  - TCP RemoteOrgan server/proxy + optional ZeroMQ swarm bus
  - Swarm topology learning + Self-Build Organ multi-node planner/executor
  - Self-Mutator with sandboxed mutation testing
  - Tkinter GUI + Flask web API + headless mode
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
import argparse
import queue
import random
import math
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple, Callable

# ============================================================
# HARD-WIRED SWARM NODE LIST (EDIT FOR YOUR CLUSTER)
# ============================================================

SWARM_NODES: List[Tuple[str, str, int]] = [
    ("node-b", "192.168.1.21", 9000),
    ("node-c", "192.168.1.22", 9000),
    ("node-d", "192.168.1.23", 9000),
    ("node-e", "192.168.1.24", 9000),
]

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
# PERSISTENCE LAYER (SQLite) + DB HEALTH + COGNITIVE TIMELINE
# + REDIS-BACKED SWARM MEMORY
# ============================================================

class PersistenceLayer:
    CURRENT_SCHEMA_VERSION = 2

    def __init__(self, db_path: str = "hybridbrain.db", redis_url: str = "redis://localhost:6379/0"):
        self.db_path = db_path
        self.backup_dir = "db_backups"
        os.makedirs(self.backup_dir, exist_ok=True)
        self._init_db()
        self._migrate_db()
        self.run_health_cycle(startup=True)

        self.redis_url = redis_url
        self.redis = autoloader.load("redis", "redis")
        self.redis_client = None
        if self.redis is not None:
            try:
                self.redis_client = self.redis.Redis.from_url(self.redis_url)
            except Exception:
                self.redis_client = None

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

    # ---------- Redis-backed swarm memory ----------

    def set_memory(self, key: str, value: Any):
        if self.redis_client is None:
            return
        try:
            self.redis_client.set(key, json.dumps(value))
            self.redis_client.publish("hybridbrain:memory", json.dumps({"key": key}))
        except Exception:
            pass

    def get_memory(self, key: str) -> Any:
        if self.redis_client is None:
            return None
        try:
            v = self.redis_client.get(key)
            if v is None:
                return None
            return json.loads(v.decode("utf-8"))
        except Exception:
            return None

    def dump_memory(self, pattern: str = "hb:*") -> Dict[str, Any]:
        out = {}
        if self.redis_client is None:
            return out
        try:
            for k in self.redis_client.scan_iter(match=pattern):
                v = self.redis_client.get(k)
                if v is None:
                    continue
                out[k.decode("utf-8")] = json.loads(v.decode("utf-8"))
        except Exception:
            pass
        return out

    def apply_memory_snapshot(self, snapshot: Dict[str, Any]):
        if self.redis_client is None:
            return
        for k, v in snapshot.items():
            try:
                self.redis_client.set(k, json.dumps(v))
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
# Ø-LAYER: SEMANTIC VECTOR + ENVELOPE
# ============================================================

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

@dataclass
class ØEnvelope:
    payload: Dict[str, Any]
    semantic: SemanticVector
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload": self.payload,
            "semantic": {
                "zero": self.semantic.zero,
                "one": self.semantic.one,
                "hybrid": self.semantic.hybrid,
            },
            "meta": dict(self.meta),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ØEnvelope":
        sem = d.get("semantic", {})
        sv = SemanticVector(
            float(sem.get("zero", 1/3)),
            float(sem.get("one", 1/3)),
            float(sem.get("hybrid", 1/3)),
        ).normalized()
        return ØEnvelope(
            payload=d.get("payload", {}) or {},
            semantic=sv,
            meta=d.get("meta", {}) or {},
        )

def make_envelope(payload: Dict[str, Any],
                  semantic: Optional[SemanticVector] = None,
                  meta: Optional[Dict[str, Any]] = None) -> ØEnvelope:
    if semantic is None:
        semantic = SemanticVector(1/3, 1/3, 1/3)
    if meta is None:
        meta = {}
    return ØEnvelope(payload=payload, semantic=semantic.normalized(), meta=meta)

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

    def gpu_centrality(self) -> Dict[str, float]:
        # Stub: simple degree-based centrality
        centrality: Dict[str, float] = {}
        for (s, d), w in self.edges.items():
            centrality[s] = centrality.get(s, 0.0) + abs(w)
            centrality[d] = centrality.get(d, 0.0) + abs(w)
        return centrality

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
# GPU-ACCELERATED PREDICTION ORGAN
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

        self.torch = t
        try:
            if self.torch.cuda.is_available():
                self.device = "cuda"
            else:
                self.device = "cpu"
        except Exception:
            self.device = "cpu"

        try:
            import torch.nn as nn

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
            self.model = m
        except Exception:
            self.model = None

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
# PREDICTION ORGAN v2 (CPU)
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
# RISK ORGAN v2
# ============================================================

class RiskOrganV2:
    ORGAN_NAME = "RiskOrganV2"

    @staticmethod
    def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, x))

    def compute(self, perf: PerformanceSnapshot, preds: PredictionSnapshot) -> RiskProfile:
        load = (perf.cpu_load + perf.mem_load + perf.io_load + perf.net_load) / 4.0
        risk = 0.4 * preds.volatility + 0.3 * perf.sys_entropy + 0.3 * load
        integrity = 1.0 - 0.5 * perf.sys_entropy
        return RiskProfile(
            risk_score=self._clamp(risk),
            integrity_score=self._clamp(integrity),
        )

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
        self.o_layer = 0.5  # Ø-layer aggressiveness

    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "appetite": dict(self.appetite),
                "reinforcement": dict(self.reinforcement),
                "fingerprint": dict(self.fingerprint),
                "policies": dict(self.policies),
                "ai_os_mode": self.ai_os_mode,
                "fs_scan_paths": list(self.fs_scan_paths),
                "o_layer": self.o_layer,
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
            if "o_layer" in data:
                self.o_layer = float(data["o_layer"])

# ============================================================
# PROMETHEUS SCANNER + REAL-TIME ANOMALY DETECTION
# ============================================================

class PrometheusScanner:
    def __init__(self, config: LiveConfig):
        self.config = config
        self.last_scan: Dict[str, Any] = {}
        self.psutil = autoloader.load("psutil", "psutil")
        self.history: List[Dict[str, float]] = []

    def _update_history(self, metrics: Dict[str, float], max_len: int = 200):
        self.history.append(metrics)
        if len(self.history) > max_len:
            self.history.pop(0)

    def _compute_anomaly_score(self, metrics: Dict[str, float]) -> float:
        if len(self.history) < 10:
            return 0.0
        keys = ["cpu", "mem", "io", "net"]
        means = {k: 0.0 for k in keys}
        vars_ = {k: 0.0 for k in keys}
        n = len(self.history)
        for h in self.history:
            for k in keys:
                means[k] += h.get(k, 0.0)
        for k in keys:
            means[k] /= n
        for h in self.history:
            for k in keys:
                diff = h.get(k, 0.0) - means[k]
                vars_[k] += diff * diff
        for k in keys:
            vars_[k] = vars_[k] / max(1, n - 1)
        score = 0.0
        for k in keys:
            std = math.sqrt(vars_[k] + 1e-9)
            z = abs(metrics.get(k, 0.0) - means[k]) / (std + 1e-9)
            score += z
        return score / len(keys)

    def scan(self) -> Dict[str, Any]:
        scan = {
            "processes": [],
            "fs_events": [],
            "network": {},
            "graph": {
                "nodes": [],
                "edges": [],
            },
            "anomaly_score": 0.0,
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

            metrics = {
                "cpu": sum(p["cpu"] for p in procs) / max(1, len(procs)),
                "mem": sum(p["mem"] for p in procs) / max(1, len(procs)),
                "io": 0.0,
                "net": float(scan["network"]["bytes_recv"] + scan["network"]["bytes_sent"]) / (1024 * 1024 * 1024),
            }
            self._update_history(metrics)
            scan["anomaly_score"] = self._compute_anomaly_score(metrics)

        except Exception:
            pass

        self.last_scan = scan
        return scan

# ============================================================
# LLM BRIDGE
# ============================================================

class LLMBridge:
    def __init__(self):
        self.provider = os.environ.get("HYBRIDBRAIN_LLM_PROVIDER", "stub").lower()
        self.api_key = os.environ.get("HYBRIDBRAIN_LLM_API_KEY", "")
        self.session = None
        self._init_client()

    def _init_client(self):
        if self.provider == "openai":
            openai = autoloader.load("openai", "openai")
            if openai and self.api_key:
                openai.api_key = self.api_key
                self.session = openai
        elif self.provider == "anthropic":
            anthropic = autoloader.load("anthropic", "anthropic")
            if anthropic and self.api_key:
                self.session = anthropic
        else:
            self.session = None

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        if self.provider == "stub" or self.session is None:
            return f"[STUB-LLM] Plan for: {prompt[:200]}..."
        try:
            if self.provider == "openai":
                resp = self.session.ChatCompletion.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                )
                return resp["choices"][0]["message"]["content"]
            elif self.provider == "anthropic":
                client = self.session.Anthropic(api_key=self.api_key)
                resp = client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text
        except Exception:
            return f"[LLM-ERROR] Fallback for: {prompt[:200]}..."
        return f"[LLM-UNKNOWN] {prompt[:200]}..."

# ============================================================
# SELF-BUILD ORGAN
# ============================================================

class SelfBuildOrgan:
    ORGAN_NAME = "SelfBuildOrgan"

    def __init__(self):
        pass

    def _phase_weight(self, sv: SemanticVector) -> Dict[str, float]:
        sv = sv.normalized()
        return {
            "idea": 0.2 + 0.4 * sv.hybrid,
            "simulate": 0.2 + 0.4 * sv.zero,
            "synthesize": 0.2 + 0.4 * sv.one,
            "archive": 0.2 + 0.2 * (sv.zero + sv.hybrid),
            "mesh": 0.2 + 0.3 * sv.hybrid,
        }

    def _base_phases(self) -> List[Dict[str, Any]]:
        return [
            {"id": "idea", "role": "IdeaGeneration", "description": "Compose and mutate candidate project ideas."},
            {"id": "simulate", "role": "EvaluationSimulation", "description": "Run physics/behavior simulations and scoring."},
            {"id": "synthesize", "role": "ProjectSynthesis", "description": "Generate concrete project artifacts (code/files)."},
            {"id": "archive", "role": "ArchiveLearning", "description": "Store results, re-ingest patterns, mutate strategies."},
            {"id": "mesh", "role": "MeshNetworking", "description": "Sync archives, broadcast ideas, merge swarm state."},
        ]

    def _default_steps_for_phase(self, phase_id: str, target: str) -> List[Dict[str, Any]]:
        if phase_id == "idea":
            return [
                {"op": "collect_metrics", "from": "HybridBrainCore", "why": "seed idea constraints"},
                {"op": "compose_idea", "tool": "LLM", "target": target},
                {"op": "mutate_idea", "tool": "LLM", "constraints": ["novelty", "utility", "impact", "curiosity"]},
            ]
        if phase_id == "simulate":
            return [
                {"op": "run_sim", "type": "physics_orbit"},
                {"op": "run_sim", "type": "energy_thermal"},
                {"op": "run_sim", "type": "ai_behavior"},
                {"op": "score", "metrics": ["novelty", "utility", "impact", "curiosity"]},
            ]
        if phase_id == "synthesize":
            return [
                {"op": "code_builder", "mode": "multi_file", "target": target},
                {"op": "file_generation", "lines": "1000+"},
            ]
        if phase_id == "archive":
            return [
                {"op": "archive_store", "where": "local_archive"},
                {"op": "re_ingest", "what": "patterns"},
                {"op": "strategy_mutation", "scope": "swarm"},
            ]
        if phase_id == "mesh":
            return [
                {"op": "mesh_connect", "protocol": "tcp/ip"},
                {"op": "broadcast_ideas", "scope": "swarm"},
                {"op": "merge_archive", "strategy": "conflict_resolve"},
            ]
        return [{"op": "noop"}]

    def plan(
        self,
        payload: Dict[str, Any],
        semantic: Optional[SemanticVector] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], SemanticVector, Dict[str, Any]]:
        if semantic is None:
            sem_dict = payload.get("semantic", {})
            semantic = SemanticVector(
                float(sem_dict.get("zero", 1/3)),
                float(sem_dict.get("one", 1/3)),
                float(sem_dict.get("hybrid", 1/3)),
            ).normalized()
        else:
            semantic = semantic.normalized()

        meta = dict(meta or {})
        target = payload.get("target", "autonomous_project")
        stance = payload.get("stance", "FLOW")
        risk = float(payload.get("risk", 0.0))

        weights = self._phase_weight(semantic)
        base_phases = self._base_phases()

        if "sub_plan" in payload and isinstance(payload["sub_plan"], list):
            phases = payload["sub_plan"]
        else:
            phases = []
            for p in base_phases:
                phases.append({
                    "id": p["id"],
                    "role": p["role"],
                    "description": p["description"],
                    "weight": weights.get(p["id"], 0.2),
                    "steps": self._default_steps_for_phase(p["id"], target),
                })

        plan = {
            "target": target,
            "stance": stance,
            "risk": risk,
            "semantic": {
                "zero": semantic.zero,
                "one": semantic.one,
                "hybrid": semantic.hybrid,
            },
            "plan": phases,
        }

        meta["selfbuild_role"] = "planner" if "sub_plan" not in payload else "executor"
        meta["timestamp"] = time.time()

        return plan, semantic, meta

# ============================================================
# SELF-MUTATOR ORGAN
# ============================================================

class SelfMutatorOrgan:
    ORGAN_NAME = "SelfMutatorOrgan"

    def __init__(self):
        self.llm = LLMBridge()

    def _read_self(self) -> str:
        try:
            path = os.path.abspath(sys.argv[0])
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def _write_variant(self, content: str, tag: str) -> str:
        base = os.path.abspath(sys.argv[0])
        root, ext = os.path.splitext(base)
        new_path = f"{root}_mutant_{tag}{ext}"
        try:
            with open(new_path, "w", encoding="utf-8") as f:
                f.write(content)
            return new_path
        except Exception:
            return ""

    def _run_selftest(self, path: str, timeout: float = 10.0) -> bool:
        try:
            proc = subprocess.Popen(
                [sys.executable, path, "--selftest"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                return False
            return proc.returncode == 0
        except Exception:
            return False

    def propose_mutation(self, reason: str = "") -> Dict[str, Any]:
        src = self._read_self()
        if not src:
            return {"ok": False, "reason": "no_source"}
        prompt = (
            "You are an AI system that proposes safe, incremental improvements to a Python AI swarm kernel.\n"
            "Given this code, propose a small mutation that improves robustness, logging, or modularity.\n"
            "Return ONLY the mutated code, no explanation.\n"
            f"Reason: {reason}\n\n"
            f"CODE:\n{src[:8000]}"
        )
        mutated = self.llm.complete(prompt, max_tokens=2048)
        tag = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self._write_variant(mutated, tag)
        if not path:
            return {"ok": False, "reason": "write_failed"}

        passed = self._run_selftest(path)
        return {
            "ok": passed,
            "path": path,
            "reason": reason,
            "selftest_passed": passed,
        }

# ============================================================
# DISTRIBUTED MEMORY MESH
# ============================================================

class DistributedMemoryMesh:
    def __init__(self, core: "HybridBrainCore", node_id: str, peers: List[Tuple[str, int]]):
        self.core = core
        self.node_id = node_id
        self.peers = peers
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def broadcast_memory_snapshot(self):
        snapshot = self.core.persistence.dump_memory(pattern="hb:*")
        payload = {
            "type": "swarm_memory_sync",
            "node_id": self.node_id,
            "memory": snapshot,
        }
        data = (json.dumps(payload) + "\n").encode("utf-8")
        for host, port in self.peers:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((host, port))
                s.sendall(data)
                s.close()
            except Exception:
                continue

    def apply_remote_snapshot(self, memory: Dict[str, Any]):
        self.core.persistence.apply_memory_snapshot(memory)

    def loop(self, interval: float = 10.0):
        while not self.stop_event.is_set():
            try:
                self.broadcast_memory_snapshot()
            except Exception:
                pass
            time.sleep(interval)

    def start(self, interval: float = 10.0):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.loop, args=(interval,), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)

# ============================================================
# REMOTE ORGAN SERVER (TCP JSON)
# ============================================================

class RemoteOrganServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9000):
        self.host = host
        self.port = port
        self.organs: Dict[str, Any] = {}
        self._stop = threading.Event()

    def register_organ(self, name: str, organ: Any):
        self.organs[name] = organ

    def _handle_client(self, conn: socket.socket, addr):
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            line = data.decode("utf-8").strip()
            if not line:
                return
            req = json.loads(line)
            organ_name = req.get("organ")
            method_name = req.get("method")
            env_dict = req.get("envelope", {})
            env = ØEnvelope.from_dict(env_dict)

            organ = self.organs.get(organ_name)
            if organ is None:
                resp_env = make_envelope({"error": f"unknown organ {organ_name}"}, env.semantic, env.meta)
            else:
                fn = getattr(organ, method_name, None)
                if not callable(fn):
                    resp_env = make_envelope({"error": f"unknown method {method_name}"}, env.semantic, env.meta)
                else:
                    try:
                        result = fn(env.payload, env.semantic, env.meta)
                        if isinstance(result, tuple) and len(result) == 3:
                            out_payload, out_sem, out_meta = result
                            resp_env = make_envelope(out_payload, out_sem, out_meta)
                        else:
                            resp_env = make_envelope(result if isinstance(result, dict) else {"result": result},
                                                     env.semantic, env.meta)
                    except Exception as e:
                        resp_env = make_envelope({"error": str(e)}, env.semantic, env.meta)

            resp_line = json.dumps({"envelope": resp_env.to_dict()}) + "\n"
            conn.sendall(resp_line.encode("utf-8"))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(16)
        while not self._stop.is_set():
            try:
                s.settimeout(1.0)
                conn, addr = s.accept()
            except socket.timeout:
                continue
            t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
            t.start()
        s.close()

    def stop(self):
        self._stop.set()

# ============================================================
# REMOTE ORGAN PROXY
# ============================================================

class RemoteOrganProxy:
    def __init__(self, name: str, host: str, port: int):
        self.name = name
        self.host = host
        self.port = port

    def call(
        self,
        method: str,
        payload: Dict[str, Any],
        semantic: Optional[SemanticVector] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], SemanticVector, Dict[str, Any]]:
        env = make_envelope(payload, semantic, meta)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((self.host, self.port))
            req = json.dumps({
                "organ": self.name,
                "method": method,
                "envelope": env.to_dict(),
            }) + "\n"
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
                return {}, env.semantic, env.meta
            resp = json.loads(line)
            resp_env = ØEnvelope.from_dict(resp.get("envelope", {}))
            return resp_env.payload, resp_env.semantic, resp_env.meta
        except Exception:
            return {}, env.semantic, env.meta

# ============================================================
# SIMPLE LOCAL SWARM BUS (in-process pub/sub)
# ============================================================

class SwarmBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self.lock = threading.Lock()

    def subscribe(self, topic: str, handler: Callable[[Dict[str, Any]], None]):
        with self.lock:
            self.subscribers.setdefault(topic, []).append(handler)

    def publish(self, topic: str, message: Dict[str, Any]):
        with self.lock:
            subs = list(self.subscribers.get(topic, []))
        for h in subs:
            try:
                h(message)
            except Exception:
                pass

# ============================================================
# ADAPTIVE SWARM TOPOLOGY
# ============================================================

class SwarmTopology:
    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.links: Dict[Tuple[str, str], Dict[str, float]] = {}
        self.lock = threading.Lock()

    def update_link(self, src: str, dst: str, latency: float, success: bool):
        with self.lock:
            key = (src, dst)
            info = self.links.get(key, {"latency_ewma": latency, "success_rate": 1.0})
            alpha = 0.3
            info["latency_ewma"] = (1 - alpha) * info["latency_ewma"] + alpha * latency
            if success:
                info["success_rate"] = (1 - alpha) * info["success_rate"] + alpha * 1.0
            else:
                info["success_rate"] = (1 - alpha) * info["success_rate"] + alpha * 0.0
            self.links[key] = info

    def best_neighbors(self, src: str, k: int = 3) -> List[str]:
        with self.lock:
            candidates = [(dst, info["success_rate"], info["latency_ewma"])
                          for (s, dst), info in self.links.items() if s == src]
        candidates.sort(key=lambda x: (-x[1], x[2]))
        return [c[0] for c in candidates[:k]]

# ============================================================
# CAUSAL REASONING ORGAN (STUB)
# ============================================================

class CausalReasonerOrgan:
    ORGAN_NAME = "CausalReasonerOrgan"

    def __init__(self):
        self.rules = [
            ("high_cpu_and_entropy", lambda perf, risk: perf.cpu_load > 0.8 and perf.sys_entropy > 0.5,
             "High CPU + entropy → elevated risk."),
            ("high_volatility", lambda perf, risk: risk.risk_score > 0.7,
             "High risk score → prefer SENTINEL stance."),
        ]

    def explain(self, perf: PerformanceSnapshot, risk: RiskProfile, stance: str) -> List[str]:
        explanations = []
        for name, cond, text in self.rules:
            try:
                if cond(perf, risk):
                    explanations.append(text)
            except Exception:
                continue
        if not explanations:
            explanations.append(f"Stance {stance} chosen based on combined risk/volatility heuristics.")
        return explanations

# ============================================================
# Ø-CONTROLLER (POLICY-GRADIENT LAYER)
# ============================================================

class ØController:
    ORGAN_NAME = "ØController"

    def __init__(self, weight_organ: HybridWeightOrgan):
        self.weight_organ = weight_organ
        self.bias = WeightVector(0.0, 0.0, 0.0, 0.0)
        self.lr = 0.05

    def _apply_bias(self, w: WeightVector) -> WeightVector:
        return WeightVector(
            w.novelty_weight + self.bias.novelty_weight,
            w.utility_weight + self.bias.utility_weight,
            w.impact_weight + self.bias.impact_weight,
            w.curiosity_weight + self.bias.curiosity_weight,
        )

    def _update_bias(self, reinforcement: ReinforcementSignal, weights: WeightVector):
        advantage = reinforcement.reward - reinforcement.penalty
        self.bias.novelty_weight += self.lr * advantage * (weights.novelty_weight - 0.25)
        self.bias.utility_weight += self.lr * advantage * (weights.utility_weight - 0.25)
        self.bias.impact_weight += self.lr * advantage * (weights.impact_weight - 0.25)
        self.bias.curiosity_weight += self.lr * advantage * (weights.curiosity_weight - 0.25)

    def adjust(
        self,
        perf: PerformanceSnapshot,
        preds: PredictionSnapshot,
        reinforcement: ReinforcementSignal,
        appetite: AppetiteProfile,
        meta_state: MetaState,
        risk: RiskProfile,
        fingerprint: Fingerprint,
        stance: str,
        previous_weights: Optional[WeightVector],
        potential: float,
        potential_gradient: float,
        o_layer: float,
    ) -> OrganOutput:
        base_out = self.weight_organ.adjust_weights(
            perf=perf,
            preds=preds,
            reinforcement=reinforcement,
            appetite=appetite,
            meta_state=meta_state,
            risk=risk,
            fingerprint=fingerprint,
            stance=stance,
            previous_weights=previous_weights,
            potential=potential,
            potential_gradient=potential_gradient,
        )
        biased_weights = self._apply_bias(base_out.weights)
        biased_weights = HybridWeightOrgan._normalize_weights(biased_weights)
        self._update_bias(reinforcement, biased_weights)

        a = max(0.0, min(1.0, o_layer))
        biased_weights.novelty_weight *= (1.0 - 0.3 * a)
        biased_weights.impact_weight *= (1.0 + 0.3 * a)
        biased_weights = HybridWeightOrgan._normalize_weights(biased_weights)

        base_out.weights = biased_weights
        base_out.reasoning_tail["o_layer"] = o_layer
        base_out.reasoning_tail["policy_bias"] = {
            "novelty": self.bias.novelty_weight,
            "utility": self.bias.utility_weight,
            "impact": self.bias.impact_weight,
            "curiosity": self.bias.curiosity_weight,
        }
        return base_out

# ============================================================
# HYBRID BRAIN CORE
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
        self.swarm_bus = SwarmBus()
        self.swarm_topology = SwarmTopology()

        self.preload_thread: Optional[threading.Thread] = None
        self.preload_stop = threading.Event()

        self.db_health_thread: Optional[threading.Thread] = None
        self.db_health_stop = threading.Event()

        self.pred_history: List[Tuple[float, float]] = []
        self.potential_history: List[float] = []

        self._register_organs()
        self._load_plugins()

    def _register_organs(self):
        hw = HybridWeightOrgan()
        self.register_organ(HybridWeightOrgan.ORGAN_NAME, hw)
        gpu_pred = GPUPredictionOrgan()
        if gpu_pred.model is not None:
            self.register_organ(GPUPredictionOrgan.ORGAN_NAME, gpu_pred)
        self.register_organ(PredictionOrganV2.ORGAN_NAME, PredictionOrganV2())
        self.register_organ(RiskOrganV2.ORGAN_NAME, RiskOrganV2())
        self.register_organ(MetaStateEngineV2.ORGAN_NAME, MetaStateEngineV2())
        self.register_organ(SelfBuildOrgan.ORGAN_NAME, SelfBuildOrgan())
        self.register_organ(SelfMutatorOrgan.ORGAN_NAME, SelfMutatorOrgan())
        self.register_organ(CausalReasonerOrgan.ORGAN_NAME, CausalReasonerOrgan())
        self.register_organ(ØController.ORGAN_NAME, ØController(hw))

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

    def _ai_os_supervision(self, perf: PerformanceSnapshot, risk: RiskProfile, potential: float, anomaly_score: float):
        if not self.config.ai_os_mode:
            return
        if risk.risk_score > 0.9 or potential > 0.8 or anomaly_score > 3.0:
            print(f"[AI-OS] High risk/potential/anomaly: risk={risk.risk_score:.3f}, "
                  f"potential={potential:.3f}, anomaly={anomaly_score:.2f}, "
                  f"CPU={perf.cpu_load:.2f}, MEM={perf.mem_load:.2f}, PROCS={perf.proc_count}")

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
        anomaly_score = scan_map.get("anomaly_score", 0.0)

        preds = self._ensemble_prediction(perf)
        risk_org: RiskOrganV2 = self.get_organ(RiskOrganV2.ORGAN_NAME)
        meta_engine: MetaStateEngineV2 = self.get_organ(MetaStateEngineV2.ORGAN_NAME)
        weight_org: HybridWeightOrgan = self.get_organ(HybridWeightOrgan.ORGAN_NAME)
        o_controller: ØController = self.get_organ(ØController.ORGAN_NAME)
        causal_org: CausalReasonerOrgan = self.get_organ(CausalReasonerOrgan.ORGAN_NAME)

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

        out = o_controller.adjust(
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
            o_layer=cfg["o_layer"],
        )

        entropy = (perf.feed_entropy + perf.sys_entropy) / 2.0
        ts = time.time()
        self.timeline.record(ts, out.weights, stance, meta_state.name, risk.risk_score, out.stability_score, entropy)

        self._run_policy_scripts(risk, meta_state)
        self._ai_os_supervision(perf, risk, potential, anomaly_score)

        out.reasoning_tail["scan_map"] = {
            "process_count": len(scan_map.get("processes", [])),
            "fs_paths": [e.get("path") for e in scan_map.get("fs_events", [])],
            "net_bytes": scan_map.get("network", {}).get("bytes_recv", 0),
            "anomaly_score": anomaly_score,
        }

        out.reasoning_tail["semantic_vector"] = {
            "zero": 0.33,
            "one": 0.33,
            "hybrid": 0.34,
        }

        out.reasoning_tail["causal_explanations"] = causal_org.explain(perf, risk, stance)
        out.reasoning_tail["graph_centrality"] = cognitive_graph.gpu_centrality()

        return out

# ============================================================
# SWARM COORDINATOR (TCP, topology-aware)
# ============================================================

class SwarmCoordinator:
    def __init__(self, core: HybridBrainCore):
        self.core = core
        self.nodes: Dict[str, RemoteOrganProxy] = {}
        self.node_id = "local"

    def add_node(self, name: str, host: str, port: int):
        self.nodes[name] = RemoteOrganProxy("SelfBuildOrgan", host, port)

    def _semantic_from_tail(self, reasoning_tail: Dict[str, Any]) -> SemanticVector:
        sv = reasoning_tail.get("semantic_vector", {"zero": 0.33, "one": 0.33, "hybrid": 0.34})
        return SemanticVector(float(sv["zero"]), float(sv["one"]), float(sv["hybrid"])).normalized()

    def generate_plan_local(self, target: str = "autonomous_project") -> Dict[str, Any]:
        out = self.core.cognition_step()
        stance = out.reasoning_tail["stance"]
        risk = float(out.reasoning_tail["risk_score"])
        sv = self._semantic_from_tail(out.reasoning_tail)

        selfbuild: SelfBuildOrgan = self.core.get_organ(SelfBuildOrgan.ORGAN_NAME)
        plan_payload, plan_sem, plan_meta = selfbuild.plan({
            "target": target,
            "stance": stance,
            "risk": risk,
            "semantic": {
                "zero": sv.zero,
                "one": sv.one,
                "hybrid": sv.hybrid,
            },
        }, sv, {"origin": "local_planner"})
        return plan_payload

    def distribute_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        phases = plan.get("plan", [])
        if not phases or not self.nodes:
            return {"error": "no phases or no nodes", "plan": plan}

        node_names = list(self.nodes.keys())
        assignments: Dict[str, List[Dict[str, Any]]] = {n: [] for n in node_names}

        for i, phase in enumerate(phases):
            node = node_names[i % len(node_names)]
            assignments[node].append(phase)

        results: Dict[str, Any] = {}
        sem_dict = plan.get("semantic", {})
        sem_vec = SemanticVector(
            float(sem_dict.get("zero", 1/3)),
            float(sem_dict.get("one", 1/3)),
            float(sem_dict.get("hybrid", 1/3)),
        ).normalized()

        for node_name, sub_phases in assignments.items():
            proxy = self.nodes[node_name]
            payload = {
                "target": plan.get("target", "autonomous_project"),
                "stance": plan.get("stance", "FLOW"),
                "risk": plan.get("risk", 0.0),
                "semantic": {
                    "zero": sem_vec.zero,
                    "one": sem_vec.one,
                    "hybrid": sem_vec.hybrid,
                },
                "sub_plan": sub_phases,
            }
            t0 = time.time()
            resp_payload, resp_sem, resp_meta = proxy.call(
                method="plan",
                payload=payload,
                semantic=sem_vec,
                meta={"node": node_name, "role": "executor"},
            )
            latency = time.time() - t0
            success = "error" not in resp_payload
            self.core.swarm_topology.update_link(self.node_id, node_name, latency, success)
            results[node_name] = {
                "request": payload,
                "response": resp_payload,
                "semantic": {
                    "zero": resp_sem.zero,
                    "one": resp_sem.one,
                    "hybrid": resp_sem.hybrid,
                },
                "meta": resp_meta,
                "latency": latency,
                "success": success,
            }

        return {
            "original_plan": plan,
            "assignments": assignments,
            "results": results,
            "best_neighbors": self.core.swarm_topology.best_neighbors(self.node_id),
        }

    def autonomous_loop(self, target: str = "autonomous_project", interval: float = 10.0):
        print("[SwarmPlanner] Autonomous swarm loop started.")
        while True:
            try:
                plan = self.generate_plan_local(target=target)
                swarm_result = self.distribute_plan(plan)

                results = swarm_result.get("results", {})
                ok_count = 0
                total = len(results)
                for node, info in results.items():
                    resp = info.get("response", {})
                    if "error" not in resp:
                        ok_count += 1
                success_ratio = (ok_count / total) if total > 0 else 0.0

                cfg = self.core.config.get_snapshot()
                delta = (success_ratio - 0.5) * 0.05
                new_reinf = dict(cfg["reinforcement"])
                new_reinf["reward"] = max(0.0, min(1.0, new_reinf["reward"] + delta))
                new_reinf["success_rate"] = max(0.0, min(1.0, new_reinf["success_rate"] * (1.0 + delta)))
                self.core.config.update_from_dict({"reinforcement": new_reinf})

                print(f"[SwarmPlanner] Plan dispatched to {total} nodes, success_ratio={success_ratio:.2f}, best_neighbors={swarm_result.get('best_neighbors')}")
            except KeyboardInterrupt:
                print("[SwarmPlanner] Stopping autonomous loop (KeyboardInterrupt).")
                break
            except Exception as e:
                print(f"[SwarmPlanner] Error in autonomous loop: {e}")
            time.sleep(interval)

# ============================================================
# TKINTER GUI + WEB API + HEADLESS
# ============================================================

def launch_tk_gui(core: HybridBrainCore):
    tk_mod = autoloader.load("tkinter", "tkinter")
    if tk_mod is None or os_manager.is_headless():
        return False

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("HybridBrain v6.3h – Universal Autopilot")

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

    ttk.Label(control_frame, text="Ø-Layer (0=conservative,1=aggressive)").grid(row=4, column=0, sticky="w")
    o_slider = tk.Scale(control_frame, from_=0.0, to=1.0, orient="horizontal", resolution=0.01)
    o_slider.set(core.config.o_layer)
    o_slider.grid(row=4, column=1, sticky="ew")

    control_frame.columnconfigure(1, weight=1)

    weights_label = ttk.Label(control_frame, text="Weights: -")
    weights_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))

    stance_label = ttk.Label(control_frame, text="Stance / MetaState: -")
    stance_label.grid(row=7, column=0, columnspan=2, sticky="w")

    stability_label = ttk.Label(control_frame, text="Stability: -")
    stability_label.grid(row=8, column=0, columnspan=2, sticky="w")

    potential_label = ttk.Label(control_frame, text="Potential: -")
    potential_label.grid(row=9, column=0, columnspan=2, sticky="w")

    anomaly_label = ttk.Label(control_frame, text="Anomaly Score: -")
    anomaly_label.grid(row=10, column=0, columnspan=2, sticky="w")

    def step_once():
        with core.config.lock:
            core.config.appetite["novelty"] = sliders["novelty"].get()
            core.config.appetite["utility"] = sliders["utility"].get()
            core.config.appetite["impact"] = sliders["impact"].get()
            core.config.appetite["curiosity"] = sliders["curiosity"].get()
            core.config.o_layer = o_slider.get()

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
        anomaly_label.config(text=f"Anomaly Score: {out.reasoning_tail['scan_map'].get('anomaly_score', 0.0):.2f}")

    step_button = ttk.Button(control_frame, text="Step", command=step_once)
    step_button.grid(row=5, column=0, columnspan=2, pady=(10, 0))

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

    from flask import Flask, jsonify, request

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

    @app.route("/scanmap", methods=["GET"])
    def scanmap():
        scan = core.scanner.last_scan or core.scanner.scan()
        return jsonify(scan)

    print(f"Web GUI/API running at http://{host}:{port}")
    app.run(host=host, port=port)

def run_headless(core: HybridBrainCore):
    print(os_manager.describe())
    print("Running HybridBrain v6.3h Universal Autopilot in headless mode.")
    out = core.cognition_step()
    print(f"Weights: {out.weights}")
    print(f"Stance / MetaState: {out.reasoning_tail['stance']} / {out.reasoning_tail['meta_state']}")
    print(f"Stability: {out.stability_score:.3f}")
    print(f"Potential: {out.reasoning_tail.get('potential', 0.0):.3f}")
    print(f"Anomaly Score: {out.reasoning_tail['scan_map'].get('anomaly_score', 0.0):.2f}")

# ============================================================
# SELFBUILD REMOTE SERVER STARTER
# ============================================================

def start_selfbuild_remote_server(core: HybridBrainCore, host: str = "0.0.0.0", port: int = 9000) -> RemoteOrganServer:
    server = RemoteOrganServer(host=host, port=port)
    server.register_organ("SelfBuildOrgan", core.get_organ(SelfBuildOrgan.ORGAN_NAME))
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    print(f"[SelfBuildRemote] SelfBuildOrgan exposed on {host}:{port}")
    return server

# ============================================================
# CLI ENTRYPOINT
# ============================================================

def parse_args(argv: List[str]) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description="HybridBrain v6.3h – Universal Autopilot")
    parser.add_argument("--swarm-planner", action="store_true", help="Run as autonomous swarm planner node")
    parser.add_argument("--swarm-executor", action="store_true", help="Run as swarm executor node (SelfBuildOrgan server only)")
    parser.add_argument("--planner-interval", type=float, default=15.0, help="Seconds between autonomous swarm plans")
    parser.add_argument("--remote-port", type=int, default=9000, help="Port for SelfBuildOrgan remote server")
    parser.add_argument("--gui", action="store_true", help="Launch Tkinter GUI if possible")
    args = parser.parse_args(argv[1:])
    return {
        "swarm_planner": args.swarm_planner,
        "swarm_executor": args.swarm_executor,
        "planner_interval": args.planner_interval,
        "remote_port": args.remote_port,
        "gui": args.gui,
    }

def main():
    args = parse_args(sys.argv)
    core = HybridBrainCore()
    core.start_preload_daemon()
    core.start_db_health_daemon()

    if args["swarm_executor"]:
        start_selfbuild_remote_server(core, host="0.0.0.0", port=args["remote_port"])
        print("[Executor] Swarm executor node running. Press Ctrl+C to stop.")
        try:
            while True:
                core.cognition_step()
                time.sleep(5.0)
        except KeyboardInterrupt:
            print("[Executor] Stopping.")
        return

    if args["swarm_planner"]:
        start_selfbuild_remote_server(core, host="0.0.0.0", port=args["remote_port"])
        coordinator = SwarmCoordinator(core)
        for name, host, port in SWARM_NODES:
            coordinator.add_node(name, host, port)
        coordinator.autonomous_loop(target="autonomous_project", interval=args["planner_interval"])
        return

    if args["gui"]:
        if launch_tk_gui(core):
            return

    try:
        launch_web_gui(core)
    except Exception:
        run_headless(core)

if __name__ == "__main__":
    main()
