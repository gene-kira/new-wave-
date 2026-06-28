#!/usr/bin/env python3
# BORG ALTERED STATES v2 — PERFORMANCE GOVERNOR + MODES + PHYSICS + LSTM PREDICTION
# Server + PC gaming, self-learning, multi-model, anomaly-aware, mode-aware, physics-style load engine.

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

# ---------------------------------------------------------
# GLOBAL STATE
# ---------------------------------------------------------
DATA_QUEUE = queue.Queue(maxsize=4000)
CONTROL_LOCK = threading.Lock()
STOP_FLAG = False

TEACHER = None
SHADOW_TEACHER = None
SWARM_STUDENTS = []
ANOMALY_MODEL = None
LSTM_MODEL = None

IN_FEATURES = None

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.csv")

CURRENT_PORT = 5001
LAST_ROTATE = time.time()

EMA_TEACHER_LOSS = None
EMA_SHADOW_LOSS = None
EMA_SWARM_LOSS = None
EMA_ANOMALY_LOSS = None
EMA_LSTM_LOSS = None

MAX_ROWS = 50000

LATEST_STATUS = {
    "ema_teacher_loss": None,
    "ema_shadow_loss": None,
    "ema_swarm_loss": None,
    "ema_anomaly_loss": None,
    "ema_lstm_loss": None,
    "samples": 0,
    "current_port": CURRENT_PORT,
    "last_update": None,
    "last_perf_score": None,
    "last_bottlenecks": [],
    "last_actions": [],
    "mode": "Idle",
    "predicted_pressure_spike": False,
    "fluid_energy": 0.0,
    "anomaly_score": 0.0,
    "prediction_horizon_sec": 10.0,
    "predicted_perf_score": None,
    "prediction_confidence": None,
    "mode_history": []
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

STATE_HISTORY = []  # (timestamp, features_vector, perf_score, energy)

# ---------------------------------------------------------
# MODES / ALTERED STATES
# ---------------------------------------------------------
MODES = ["Flow", "DeepWork", "Recovery", "Dream", "Idle"]

def current_hour():
    return time.localtime().tm_hour

def infer_mode(features, ext, perf_score):
    cpu = features["cpu"]
    ram = features["ram"]
    gpu = features["gpu_usage"]
    latency = features["net_latency_ms"]
    game_cpu = ext["game_cpu"]
    server_cpu = ext["server_cpu"]
    fps = features["fps"]
    hour = current_hour()

    if game_cpu > 50 or fps > 30 or gpu > 60:
        return "Flow"
    if server_cpu > 80 or (cpu > 70 and gpu < 40):
        return "DeepWork"
    if perf_score < 40 and (cpu > 70 or ram > 80 or gpu > 80):
        return "Recovery"
    if hour >= 1 and hour <= 5 and cpu < 30 and gpu < 30 and server_cpu < 30 and game_cpu < 30:
        return "Dream"
    return "Idle"

def mode_personality(mode):
    if mode == "Flow":
        return {"lr_factor": 1.2, "mutation_rate": 0.03, "physics_sensitivity": 1.2, "control_aggressiveness": 0.7}
    if mode == "DeepWork":
        return {"lr_factor": 1.0, "mutation_rate": 0.05, "physics_sensitivity": 1.0, "control_aggressiveness": 0.8}
    if mode == "Recovery":
        return {"lr_factor": 0.8, "mutation_rate": 0.02, "physics_sensitivity": 1.5, "control_aggressiveness": 0.9}
    if mode == "Dream":
        return {"lr_factor": 0.6, "mutation_rate": 0.04, "physics_sensitivity": 0.8, "control_aggressiveness": 0.3}
    return {"lr_factor": 1.0, "mutation_rate": 0.03, "physics_sensitivity": 1.0, "control_aggressiveness": 0.5}

def update_mode_history(mode):
    LATEST_STATUS["mode_history"].append((time.time(), mode))
    if len(LATEST_STATUS["mode_history"]) > 100:
        LATEST_STATUS["mode_history"] = LATEST_STATUS["mode_history"][-100:]

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
    fps = 0.0
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
# PHYSICS-STYLE FLUID LOAD MODEL
# ---------------------------------------------------------
def compute_fluid_energy(features):
    cpu = features["cpu"]
    ram = features["ram"]
    gpu = features["gpu_usage"]
    latency = features["net_latency_ms"]
    energy = 0.5 * (cpu ** 2) + 0.4 * (ram ** 2) + 0.3 * (gpu ** 2) + 0.1 * (latency ** 2) / 100.0
    return energy

def update_state_history(features, perf_score):
    global STATE_HISTORY
    energy = compute_fluid_energy(features)
    timestamp = features["timestamp"]
    vec = np.array([
        features["cpu"], features["ram"], features["gpu_usage"],
        features["net_latency_ms"], perf_score
    ], dtype=np.float32)
    STATE_HISTORY.append((timestamp, vec, perf_score, energy))
    if len(STATE_HISTORY) > 300:
        STATE_HISTORY = STATE_HISTORY[-300:]
    LATEST_STATUS["fluid_energy"] = energy

def predict_pressure_spike():
    if len(STATE_HISTORY) < 5:
        return False
    times = np.array([t for t, v, p, e in STATE_HISTORY[-20:]])
    energies = np.array([e for t, v, p, e in STATE_HISTORY[-20:]])
    t0 = times[0]
    x = times - t0
    if len(x) < 2:
        return False
    A = np.vstack([x, np.ones_like(x)]).T
    try:
        m, c = np.linalg.lstsq(A, energies, rcond=None)[0]
    except Exception:
        return False
    return m > 5.0

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
            update_state_history(features, perf_score)
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

class ShadowTeacher(nn.Module):
    def __init__(self, in_features):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_features, 128),
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

