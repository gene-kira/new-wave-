#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BORG OS v12 – Self-Migrating Kernel-Sim Persona Borg Governor
(Threat-Aware + Adaptive Reputation + Protected Apps + Crash-Proof GUI + Browser/Apps Shield + Load Governor +
 Deep Network Anomaly + GPU-Aware + Event Bus + Personas + Persona AI Learning + Mesh Intelligence + Dashboard +
 Automatic DB Schema Migration + Kernel Simulation Hooks + Driver Integration Stubs)

Key upgrades over v11:
- Automatic SQLite schema migration (no more schema mismatch crashes)
- Kernel simulation layer (abstract hooks for future drivers)
- Driver integration stubs (clear extension points)
- Deeper persona AI (multi-signal adaptation)
- Full mesh intelligence (safe/threat intel sharing)
"""

import sys
import os
import platform
import time
import random
import threading
import sqlite3
import socket

OS_NAME = platform.system().lower()

# ============================================================
#  AUTOLOADER
# ============================================================

FASTAPI_AVAILABLE = False
TK_AVAILABLE = False
HTTPX_AVAILABLE = False
CRYPTO_AVAILABLE = False
PSUTIL_AVAILABLE = False

try:
    from fastapi import FastAPI
    from pydantic import BaseModel
    import uvicorn
    FASTAPI_AVAILABLE = True
except Exception:
    FASTAPI_AVAILABLE = False

try:
    import tkinter as tk
    from tkinter import ttk
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False

try:
    import httpx
    HTTPX_AVAILABLE = True
except Exception:
    HTTPX_AVAILABLE = False

try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except Exception:
    CRYPTO_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except Exception:
    PSUTIL_AVAILABLE = False

from enum import Enum, auto

# ============================================================
#  BORG CORE STATES + PERSONAS
# ============================================================

class BorgState(Enum):
    DOMINANT   = auto()
    SUPPRESSED = auto()
    FLAGGED    = auto()
    TRANSITION = auto()
    BORG_CORE  = auto()

class BorgPersona(Enum):
    AGGRESSIVE = auto()
    DEFENSIVE  = auto()
    PASSIVE    = auto()

class BorgCell:
    def __init__(self, state=BorgState.SUPPRESSED):
        self.state = state

class BorgMemoryOrgan:
    def __init__(self, size):
        self.cells = [BorgCell() for _ in range(size)]

    def __len__(self):
        return len(self.cells)

class BorgLogicEngine:
    def __init__(self, organ: BorgMemoryOrgan):
        self.organ = organ

    def autonomous_step(self):
        # Drift
        for cell in self.organ.cells:
            s = cell.state
            if s == BorgState.TRANSITION:
                cell.state = BorgState.FLAGGED
            elif s == BorgState.FLAGGED:
                if random.random() < 0.10:
                    cell.state = BorgState.SUPPRESSED

        # Neighbor sync
        new_states = [cell.state for cell in self.organ.cells]
        for i, cell in enumerate(self.organ.cells):
            left  = self.organ.cells[i-1].state if i > 0 else None
            right = self.organ.cells[i+1].state if i < len(self.organ.cells)-1 else None
            if cell.state == BorgState.SUPPRESSED:
                if left == BorgState.DOMINANT or right == BorgState.DOMINANT:
                    new_states[i] = BorgState.TRANSITION
        for i, s in enumerate(new_states):
            self.organ.cells[i].state = s

        # Entropy
        for cell in self.organ.cells:
            r = random.random()
            if r < 0.02:
                cell.state = BorgState.DOMINANT
            elif r < 0.04:
                cell.state = BorgState.SUPPRESSED

# ============================================================
#  EVENT BUS
# ============================================================

class BorgEventBus:
    def __init__(self, logger=None, storage=None):
        self.logger = logger or (lambda msg: None)
        self.storage = storage

    def emit(self, kind, detail, reason=""):
        msg = f"[EVENT] {kind}: {detail} ({reason})"
        self.logger(msg)
        if self.storage:
            self.storage.log_event(kind, detail, reason)

# ============================================================
#  PERSISTENT BORG MEMORY (SQLite) + AUTO MIGRATION
# ============================================================

class BorgStorage:
    def __init__(self, path="borg_memory.db"):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self._init_schema()
        self._migrate_schema()

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS borg_cells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idx INTEGER NOT NULL,
            state TEXT NOT NULL,
            tick INTEGER NOT NULL,
            ts REAL NOT NULL
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS borg_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL,
            ts REAL NOT NULL
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS borg_safe_processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            samples INTEGER NOT NULL,
            reputation REAL NOT NULL
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS borg_mesh_intel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            ts REAL NOT NULL
        )
        """)
        self.conn.commit()

    def _migrate_schema(self):
        cur = self.conn.cursor()

        # Ensure borg_events has 'reason' column
        cur.execute("PRAGMA table_info(borg_events)")
        cols = [row[1] for row in cur.fetchall()]
        if "reason" not in cols:
            cur.execute("ALTER TABLE borg_events ADD COLUMN reason TEXT DEFAULT ''")
            self.conn.commit()

        # Future migrations can be added here safely

    def save_states(self, states, tick):
        cur = self.conn.cursor()
        ts = time.time()
        for i, s in enumerate(states):
            cur.execute(
                "INSERT INTO borg_cells (idx, state, tick, ts) VALUES (?, ?, ?, ?)",
                (i, s.name, tick, ts)
            )
        self.conn.commit()

    def log_event(self, kind, detail, reason=""):
        cur = self.conn.cursor()
        ts = time.time()
        cur.execute(
            "INSERT INTO borg_events (kind, detail, ts, reason) VALUES (?, ?, ?, ?)",
            (kind, detail, ts, reason)
        )
        self.conn.commit()

    def update_safe_process(self, name, delta_rep=1.0):
        cur = self.conn.cursor()
        ts = time.time()
        cur.execute("SELECT id, samples, reputation FROM borg_safe_processes WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            pid, samples, rep = row
            new_rep = max(0.0, rep + delta_rep)
            cur.execute(
                "UPDATE borg_safe_processes SET last_seen = ?, samples = ?, reputation = ? WHERE id = ?",
                (ts, samples + 1, new_rep, pid)
            )
        else:
            cur.execute(
                "INSERT INTO borg_safe_processes (name, first_seen, last_seen, samples, reputation) VALUES (?, ?, ?, ?, ?)",
                (name, ts, ts, 1, max(0.0, delta_rep))
            )
        self.conn.commit()

    def decay_reputation(self, factor=0.99):
        cur = self.conn.cursor()
        cur.execute("SELECT id, reputation FROM borg_safe_processes")
        rows = cur.fetchall()
        for pid, rep in rows:
            new_rep = rep * factor
            cur.execute("UPDATE borg_safe_processes SET reputation = ? WHERE id = ?", (new_rep, pid))
        self.conn.commit()

    def load_safe_processes(self):
        cur = self.conn.cursor()
        cur.execute("SELECT name, samples, reputation FROM borg_safe_processes")
        rows = cur.fetchall()
        safe = {}
        for name, samples, rep in rows:
            safe[name] = (samples, rep)
        return safe

    def store_mesh_intel(self, kind, payload):
        cur = self.conn.cursor()
        ts = time.time()
        cur.execute(
            "INSERT INTO borg_mesh_intel (kind, payload, ts) VALUES (?, ?, ?)",
            (kind, payload, ts)
        )
        self.conn.commit()

    def load_mesh_intel(self, kind=None):
        cur = self.conn.cursor()
        if kind:
            cur.execute("SELECT payload FROM borg_mesh_intel WHERE kind = ?", (kind,))
        else:
            cur.execute("SELECT payload FROM borg_mesh_intel")
        rows = cur.fetchall()
        return [p for (p,) in rows]

# ============================================================
#  ENCRYPTED MESH (Fernet)
# ============================================================

if CRYPTO_AVAILABLE:
    SHARED_KEY = Fernet.generate_key()
    CIPHER = Fernet(SHARED_KEY)

    def encrypt_payload(payload: dict) -> str:
        import json
        raw = json.dumps(payload).encode("utf-8")
        return CIPHER.encrypt(raw).decode("utf-8")

    def decrypt_payload(token: str) -> dict:
        import json
        raw = CIPHER.decrypt(token.encode("utf-8"))
        return json.loads(raw.decode("utf-8"))
else:
    def encrypt_payload(payload: dict) -> str:
        import json
        return json.dumps(payload)

    def decrypt_payload(token: str) -> dict:
        import json
        return json.loads(token)

# ============================================================
#  THREAT SIGNATURES (BEHAVIORAL)
# ============================================================

class ThreatSignatureEngine:
    def __init__(self):
        self.signatures = [
            ("HIGH_CPU_MEM", lambda cpu, mem, io_r, io_w, net: cpu > 95.0 and mem > 90.0),
            ("HEAVY_IO", lambda cpu, mem, io_r, io_w, net: io_r > 200 * 1024 * 1024 or io_w > 100 * 1024 * 1024),
            ("HEAVY_NET", lambda cpu, mem, io_r, io_w, net: net > 100 * 1024 * 1024),
        ]

    def match(self, cpu, mem, io_read, io_write, net_bytes):
        matched = []
        for name, fn in self.signatures:
            try:
                if fn(cpu, mem, io_read, io_write, net_bytes):
                    matched.append(name)
            except Exception:
                continue
        return matched

# ============================================================
#  PROCESS REPUTATION + PROTECTION (ADAPTIVE)
# ============================================================

class ProcessReputation:
    def __init__(self, storage: BorgStorage | None = None):
        self.storage = storage

        self.safe_names = {
            "System", "Idle", "explorer.exe", "wininit.exe",
            "services.exe", "lsass.exe", "csrss.exe",
            "python.exe", "borg_os.py",
            "steam.exe", "Steam.exe",
            "EpicGamesLauncher.exe", "epicgameslauncher.exe",
            "Teams.exe", "teams.exe",
            "Copilot.exe", "copilot.exe",
            "ms-teams.exe",
            "chrome.exe", "Chrome.exe",
            "msedge.exe", "msedge.exe",
            "firefox.exe", "Firefox.exe",
            "brave.exe", "Brave.exe",
            "opera.exe", "Opera.exe"
        }

        self.suspicious_patterns = [
            "miner", "crypto", "bot", "rat", "hack", "inject", "cheat"
        ]

        self.learned_safe = {}
        if self.storage:
            self.learned_safe = self.storage.load_safe_processes()

        self.sig_engine = ThreatSignatureEngine()

    def is_hard_protected(self, name):
        if not name:
            return False
        if name in self.safe_names:
            return True
        n = name.lower()
        protected_keywords = [
            "steam", "epic",
            "teams", "copilot", "microsoft",
            "chrome", "edge", "firefox", "brave", "opera"
        ]
        return any(k in n for k in protected_keywords)

    def is_learned_safe(self, name):
        if not name:
            return False
        if name not in self.learned_safe:
            return False
        samples, rep = self.learned_safe[name]
        return samples >= 5 and rep >= 3.0

    def observe_safe(self, name, cpu, mem):
        if not name or not self.storage:
            return
        delta = 1.0
        if cpu < 10.0 and mem < 5.0:
            delta = 1.5
        self.storage.update_safe_process(name, delta_rep=delta)
        self.learned_safe = self.storage.load_safe_processes()

    def observe_suspicious(self, name):
        if not name or not self.storage:
            return
        self.storage.update_safe_process(name, delta_rep=-2.0)
        self.learned_safe = self.storage.load_safe_processes()

    def decay(self):
        if self.storage:
            self.storage.decay_reputation()
            self.learned_safe = self.storage.load_safe_processes()

    def score(self, name, cpu, mem, io_read, io_write, net_bytes):
        name_l = (name or "").lower()
        score = 0.0

        if self.is_hard_protected(name):
            score -= 200.0

        if self.is_learned_safe(name):
            score -= 120.0

        for pat in self.suspicious_patterns:
            if pat in name_l:
                score += 60.0

        score += cpu * 0.6
        score += mem * 0.6

        if io_read > 100 * 1024 * 1024:
            score += 25.0
        if io_write > 50 * 1024 * 1024:
            score += 25.0

        if net_bytes > 50 * 1024 * 1024:
            score += 35.0

        sigs = self.sig_engine.match(cpu, mem, io_read, io_write, net_bytes)
        if sigs:
            score += 40.0

        return score, sigs

# ============================================================
#  NETWORK SAMPLING + DEEP INSPECTION (SIMULATED)
# ============================================================

class NetworkSampler:
    def __init__(self):
        self.last_bytes = None

    def sample(self):
        if not PSUTIL_AVAILABLE:
            return 0
        try:
            io = psutil.net_io_counters()
            total = io.bytes_sent + io.bytes_recv
            if self.last_bytes is None:
                self.last_bytes = total
                return 0
            delta = total - self.last_bytes
            self.last_bytes = total
            return delta
        except Exception:
            return 0

class DeepNetworkInspector:
    def __init__(self, logger=None, event_bus=None):
        self.logger = logger or (lambda msg: None)
        self.event_bus = event_bus
        self.last_scan = 0.0

    def scan(self):
        if not PSUTIL_AVAILABLE:
            return
        now = time.time()
        if now - self.last_scan < 5.0:
            return
        self.last_scan = now

        try:
            conns = psutil.net_connections(kind='inet')
        except Exception:
            return

        suspicious = []
        for c in conns:
            try:
                raddr = c.raddr
                if not raddr:
                    continue
                ip = raddr.ip
                port = raddr.port
                if port in (4444, 5555, 6666, 1337):
                    suspicious.append(f"{ip}:{port}")
            except Exception:
                continue

        if suspicious:
            msg = f"DeepNet suspicious endpoints: {', '.join(suspicious)}"
            self.logger(f"[DEEPNET] {msg}")
            if self.event_bus:
                self.event_bus.emit("DEEP_NET_ANOMALY", msg, reason="ports")

# ============================================================
#  GPU AWARENESS + GOVERNOR (SIMULATED)
# ============================================================

def sample_gpu_load():
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        vals = [float(x) for x in out.splitlines() if x.strip()]
        if vals:
            return sum(vals) / len(vals)
        return 0.0
    except Exception:
        return 0.0

class GPUGovernor:
    def __init__(self, logger=None, event_bus=None):
        self.logger = logger or (lambda msg: None)
        self.event_bus = event_bus

    def adjust_for_gpu(self, gpu_load, persona: BorgPersona):
        if gpu_load < 10.0:
            return "idle"
        if persona == BorgPersona.AGGRESSIVE and gpu_load > 90.0:
            msg = f"GPU overload under AGGRESSIVE persona: {gpu_load:.1f}%"
            self.logger(f"[GPU] {msg}")
            if self.event_bus:
                self.event_bus.emit("GPU_OVERLOAD", msg, reason="persona")
            return "overload"
        if persona == BorgPersona.DEFENSIVE and gpu_load > 95.0:
            msg = f"GPU overload under DEFENSIVE persona: {gpu_load:.1f}%"
            self.logger(f"[GPU] {msg}")
            if self.event_bus:
                self.event_bus.emit("GPU_OVERLOAD", msg, reason="persona")
            return "overload"
        if persona == BorgPersona.PASSIVE and gpu_load > 98.0:
            msg = f"GPU overload under PASSIVE persona: {gpu_load:.1f}%"
            self.logger(f"[GPU] {msg}")
            if self.event_bus:
                self.event_bus.emit("GPU_OVERLOAD", msg, reason="persona")
            return "overload"
        return "normal"

# ============================================================
#  SYSTEM LOAD GOVERNOR (NEVER OVERLOAD)
# ============================================================

class SystemLoadGovernor:
    def __init__(self, logger=None, event_bus=None):
        self.logger = logger or (lambda msg: None)
        self.event_bus = event_bus
        self.overloaded = False
        self.last_check = 0.0

        self.cpu_high = 85.0
        self.mem_high = 80.0
        self.gpu_high = 90.0

    def check_load(self):
        if not PSUTIL_AVAILABLE:
            self.overloaded = False
            return self.overloaded

        now = time.time()
        if now - self.last_check < 1.0:
            return self.overloaded
        self.last_check = now

        try:
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory().percent
        except Exception:
            cpu = 0.0
            mem = 0.0

        gpu = sample_gpu_load()

        overloaded = (
            cpu >= self.cpu_high or
            mem >= self.mem_high or
            gpu >= self.gpu_high
        )

        if overloaded and not self.overloaded:
            msg = f"System overload: CPU={cpu:.1f} MEM={mem:.1f} GPU={gpu:.1f}"
            self.logger(f"[LOAD] {msg}")
            if self.event_bus:
                self.event_bus.emit("LOAD_OVERLOAD", msg, reason="threshold")
        if not overloaded and self.overloaded:
            msg = f"System normalized: CPU={cpu:.1f} MEM={mem:.1f} GPU={gpu:.1f}"
            self.logger(f"[LOAD] {msg}")
            if self.event_bus:
                self.event_bus.emit("LOAD_NORMAL", msg, reason="threshold")

        self.overloaded = overloaded
        return self.overloaded

# ============================================================
#  KERNEL SIMULATION + DRIVER STUBS
# ============================================================

class KernelSimulator:
    """
    High-level abstraction for future driver integration.
    Currently logs intent; real implementation would call into drivers.
    """
    def __init__(self, logger=None, event_bus=None):
        self.logger = logger or (lambda msg: None)
        self.event_bus = event_bus

    def hook_process(self, pid, name):
        msg = f"KernelSim hook process pid={pid} name={name}"
        self.logger(f"[KERNEL] {msg}")
        if self.event_bus:
            self.event_bus.emit("KERNEL_HOOK", msg, reason="sim")

    def enforce_policy(self, pid, name, action):
        msg = f"KernelSim enforce {action} on pid={pid} name={name}"
        self.logger(f"[KERNEL] {msg}")
        if self.event_bus:
            self.event_bus.emit("KERNEL_POLICY", msg, reason=action)

# ============================================================
#  PERSONA AI LEARNING (DEEPER)
# ============================================================

class PersonaLearner:
    """
    Deeper persona adaptation:
    - Tracks kills, overloads, safe observations.
    - Uses ratios to drift persona.
    """
    def __init__(self, logger=None, event_bus=None):
        self.logger = logger or (lambda msg: None)
        self.event_bus = event_bus
        self.kill_count = 0
        self.overload_count = 0
        self.safe_count = 0
        self.last_eval = time.time()

    def record_kill(self):
        self.kill_count += 1

    def record_overload(self):
        self.overload_count += 1

    def record_safe(self):
        self.safe_count += 1

    def evaluate(self, current_persona: BorgPersona):
        now = time.time()
        if now - self.last_eval < 30.0:
            return current_persona
        self.last_eval = now

        new_persona = current_persona

        if self.kill_count > 10 and self.overload_count < 3 and self.safe_count < 20:
            new_persona = BorgPersona.AGGRESSIVE
        elif self.overload_count > 5 and self.safe_count > 30:
            new_persona = BorgPersona.PASSIVE
        else:
            new_persona = BorgPersona.DEFENSIVE

        if new_persona != current_persona:
            msg = f"Persona shift: {current_persona.name} -> {new_persona.name} (kills={self.kill_count}, overloads={self.overload_count}, safe={self.safe_count})"
            self.logger(f"[PERSONA] {msg}")
            if self.event_bus:
                self.event_bus.emit("PERSONA_SHIFT", msg, reason="usage")

        self.kill_count = 0
        self.overload_count = 0
        self.safe_count = 0
        return new_persona

# ============================================================
#  OS RESOURCE GOVERNOR (ADVANCED)
# ============================================================

class BorgOSGovernor:
    def __init__(self, logger=None, storage=None, event_bus=None,
                 persona=BorgPersona.DEFENSIVE, persona_learner=None, kernel_sim=None):
        self.logger = logger or (lambda msg: None)
        self.storage = storage
        self.event_bus = event_bus
        self.reputation = ProcessReputation(storage=storage)
        self.net_sampler = NetworkSampler()
        self.last_decay = time.time()
        self.load_governor = SystemLoadGovernor(logger=self.logger, event_bus=self.event_bus)
        self.deep_net = DeepNetworkInspector(logger=self.logger, event_bus=self.event_bus)
        self.gpu_governor = GPUGovernor(logger=self.logger, event_bus=self.event_bus)
        self.persona = persona
        self.persona_learner = persona_learner or PersonaLearner(logger=self.logger, event_bus=self.event_bus)
        self.kernel_sim = kernel_sim or KernelSimulator(logger=self.logger, event_bus=self.event_bus)

    def apply(self, states):
        if not PSUTIL_AVAILABLE:
            return

        overloaded = self.load_governor.check_load()
        self.deep_net.scan()

        if overloaded:
            self.persona_learner.record_overload()

        if self.persona == BorgPersona.AGGRESSIVE:
            kill_threshold = 100.0
        elif self.persona == BorgPersona.PASSIVE:
            kill_threshold = 140.0
        else:
            kill_threshold = 110.0

        if time.time() - self.last_decay > 60.0:
            self.reputation.decay()
            self.last_decay = time.time()

        procs = psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'io_counters'])
        procs = list(procs)
        if not procs:
            return

        net_delta = self.net_sampler.sample()
        gpu_load = sample_gpu_load()
        gpu_mode = self.gpu_governor.adjust_for_gpu(gpu_load, self.persona)

        if gpu_mode == "overload":
            overloaded = True

        chunk = max(1, len(procs) // max(1, len(states)))
        for i, state in enumerate(states):
            start = i * chunk
            end = min(len(procs), start + chunk)
            for p in procs[start:end]:
                try:
                    name = p.info.get('name') or ""
                    pid = p.info.get('pid')
                    cpu = p.info.get('cpu_percent') or 0.0
                    mem = p.info.get('memory_percent') or 0.0
                    io = p.info.get('io_counters')
                    io_read = io.read_bytes if io else 0
                    io_write = io.write_bytes if io else 0

                    rep_score, sigs = self.reputation.score(name, cpu, mem, io_read, io_write, net_delta)
                    hard_protected = self.reputation.is_hard_protected(name)
                    learned_safe = self.reputation.is_learned_safe(name)

                    if cpu < 20.0 and mem < 10.0 and not hard_protected:
                        self.reputation.observe_safe(name, cpu, mem)
                        self.persona_learner.record_safe()

                    if sigs and not hard_protected and not learned_safe:
                        self.reputation.observe_suspicious(name)

                    threat = (
                        rep_score > kill_threshold or
                        (cpu > 97.0 and mem > 90.0 and sigs)
                    ) and not hard_protected and not learned_safe

                    self.kernel_sim.hook_process(pid, name)

                    if overloaded:
                        self._monitor_process(p, rep_score, hard_protected, learned_safe, sigs, overload=True)
                        continue

                    if state == BorgState.DOMINANT:
                        if not hard_protected and not learned_safe:
                            self._boost_process(p, rep_score, hard_protected, learned_safe, sigs)
                        else:
                            self._monitor_process(p, rep_score, hard_protected, learned_safe, sigs)
                    elif state == BorgState.SUPPRESSED:
                        if threat:
                            self._kill_process(p, rep_score, gpu_load, hard_protected, learned_safe, sigs)
                        elif not hard_protected and not learned_safe:
                            self._suppress_process(p, rep_score, sigs)
                        else:
                            self._monitor_process(p, rep_score, hard_protected, learned_safe, sigs)
                    elif state == BorgState.FLAGGED:
                        if threat:
                            self._kill_process(p, rep_score, gpu_load, hard_protected, learned_safe, sigs)
                        else:
                            self._monitor_process(p, rep_score, hard_protected, learned_safe, sigs)
                    elif state == BorgState.BORG_CORE:
                        self._protect_process(p, hard_protected, learned_safe, sigs)
                except Exception:
                    continue

        self.persona = self.persona_learner.evaluate(self.persona)

    def _boost_process(self, p, score, hard_protected, learned_safe, sigs):
        tag = self._tag(hard_protected, learned_safe, sigs)
        if OS_NAME == "windows":
            try:
                p.nice(psutil.HIGH_PRIORITY_CLASS)
                msg = f"[OS] Boosted {p.pid} {p.info.get('name')} | score={score:.1f} [{tag}]"
                self.logger(msg)
                if self.event_bus:
                    self.event_bus.emit("BOOST", msg, reason=tag)
                self.kernel_sim.enforce_policy(p.pid, p.info.get('name'), "boost")
            except Exception:
                pass
        else:
            try:
                p.nice(-5)
                msg = f"[OS] Boosted {p.pid} {p.info.get('name')} | score={score:.1f} [{tag}]"
                self.logger(msg)
                if self.event_bus:
                    self.event_bus.emit("BOOST", msg, reason=tag)
                self.kernel_sim.enforce_policy(p.pid, p.info.get('name'), "boost")
            except Exception:
                pass

    def _suppress_process(self, p, score, sigs):
        reason = "non-protected"
        if sigs:
            reason += f" sigs={','.join(sigs)}"
        if OS_NAME == "windows":
            try:
                p.nice(psutil.IDLE_PRIORITY_CLASS)
                msg = f"[OS] Suppressed {p.pid} {p.info.get('name')} | score={score:.1f} ({reason})"
                self.logger(msg)
                if self.event_bus:
                    self.event_bus.emit("SUPPRESS", msg, reason=reason)
                self.kernel_sim.enforce_policy(p.pid, p.info.get('name'), "suppress")
            except Exception:
                pass
        else:
            try:
                p.nice(10)
                msg = f"[OS] Suppressed {p.pid} {p.info.get('name')} | score={score:.1f} ({reason})"
                self.logger(msg)
                if self.event_bus:
                    self.event_bus.emit("SUPPRESS", msg, reason=reason)
                self.kernel_sim.enforce_policy(p.pid, p.info.get('name'), "suppress")
            except Exception:
                pass

    def _kill_process(self, p, score, gpu_load, hard_protected, learned_safe, sigs):
        try:
            name = p.info.get('name')
            tag = self._tag(hard_protected, learned_safe, sigs)
            sig_str = ",".join(sigs) if sigs else "none"
            reason = f"threat score={score:.1f}, gpu={gpu_load:.1f}, sigs={sig_str}, {tag}"
            msg = f"[OS] KILL THREAT {p.pid} {name} | {reason}"
            self.logger(msg)
            if self.event_bus:
                self.event_bus.emit("KILL", msg, reason=reason)
            self.kernel_sim.enforce_policy(p.pid, name, "kill")
            p.terminate()
            self.persona_learner.record_kill()
        except Exception:
            pass

    def _monitor_process(self, p, score, hard_protected, learned_safe, sigs, overload=False):
        name = p.info.get('name')
        tag = self._tag(hard_protected, learned_safe, sigs)
        sig_str = ",".join(sigs) if sigs else "none"
        mode = "SAFE-LOAD" if overload else "NORMAL"
        msg = f"[OS] Monitor {p.pid} {name} | score={score:.1f} sigs={sig_str} [{tag}] mode={mode}"
        self.logger(msg)
        if self.event_bus:
            self.event_bus.emit("MONITOR", msg, reason=f"{tag}/{mode}")

    def _protect_process(self, p, hard_protected, learned_safe, sigs):
        name = p.info.get('name')
        tag = self._tag(hard_protected, learned_safe, sigs)
        msg = f"[OS] Protect {p.pid} {name} [{tag}]"
        self.logger(msg)
        if self.event_bus:
            self.event_bus.emit("PROTECT", msg, reason=tag)

    def _tag(self, hard_protected, learned_safe, sigs):
        base = []
        if hard_protected:
            base.append("HARD_PROTECTED")
        if learned_safe:
            base.append("LEARNED_SAFE")
        if sigs:
            base.append("SIG_MATCH")
        if not base:
            return "NORMAL"
        return "+".join(base)

# ============================================================
#  BORG CORE
# ============================================================

class BorgCore:
    def __init__(self, size=10, logger=None, storage=None, governor=None, event_bus=None, persona=BorgPersona.DEFENSIVE):
        self.organ = BorgMemoryOrgan(size=size)
        self.engine = BorgLogicEngine(self.organ)
        self.logger = logger or (lambda msg: None)
        self.storage = storage
        self.event_bus = event_bus
        self.governor = governor
        self.tick_counter = 0
        self.watchdog = None
        self.persona = persona

    def attach_watchdog(self, watchdog):
        self.watchdog = watchdog

    def tick(self):
        self.engine.autonomous_step()
        self.tick_counter += 1

        states = self.get_states()

        if self.storage:
            self.storage.save_states(states, self.tick_counter)

        if self.governor:
            self.governor.apply(states)
            self.persona = self.governor.persona

        msg = f"BorgCore tick {self.tick_counter} persona={self.persona.name}"
        self.logger(f"[HEARTBEAT] {msg}")
        if self.event_bus:
            self.event_bus.emit("HEARTBEAT", msg, reason="tick")

        if self.watchdog:
            self.watchdog.notify_tick()

    def get_states(self):
        return [cell.state for cell in self.organ.cells]

    def set_state(self, index: int, state: BorgState):
        if 0 <= index < len(self.organ.cells):
            self.organ.cells[index].state = state
            msg = f"Cell {index} forced to {state.name}"
            self.logger(f"[SET] {msg}")
            if self.event_bus:
                self.event_bus.emit("SET_STATE", msg, reason="manual")

# ============================================================
#  WATCHDOG
# ============================================================

class BorgWatchdog:
    def __init__(self, core: BorgCore, timeout=5.0, event_bus=None):
        self.core = core
        self.timeout = timeout
        self.last_tick = time.time()
        self.running = True
        self.event_bus = event_bus
        t = threading.Thread(target=self.loop, daemon=True)
        t.start()

    def notify_tick(self):
        self.last_tick = time.time()

    def loop(self):
        while self.running:
            if time.time() - self.last_tick > self.timeout:
                msg = "Tick stall detected, resetting BorgCore"
                self.core.logger(f"[WATCHDOG] {msg}")
                if self.event_bus:
                    self.event_bus.emit("WATCHDOG_RESET", msg, reason="stall")
                size = len(self.core.organ.cells)
                self.core.organ = BorgMemoryOrgan(size=size)
                self.core.engine = BorgLogicEngine(self.core.organ)
                self.last_tick = time.time()
            time.sleep(1.0)

# ============================================================
#  GLOBAL CORE INSTANCE + RUNNER
# ============================================================

storage = BorgStorage("borg_memory.db")
event_bus = BorgEventBus(logger=lambda msg: print(msg), storage=storage)
persona_learner = PersonaLearner(logger=lambda msg: print(msg), event_bus=event_bus)
kernel_sim = KernelSimulator(logger=lambda msg: print(msg), event_bus=event_bus)
governor = BorgOSGovernor(logger=lambda msg: print(msg), storage=storage, event_bus=event_bus,
                          persona=BorgPersona.DEFENSIVE, persona_learner=persona_learner, kernel_sim=kernel_sim)
core = BorgCore(size=10, logger=lambda msg: print(msg), storage=storage, governor=governor,
                event_bus=event_bus, persona=BorgPersona.DEFENSIVE)
watchdog = BorgWatchdog(core, event_bus=event_bus)
core.attach_watchdog(watchdog)

class BorgRunner:
    def __init__(self, core: BorgCore, interval=0.5):
        self.core = core
        self.interval = interval
        self.running = True
        t = threading.Thread(target=self.loop, daemon=True)
        t.start()

    def loop(self):
        while self.running:
            self.core.tick()
            time.sleep(self.interval)

runner = BorgRunner(core, interval=0.5)

# ============================================================
#  SWARM CLUSTER CONFIG + MESH INTEL
# ============================================================

PEERS = []  # manually add peers if desired

def discover_peers(port=8000, timeout=0.2):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        msg = b"BORG_DISCOVERY_V12"
        sock.sendto(msg, ("255.255.255.255", port))
        sock.close()
    except Exception:
        pass

def broadcast_mesh_intel():
    if not HTTPX_AVAILABLE:
        return
    safe_payloads = storage.load_mesh_intel("SAFE")
    threat_payloads = storage.load_mesh_intel("THREAT")
    payload = {
        "safe": safe_payloads,
        "threat": threat_payloads,
    }
    token = encrypt_payload(payload)
    for peer in PEERS:
        try:
            httpx.post(f"{peer}/mesh/intel", json={"token": token}, timeout=1.0)
        except Exception:
            continue

# ============================================================
#  FASTAPI SERVICE
# ============================================================

if FASTAPI_AVAILABLE:
    app = FastAPI(title="Borg OS Node", version="12.0.0")

    class SyncPushRequest(BaseModel):
        token: str

    class MeshIntelRequest(BaseModel):
        token: str

    @app.get("/states")
    def get_states():
        states = core.get_states()
        return {
            "states": [s.name for s in states],
            "os": OS_NAME,
            "tick": core.tick_counter,
            "persona": core.persona.name,
        }

    @app.get("/metrics")
    def get_metrics():
        if not PSUTIL_AVAILABLE:
            return {"error": "psutil not available"}
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory().percent
        gpu = sample_gpu_load()
        return {
            "cpu": cpu,
            "mem": mem,
            "gpu": gpu,
            "tick": core.tick_counter,
            "persona": core.persona.name,
        }

    @app.get("/processes")
    def get_processes():
        if not PSUTIL_AVAILABLE:
            return {"error": "psutil not available"}
        procs_info = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                procs_info.append({
                    "pid": p.info.get('pid'),
                    "name": p.info.get('name'),
                    "cpu": p.info.get('cpu_percent'),
                    "mem": p.info.get('memory_percent'),
                })
            except Exception:
                continue
        return {"processes": procs_info, "count": len(procs_info)}

    @app.post("/sync/push")
    def sync_push():
        if not HTTPX_AVAILABLE:
            return {"error": "httpx not available"}
        states = core.get_states()
        payload = {"states": [s.name for s in states], "tick": core.tick_counter}
        token = encrypt_payload(payload)
        for peer in PEERS:
            try:
                httpx.post(f"{peer}/sync/pull", json={"token": token}, timeout=1.0)
            except Exception:
                pass
        return {"status": "ok"}

    @app.post("/sync/pull")
    def sync_pull(req: SyncPushRequest):
        payload = decrypt_payload(req.token)
        names = payload.get("states", [])
        for i, name in enumerate(names):
            try:
                state = BorgState[name]
                core.set_state(i, state)
            except KeyError:
                continue
        return {"status": "ok"}

    @app.post("/mesh/intel")
    def mesh_intel(req: MeshIntelRequest):
        payload = decrypt_payload(req.token)
        safe = payload.get("safe", [])
        threat = payload.get("threat", [])
        for s in safe:
            storage.store_mesh_intel("SAFE", s)
        for t in threat:
            storage.store_mesh_intel("THREAT", t)
        return {"status": "ok"}

# ============================================================
#  TKINTER DASHBOARD GUI
# ============================================================

class BorgGUI:
    def __init__(self, root, core: BorgCore):
        self.root = root
        self.core = core

        root.title("BORG OS NODE v12 – Dashboard (Threat-Aware + Protected Apps + Load-Aware + Personas + Mesh)")
        root.configure(bg="#111111")

        self.cell_frames = []
        self.cell_labels = []

        self.build_ui()
        self.safe_refresh()

    def build_ui(self):
        frame_cells = tk.Frame(self.root, bg="#111111")
        frame_cells.pack(padx=20, pady=10)

        for i in range(len(self.core.organ.cells)):
            f = tk.Frame(frame_cells, width=60, height=60, bg="#1a1a1a",
                         highlightthickness=2, highlightbackground="#333333")
            f.grid(row=0, column=i, padx=5)
            lbl = tk.Label(f, text="", bg="#1a1a1a", fg="#ffffff", font=("Consolas", 18))
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            self.cell_frames.append(f)
            self.cell_labels.append(lbl)

        frame_info = tk.Frame(self.root, bg="#111111")
        frame_info.pack(padx=20, pady=5, fill="x")

        self.info_label = tk.Label(frame_info, text="", bg="#111111", fg="#00ff66", font=("Consolas", 12))
        self.info_label.pack(side="left", padx=5)

        self.persona_label = tk.Label(frame_info, text="", bg="#111111", fg="#ffaa00", font=("Consolas", 12))
        self.persona_label.pack(side="right", padx=5)

        frame_metrics = tk.Frame(self.root, bg="#111111")
        frame_metrics.pack(padx=20, pady=5, fill="x")

        self.cpu_var = tk.StringVar(value="CPU: ?")
        self.mem_var = tk.StringVar(value="MEM: ?")
        self.gpu_var = tk.StringVar(value="GPU: ?")

        tk.Label(frame_metrics, textvariable=self.cpu_var, bg="#111111", fg="#00ff66", font=("Consolas", 11)).pack(side="left", padx=5)
        tk.Label(frame_metrics, textvariable=self.mem_var, bg="#111111", fg="#00ff66", font=("Consolas", 11)).pack(side="left", padx=5)
        tk.Label(frame_metrics, textvariable=self.gpu_var, bg="#111111", fg="#00ff66", font=("Consolas", 11)).pack(side="left", padx=5)

        frame_persona = tk.Frame(self.root, bg="#111111")
        frame_persona.pack(padx=20, pady=5, fill="x")

        tk.Label(frame_persona, text="Persona:", bg="#111111", fg="#ffffff", font=("Consolas", 11)).pack(side="left", padx=5)
        self.persona_combo = ttk.Combobox(frame_persona, values=[p.name for p in BorgPersona], state="readonly")
        self.persona_combo.set(self.core.persona.name)
        self.persona_combo.pack(side="left", padx=5)
        self.persona_combo.bind("<<ComboboxSelected>>", self.on_persona_change)

    def on_persona_change(self, event):
        name = self.persona_combo.get()
        try:
            new_persona = BorgPersona[name]
            core.persona = new_persona
            governor.persona = new_persona
        except KeyError:
            pass

    def safe_refresh(self):
        try:
            self.update_display()
        except Exception:
            pass
        self.root.after(250, self.safe_refresh)

    def update_display(self):
        glyphs = {
            BorgState.DOMINANT:   "🟩",
            BorgState.SUPPRESSED: "⬛",
            BorgState.FLAGGED:    "🟧",
            BorgState.TRANSITION: "🟪",
            BorgState.BORG_CORE:  "🟦",
        }

        states = core.get_states()

        for i, f in enumerate(self.cell_frames):
            state = states[i]

            if state == BorgState.DOMINANT:
                color = "#00ff66"
            elif state == BorgState.SUPPRESSED:
                color = "#444444"
            elif state == BorgState.FLAGGED:
                color = "#ffaa00"
            elif state == BorgState.TRANSITION:
                color = "#ff00ff"
            elif state == BorgState.BORG_CORE:
                color = "#00aaff"
            else:
                color = "#222222"

            f.configure(bg=color)
            self.cell_labels[i].configure(text=glyphs[state], bg=color)

        self.info_label.configure(
            text=f"Tick: {core.tick_counter} | OS: {OS_NAME}"
        )
        self.persona_label.configure(
            text=f"Persona: {core.persona.name}"
        )

        if PSUTIL_AVAILABLE:
            cpu = psutil.cpu_percent(interval=0.0)
            mem = psutil.virtual_memory().percent
            gpu = sample_gpu_load()
            self.cpu_var.set(f"CPU: {cpu:.1f}%")
            self.mem_var.set(f"MEM: {mem:.1f}%")
            self.gpu_var.set(f"GPU: {gpu:.1f}%")

# ============================================================
#  CLI FALLBACK
# ============================================================

def run_cli():
    print("BORG OS NODE (CLI MODE, Autonomous v12, Threat-Aware + Protected Apps + Load-Aware + Personas + Mesh)")
    print(f"OS: {OS_NAME}")
    print("Press Ctrl+C to exit.\n")

    try:
        while True:
            states = core.get_states()
            line = " ".join(s.name[0] for s in states)
            print(f"STATE: {line} | TICK: {core.tick_counter} | Persona: {core.persona.name}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nCLI Borg node terminated.")

# ============================================================
#  MAIN LAUNCHER
# ============================================================

def main():
    discover_peers()

    if FASTAPI_AVAILABLE and "--api" in sys.argv:
        uvicorn.run(__name__ + ":app", host="0.0.0.0", port=8000, log_level="info")
        return

    if TK_AVAILABLE and "--cli" not in sys.argv:
        root = tk.Tk()
        gui = BorgGUI(root, core)
        root.mainloop()
        return

    run_cli()

if __name__ == "__main__":
    main()
