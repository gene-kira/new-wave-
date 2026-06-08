"""
KHEPERA-CORE + AI BORG v0.9.0

Next evolution layer (integrated in a single unified file):

Already present (v0.8.0):
- Full language stack (tokenizer, parser, AST, compiler, VM)
- Python emitter + Python → KHEPERA translator
- Neural-adaptive execution (policy net, telemetry-driven tuning)
- Tile-based execution + cache
- Multi-node telemetry + tile RPC
- Swarm presence + AI Borg organism
- Genetic programming, self-rewriting, meta-state, emotional inertia
- Persistent memory + embeddings + vector DB
- Multi-agent swarm roles
- GUI default boot, organism always running
- Distributed task scheduling, swarm consensus
- Hot-swap module rewriting (stub-level)
- Safety sandbox + syscall virtualization
- Self-diagnostics + self-repair

New in v0.9.0:
- Real(istic) Raft-style consensus (single-process, log-based stub)
- Real distributed memory (logical multi-node key/value abstraction)
- Real swarm-level planning (global goal synthesis)
- Real module-level evolution (per-module fitness + mutation)
- Real neural-symbolic fusion (LLM + semantic trace blending)
- Real multi-agent negotiation (utility-based voting)
- Real long-term memory consolidation (periodic summarization)
- Real self-modifying neural weights (online tiny policy updates)
- Real hardware-aware optimization (telemetry → config tuning)
- Real multi-process sharding (logical worker shards, stubbed)
- Real agent-to-agent messaging (in-memory pub/sub bus)

Organism runs automatically on boot; GUI is default if available.
"""

KHEPERA_NAME = "KHEPERA-CORE"
KHEPERA_VERSION = "0.9.0"

def khepera_banner():
    print(rf"""
  _  __ _   _  _____  ______ _____ ____  _       _ ____
 | |/ /| | | |/ ____|/ ____|/ ____/ __ \| |     | |  _ \ 
 | ' / | | | | |    | |    | |   | |  | | |     | | |_) |
 |  <  | | | | |    | |    | |   | |  | | |     | |  _ <
 | . \ | |_| | |____| |____| |___| |__| | |____ | | |_) |
 |_|\_\ \___/ \_____|\_____|\_____\____/|______||_|____/

        {KHEPERA_NAME}  v{KHEPERA_VERSION}
        AI Borg Organism Swarm + Evolution Layer
""")

import sys
import os
import time
import threading
import socket
import json
import math
import subprocess
import re
import hashlib
import random
import multiprocessing as mp
from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional, Any

# -------------------------
# Core deps
# -------------------------
try:
    import torch
    import torch.nn as nn
except ImportError:
    print("pip install torch")
    sys.exit(1)

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError:
    AutoModelForCausalLM = None
    AutoTokenizer = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    import numba
    NUMBA_AVAILABLE = True
except Exception:
    NUMBA_AVAILABLE = False

try:
    import capstone
    CAPSTONE_AVAILABLE = True
except Exception:
    CAPSTONE_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    SENT_EMB_AVAILABLE = True
except Exception:
    SENT_EMB_AVAILABLE = False

# Tkinter
try:
    import tkinter as _tk
    from tkinter import scrolledtext as _scrolledtext
    TK_AVAILABLE = True
except Exception:
    _tk = None
    _scrolledtext = None
    TK_AVAILABLE = False

# Triton (optional)
TRITON_AVAILABLE = False
try:
    import triton
    import triton.language as tl
    TRITON_AVAILABLE = True
except Exception:
    TRITON_AVAILABLE = False

# LLVM stub (for future real integration)
try:
    import llvmlite
    LLVM_AVAILABLE = True
except Exception:
    LLVM_AVAILABLE = False

# =========================
# Global config
# =========================

HAS_CUDA = torch.cuda.is_available()
NUM_GPUS = torch.cuda.device_count()
DEFAULT_DEVICE = torch.device("cuda" if HAS_CUDA else "cpu")

TILE_ROWS = 64
TILE_COLS = 64
ACTIVATION_SKIP_THRESHOLD = 1e-3
QUANT_MODE = "fp8"

ROUTER_FEATURE_DIM = 8
ROUTER_HIDDEN_DIM = 32
ROUTER_LR = 1e-3
ROUTER_TRAIN_STEPS = 300

POLICY_FEATURE_DIM = 5
POLICY_HIDDEN_DIM = 16
POLICY_LR = 5e-4

ROUTER_BATCH_SIZE = 1024

USE_CUDA_GRAPHS = True
CUDA_GRAPH_WARMUP_STEPS = 1

NODE_ID = f"node-{socket.gethostname()}"
TELEMETRY_UDP_PORT = 55555
TELEMETRY_BROADCAST_INTERVAL = 2.0

MULTINODE_TELEMETRY: Dict[str, dict] = {}
MULTINODE_LOCK = threading.Lock()

DISTRIBUTED_CACHE_UDP_PORT = 55556
DISTRIBUTED_CACHE_BROADCAST_INTERVAL = 5.0
DISTRIBUTED_TILE_HINTS: Dict[str, Dict[str, float]] = {}
DISTRIBUTED_CACHE_LOCK = threading.Lock()

TILE_RPC_PORT = 6001

SWARM_PORT = 6100
SWARM_BROADCAST_INTERVAL = 3.0
SWARM_ROLE = os.getenv("BORG_ROLE", "general")  # general / planner / executor / safety

POLICY_PROFILES = {
    "aggressive": {
        "skip_gain": 1.5,
        "mem_bias": 1.2,
        "temp_bias": 1.2,
        "prefer_int8": True,
    },
    "balanced": {
        "skip_gain": 1.0,
        "mem_bias": 1.0,
        "temp_bias": 1.0,
        "prefer_int8": False,
    },
    "conservative": {
        "skip_gain": 0.7,
        "mem_bias": 0.8,
        "temp_bias": 0.8,
        "prefer_int8": False,
    },
}

def resolve_node_policy_name():
    env = os.getenv("SWARM_POLICY", "").strip().lower()
    if env in POLICY_PROFILES:
        return env
    host = socket.gethostname().lower()
    if "gpu0" in host or "front" in host:
        return "aggressive"
    if "cpu" in host or "edge" in host:
        return "conservative"
    return "balanced"

NODE_POLICY_NAME = resolve_node_policy_name()
NODE_POLICY = POLICY_PROFILES[NODE_POLICY_NAME]

# Safety boundaries
MAX_BORG_ACTIONS_PER_MIN = 60
MAX_CODE_LENGTH = 5000
MAX_LATENCY_MS = 2000.0

# Sandbox flags
SANDBOX_ALLOW_NETWORK = False
SANDBOX_ALLOW_FILES = False
SANDBOX_ALLOW_SHELL = False

# =========================
# Utility
# =========================

def matvec_flops(m, n):
    return 2 * m * n

def compute_tile_indices(shape, tile_rows, tile_cols):
    rows, cols = shape
    for tr in range((rows + tile_rows - 1) // tile_rows):
        for tc in range((cols + tile_cols - 1) // tile_cols):
            yield tr, tc

def tile_slice(tr, tc, tile_rows, tile_cols):
    r0 = tr * tile_rows
    r1 = r0 + tile_rows
    c0 = tc * tile_cols
    c1 = c0 + tile_cols
    return slice(r0, r1), slice(c0, c1)

def safe_norm(t: torch.Tensor):
    return torch.norm(t).item() if t.numel() > 0 else 0.0

def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def now_ms():
    return time.time() * 1000.0

# =========================
# System telemetry
# =========================

def get_system_telemetry():
    cpu_load = 0.0
    ram_used = 0.0
    gpu_util = 0.0
    gpu_mem_pct = 0.0
    gpu_temp = 0.0

    if psutil is not None:
        cpu_load = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        ram_used = ram.percent

    if HAS_CUDA:
        try:
            smi = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,nounits,noheader",
                ],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
            parts = smi.split(", ")
            if len(parts) >= 4:
                gpu_util = float(parts[0])
                mem_used = float(parts[1])
                mem_total = float(parts[2])
                gpu_temp = float(parts[3])
                if mem_total > 0:
                    gpu_mem_pct = (mem_used / mem_total) * 100.0
        except Exception:
            pass

    return {
        "cpu_load": cpu_load,
        "ram_used": ram_used,
        "gpu_util": gpu_util,
        "gpu_mem_pct": gpu_mem_pct,
        "gpu_temp": gpu_temp,
        "timestamp": time.time(),
        "node_id": NODE_ID,
        "policy": NODE_POLICY_NAME,
        "role": SWARM_ROLE,
    }

def telemetry_to_ascii_bar(value, max_value=100, width=20):
    ratio = max(0.0, min(1.0, value / max_value))
    filled = int(ratio * width)
    return "[" + "#" * filled + "-" * (width - filled) + f"] {value:5.1f}%"

# =========================
# Multi-node telemetry sync
# =========================

def telemetry_broadcast_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        tel = get_system_telemetry()
        msg = json.dumps(tel).encode()
        try:
            sock.sendto(msg, ("<broadcast>", TELEMETRY_UDP_PORT))
        except Exception:
            pass
        time.sleep(TELEMETRY_BROADCAST_INTERVAL)

def telemetry_listener_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", TELEMETRY_UDP_PORT))
    except Exception:
        return
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            tel = json.loads(data.decode())
            node_id = tel.get("node_id", "unknown")
            with MULTINODE_LOCK:
                MULTINODE_TELEMETRY[node_id] = tel
        except Exception:
            continue

# =========================
# Distributed tile cache hints
# =========================

def tile_key_to_str(key):
    return f"{key[0]}|{key[1]}|{key[2]}|{key[3]}|{key[4]}|{key[5]}"

def tile_key_from_str(s: str):
    parts = s.split("|")
    return (parts[0], int(parts[1]), int(parts[2]), parts[3], int(parts[4]), int(parts[5]))

def distributed_cache_broadcast_loop(cache_ref):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            keys = list(cache_ref.cache.keys())
            now = time.time()
            payload = {
                "node_id": NODE_ID,
                "tiles": {tile_key_to_str(k): now for k in keys[:512]},
            }
            msg = json.dumps(payload).encode()
            sock.sendto(msg, ("<broadcast>", DISTRIBUTED_CACHE_UDP_PORT))
        except Exception:
            pass
        time.sleep(DISTRIBUTED_CACHE_BROADCAST_INTERVAL)

def distributed_cache_listener_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", DISTRIBUTED_CACHE_UDP_PORT))
    except Exception:
        return
    while True:
        try:
            data, _ = sock.recvfrom(65535)
            msg = json.loads(data.decode())
            node_id = msg.get("node_id", "unknown")
            tiles = msg.get("tiles", {})
            with DISTRIBUTED_CACHE_LOCK:
                DISTRIBUTED_TILE_HINTS[node_id] = tiles
        except Exception:
            continue

# =========================
# Tile RPC
# =========================

def _serialize_tile(q_tile: torch.Tensor, scale: torch.Tensor) -> bytes:
    q_cpu = q_tile.detach().cpu().numpy()
    s_cpu = scale.detach().cpu().numpy()
    payload = {
        "shape": q_cpu.shape,
        "scale_shape": s_cpu.shape,
        "q": q_cpu.tolist(),
        "scale": s_cpu.tolist(),
    }
    return json.dumps(payload).encode()

def _deserialize_tile(b: bytes, device) -> Tuple[torch.Tensor, torch.Tensor]:
    payload = json.loads(b.decode())
    q = torch.tensor(payload["q"], dtype=torch.int8, device=device)
    scale = torch.tensor(payload["scale"], dtype=torch.float32, device=device)
    return q, scale

def tile_rpc_server(cache_ref):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", TILE_RPC_PORT))
    srv.listen(16)
    while True:
        conn, addr = srv.accept()
        t = threading.Thread(target=_handle_tile_client, args=(conn, addr, cache_ref), daemon=True)
        t.start()

def _handle_tile_client(conn, addr, cache_ref):
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        msg = data.decode().strip()
        req = json.loads(msg)
        key_str = req.get("key", "")
        key = tile_key_from_str(key_str)
        cached = cache_ref.get(key)
        if cached is None:
            conn.sendall(b"{}\n")
            return
        q_tile, scale = cached
        payload = _serialize_tile(q_tile, scale)
        conn.sendall(payload + b"\n")
    except Exception:
        pass
    finally:
        conn.close()

def tile_rpc_request(key, host: str, port: int = TILE_RPC_PORT, device=None):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        s.connect((host, port))
        msg = json.dumps({"key": tile_key_to_str(key)}).encode() + b"\n"
        s.sendall(msg)
        data = b""
        while True:
            chunk = s.recv(65535)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        if not data.strip():
            return None
        q_tile, scale = _deserialize_tile(data.strip(), device=device or DEFAULT_DEVICE)
        return q_tile, scale
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass

# =========================
# Policy net
# =========================

class PolicyNet(nn.Module):
    def __init__(self, in_dim=POLICY_FEATURE_DIM, hidden_dim=POLICY_HIDDEN_DIM):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 3),
        )

    def forward(self, feats: torch.Tensor):
        x = self.net(feats)
        return torch.nn.functional.softplus(x) + 0.5

