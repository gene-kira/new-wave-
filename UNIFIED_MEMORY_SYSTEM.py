#!/usr/bin/env python3
"""
UNIFIED_MEMORY_SYSTEM.py

Single-file unified memory engine with:

- UnifiedMemoryManager + UnifiedBuffer
- RAM + VRAM telemetry with ASCII bars (terminal)
- Tkinter GUI dashboard
- Tkinter real-time graphs (RAM + VRAM)
- Automatic GPU/CPU migration policies + simulated VRAM paging
- GPU kernel execution (Numba CUDA)
- Memory fragmentation visualizer (terminal + GUI)
- Logging + history graph (CSV + matplotlib)
- Multi-GPU support (NVIDIA via nvidia-smi)

Runs on: Windows, Linux, macOS
Backends: CPU (NumPy), CUDA unified (Numba+CUDA)
"""

import sys
import time
import threading
import traceback
import subprocess
import csv
from datetime import datetime

# -----------------------------
# Soft imports
# -----------------------------

def _try_import(name, alias=None):
    try:
        module = __import__(name)
        if alias:
            globals()[alias] = module
        else:
            globals()[name] = module
        return True
    except ImportError:
        return False

# Required
if not _try_import("numpy", alias="np"):
    raise ImportError("NumPy required: pip install numpy")

# Optional
_try_import("psutil")
_psutil_available = "psutil" in globals()
if _psutil_available:
    import psutil

_numba_available = _try_import("numba")
_cupy_available = _try_import("cupy", alias="cp")

_cuda_available = False
if _numba_available:
    try:
        from numba import cuda
        _cuda_available = cuda.is_available()
    except Exception:
        _cuda_available = False

_matplotlib_available = _try_import("matplotlib")
if _matplotlib_available:
    import matplotlib.pyplot as plt

_tk_available = _try_import("tkinter", alias="tk")

# -----------------------------
# Backend info
# -----------------------------

class BackendInfo:
    def __init__(self):
        self.numpy = True
        self.numba = _numba_available
        self.cupy = _cupy_available
        self.cuda = _cuda_available

    def best_mode(self):
        if self.cuda and self.numba:
            return "cuda_unified"
        return "cpu"

    def summary(self):
        return {
            "numpy": self.numpy,
            "numba": self.numba,
            "cupy": self.cupy,
            "cuda": self.cuda,
            "default_mode": self.best_mode(),
        }

BACKEND = BackendInfo()

# -----------------------------
# GPU telemetry (multi-GPU)
# -----------------------------

def query_nvidia_gpus():
    gpus = []
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=index,memory.total,memory.used",
            "--format=csv,noheader,nounits"
        ]
        out = subprocess.check_output(cmd).decode().strip()
        if not out:
            return gpus
        for line in out.splitlines():
            idx, total, used = [x.strip() for x in line.split(",")]
            gpus.append({
                "index": int(idx),
                "mem_total": int(total),
                "mem_used": int(used),
            })
    except Exception:
        pass
    return gpus

# -----------------------------
# UnifiedBuffer
# -----------------------------

class UnifiedBuffer:
    def __init__(self, size, dtype=np.float32, mode=None, tag=None):
        self.size = int(size)
        self.dtype = dtype
        self.tag = tag or "default"

        if mode is None:
            mode = BACKEND.best_mode()
        if mode not in ("cpu", "cuda_unified"):
            raise ValueError(f"Unsupported mode: {mode}")
        self.mode = mode

        self._cpu_view = None
        self._gpu_view = None

        if self.mode == "cpu":
            self._init_cpu()
        elif self.mode == "cuda_unified":
            self._init_cuda_unified()

    def _init_cpu(self):
        self._cpu_view = np.zeros(self.size, dtype=self.dtype)

    def _init_cuda_unified(self):
        if not (BACKEND.cuda and BACKEND.numba):
            raise RuntimeError("CUDA unified requested but not available.")
        from numba import cuda
        self._gpu_view = cuda.managed_array(self.size, dtype=self.dtype)
        self._cpu_view = self._gpu_view

    @property
    def cpu(self):
        return self._cpu_view

    @property
    def gpu(self):
        if self.mode != "cuda_unified":
            raise RuntimeError("GPU view only valid in cuda_unified mode.")
        return self._gpu_view

    def to_numpy(self, copy=True):
        if copy:
            return np.array(self.cpu, copy=True)
        return self.cpu

    def fill(self, value):
        self.cpu[...] = value

    def fill_sequential(self, start=0):
        self.cpu[...] = np.arange(start, start + self.size, dtype=self.dtype)

    def bytes(self):
        return self.size * np.dtype(self.dtype).itemsize

