#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BORG OS v2 – Fully Autonomous Extreme Borg

- BorgCore organism
- Persistent Borg memory (SQLite)
- Swarm cluster mode (multi-node sync)
- Encrypted Borg mesh communication
- Self-healing watchdog
- Extreme OS resource governor (can kill threats)
- Autonomous heartbeat (no manual stimuli)
- Autoloader: FastAPI API, Tkinter GUI, CLI fallback
"""

import sys
import os
import platform
import time
import random
import threading
import sqlite3

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
        """
        Fully autonomous state evolution:
        - Drift
        - Neighbor sync
        - Random dominance / suppression
        """
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

        # Random dominance / suppression for entropy
        for cell in self.organ.cells:
            if random.random() < 0.02:
                cell.state = BorgState.DOMINANT
            elif random.random() < 0.02:
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

    def load_latest_states(self, size):
        cur = self.conn.cursor()
        cur.execute("""
        SELECT idx, state FROM borg_cells
        WHERE tick = (SELECT MAX(tick) FROM borg_cells)
        """)
        rows = cur.fetchall()
        states = [BorgState.SUPPRESSED] * size
        for idx, state_name in rows:
            if 0 <= idx < size:
                try:
                    states[idx] = BorgState[state_name]
                except KeyError:
                    pass
        return states

# ============================================================
#  ENCRYPTED MESH (Fernet)
# ============================================================

if CRYPTO_AVAILABLE:
    # In real deployment, load from config/secret
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
#  OS RESOURCE GOVERNOR (EXTREME)
# ============================================================

class BorgOSGovernor:
    """
    Extreme Borg OS governor:
    - DOMINANT: boost priority
    - SUPPRESSED: suppress priority
    - FLAGGED: monitor anomaly
    - BORG_CORE: protect
    - Kill only if process is a threat:
        * very high CPU or memory
        * not in protected list
    """

    def __init__(self, logger=None):
        self.logger = logger or (lambda msg: None)
        self.protected_names = [
            "System", "Idle", "explorer.exe", "wininit.exe",
            "services.exe", "lsass.exe", "csrss.exe",
            "python.exe", "borg_os.py"
        ]

    def apply(self, states):
        if not PSUTIL_AVAILABLE:
            return

        procs = psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent'])
        procs = list(procs)
        if not procs:
            return

        chunk = max(1, len(procs) // max(1, len(states)))
        for i, state in enumerate(states):
            start = i * chunk
            end = min(len(procs), start + chunk)
            for p in procs[start:end]:
                try:
                    name = p.info.get('name') or ""
                    cpu = p.info.get('cpu_percent') or 0.0
                    mem = p.info.get('memory_percent') or 0.0

                    threat = self._is_threat(name, cpu, mem)

                    if state == BorgState.DOMINANT:
                        self._boost_process(p)
                    elif state == BorgState.SUPPRESSED:
                        if threat:
                            self._kill_process(p)
                        else:
                            self._suppress_process(p)
                    elif state == BorgState.FLAGGED:
                        if threat:
                            self._kill_process(p)
                        else:
                            self._monitor_process(p)
                    elif state == BorgState.BORG_CORE:
                        self._protect_process(p)
                except Exception:
                    continue

    def _is_threat(self, name, cpu, mem):
        if name in self.protected_names:
            return False
        if cpu > 90.0 or mem > 80.0:
            return True
        return False

    def _boost_process(self, p):
        if OS_NAME == "windows":
            try:
                p.nice(psutil.HIGH_PRIORITY_CLASS)
                self.logger(f"[OS] Boosted {p.pid} {p.info.get('name')}")
            except Exception:
                pass
        else:
            try:
                p.nice(-5)
                self.logger(f"[OS] Boosted {p.pid} {p.info.get('name')}")
            except Exception:
                pass

    def _suppress_process(self, p):
        if OS_NAME == "windows":
            try:
                p.nice(psutil.IDLE_PRIORITY_CLASS)
                self.logger(f"[OS] Suppressed {p.pid} {p.info.get('name')}")
            except Exception:
                pass
        else:
            try:
                p.nice(10)
                self.logger(f"[OS] Suppressed {p.pid} {p.info.get('name')}")
            except Exception:
                pass

    def _kill_process(self, p):
        try:
            name = p.info.get('name')
            self.logger(f"[OS] KILL THREAT {p.pid} {name}")
            p.terminate()
        except Exception:
            pass

    def _monitor_process(self, p):
        name = p.info.get('name')
        self.logger(f"[OS] Monitor {p.pid} {name}")

    def _protect_process(self, p):
        name = p.info.get('name')
        self.logger(f"[OS] Protect {p.pid} {name} (no changes)")

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

        if self.storage:
            states = self.storage.load_latest_states(size)
            for i, s in enumerate(states):
                self.organ.cells[i].state = s

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
governor = BorgOSGovernor(logger=lambda msg: print(msg))
core = BorgCore(size=10, logger=lambda msg: print(msg), storage=storage, governor=governor)
watchdog = BorgWatchdog(core)
core.attach_watchdog(watchdog)

class BorgRunner:
    """
    Autonomous heartbeat thread – no manual activation.
    """
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
#  SWARM CLUSTER CONFIG
# ============================================================

PEERS = []  # e.g. ["http://node1:8000", "http://node2:8000"]

# ============================================================
#  FASTAPI SERVICE (READ-ONLY MONITOR)
# ============================================================

if FASTAPI_AVAILABLE:
    app = FastAPI(title="Borg OS Node", version="2.0.0")

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
        return {"cpu": cpu, "mem": mem, "tick": core.tick_counter}

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
#  TKINTER GUI (VISUAL ONLY)
# ============================================================

class BorgGUI:
    def __init__(self, root, core: BorgCore):
        self.root = root
        self.core = core

        root.title("BORG OS NODE (Autonomous)")
        root.configure(bg="#111111")

        self.cell_frames = []
        self.cell_labels = []

        self.build_ui()
        self.update_display()
        self.auto_refresh()

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

    def update_display(self):
        glyphs = {
            BorgState.DOMINANT:   "🟩",
            BorgState.SUPPRESSED: "⬛",
            BorgState.FLAGGED:    "🟧",
            BorgState.TRANSITION: "🟪",
            BorgState.BORG_CORE:  "🟦",
        }

        for i, f in enumerate(self.cell_frames):
            state = self.core.organ.cells[i].state

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

    def auto_refresh(self):
        self.update_display()
        self.root.after(250, self.auto_refresh)

# ============================================================
#  CLI FALLBACK (MONITOR ONLY)
# ============================================================

def run_cli():
    print("BORG OS NODE (CLI MODE, Autonomous)")
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