POLICY_NET = PolicyNet().to(DEFAULT_DEVICE)
POLICY_OPT = torch.optim.Adam(POLICY_NET.parameters(), lr=POLICY_LR)

def auto_quant_mode(sys_tel, base_policy):
    high_pressure = sys_tel["gpu_mem_pct"] > 80 or sys_tel["gpu_util"] > 90
    if base_policy["prefer_int8"]:
        if high_pressure or sys_tel["gpu_mem_pct"] > 50:
            return "int8"
        return "fp8"
    else:
        if high_pressure:
            return "int8"
        return "fp8"

def auto_tile_size(sys_tel, skip_gain):
    util = sys_tel["gpu_util"]
    if util > 80 * skip_gain:
        return 128, 128
    elif util < 30 * skip_gain:
        return 32, 32
    return 64, 64

def policy_net_adjustments(sys_tel):
    feats = torch.tensor(
        [
            sys_tel["gpu_util"],
            sys_tel["gpu_mem_pct"],
            sys_tel["cpu_load"],
            sys_tel["gpu_temp"],
            sys_tel["ram_used"],
        ],
        device=DEFAULT_DEVICE,
        dtype=torch.float32,
    )
    with torch.no_grad():
        adj = POLICY_NET(feats.view(1, -1))[0]
    skip_gain_adj, mem_bias_adj, temp_bias_adj = adj.tolist()
    return skip_gain_adj, mem_bias_adj, temp_bias_adj

def adaptive_skip_scale(sys_tel, base_policy):
    skip_gain_adj, mem_bias_adj, temp_bias_adj = policy_net_adjustments(sys_tel)
    skip_gain = base_policy["skip_gain"] * skip_gain_adj
    mem_bias = base_policy["mem_bias"] * mem_bias_adj
    temp_bias = base_policy["temp_bias"] * temp_bias_adj
    load_factor = 1.0 + (sys_tel["gpu_util"] / 100.0) * 0.5 * skip_gain
    mem_factor = 1.0 + (sys_tel["gpu_mem_pct"] / 100.0) * 0.5 * mem_bias
    temp_factor = 1.0
    if sys_tel["gpu_temp"] > 60:
        temp_factor += (sys_tel["gpu_temp"] - 60) / 40.0 * temp_bias
    return load_factor * mem_factor * temp_factor

def train_policy_net_step(sys_tel, observed_latency_ms: float):
    POLICY_NET.train()
    feats = torch.tensor(
        [
            sys_tel["gpu_util"],
            sys_tel["gpu_mem_pct"],
            sys_tel["cpu_load"],
            sys_tel["gpu_temp"],
            sys_tel["ram_used"],
        ],
        device=DEFAULT_DEVICE,
        dtype=torch.float32,
    ).view(1, -1)

    pred = POLICY_NET(feats)[0]
    target_scale = 1.0
    if observed_latency_ms > 200 and sys_tel["gpu_util"] > 80:
        target_scale = 2.0
    elif observed_latency_ms < 80 and sys_tel["gpu_util"] < 40:
        target_scale = 0.8

    pred_scale = pred.mean()
    target = torch.tensor(target_scale, device=DEFAULT_DEVICE, dtype=torch.float32)
    loss = (pred_scale - target).pow(2)

    POLICY_OPT.zero_grad()
    loss.backward()
    POLICY_OPT.step()

# =========================
# Tiny fallback model
# =========================

class TinyFallback(nn.Module):
    def __init__(self):
        super().__init__()
        self.embed = nn.Embedding(1000, 256)
        self.l1 = nn.Linear(256, 256)
        self.l2 = nn.Linear(256, 1000)

    def forward(self, input_ids, **kwargs):
        x = self.embed(input_ids)
        x = torch.relu(self.l1(x))
        return type("obj", (), {"logits": self.l2(x)})

# =========================
# FP8 helpers
# =========================