# -----------------------------
# GPU kernel execution (example)
# -----------------------------

if BACKEND.cuda and BACKEND.numba:
    from numba import cuda

    @cuda.jit
    def add_scalar_kernel(arr, value):
        i = cuda.grid(1)
        if i < arr.size:
            arr[i] += value

    def run_gpu_add_scalar(buf: UnifiedBuffer, value: float):
        if buf.mode != "cuda_unified":
            return
        threads_per_block = 256
        blocks = (buf.size + threads_per_block - 1) // threads_per_block
        add_scalar_kernel[blocks, threads_per_block](buf.gpu, value)
        cuda.synchronize()

# -----------------------------
# Policy engine (GPU/CPU + VRAM paging)
# -----------------------------

class PolicyEngine:
    def __init__(self, manager):
        self.manager = manager
        self.vram_limit_mb = None  # optional soft limit

    def estimate_vram_usage_mb(self):
        gpus = query_nvidia_gpus()
        if not gpus:
            return 0
        return sum(g["mem_used"] for g in gpus)

    def apply_policies(self):
        # Tag-based migration
        for buf in list(self.manager.buffers):
            if buf.tag == "gpu_hot" and buf.mode == "cpu" and BACKEND.cuda:
                new_buf = UnifiedBuffer(buf.size, buf.dtype, mode="cuda_unified", tag=buf.tag)
                new_buf.cpu[...] = buf.cpu[...]
                self.manager.replace_buffer(buf, new_buf)

            elif buf.tag == "cpu_hot" and buf.mode == "cuda_unified":
                new_buf = UnifiedBuffer(buf.size, buf.dtype, mode="cpu", tag=buf.tag)
                new_buf.cpu[...] = buf.cpu[...]
                self.manager.replace_buffer(buf, new_buf)

        # VRAM paging: if VRAM limit set and exceeded, move some gpu_hot buffers back to CPU
        if self.vram_limit_mb is not None and BACKEND.cuda:
            current_vram = self.estimate_vram_usage_mb()
            if current_vram > self.vram_limit_mb:
                # page out largest gpu_hot buffers first
                gpu_hot_buffers = [b for b in self.manager.buffers if b.mode == "cuda_unified" and b.tag == "gpu_hot"]
                gpu_hot_buffers.sort(key=lambda b: b.bytes(), reverse=True)
                for buf in gpu_hot_buffers:
                    if current_vram <= self.vram_limit_mb:
                        break
                    new_buf = UnifiedBuffer(buf.size, buf.dtype, mode="cpu", tag="cpu_hot")
                    new_buf.cpu[...] = buf.cpu[...]
                    self.manager.replace_buffer(buf, new_buf)
                    current_vram = self.estimate_vram_usage_mb()

# -----------------------------
# UnifiedMemoryManager
# -----------------------------

class UnifiedMemoryManager:
    def __init__(self):
        self.backend = BACKEND
        self.buffers = []
        self.policy_engine = PolicyEngine(self)

    def backend_summary(self):
        return self.backend.summary()

    def create_buffer(self, size, dtype=np.float32, mode=None, tag=None):
        buf = UnifiedBuffer(size=size, dtype=dtype, mode=mode, tag=tag)
        self.buffers.append(buf)
        return buf

    def list_buffers(self):
        return list(self.buffers)

    def clear_buffers(self):
        self.buffers.clear()

    def total_bytes(self):
        return sum(b.bytes() for b in self.buffers)

    def replace_buffer(self, old, new):
        idx = self.buffers.index(old)
        self.buffers[idx] = new

    def apply_policies(self):
        self.policy_engine.apply_policies()

    def fragmentation_view(self):
        # Simple fragmentation visualization: list buffers by size
        buf_info = sorted(self.buffers, key=lambda b: b.bytes(), reverse=True)
        lines = []
        for i, b in enumerate(buf_info):
            mb = b.bytes() / (1024**2)
            lines.append(f"{i:02d}: {mb:8.2f} MB | mode={b.mode} | tag={b.tag}")
        return "\n".join(lines)

