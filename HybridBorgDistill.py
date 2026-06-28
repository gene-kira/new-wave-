#!/usr/bin/env python3
# HYBRID BORG SWARM DISTILLATION DAEMON — 24/7, MULTI-THREADED, SWARM MODE

import os
import sys
import time
import threading
import queue
import subprocess
import traceback

# ---------------------------------------------------------
# AUTOLOADER
# ---------------------------------------------------------
REQUIRED = ["torch", "numpy", "scikit-learn", "pandas", "joblib"]

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
from sklearn.model_selection import train_test_split
from joblib import dump

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
# GLOBAL STATE
# ---------------------------------------------------------
DATA_QUEUE = queue.Queue(maxsize=1000)   # streaming batches
CONTROL_LOCK = threading.Lock()
CURRENT_DATA_MTIME = None
STOP_FLAG = False

TEACHER = None
SWARM_STUDENTS = []   # multiple nodes
IN_FEATURES = None

# ---------------------------------------------------------
# DATA STREAMING + AUTO-RELOAD
# ---------------------------------------------------------
def load_full_data():
    global CURRENT_DATA_MTIME, IN_FEATURES
    csv_path = os.path.join(os.path.dirname(__file__), "data.csv")
    if os.path.exists(csv_path):
        mtime = os.path.getmtime(csv_path)
        if CURRENT_DATA_MTIME is None or mtime != CURRENT_DATA_MTIME:
            CURRENT_DATA_MTIME = mtime
            df = pd.read_csv(csv_path)
            X = df.iloc[:, :-1].values.astype(np.float32)
            y = df.iloc[:, -1].values.astype(np.float32)
            IN_FEATURES = X.shape[1]
            print(f"[BORG] Reloaded data.csv, shape={df.shape}")
        else:
            # No change, just reuse
            df = pd.read_csv(csv_path)
            X = df.iloc[:, :-1].values.astype(np.float32)
            y = df.iloc[:, -1].values.astype(np.float32)
    else:
        X = np.random.randn(1000, 10).astype(np.float32)
        y = (X.sum(axis=1) + np.random.randn(1000)).astype(np.float32)
        IN_FEATURES = X.shape[1]
        print("[BORG] Using synthetic data (no data.csv)")

    return X, y

def streaming_ingestion_loop(batch_size=64, sleep_between_batches=1.0):
    global STOP_FLAG
    print("[STREAM] Streaming ingestion thread started")
    while not STOP_FLAG:
        try:
            X, y = load_full_data()
            # Shuffle and stream mini-batches
            idx = np.random.permutation(len(X))
            X = X[idx]
            y = y[idx]

            for i in range(0, len(X), batch_size):
                xb = X[i:i+batch_size]
                yb = y[i:i+batch_size]
                if xb.shape[0] == 0:
                    continue
                try:
                    DATA_QUEUE.put((xb, yb), timeout=1.0)
                except queue.Full:
                    # If queue is full, drop batch (borg keeps moving)
                    pass

                if STOP_FLAG:
                    break

                time.sleep(sleep_between_batches)
        except Exception as e:
            print("[STREAM] Exception in streaming loop:", e)
            traceback.print_exc()
            time.sleep(2.0)

    print("[STREAM] Streaming ingestion thread exiting")

