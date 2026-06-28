#!/usr/bin/env python3
# BORG EVOLUTION ENGINE — FULL BORG MODE
# Single-machine, 24/7, self-optimizing, adaptive, swarm-based distillation.

import os
import sys
import time
import threading
import queue
import subprocess
import traceback
import csv

REQUIRED = ["torch", "numpy", "pandas", "joblib", "psutil"]

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

# ---------------------------------------------------------
# GLOBAL STATE
# ---------------------------------------------------------
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

MAX_ROWS = 50000  # rolling window

# ---------------------------------------------------------
# SYSTEM DATA INGESTION
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

def ensure_data_file():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "cpu", "ram", "disk",
                "net_sent", "net_recv", "procs", "uptime", "target"
            ])
        print("[BORG] Created new data.csv")

def append_system_data():
    metrics = collect_system_metrics()
    target = metrics["cpu"] + metrics["ram"]
    with open(DATA_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            metrics["timestamp"],
            metrics["cpu"],
            metrics["ram"],
            metrics["disk"],
            metrics["net_sent"],
            metrics["net_recv"],
            metrics["procs"],
            metrics["uptime"],
            target
        ])

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
            metrics = collect_system_metrics()
            cpu = metrics["cpu"]
            ram = metrics["ram"]
            time_trigger = (time.time() - LAST_ROTATE) > 1800
            load_trigger = cpu > 70 or ram > 75
            if time_trigger or load_trigger:
                CURRENT_PORT += 1
                if CURRENT_PORT > 5100:
                    CURRENT_PORT = 5001
                LAST_ROTATE = time.time()
                print(f"[PORT] Rotated internal port to {CURRENT_PORT} (cpu={cpu}, ram={ram})")
            time.sleep(5)
        except Exception as e:
            print("[PORT] Exception:", e)
            time.sleep(2)

# ---------------------------------------------------------
# STREAMING INGESTION LOOP (DYNAMIC BATCH SIZE)
# ---------------------------------------------------------
def dynamic_batch_size():
    metrics = collect_system_metrics()
    cpu = metrics["cpu"]
    ram = metrics["ram"]
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
            append_system_data()
            trim_data_file()
            df = pd.read_csv(DATA_FILE)
            X = df.iloc[:, :-1].values.astype(np.float32)
            y = df.iloc[:, -1].values.astype(np.float32)
            IN_FEATURES = X.shape[1]
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
# MODELS (ADAPTIVE ARCHITECTURE)
# ---------------------------------------------------------
def make_teacher(in_features):
    hidden1 = 256
    hidden2 = 128
    hidden3 = 64
    return nn.Sequential(
        nn.Linear(in_features, hidden1),
        nn.ReLU(),
        nn.Linear(hidden1, hidden2),
        nn.ReLU(),
        nn.Linear(hidden2, hidden3),
        nn.ReLU(),
        nn.Linear(hidden3, 1)
    )

def make_student(in_features, variant=0):
    if variant == 0:
        h = 32
        act = nn.ReLU()
    elif variant == 1:
        h = 48
        act = nn.LeakyReLU(0.1)
    else:
        h = 24
        act = nn.ELU()
    return nn.Sequential(
        nn.Linear(in_features, h),
        act,
        nn.Linear(h, 1)
    )

class Teacher(nn.Module):
    def __init__(self, in_features):
        super().__init__()
        self.net = make_teacher(in_features)
    def forward(self, x):
        return self.net(x)

class Student(nn.Module):
    def __init__(self, in_features, variant=0):
        super().__init__()
        self.net = make_student(in_features, variant)
    def forward(self, x):
        return self.net(x)

# ---------------------------------------------------------
# ADAPTIVE LEARNING RATE
# ---------------------------------------------------------
def adaptive_lr(base_lr=1e-3):
    metrics = collect_system_metrics()
    cpu = metrics["cpu"]
    ram = metrics["ram"]
    factor = 1.0
    if cpu > 80 or ram > 80:
        factor = 0.5
    elif cpu < 40 and ram < 40:
        factor = 1.5
    return base_lr * factor

# ---------------------------------------------------------
# TEACHER LOOP (EMA LOSS, ADAPTIVE LR)
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
        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            print("[TEACHER] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# SWARM STUDENTS (DIVERSITY + ADAPTIVE ALPHA + MUTATION)
# ---------------------------------------------------------
def init_swarm(n=5):
    global SWARM_STUDENTS, IN_FEATURES
    SWARM_STUDENTS = []
    for i in range(n):
        variant = i % 3
        SWARM_STUDENTS.append(Student(IN_FEATURES, variant=variant))
    print(f"[SWARM] Initialized {n} diverse nodes")

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
        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            print(f"[SWARM-{node_id}] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# SNAPSHOT + AGGREGATION + DIAGNOSTICS
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
                    "port": CURRENT_PORT
                }
                dump(report, "borg_evolution_report.joblib")
                print("[SNAPSHOT] Saved artifacts", report)
        except Exception as e:
            print("[SNAPSHOT] Exception:", e)
            traceback.print_exc()

# ---------------------------------------------------------
# WATCHDOG + AUTO-HEAL
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
    return [t_stream, t_teacher, *swarm_threads, t_snapshot, t_port]

def reset_state():
    global DATA_QUEUE, STOP_FLAG, TEACHER, SWARM_STUDENTS, IN_FEATURES
    with CONTROL_LOCK:
        DATA_QUEUE = queue.Queue(maxsize=4000)
        STOP_FLAG = False
        TEACHER = None
        SWARM_STUDENTS = []
        IN_FEATURES = None
        print("[WATCHDOG] State reset")

def watchdog_loop():
    print("=== BORG EVOLUTION ENGINE — WATCHDOG MODE ===")
    while True:
        try:
            threads = start_threads()
            print("[WATCHDOG] Threads running")
            last_update = time.time()
            while True:
                time.sleep(5)
                if EMA_TEACHER_LOSS is not None or EMA_SWARM_LOSS is not None:
                    last_update = time.time()
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