class AnomalyAutoencoder(nn.Module):
    def __init__(self, input_dim):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 8),
            nn.ReLU()
        )
        self.decoder = nn.Sequential(
            nn.Linear(8, 16),
            nn.ReLU(),
            nn.Linear(16, input_dim)
        )
    def forward(self, x):
        z = self.encoder(x)
        recon = self.decoder(z)
        return recon

class LSTMPredictor(nn.Module):
    def __init__(self, feature_dim, hidden_dim=32, num_layers=1):
        super().__init__()
        self.lstm = nn.LSTM(feature_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, 1)
    def forward(self, x_seq):
        out, _ = self.lstm(x_seq)
        last = out[:, -1, :]
        pred = self.fc(last)
        return pred

# ---------------------------------------------------------
# ADAPTIVE LR
# ---------------------------------------------------------
def adaptive_lr(base_lr=1e-3, mode="Idle"):
    m = collect_system_metrics()
    cpu = m["cpu"]
    ram = m["ram"]
    personality = mode_personality(mode)
    factor = personality["lr_factor"]
    if cpu > 80 or ram > 80:
        factor *= 0.7
    elif cpu < 40 and ram < 40:
        factor *= 1.2
    return base_lr * factor

# ---------------------------------------------------------
# TEACHER + SHADOW TEACHER LOOP
# ---------------------------------------------------------
def teacher_loop():
    global TEACHER, SHADOW_TEACHER, IN_FEATURES, STOP_FLAG
    global EMA_TEACHER_LOSS, EMA_SHADOW_LOSS
    print("[TEACHER] Teacher threads started")
    while IN_FEATURES is None and not STOP_FLAG:
        time.sleep(0.5)

    TEACHER = Teacher(IN_FEATURES)
    SHADOW_TEACHER = ShadowTeacher(IN_FEATURES)

    criterion = nn.MSELoss()
    opt_main = optim.Adam(TEACHER.parameters(), lr=adaptive_lr(1e-3))
    opt_shadow = optim.Adam(SHADOW_TEACHER.parameters(), lr=adaptive_lr(8e-4))

    ema_alpha = 0.9

    while not STOP_FLAG:
        try:
            xb, yb = DATA_QUEUE.get(timeout=2.0)
            xb_t = torch.tensor(xb)
            yb_t = torch.tensor(yb).reshape(-1, 1)

            mode = LATEST_STATUS["mode"]
            opt_main.param_groups[0]["lr"] = adaptive_lr(1e-3, mode)
            opt_shadow.param_groups[0]["lr"] = adaptive_lr(8e-4, mode)

            TEACHER.train()
            SHADOW_TEACHER.train()

            preds_main = TEACHER(xb_t)
            preds_shadow = SHADOW_TEACHER(xb_t)

            loss_main = criterion(preds_main, yb_t)
            loss_shadow = criterion(preds_shadow, yb_t)

            opt_main.zero_grad()
            loss_main.backward()
            opt_main.step()

            opt_shadow.zero_grad()
            loss_shadow.backward()
            opt_shadow.step()

            lm = float(loss_main.item())
            ls = float(loss_shadow.item())

            if EMA_TEACHER_LOSS is None:
                EMA_TEACHER_LOSS = lm
            else:
                EMA_TEACHER_LOSS = ema_alpha * EMA_TEACHER_LOSS + (1 - ema_alpha) * lm

            if EMA_SHADOW_LOSS is None:
                EMA_SHADOW_LOSS = ls
            else:
                EMA_SHADOW_LOSS = ema_alpha * EMA_SHADOW_LOSS + (1 - ema_alpha) * ls

            LATEST_STATUS["ema_teacher_loss"] = EMA_TEACHER_LOSS
            LATEST_STATUS["ema_shadow_loss"] = EMA_SHADOW_LOSS
            LATEST_STATUS["last_update"] = time.time()

        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            print("[TEACHER] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# SWARM STUDENTS
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

def mutate_student(student, mode="Idle"):
    personality = mode_personality(mode)
    rate = personality["mutation_rate"]
    with torch.no_grad():
        for p in student.parameters():
            if torch.rand(1).item() < rate:
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
            mode = LATEST_STATUS["mode"]
            opt.param_groups[0]["lr"] = adaptive_lr(1e-3, mode)
            opt.zero_grad()
            loss.backward()
            opt.step()
            mutate_student(SWARM_STUDENTS[node_id], mode)
            lv = float(loss.item())
            if EMA_SWARM_LOSS is None:
                EMA_SWARM_LOSS = lv
            else:
                EMA_SWARM_LOSS = ema_alpha * EMA_SWARM_LOSS + (1 - ema_alpha) * lv
            LATEST_STATUS["ema_swarm_loss"] = EMA_SWARM_LOSS
            LATEST_STATUS["last_update"] = time.time()
        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            print(f"[SWARM-{node_id}] Exception:", e)
            traceback.print_exc()
            time.sleep(2.0)

# ---------------------------------------------------------
# ANOMALY DETECTION (AUTOENCODER)
# ---------------------------------------------------------
def init_anomaly_model():
    global ANOMALY_MODEL
    ANOMALY_MODEL = AnomalyAutoencoder(input_dim=5)
    print("[ANOMALY] Autoencoder initialized")

def anomaly_loop():
    global ANOMALY_MODEL, STOP_FLAG, EMA_ANOMALY_LOSS
    print("[ANOMALY] Anomaly loop started")
    if ANOMALY_MODEL is None:
        init_anomaly_model()
    criterion = nn.MSELoss()
    opt = optim.Adam(ANOMALY_MODEL.parameters(), lr=1e-3)
    ema_alpha = 0.9
    while not STOP_FLAG:
        try:
            if len(STATE_HISTORY) < 10:
                time.sleep(2)
                continue
            batch = STATE_HISTORY[-32:]
            x = np.stack([v for t, v, p, e in batch], axis=0)
            x_t = torch.tensor(x)
            recon = ANOMALY_MODEL(x_t)
            loss = criterion(recon, x_t)
            opt.zero_grad()
            loss.backward()
            opt.step()
            lv = float(loss.item())
            if EMA_ANOMALY_LOSS is None:
                EMA_ANOMALY_LOSS = lv
            else:
                EMA_ANOMALY_LOSS = ema_alpha * EMA_ANOMALY_LOSS + (1 - ema_alpha) * lv
            LATEST_STATUS["ema_anomaly_loss"] = EMA_ANOMALY_LOSS
            LATEST_STATUS["anomaly_score"] = lv
            time.sleep(5)
        except Exception as e:
            print("[ANOMALY] Exception:", e)
            traceback.print_exc()
            time.sleep(5)

# ---------------------------------------------------------
# LSTM TEMPORAL PREDICTION
# ---------------------------------------------------------
def init_lstm_model():
    global LSTM_MODEL
    LSTM_MODEL = LSTMPredictor(feature_dim=5, hidden_dim=32, num_layers=1)
    print("[LSTM] Predictor initialized")

def lstm_loop():
    global LSTM_MODEL, STOP_FLAG, EMA_LSTM_LOSS
    print("[LSTM] Temporal prediction loop started")
    if LSTM_MODEL is None:
        init_lstm_model()
    criterion = nn.MSELoss()
    opt = optim.Adam(LSTM_MODEL.parameters(), lr=1e-3)
    ema_alpha = 0.9
    horizon = int(LATEST_STATUS["prediction_horizon_sec"])
    while not STOP_FLAG:
        try:
            if len(STATE_HISTORY) < 40:
                time.sleep(2)
                continue
            seq_len = 20
            batch_states = STATE_HISTORY[-(seq_len + horizon):]
            seq = np.stack([v for t, v, p, e in batch_states[:seq_len]], axis=0)
            target_perf = batch_states[-1][2]
            x_seq = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
            y_t = torch.tensor([[target_perf]], dtype=torch.float32)
            pred = LSTM_MODEL(x_seq)
            loss = criterion(pred, y_t)
            opt.zero_grad()
            loss.backward()
            opt.step()
            lv = float(loss.item())
            if EMA_LSTM_LOSS is None:
                EMA_LSTM_LOSS = lv
            else:
                EMA_LSTM_LOSS = ema_alpha * EMA_LSTM_LOSS + (1 - ema_alpha) * lv
            LATEST_STATUS["ema_lstm_loss"] = EMA_LSTM_LOSS
            LATEST_STATUS["predicted_perf_score"] = float(pred.item())
            conf = max(0.0, 1.0 / (1.0 + EMA_LSTM_LOSS))
            LATEST_STATUS["prediction_confidence"] = conf
            time.sleep(5)
        except Exception as e:
            print("[LSTM] Exception:", e)
            traceback.print_exc()
            time.sleep(5)

# ---------------------------------------------------------
# BOTTLENECKS + RECOMMENDATIONS + CONTROLS
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

def recommend_actions(features, ext, bottlenecks, mode, predicted_spike, predicted_perf, conf):
    actions = []
    if predicted_spike:
        actions.append("Predicted upcoming load spike; pre-emptively reduce background activity.")
    if predicted_perf is not None and conf is not None:
        actions.append(f"Predicted performance score in ~{int(LATEST_STATUS['prediction_horizon_sec'])}s: {predicted_perf:.1f} (confidence {conf:.2f}).")
    if mode == "Flow":
        actions.append("Flow mode: prioritize low latency and FPS; suppress non-essential background tasks.")
    if mode == "DeepWork":
        actions.append("DeepWork mode: prioritize throughput; allow higher CPU usage, monitor RAM.")
    if mode == "Recovery":
        actions.append("Recovery mode: cool down system; lower priorities and pause non-essential tasks.")
    if mode == "Dream":
        actions.append("Dream mode: system idle; run offline learning or maintenance tasks.")
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

def apply_safe_controls(ext, bottlenecks, mode, predicted_spike):
    actions_taken = []
    personality = mode_personality(mode)
    aggressiveness = personality["control_aggressiveness"]
    try:
        if (predicted_spike or "High CPU load" in bottlenecks) and aggressiveness > 0.6:
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
                    "ema_shadow_loss": EMA_SHADOW_LOSS,
                    "ema_swarm_loss": EMA_SWARM_LOSS,
                    "ema_anomaly_loss": EMA_ANOMALY_LOSS,
                    "ema_lstm_loss": EMA_LSTM_LOSS,
                    "port": CURRENT_PORT,
                    "samples": LATEST_STATUS["samples"],
                    "last_perf_score": LATEST_STATUS["last_perf_score"],
                    "mode": LATEST_STATUS["mode"],
                    "fluid_energy": LATEST_STATUS["fluid_energy"],
                    "anomaly_score": LATEST_STATUS["anomaly_score"],
                    "predicted_perf_score": LATEST_STATUS["predicted_perf_score"],
                    "prediction_confidence": LATEST_STATUS["prediction_confidence"]
                }
                dump(report, "borg_altered_states_lstm_report.joblib")
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
        "ema_shadow_loss": LATEST_STATUS["ema_shadow_loss"],
        "ema_swarm_loss": LATEST_STATUS["ema_swarm_loss"],
        "ema_anomaly_loss": LATEST_STATUS["ema_anomaly_loss"],
        "ema_lstm_loss": LATEST_STATUS["ema_lstm_loss"],
        "samples": LATEST_STATUS["samples"],
        "current_port": LATEST_STATUS["current_port"],
        "last_update": LATEST_STATUS["last_update"],
        "last_perf_score": LATEST_STATUS["last_perf_score"],
        "last_bottlenecks": LATEST_STATUS["last_bottlenecks"],
        "last_actions": LATEST_STATUS["last_actions"],
        "mode": LATEST_STATUS["mode"],
        "mode_history": LATEST_STATUS["mode_history"],
        "predicted_pressure_spike": LATEST_STATUS["predicted_pressure_spike"],
        "fluid_energy": LATEST_STATUS["fluid_energy"],
        "anomaly_score": LATEST_STATUS["anomaly_score"],
        "predicted_perf_score": LATEST_STATUS["predicted_perf_score"],
        "prediction_confidence": LATEST_STATUS["prediction_confidence"],
        "time": time.time()
    })

