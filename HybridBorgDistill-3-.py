#!/usr/bin/env python3
# HYBRID BORG SWARM DISTILLATION DAEMON — CROSS-PLATFORM SYSTEM DATA INGESTION
# 24/7 autonomous, multi-threaded, swarm distillation, watchdog, auto-recovery.
# NOW WITH SAFE AUTO-PORT ROTATION (NO NETWORKING)

import os
import sys
import time
import threading
import queue
import subprocess
import traceback
import csv
import psutil
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import pandas as pd
from joblib import dump

# ---------------------------------------------------------
# GLOBAL STATE
# ---------------------------------------------------------
DATA_QUEUE = queue.Queue(maxsize=2000)
CONTROL_LOCK = threading.Lock()
STOP_FLAG = False
CURRENT_DATA_MTIME = None

TEACHER = None
SWARM_STUDENTS = []
IN_FEATURES = None

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.csv")

# NEW: SAFE LOCAL PORT ROTATION
CURRENT_PORT = 5001
LAST_ROTATE = time.time()

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

# ---------------------------------------------------------
# SAFE AUTO PORT ROTATION (NO NETWORKING)
# ---------------------------------------------------------
def port_rotation_loop():
    global CURRENT_PORT, LAST_ROTATE

    print("[PORT] Port rotation thread started")

    while not STOP_FLAG:
        try:
            metrics = collect_system_metrics()
            cpu = metrics["cpu"]
            ram = metrics["ram"]

            time_trigger = (time.time() - LAST_ROTATE) > 1800  # 30 min
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
# STREAMING INGESTION LOOP
# ---------------------------------------------------------
def streaming_ingestion_loop(batch_size=64):
    global STOP_FLAG
    ensure_data_file()
    print("[STREAM] Streaming ingestion started")

    while not STOP_FLAG:
        try:
            append_system_data()

            df = pd.read_csv(DATA_FILE)
            X = df.iloc[:, :-1].values.astype(np.float32)
            y = df.iloc[:, -1].values.astype(np.float32)

            IN_FEATURES = X.shape[1]

            idx = np.random.permutation(len(X))
            X = X[idx]
            y = y[idx]

            for i in range(0, len(X), batch_size):
                xb = X[i:i+batch_size]
                yb = y[i:i+batch_size]

                try:
                    DATA_QUEUE.put((xb, yb), timeout=1.0)
                except queue.Full:
                    pass

            time.sleep(1.0)

        except Exception as e:
            print("[STREAM] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# MODELS
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
# TEACHER LOOP
# ---------------------------------------------------------
def teacher_loop():
    global TEACHER, IN_FEATURES, STOP_FLAG

    print("[TEACHER] Teacher thread started")

    while IN_FEATURES is None:
        time.sleep(0.5)

    TEACHER = Teacher(IN_FEATURES)
    criterion = nn.MSELoss()
    opt = optim.Adam(TEACHER.parameters(), lr=1e-3)

    while not STOP_FLAG:
        try:
            xb, yb = DATA_QUEUE.get(timeout=2.0)
            xb_t = torch.tensor(xb)
            yb_t = torch.tensor(yb).reshape(-1, 1)

            TEACHER.train()
            preds = TEACHER(xb_t)
            loss = criterion(preds, yb_t)

            opt.zero_grad()
            loss.backward()
            opt.step()

        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            print("[TEACHER] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# SWARM STUDENTS
# ---------------------------------------------------------
def init_swarm(n=3):
    global SWARM_STUDENTS, IN_FEATURES
    SWARM_STUDENTS = [Student(IN_FEATURES) for _ in range(n)]
    print(f"[SWARM] Initialized {n} nodes")

def student_node_loop(node_id, alpha=0.5):
    global TEACHER, SWARM_STUDENTS, STOP_FLAG

    print(f"[SWARM-{node_id}] Node started")

    criterion = nn.MSELoss()
    opt = optim.Adam(SWARM_STUDENTS[node_id].parameters(), lr=1e-3)

    while TEACHER is None:
        time.sleep(0.5)

    while not STOP_FLAG:
        try:
            xb, yb = DATA_QUEUE.get(timeout=2.0)
            xb_t = torch.tensor(xb)
            yb_t = torch.tensor(yb).reshape(-1, 1)

            with torch.no_grad():
                teacher_soft = TEACHER(xb_t)

            preds = SWARM_STUDENTS[node_id](xb_t)

            loss_teacher = criterion(preds, teacher_soft)
            loss_true = criterion(preds, yb_t)
            loss = alpha * loss_teacher + (1 - alpha) * loss_true

            opt.zero_grad()
            loss.backward()
            opt.step()

        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            print(f"[SWARM-{node_id}] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# SNAPSHOT LOOP
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
                print("[SNAPSHOT] Aggregating swarm")

                states = [s.state_dict() for s in SWARM_STUDENTS]
                avg_state = {}

                for key in states[0].keys():
                    avg_state[key] = sum(sd[key] for sd in states) / len(states)

                for s in SWARM_STUDENTS:
                    s.load_state_dict(avg_state)

                torch.save(TEACHER.state_dict(), "borg_teacher.pt")
                torch.save(avg_state, "borg_swarm_student.pt")

                dump({"timestamp": time.time(), "port": CURRENT_PORT}, "borg_report.joblib")

                print("[SNAPSHOT] Saved artifacts")

        except Exception as e:
            print("[SNAPSHOT] Exception:", e)
            traceback.print_exc()

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

    while IN_FEATURES is None:
        time.sleep(0.5)

    init_swarm(3)

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
        DATA_QUEUE = queue.Queue(maxsize=2000)
        STOP_FLAG = False
        TEACHER = None
        SWARM_STUDENTS = []
        IN_FEATURES = None
        print("[WATCHDOG] State reset")

def watchdog_loop():
    print("=== HYBRID BORG SWARM DAEMON — WATCHDOG MODE ===")

    while True:
        try:
            threads = start_threads()
            print("[WATCHDOG] Threads running")

            while True:
                time.sleep(5)
                if STOP_FLAG:
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
