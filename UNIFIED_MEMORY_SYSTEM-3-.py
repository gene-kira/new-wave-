#!/usr/bin/env python3
"""
UNIFIED_MEMORY_SYSTEM_BORG.py

Unified memory engine with:

- CPU (NumPy) backend
- CUDA unified memory backend (Numba + CUDA)
- OpenCL backend (PyOpenCL) for AMD/Intel GPUs
- UnifiedMemoryManager + UnifiedBuffer
- RAM + VRAM telemetry (ASCII bars)
- Tkinter GUI dashboard + real-time graph
- GPU kernel execution (CUDA + OpenCL)
- VRAM fragmentation model + visualizer
- Distributed cluster with Borg-style gossip
- Live policy controls in GUI (VRAM limit, backend preference, migration mode)
- Logging + history (CSV + optional matplotlib)
- Multi-GPU support via nvidia-smi
"""

import sys
import time
import threading
import traceback
import subprocess
import csv
import json
import socket
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
_pyopencl_available = _try_import("pyopencl", alias="cl")

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
        self.opencl = _pyopencl_available

    def best_mode(self, preferred=None):
        if preferred == "cuda_unified" and self.cuda and self.numba:
            return "cuda_unified"
        if preferred == "opencl_device" and self.opencl:
            return "opencl_device"
        if preferred == "cpu":
            return "cpu"
        if self.cuda and self.numba:
            return "cuda_unified"
        if self.opencl:
            return "opencl_device"
        return "cpu"

    def summary(self):
        return {
            "numpy": self.numpy,
            "numba": self.numba,
            "cupy": self.cupy,
            "cuda": self.cuda,
            "opencl": self.opencl,
        }

BACKEND = BackendInfo()

# -----------------------------
# GPU telemetry (multi-GPU via nvidia-smi)
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
# OpenCL context + kernels
# -----------------------------

class OpenCLContext:
    def __init__(self):
        self.available = False
        self.ctx = None
        self.queue = None
        self.program = None
        if not BACKEND.opencl:
            return
        try:
            platforms = cl.get_platforms()
            devices = []
            for p in platforms:
                devices.extend(p.get_devices())
            if not devices:
                return
            self.ctx = cl.Context(devices=devices)
            self.queue = cl.CommandQueue(self.ctx)
            self.available = True
            self._build_program()
        except Exception:
            self.available = False
            self.ctx = None
            self.queue = None
            self.program = None

    def _build_program(self):
        src = r"""
        __kernel void add_scalar(__global float *arr, float value) {
            int i = get_global_id(0);
            arr[i] += value;
        }
        """
        self.program = cl.Program(self.ctx, src).build()

OPENCL_CTX = OpenCLContext()

def run_opencl_add_scalar(buf, value: float):
    if buf.mode != "opencl_device" or not OPENCL_CTX.available or OPENCL_CTX.program is None:
        return
    buf.sync_to_device_opencl()
    n = buf.size
    global_size = (n,)
    kernel = OPENCL_CTX.program.add_scalar
    kernel(OPENCL_CTX.queue, global_size, None, buf.gpu, np.float32(value))
    OPENCL_CTX.queue.finish()
    buf.sync_to_host_opencl()

# -----------------------------
# UnifiedBuffer
# -----------------------------