# ---------------------------------------------------------
# TEACHER TRAINING (MICRO-UPDATES)
# ---------------------------------------------------------
def teacher_loop():
    global TEACHER, IN_FEATURES, STOP_FLAG
    print("[TEACHER] Teacher thread started")

    # Initialize teacher lazily
    while IN_FEATURES is None and not STOP_FLAG:
        time.sleep(0.5)

    if TEACHER is None and IN_FEATURES is not None:
        TEACHER = Teacher(IN_FEATURES)
        print("[TEACHER] Teacher initialized")

    criterion = nn.MSELoss()
    opt = optim.Adam(TEACHER.parameters(), lr=1e-3)

    while not STOP_FLAG:
        try:
            # Micro-training: consume small batches from queue
            try:
                xb, yb = DATA_QUEUE.get(timeout=2.0)
            except queue.Empty:
                time.sleep(0.5)
                continue

            xb_t = torch.tensor(xb, dtype=torch.float32)
            yb_t = torch.tensor(yb, dtype=torch.float32).reshape(-1, 1)

            TEACHER.train()
            preds = TEACHER(xb_t)
            loss = criterion(preds, yb_t)
            opt.zero_grad()
            loss.backward()
            opt.step()

        except Exception as e:
            print("[TEACHER] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

    print("[TEACHER] Teacher thread exiting")

# ---------------------------------------------------------
# SWARM STUDENT MICRO-DISTILLATION
# ---------------------------------------------------------
def init_swarm(num_nodes=3):
    global SWARM_STUDENTS, IN_FEATURES
    SWARM_STUDENTS = []
    for i in range(num_nodes):
        SWARM_STUDENTS.append(Student(IN_FEATURES))
    print(f"[SWARM] Initialized {num_nodes} student nodes")

def student_node_loop(node_id, alpha=0.5):
    global TEACHER, SWARM_STUDENTS, STOP_FLAG
    print(f"[SWARM-{node_id}] Student node thread started")

    criterion = nn.MSELoss()
    opt = optim.Adam(SWARM_STUDENTS[node_id].parameters(), lr=1e-3)

    while TEACHER is None and not STOP_FLAG:
        time.sleep(0.5)

    while not STOP_FLAG:
        try:
            # Micro-distillation: small batches from queue
            try:
                xb, yb = DATA_QUEUE.get(timeout=2.0)
            except queue.Empty:
                time.sleep(0.5)
                continue

            xb_t = torch.tensor(xb, dtype=torch.float32)
            yb_t = torch.tensor(yb, dtype=torch.float32).reshape(-1, 1)

            with torch.no_grad():
                teacher_soft = TEACHER(xb_t)

            SWARM_STUDENTS[node_id].train()
            preds = SWARM_STUDENTS[node_id](xb_t)

            loss_teacher = criterion(preds, teacher_soft)
            loss_true = criterion(preds, yb_t)
            loss = alpha * loss_teacher + (1 - alpha) * loss_true

            opt.zero_grad()
            loss.backward()
            opt.step()

        except Exception as e:
            print(f"[SWARM-{node_id}] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

    print(f"[SWARM-{node_id}] Student node thread exiting")

# ---------------------------------------------------------
# PERIODIC SNAPSHOT + SWARM AGGREGATION
# ---------------------------------------------------------
def snapshot_and_aggregate_loop(snapshot_interval=60):
    global TEACHER, SWARM_STUDENTS, STOP_FLAG
    print("[SNAPSHOT] Snapshot thread started")

    while (TEACHER is None or len(SWARM_STUDENTS) == 0) and not STOP_FLAG:
        time.sleep(1.0)

    while not STOP_FLAG:
        try:
            time.sleep(snapshot_interval)
            if TEACHER is None or len(SWARM_STUDENTS) == 0:
                continue

            # Simple swarm aggregation: average student weights
            with CONTROL_LOCK:
                print("[SNAPSHOT] Aggregating swarm students")
                # Collect state dicts
                state_dicts = [s.state_dict() for s in SWARM_STUDENTS]
                avg_state = {}
                for key in state_dicts[0].keys():
                    avg_state[key] = sum(sd[key] for sd in state_dicts) / len(state_dicts)

                # Apply averaged state to all students
                for s in SWARM_STUDENTS:
                    s.load_state_dict(avg_state)

                # Save artifacts
                torch.save(TEACHER.state_dict(), "borg_teacher.pt")
                torch.save(avg_state, "borg_swarm_student_state.pt")

                report = {
                    "n_students": len(SWARM_STUDENTS),
                    "in_features": IN_FEATURES,
                    "timestamp": time.time(),
                }
                dump(report, "borg_swarm_report.joblib")

                print("[SNAPSHOT] Swarm aggregated and saved")
        except Exception as e:
            print("[SNAPSHOT] Exception:", e)
            traceback.print_exc()
            time.sleep(5.0)

    print("[SNAPSHOT] Snapshot thread exiting")

# ---------------------------------------------------------
# WATCHDOG + AUTO-RECOVERY
# ---------------------------------------------------------
def start_all_threads():
    global STOP_FLAG

    # Reset stop flag
    STOP_FLAG = False

    # Streaming ingestion
    t_stream = threading.Thread(target=streaming_ingestion_loop, daemon=True)
    t_stream.start()

    # Teacher
    t_teacher = threading.Thread(target=teacher_loop, daemon=True)
    t_teacher.start()

    # Wait for IN_FEATURES to be known
    while IN_FEATURES is None and not STOP_FLAG:
        time.sleep(0.5)

    # Swarm students
    init_swarm(num_nodes=3)
    swarm_threads = []
    for i in range(len(SWARM_STUDENTS)):
        t = threading.Thread(target=student_node_loop, args=(i,), daemon=True)
        t.start()
        swarm_threads.append(t)

    # Snapshot / aggregation
    t_snapshot = threading.Thread(target=snapshot_and_aggregate_loop, daemon=True)
    t_snapshot.start()

    return [t_stream, t_teacher, *swarm_threads, t_snapshot]

def watchdog_loop():
    global STOP_FLAG
    print("=== HYBRID BORG SWARM DISTILLATION DAEMON — WATCHDOG MODE ===")

    while True:
        try:
            threads = start_all_threads()
            print("[WATCHDOG] Threads started, entering monitoring loop")

            # Monitor threads
            while True:
                time.sleep(5.0)
                # In this design, threads are daemon and run forever.
                # If we detect a global STOP_FLAG, we break and restart.
                if STOP_FLAG:
                    print("[WATCHDOG] STOP_FLAG set, restarting all threads")
                    break

        except Exception as e:
            print("[WATCHDOG] Exception in main loop:", e)
            traceback.print_exc()

        # Auto-recovery: reset state and restart
        print("[WATCHDOG] Auto-recovery: resetting state and restarting")
        reset_global_state()
        time.sleep(3.0)

def reset_global_state():
    global DATA_QUEUE, CURRENT_DATA_MTIME, STOP_FLAG, TEACHER, SWARM_STUDENTS, IN_FEATURES
    with CONTROL_LOCK:
        STOP_FLAG = False
        DATA_QUEUE = queue.Queue(maxsize=1000)
        CURRENT_DATA_MTIME = None
        TEACHER = None
        SWARM_STUDENTS = []
        IN_FEATURES = None
        print("[WATCHDOG] Global state reset")

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
if __name__ == "__main__":
    # Watchdog never exits; it restarts threads on failure.
    watchdog_loop()