# -----------------------------
# Logging + history
# -----------------------------

class MemoryLogger:
    def __init__(self, filename="memory_history.csv"):
        self.filename = filename
        self._init_file()

    def _init_file(self):
        with open(self.filename, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "ram_used_mb", "ram_total_mb",
                        "gpu_index", "gpu_used_mb", "gpu_total_mb"])

    def log_snapshot(self, ram_used_mb, ram_total_mb, gpu_stats):
        ts = datetime.utcnow().isoformat()
        with open(self.filename, "a", newline="") as f:
            w = csv.writer(f)
            if not gpu_stats:
                w.writerow([ts, ram_used_mb, ram_total_mb, "", "", ""])
            else:
                for g in gpu_stats:
                    w.writerow([ts, ram_used_mb, ram_total_mb,
                                g["index"], g["mem_used"], g["mem_total"]])

    def plot_history(self):
        if not _matplotlib_available:
            print("matplotlib not available")
            return

        ram_used = []
        gpu_used = []

        with open(self.filename, "r") as f:
            r = csv.DictReader(f)
            for row in r:
                ram_used.append(float(row["ram_used_mb"]))
                if row["gpu_used_mb"]:
                    gpu_used.append(float(row["gpu_used_mb"]))

        plt.figure(figsize=(10, 5))
        plt.plot(ram_used, label="RAM used (MB)")
        if gpu_used:
            plt.plot(gpu_used, label="GPU used (MB)")
        plt.legend()
        plt.title("Memory usage history")
        plt.xlabel("Samples")
        plt.ylabel("MB")
        plt.tight_layout()
        plt.show()

# -----------------------------
# Telemetry + ASCII bars
# -----------------------------