class UnifiedBuffer:
    """
    Modes:
    - 'cpu'          : NumPy array in system RAM
    - 'cuda_unified' : Numba cuda.managed_array (CPU+GPU shared)
    - 'opencl_device': PyOpenCL device buffer (GPU VRAM)
    """

    def __init__(self, size, dtype=np.float32, mode=None, tag=None, preferred_backend=None):
        self.size = int(size)
        self.dtype = dtype
        self.tag = tag or "default"

        if mode is None:
            mode = BACKEND.best_mode(preferred=preferred_backend)
        if mode not in ("cpu", "cuda_unified", "opencl_device"):
            raise ValueError(f"Unsupported mode: {mode}")
        self.mode = mode

        self._cpu_view = None
        self._gpu_view = None
        self._opencl_buf = None

        if self.mode == "cpu":
            self._init_cpu()
        elif self.mode == "cuda_unified":
            self._init_cuda_unified()
        elif self.mode == "opencl_device":
            self._init_opencl_device()

    def _init_cpu(self):
        self._cpu_view = np.zeros(self.size, dtype=self.dtype)

    def _init_cuda_unified(self):
        if not (BACKEND.cuda and BACKEND.numba):
            raise RuntimeError("CUDA unified requested but not available.")
        from numba import cuda
        self._gpu_view = cuda.managed_array(self.size, dtype=self.dtype)
        self._cpu_view = self._gpu_view

    def _init_opencl_device(self):
        if not OPENCL_CTX.available:
            raise RuntimeError("OpenCL requested but no devices available.")
        self._cpu_view = np.zeros(self.size, dtype=self.dtype)
        mf = cl.mem_flags
        self._opencl_buf = cl.Buffer(OPENCL_CTX.ctx, mf.READ_WRITE, self._cpu_view.nbytes)

    @property
    def cpu(self):
        return self._cpu_view

    @property
    def gpu(self):
        if self.mode == "cuda_unified":
            return self._gpu_view
        elif self.mode == "opencl_device":
            return self._opencl_buf
        else:
            raise RuntimeError("GPU view only valid in cuda_unified or opencl_device mode.")

    def sync_to_device_opencl(self):
        if self.mode != "opencl_device":
            return
        cl.enqueue_copy(OPENCL_CTX.queue, self._opencl_buf, self._cpu_view).wait()

    def sync_to_host_opencl(self):
        if self.mode != "opencl_device":
            return
        cl.enqueue_copy(OPENCL_CTX.queue, self._cpu_view, self._opencl_buf).wait()

    def to_numpy(self, copy=True):
        if copy:
            return np.array(self.cpu, copy=True)
        return self.cpu

    def fill(self, value):
        self.cpu[...] = value
        if self.mode == "opencl_device":
            self.sync_to_device_opencl()

    def fill_sequential(self, start=0):
        self.cpu[...] = np.arange(start, start + self.size, dtype=self.dtype)
        if self.mode == "opencl_device":
            self.sync_to_device_opencl()

    def bytes(self):
        return self.size * np.dtype(self.dtype).itemsize

# -----------------------------
# CUDA GPU kernel example
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
# VRAM fragmentation model
# -----------------------------

class VRAMFragmentationModel:
    def __init__(self):
        self.regions = []
        self.next_addr = 0

    def register_buffer(self, buf: UnifiedBuffer):
        if buf.mode not in ("cuda_unified", "opencl_device"):
            return
        size = buf.bytes()
        start = self.next_addr
        self.next_addr += size
        self.regions.append((start, size, buf.tag, buf.mode))

    def clear(self):
        self.regions.clear()
        self.next_addr = 0

    def compute_stats(self):
        if not self.regions:
            return {
                "total_regions": 0,
                "total_bytes": 0,
                "largest_region": 0,
                "fragmentation_score": 0.0,
            }
        total_bytes = sum(r[1] for r in self.regions)
        largest_region = max(r[1] for r in self.regions)
        frag_score = 1.0 - (largest_region / total_bytes)
        return {
            "total_regions": len(self.regions),
            "total_bytes": total_bytes,
            "largest_region": largest_region,
            "fragmentation_score": frag_score,
        }

    def ascii_map(self, width=60):
        if not self.regions:
            return "[no GPU regions]"
        total_bytes = sum(r[1] for r in self.regions)
        if total_bytes <= 0:
            return "[empty]"
        bar = ["-"] * width
        offset = 0
        for start, size, tag, mode in self.regions:
            span = max(1, int((size / total_bytes) * width))
            for i in range(offset, min(width, offset + span)):
                bar[i] = "#" if mode == "cuda_unified" else "O"
            offset += span
        return "[" + "".join(bar) + "]"

