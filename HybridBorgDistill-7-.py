#!/usr/bin/env python3
# BORG PERFORMANCE GOVERNOR — GAMING + SERVER OPTIMIZER

import os
import sys
import time
import threading
import queue
import subprocess
import traceback
import csv

REQUIRED = ["torch", "numpy", "pandas", "joblib", "psutil", "flask"]

def install_missing():
    for pkg in REQUIRED:
        try:
            __import__(pkg)
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", pkg])

install_missing()

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import pandas as pd
import psutil
from joblib import dump
from flask import Flask, jsonify

# optional GPU support
try:
    import pynvml
    pynvml.nvmlInit()
    GPU_AVAILABLE = True
except Exception:
    GPU_AVAILABLE = False

DATA_QUEUE = queue.Queue(maxsize=4000)
CONTROL_LOCK = threading.Lock()
STOP_FLAG = False

TEACHER = None
SWARM_STUDENTS = []
IN_FEATURES = None

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.csv")

CURRENT_PORT = 5001
LAST_ROTATE = time.time()

EMA_TEACHER_LOSS = None
EMA_SWARM_LOSS = None

MAX_ROWS = 50000

LATEST_STATUS = {
    "ema_teacher_loss": None,
    "ema_swarm_loss": None,
    "samples": 0,
    "current_port": CURRENT_PORT,
    "last_update": None,
    "last_perf_score": None,
    "last_bottlenecks": [],
    "last_actions": []
}

GAME_PROCESS_HINTS = [
    "steam.exe", "cs2.exe", "valorant.exe", "fortnite.exe",
    "eldenring.exe", "wow.exe", "leagueoflegends.exe"
]

SERVER_PROCESS_HINTS = [
    "nginx", "apache2", "httpd", "mysqld", "postgres",
    "redis-server", "node", "dotnet", "java"
]

PING_TARGET = "8.8.8.8"

# ---------------------------------------------------------
# BASE SYSTEM METRICS
# ---------------------------------------------------------
def collect_system_metrics():
    return {
        "timestamp": time.time(),
        "cpu": psutil.cpu_percent(interval=0),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "net_sent": psutil.net_io_counters().bytes_sent,
        "net_recv": psutil.net_io_counters().bytes_recv,
        "procs": len(psutil.pids()),
        "uptime": time.time() - psutil.boot_time()
    }

def get_gpu_usage():
    if not GPU_AVAILABLE:
        return 0.0
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return float(util.gpu)
    except Exception:
        return 0.0

def get_process_usage(hints):
    cpu_total = 0.0
    ram_total = 0.0
    count = 0
    names = []
    for p in psutil.process_iter(attrs=["name", "cpu_percent", "memory_percent", "pid"]):
        try:
            name = (p.info["name"] or "").lower()
            if any(h.lower() in name for h in hints):
                cpu_total += p.info["cpu_percent"]
                ram_total += p.info["memory_percent"]
                count += 1
                names.append((p.info["pid"], name, p.info["cpu_percent"], p.info["memory_percent"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {
        "cpu": cpu_total,
        "ram": ram_total,
        "count": count,
        "procs": names
    }

def measure_latency(host=PING_TARGET):
    try:
        start = time.time()
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect((host, 80))
        s.close()
        return (time.time() - start) * 1000.0
    except Exception:
        return 0.0

def collect_external_sensors():
    gpu = get_gpu_usage()
    game = get_process_usage(GAME_PROCESS_HINTS)
    server = get_process_usage(SERVER_PROCESS_HINTS)
    latency = measure_latency(PING_TARGET)
    fps = 0.0  # placeholder; can be wired to real overlay later
    return {
        "gpu_usage": gpu,
        "game_cpu": game["cpu"],
        "game_ram": game["ram"],
        "game_count": game["count"],
        "server_cpu": server["cpu"],
        "server_ram": server["ram"],
        "server_count": server["count"],
        "net_latency_ms": latency,
        "fps": fps,
        "game_procs": game["procs"],
        "server_procs": server["procs"]
    }

# ---------------------------------------------------------
# FEATURE VECTOR + DATA FILE
# ---------------------------------------------------------
def build_feature_vector():
    sys_metrics = collect_system_metrics()
    ext = collect_external_sensors()
    features = {
        "timestamp": sys_metrics["timestamp"],
        "cpu": sys_metrics["cpu"],
        "ram": sys_metrics["ram"],
        "disk": sys_metrics["disk"],
        "net_sent": sys_metrics["net_sent"],
        "net_recv": sys_metrics["net_recv"],
        "procs": sys_metrics["procs"],
        "uptime": sys_metrics["uptime"],
        "gpu_usage": ext["gpu_usage"],
        "game_cpu": ext["game_cpu"],
        "game_ram": ext["game_ram"],
        "server_cpu": ext["server_cpu"],
        "server_ram": ext["server_ram"],
        "net_latency_ms": ext["net_latency_ms"],
        "fps": ext["fps"]
    }
    return features, ext

def ensure_data_file():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "cpu", "ram", "disk",
                "net_sent", "net_recv", "procs", "uptime",
                "gpu_usage", "game_cpu", "game_ram",
                "server_cpu", "server_ram", "net_latency_ms",
                "fps", "perf_score"
            ])
        print("[BORG] Created new data.csv")

def compute_perf_score(features):
    # simple heuristic: higher is better
    # penalize high cpu/ram/gpu/latency, reward fps
    cpu = features["cpu"]
    ram = features["ram"]
    gpu = features["gpu_usage"]
    latency = features["net_latency_ms"]
    fps = features["fps"]
    score = 100.0
    score -= 0.3 * cpu
    score -= 0.3 * ram
    score -= 0.2 * gpu
    score -= 0.05 * latency
    score += 0.5 * fps
    return max(score, 0.0)

def append_system_data():
    features, ext = build_feature_vector()
    perf_score = compute_perf_score(features)
    with open(DATA_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            features["timestamp"],
            features["cpu"],
            features["ram"],
            features["disk"],
            features["net_sent"],
            features["net_recv"],
            features["procs"],
            features["uptime"],
            features["gpu_usage"],
            features["game_cpu"],
            features["game_ram"],
            features["server_cpu"],
            features["server_ram"],
            features["net_latency_ms"],
            features["fps"],
            perf_score
        ])
    LATEST_STATUS["last_perf_score"] = perf_score
    return features, ext, perf_score