@app.route("/recommendations")
def recommendations():
    return jsonify({
        "bottlenecks": LATEST_STATUS["last_bottlenecks"],
        "actions": LATEST_STATUS["last_actions"],
        "perf_score": LATEST_STATUS["last_perf_score"],
        "mode": LATEST_STATUS["mode"],
        "predicted_pressure_spike": LATEST_STATUS["predicted_pressure_spike"],
        "fluid_energy": LATEST_STATUS["fluid_energy"],
        "anomaly_score": LATEST_STATUS["anomaly_score"],
        "predicted_perf_score": LATEST_STATUS["predicted_perf_score"],
        "prediction_confidence": LATEST_STATUS["prediction_confidence"],
        "time": time.time()
    })

def web_status_loop(port=5000):
    print(f"[WEB] Status endpoint on port {port} at /status and /recommendations")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ---------------------------------------------------------
# GOVERNOR LOOP (MODES + PHYSICS + LSTM + CONTROL)
# ---------------------------------------------------------
def governor_loop():
    print("[GOVERNOR] Performance governor started")
    while not STOP_FLAG:
        try:
            features, ext, perf_score = append_system_data()
            update_state_history(features, perf_score)
            mode = infer_mode(features, ext, perf_score)
            update_mode_history(mode)
            predicted_spike = predict_pressure_spike()
            LATEST_STATUS["mode"] = mode
            LATEST_STATUS["predicted_pressure_spike"] = predicted_spike
            bottlenecks = detect_bottlenecks(features, ext, perf_score)
            predicted_perf = LATEST_STATUS["predicted_perf_score"]
            conf = LATEST_STATUS["prediction_confidence"]
            actions = recommend_actions(features, ext, bottlenecks, mode, predicted_spike, predicted_perf, conf)
            actions_taken = apply_safe_controls(ext, bottlenecks, mode, predicted_spike)
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

    t_anom = threading.Thread(target=anomaly_loop, daemon=True)
    t_anom.start()

    t_lstm = threading.Thread(target=lstm_loop, daemon=True)
    t_lstm.start()

    return [t_stream, t_teacher, *swarm_threads, t_snapshot, t_port, t_web, t_gov, t_anom, t_lstm]

