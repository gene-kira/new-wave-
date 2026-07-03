#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BORG OS v9 – System-Aware Borg Governor
(Threat-Aware + Adaptive Reputation + Protected Apps + Crash-Proof GUI + Browser/Apps Shield + Load Governor)

Upgrades over v8:
- System Load Governor:
    * Monitors CPU, RAM, GPU, I/O, Network
    * Enters SAFE-LOAD mode when system is stressed
    * In SAFE-LOAD mode:
        - No kills
        - No boosts
        - No suppressions
        - Monitor/Protect only
- Adaptive heartbeat:
    * Governor respects overload and becomes passive
- Stronger protection rules:
    * Steam / Epic / Teams / Copilot / Browsers / Core Windows
    * Explicit “never kill / never suppress” shield
- More robust GUI:
    * Extra safety around window movement / resize
- Same guarantees:
    * Fully autonomous heartbeat
    * Self-healing watchdog
    * Single-file architecture
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
#  BORG CORE
# ============================================================

class BorgState(Enum):
    DOMINANT   = auto()
    SUPPRESSED = auto()
    FLAGGED    = auto()
    TRANSITION = auto()
    BORG_CORE  = auto()

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
#  PERSISTENT BORG MEMORY (SQLite)
# ============================================================

class BorgStorage:
    def __init__(self, path="borg_memory.db"):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self._init_schema()

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
            reason TEXT NOT NULL,
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
        self.conn.commit()

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
            "INSERT INTO borg_events (kind, detail, reason, ts) VALUES (?, ?, ?, ?)",
            (kind, detail, reason, ts)
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

        # Hard-protected names (never kill, never suppress)
        self.safe_names = {
            # Core Windows / system
            "System", "Idle", "explorer.exe", "wininit.exe",
            "services.exe", "lsass.exe", "csrss.exe",
            "python.exe", "borg_os.py",

            # Gaming / launchers
            "steam.exe", "Steam.exe",
            "EpicGamesLauncher.exe", "epicgameslauncher.exe",

            # Collaboration / assistants
            "Teams.exe", "teams.exe",
            "Copilot.exe", "copilot.exe",
            "ms-teams.exe",

            # Browsers (explicit)
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
#  NETWORK SAMPLING (BEST-EFFORT)
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

# ============================================================
#  GPU AWARENESS (BEST-EFFORT)
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

# ============================================================
#  SYSTEM LOAD GOVERNOR (NEVER OVERLOAD)
# ============================================================

class SystemLoadGovernor:
    def __init__(self, logger=None):
        self.logger = logger or (lambda msg: None)
        self.overloaded = False
        self.last_check = 0.0

        # Thresholds (tunable)
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
            self.logger(f"[LOAD] System overload detected: CPU={cpu:.1f} MEM={mem:.1f} GPU={gpu:.1f}")
        if not overloaded and self.overloaded:
            self.logger(f"[LOAD] System load normalized: CPU={cpu:.1f} MEM={mem:.1f} GPU={gpu:.1f}")

        self.overloaded = overloaded
        return self.overloaded

# ============================================================
#  OS RESOURCE GOVERNOR (ADVANCED, SIGNATURE + REPUTATION + LOAD-AWARE)
# ============================================================

class BorgOSGovernor:
    def __init__(self, logger=None, storage=None):
        self.logger = logger or (lambda msg: None)
        self.storage = storage
        self.reputation = ProcessReputation(storage=storage)
        self.net_sampler = NetworkSampler()
        self.last_decay = time.time()
        self.load_governor = SystemLoadGovernor(logger=self.logger)

    def apply(self, states):
        if not PSUTIL_AVAILABLE:
            return

        overloaded = self.load_governor.check_load()

        # Periodic reputation decay
        if time.time() - self.last_decay > 60.0:
            self.reputation.decay()
            self.last_decay = time.time()

        procs = psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'io_counters'])
        procs = list(procs)
        if not procs:
            return

        net_delta = self.net_sampler.sample()
        gpu_load = sample_gpu_load()

        chunk = max(1, len(procs) // max(1, len(states)))
        for i, state in enumerate(states):
            start = i * chunk
            end = min(len(procs), start + chunk)
            for p in procs[start:end]:
                try:
                    name = p.info.get('name') or ""
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

                    if sigs and not hard_protected and not learned_safe:
                        self.reputation.observe_suspicious(name)

                    threat = (
                        rep_score > 110.0 or
                        (cpu > 97.0 and mem > 90.0 and sigs)
                    ) and not hard_protected and not learned_safe

                    # If system is overloaded: NO kill/boost/suppress, only monitor/protect
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

    def _boost_process(self, p, score, hard_protected, learned_safe, sigs):
        tag = self._tag(hard_protected, learned_safe, sigs)
        if OS_NAME == "windows":
            try:
                p.nice(psutil.HIGH_PRIORITY_CLASS)
                msg = f"[OS] Boosted {p.pid} {p.info.get('name')} | score={score:.1f} [{tag}]"
                self.logger(msg)
                if self.storage:
                    self.storage.log_event("BOOST", msg, reason=tag)
            except Exception:
                pass
        else:
            try:
                p.nice(-5)
                msg = f"[OS] Boosted {p.pid} {p.info.get('name')} | score={score:.1f} [{tag}]"
                self.logger(msg)
                if self.storage:
                    self.storage.log_event("BOOST", msg, reason=tag)
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
                if self.storage:
                    self.storage.log_event("SUPPRESS", msg, reason=reason)
            except Exception:
                pass
        else:
            try:
                p.nice(10)
                msg = f"[OS] Suppressed {p.pid} {p.info.get('name')} | score={score:.1f} ({reason})"
                self.logger(msg)
                if self.storage:
                    self.storage.log_event("SUPPRESS", msg, reason=reason)
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
            if self.storage:
                self.storage.log_event("KILL", msg, reason=reason)
            p.terminate()
        except Exception:
            pass

    def _monitor_process(self, p, score, hard_protected, learned_safe, sigs, overload=False):
        name = p.info.get('name')
        tag = self._tag(hard_protected, learned_safe, sigs)
        sig_str = ",".join(sigs) if sigs else "none"
        mode = "SAFE-LOAD" if overload else "NORMAL"
        msg = f"[OS] Monitor {p.pid} {name} | score={score:.1f} sigs={sig_str} [{tag}] mode={mode}"
        self.logger(msg)
        if self.storage:
            self.storage.log_event("MONITOR", msg, reason=f"{tag}/{mode}")

    def _protect_process(self, p, hard_protected, learned_safe, sigs):
        name = p.info.get('name')
        tag = self._tag(hard_protected, learned_safe, sigs)
        msg = f"[OS] Protect {p.pid} {name} [{tag}]"
        self.logger(msg)
        if self.storage:
            self.storage.log_event("PROTECT", msg, reason=tag)

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
#  BORG CORE WITH ALL UPGRADES
# ============================================================

class BorgCore:
    def __init__(self, size=10, logger=None, storage=None, governor=None):
        self.organ = BorgMemoryOrgan(size=size)
        self.engine = BorgLogicEngine(self.organ)
        self.logger = logger or (lambda msg: None)
        self.storage = storage
        self.governor = governor
        self.tick_counter = 0
        self.watchdog = None

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

        self.logger(f"[HEARTBEAT] BorgCore tick {self.tick_counter}")

        if self.watchdog:
            self.watchdog.notify_tick()

    def get_states(self):
        return [cell.state for cell in self.organ.cells]

    def set_state(self, index: int, state: BorgState):
        if 0 <= index < len(self.organ.cells):
            self.organ.cells[index].state = state
            self.logger(f"[SET] Cell {index} forced to {state.name}")

# ============================================================
#  WATCHDOG
# ============================================================

class BorgWatchdog:
    def __init__(self, core: BorgCore, timeout=5.0):
        self.core = core
        self.timeout = timeout
        self.last_tick = time.time()
        self.running = True
        t = threading.Thread(target=self.loop, daemon=True)
        t.start()

    def notify_tick(self):
        self.last_tick = time.time()

    def loop(self):
        while self.running:
            if time.time() - self.last_tick > self.timeout:
                self.core.logger("[WATCHDOG] Tick stall detected, resetting BorgCore")
                size = len(self.core.organ.cells)
                self.core.organ = BorgMemoryOrgan(size=size)
                self.core.engine = BorgLogicEngine(self.core.organ)
                self.last_tick = time.time()
            time.sleep(1.0)

# ============================================================
#  GLOBAL CORE INSTANCE + RUNNER
# ============================================================

storage = BorgStorage("borg_memory.db")
governor = BorgOSGovernor(logger=lambda msg: print(msg), storage=storage)
core = BorgCore(size=10, logger=lambda msg: print(msg), storage=storage, governor=governor)
watchdog = BorgWatchdog(core)
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
#  SWARM CLUSTER CONFIG + DISCOVERY
# ============================================================

PEERS = []  # manually add peers if desired

def discover_peers(port=8000, timeout=0.2):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        msg = b"BORG_DISCOVERY"
        sock.sendto(msg, ("255.255.255.255", port))
        sock.close()
    except Exception:
        pass

# ============================================================
#  FASTAPI SERVICE (MONITOR + SYNC + PROCESS SNAPSHOT)
# ============================================================

if FASTAPI_AVAILABLE:
    app = FastAPI(title="Borg OS Node", version="9.0.0")

    class SyncPushRequest(BaseModel):
        token: str

    @app.get("/states")
    def get_states():
        states = core.get_states()
        return {
            "states": [s.name for s in states],
            "os": OS_NAME,
            "tick": core.tick_counter,
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

# ============================================================
#  TKINTER GUI (CRASH-PROOF, MAIN-THREAD ONLY)
# ============================================================

class BorgGUI:
    def __init__(self, root, core: BorgCore):
        self.root = root
        self.core = core

        root.title("BORG OS NODE (Autonomous v9, Threat-Aware + Protected Apps + Browsers + Load-Aware)")
        root.configure(bg="#111111")

        self.cell_frames = []
        self.cell_labels = []

        self.build_ui()
        self.safe_refresh()

    def build_ui(self):
        frame = tk.Frame(self.root, bg="#111111")
        frame.pack(padx=20, pady=20)

        for i in range(len(self.core.organ.cells)):
            f = tk.Frame(frame, width=60, height=60, bg="#1a1a1a",
                         highlightthickness=2, highlightbackground="#333333")
            f.grid(row=0, column=i, padx=5)
            lbl = tk.Label(f, text="", bg="#1a1a1a", fg="#ffffff", font=("Consolas", 18))
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            self.cell_frames.append(f)
            self.cell_labels.append(lbl)

        self.info_label = tk.Label(self.root, text="", bg="#111111", fg="#00ff66", font=("Consolas", 12))
        self.info_label.pack(pady=10)

    def safe_refresh(self):
        try:
            self.update_display()
        except Exception:
            # GUI safety: ignore transient errors (e.g., during move/resize)
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

        self.info_label.configure(text=f"Tick: {core.tick_counter} | OS: {OS_NAME}")

# ============================================================
#  CLI FALLBACK (MONITOR ONLY)
# ============================================================

def run_cli():
    print("BORG OS NODE (CLI MODE, Autonomous v9, Threat-Aware + Protected Apps + Browsers + Load-Aware)")
    print(f"OS: {OS_NAME}")
    print("Press Ctrl+C to exit.\n")

    try:
        while True:
            states = core.get_states()
            line = " ".join(s.name[0] for s in states)
            print(f"STATE: {line} | TICK: {core.tick_counter}")
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