# -----------------------------
# Policy engine (GPU/CPU + VRAM paging)
# -----------------------------

class PolicyEngine:
    def __init__(self, manager):
        self.manager = manager
        self.vram_limit_mb = None
        self.preferred_backend = None
        self.migration_mode = "auto"  # "auto", "cpu_only", "gpu_only"

    def estimate_vram_usage_mb(self):
        gpus = query_nvidia_gpus()
        if not gpus:
            return 0
        return sum(g["mem_used"] for g in gpus)

    def apply_policies(self):
        for buf in list(self.manager.buffers):
            if self.migration_mode == "cpu_only":
                if buf.mode in ("cuda_unified", "opencl_device"):
                    new_buf = UnifiedBuffer(buf.size, buf.dtype, mode="cpu", tag="cpu_hot")
                    if buf.mode == "opencl_device":
                        buf.sync_to_host_opencl()
                    new_buf.cpu[...] = buf.cpu[...]
                    self.manager.replace_buffer(buf, new_buf)
                continue

            if self.migration_mode == "gpu_only":
                if buf.mode == "cpu" and (BACKEND.cuda or BACKEND.opencl):
                    mode = BACKEND.best_mode(preferred=self.preferred_backend)
                    new_buf = UnifiedBuffer(buf.size, buf.dtype, mode=mode, tag="gpu_hot")
                    new_buf.cpu[...] = buf.cpu[...]
                    if mode == "opencl_device":
                        new_buf.sync_to_device_opencl()
                    self.manager.replace_buffer(buf, new_buf)
                continue

            if buf.tag == "gpu_hot" and buf.mode == "cpu" and (BACKEND.cuda or BACKEND.opencl):
                mode = BACKEND.best_mode(preferred=self.preferred_backend)
                new_buf = UnifiedBuffer(buf.size, buf.dtype, mode=mode, tag=buf.tag)
                new_buf.cpu[...] = buf.cpu[...]
                if mode == "opencl_device":
                    new_buf.sync_to_device_opencl()
                self.manager.replace_buffer(buf, new_buf)

            elif buf.tag == "cpu_hot" and buf.mode in ("cuda_unified", "opencl_device"):
                new_buf = UnifiedBuffer(buf.size, buf.dtype, mode="cpu", tag=buf.tag)
                if buf.mode == "opencl_device":
                    buf.sync_to_host_opencl()
                new_buf.cpu[...] = buf.cpu[...]
                self.manager.replace_buffer(buf, new_buf)

        if self.vram_limit_mb is not None and (BACKEND.cuda or BACKEND.opencl):
            current_vram = self.estimate_vram_usage_mb()
            if current_vram > self.vram_limit_mb:
                gpu_hot_buffers = [b for b in self.manager.buffers
                                   if b.mode in ("cuda_unified", "opencl_device") and b.tag == "gpu_hot"]
                gpu_hot_buffers.sort(key=lambda b: b.bytes(), reverse=True)
                for buf in gpu_hot_buffers:
                    if current_vram <= self.vram_limit_mb:
                        break
                    new_buf = UnifiedBuffer(buf.size, buf.dtype, mode="cpu", tag="cpu_hot")
                    if buf.mode == "opencl_device":
                        buf.sync_to_host_opencl()
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
        self.vram_frag_model = VRAMFragmentationModel()

    def backend_summary(self):
        return self.backend.summary()

    def create_buffer(self, size, dtype=np.float32, mode=None, tag=None):
        buf = UnifiedBuffer(size=size, dtype=dtype, mode=mode, tag=tag,
                            preferred_backend=self.policy_engine.preferred_backend)
        self.buffers.append(buf)
        self.vram_frag_model.register_buffer(buf)
        return buf

    def list_buffers(self):
        return list(self.buffers)

    def clear_buffers(self):
        self.buffers.clear()
        self.vram_frag_model.clear()

    def total_bytes(self):
        return sum(b.bytes() for b in self.buffers)

    def replace_buffer(self, old, new):
        idx = self.buffers.index(old)
        self.buffers[idx] = new
        self.vram_frag_model.clear()
        for b in self.buffers:
            self.vram_frag_model.register_buffer(b)

    def apply_policies(self):
        self.policy_engine.apply_policies()

    def fragmentation_view(self):
        buf_info = sorted(self.buffers, key=lambda b: b.bytes(), reverse=True)
        lines = []
        for i, b in enumerate(buf_info):
            mb = b.bytes() / (1024**2)
            lines.append(f"{i:02d}: {mb:8.2f} MB | mode={b.mode} | tag={b.tag}")
        stats = self.vram_frag_model.compute_stats()
        lines.append(f"VRAM regions: {stats['total_regions']}, "
                     f"frag_score={stats['fragmentation_score']:.3f}")
        lines.append(f"VRAM map: {self.vram_frag_model.ascii_map()}")
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
# Tkinter GUI dashboard + controls
# -----------------------------