def reset_state():
    global DATA_QUEUE, STOP_FLAG, TEACHER, SHADOW_TEACHER, SWARM_STUDENTS, IN_FEATURES, STATE_HISTORY
    global ANOMALY_MODEL, LSTM_MODEL
    with CONTROL_LOCK:
        DATA_QUEUE = queue.Queue(maxsize=4000)
        STOP_FLAG = False
        TEACHER = None
        SHADOW_TEACHER = None
        SWARM_STUDENTS = []
        IN_FEATURES = None
        STATE_HISTORY = []
        ANOMALY_MODEL = None
        LSTM_MODEL = None
        LATEST_STATUS["last_bottlenecks"] = []
        LATEST_STATUS["last_actions"] = []
        LATEST_STATUS["mode"] = "Idle"
        LATEST_STATUS["predicted_pressure_spike"] = False
        LATEST_STATUS["fluid_energy"] = 0.0
        LATEST_STATUS["anomaly_score"] = 0.0
        LATEST_STATUS["predicted_perf_score"] = None
        LATEST_STATUS["prediction_confidence"] = None
        LATEST_STATUS["mode_history"] = []
        print("[WATCHDOG] State reset")

def watchdog_loop():
    print("=== BORG ALTERED STATES + LSTM — WATCHDOG MODE ===")
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
