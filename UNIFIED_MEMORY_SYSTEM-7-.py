#!/usr/bin/env python3
"""
UNIFIED_MEMORY_SYSTEM_BORG_NEURAL_OS.py

Evolved version:

- Fully autonomous unified memory + adaptive intelligence engine
- CPU (NumPy) backend
- CUDA unified memory backend (Numba + CUDA)
- OpenCL backend (PyOpenCL) for AMD/Intel GPUs
- UnifiedMemoryManager + UnifiedBuffer
- RAM + VRAM telemetry (ASCII bars)
- Tkinter GUI dashboard (read-only, no controls)
- Multiple GPU kernels (CUDA + OpenCL): add, mul, ReLU
- VRAM fragmentation model + visualizer
- Borg-style gossip cluster + advanced sharding engine
- Real-ish neural memory brain (online-learned scoring)
- Real-ish water-physics migration engine (pressure/flow/turbulence)
- Advanced predictive model wrapper (AR-like, LSTM-like stub, GB-like stub)
- Gaming-grade VRAM allocator (hot/cold, frame-time oriented scoring)
- LLM-grade tensor residency engine (lifetime, layer-aware, KV-cache hints)
- Logging + history (CSV + optional matplotlib)
- Tuned modes for gaming and LLM workloads

NOTE:
- This is still a high-level orchestrator/simulator, not a kernel driver.
- "Real" models are implemented in a lightweight, self-contained way (no heavy ML deps).
"""

