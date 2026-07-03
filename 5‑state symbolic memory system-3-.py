#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BORG MEMORY ORGANISM - AUTOLOADER BACKBONE

- Auto-detects OS
- Tries to load FastAPI + uvicorn (service mode)
- Tries to load Tkinter (GUI mode)
- Falls back to CLI mode
"""

import sys
import platform
import time
import random

OS_NAME = platform.system().lower()

# ============================================================
#  AUTOLOADER
# ============================================================

FASTAPI_AVAILABLE = False
TK_AVAILABLE = False

# Try FastAPI stack
try:
    from fastapi import FastAPI
    from pydantic import BaseModel
    import uvicorn
    FASTAPI_AVAILABLE = True
except Exception:
    FASTAPI_AVAILABLE = False

# Try Tkinter
try:
    import tkinter as tk
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False

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

class Stimulus(Enum):
    ACTIVATE   = auto()
    SUPPRESS   = auto()
    FLAG       = auto()
    STABILIZE  = auto()
    CORE_LOCK  = auto()

class BorgLogicEngine:
    def __init__(self, organ: BorgMemoryOrgan):
        self.organ = organ

    def step_cell(self, index, stimulus: Stimulus):
        cell = self.organ.cells[index]
        s_old = cell.state
        s = s_old

        if stimulus == Stimulus.ACTIVATE:
            if s in (BorgState.SUPPRESSED, BorgState.FLAGGED, BorgState.TRANSITION):
                cell.state = BorgState.DOMINANT

        elif stimulus == Stimulus.SUPPRESS:
            if s in (BorgState.DOMINANT, BorgState.FLAGGED, BorgState.TRANSITION):
                cell.state = BorgState.SUPPRESSED

        elif stimulus == Stimulus.FLAG:
            if s == BorgState.SUPPRESSED:
                cell.state = BorgState.FLAGGED
            elif s == BorgState.DOMINANT:
                cell.state = BorgState.TRANSITION

        elif stimulus == Stimulus.STABILIZE:
            if s == BorgState.TRANSITION:
                cell.state = BorgState.FLAGGED

        elif stimulus == Stimulus.CORE_LOCK:
            if s in (BorgState.DOMINANT, BorgState.FLAGGED):
                cell.state = BorgState.BORG_CORE

        return s_old, cell.state

    def step_all(self, stimulus: Stimulus):
        transitions = []
        for i in range(len(self.organ.cells)):
            s_old, s_new = self.step_cell(i, stimulus)
            if s_old != s_new:
                transitions.append((i, s_old, s_new))
        return transitions

    def autonomous_drift(self):
        for cell in self.organ.cells:
            s = cell.state
            if s == BorgState.TRANSITION:
                cell.state = BorgState.FLAGGED
            elif s == BorgState.FLAGGED:
                if random.random() < 0.10:
                    cell.state = BorgState.SUPPRESSED

    def neighbor_sync(self):
        new_states = [cell.state for cell in self.organ.cells]
        for i, cell in enumerate(self.organ.cells):
            left  = self.organ.cells[i-1].state if i > 0 else None
            right = self.organ.cells[i+1].state if i < len(self.organ.cells)-1 else None
            if cell.state == BorgState.SUPPRESSED:
                if left == BorgState.DOMINANT or right == BorgState.DOMINANT:
                    new_states[i] = BorgState.TRANSITION
        for i, s in enumerate(new_states):
            self.organ.cells[i].state = s

class BorgCore:
    def __init__(self, size=10, logger=None):
        self.organ = BorgMemoryOrgan(size=size)
        self.engine = BorgLogicEngine(self.organ)
        self.logger = logger or (lambda msg: None)

    def apply_stimulus(self, stimulus: Stimulus):
        transitions = self.engine.step_all(stimulus)
        for i, s_old, s_new in transitions:
            self.logger(f"[STIM] Cell {i}: {s_old.name} → {s_new.name}")
            if s_old == BorgState.SUPPRESSED and s_new == BorgState.DOMINANT:
                self.logger(f"[RESURRECT] Cell {i} resurrected from SUPPRESSED to DOMINANT")

    def tick(self):
        self.engine.autonomous_drift()
        self.engine.neighbor_sync()
        self.logger("[HEARTBEAT] BorgCore tick")

    def get_states(self):
        return [cell.state for cell in self.organ.cells]

    def set_state(self, index: int, state: BorgState):
        if 0 <= index < len(self.organ.cells):
            self.organ.cells[index].state = state
            self.logger(f"[SET] Cell {index} forced to {state.name}")


core = BorgCore(size=10, logger=lambda msg: print(msg))


# ============================================================
#  FASTAPI SERVICE MODE (IF AVAILABLE)
# ============================================================

if FASTAPI_AVAILABLE:
    app = FastAPI(title="Borg Memory Organism Service", version="1.0.0")

    class StimulusRequest(BaseModel):
        stimulus: str

    class TickRequest(BaseModel):
        ticks: int = 1

    class SetStateRequest(BaseModel):
        index: int
        state: str

    @app.get("/states")
    def get_states():
        states = core.get_states()
        return {
            "states": [s.name for s in states],
            "os": OS_NAME,
        }

    @app.post("/stimulus")
    def apply_stimulus(req: StimulusRequest):
        try:
            stim = Stimulus[req.stimulus.upper()]
        except KeyError:
            return {"error": f"Unknown stimulus: {req.stimulus}"}
        core.apply_stimulus(stim)
        return {"status": "ok", "stimulus": stim.name}

    @app.post("/tick")
    def tick(req: TickRequest):
        for _ in range(req.ticks):
            core.tick()
        return {"status": "ok", "ticks": req.ticks}

    @app.post("/set_state")
    def set_state(req: SetStateRequest):
        try:
            state = BorgState[req.state.upper()]
        except KeyError:
            return {"error": f"Unknown state: {req.state}"}
        core.set_state(req.index, state)
        return {"status": "ok", "index": req.index, "state": state.name}


# ============================================================
#  TKINTER GUI MODE (IF AVAILABLE)
# ============================================================

class BorgGUI:
    def __init__(self, root, core: BorgCore):
        self.root = root
        self.core = core

        root.title("BORG MEMORY ORGANISM")
        root.configure(bg="#111111")

        self.cell_frames = []
        self.cell_labels = []

        self.build_ui()
        self.update_display()
        self.auto_tick()

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

        btn_frame = tk.Frame(self.root, bg="#111111")
        btn_frame.pack(pady=10)

        buttons = [
            ("ACTIVATE",   Stimulus.ACTIVATE),
            ("SUPPRESS",   Stimulus.SUPPRESS),
            ("FLAG",       Stimulus.FLAG),
            ("STABILIZE",  Stimulus.STABILIZE),
            ("CORE LOCK",  Stimulus.CORE_LOCK),
        ]

        for label, stim in buttons:
            b = tk.Button(
                btn_frame,
                text=label,
                command=lambda s=stim: self.apply_stimulus(s),
                bg="#222222",
                fg="#00ff66",
                activebackground="#00aa55",
                activeforeground="#000000",
                width=12,
                height=2
            )
            b.pack(side="left", padx=10)

    def apply_stimulus(self, stimulus):
        core.apply_stimulus(stimulus)
        self.update_display()

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

    def auto_tick(self):
        core.tick()
        self.update_display()
        self.root.after(250, self.auto_tick)


# ============================================================
#  CLI FALLBACK
# ============================================================

def run_cli():
    print("BORG MEMORY ORGANISM (CLI MODE)")
    print(f"OS: {OS_NAME}")
    print("Press Ctrl+C to exit.\n")

    core.logger = lambda msg: print(msg)

    try:
        while True:
            core.tick()
            states = core.get_states()
            line = " ".join(s.name[0] for s in states)
            print(f"STATE: {line}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nCLI Borg organism terminated.")


# ============================================================
#  MAIN LAUNCHER
# ============================================================

def main():
    # If FastAPI is available and explicitly requested
    if FASTAPI_AVAILABLE and "--api" in sys.argv:
        uvicorn.run("borg_autoload:app", host="0.0.0.0", port=8000, log_level="info")
        return

    # If Tkinter is available, run GUI
    if TK_AVAILABLE:
        root = tk.Tk()
        gui = BorgGUI(root, core)
        root.mainloop()
        return

    # Fallback: CLI
    run_cli()


if __name__ == "__main__":
    main()