def trim_data_file():
    try:
        df = pd.read_csv(DATA_FILE)
        if len(df) > MAX_ROWS:
            df = df.iloc[-MAX_ROWS:]
            df.to_csv(DATA_FILE, index=False)
            print(f"[DATA] Trimmed data.csv to {MAX_ROWS} rows")
    except Exception as e:
        print("[DATA] Trim exception:", e)

# ---------------------------------------------------------
# SAFE LOCAL PORT ROTATION
# ---------------------------------------------------------
def port_rotation_loop():
    global CURRENT_PORT, LAST_ROTATE
    print("[PORT] Port rotation thread started")
    while not STOP_FLAG:
        try:
            m = collect_system_metrics()
            cpu = m["cpu"]
            ram = m["ram"]
            time_trigger = (time.time() - LAST_ROTATE) > 1800
            load_trigger = cpu > 80 or ram > 85
            if time_trigger or load_trigger:
                CURRENT_PORT += 1
                if CURRENT_PORT > 5100:
                    CURRENT_PORT = 5001
                LAST_ROTATE = time.time()
                LATEST_STATUS["current_port"] = CURRENT_PORT
                print(f"[PORT] Rotated internal port to {CURRENT_PORT} (cpu={cpu}, ram={ram})")
            time.sleep(5)
        except Exception as e:
            print("[PORT] Exception:", e)
            time.sleep(2)

# ---------------------------------------------------------
# STREAMING INGESTION LOOP
# ---------------------------------------------------------
def dynamic_batch_size():
    m = collect_system_metrics()
    cpu = m["cpu"]
    ram = m["ram"]
    if cpu < 40 and ram < 50:
        return 128
    elif cpu < 70 and ram < 70:
        return 64
    else:
        return 32

def streaming_ingestion_loop():
    global STOP_FLAG, IN_FEATURES
    ensure_data_file()
    print("[STREAM] Streaming ingestion started")
    while not STOP_FLAG:
        try:
            features, ext, perf_score = append_system_data()
            trim_data_file()
            df = pd.read_csv(DATA_FILE)
            X = df.iloc[:, :-1].values.astype(np.float32)
            y = df.iloc[:, -1].values.astype(np.float32)
            IN_FEATURES = X.shape[1]
            LATEST_STATUS["samples"] = len(X)
            idx = np.random.permutation(len(X))
            X = X[idx]
            y = y[idx]
            batch_size = dynamic_batch_size()
            for i in range(0, len(X), batch_size):
                xb = X[i:i+batch_size]
                yb = y[i:i+batch_size]
                try:
                    DATA_QUEUE.put((xb, yb), timeout=1.0)
                except queue.Full:
                    pass
            time.sleep(0.5)
        except Exception as e:
            print("[STREAM] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# MODELS (UNIFORM ARCHITECTURE TO AVOID SHAPE MISMATCH)
# ---------------------------------------------------------
class Teacher(nn.Module):
    def __init__(self, in_features):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_features, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 1)
        )
    def forward(self, x):
        return self.net(x)