def ascii_bar(used, total, width=40):
    if total <= 0:
        return "[" + "-" * width + "]"
    ratio = used / total
    filled = int(ratio * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"

def get_ram_stats_mb():
    if not _psutil_available:
        return None, None
    vm = psutil.virtual_memory()
    return vm.used // (1024**2), vm.total // (1024**2)

def print_ascii_telemetry(manager: UnifiedMemoryManager):
    ram_used, ram_total = get_ram_stats_mb()
    gpus = query_nvidia_gpus()

    print("=" * 70)
    print("UNIFIED MEMORY LIVE STATUS")
    if ram_used is not None:
        print(f"System RAM: {ram_used} MB / {ram_total} MB {ascii_bar(ram_used, ram_total)}")
    else:
        print("System RAM: psutil not available")

    if gpus:
        for g in gpus:
            print(f"GPU {g['index']} VRAM: {g['mem_used']} MB / {g['mem_total']} MB "
                  f"{ascii_bar(g['mem_used'], g['mem_total'])}")
    else:
        print("GPU VRAM: No NVIDIA GPUs detected")

    total_bytes = manager.total_bytes()
    print(f"Unified buffers total: {total_bytes / (1024**2):.2f} MB across {len(manager.buffers)} buffers")
    print("Fragmentation view:")
    print(manager.fragmentation_view())
    print("=" * 70)

# -----------------------------
# Tkinter GUI dashboard + graphs
# -----------------------------

class MemoryDashboard:
    def __init__(self, manager: UnifiedMemoryManager, logger: MemoryLogger = None):
        if not _tk_available:
            raise RuntimeError("Tkinter not available.")
        self.manager = manager
        self.logger = logger
        self.root = tk.Tk()
        self.root.title("Unified Memory Dashboard")

        self.ram_label = tk.Label(self.root, text="RAM: ", font=("Consolas", 12))
        self.ram_label.pack(pady=5)

        self.gpu_labels = []

        self.buffers_label = tk.Label(self.root, text="Buffers: ", font=("Consolas", 12))
        self.buffers_label.pack(pady=5)

        # Fragmentation text
        self.frag_text = tk.Text(self.root, height=10, width=60, font=("Consolas", 9))
        self.frag_text.pack(pady=5)

        # Simple real-time graph using Canvas
        self.graph_canvas = tk.Canvas(self.root, width=400, height=150, bg="black")
        self.graph_canvas.pack(pady=5)
        self.ram_history = []
        self.vram_history = []

        self.update_interval_ms = 1000
        self.root.after(self.update_interval_ms, self.update)

    def update_graph(self, ram_used, vram_used):
        self.ram_history.append(ram_used or 0)
        self.vram_history.append(vram_used or 0)
        if len(self.ram_history) > 100:
            self.ram_history = self.ram_history[-100:]
            self.vram_history = self.vram_history[-100:]

        self.graph_canvas.delete("all")
        w = 400
        h = 150
        max_val = max(self.ram_history + self.vram_history + [1])

        # RAM line (green)
        for i in range(1, len(self.ram_history)):
            x1 = (i - 1) * w / 100
            x2 = i * w / 100
            y1 = h - (self.ram_history[i - 1] / max_val) * h
            y2 = h - (self.ram_history[i] / max_val) * h
            self.graph_canvas.create_line(x1, y1, x2, y2, fill="green")

        # VRAM line (red)
        for i in range(1, len(self.vram_history)):
            x1 = (i - 1) * w / 100
            x2 = i * w / 100
            y1 = h - (self.vram_history[i - 1] / max_val) * h
            y2 = h - (self.vram_history[i] / max_val) * h
            self.graph_canvas.create_line(x1, y1, x2, y2, fill="red")

    def update(self):
        ram_used, ram_total = get_ram_stats_mb()
        gpus = query_nvidia_gpus()
        vram_used = sum(g["mem_used"] for g in gpus) if gpus else 0

        if ram_used is not None:
            self.ram_label.config(text=f"RAM: {ram_used} / {ram_total} MB")
        else:
            self.ram_label.config(text="RAM: psutil not available")

        for lbl in self.gpu_labels:
            lbl.destroy()
        self.gpu_labels.clear()

        if gpus:
            for g in gpus:
                lbl = tk.Label(self.root,
                               text=f"GPU {g['index']} VRAM: {g['mem_used']} / {g['mem_total']} MB",
                               font=("Consolas", 12))
                lbl.pack()
                self.gpu_labels.append(lbl)
        else:
            lbl = tk.Label(self.root, text="GPU: none", font=("Consolas", 12))
            lbl.pack()
            self.gpu_labels.append(lbl)

        total_bytes = self.manager.total_bytes()
        self.buffers_label.config(
            text=f"Buffers: {len(self.manager.buffers)} | {total_bytes / (1024**2):.2f} MB"
        )

        # Fragmentation
        self.frag_text.delete("1.0", tk.END)
        self.frag_text.insert(tk.END, self.manager.fragmentation_view())

        # Graph
        self.update_graph(ram_used, vram_used)

        if self.logger and ram_used is not None:
            self.logger.log_snapshot(ram_used, ram_total, gpus)

        self.root.after(self.update_interval_ms, self.update)

    def run(self):
        self.root.mainloop()

# -----------------------------
# Main live runner
# -----------------------------

def run_live_system(use_gui=True, log_history=True):
    mgr = UnifiedMemoryManager()
    logger = MemoryLogger() if log_history else None

    # Example buffers
    buf1 = mgr.create_buffer(50_000_000, tag="cpu_hot")
    buf1.fill_sequential()
    buf2 = mgr.create_buffer(50_000_000, tag="gpu_hot")
    buf2.fill(1.0)

    # Example GPU kernel execution
    if BACKEND.cuda and BACKEND.numba:
        run_gpu_add_scalar(buf2, 5.0)

    def telemetry_loop():
        while True:
            mgr.apply_policies()
            print_ascii_telemetry(mgr)
            if logger:
                ram_used, ram_total = get_ram_stats_mb()
                gpus = query_nvidia_gpus()
                if ram_used is not None:
                    logger.log_snapshot(ram_used, ram_total, gpus)
            time.sleep(1)

    t = threading.Thread(target=telemetry_loop, daemon=True)
    t.start()

    if use_gui and _tk_available:
        dash = MemoryDashboard(mgr, logger if log_history else None)
        dash.run()
    else:
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            if logger and _matplotlib_available:
                logger.plot_history()

# -----------------------------
# Entry point
# -----------------------------

if __name__ == "__main__":
    try:
        run_live_system(use_gui=True, log_history=True)
    except Exception as e:
        print("Fatal error:", e)
        traceback.print_exc()
        sys.exit(1)