class MemoryDashboard:
    def __init__(self, manager: UnifiedMemoryManager, logger: MemoryLogger = None):
        if not _tk_available:
            raise RuntimeError("Tkinter not available.")
        self.manager = manager
        self.logger = logger
        self.root = tk.Tk()
        self.root.title("Unified Memory Borg Dashboard")

        self.ram_label = tk.Label(self.root, text="RAM: ", font=("Consolas", 12))
        self.ram_label.pack(pady=5)

        self.gpu_labels = []

        self.buffers_label = tk.Label(self.root, text="Buffers: ", font=("Consolas", 12))
        self.buffers_label.pack(pady=5)

        self.frag_text = tk.Text(self.root, height=10, width=80, font=("Consolas", 9))
        self.frag_text.pack(pady=5)

        self.graph_canvas = tk.Canvas(self.root, width=400, height=150, bg="black")
        self.graph_canvas.pack(pady=5)
        self.ram_history = []
        self.vram_history = []

        controls_frame = tk.Frame(self.root)
        controls_frame.pack(pady=5)

        tk.Label(controls_frame, text="VRAM limit (MB):").grid(row=0, column=0, sticky="w")
        self.vram_limit_var = tk.StringVar(value="")
        tk.Entry(controls_frame, textvariable=self.vram_limit_var, width=8).grid(row=0, column=1)

        tk.Label(controls_frame, text="Preferred backend:").grid(row=0, column=2, sticky="w")
        self.backend_var = tk.StringVar(value="auto")
        tk.OptionMenu(controls_frame, self.backend_var, "auto", "cuda_unified", "opencl_device", "cpu").grid(row=0, column=3)

        tk.Label(controls_frame, text="Migration mode:").grid(row=0, column=4, sticky="w")
        self.migration_var = tk.StringVar(value="auto")
        tk.OptionMenu(controls_frame, self.migration_var, "auto", "cpu_only", "gpu_only").grid(row=0, column=5)

        tk.Button(controls_frame, text="Apply Policies", command=self.apply_controls).grid(row=0, column=6, padx=10)

        self.update_interval_ms = 1000
        self.root.after(self.update_interval_ms, self.update)

    def apply_controls(self):
        vram_limit_text = self.vram_limit_var.get().strip()
        if vram_limit_text:
            try:
                self.manager.policy_engine.vram_limit_mb = float(vram_limit_text)
            except ValueError:
                self.manager.policy_engine.vram_limit_mb = None
        else:
            self.manager.policy_engine.vram_limit_mb = None

        backend_choice = self.backend_var.get()
        if backend_choice == "auto":
            self.manager.policy_engine.preferred_backend = None
        else:
            self.manager.policy_engine.preferred_backend = backend_choice

        self.manager.policy_engine.migration_mode = self.migration_var.get()

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

        for i in range(1, len(self.ram_history)):
            x1 = (i - 1) * w / 100
            x2 = i * w / 100
            y1 = h - (self.ram_history[i - 1] / max_val) * h
            y2 = h - (self.ram_history[i] / max_val) * h
            self.graph_canvas.create_line(x1, y1, x2, y2, fill="green")

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

        self.frag_text.delete("1.0", tk.END)
        self.frag_text.insert(tk.END, self.manager.fragmentation_view())

        self.update_graph(ram_used, vram_used)

        if self.logger and ram_used is not None:
            self.logger.log_snapshot(ram_used, ram_total, gpus)

        self.root.after(self.update_interval_ms, self.update)

    def run(self):
        self.root.mainloop()