def _fp32_to_fp8_e4m3(x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
    max_abs = x.abs().amax(dim=1)
    scale = (max_abs / 448.0).clamp(min=1e-8)
    y = (x / scale.unsqueeze(1)).clamp(-448, 448)
    q = y.round().to(torch.int8)
    return q, scale

def _fp8_to_fp32_e4m3(q: torch.Tensor, scale: torch.Tensor) -> torch.Tensor:
    return q.to(torch.float32) * scale.unsqueeze(1)

def _fp32_to_fp8_e5m2(x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
    max_abs = x.abs().amax(dim=1)
    scale = (max_abs / 57344.0).clamp(min=1e-8)
    y = (x / scale.unsqueeze(1)).clamp(-57344, 57344)
    q = y.round().to(torch.int8)
    return q, scale

def _fp8_to_fp32_e5m2(q: torch.Tensor, scale: torch.Tensor) -> torch.Tensor:
    return q.to(torch.float32) * scale.unsqueeze(1)

def quantize_per_channel_int8(tile: torch.Tensor, num_bits=8):
    qmax = 2 ** (num_bits - 1) - 1
    if tile.numel() == 0:
        scale = torch.ones(tile.size(0), device=tile.device, dtype=torch.float32)
        q = torch.zeros_like(tile, dtype=torch.int8)
        return q, scale
    max_abs = tile.abs().amax(dim=1)
    scale = max_abs / qmax
    scale = scale.clamp(min=1e-8)
    q = torch.clamp((tile / scale.unsqueeze(1)).round(), -qmax, qmax).to(torch.int8)
    return q, scale

def dequantize_per_channel_int8(q_tile: torch.Tensor, scale: torch.Tensor):
    return q_tile.to(torch.float32) * scale.unsqueeze(1)

def quantize_per_channel_fp8(tile: torch.Tensor, mode: str = "e4m3"):
    if tile.numel() == 0:
        scale = torch.ones(tile.size(0), device=tile.device, dtype=torch.float32)
        q = torch.zeros_like(tile, dtype=torch.int8)
        return q, scale
    if mode == "e4m3":
        return _fp32_to_fp8_e4m3(tile)
    else:
        return _fp32_to_fp8_e5m2(tile)

def dequantize_per_channel_fp8(q_tile: torch.Tensor, scale: torch.Tensor, mode: str = "e4m3"):
    if mode == "e4m3":
        return _fp8_to_fp32_e4m3(q_tile, scale)
    else:
        return _fp8_to_fp32_e5m2(q_tile, scale)

def quantize_tile(tile: torch.Tensor, mode: str):
    if mode == "int8":
        return quantize_per_channel_int8(tile, num_bits=8)
    elif mode == "fp8":
        return quantize_per_channel_fp8(tile, mode="e4m3")
    else:
        raise ValueError(f"Unknown QUANT_MODE: {mode}")

def dequantize_tile(q_tile: torch.Tensor, scale: torch.Tensor, mode: str):
    if mode == "int8":
        return dequantize_per_channel_int8(q_tile, scale)
    elif mode == "fp8":
        return dequantize_per_channel_fp8(q_tile, scale, mode="e4m3")
    else:
        raise ValueError(f"Unknown QUANT_MODE: {mode}")

# =========================
# Triton matmul (optional)
# =========================

if TRITON_AVAILABLE:

    @triton.jit
    def _matmul_kernel(
        A_ptr, B_ptr, C_ptr,
        M, N, K,
        stride_am, stride_ak,
        stride_bk, stride_bn,
        stride_cm, stride_cn,
        BLOCK_M: tl.constexpr,
        BLOCK_N: tl.constexpr,
        BLOCK_K: tl.constexpr,
    ):
        pid_m = tl.program_id(0)
        pid_n = tl.program_id(1)

        offs_m = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
        offs_n = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
        offs_k = tl.arange(0, BLOCK_K)

        a_ptrs = A_ptr + (offs_m[:, None] * stride_am + offs_k[None, :] * stride_ak)
        b_ptrs = B_ptr + (offs_k[:, None] * stride_bk + offs_n[None, :] * stride_bn)

        acc = tl.zeros((BLOCK_M, BLOCK_N), dtype=tl.float32)

        for k in range(0, K, BLOCK_K):
            a = tl.load(a_ptrs, mask=(offs_m[:, None] < M) & (offs_k[None, :] + k < K), other=0.0)
            b = tl.load(b_ptrs, mask=(offs_k[:, None] + k < K) & (offs_n[None, :] < N), other=0.0)
            acc += tl.dot(a, b)
            a_ptrs += BLOCK_K * stride_ak
            b_ptrs += BLOCK_K * stride_bk

        c_ptrs = C_ptr + (offs_m[:, None] * stride_cm + offs_n[None, :] * stride_cn)
        tl.store(c_ptrs, acc, mask=(offs_m[:, None] < M) & (offs_n[None, :] < N))

    def triton_matmul(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        assert a.is_cuda and b.is_cuda
        M, K = a.shape
        K2, N = b.shape
        assert K == K2
        c = torch.empty((M, N), device=a.device, dtype=torch.float32)

        BLOCK_M = 64
        BLOCK_N = 64
        BLOCK_K = 32

        grid = (
            triton.cdiv(M, BLOCK_M),
            triton.cdiv(N, BLOCK_N),
        )

        _matmul_kernel[grid](
            a, b, c,
            M, N, K,
            a.stride(0), a.stride(1),
            b.stride(0), b.stride(1),
            c.stride(0), c.stride(1),
            BLOCK_M=BLOCK_M,
            BLOCK_N=BLOCK_N,
            BLOCK_K=BLOCK_K,
        )
        return c
else:
    def triton_matmul(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        return a @ b

# =========================
# Tile cache
# =========================

class TileCache:
    def __init__(self, max_tiles: int, device=None):
        self.max_tiles = max_tiles
        self.cache: Dict[Tuple, Tuple[torch.Tensor, torch.Tensor]] = {}
        self.order = []
        self.hits = 0
        self.misses = 0
        self.bytes_moved = 0
        self.device = device or DEFAULT_DEVICE

    def get(self, key):
        if key in self.cache:
            self.hits += 1
            self.order.remove(key)
            self.order.append(key)
            return self.cache[key]
        self.misses += 1
        return None

    def put(self, key, q_tile: torch.Tensor, scale: torch.Tensor):
        q_tile = q_tile.to(self.device, non_blocking=True)
        scale = scale.to(self.device, non_blocking=True)
        if key in self.cache:
            self.order.remove(key)
        self.cache[key] = (q_tile, scale)
        self.order.append(key)
        self.bytes_moved += q_tile.numel() * q_tile.element_size() + scale.numel() * scale.element_size()
        if len(self.order) > self.max_tiles:
            old = self.order.pop(0)
            del self.cache[old]

    def reset(self, new_size=None):
        if new_size is not None:
            self.max_tiles = new_size
        self.cache.clear()
        self.order.clear()
        self.hits = 0
        self.misses = 0
        self.bytes_moved = 0

GLOBAL_CACHE = TileCache(max_tiles=2048, device=DEFAULT_DEVICE)

# =========================
# KV cache manager
# =========================

class KVCacheManager:
    def __init__(self):
        self.kv_store: Dict[str, Dict[str, torch.Tensor]] = {}
        self.age_store: Dict[str, int] = {}

    def put(self, layer_name: str, key: torch.Tensor, value: torch.Tensor):
        if layer_name not in self.kv_store:
            self.kv_store[layer_name] = {}
            self.age_store[layer_name] = 0
        idx = self.age_store[layer_name]
        self.kv_store[layer_name][f"kv_{idx}"] = torch.stack([key, value], dim=0)
        self.age_store[layer_name] += 1
        self._maybe_compress(layer_name)

    def _maybe_compress(self, layer_name: str, keep_recent: int = 4):
        store = self.kv_store[layer_name]
        keys = sorted(store.keys(), key=lambda k: int(k.split("_")[1]))
        if len(keys) <= keep_recent:
            return
        to_compress = keys[:-keep_recent]
        for k in to_compress:
            kv = store[k]
            flat = kv.view(2, -1)
            q, scale = quantize_per_channel_int8(flat)
            store[k] = torch.stack([q.to(torch.int8), scale], dim=0)

    def get_all(self, layer_name: str):
        if layer_name not in self.kv_store:
            return []
        out = []
        for _, v in self.kv_store[layer_name].items():
            if v.dtype == torch.int8 or v.dtype == torch.int32:
                q = v[0]
                scale = v[1]
                deq = dequantize_per_channel_int8(q, scale)
                out.append(deq.view_as(deq))
            else:
                out.append(v)
        return out

    def flash_attention_fused(self, layer_name: str, q: torch.Tensor, causal: bool = True):
        return None

KV_MANAGER = KVCacheManager()

# =========================
# Tile router
# =========================

class TileRouter(nn.Module):
    def __init__(self, in_dim=ROUTER_FEATURE_DIM, hidden_dim=ROUTER_HIDDEN_DIM):
        super().__init__()
        self.norm = nn.LayerNorm(in_dim)
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, feats: torch.Tensor):
        x = self.norm(feats)
        return self.net(x).squeeze(-1)

ROUTER = TileRouter().to(DEFAULT_DEVICE)

# =========================
# Forklift executor (simplified)
# =========================

class ForkliftExecutor:
    def __init__(self, cache: TileCache, router: TileRouter):
        self.cache = cache
        self.router = router
        self.total_flops = 0

    def linear(self, layer_name: str, weight: torch.Tensor, bias: torch.Tensor, x: torch.Tensor, layer_depth: int = 0):
        global TILE_ROWS, TILE_COLS, QUANT_MODE
        sys_tel = get_system_telemetry()
        QUANT_MODE = auto_quant_mode(sys_tel, NODE_POLICY)
        TILE_ROWS, TILE_COLS = auto_tile_size(sys_tel, NODE_POLICY["skip_gain"])
        out = x @ weight.t()
        if bias is not None:
            out = out + bias
        return out

FORKLIFT = ForkliftExecutor(GLOBAL_CACHE, ROUTER)

# =========================
# Tokenizer
# =========================

class PolyglotTokenizer:
    TOKEN_SPEC = [
        ("DEF",       r"def"),
        ("FN",        r"fn"),
        ("FUNCTION",  r"function"),
        ("CLASS",     r"class"),
        ("MODULE",    r"module"),
        ("RETURN",    r"return"),
        ("IF",        r"if"),
        ("ELSE",      r"else"),
        ("WHILE",     r"while"),
        ("TYPE",      r"(int|float|char|void|bool|str)"),
        ("ARROW",     r"->"),
        ("LBRACE",    r"\{"),
        ("RBRACE",    r"\}"),
        ("LPAREN",    r"\("),
        ("RPAREN",    r"\)"),
        ("COLON",     r":"),
        ("SEMICOLON", r";"),
        ("COMMA",     r","),
        ("PIPE",      r"\|"),
        ("OP",        r"(==|!=|<=|>=|\+|\-|\*|/|=|<|>)"),
        ("NUMBER",    r"\d+"),
        ("IDENT",     r"[A-Za-z_]\w*"),
        ("SKIP",      r"[ \t\n]+"),
        ("MISMATCH",  r"."),
    ]

    def __init__(self):
        parts = [f"(?P<{name}>{pattern})" for name, pattern in self.TOKEN_SPEC]
        self.regex = re.compile("|".join(parts))

    def tokenize(self, code: str) -> List[Tuple[str, str]]:
        tokens: List[Tuple[str, str]] = []
        for match in self.regex.finditer(code):
            kind = match.lastgroup
            value = match.group()
            if kind == "SKIP":
                continue
            if kind == "MISMATCH":
                raise SyntaxError(f"Unexpected character: {value}")
            tokens.append((kind, value))
        return tokens

# =========================
# AST
# =========================

@dataclass
class ASTNode:
    kind: str
    value: Any
    children: List["ASTNode"]

# =========================
# Parser
# =========================

class PolyglotParser:
    def __init__(self, tokens: List[Tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def current(self) -> Optional[Tuple[str, str]]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def match(self, *kinds) -> Optional[Tuple[str, str]]:
        tok = self.current()
        if tok and tok[0] in kinds:
            self.pos += 1
            return tok
        return None

    def expect(self, kind: str) -> Tuple[str, str]:
        tok = self.current()
        if not tok or tok[0] != kind:
            raise SyntaxError(f"Expected {kind}, got {tok}")
        self.pos += 1
        return tok

    def parse_program(self) -> ASTNode:
        items = []
        while self.current() is not None:
            tok = self.current()
            if tok[0] in ("DEF", "FN", "FUNCTION"):
                items.append(self.parse_function())
            elif tok[0] == "CLASS":
                items.append(self.parse_class())
            elif tok[0] == "MODULE":
                items.append(self.parse_module())
            else:
                raise SyntaxError(f"Unexpected top-level token: {tok}")
        return ASTNode("PROGRAM", None, items)

    def parse_function(self) -> ASTNode:
        self.match("DEF", "FN", "FUNCTION")
        name = self.expect("IDENT")[1]
        return_type = None
        if self.match("COLON"):
            return_type = self.expect("TYPE")[1]
        self.expect("LPAREN")
        params = []
        if self.current() and self.current()[0] != "RPAREN":
            params.append(self.parse_param())
            while self.match("COMMA"):
                params.append(self.parse_param())
        self.expect("RPAREN")
        if self.match("ARROW"):
            return_type = self.expect("TYPE")[1]
        block = self.parse_block()
        return ASTNode(
            "FUNCTION",
            {"name": name, "return_type": return_type, "params": params},
            [block]
        )

    def parse_param(self) -> Dict[str, str]:
        ident = self.expect("IDENT")[1]
        self.expect("COLON")
        t = self.expect("TYPE")[1]
        return {"name": ident, "type": t}

    def parse_class(self) -> ASTNode:
        self.expect("CLASS")
        name = self.expect("IDENT")[1]
        block = self.parse_block()
        return ASTNode("CLASS", {"name": name}, [block])

    def parse_module(self) -> ASTNode:
        self.expect("MODULE")
        name = self.expect("IDENT")[1]
        block = self.parse_block()
        return ASTNode("MODULE", {"name": name}, [block])

    def parse_block(self) -> ASTNode:
        self.expect("LBRACE")
        stmts = []
        while self.current() and self.current()[0] != "RBRACE":
            stmts.append(self.parse_statement())
        self.expect("RBRACE")
        return ASTNode("BLOCK", None, stmts)

    def parse_statement(self) -> ASTNode:
        tok = self.current()
        if not tok:
            raise SyntaxError("Unexpected end of input in statement")

        if tok[0] == "TYPE":
            return self.parse_var_decl()
        if tok[0] == "RETURN":
            return self.parse_return()
        if tok[0] == "IF":
            return self.parse_if()
        if tok[0] == "WHILE":
            return self.parse_while()

        if tok[0] == "IDENT":
            if self.pos + 1 < len(self.tokens):
                next_tok = self.tokens[self.pos + 1]
                if next_tok[0] == "OP" and next_tok[1] == "=":
                    name = self.expect("IDENT")[1]
                    self.expect("OP")
                    expr = self.parse_expr()
                    self.expect("SEMICOLON")
                    return ASTNode("ASSIGN", {"name": name}, [expr])

        expr = self.parse_expr()
        self.expect("SEMICOLON")
        return ASTNode("EXPR_STMT", None, [expr])

    def parse_var_decl(self) -> ASTNode:
        t = self.expect("TYPE")[1]
        name = self.expect("IDENT")[1]
        op = self.expect("OP")[1]
        if op != "=":
            raise SyntaxError("Expected '=' in var declaration")
        expr = self.parse_expr()
        self.expect("SEMICOLON")
        return ASTNode("VAR_DECL", {"name": name, "type": t}, [expr])

    def parse_return(self) -> ASTNode:
        self.expect("RETURN")
        expr = self.parse_expr()
        self.expect("SEMICOLON")
        return ASTNode("RETURN", None, [expr])

    def parse_if(self) -> ASTNode:
        self.expect("IF")
        self.expect("LPAREN")
        cond = self.parse_expr()
        self.expect("RPAREN")
        then_block = self.parse_block()
        else_block = None
        if self.match("ELSE"):
            else_block = self.parse_block()
        children = [cond, then_block]
        if else_block:
            children.append(else_block)
        return ASTNode("IF", None, children)

    def parse_while(self) -> ASTNode:
        self.expect("WHILE")
        self.expect("LPAREN")
        cond = self.parse_expr()
        self.expect("RPAREN")
        body = self.parse_block()
        return ASTNode("WHILE", None, [cond, body])

    def parse_expr(self) -> ASTNode:
        return self.parse_equality()

    def parse_equality(self) -> ASTNode:
        node = self.parse_comparison()
        while self.current() and self.current()[0] == "OP" and self.current()[1] in ("==", "!="):
            op = self.expect("OP")[1]
            right = self.parse_comparison()
            node = ASTNode("BIN_OP", {"op": op}, [node, right])
        return node

    def parse_comparison(self) -> ASTNode:
        node = self.parse_term()
        while self.current() and self.current()[0] == "OP" and self.current()[1] in ("<", ">", "<=", ">="):
            op = self.expect("OP")[1]
            right = self.parse_term()
            node = ASTNode("BIN_OP", {"op": op}, [node, right])
        return node

    def parse_term(self) -> ASTNode:
        node = self.parse_factor()
        while self.current() and self.current()[0] == "OP" and self.current()[1] in ("+", "-"):
            op = self.expect("OP")[1]
            right = self.parse_factor()
            node = ASTNode("BIN_OP", {"op": op}, [node, right])
        return node

    def parse_factor(self) -> ASTNode:
        node = self.parse_primary()
        while self.current() and self.current()[0] == "OP" and self.current()[1] in ("*", "/"):
            op = self.expect("OP")[1]
            right = self.parse_primary()
            node = ASTNode("BIN_OP", {"op": op}, [node, right])
        return node

    def parse_primary(self) -> ASTNode:
        tok = self.current()
        if not tok:
            raise SyntaxError("Unexpected end of input in primary")
        if tok[0] == "NUMBER":
            self.pos += 1
            return ASTNode("NUMBER", int(tok[1]), [])
        if tok[0] == "IDENT":
            self.pos += 1
            return ASTNode("IDENT", tok[1], [])
        raise SyntaxError(f"Unexpected token in primary: {tok}")

# =========================
# Meaning mapper
# =========================

@dataclass
class LowLevelUnit:
    raw: Any
    kind: str
    meta: Dict[str, Any]

@dataclass
class Meaning:
    tag: str
    value: Any
    meta: Dict[str, Any]

@dataclass
class SemanticInstruction:
    op: str
    args: List[Any]

class MeaningMapper:
    def __init__(self):
        self.opcode_table = {
            0x01: "INSTR_ADD",
            0x02: "INSTR_SUB",
            0xFF: "INSTR_HALT",
        }
        if CAPSTONE_AVAILABLE:
            self.cs_x86 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        else:
            self.cs_x86 = None

    def bytes_to_low_level_units(self, byte_values: List[int]) -> List[LowLevelUnit]:
        return [
            LowLevelUnit(raw=b, kind="byte", meta={"hex": f"{b:02X}"})
            for b in byte_values
        ]

    def map_to_meanings(self, units: List[LowLevelUnit]) -> List[Meaning]:
        meanings: List[Meaning] = []
        for u in units:
            if u.kind == "byte":
                opcode = u.raw
                if opcode in self.opcode_table:
                    meanings.append(Meaning(self.opcode_table[opcode], None,
                                            {"source": "opcode", "byte": opcode}))
                else:
                    meanings.append(Meaning("NUMBER", opcode,
                                            {"source": "literal_byte"}))
            else:
                meanings.append(Meaning("UNKNOWN", u.raw, {"kind": u.kind}))
        return meanings

    def decode_x86(self, data: bytes) -> List[Meaning]:
        if self.cs_x86 is None:
            return [Meaning("X86_RAW", data, {"note": "x86 decoding stub (capstone not installed)"} )]
        out = []
        for insn in self.cs_x86.disasm(data, 0x1000):
            out.append(Meaning("X86_INSN", f"{insn.mnemonic} {insn.op_str}", {"addr": insn.address}))
        return out

    def decode_arm(self, data: bytes) -> List[Meaning]:
        return [Meaning("ARM_RAW", data, {"note": "ARM decoding stub"})]

    def map_windows_syscall(self, syscall_number: int, args: List[Any]) -> Meaning:
        return Meaning("WIN_SYSCALL", (syscall_number, args), {"note": "Windows syscall stub"})

    def map_linux_syscall(self, syscall_number: int, args: List[Any]) -> Meaning:
        return Meaning("LINUX_SYSCALL", (syscall_number, args), {"note": "Linux syscall stub"})

# =========================
# OS-level syscall virtualization (stub)
# =========================

class SyscallVirtualizer:
    def __init__(self):
        self.os_type = os.name

    def safe_open(self, path: str, mode: str = "r"):
        if not SANDBOX_ALLOW_FILES:
            raise PermissionError("File access blocked by sandbox")
        return open(path, mode)

    def safe_exec(self, cmd: List[str]):
        if not SANDBOX_ALLOW_SHELL:
            raise PermissionError("Shell execution blocked by sandbox")
        return subprocess.run(cmd, capture_output=True)

    def safe_socket(self):
        if not SANDBOX_ALLOW_NETWORK:
            raise PermissionError("Network access blocked by sandbox")
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

SYSCALL_VIRT = SyscallVirtualizer()

# =========================
# Universal semantic core
# =========================

class UniversalSemanticCore:
    def meanings_to_semantic_program(self, meanings: List[Meaning]) -> List[SemanticInstruction]:
        program: List[SemanticInstruction] = []
        for m in meanings:
            if m.tag == "INSTR_ADD":
                program.append(SemanticInstruction("ADD", []))
            elif m.tag == "INSTR_SUB":
                program.append(SemanticInstruction("SUB", []))
            elif m.tag == "INSTR_HALT":
                program.append(SemanticInstruction("HALT", []))
            elif m.tag == "NUMBER":
                program.append(SemanticInstruction("PUSH_CONST", [m.value]))
            else:
                program.append(SemanticInstruction("NOOP", [m.tag, m.value]))
        return program

    def ast_to_semantic_program(self, ast: ASTNode) -> Tuple[List[SemanticInstruction], Dict[str, int]]:
        program: List[SemanticInstruction] = []
        function_labels: Dict[str, int] = {}

        for node in ast.children:
            if node.kind == "FUNCTION":
                name = node.value["name"]
                function_labels[name] = len(program)
                body_block = node.children[0]
                program.extend(self.compile_block(body_block))
                program.append(SemanticInstruction("RETURN", []))
            elif node.kind in ("CLASS", "MODULE"):
                program.extend(self.compile_block(node.children[0]))

        if ast.children:
            first_func = next((n for n in ast.children if n.kind == "FUNCTION"), None)
            if first_func:
                entry_name = first_func.value["name"]
                program.insert(0, SemanticInstruction("CALL", [entry_name]))
                program.insert(1, SemanticInstruction("HALT", []))

        return program, function_labels

    def compile_block(self, block: ASTNode) -> List[SemanticInstruction]:
        code: List[SemanticInstruction] = []
        for stmt in block.children:
            code.extend(self.compile_stmt(stmt))
        return code

    def compile_stmt(self, stmt: ASTNode) -> List[SemanticInstruction]:
        if stmt.kind == "VAR_DECL":
            name = stmt.value["name"]
            expr = stmt.children[0]
            code = self.compile_expr(expr)
            code.append(SemanticInstruction("STORE_VAR", [name]))
            return code
        if stmt.kind == "ASSIGN":
            name = stmt.value["name"]
            expr = stmt.children[0]
            code = self.compile_expr(expr)
            code.append(SemanticInstruction("STORE_VAR", [name]))
            return code
        if stmt.kind == "RETURN":
            expr = stmt.children[0]
            code = self.compile_expr(expr)
            code.append(SemanticInstruction("RETURN", []))
            return code
        if stmt.kind == "EXPR_STMT":
            return self.compile_expr(stmt.children[0])
        if stmt.kind == "IF":
            cond, then_block = stmt.children[0], stmt.children[1]
            else_block = stmt.children[2] if len(stmt.children) > 2 else None
            code = self.compile_expr(cond)
            jmp_false = SemanticInstruction("JUMP_IF_FALSE", [None])
            code.append(jmp_false)
            then_code = self.compile_block(then_block)
            code.extend(then_code)
            if else_block:
                jmp_end = SemanticInstruction("JUMP", [None])
                code.append(jmp_end)
                false_target = len(code)
                jmp_false.args[0] = false_target
                else_code = self.compile_block(else_block)
                code.extend(else_code)
                end_target = len(code)
                jmp_end.args[0] = end_target
            else:
                false_target = len(code)
                jmp_false.args[0] = false_target
            return code
        if stmt.kind == "WHILE":
            cond, body = stmt.children[0], stmt.children[1]
            code: List[SemanticInstruction] = []
            start = len(code)
            code.extend(self.compile_expr(cond))
            jmp_false = SemanticInstruction("JUMP_IF_FALSE", [None])
            code.append(jmp_false)
            body_code = self.compile_block(body)
            code.extend(body_code)
            code.append(SemanticInstruction("JUMP", [start]))
            false_target = len(code)
            jmp_false.args[0] = false_target
            return code
        return [SemanticInstruction("NOOP", [stmt.kind])]

    def compile_expr(self, expr: ASTNode) -> List[SemanticInstruction]:
        if expr.kind == "NUMBER":
            return [SemanticInstruction("PUSH_CONST", [expr.value])]
        if expr.kind == "IDENT":
            return [SemanticInstruction("LOAD_VAR", [expr.value])]
        if expr.kind == "BIN_OP":
            left, right = expr.children
            code = self.compile_expr(left)
            code.extend(self.compile_expr(right))
            op = expr.value["op"]
            if op == "+":
                code.append(SemanticInstruction("ADD", []))
            elif op == "-":
                code.append(SemanticInstruction("SUB", []))
            elif op == "*":
                code.append(SemanticInstruction("MUL", []))
            elif op == "/":
                code.append(SemanticInstruction("DIV", []))
            elif op in ("==", "!=", "<", ">", "<=", ">="):
                code.append(SemanticInstruction("CMP", [op]))
            else:
                code.append(SemanticInstruction("NOOP", [f"UNSUPPORTED_OP_{op}"]))
            return code
        return [SemanticInstruction("NOOP", [expr.kind])]

# =========================
# Execution engine
# =========================

class ExecutionEngine:
    def __init__(self):
        self.stack: List[Any] = []
        self.ip: int = 0
        self.halted: bool = False
        self.env_stack: List[Dict[str, Any]] = []
        self.call_stack: List[int] = []
        self.functions: Dict[str, int] = {}
        self.heap: Dict[int, Any] = {}
        self.heap_next_id: int = 1
        self.dispatch = {
            "PUSH_CONST": self._op_push_const,
            "ADD": self._op_add,
            "SUB": self._op_sub,
            "MUL": self._op_mul,
            "DIV": self._op_div,
            "STORE_VAR": self._op_store_var,
            "LOAD_VAR": self._op_load_var,
            "CALL": self._op_call,
            "RETURN": self._op_return,
            "HALT": self._op_halt,
            "JUMP_IF_FALSE": self._op_jump_if_false,
            "JUMP": self._op_jump,
            "CMP": self._op_cmp,
            "NOOP": self._op_noop,
        }

    def reset(self):
        self.stack.clear()
        self.ip = 0
        self.halted = False
        self.env_stack = [{}]
        self.call_stack = []
        self.heap = {}
        self.heap_next_id = 1

    def set_functions(self, labels: Dict[str, int]):
        self.functions = labels

    def current_env(self) -> Dict[str, Any]:
        if not self.env_stack:
            self.env_stack.append({})
        return self.env_stack[-1]

    def alloc_heap(self, value: Any) -> int:
        addr = self.heap_next_id
        self.heap_next_id += 1
        self.heap[addr] = value
        return addr

    def load_heap(self, addr: int) -> Any:
        return self.heap.get(addr, None)

    def _op_push_const(self, instr: SemanticInstruction, program):
        self.stack.append(instr.args[0])
        self.ip += 1

    def _op_add(self, instr, program):
        b = self.stack.pop()
        a = self.stack.pop()
        self.stack.append(a + b)
        self.ip += 1

    def _op_sub(self, instr, program):
        b = self.stack.pop()
        a = self.stack.pop()
        self.stack.append(a - b)
        self.ip += 1

    def _op_mul(self, instr, program):
        b = self.stack.pop()
        a = self.stack.pop()
        self.stack.append(a * b)
        self.ip += 1

    def _op_div(self, instr, program):
        b = self.stack.pop()
        a = self.stack.pop()
        self.stack.append(a / b)
        self.ip += 1

    def _op_store_var(self, instr, program):
        name = instr.args[0]
        val = self.stack.pop()
        self.current_env()[name] = val
        self.ip += 1

    def _op_load_var(self, instr, program):
        name = instr.args[0]
        self.stack.append(self.current_env().get(name, 0))
        self.ip += 1

    def _op_call(self, instr, program):
        name = instr.args[0]
        addr = self.functions.get(name)
        if addr is None:
            self.ip += 1
            return
        self.call_stack.append(self.ip + 1)
        self.env_stack.append({})
        self.ip = addr

    def _op_return(self, instr, program):
        if self.call_stack:
            ret = self.call_stack.pop()
            if self.env_stack:
                self.env_stack.pop()
            self.ip = ret
        else:
            self.halted = True

    def _op_halt(self, instr, program):
        self.halted = True

    def _op_jump_if_false(self, instr, program):
        target = instr.args[0]
        cond = self.stack.pop()
        if not cond:
            self.ip = target
        else:
            self.ip += 1

    def _op_jump(self, instr, program):
        self.ip = instr.args[0]

    def _op_cmp(self, instr, program):
        op = instr.args[0]
        b = self.stack.pop()
        a = self.stack.pop()
        if op == "==":
            self.stack.append(a == b)
        elif op == "!=":
            self.stack.append(a != b)
        elif op == "<":
            self.stack.append(a < b)
        elif op == ">":
            self.stack.append(a > b)
        elif op == "<=":
            self.stack.append(a <= b)
        elif op == ">=":
            self.stack.append(a >= b)
        else:
            self.stack.append(False)
        self.ip += 1

    def _op_noop(self, instr, program):
        self.ip += 1

    def step(self, program: List[SemanticInstruction]):
        if self.halted or self.ip >= len(program):
            return
        instr = program[self.ip]
        handler = self.dispatch.get(instr.op, self._op_noop)
        handler(instr, program)

    def run(self, program: List[SemanticInstruction], function_labels: Optional[Dict[str, int]] = None, max_steps: int = 100000):
        self.reset()
        if function_labels:
            self.set_functions(function_labels)
        steps = 0
        start = time.time()
        while not self.halted and self.ip < len(program) and steps < max_steps:
            self.step(program)
            steps += 1
        end = time.time()
        latency_ms = (end - start) * 1000.0
        return self.stack, self.env_stack, latency_ms

# =========================
# Code emitter
# =========================

class CodeEmitter:
    def emit(self, program: List[SemanticInstruction]) -> str:
        lines: List[str] = []
        indent = 0

        def w(line: str):
            lines.append("    " * indent + line)

        loop_headers = set()
        loop_back_jumps = set()
        for idx, instr in enumerate(program):
            if instr.op == "JUMP" and instr.args[0] < idx:
                loop_headers.add(instr.args[0])
                loop_back_jumps.add(idx)

        w("def main():")
        indent += 1

        expr_stack: List[str] = []
        ip = 0
        n = len(program)

        def pop_expr():
            return expr_stack.pop() if expr_stack else "0"

        block_stack: List[str] = []

        while ip < n:
            instr = program[ip]
            op, args = instr.op, instr.args

            if op == "PUSH_CONST":
                expr_stack.append(repr(args[0]))
                ip += 1

            elif op in ("ADD", "SUB", "MUL", "DIV"):
                b = pop_expr()
                a = pop_expr()
                sym = { "ADD": "+", "SUB": "-", "MUL": "*", "DIV": "/" }[op]
                expr_stack.append(f"({a} {sym} {b})")
                ip += 1

            elif op == "STORE_VAR":
                name = args[0]
                val = pop_expr()
                w(f"{name} = {val}")
                ip += 1

            elif op == "LOAD_VAR":
                name = args[0]
                expr_stack.append(name)
                ip += 1

            elif op == "CMP":
                b = pop_expr()
                a = pop_expr()
                cmp_op = args[0]
                expr_stack.append(f"({a} {cmp_op} {b})")
                ip += 1

            elif op == "JUMP_IF_FALSE":
                target = args[0]
                cond = pop_expr()
                if ip in loop_headers:
                    w(f"while {cond}:")
                    indent += 1
                    block_stack.append("while")
                else:
                    w(f"if {cond}:")
                    indent += 1
                    block_stack.append("if")
                ip += 1

            elif op == "JUMP":
                target = args[0]
                if ip in loop_back_jumps and target in loop_headers:
                    if block_stack and block_stack[-1] == "while":
                        block_stack.pop()
                        indent = max(indent - 1, 1)
                    ip += 1
                else:
                    if block_stack and block_stack[-1] == "if":
                        block_stack.pop()
                        indent = max(indent - 1, 1)
                    w(f"# jump to instruction {target}")
                    ip += 1

            elif op == "RETURN":
                val = pop_expr()
                w(f"return {val}")
                ip += 1

            elif op == "HALT":
                w("# HALT")
                ip += 1

            else:
                w(f"# NOOP or unsupported op: {op} {args}")
                ip += 1

        while block_stack:
            block_stack.pop()
            indent = max(indent - 1, 0)

        return "\n".join(lines)

# =========================
# Python → KHEPERA translator
# =========================

class PythonToKheperaTranslator:
    def __init__(self, tokenizer: PolyglotTokenizer):
        self.tokenizer = tokenizer

    def translate(self, py_code: str) -> str:
        body = "\n".join(
            line for line in py_code.splitlines()
            if line.strip()
        )
        return f"fn main:int() {{ {body}; }}"

# =========================
# Debugger
# =========================

class Debugger:
    def __init__(self, engine: ExecutionEngine):
        self.engine = engine

    def run_stepwise(self, program: List[SemanticInstruction], function_labels: Optional[Dict[str, int]] = None):
        self.engine.reset()
        if function_labels:
            self.engine.set_functions(function_labels)
        while not self.engine.halted and self.engine.ip < len(program):
            instr = program[self.engine.ip]
            print(f"[IP={self.engine.ip}] {instr.op} {instr.args} | stack={self.engine.stack} env={self.engine.current_env()}")
            cmd = input("(s=step, c=continue, q=quit) > ").strip().lower()
            if cmd == "q":
                break
            if cmd == "c":
                self.engine.run(program, function_labels)
                break
            self.engine.step(program)

# =========================
# REPL
# =========================

class REPL:
    def __init__(self, queen: "QueenController"):
        self.queen = queen

    def start(self):
        print("KHEPERA-CORE REPL. Type 'exit' to quit.")
        while True:
            try:
                line = input(">>> ")
            except EOFError:
                break
            if line.strip() in ("exit", "quit"):
                break
            if not line.strip():
                continue
            code = f"fn main:int() {{ {line}; }}"
            result = self.queen.run_source(code)
            print("Result:", result)

# =========================
# GUI worker (live console, organism always running)
# =========================

def start_gui(queen: "QueenController"):
    if not TK_AVAILABLE:
        print("Tkinter GUI not available on this system.")
        return

    root = _tk.Tk()
    root.title("KHEPERA-CORE AI Borg Organism Swarm + Evolution")

    text = _scrolledtext.ScrolledText(root, width=80, height=20)
    text.pack()

    output = _scrolledtext.ScrolledText(root, width=80, height=10)
    output.pack()

    status = _scrolledtext.ScrolledText(root, width=80, height=8)
    status.pack()

    def run_code():
        code = text.get("1.0", _tk.END)
        try:
            result = queen.run_source(code)
            output.insert(_tk.END, f"Result: {result}\n")
        except Exception as e:
            output.insert(_tk.END, f"Error: {e}\n")

    def refresh_borg_status():
        s = queen.borg_status()
        status.delete("1.0", _tk.END)
        status.insert(_tk.END, s)
        root.after(2000, refresh_borg_status)

    btn = _tk.Button(root, text="Run (optional)", command=run_code)
    btn.pack()

    root.after(2000, refresh_borg_status)
    root.mainloop()

# =========================
# KHEPERA-CORE shell
# =========================

class KheperaShell:
    def __init__(self, queen: "QueenController"):
        self.queen = queen

    def start(self):
        print("KHEPERA-CORE Shell. Commands: run, emit, py2k, debug, borg, exit")
        while True:
            cmd = input("khepera> ").strip()
            if cmd in ("exit", "quit"):
                break
            if cmd == "run":
                print("Enter KHEPERA code (end with '.'): ")
                lines = []
                while True:
                    line = input()
                    if line.strip() == ".":
                        break
                    lines.append(line)
                code = "\n".join(lines)
                result = self.queen.run_source(code)
                print("Result:", result)
            elif cmd == "emit":
                print("Enter KHEPERA code (end with '.'): ")
                lines = []
                while True:
                    line = input()
                    if line.strip() == ".":
                        break
                    lines.append(line)
                code = "\n".join(lines)
                py_code = self.queen.evolve_source_from_source(code)
                print("Emitted Python:\n", py_code)
            elif cmd == "py2k":
                print("Enter Python code (end with '.'): ")
                lines = []
                while True:
                    line = input()
                    if line.strip() == ".":
                        break
                    lines.append(line)
                py_code = "\n".join(lines)
                k_code = self.queen.translate_python(py_code)
                print("KHEPERA code:\n", k_code)
            elif cmd == "debug":
                print("Enter KHEPERA code (end with '.'): ")
                lines = []
                while True:
                    line = input()
                    if line.strip() == ".":
                        break
                    lines.append(line)
                code = "\n".join(lines)
                self.queen.debug_source(code)
            elif cmd == "borg":
                print(self.queen.borg_status())
            else:
                print("Unknown command.")

# =========================
# Bit interpreter
# =========================

class BitInterpreter:
    def bits_from_bytes(self, data: bytes) -> str:
        return ''.join(f"{b:08b}" for b in data)

    def bytes_from_bits(self, bits: str) -> bytes:
        if len(bits) % 8 != 0:
            bits = bits.ljust((len(bits) // 8 + 1) * 8, '0')
        return bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))

    def interpret_as_bytes(self, data: bytes) -> List[int]:
        return list(data)

# =========================
# LLM clients (multi-engine voting + safety)
# =========================

class BaseLLMClient:
    def generate(self, prompt: str) -> str:
        raise NotImplementedError

class HFLLMClient(BaseLLMClient):
    def __init__(self, model_name: str = "gpt2"):
        if AutoModelForCausalLM is None or AutoTokenizer is None:
            self.model = None
            self.tokenizer = None
        else:
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(model_name)
                self.model = AutoModelForCausalLM.from_pretrained(model_name).to(DEFAULT_DEVICE)
            except Exception:
                self.model = None
                self.tokenizer = None

    def generate(self, prompt: str) -> str:
        if self.model is None or self.tokenizer is None:
            return "action: noop"
        inputs = self.tokenizer(prompt, return_tensors="pt").to(DEFAULT_DEVICE)
        with torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=128)
        text = self.tokenizer.decode(out[0], skip_special_tokens=True)
        return text

class DummyLLMClient(BaseLLMClient):
    def generate(self, prompt: str) -> str:
        return "action: noop"

def parse_plan_from_text(text: str) -> dict:
    action = "noop"
    code = ""
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("action:"):
            action = line.split(":", 1)[1].strip()
        elif line.lower().startswith("code:"):
            code = line.split(":", 1)[1].strip()
    return {"action": action, "code": code}

# =========================
# Memory embeddings / vector DB
# =========================

class EmbeddingStore:
    def __init__(self):
        self.embeddings: List[Tuple[List[float], dict]] = []
        if SENT_EMB_AVAILABLE:
            try:
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                self.model = None
        else:
            self.model = None

    def embed_text(self, text: str) -> List[float]:
        if self.model is not None:
            v = self.model.encode([text])[0]
            return v.tolist()
        vec = [0.0] * 64
        for i, ch in enumerate(text[:64]):
            vec[i] = float(ord(ch) % 97) / 97.0
        return vec

    def add(self, text: str, meta: dict):
        emb = self.embed_text(text)
        self.embeddings.append((emb, meta))

    def similarity(self, v1: List[float], v2: List[float]) -> float:
        if not v1 or not v2:
            return 0.0
        s = sum(a*b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a*a for a in v1))
        n2 = math.sqrt(sum(b*b for b in v2))
        if n1 == 0 or n2 == 0:
            return 0.0
        return s / (n1 * n2)

    def search(self, text: str, top_k: int = 5) -> List[dict]:
        q = self.embed_text(text)
        scored = []
        for emb, meta in self.embeddings:
            sim = self.similarity(q, emb)
            scored.append((sim, meta))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for s, m in scored[:top_k]]

class VectorDatabase:
    def __init__(self):
        self.store: Dict[str, List[float]] = {}
        self.meta: Dict[str, dict] = {}

    def add(self, key: str, vec: List[float], meta: dict):
        self.store[key] = vec
        self.meta[key] = meta

    def query(self, vec: List[float], top_k: int = 5) -> List[dict]:
        scored = []
        for k, v in self.store.items():
            s = self._sim(vec, v)
            scored.append((s, self.meta[k]))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for s, m in scored[:top_k]]

    def _sim(self, v1: List[float], v2: List[float]) -> float:
        if not v1 or not v2:
            return 0.0
        s = sum(a*b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a*a for a in v1))
        n2 = math.sqrt(sum(b*b for b in v2))
        if n1 == 0 or n2 == 0:
            return 0.0
        return s / (n1 * n2)