import sys
import time
import threading
import traceback
import subprocess
import csv
import json
import socket
from collections import deque
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
        __kernel void mul_scalar(__global float *arr, float value) {
            int i = get_global_id(0);
            arr[i] *= value;
        }
        __kernel void relu(__global float *arr) {
            int i = get_global_id(0);
            float v = arr[i];
            arr[i] = v > 0.0f ? v : 0.0f;
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

def run_opencl_mul_scalar(buf, value: float):
    if buf.mode != "opencl_device" or not OPENCL_CTX.available or OPENCL_CTX.program is None:
        return
    buf.sync_to_device_opencl()
    n = buf.size
    global_size = (n,)
    kernel = OPENCL_CTX.program.mul_scalar
    kernel(OPENCL_CTX.queue, global_size, None, buf.gpu, np.float32(value))
    OPENCL_CTX.queue.finish()
    buf.sync_to_host_opencl()

def run_opencl_relu(buf):
    if buf.mode != "opencl_device" or not OPENCL_CTX.available or OPENCL_CTX.program is None:
        return
    buf.sync_to_device_opencl()
    n = buf.size
    global_size = (n,)
    kernel = OPENCL_CTX.program.relu
    kernel(OPENCL_CTX.queue, global_size, None, buf.gpu)
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

    def __init__(self, size, dtype=np.float32, mode=None, tag=None,
                 preferred_backend=None, shard_id=None, meta=None):
        self.size = int(size)
        self.dtype = dtype
        self.tag = tag or "default"
        self.shard_id = shard_id
        self.meta = meta or {}  # can hold: layer_id, is_kv_cache, last_access, frame_id, etc.

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
        self.meta["last_access"] = time.time()
        return self._cpu_view

    @property
    def gpu(self):
        self.meta["last_access"] = time.time()
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
# CUDA GPU kernels
# -----------------------------

if BACKEND.cuda and BACKEND.numba:
    from numba import cuda

    @cuda.jit
    def add_scalar_kernel(arr, value):
        i = cuda.grid(1)
        if i < arr.size:
            arr[i] += value

    @cuda.jit
    def mul_scalar_kernel(arr, value):
        i = cuda.grid(1)
        if i < arr.size:
            arr[i] *= value

    @cuda.jit
    def relu_kernel(arr):
        i = cuda.grid(1)
        if i < arr.size:
            v = arr[i]
            arr[i] = v if v > 0 else 0

    def run_gpu_add_scalar(buf: UnifiedBuffer, value: float):
        if buf.mode != "cuda_unified":
            return
        threads_per_block = 256
        blocks = (buf.size + threads_per_block - 1) // threads_per_block
        add_scalar_kernel[blocks, threads_per_block](buf.gpu, value)
        cuda.synchronize()

    def run_gpu_mul_scalar(buf: UnifiedBuffer, value: float):
        if buf.mode != "cuda_unified":
            return
        threads_per_block = 256
        blocks = (buf.size + threads_per_block - 1) // threads_per_block
        mul_scalar_kernel[blocks, threads_per_block](buf.gpu, value)
        cuda.synchronize()

    def run_gpu_relu(buf: UnifiedBuffer):
        if buf.mode != "cuda_unified":
            return
        threads_per_block = 256
        blocks = (buf.size + threads_per_block - 1) // threads_per_block
        relu_kernel[blocks, threads_per_block](buf.gpu)
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
# Advanced predictive model (AR-like, LSTM-like, GB-like)
# -----------------------------

class ARLikePredictor:
    """Simple AR(1)-style predictor."""
    def predict(self, hist):
        if len(hist) < 3:
            return None
        x = np.array(hist[:-1], dtype=np.float64)
        y = np.array(hist[1:], dtype=np.float64)
        A = np.vstack([x, np.ones_like(x)]).T
        m, c = np.linalg.lstsq(A, y, rcond=None)[0]
        return m * hist[-1] + c

class LSTMLikePredictor:
    """Tiny 'LSTM-like' predictor: uses a small stateful linear recurrence."""
    def __init__(self):
        self.h = 0.0
        self.w_in = 0.5
        self.w_h = 0.4
        self.b = 0.0

    def predict(self, hist):
        if not hist:
            return None
        x = hist[-1]
        self.h = np.tanh(self.w_in * x + self.w_h * self.h + self.b)
        return x + 0.2 * self.h

class GradientBoostLikePredictor:
    """Ensemble of simple weak learners (means over different windows)."""
    def predict(self, hist):
        if len(hist) < 3:
            return None
        h = np.array(hist, dtype=np.float64)
        preds = []
        preds.append(h[-1])
        preds.append(h[-3:].mean())
        preds.append(h.mean())
        return float(np.mean(preds))

class AdvancedPredictor:
    """
    Wraps multiple simple predictors (AR-like, LSTM-like, GB-like)
    and averages their outputs when available.
    """

    def __init__(self):
        self.ar = ARLikePredictor()
        self.lstm = LSTMLikePredictor()
        self.gb = GradientBoostLikePredictor()

    def predict(self, hist):
        candidates = []
        p1 = self.ar.predict(hist)
        if p1 is not None:
            candidates.append(p1)
        p2 = self.lstm.predict(hist)
        if p2 is not None:
            candidates.append(p2)
        p3 = self.gb.predict(hist)
        if p3 is not None:
            candidates.append(p3)
        if not candidates:
            return None
        return float(np.mean(candidates))

class PredictiveEngine:
    """
    Multi-feature predictors for RAM/VRAM pressure and fragmentation.
    Uses rolling windows and AdvancedPredictor.
    """

    def __init__(self, window=120):
        self.ram_history = deque(maxlen=window)
        self.vram_history = deque(maxlen=window)
        self.frag_history = deque(maxlen=window)
        self.model = AdvancedPredictor()

    def update(self, ram_used_mb, vram_used_mb, frag_score):
        if ram_used_mb is not None:
            self.ram_history.append(ram_used_mb)
        if vram_used_mb is not None:
            self.vram_history.append(vram_used_mb)
        if frag_score is not None:
            self.frag_history.append(frag_score)

    def predict_ram(self):
        return self.model.predict(list(self.ram_history))

    def predict_vram(self):
        return self.model.predict(list(self.vram_history))

    def predict_frag(self):
        return self.model.predict(list(self.frag_history))

# -----------------------------
# Altered states engine
# -----------------------------

class AlteredStatesEngine:
    """
    System modes based on pressure and workload:
    - FLOW: normal operation
    - ALERT: high VRAM/RAM pressure
    - DREAM: low load, background compaction
    - PANIC: extreme pressure, aggressive paging
    - LLM: tuned for large, long-lived buffers
    - GAMING: tuned for fast GPU hot buffers
    - DRAIN: aggressively freeing memory
    - BALANCE: equal CPU/GPU preference
    """

    def __init__(self):
        self.state = "FLOW"
        self.workload_profile = "generic"

    def set_workload_profile(self, profile):
        if profile in ("gaming", "llm", "generic"):
            self.workload_profile = profile

    def update_state(self, ram_used, ram_total, vram_used, vram_total,
                     pred_ram, pred_vram, pred_frag):
        ram_ratio = (ram_used / ram_total) if (ram_used and ram_total) else 0
        vram_ratio = (vram_used / vram_total) if (vram_used and vram_total) else 0

        high_future = False
        if pred_ram and ram_total and pred_ram / ram_total > 0.9:
            high_future = True
        if pred_vram and vram_total and pred_vram / vram_total > 0.9:
            high_future = True

        if ram_ratio > 0.97 or vram_ratio > 0.97:
            self.state = "PANIC"
        elif ram_ratio > 0.9 or vram_ratio > 0.9 or high_future:
            self.state = "ALERT"
        elif ram_ratio < 0.35 and vram_ratio < 0.35:
            self.state = "DREAM"
        else:
            if self.workload_profile == "gaming":
                self.state = "GAMING"
            elif self.workload_profile == "llm":
                self.state = "LLM"
            else:
                self.state = "BALANCE"

        if pred_frag and pred_frag > 0.7 and self.state not in ("PANIC", "ALERT"):
            self.state = "DRAIN"

# -----------------------------
# Real-ish water-physics migration engine
# -----------------------------

class WaterPhysicsEngine:
    """
    Fluid-inspired migration engine:

    - Pressure: usage ratio (RAM/VRAM)
    - Flow: migration rate between CPU/GPU
    - Turbulence: fragmentation + rapid changes

    Produces hints for policy engine.
    """

    def __init__(self):
        self.last_ram_ratio = 0.0
        self.last_vram_ratio = 0.0
        self.last_frag = 0.0

    def compute_hints(self, ram_ratio, vram_ratio, frag_score, state):
        d_ram = ram_ratio - self.last_ram_ratio
        d_vram = vram_ratio - self.last_vram_ratio
        d_frag = frag_score - self.last_frag

        self.last_ram_ratio = ram_ratio
        self.last_vram_ratio = vram_ratio
        self.last_frag = frag_score

        pressure_gpu = vram_ratio
        pressure_cpu = ram_ratio

        flow_gpu_to_cpu = max(0.0, pressure_gpu - pressure_cpu)
        flow_cpu_to_gpu = max(0.0, pressure_cpu - pressure_gpu)

        turbulence = abs(d_frag) + 0.5 * (abs(d_ram) + abs(d_vram))

        hints = {
            "prefer_gpu": False,
            "prefer_cpu": False,
            "need_compaction": False,
            "aggressiveness": 1.0,
            "llm_bias_cpu": False,
            "gaming_bias_gpu": False,
            "flow_gpu_to_cpu": flow_gpu_to_cpu,
            "flow_cpu_to_gpu": flow_cpu_to_gpu,
            "turbulence": turbulence,
        }

        if pressure_gpu > 0.9 or flow_gpu_to_cpu > 0.1:
            hints["prefer_cpu"] = True
            hints["aggressiveness"] *= 1.5

        if pressure_cpu > 0.9 or flow_cpu_to_gpu > 0.1:
            hints["prefer_gpu"] = True
            hints["aggressiveness"] *= 1.3

        if frag_score > 0.5 or turbulence > 0.2:
            hints["need_compaction"] = True
            hints["aggressiveness"] *= 1.2

        if state == "LLM":
            hints["llm_bias_cpu"] = True
        if state == "GAMING":
            hints["gaming_bias_gpu"] = True

        return hints

# -----------------------------
# Real neural memory brain (online-learned scoring)
# -----------------------------

class NeuralMemoryBrain:
    """
    Tiny online-learned "neural" scoring model.

    Input features per buffer:
    - size_mb / 1024
    - frag_score
    - ram_ratio
    - vram_ratio
    - is_kv_cache
    - is_llm_tensor
    - is_gaming_hot
    - age (normalized)

    Outputs:
    - score_gpu
    - score_cpu

    Weights are updated online with a simple gradient step based on
    whether the last decision reduced pressure or not.
    """

    def __init__(self, lr=0.01):
        self.lr = lr
        self.w_gpu = np.random.randn(8).astype(np.float32) * 0.1
        self.w_cpu = np.random.randn(8).astype(np.float32) * 0.1
        self.last_pressure = None

    def _features_for_buffer(self, meta, frag_score, ram_ratio, vram_ratio, now):
        size_mb = meta["size_bytes"] / (1024**2)
        tag = meta["tag"]
        buf_meta = meta.get("meta", {})

        is_kv = 1.0 if buf_meta.get("is_kv_cache") else 0.0
        is_llm = 1.0 if buf_meta.get("is_llm_tensor") else 0.0
        is_gaming = 1.0 if tag == "gpu_hot" and buf_meta.get("is_frame_buffer") else 0.0

        last_access = buf_meta.get("last_access", now)
        age = max(0.0, now - last_access)
        age_norm = np.tanh(age / 5.0)

        x = np.array([
            size_mb / 1024.0,
            frag_score,
            ram_ratio,
            vram_ratio,
            is_kv,
            is_llm,
            is_gaming,
            age_norm,
        ], dtype=np.float32)
        return x

    def decide_and_learn(self, state, hints, buffer_meta, ram_ratio, vram_ratio, frag_score):
        now = time.time()
        plan = []

        pressure = max(ram_ratio, vram_ratio)
        if self.last_pressure is None:
            self.last_pressure = pressure

        for i, meta in enumerate(buffer_meta):
            mode = meta["mode"]
            x = self._features_for_buffer(meta, frag_score, ram_ratio, vram_ratio, now)

            score_gpu = float(np.dot(self.w_gpu, x))
            score_cpu = float(np.dot(self.w_cpu, x))

            if hints["gaming_bias_gpu"]:
                score_gpu += 0.5
            if hints["llm_bias_cpu"]:
                score_cpu += 0.5

            if state in ("PANIC", "DRAIN"):
                score_cpu += 0.7

            target = None
            if mode == "cpu" and hints["prefer_gpu"] and score_gpu > score_cpu:
                target = "cuda_unified"
            elif mode in ("cuda_unified", "opencl_device") and hints["prefer_cpu"] and score_cpu > score_gpu:
                target = "cpu"

            if target is not None:
                plan.append((i, target, "gpu_hot" if target != "cpu" else "cpu_hot"))

                new_pressure = pressure * 0.98 if target == "cpu" and vram_ratio > ram_ratio else pressure * 0.99
                delta = new_pressure - self.last_pressure
                grad_sign = -1.0 if delta < 0 else +1.0

                if target != "cpu":
                    self.w_gpu -= self.lr * grad_sign * x
                else:
                    self.w_cpu -= self.lr * grad_sign * x

        self.last_pressure = pressure
        return plan

# -----------------------------
# Gaming-optimized VRAM allocator
# -----------------------------

class GamingVRAMAllocator:
    """
    Gaming-grade VRAM allocator:

    - Hot/cold separation based on last_access + frame_id
    - Frame-time oriented: tries to keep hot buffers on GPU
    - GPU residency scoring: penalizes large cold buffers on GPU
    """

    def score_buffer(self, buf: UnifiedBuffer, frame_id: int):
        meta = buf.meta
        last_access = meta.get("last_access", 0.0)
        is_frame = meta.get("is_frame_buffer", False)
        size_mb = buf.bytes() / (1024**2)

        age = max(0.0, time.time() - last_access)
        age_norm = np.tanh(age / 5.0)

        hot_score = 0.0
        if is_frame:
            hot_score += 1.0
        if age_norm < 0.3:
            hot_score += 0.5

        residency_penalty = 0.0
        if buf.mode in ("cuda_unified", "opencl_device") and age_norm > 0.7 and size_mb > 256:
            residency_penalty += 0.7

        return hot_score - residency_penalty

    def suggest_migrations(self, buffers, vram_ratio, frame_id):
        plan = []
        for i, b in enumerate(buffers):
            score = self.score_buffer(b, frame_id)
            if vram_ratio > 0.9 and score < 0.0 and b.mode in ("cuda_unified", "opencl_device"):
                plan.append((i, "cpu", "cpu_cold"))
            elif vram_ratio < 0.7 and score > 0.8 and b.mode == "cpu":
                plan.append((i, "cuda_unified", "gpu_hot"))
        return plan

# -----------------------------
# LLM-optimized tensor residency engine
# -----------------------------

class LLMTensorResidencyEngine:
    """
    LLM-grade tensor residency:

    - Tensor lifetime prediction (simple decay)
    - Layer-aware residency (early vs late layers)
    - KV-cache optimization (keep hot KV on GPU)
    """

    def score_tensor(self, buf: UnifiedBuffer, layer_count=96):
        meta = buf.meta
        is_llm = meta.get("is_llm_tensor", False)
        if not is_llm:
            return 0.0

        size_mb = buf.bytes() / (1024**2)
        layer_id = meta.get("layer_id", layer_count // 2)
        is_kv = meta.get("is_kv_cache", False)
        last_access = meta.get("last_access", 0.0)
        age = max(0.0, time.time() - last_access)
        age_norm = np.tanh(age / 10.0)

        layer_pos = layer_id / max(1, layer_count - 1)
        layer_weight = 1.0 - abs(layer_pos - 0.5)

        score = 0.0
        score += 0.5 * layer_weight
        if is_kv:
            score += 0.7
        if age_norm < 0.3:
            score += 0.4
        if size_mb > 2048:
            score -= 0.3

        return score

    def suggest_migrations(self, buffers, vram_ratio, layer_count=96):
        plan = []
        for i, b in enumerate(buffers):
            score = self.score_tensor(b, layer_count=layer_count)
            if score <= 0.0:
                continue
            if vram_ratio < 0.95 and b.mode == "cpu":
                plan.append((i, "cuda_unified", "llm_hot"))
            elif vram_ratio > 0.98 and b.mode in ("cuda_unified", "opencl_device") and score < 0.5:
                plan.append((i, "cpu", "llm_cold"))
        return plan

# -----------------------------
# Policy engine (uses neural brain + water physics + gaming + LLM engines)
# -----------------------------

class PolicyEngine:
    def __init__(self, manager):
        self.manager = manager
        self.preferred_backend = None
        self.predictive = PredictiveEngine()
        self.states = AlteredStatesEngine()
        self.water = WaterPhysicsEngine()
        self.neural_brain = NeuralMemoryBrain()
        self.gaming_alloc = GamingVRAMAllocator()
        self.llm_residency = LLMTensorResidencyEngine()
        self.frame_id = 0

    def estimate_vram_usage_mb(self):
        gpus = query_nvidia_gpus()
        if not gpus:
            return 0, 0
        used = sum(g["mem_used"] for g in gpus)
        total = sum(g["mem_total"] for g in gpus)
        return used, total

    def apply_policies(self):
        self.frame_id += 1

        ram_used, ram_total = get_ram_stats_mb()
        vram_used, vram_total = self.estimate_vram_usage_mb()
        frag_stats = self.manager.vram_frag_model.compute_stats()
        frag_score = frag_stats["fragmentation_score"]

        self.predictive.update(ram_used, vram_used, frag_score)
        pred_ram = self.predictive.predict_ram()
        pred_vram = self.predictive.predict_vram()
        pred_frag = self.predictive.predict_frag()

        self.states.update_state(ram_used, ram_total, vram_used, vram_total,
                                 pred_ram, pred_vram, pred_frag)
        state = self.states.state

        ram_ratio = (ram_used / ram_total) if (ram_used and ram_total) else 0
        vram_ratio = (vram_used / vram_total) if (vram_used and vram_total) else 0

        hints = self.water.compute_hints(ram_ratio, vram_ratio, frag_score, state)

        buffer_meta = []
        for b in self.manager.buffers:
            buffer_meta.append({
                "size_bytes": b.bytes(),
                "mode": b.mode,
                "tag": b.tag,
                "meta": b.meta,
            })

        plan = self.neural_brain.decide_and_learn(
            state, hints, buffer_meta, ram_ratio, vram_ratio, frag_score
        )

        if self.states.workload_profile == "gaming":
            plan.extend(self.gaming_alloc.suggest_migrations(self.manager.buffers, vram_ratio, self.frame_id))
        elif self.states.workload_profile == "llm":
            plan.extend(self.llm_residency.suggest_migrations(self.manager.buffers, vram_ratio))

        applied_indices = set()
        for idx, target_mode, new_tag in plan:
            if idx in applied_indices:
                continue
            applied_indices.add(idx)
            if idx < 0 or idx >= len(self.manager.buffers):
                continue
            buf = self.manager.buffers[idx]
            if buf.mode == target_mode:
                continue
            new_buf = UnifiedBuffer(buf.size, buf.dtype, mode=target_mode, tag=new_tag,
                                    preferred_backend=self.preferred_backend,
                                    shard_id=buf.shard_id, meta=dict(buf.meta))
            if buf.mode == "opencl_device":
                buf.sync_to_host_opencl()
            new_buf.cpu[...] = buf.cpu[...]
            if target_mode == "opencl_device":
                new_buf.sync_to_device_opencl()
            self.manager.replace_buffer(buf, new_buf)

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

    def create_buffer(self, size, dtype=np.float32, mode=None, tag=None,
                      shard_id=None, meta=None):
        buf = UnifiedBuffer(size=size, dtype=dtype, mode=mode, tag=tag,
                            preferred_backend=self.policy_engine.preferred_backend,
                            shard_id=shard_id, meta=meta)
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
            lines.append(
                f"{i:02d}: {mb:8.2f} MB | mode={b.mode} | tag={b.tag} | shard={b.shard_id} | meta={b.meta}"
            )
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
    vram_used = sum(g["mem_used"] for g in gpus) if gpus else 0
    vram_total = sum(g["mem_total"] for g in gpus) if gpus else 0

    print("=" * 110)
    print("UNIFIED MEMORY LIVE STATUS (NEURAL OS)")
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

    state = manager.policy_engine.states.state
    profile = manager.policy_engine.states.workload_profile
    print(f"System state: {state} | Workload profile: {profile}")

    print("Fragmentation + buffers:")
    print(manager.fragmentation_view())
    print("=" * 110)

# -----------------------------
# Tkinter GUI dashboard (read-only)
# -----------------------------

class MemoryDashboard:
    def __init__(self, manager: UnifiedMemoryManager, logger: MemoryLogger = None):
        if not _tk_available:
            raise RuntimeError("Tkinter not available.")
        self.manager = manager
        self.logger = logger
        self.root = tk.Tk()
        self.root.title("Unified Memory Borg Neural OS Dashboard")

        self.ram_label = tk.Label(self.root, text="RAM: ", font=("Consolas", 12))
        self.ram_label.pack(pady=5)

        self.gpu_labels = []

        self.state_label = tk.Label(self.root, text="State: ", font=("Consolas", 12))
        self.state_label.pack(pady=5)

        self.buffers_label = tk.Label(self.root, text="Buffers: ", font=("Consolas", 12))
        self.buffers_label.pack(pady=5)

        self.frag_text = tk.Text(self.root, height=16, width=110, font=("Consolas", 9))
        self.frag_text.pack(pady=5)

        self.graph_canvas = tk.Canvas(self.root, width=500, height=180, bg="black")
        self.graph_canvas.pack(pady=5)
        self.ram_history = []
        self.vram_history = []

        self.update_interval_ms = 1000
        self.root.after(self.update_interval_ms, self.update)

    def update_graph(self, ram_used, vram_used):
        self.ram_history.append(ram_used or 0)
        self.vram_history.append(vram_used or 0)
        if len(self.ram_history) > 150:
            self.ram_history = self.ram_history[-150:]
            self.vram_history = self.vram_history[-150:]

        self.graph_canvas.delete("all")
        w = 500
        h = 180
        max_val = max(self.ram_history + self.vram_history + [1])

        for i in range(1, len(self.ram_history)):
            x1 = (i - 1) * w / 150
            x2 = i * w / 150
            y1 = h - (self.ram_history[i - 1] / max_val) * h
            y2 = h - (self.ram_history[i] / max_val) * h
            self.graph_canvas.create_line(x1, y1, x2, y2, fill="green")

        for i in range(1, len(self.vram_history)):
            x1 = (i - 1) * w / 150
            x2 = i * w / 150
            y1 = h - (self.vram_history[i - 1] / max_val) * h
            y2 = h - (self.vram_history[i] / max_val) * h
            self.graph_canvas.create_line(x1, y1, x2, y2, fill="red")

    def update(self):
        ram_used, ram_total = get_ram_stats_mb()
        gpus = query_nvidia_gpus()
        vram_used = sum(g["mem_used"] for g in gpus) if gpus else 0
        vram_total = sum(g["mem_total"] for g in gpus) if gpus else 0

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

        state = self.manager.policy_engine.states.state
        profile = self.manager.policy_engine.states.workload_profile
        self.state_label.config(text=f"State: {state} | Profile: {profile}")

        self.frag_text.delete("1.0", tk.END)
        self.frag_text.insert(tk.END, self.manager.fragmentation_view())

        self.update_graph(ram_used, vram_used)

        if self.logger and ram_used is not None:
            self.logger.log_snapshot(ram_used, ram_total, gpus)

        self.root.after(self.update_interval_ms, self.update)

    def run(self):
        self.root.mainloop()

# -----------------------------
# Borg-style gossip cluster + advanced sharding
# -----------------------------

class ClusterNode:
    """
    Fuller sharding engine:

    - Hash-based base placement
    - Load-aware (total bytes per node)
    - GPU availability-aware
    - Latency-aware (simple RTT estimate)
    """

    def __init__(self, manager: UnifiedMemoryManager, host="0.0.0.0", port=50050,
                 peers=None, node_id=None):
        self.manager = manager
        self.host = host
        self.port = port
        self.peers = peers or []
        self.running = False
        self.cluster_state = {}
        self.node_id = node_id or f"{self.host}:{self.port}"
        self.latency_estimates = {p: 1.0 for p in self.peers}

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
        total_bytes = sum(b.bytes() for b in self.manager.buffers)
        gpu_info = query_nvidia_gpus()
        gpu_total = sum(g["mem_total"] for g in gpu_info) if gpu_info else 0
        gpu_used = sum(g["mem_used"] for g in gpu_info) if gpu_info else 0
        return {
            "buffers": [
                {
                    "size": b.size,
                    "bytes": b.bytes(),
                    "mode": b.mode,
                    "tag": b.tag,
                    "shard_id": b.shard_id,
                }
                for b in self.manager.buffers
            ],
            "total_bytes": total_bytes,
            "gpu_total": gpu_total,
            "gpu_used": gpu_used,
        }

    def _merge_cluster_state(self, incoming):
        self.cluster_state[self.node_id] = self._local_state()
        for k, v in incoming.items():
            self.cluster_state[k] = v

    def _send_gossip(self, peer):
        try:
            host, port = peer
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            t0 = time.time()
            s.connect((host, port))
            self.cluster_state[self.node_id] = self._local_state()
            msg = {"type": "gossip", "state": self.cluster_state}
            s.send(json.dumps(msg).encode())
            s.close()
            rtt = max(0.001, time.time() - t0)
            self.latency_estimates[peer] = 0.8 * self.latency_estimates.get(peer, rtt) + 0.2 * rtt
        except Exception:
            pass

    def _node_score(self, node_id):
        st = self.cluster_state.get(node_id, {})
        total_bytes = st.get("total_bytes", 0)
        gpu_total = st.get("gpu_total", 0)
        gpu_used = st.get("gpu_used", 0)
        gpu_ratio = (gpu_used / gpu_total) if gpu_total else 0.0

        load_penalty = total_bytes / (1024**3)
        gpu_penalty = gpu_ratio
        latency_penalty = 0.0
        for peer, lat in self.latency_estimates.items():
            if f"{peer[0]}:{peer[1]}" == node_id:
                latency_penalty = lat
                break

        score = -0.5 * load_penalty - 0.3 * gpu_penalty - 0.2 * latency_penalty
        return score

    def suggest_shard_id(self, key):
        if not self.cluster_state:
            return self.node_id
        nodes = sorted(self.cluster_state.keys())
        if not nodes:
            return self.node_id

        base_idx = sum(ord(c) for c in key) % len(nodes)
        candidates = nodes[max(0, base_idx - 1):min(len(nodes), base_idx + 2)]
        if not candidates:
            candidates = nodes

        best_node = None
        best_score = -1e9
        for n in candidates:
            s = self._node_score(n)
            if s > best_score:
                best_score = s
                best_node = n
        return best_node or self.node_id

    def rebalance_shards(self):
        if not self.cluster_state:
            return
        nodes = sorted(self.cluster_state.keys())
        if not nodes:
            return
        for b in self.manager.buffers:
            if b.shard_id is None:
                b.shard_id = self.suggest_shard_id(f"{b.tag}:{b.size}")

# -----------------------------
# Main live runner
# -----------------------------

def run_live_system(use_gui=True, log_history=True, start_cluster=False,
                    peers=None, workload_profile="generic"):
    mgr = UnifiedMemoryManager()
    mgr.policy_engine.states.set_workload_profile(workload_profile)

    logger = MemoryLogger() if log_history else None

    if workload_profile == "gaming":
        buf1 = mgr.create_buffer(
            5_000_000, tag="gpu_hot",
            meta={"is_frame_buffer": True, "is_llm_tensor": False}
        )
        buf1.fill(1.0)
        buf2 = mgr.create_buffer(
            5_000_000, tag="gpu_hot",
            meta={"is_frame_buffer": True, "is_llm_tensor": False}
        )
        buf2.fill(2.0)
    elif workload_profile == "llm":
        buf1 = mgr.create_buffer(
            50_000_000, tag="cpu_hot",
            meta={"is_llm_tensor": True, "layer_id": 10, "is_kv_cache": False}
        )
        buf1.fill_sequential()
        buf2 = mgr.create_buffer(
            50_000_000, tag="gpu_hot",
            meta={"is_llm_tensor": True, "layer_id": 80, "is_kv_cache": True}
        )
        buf2.fill(0.5)
    else:
        buf1 = mgr.create_buffer(10_000_000, tag="cpu_hot",
                                 meta={"is_llm_tensor": False})
        buf1.fill_sequential()
        buf2 = mgr.create_buffer(10_000_000, tag="gpu_hot",
                                 meta={"is_llm_tensor": False})
        buf2.fill(1.0)

    if BACKEND.cuda and BACKEND.numba:
        run_gpu_add_scalar(buf2, 5.0)
        run_gpu_mul_scalar(buf2, 0.9)
        run_gpu_relu(buf2)
    if OPENCL_CTX.available:
        run_opencl_add_scalar(buf2, 3.0)
        run_opencl_mul_scalar(buf2, 0.8)
        run_opencl_relu(buf2)

    cluster = None
    if start_cluster:
        cluster = ClusterNode(mgr, host="0.0.0.0", port=50050, peers=peers or [])
        cluster.start()

    def telemetry_loop():
        while True:
            mgr.apply_policies()
            if cluster:
                cluster.rebalance_shards()
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
            peers=[("127.0.0.1", 50050)],
            workload_profile="llm"  # "generic", "gaming", "llm"
        )
    except Exception as e:
        print("Fatal error:", e)
        traceback.print_exc()
        sys.exit(1)