class Student(nn.Module):
    def __init__(self, in_features):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_features, 32),
            nn.ReLU(),
            nn.Linear(32, 1)
        )
    def forward(self, x):
        return self.net(x)

# ---------------------------------------------------------
# ADAPTIVE LR
# ---------------------------------------------------------
def adaptive_lr(base_lr=1e-3):
    m = collect_system_metrics()
    cpu = m["cpu"]
    ram = m["ram"]
    factor = 1.0
    if cpu > 80 or ram > 80:
        factor = 0.5
    elif cpu < 40 and ram < 40:
        factor = 1.5
    return base_lr * factor

# ---------------------------------------------------------
# TEACHER LOOP
# ---------------------------------------------------------
def teacher_loop():
    global TEACHER, IN_FEATURES, STOP_FLAG, EMA_TEACHER_LOSS
    print("[TEACHER] Teacher thread started")
    while IN_FEATURES is None and not STOP_FLAG:
        time.sleep(0.5)
    TEACHER = Teacher(IN_FEATURES)
    criterion = nn.MSELoss()
    opt = optim.Adam(TEACHER.parameters(), lr=adaptive_lr(1e-3))
    ema_alpha = 0.9
    while not STOP_FLAG:
        try:
            xb, yb = DATA_QUEUE.get(timeout=2.0)
            xb_t = torch.tensor(xb)
            yb_t = torch.tensor(yb).reshape(-1, 1)
            TEACHER.train()
            preds = TEACHER(xb_t)
            loss = criterion(preds, yb_t)
            opt.param_groups[0]["lr"] = adaptive_lr(1e-3)
            opt.zero_grad()
            loss.backward()
            opt.step()
            loss_val = float(loss.item())
            if EMA_TEACHER_LOSS is None:
                EMA_TEACHER_LOSS = loss_val
            else:
                EMA_TEACHER_LOSS = ema_alpha * EMA_TEACHER_LOSS + (1 - ema_alpha) * loss_val
            LATEST_STATUS["ema_teacher_loss"] = EMA_TEACHER_LOSS
            LATEST_STATUS["last_update"] = time.time()
        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            print("[TEACHER] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# SWARM STUDENTS (UNIFORM, SAFE)
# ---------------------------------------------------------
def init_swarm(n=5):
    global SWARM_STUDENTS, IN_FEATURES
    SWARM_STUDENTS = [Student(IN_FEATURES) for _ in range(n)]
    print(f"[SWARM] Initialized {n} nodes")

def adaptive_alpha():
    if EMA_TEACHER_LOSS is None:
        return 0.5
    if EMA_TEACHER_LOSS > 5.0:
        return 0.7
    elif EMA_TEACHER_LOSS < 1.0:
        return 0.3
    else:
        return 0.5

def mutate_student(student):
    with torch.no_grad():
        for p in student.parameters():
            if torch.rand(1).item() < 0.05:
                noise = torch.randn_like(p) * 0.01
                p.add_(noise)

def student_node_loop(node_id):
    global TEACHER, SWARM_STUDENTS, STOP_FLAG, EMA_SWARM_LOSS
    print(f"[SWARM-{node_id}] Node started")
    criterion = nn.MSELoss()
    opt = optim.Adam(SWARM_STUDENTS[node_id].parameters(), lr=adaptive_lr(1e-3))
    ema_alpha = 0.9
    while TEACHER is None and not STOP_FLAG:
        time.sleep(0.5)
    while not STOP_FLAG:
        try:
            xb, yb = DATA_QUEUE.get(timeout=2.0)
            xb_t = torch.tensor(xb)
            yb_t = torch.tensor(yb).reshape(-1, 1)
            with torch.no_grad():
                teacher_soft = TEACHER(xb_t)
            preds = SWARM_STUDENTS[node_id](xb_t)
            alpha = adaptive_alpha()
            loss_teacher = criterion(preds, teacher_soft)
            loss_true = criterion(preds, yb_t)
            loss = alpha * loss_teacher + (1 - alpha) * loss_true
            opt.param_groups[0]["lr"] = adaptive_lr(1e-3)
            opt.zero_grad()
            loss.backward()
            opt.step()
            mutate_student(SWARM_STUDENTS[node_id])
            loss_val = float(loss.item())
            if EMA_SWARM_LOSS is None:
                EMA_SWARM_LOSS = loss_val
            else:
                EMA_SWARM_LOSS = ema_alpha * EMA_SWARM_LOSS + (1 - ema_alpha) * loss_val
            LATEST_STATUS["ema_swarm_loss"] = EMA_SWARM_LOSS
            LATEST_STATUS["last_update"] = time.time()
        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            print(f"[SWARM-{node_id}] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# BOTTLENECK DETECTION + RECOMMENDATIONS + SAFE ACTIONS
# ---------------------------------------------------------
def detect_bottlenecks(features, ext, perf_score):
    bottlenecks = []
    if features["cpu"] > 85:
        bottlenecks.append("High CPU load")
    if features["ram"] > 85:
        bottlenecks.append("High RAM usage")
    if features["gpu_usage"] > 90:
        bottlenecks.append("GPU saturated")
    if features["net_latency_ms"] > 80:
        bottlenecks.append("High network latency")
    if ext["game_cpu"] > 150:
        bottlenecks.append("Game processes heavy CPU")
    if ext["server_cpu"] > 150:
        bottlenecks.append("Server processes heavy CPU")
    if perf_score < 40:
        bottlenecks.append("Overall performance degraded")
    return bottlenecks

def recommend_actions(features, ext, bottlenecks):
    actions = []
    if "High CPU load" in bottlenecks:
        actions.append("Lower priority of background CPU-heavy processes.")
    if "High RAM usage" in bottlenecks:
        actions.append("Close unused apps or browser tabs to free RAM.")
    if "GPU saturated" in bottlenecks:
        actions.append("Reduce in-game graphics settings or close GPU-heavy apps.")
    if "High network latency" in bottlenecks:
        actions.append("Check network usage, pause downloads/streams, or switch to wired connection.")
    if "Game processes heavy CPU" in bottlenecks:
        actions.append("Limit background tasks while gaming; focus resources on game.")
    if "Server processes heavy CPU" in bottlenecks:
        actions.append("Consider moving some server workloads or limiting concurrent jobs.")
    if "Overall performance degraded" in bottlenecks:
        actions.append("Apply a balanced performance profile and reduce non-essential workloads.")
    return actions

def apply_safe_controls(ext, bottlenecks):
    actions_taken = []
    # VERY conservative: only lower priority of obvious background processes
    try:
        if "High CPU load" in bottlenecks or "Game processes heavy CPU" in bottlenecks:
            for pid, name, cpu, mem in ext["server_procs"]:
                try:
                    p = psutil.Process(pid)
                    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
                    actions_taken.append(f"Lowered priority of server process {name} (pid={pid}).")
                except Exception:
                    continue
    except Exception:
        pass
    return actions_taken

# ---------------------------------------------------------
# SNAPSHOT + AGGREGATION
# ---------------------------------------------------------
def snapshot_loop():
    global TEACHER, SWARM_STUDENTS, STOP_FLAG
    print("[SNAPSHOT] Snapshot thread started")
    while not STOP_FLAG:
        time.sleep(60)
        try:
            if TEACHER is None or len(SWARM_STUDENTS) == 0:
                continue
            with CONTROL_LOCK:
                states = [s.state_dict() for s in SWARM_STUDENTS]
                avg_state = {}
                for key in states[0].keys():
                    avg_state[key] = sum(sd[key] for sd in states) / len(states)
                for s in SWARM_STUDENTS:
                    s.load_state_dict(avg_state)
                torch.save(TEACHER.state_dict(), "borg_teacher.pt")
                torch.save(avg_state, "borg_swarm_student.pt")
                report = {
                    "timestamp": time.time(),
                    "ema_teacher_loss": EMA_TEACHER_LOSS,
                    "ema_swarm_loss": EMA_SWARM_LOSS,
                    "port": CURRENT_PORT,
                    "samples": LATEST_STATUS["samples"],
                    "last_perf_score": LATEST_STATUS["last_perf_score"]
                }
                dump(report, "borg_perf_report.joblib")
                print("[SNAPSHOT] Saved artifacts", report)
        except Exception as e:
            print("[SNAPSHOT] Exception:", e)
            traceback.print_exc()

# ---------------------------------------------------------
# WEB STATUS + RECOMMENDATIONS
# ---------------------------------------------------------
app = Flask(__name__)

@app.route("/status")
def status():
    return jsonify({
        "ema_teacher_loss": LATEST_STATUS["ema_teacher_loss"],
        "ema_swarm_loss": LATEST_STATUS["ema_swarm_loss"],
        "samples": LATEST_STATUS["samples"],
        "current_port": LATEST_STATUS["current_port"],
        "last_update": LATEST_STATUS["last_update"],
        "last_perf_score": LATEST_STATUS["last_perf_score"],
        "last_bottlenecks": LATEST_STATUS["last_bottlenecks"],
        "last_actions": LATEST_STATUS["last_actions"],
        "time": time.time()
    })

@app.route("/recommendations")
def recommendations():
    return jsonify({
        "bottlenecks": LATEST_STATUS["last_bottlenecks"],
        "actions": LATEST_STATUS["last_actions"],
        "perf_score": LATEST_STATUS["last_perf_score"],
        "time": time.time()
    })

def web_status_loop(port=5000):
    print(f"[WEB] Status endpoint on port {port} at /status and /recommendations")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ---------------------------------------------------------
# GOVERNOR LOOP (PERF ANALYSIS + CONTROL)
# ---------------------------------------------------------
def governor_loop():
    print("[GOVERNOR] Performance governor started")
    while not STOP_FLAG:
        try:
            features, ext, perf_score = append_system_data()
            bottlenecks = detect_bottlenecks(features, ext, perf_score)
            actions = recommend_actions(features, ext, bottlenecks)
            actions_taken = apply_safe_controls(ext, bottlenecks)
            LATEST_STATUS["last_bottlenecks"] = bottlenecks
            LATEST_STATUS["last_actions"] = actions + actions_taken
            time.sleep(5)
        except Exception as e:
            print("[GOVERNOR] Exception:", e)
            traceback.print_exc()
            time.sleep(5)

# ---------------------------------------------------------
# WATCHDOG
# ---------------------------------------------------------
def start_threads():
    global STOP_FLAG
    STOP_FLAG = False
    t_stream = threading.Thread(target=streaming_ingestion_loop, daemon=True)
    t_stream.start()
    t_teacher = threading.Thread(target=teacher_loop, daemon=True)
    t_teacher.start()
    while IN_FEATURES is None and not STOP_FLAG:
        time.sleep(0.5)
    init_swarm(5)
    swarm_threads = []
    for i in range(len(SWARM_STUDENTS)):
        t = threading.Thread(target=student_node_loop, args=(i,), daemon=True)
        t.start()
        swarm_threads.append(t)
    t_snapshot = threading.Thread(target=snapshot_loop, daemon=True)
    t_snapshot.start()
    t_port = threading.Thread(target=port_rotation_loop, daemon=True)
    t_port.start()
    t_web = threading.Thread(target=web_status_loop, daemon=True)
    t_web.start()
    t_gov = threading.Thread(target=governor_loop, daemon=True)
    t_gov.start()
    return [t_stream, t_teacher, *swarm_threads, t_snapshot, t_port, t_web, t_gov]

def reset_state():
    global DATA_QUEUE, STOP_FLAG, TEACHER, SWARM_STUDENTS, IN_FEATURES
    with CONTROL_LOCK:
        DATA_QUEUE = queue.Queue(maxsize=4000)
        STOP_FLAG = False
        TEACHER = None
        SWARM_STUDENTS = []
        IN_FEATURES = None
        LATEST_STATUS["last_bottlenecks"] = []
        LATEST_STATUS["last_actions"] = []
        print("[WATCHDOG] State reset")

def watchdog_loop():
    print("=== BORG PERFORMANCE GOVERNOR — WATCHDOG MODE ===")
    while True:
        try:
            threads = start_threads()
            print("[WATCHDOG] Threads running")
            last_update = time.time()
            while True:
                time.sleep(5)
                if LATEST_STATUS["last_update"] is not None:
                    last_update = LATEST_STATUS["last_update"]
                if time.time() - last_update > 600:
                    print("[WATCHDOG] Stalled evolution detected, resetting")
                    break
        except Exception as e:
            print("[WATCHDOG] Exception:", e)
            traceback.print_exc()
        print("[WATCHDOG] Restarting system")
        reset_state()
        time.sleep(3)

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
if __name__ == "__main__":
    watchdog_loop()