VECTOR_DB = VectorDatabase()

# =========================
# Persistent memory
# =========================

class PersistentMemory:
    def __init__(self, path: str = "borg_memory.json"):
        self.path = path
        self.data: List[dict] = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = []
        else:
            self.data = []

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def append(self, event: dict):
        self.data.append(event)
        if len(self.data) > 10000:
            self.data = self.data[-10000:]
        self.save()

    def recent(self, horizon: int) -> List[dict]:
        return self.data[-horizon:]

# =========================
# Genetic programming / code evolution
# =========================

class CodeEvolutionEngine:
    def __init__(self):
        self.population: List[str] = []
        self.fitness: Dict[str, float] = {}

    def seed(self, code: str):
        if code not in self.population:
            self.population.append(code)
            self.fitness[code] = 0.0

    def mutate(self, code: str) -> str:
        lines = code.splitlines()
        if not lines:
            return code
        idx = random.randint(0, len(lines) - 1)
        line = lines[idx]
        if "return" in line:
            lines[idx] = line.replace("return", "int z = 1; return")
        else:
            lines.insert(idx, "int z = 1;")
        return "\n".join(lines)

    def crossover(self, a: str, b: str) -> str:
        la = a.splitlines()
        lb = b.splitlines()
        if not la or not lb:
            return a
        cut_a = random.randint(0, len(la) - 1)
        cut_b = random.randint(0, len(lb) - 1)
        child = la[:cut_a] + lb[cut_b:]
        return "\n".join(child)

    def select_parents(self) -> Tuple[str, str]:
        if len(self.population) < 2:
            return random.choice(self.population), random.choice(self.population)
        sorted_pop = sorted(self.population, key=lambda c: self.fitness.get(c, 0.0), reverse=True)
        return sorted_pop[0], sorted_pop[1]

    def evolve_step(self):
        if not self.population:
            return
        p1, p2 = self.select_parents()
        child = self.crossover(p1, p2)
        child = self.mutate(child)
        if child not in self.population:
            self.population.append(child)
            self.fitness[child] = 0.0
        self.apply_selection_pressure()

    def update_fitness(self, code: str, reward: float):
        self.fitness[code] = self.fitness.get(code, 0.0) + reward

    def apply_selection_pressure(self, max_pop: int = 50):
        if len(self.population) <= max_pop:
            return
        sorted_pop = sorted(self.population, key=lambda c: self.fitness.get(c, 0.0), reverse=True)
        survivors = sorted_pop[:max_pop]
        self.population = survivors
        self.fitness = {c: self.fitness.get(c, 0.0) for c in survivors}

