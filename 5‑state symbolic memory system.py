import tkinter as tk
from tkinter import ttk
from enum import Enum, auto

# ============================================================
#  BORG STATES
# ============================================================

class BorgState(Enum):
    DOMINANT   = auto()   # 1→0
    SUPPRESSED = auto()   # 0→1
    FLAGGED    = auto()   # 0✓
    TRANSITION = auto()   # ✓0
    BORG_CORE  = auto()   # 0│

STATE_TO_CODE = {
    BorgState.DOMINANT:   (0,0,0),
    BorgState.SUPPRESSED: (0,0,1),
    BorgState.FLAGGED:    (0,1,0),
    BorgState.TRANSITION: (0,1,1),
    BorgState.BORG_CORE:  (1,0,0),
}

CODE_TO_STATE = {v:k for k,v in STATE_TO_CODE.items()}


# ============================================================
#  BORG CELL
# ============================================================

class BorgCell:
    def __init__(self, state=BorgState.SUPPRESSED):
        self.state = state

    def encode(self):
        return STATE_TO_CODE[self.state]

    @classmethod
    def decode(cls, bits):
        return cls(CODE_TO_STATE[tuple(bits)])


# ============================================================
#  BORG MEMORY ORGAN
# ============================================================

class BorgMemoryOrgan:
    def __init__(self, size):
        self.cells = [BorgCell() for _ in range(size)]

    def read(self, index):
        return self.cells[index].state

    def write(self, index, state):
        self.cells[index].state = state

    def encode_row(self):
        return [cell.encode() for cell in self.cells]


# ============================================================
#  STIMULI
# ============================================================

class Stimulus(Enum):
    ACTIVATE   = auto()
    SUPPRESS   = auto()
    FLAG       = auto()
    STABILIZE  = auto()
    CORE_LOCK  = auto()


# ============================================================
#  LOGIC ENGINE
# ============================================================

class BorgLogicEngine:
    def __init__(self, organ):
        self.organ = organ

    def step_cell(self, index, stimulus):
        cell = self.organ.cells[index]
        s = cell.state

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

    def step_all(self, stimulus):
        for i in range(len(self.organ.cells)):
            self.step_cell(i, stimulus)


# ============================================================
#  TKINTER GUI
# ============================================================

class BorgGUI:
    def __init__(self, root, organ, engine):
        self.root = root
        self.organ = organ
        self.engine = engine

        root.title("BORG MEMORY ORGANISM")
        root.configure(bg="#111111")

        self.cell_frames = []
        self.build_ui()
        self.update_display()

    def build_ui(self):
        frame = tk.Frame(self.root, bg="#111111")
        frame.pack(padx=20, pady=20)

        # CELL GRID
        for i in range(len(self.organ.cells)):
            f = tk.Frame(frame, width=60, height=60, bg="#1a1a1a", highlightthickness=2)
            f.grid(row=0, column=i, padx=5)
            self.cell_frames.append(f)

        # BUTTONS
        btn_frame = tk.Frame(self.root, bg="#111111")
        btn_frame.pack(pady=20)

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
        self.engine.step_all(stimulus)
        self.update_display()

    def update_display(self):
        for i, f in enumerate(self.cell_frames):
            state = self.organ.cells[i].state

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

            f.configure(bg=color)


# ============================================================
#  MAIN
# ============================================================

if __name__ == "__main__":
    organ = BorgMemoryOrgan(size=10)
    engine = BorgLogicEngine(organ)

    root = tk.Tk()
    gui = BorgGUI(root, organ, engine)
    root.mainloop()