# -----------------------------
# Borg-style gossip cluster
# -----------------------------

class ClusterNode:
    def __init__(self, manager: UnifiedMemoryManager, host="0.0.0.0", port=50050, peers=None):
        self.manager = manager
        self.host = host
        self.port = port
        self.peers = peers or []
        self.running = False
        self.cluster_state = {}

    def start(self):
        self._start_server()
        self._start_gossip()

    def _start_server(self):
        def server_loop():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(5)
            self.running = True
            while self.running:
                try:
                    conn, addr = s.accept()
                    data = conn.recv(8192)
                    if not data:
                        conn.close()
                        continue
                    msg = json.loads(data.decode())
                    if msg.get("type") == "gossip":
                        self._merge_cluster_state(msg.get("state", {}))
                    conn.close()
                except Exception:
                    continue
        t = threading.Thread(target=server_loop, daemon=True)
        t.start()

    def _start_gossip(self):
        def gossip_loop():
            while True:
                if self.peers:
                    peer = self.peers[int(time.time()) % len(self.peers)]
                    self._send_gossip(peer)
                time.sleep(2)
        t = threading.Thread(target=gossip_loop, daemon=True)
        t.start()

    def _local_state(self):
        return {
            "buffers": [
                {
                    "size": b.size,
                    "bytes": b.bytes(),
                    "mode": b.mode,
                    "tag": b.tag,
                }
                for b in self.manager.buffers
            ]
        }

    def _merge_cluster_state(self, incoming):
        node_id = f"{self.host}:{self.port}"
        self.cluster_state[node_id] = self._local_state()
        for k, v in incoming.items():
            self.cluster_state[k] = v

    def _send_gossip(self, peer):
        try:
            host, port = peer
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((host, port))
            node_id = f"{self.host}:{self.port}"
            self.cluster_state[node_id] = self._local_state()
            msg = {"type": "gossip", "state": self.cluster_state}
            s.send(json.dumps(msg).encode())
            s.close()
        except Exception:
            pass

# -----------------------------
# Main live runner
# -----------------------------

def run_live_system(use_gui=True, log_history=True, start_cluster=False, peers=None):
    mgr = UnifiedMemoryManager()
    logger = MemoryLogger() if log_history else None

    buf1 = mgr.create_buffer(10_000_000, tag="cpu_hot")
    buf1.fill_sequential()
    buf2 = mgr.create_buffer(10_000_000, tag="gpu_hot")
    buf2.fill(1.0)

    if BACKEND.cuda and BACKEND.numba:
        run_gpu_add_scalar(buf2, 5.0)
    if OPENCL_CTX.available:
        run_opencl_add_scalar(buf2, 3.0)

    cluster = None
    if start_cluster:
        cluster = ClusterNode(mgr, host="0.0.0.0", port=50050, peers=peers or [])
        cluster.start()

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
        run_live_system(
            use_gui=True,
            log_history=True,
            start_cluster=False,
            peers=[("127.0.0.1", 50050)]
        )
    except Exception as e:
        print("Fatal error:", e)
        traceback.print_exc()
        sys.exit(1)