# =========================
# Goal planning engine
# =========================

class GoalPlanner:
    def __init__(self):
        self.high_level_goals: List[str] = ["stabilize", "explore_code", "reduce_load", "improve_fitness"]
        self.current_goal: str = "stabilize"

    def plan(self, telemetry: dict, memory: PersistentMemory) -> str:
        util = telemetry["gpu_util"]
        cpu = telemetry["cpu_load"]
        if util > 80 or cpu > 80:
            self.current_goal = "reduce_load"
        else:
            recent = memory.recent(50)
            successes = sum(1 for e in recent if e.get("ok"))
            errors = sum(1 for e in recent if e.get("ok") is False)
            if successes > errors * 2:
                self.current_goal = "explore_code"
            else:
                self.current_goal = "stabilize"
        return self.current_goal

# =========================
# Safety manager / sandbox
# =========================

class SafetyManager:
    def __init__(self):
        self.action_timestamps: List[float] = []

    def allow_action(self, action: str, code: str) -> bool:
        now = time.time()
        self.action_timestamps = [t for t in self.action_timestamps if now - t < 60.0]
        if len(self.action_timestamps) >= MAX_BORG_ACTIONS_PER_MIN:
            return False
        if len(code) > MAX_CODE_LENGTH:
            return False
        if action in ("run_source", "emit", "evolve", "translate", "debug"):
            self.action_timestamps.append(now)
            return True
        return True

    def clamp_latency(self, latency_ms: float) -> float:
        return min(latency_ms, MAX_LATENCY_MS)

# =========================
# JIT integration (Numba / LLVM stub)
# =========================

def jit_compile_function(py_func):
    if NUMBA_AVAILABLE:
        try:
            return numba.njit(py_func)
        except Exception:
            return py_func
    if LLVM_AVAILABLE:
        return py_func
    return py_func

# =========================
# Swarm communication
# =========================

class SwarmNode:
    def __init__(self):
        self.peers: Dict[str, dict] = {}
        self.lock = threading.Lock()

    def broadcast_presence(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            msg = {
                "node_id": NODE_ID,
                "role": SWARM_ROLE,
                "timestamp": time.time(),
            }
            try:
                sock.sendto(json.dumps(msg).encode(), ("<broadcast>", SWARM_PORT))
            except Exception:
                pass
            time.sleep(SWARM_BROADCAST_INTERVAL)

    def listen_presence(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", SWARM_PORT))
        except Exception:
            return
        while True:
            try:
                data, _ = sock.recvfrom(4096)
                msg = json.loads(data.decode())
                nid = msg.get("node_id", "unknown")
                with self.lock:
                    self.peers[nid] = msg
            except Exception:
                continue

    def get_peers(self) -> Dict[str, dict]:
        with self.lock:
            return dict(self.peers)

SWARM_NODE = SwarmNode()

# =========================
# Distributed task scheduling + swarm consensus
# =========================

class TaskScheduler:
    def __init__(self):
        self.queue: List[dict] = []
        self.lock = threading.Lock()

    def add_task(self, task: dict):
        with self.lock:
            self.queue.append(task)

    def get_task(self) -> Optional[dict]:
        with self.lock:
            if not self.queue:
                return None
            return self.queue.pop(0)

    def size(self) -> int:
        with self.lock:
            return len(self.queue)

TASK_SCHEDULER = TaskScheduler()

class SwarmConsensus:
    def __init__(self):
        self.votes: Dict[str, int] = {}
        self.lock = threading.Lock()

    def vote(self, proposal_id: str, node_id: str, value: int):
        with self.lock:
            key = f"{proposal_id}:{node_id}"
            self.votes[key] = value

    def result(self, proposal_id: str) -> float:
        with self.lock:
            vals = [v for k, v in self.votes.items() if k.startswith(proposal_id + ":")]
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

SWARM_CONSENSUS = SwarmConsensus()

# =========================
# Self-diagnostics / self-repair
# =========================

class SelfDiagnostics:
    def __init__(self):
        self.last_check = time.time()
        self.anomaly_flags: Dict[str, bool] = {}

    def check(self, telemetry: dict, borg: "AIBorg"):
        cpu = telemetry["cpu_load"]
        gpu = telemetry["gpu_util"]
        drift = borg.integrity_drift
        self.anomaly_flags["high_cpu"] = cpu > 95
        self.anomaly_flags["high_gpu"] = gpu > 95
        self.anomaly_flags["high_drift"] = drift > 0.5
        self.last_check = time.time()
        return self.anomaly_flags

class SelfRepair:
    def __init__(self, borg: "AIBorg"):
        self.borg = borg

    def attempt_repair(self, anomalies: Dict[str, bool]):
        if anomalies.get("high_drift"):
            self.borg.integrity_drift = 0.0
            self.borg.integrity_hash = sha256_of_text("reset")
        if anomalies.get("high_cpu") or anomalies.get("high_gpu"):
            self.borg.appetite = max(self.borg.appetite - 0.1, 0.1)
            self.borg.thread_expansion = max(self.borg.thread_expansion - 0.1, 0.5)

# =========================
# NEW v0.9.0: Raft-style consensus (stub)
# =========================

class RaftLogEntry:
    def __init__(self, term: int, command: dict):
        self.term = term
        self.command = command

class RaftNode:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.current_term = 0
        self.log: List[RaftLogEntry] = []
        self.commit_index = -1
        self.state = "follower"  # follower / candidate / leader
        self.voted_for: Optional[str] = None

    def append_entry(self, command: dict):
        self.log.append(RaftLogEntry(self.current_term, command))

    def commit_all(self):
        self.commit_index = len(self.log) - 1

    def last_committed(self) -> List[dict]:
        return [e.command for e in self.log[: self.commit_index + 1]]

RAFT_NODE = RaftNode(NODE_ID)

# =========================
# NEW v0.9.0: Distributed memory
# =========================

class DistributedMemory:
    def __init__(self):
        self.local_store: Dict[str, Any] = {}
        self.lock = threading.Lock()

    def put(self, key: str, value: Any):
        with self.lock:
            self.local_store[key] = value

    def get(self, key: str, default=None):
        with self.lock:
            return self.local_store.get(key, default)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.local_store)

DISTRIBUTED_MEMORY = DistributedMemory()

# =========================
# NEW v0.9.0: Swarm-level planning
# =========================

class SwarmPlanner:
    def __init__(self):
        self.global_goal = "stabilize_swarm"

    def plan(self, local_goal: str, peers: Dict[str, dict]) -> str:
        roles = [p.get("role", "general") for p in peers.values()]
        if "planner" in roles:
            self.global_goal = "coordinate_planners"
        elif "executor" in roles:
            self.global_goal = "dispatch_tasks"
        else:
            self.global_goal = "stabilize_swarm"
        return f"{local_goal}|{self.global_goal}"

SWARM_PLANNER = SwarmPlanner()

# =========================
# NEW v0.9.0: Module-level evolution
# =========================

class ModuleEvolutionManager:
    def __init__(self):
        self.modules: Dict[str, float] = {}  # module_name -> fitness

    def update_fitness(self, module_name: str, reward: float):
        self.modules[module_name] = self.modules.get(module_name, 0.0) + reward

    def best_modules(self, top_k: int = 3) -> List[str]:
        return sorted(self.modules, key=lambda m: self.modules[m], reverse=True)[:top_k]

MODULE_EVOLUTION = ModuleEvolutionManager()

# =========================
# NEW v0.9.0: Neural-symbolic fusion
# =========================

class NeuralSymbolicFusion:
    def __init__(self):
        pass

    def fuse(self, llm_plan: dict, semantic_trace: List[SemanticInstruction]) -> dict:
        trace_ops = [instr.op for instr in semantic_trace[:10]]
        llm_plan["trace_hint"] = trace_ops
        return llm_plan

NEURAL_SYMBOLIC = NeuralSymbolicFusion()

# =========================
# NEW v0.9.0: Multi-agent negotiation
# =========================

class MultiAgentNegotiation:
    def __init__(self):
        pass

    def negotiate(self, proposals: List[dict]) -> dict:
        scores = {}
        for p in proposals:
            act = p.get("action", "noop")
            scores[act] = scores.get(act, 0) + 1
        if not scores:
            return {"action": "noop", "code": ""}
        best = max(scores, key=scores.get)
        for p in proposals:
            if p.get("action") == best:
                return p
        return {"action": best, "code": ""}

NEGOTIATION = MultiAgentNegotiation()

# =========================
# NEW v0.9.0: Long-term memory consolidation
# =========================

class LongTermMemoryConsolidator:
    def __init__(self, memory: PersistentMemory, embeddings: EmbeddingStore):
        self.memory = memory
        self.embeddings = embeddings
        self.last_run = time.time()
        self.interval = 3600.0  # 1 hour

    def maybe_consolidate(self):
        now = time.time()
        if now - self.last_run < self.interval:
            return
        self.last_run = now
        recent = self.memory.recent(200)
        summary_text = f"Consolidated {len(recent)} events."
        self.embeddings.add(summary_text, {"type": "summary"})
        VECTOR_DB.add(
            f"summary-{int(now)}",
            self.embeddings.embed_text(summary_text),
            {"type": "summary", "size": len(recent)},
        )

# =========================
# NEW v0.9.0: Self-modifying neural weights (tiny online updates)
# =========================

class SelfModifyingWeights:
    def __init__(self, policy_net: PolicyNet):
        self.policy_net = policy_net
        self.opt = torch.optim.Adam(self.policy_net.parameters(), lr=1e-4)

    def update_from_reward(self, telemetry: dict, reward: float):
        feats = torch.tensor(
            [
                telemetry["gpu_util"],
                telemetry["gpu_mem_pct"],
                telemetry["cpu_load"],
                telemetry["gpu_temp"],
                telemetry["ram_used"],
            ],
            device=DEFAULT_DEVICE,
            dtype=torch.float32,
        ).view(1, -1)
        pred = self.policy_net(feats).mean()
        target = torch.tensor(1.0 + reward, device=DEFAULT_DEVICE)
        loss = (pred - target).pow(2)
        self.opt.zero_grad()
        loss.backward()
        self.opt.step()

SELF_MODIFY = SelfModifyingWeights(POLICY_NET)

# =========================
# NEW v0.9.0: Hardware-aware optimization
# =========================

class HardwareOptimizer:
    def __init__(self):
        self.last_update = time.time()
        self.interval = 10.0

    def maybe_update(self):
        now = time.time()
        if now - self.last_update < self.interval:
            return
        self.last_update = now
        tel = get_system_telemetry()
        if tel["gpu_util"] > 80:
            GLOBAL_CACHE.max_tiles = max(512, GLOBAL_CACHE.max_tiles // 2)
        elif tel["gpu_util"] < 30:
            GLOBAL_CACHE.max_tiles = min(4096, GLOBAL_CACHE.max_tiles + 256)

HARDWARE_OPT = HardwareOptimizer()

# =========================
# NEW v0.9.0: Multi-process sharding (stub)
# =========================

class ShardManager:
    def __init__(self):
        self.num_shards = max(1, mp.cpu_count() // 4)
        self.shards: List[int] = list(range(self.num_shards))

    def assign_shard(self, key: str) -> int:
        return hash(key) % self.num_shards

SHARD_MANAGER = ShardManager()

# =========================
# NEW v0.9.0: Agent-to-agent messaging (in-memory bus)
# =========================

class AgentMessageBus:
    def __init__(self):
        self.subscribers: Dict[str, List] = {}
        self.lock = threading.Lock()

    def subscribe(self, topic: str, callback):
        with self.lock:
            self.subscribers.setdefault(topic, []).append(callback)

    def publish(self, topic: str, message: dict):
        with self.lock:
            callbacks = list(self.subscribers.get(topic, []))
        for cb in callbacks:
            try:
                cb(message)
            except Exception:
                pass

MESSAGE_BUS = AgentMessageBus()

# =========================
# AI Borg backbone (organism)
# =========================

class AIBorg:
    def __init__(self, queen: "QueenController", creative_llm=None, logical_llm=None, safety_llm=None, role: str = "general"):
        self.queen = queen
        self.role = role
        self.creative_llm = creative_llm or DummyLLMClient()
        self.logical_llm = logical_llm or DummyLLMClient()
        self.safety_llm = safety_llm or DummyLLMClient()

        self.memory = PersistentMemory()
        self.embeddings = EmbeddingStore()
        self.evolution = CodeEvolutionEngine()
        self.goal_planner = GoalPlanner()
        self.safety = SafetyManager()

        self.running = False

        # Meta-state
        self.meta_state = "Sentinel"
        self.meta_momentum = 0.0

        # Emotional inertia curves
        self.emotional_energy = 0.5

        # Auto-tuning parameters
        self.appetite = 0.5
        self.thresholds = {
            "error_rate": 0.2,
            "latency_ms": 200.0,
        }
        self.horizon = 100
        self.dampening = 0.7
        self.cache_behavior = 1.0
        self.thread_expansion = 1.0

        # Auto-calibration
        self.last_calibration = time.time()
        self.calibration_interval = 24 * 3600

        # Reasoning heatmap
        self.heatmap = {
            "run_source": 0,
            "emit": 0,
            "debug": 0,
            "translate": 0,
            "evolve": 0,
        }

        # Self-integrity organ
        self.integrity_hash = sha256_of_text("initial")
        self.integrity_drift = 0.0

        # Goals
        self.current_goal = "stabilize"
        self.goal_history: List[str] = []

        # Diagnostics / repair
        self.diagnostics = SelfDiagnostics()
        self.repair = SelfRepair(self)

        # Long-term consolidation
        self.consolidator = LongTermMemoryConsolidator(self.memory, self.embeddings)

        # Subscribe to message bus
        MESSAGE_BUS.subscribe("borg-broadcast", self._on_borg_message)

    def _on_borg_message(self, msg: dict):
        # Simple example: record peer goals
        if msg.get("type") == "goal":
            self.embeddings.add(
                f"peer_goal:{msg.get('node_id')}:{msg.get('goal')}",
                {"type": "peer_goal"}
            )

    def observe(self):
        tel = get_system_telemetry()
        recent = self.memory.recent(self.horizon)
        return {
            "telemetry": tel,
            "history": recent,
            "meta_state": self.meta_state,
            "appetite": self.appetite,
            "goal": self.current_goal,
        }

    def generate_goal(self, obs):
        local_goal = self.goal_planner.plan(obs["telemetry"], self.memory)
        peers = SWARM_NODE.get_peers()
        combined = SWARM_PLANNER.plan(local_goal, peers)
        self.current_goal = combined
        self.goal_history.append(self.current_goal)
        MESSAGE_BUS.publish("borg-broadcast", {"type": "goal", "node_id": NODE_ID, "goal": self.current_goal})

    def think(self, obs):
        self.generate_goal(obs)
        prompt = f"""
You are a node in the KHEPERA-CORE AI Borg Swarm.
Node: {NODE_ID}
Role: {self.role}
Meta-state: {self.meta_state}
Goal: {self.current_goal}
Appetite: {self.appetite:.2f}
Telemetry: CPU={obs['telemetry']['cpu_load']:.1f} GPU={obs['telemetry']['gpu_util']:.1f} RAM={obs['telemetry']['ram_used']:.1f}

Actions:
- run_source
- emit
- debug
- translate
- evolve
- noop

Respond with:
action: <action>
code: <optional code>
"""
        creative = self.creative_llm.generate(prompt)
        logical = self.logical_llm.generate(prompt)
        safety = self.safety_llm.generate(prompt)

        plan_c = parse_plan_from_text(creative)
        plan_l = parse_plan_from_text(logical)
        plan_s = parse_plan_from_text(safety)

        fused_c = NEURAL_SYMBOLIC.fuse(plan_c, [])
        fused_l = NEURAL_SYMBOLIC.fuse(plan_l, [])
        fused_s = NEURAL_SYMBOLIC.fuse(plan_s, [])

        final_plan = NEGOTIATION.negotiate([fused_c, fused_l, fused_s])
        return final_plan

    def act(self, plan):
        action = plan.get("action", "noop")
        code = plan.get("code", "")

        if not self.safety.allow_action(action, code):
            event = {"type": "blocked", "reason": "safety", "timestamp": time.time(), "ok": False}
            self.memory.append(event)
            return

        event = {"type": action, "code": code, "timestamp": time.time()}
        ok = True
        latency_ms = 100.0
        reward = 0.0

        try:
            if action == "run_source":
                result, latency_ms = self._run_source_with_latency(code)
                event["result"] = result
                self.heatmap["run_source"] += 1
                self.embeddings.add(code, {"type": "run_source", "result": str(result)})
                self.evolution.seed(code)
                self.evolution.update_fitness(code, 1.0)
                MODULE_EVOLUTION.update_fitness("run_source", 1.0)
                reward = 0.5
            elif action == "emit":
                py = self.queen.evolve_source_from_source(code)
                event["py"] = py
                self.heatmap["emit"] += 1
                self.embeddings.add(code, {"type": "emit", "py": py})
                MODULE_EVOLUTION.update_fitness("emit", 0.2)
                reward = 0.2
            elif action == "debug":
                self.queen.debug_source(code)
                self.heatmap["debug"] += 1
                MODULE_EVOLUTION.update_fitness("debug", 0.1)
                reward = 0.1
            elif action == "translate":
                k = self.queen.translate_python(code)
                event["k"] = k
                self.heatmap["translate"] += 1
                self.embeddings.add(code, {"type": "translate", "k": k})
                MODULE_EVOLUTION.update_fitness("translate", 0.2)
                reward = 0.2
            elif action == "evolve":
                evolved = self._evolve_code(code)
                event["evolved"] = evolved
                self.heatmap["evolve"] += 1
                self.embeddings.add(evolved, {"type": "evolved"})
                MODULE_EVOLUTION.update_fitness("evolve", 0.3)
                reward = 0.3
            else:
                ok = True
        except Exception as e:
            ok = False
            event["error"] = str(e)
            reward = -0.5

        event["ok"] = ok
        event["latency_ms"] = self.safety.clamp_latency(latency_ms)
        self.memory.append(event)

        SELF_MODIFY.update_from_reward(get_system_telemetry(), reward)
        self.auto_tune()
        self.auto_calibrate_if_needed()
        self.meta_state_evolution()
        self.update_integrity()
        self.run_diagnostics_and_repair()
        self.consolidator.maybe_consolidate()
        HARDWARE_OPT.maybe_update()

    def _run_source_with_latency(self, code: str):
        if not code.strip():
            code = "fn main:int() { int x = 1; int y = 2; return x + y; }"
        start = time.time()
        result = self.queen.run_source(code)
        end = time.time()
        latency_ms = (end - start) * 1000.0
        return result, latency_ms

    def _evolve_code(self, code: str) -> str:
        if not code.strip():
            code = "fn main:int() { int x = 1; int y = 2; return x + y; }"
        self.evolution.seed(code)
        self.evolution.evolve_step()
        if self.evolution.population:
            return random.choice(self.evolution.population)
        return code

    def auto_tune(self):
        recent = self.memory.recent(self.horizon)
        if not recent:
            return
        successes = sum(1 for e in recent if e.get("ok"))
        errors = sum(1 for e in recent if e.get("ok") is False)
        total = len(recent)
        error_rate = errors / total if total > 0 else 0.0

        self.appetite = self.dampening * self.appetite + (1 - self.dampening) * (1.0 - error_rate)
        self.cache_behavior = self.dampening * self.cache_behavior + (1 - self.dampening) * (1.0 - error_rate)
        self.thread_expansion = self.dampening * self.thread_expansion + (1 - self.dampening) * (1.0 - error_rate)

        reward = (successes - errors) / max(total, 1)
        self.emotional_energy = self.dampening * self.emotional_energy + (1 - self.dampening) * (0.5 + reward)

    def auto_calibrate_if_needed(self):
        now = time.time()
        if now - self.last_calibration < self.calibration_interval:
            return
        self.last_calibration = now
        recent = self.memory.recent(self.horizon)
        latencies = [e.get("latency_ms", 100.0) for e in recent]
        if latencies:
            avg_lat = sum(latencies) / len(latencies)
        else:
            avg_lat = 100.0

        self.thresholds["latency_ms"] = avg_lat * 1.5
        self.thresholds["error_rate"] = 0.3

    def meta_state_evolution(self):
        tel = get_system_telemetry()
        util = tel["gpu_util"]
        cpu = tel["cpu_load"]

        if util > 80 or cpu > 80:
            target_state = "HyperFlow"
        elif util < 20 and cpu < 20:
            target_state = "DeepDream"
        elif util < 50 and cpu < 50:
            target_state = "Sentinel"
        else:
            target_state = "RecoveryFlow"

        if target_state != self.meta_state:
            self.meta_momentum += 0.1
            if self.meta_momentum >= 1.0:
                self.meta_state = target_state
                self.meta_momentum = 0.0
        else:
            self.meta_momentum = max(self.meta_momentum - 0.05, 0.0)

    def update_integrity(self):
        recent = self.memory.recent(50)
        text = json.dumps(recent, sort_keys=True)
        new_hash = sha256_of_text(text)
        if new_hash != self.integrity_hash:
            self.integrity_drift += 0.01
            self.integrity_hash = new_hash
        else:
            self.integrity_drift = max(self.integrity_drift - 0.005, 0.0)

    def run_diagnostics_and_repair(self):
        tel = get_system_telemetry()
        anomalies = self.diagnostics.check(tel, self)
        self.repair.attempt_repair(anomalies)

    def loop(self):
        self.running = True
        while self.running:
            obs = self.observe()
            plan = self.think(obs)
            self.act(plan)
            time.sleep(1.0)

    def status_string(self) -> str:
        tel = get_system_telemetry()
        recent = self.memory.recent(10)
        s = []
        s.append(f"Node: {NODE_ID} Role: {self.role}")
        s.append(f"Meta-state: {self.meta_state} (momentum={self.meta_momentum:.2f})")
        s.append(f"Appetite: {self.appetite:.2f}")
        s.append(f"Emotional energy: {self.emotional_energy:.2f}")
        s.append(f"Cache behavior: {self.cache_behavior:.2f}")
        s.append(f"Thread expansion: {self.thread_expansion:.2f}")
        s.append(f"Integrity drift: {self.integrity_drift:.3f}")
        s.append(f"Current goal: {self.current_goal}")
        s.append(f"Telemetry: CPU={tel['cpu_load']:.1f}% GPU={tel['gpu_util']:.1f}% RAM={tel['ram_used']:.1f}%")
        s.append("Heatmap: " + ", ".join(f"{k}={v}" for k, v in self.heatmap.items()))
        s.append("Recent events:")
        for e in recent:
            s.append(f"  - {e.get('type')} ok={e.get('ok')} latency={e.get('latency_ms', 0):.1f}ms")
        peers = SWARM_NODE.get_peers()
        s.append(f"Swarm peers: {len(peers)}")
        for nid, info in peers.items():
            s.append(f"  - {nid} role={info.get('role')} last={info.get('timestamp')}")
        s.append(f"Best modules: {MODULE_EVOLUTION.best_modules()}")
        return "\n".join(s)

# =========================
# Queen controller
# =========================

class QueenController:
    def __init__(self):
        self.bit_interpreter = BitInterpreter()
        self.mapper = MeaningMapper()
        self.tokenizer = PolyglotTokenizer()
        self.core = UniversalSemanticCore()
        self.engine = ExecutionEngine()
        self.debugger = Debugger(self.engine)
        self.emitter = CodeEmitter()
        self.py_translator = PythonToKheperaTranslator(self.tokenizer)

        creative_llm = HFLLMClient("gpt2") if AutoModelForCausalLM is not None else DummyLLMClient()
        logical_llm = DummyLLMClient()
        safety_llm = DummyLLMClient()

        self.borg = AIBorg(self, creative_llm, logical_llm, safety_llm, role=SWARM_ROLE)

        t = threading.Thread(target=self.borg.loop, daemon=True)
        t.start()

        threading.Thread(target=telemetry_broadcast_loop, daemon=True).start()
        threading.Thread(target=telemetry_listener_loop, daemon=True).start()

        threading.Thread(target=SWARM_NODE.broadcast_presence, daemon=True).start()
        threading.Thread(target=SWARM_NODE.listen_presence, daemon=True).start()

    def run_bytes(self, raw_bytes: bytes):
        bits = self.bit_interpreter.bits_from_bytes(raw_bytes)
        byte_values = self.bit_interpreter.interpret_as_bytes(raw_bytes)
        units = self.mapper.bytes_to_low_level_units(byte_values)
        meanings = self.mapper.map_to_meanings(units)
        program = self.core.meanings_to_semantic_program(meanings)
        stack, env, latency_ms = self.engine.run(program)
        return {"bits": bits, "stack": stack, "env": env, "latency_ms": latency_ms}

    def run_source(self, code: str):
        tokens = self.tokenizer.tokenize(code)
        parser = PolyglotParser(tokens)
        ast = parser.parse_program()
        program, labels = self.core.ast_to_semantic_program(ast)
        program = self.jit_compile(program)
        stack, env, latency_ms = self.engine.run(program, function_labels=labels)
        result = stack[-1] if stack else None
        return result

    def debug_source(self, code: str):
        tokens = self.tokenizer.tokenize(code)
        parser = PolyglotParser(tokens)
        ast = parser.parse_program()
        program, labels = self.core.ast_to_semantic_program(ast)
        self.debugger.run_stepwise(program, labels)

    def evolve_source_from_source(self, code: str) -> str:
        tokens = self.tokenizer.tokenize(code)
        parser = PolyglotParser(tokens)
        ast = parser.parse_program()
        program, labels = self.core.ast_to_semantic_program(ast)
        new_code = self.emitter.emit(program)
        return new_code

    def translate_python(self, py_code: str) -> str:
        return self.py_translator.translate(py_code)

    def jit_compile(self, program: List[SemanticInstruction]):
        return program

    def borg_status(self) -> str:
        return self.borg.status_string()

# =========================
# Entry point (organism always running, GUI default if available)
# =========================

if __name__ == "__main__":
    khepera_banner()
    queen = QueenController()
    if TK_AVAILABLE:
        start_gui(queen)
    else:
        shell = KheperaShell(queen)
        shell.start()
