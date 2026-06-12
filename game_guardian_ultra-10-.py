#!/usr/bin/env python
# game_guardian_ultra_ai_cluster_mic_supervisor_plus_audio_intel_v4.py
#
# Game Guardian ULTRA AI CLUSTER + MIC / AUDIO INTEL (V4):
# - Real-time game connection monitor
# - ML-style risk engine + real ML model integration
# - ML training pipeline from logs
# - Real-time anomaly detector
# - Distributed threat intelligence node + peers
# - Server reputation scoring
# - AI-style threat summaries
# - Auto-block suggestions (manual firewall)
# - DNS hijack detection
# - Network optimizer
# - Device health panel
# - Plugin system
# - Auto-update stub
# - Cloud sync stub
# - Per-IP notes
# - Threat playback animations
# - System tray mode
# - Dark-mode toggle
# - Per-game profiles
# - Auto-export logs
# - Live latency graph
#
# MIC / AUDIO SUBSYSTEM:
# - Self-healing microphone loop
# - Mic crash prediction (pattern-based)
# - USB port watchdog with reset hook (PowerShell/devcon-style)
# - Exclusive-mode killer (apps that lock mic)
# - Discord/Steam voice auto-reconnect
# - Audio device priority enforcement
# - AI-based voice quality scoring (RMS / clipping / noise)
# - Voice Activity Detection (VAD) engine (energy-based)
# - Audio driver crash forensics logging
# - Full audio routing map (devices + host APIs)
# - Game-specific voice optimization profiles
#
# AUDIO INTELLIGENCE (V3):
# - AI-style noise suppression (RNNoise-style stub: band/energy gate)
# - Automatic Gain Control (AGC) on mic stream
# - Voice fingerprinting (per-speaker profile stub)
# - Audio driver crash recovery without full service restart (stream re-init path)
# - Full audio graph visualization (nodes + endpoints)
# - Machine-learning voice clarity optimizer (stub model on voice features)
#
# NEW AUDIO INTELLIGENCE (V4):
# - Real RNNoise integration hook (C DLL binding stub)
# - Deep-learning voice enhancement pipeline stub (PyTorch-ready)
# - Per-game auto-EQ tuning (EQ profiles per game)
# - Audio latency compensation engine (buffer-based)
# - Voice-triggered macros / automation (keyword / VAD-based)
#
# Run as Administrator on Windows.

import os
import sys
import time
import threading
import subprocess
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# -----------------------------
# Autoloader
# -----------------------------
REQUIRED_LIBS = [
    "psutil",
    "sounddevice",
    "pynput",
    "requests",
    "pystray",
    "Pillow",
    "scikit-learn",
    "joblib",
    "numpy",
]

def ensure_libs():
    for lib in REQUIRED_LIBS:
        try:
            __import__(lib)
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", lib], check=False)

ensure_libs()

import psutil
import sounddevice as sd
from pynput import mouse, keyboard
import requests
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import pystray
from PIL import Image, ImageDraw
from sklearn.linear_model import LogisticRegression  # type: ignore
import joblib  # type: ignore
import math
import statistics
import numpy as np  # type: ignore

# Optional PyTorch (for deep enhancement stub)
try:
    import torch  # type: ignore
    TORCH_AVAILABLE = True
except Exception:
    TORCH_AVAILABLE = False

# Optional ctypes for RNNoise DLL binding
import ctypes

# -----------------------------
# Admin check
# -----------------------------
def is_admin():
    try:
        import ctypes as _ct
        return _ct.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def relaunch_as_admin():
    if is_admin():
        return
    import ctypes as _ct
    params = " ".join([f'"{arg}"' for arg in sys.argv])
    _ct.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )
    sys.exit(0)

relaunch_as_admin()

# -----------------------------
# Version / update
# -----------------------------
CURRENT_VERSION = "4.0.0"
UPDATE_CHECK_URL = ""

def check_for_update() -> Optional[str]:
    if not UPDATE_CHECK_URL:
        return None
    try:
        r = requests.get(UPDATE_CHECK_URL, timeout=3)
        if r.status_code == 200:
            data = r.json()
            latest = data.get("latest_version")
            if latest and latest != CURRENT_VERSION:
                return latest
    except Exception:
        pass
    return None

# -----------------------------
# Config / Settings
# -----------------------------
CONFIG_FILE = "gg_ultra_settings.json"
LOG_FILE = "game_guardian_log.jsonl"
DISCORD_WEBHOOK_URL = ""
IP_NOTES_FILE = "gg_ip_notes.json"

DEFAULT_CONFIG = {
    "scan_interval": 3.0,
    "ping_timeout_ms": 800,
    "geoip_timeout": 2.0,
    "dark_mode": False,
    "auto_export_logs": True,
    "cloud_sync_enabled": False,
    "cloud_endpoint": "",
    "game_profiles": {
        "cs2": 1.0,
        "fortnite": 1.0,
        "apex": 1.0,
        "valorant": 1.0,
        "warzone": 1.0
    },
    # Threat intelligence network
    "ti_enabled": False,
    "ti_port": 8099,
    "ti_peers": [],
    # Mic subsystem
    "mic_preferred_device_name": "",
    "mic_check_interval": 5.0,
    "mic_prediction_window": 10,
    "mic_prediction_threshold": 0.6,
    "mic_usb_watchdog_enabled": True,
    "mic_exclusive_killer_enabled": True,
    "mic_voice_reconnect_enabled": True,
    "mic_priority_enforcement_enabled": True,
    # Voice quality / VAD
    "voice_monitor_enabled": True,
    "voice_sample_rate": 16000,
    "voice_frame_ms": 30,
    "voice_energy_threshold": 0.01,
    "voice_clip_threshold": 0.95,
    # Game-specific voice optimization profiles
    "voice_profiles": {
        "cs2": {"mic_device": "", "sample_rate": 48000},
        "fortnite": {"mic_device": "", "sample_rate": 48000},
        "apex": {"mic_device": "", "sample_rate": 48000},
        "valorant": {"mic_device": "", "sample_rate": 48000},
        "warzone": {"mic_device": "", "sample_rate": 48000}
    },
    # Audio intelligence toggles
    "noise_suppression_enabled": True,
    "agc_enabled": True,
    "voice_fingerprint_enabled": False,
    "clarity_optimizer_enabled": False,
    # New V4 features
    "rnnoise_enabled": False,
    "rnnoise_dll_path": "rnnoise.dll",
    "deep_enhancement_enabled": False,
    "per_game_eq_enabled": True,
    "latency_compensation_enabled": True,
    "voice_macros_enabled": True,
    # Per-game EQ profiles: simple 3-band EQ (low, mid, high gains)
    "eq_profiles": {
        "cs2": {"low": 0.0, "mid": 2.0, "high": 3.0},
        "fortnite": {"low": 1.0, "mid": 1.0, "high": 2.0},
        "apex": {"low": 0.0, "mid": 1.5, "high": 2.5},
        "valorant": {"low": -1.0, "mid": 2.0, "high": 3.0},
        "warzone": {"low": 0.5, "mid": 1.5, "high": 2.5}
    },
    # Voice macro keywords (simple text triggers)
    "voice_macros": {
        "mute all": {"action": "macro_mute_all"},
        "unmute all": {"action": "macro_unmute_all"},
        "clip that": {"action": "macro_clip"},
    },
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = DEFAULT_CONFIG.copy()
        cfg.update(data)
        return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(cfg: Dict[str, Any]):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

config = load_config()

SCAN_INTERVAL = config["scan_interval"]
PING_TIMEOUT_MS = int(config["ping_timeout_ms"])
GEOIP_TIMEOUT = float(config["geoip_timeout"])

BLUE = "#0055FF"

GAME_HINTS = [
    "steam", "epic", "fortnite", "cs2", "apex", "valorant", "gta", "warzone"
]

CURRENT_GAME_NAME = ""  # updated dynamically

# -----------------------------
# IP notes
# -----------------------------
def load_ip_notes() -> Dict[str, str]:
    if not os.path.exists(IP_NOTES_FILE):
        return {}
    try:
        with open(IP_NOTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_ip_notes(notes: Dict[str, str]):
    try:
        with open(IP_NOTES_FILE, "w", encoding="utf-8") as f:
            json.dump(notes, f, indent=2)
    except Exception:
        pass

ip_notes = load_ip_notes()

# -----------------------------
# Cloud sync (stub)
# -----------------------------
def cloud_sync_push():
    if not config.get("cloud_sync_enabled"):
        return
    endpoint = config.get("cloud_endpoint") or ""
    if not endpoint:
        return
    try:
        payload = {
            "config": config,
            "timestamp": time.time()
        }
        requests.post(endpoint + "/push_config", json=payload, timeout=3)
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = f.read()
            requests.post(endpoint + "/push_logs", data=logs.encode("utf-8"), timeout=5)
    except Exception:
        pass

def cloud_sync_pull():
    if not config.get("cloud_sync_enabled"):
        return
    endpoint = config.get("cloud_endpoint") or ""
    if not endpoint:
        return
    try:
        r = requests.get(endpoint + "/pull_config", timeout=3)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                config.update(data)
                save_config(config)
    except Exception:
        pass

# -----------------------------
# Helpers
# -----------------------------
def detect_games() -> List[psutil.Process]:
    games = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        name = (proc.info.get("name") or "").lower()
        exe = (proc.info.get("exe") or "").lower()
        if any(h in name for h in GAME_HINTS) or any(h in exe for h in GAME_HINTS):
            games.append(proc)
    return games

def ping_host_raw(host: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["ping", "-n", "4", "-w", str(PING_TIMEOUT_MS), host],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except Exception:
        return None

def parse_ping_stats(output: str) -> Dict[str, Any]:
    stats = {"avg_ms": None, "loss_pct": None}
    if not output:
        return stats
    try:
        for line in output.splitlines():
            line_lower = line.lower()
            if "lost" in line_lower and "%" in line_lower:
                parts = line_lower.split("(")
                if len(parts) > 1 and "%" in parts[1]:
                    pct = parts[1].split("%")[0]
                    pct = pct.replace("loss", "").strip()
                    stats["loss_pct"] = float(pct)
            if "average" in line_lower:
                parts = line_lower.split("average =")
                if len(parts) > 1:
                    avg_part = parts[1].strip()
                    if avg_part.endswith("ms"):
                        avg_part = avg_part[:-2]
                    stats["avg_ms"] = float(avg_part)
    except Exception:
        pass
    return stats

def is_private_ip(ip: str) -> bool:
    return (
        ip.startswith("10.") or
        ip.startswith("192.168.") or
        ip.startswith("172.") or
        ip.startswith("127.")
    )

def geoip_lookup(ip: str) -> Dict[str, Any]:
    info = {"country": "?", "region": "?", "isp": "?", "as": "?"}
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}", timeout=GEOIP_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            info["country"] = data.get("country", "?")
            info["region"] = data.get("regionName", "?")
            info["isp"] = data.get("isp", "?")
            info["as"] = data.get("as", "?")
    except Exception:
        pass
    return info

def firewall_block_ip(ip: str) -> bool:
    try:
        rule_name = f"GameGuardian_Block_{ip}"
        cmd = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}",
            "dir=in",
            "action=block",
            f"remoteip={ip}",
            "enable=yes"
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        return True
    except Exception:
        return False

def log_event(event: Dict[str, Any]):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass

def send_discord_alert(message: str):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=3)
    except Exception:
        pass

def auto_export_logs():
    if not config.get("auto_export_logs", True):
        return
    try:
        if not os.path.exists(LOG_FILE):
            return
        today = datetime.now().strftime("%Y-%m-%d")
        export_name = f"gg_logs_{today}.jsonl"
        if os.path.exists(export_name):
            return
        with open(LOG_FILE, "r", encoding="utf-8") as src, open(export_name, "w", encoding="utf-8") as dst:
            for line in src:
                dst.write(line)
    except Exception:
        pass

# -----------------------------
# DNS hijack detection
# -----------------------------
def get_dns_servers() -> List[str]:
    servers = []
    try:
        result = subprocess.run(
            ["nslookup"], capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if "address" in line.lower():
                parts = line.split(":")
                if len(parts) > 1:
                    ip = parts[1].strip()
                    servers.append(ip)
    except Exception:
        pass
    return servers

def check_hosts_file() -> bool:
    suspicious = False
    try:
        hosts_path = r"C:\Windows\System32\drivers\etc\hosts"
        if os.path.exists(hosts_path):
            with open(hosts_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue
                    if "steam" in line.lower() or "epic" in line.lower() or "riot" in line.lower():
                        suspicious = True
                        break
    except Exception:
        pass
    return suspicious

def dns_hijack_status() -> Dict[str, Any]:
    servers = get_dns_servers()
    hosts_suspicious = check_hosts_file()
    return {
        "servers": servers,
        "hosts_suspicious": hosts_suspicious
    }

# -----------------------------
# Network auto-optimization
# -----------------------------
def optimize_network():
    try:
        subprocess.run(["netsh", "winsock", "reset"], capture_output=True, text=True)
        subprocess.run(["netsh", "int", "ip", "reset"], capture_output=True, text=True)
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True)
        subprocess.run(["ipconfig", "/renew"], capture_output=True, text=True)
        return True
    except Exception:
        return False

# -----------------------------
# ML-style risk engine + real ML hook
# -----------------------------
ip_risk_history: Dict[str, List[int]] = {}
ip_reputation: Dict[str, Dict[str, int]] = {}

ML_MODEL_PATH = "gg_ml_model.pkl"
ml_model: Optional[LogisticRegression] = None

def load_ml_model():
    global ml_model
    if not os.path.exists(ML_MODEL_PATH):
        ml_model = None
        return
    try:
        ml_model = joblib.load(ML_MODEL_PATH)
    except Exception:
        ml_model = None

load_ml_model()

def get_game_profile_multiplier(game_name: str) -> float:
    name = (game_name or "").lower()
    for key, mult in config.get("game_profiles", {}).items():
        if key in name:
            return float(mult)
    return 1.0

def compute_base_risk(ip: str, latency_ms: Optional[float], loss_pct: Optional[float], geo: Dict[str, Any]) -> int:
    score = 0

    if is_private_ip(ip):
        score += 60

    if latency_ms is None:
        score += 20
    else:
        if latency_ms > 150:
            score += 30
        elif latency_ms > 80:
            score += 15

    if loss_pct is not None:
        if loss_pct > 50:
            score += 30
        elif loss_pct > 10:
            score += 15

    isp = geo.get("isp", "").lower()
    asn = geo.get("as", "").lower()
    risky_keywords = ["vpn", "hosting", "datacenter", "cloud", "colo", "m247", "ovh", "digitalocean"]
    if any(k in isp for k in risky_keywords) or any(k in asn for k in risky_keywords):
        score += 30

    if geo.get("country", "?") == "?":
        score += 10

    score = max(0, min(100, score))
    return score

def ml_model_predict_adjust(ip: str, base_score: int, latency_ms: Optional[float], loss_pct: Optional[float]) -> int:
    if ml_model is not None:
        try:
            lat = latency_ms if latency_ms is not None else 200.0
            loss = loss_pct if loss_pct is not None else 0.0
            features = [[base_score, lat, loss]]
            prob = ml_model.predict_proba(features)[0][1]
            adjust = int(prob * 20)
            return base_score + adjust
        except Exception:
            pass
    adjust = 0
    if latency_ms and latency_ms > 200:
        adjust += 5
    if loss_pct and loss_pct > 30:
        adjust += 5
    return base_score + adjust

def update_reputation(ip: str, score: int):
    rep = ip_reputation.get(ip, {"good": 0, "bad": 0})
    if score >= 70:
        rep["bad"] += 1
    elif score <= 20:
        rep["good"] += 1
    ip_reputation[ip] = rep

def get_reputation_label(ip: str) -> str:
    rep = ip_reputation.get(ip, {"good": 0, "bad": 0})
    g, b = rep["good"], rep["bad"]
    if b == 0 and g == 0:
        return "Unknown"
    if b > g * 2 and b >= 3:
        return "Bad"
    if g > b * 2 and g >= 3:
        return "Good"
    return "Mixed"

def compute_ml_risk(ip: str, base_score: int, game_name: str,
                    latency_ms: Optional[float], loss_pct: Optional[float]) -> int:
    history = ip_risk_history.get(ip, [])
    history.append(base_score)
    if len(history) > 50:
        history = history[-50:]
    ip_risk_history[ip] = history

    avg_history = sum(history) / len(history)
    if avg_history > 70:
        base_score += 10
    elif avg_history > 40:
        base_score += 5

    base_score = ml_model_predict_adjust(ip, base_score, latency_ms, loss_pct)

    mult = get_game_profile_multiplier(game_name)
    base_score = int(base_score * mult)

    base_score = max(0, min(100, base_score))
    update_reputation(ip, base_score)
    return base_score

def risk_label(score: int) -> str:
    if score < 20:
        return "OK"
    elif score < 50:
        return "CAUTION"
    elif score < 80:
        return "SUSPICIOUS"
    else:
        return "DANGEROUS"

def auto_block_suggestion(ip: str, score: int) -> bool:
    rep = get_reputation_label(ip)
    if score >= 90:
        return True
    if score >= 80 and rep == "Bad":
        return True
    return False

# -----------------------------
# Real-time anomaly detector
# -----------------------------
latency_history_global: List[float] = []
loss_history_global: List[float] = []

def detect_anomaly(latency_ms: Optional[float], loss_pct: Optional[float]) -> bool:
    if latency_ms is not None:
        latency_history_global.append(latency_ms)
        if len(latency_history_global) > 200:
            del latency_history_global[0]
    if loss_pct is not None:
        loss_history_global.append(loss_pct)
        if len(loss_history_global) > 200:
            del loss_history_global[0]

    anomaly = False

    if latency_ms is not None and len(latency_history_global) >= 30:
        mean_lat = statistics.mean(latency_history_global)
        std_lat = statistics.pstdev(latency_history_global) or 1.0
        z_lat = (latency_ms - mean_lat) / std_lat
        if z_lat > 3.0 and latency_ms > 120:
            anomaly = True

    if loss_pct is not None and len(loss_history_global) >= 30:
        mean_loss = statistics.mean(loss_history_global)
        std_loss = statistics.pstdev(loss_history_global) or 1.0
        z_loss = (loss_pct - mean_loss) / std_loss
        if z_loss > 3.0 and loss_pct > 20:
            anomaly = True

    return anomaly

# -----------------------------
# Device health monitor
# -----------------------------
class DeviceHealth:
    def __init__(self):
        self.mouse_ok = False
        self.keyboard_ok = False
        self.mic_ok = False
        self.last_mouse_event = 0.0
        self.last_key_event = 0.0
        self.last_mic_check = 0.0

        self.mouse_listener = mouse.Listener(on_move=self.on_mouse_event,
                                             on_click=self.on_mouse_event,
                                             on_scroll=self.on_mouse_event)
        self.keyboard_listener = keyboard.Listener(on_press=self.on_key_event)

    def on_mouse_event(self, *args, **kwargs):
        self.mouse_ok = True
        self.last_mouse_event = time.time()

    def on_key_event(self, *args, **kwargs):
        self.keyboard_ok = True
        self.last_key_event = time.time()

    def start(self):
        self.mouse_listener.start()
        self.keyboard_listener.start()

    def check_mic(self):
        try:
            devices = sd.query_devices()
            input_devices = [d for d in devices if d["max_input_channels"] > 0]
            self.mic_ok = len(input_devices) > 0
        except Exception:
            self.mic_ok = False
        self.last_mic_check = time.time()

    def loop(self):
        self.start()
        while True:
            self.check_mic()
            time.sleep(5.0)

# -----------------------------
# Plugin system
# -----------------------------
Plugin = Callable[[Dict[str, Any]], None]
plugins: List[Plugin] = []

def load_plugins():
    plugins_dir = "plugins"
    if not os.path.isdir(plugins_dir):
        return
    sys.path.insert(0, os.path.abspath(plugins_dir))
    for fname in os.listdir(plugins_dir):
        if not fname.endswith(".py"):
            continue
        mod_name = os.path.splitext(fname)[0]
        try:
            mod = __import__(mod_name)
            if hasattr(mod, "on_event") and callable(mod.on_event):
                plugins.append(mod.on_event)
        except Exception:
            continue

def run_plugins(event: Dict[str, Any]):
    for p in plugins:
        try:
            p(event)
        except Exception:
            continue

# -----------------------------
# Threat intelligence network
# -----------------------------
ti_server: Optional[HTTPServer] = None

def ti_broadcast_event(ev: Dict[str, Any]):
    if not config.get("ti_enabled", False):
        return
    peers = config.get("ti_peers", [])
    if not peers:
        return
    for peer in peers:
        try:
            requests.post(peer.rstrip("/") + "/ti_event", json=ev, timeout=2)
        except Exception:
            continue

class ThreatIntelHandler(BaseHTTPRequestHandler):
    def _send_json(self, obj: Any, code: int = 200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path == "/ti_event":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                ev = json.loads(body.decode("utf-8"))
            except Exception:
                self._send_json({"status": "error", "reason": "invalid json"}, 400)
                return
            ip = ev.get("ip")
            score = ev.get("score")
            if ip and isinstance(score, int):
                update_reputation(ip, score)
            log_event({"type": "ti_event", "event": ev, "timestamp": time.time()})
            self._send_json({"status": "ok"})
        else:
            self._send_json({"status": "not_found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/ti_reputation":
            qs = parse_qs(parsed.query)
            ip = qs.get("ip", [""])[0]
            if not ip:
                self._send_json({"status": "error", "reason": "missing ip"}, 400)
                return
            rep = get_reputation_label(ip)
            self._send_json({"status": "ok", "ip": ip, "reputation": rep})
        else:
            self._send_json({"status": "not_found"}, 404)

def start_ti_server():
    if not config.get("ti_enabled", False):
        return
    port = int(config.get("ti_port", 8099))
    def run():
        global ti_server
        try:
            server = HTTPServer(("0.0.0.0", port), ThreatIntelHandler)
            ti_server = server
            server.serve_forever()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()

# -----------------------------
# AI-style threat summaries
# -----------------------------
def generate_threat_summary(ev: Dict[str, Any]) -> str:
    ip = ev.get("ip", "?")
    score = ev.get("score", 0)
    risk = ev.get("risk", "UNKNOWN")
    latency = ev.get("latency_ms", None)
    loss = ev.get("loss_pct", None)
    geo = ev.get("geo", {})
    rep = get_reputation_label(ip)
    suggestion = auto_block_suggestion(ip, score)

    parts = []
    parts.append(f"IP {ip} is classified as {risk} with a risk score of {score}/100.")
    if latency is not None:
        parts.append(f"Latency observed: {latency:.1f} ms.")
    if loss is not None:
        parts.append(f"Packet loss: {loss:.1f}%.")
    country = geo.get("country", "?")
    isp = geo.get("isp", "?")
    if country != "?":
        parts.append(f"GeoIP: {country}, ISP: {isp}.")
    parts.append(f"Server reputation: {rep}.")
    if suggestion:
        parts.append("Auto-block suggestion: HIGH — consider blocking this IP if it appears repeatedly.")
    else:
        parts.append("Auto-block suggestion: LOW — monitor, but blocking is optional.")
    return " ".join(parts)

def generate_anomaly_summary(latency_ms: Optional[float], loss_pct: Optional[float]) -> str:
    parts = ["Anomaly detected in network performance."]
    if latency_ms is not None:
        parts.append(f"Latency spike: {latency_ms:.1f} ms.")
    if loss_pct is not None:
        parts.append(f"Packet loss spike: {loss_pct:.1f}%.")
    parts.append("This may indicate routing issues, congestion, or targeted disruption.")
    return " ".join(parts)

# -----------------------------
# ML training pipeline
# -----------------------------
def train_ml_model_from_logs() -> bool:
    if not os.path.exists(LOG_FILE):
        return False
    X = []
    y = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") != "connection":
                    continue
                vals = ev.get("values", [])
                if len(vals) < 12:
                    continue
                latency_str = vals[4]
                loss_str = vals[5]
                risk = vals[10]
                score = int(vals[11])
                if latency_str == "timeout":
                    lat = 300.0
                else:
                    try:
                        lat = float(latency_str.split()[0])
                    except Exception:
                        lat = 200.0
                try:
                    loss = float(loss_str.replace("%", "")) if loss_str != "?" else 0.0
                except Exception:
                    loss = 0.0
                label = 1 if risk in ("SUSPICIOUS", "DANGEROUS") or score >= 70 else 0
                X.append([score, lat, loss])
                y.append(label)
    except Exception:
        return False

    if len(X) < 50:
        return False

    try:
        model = LogisticRegression(max_iter=1000)
        model.fit(X, y)
        joblib.dump(model, ML_MODEL_PATH)
        return True
    except Exception:
        return False

# -----------------------------
# AUDIO ROUTING MAP
# -----------------------------
def get_audio_routing_map() -> Dict[str, Any]:
    info: Dict[str, Any] = {"devices": [], "hostapis": []}
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        info["devices"] = devices
        info["hostapis"] = hostapis
    except Exception as e:
        info["error"] = str(e)
    return info

# -----------------------------
# RNNoise DLL binding (stub)
# -----------------------------
class RNNoiseWrapper:
    """
    RNNoise integration hook:
    - Expects rnnoise.dll with functions:
      rnnoise_create, rnnoise_destroy, rnnoise_process_frame
    - Here we just stub the interface; if DLL is present, we call it.
    """
    def __init__(self, dll_path: str):
        self.dll_path = dll_path
        self.lib = None
        self.state = None
        self.frame_size = 480  # RNNoise default at 48kHz
        self._load()

    def _load(self):
        if not os.path.exists(self.dll_path):
            return
        try:
            self.lib = ctypes.cdll.LoadLibrary(self.dll_path)
            self.lib.rnnoise_create.restype = ctypes.c_void_p
            self.lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
            self.lib.rnnoise_process_frame.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
            ]
            self.state = self.lib.rnnoise_create()
        except Exception:
            self.lib = None
            self.state = None

    def process(self, frame: np.ndarray) -> np.ndarray:
        if self.lib is None or self.state is None:
            return frame
        # Expect mono float32, length == frame_size
        if frame.dtype != np.float32:
            frame = frame.astype(np.float32)
        if frame.shape[0] != self.frame_size:
            # simple pad/trim
            if frame.shape[0] < self.frame_size:
                pad = np.zeros(self.frame_size - frame.shape[0], dtype=np.float32)
                frame = np.concatenate([frame, pad])
            else:
                frame = frame[:self.frame_size]
        in_buf = (ctypes.c_float * self.frame_size)(*frame.tolist())
        out_buf = (ctypes.c_float * self.frame_size)()
        try:
            self.lib.rnnoise_process_frame(self.state, out_buf, in_buf)
            out = np.frombuffer(out_buf, dtype=np.float32)
            return out
        except Exception:
            return frame

    def __del__(self):
        if self.lib is not None and self.state is not None:
            try:
                self.lib.rnnoise_destroy(self.state)
            except Exception:
                pass

# -----------------------------
# Deep-learning enhancement stub (PyTorch-ready)
# -----------------------------
class DeepEnhancer:
    """
    Deep-learning voice enhancement stub:
    - If PyTorch is available and a model is loaded, run it.
    - Otherwise, pass-through.
    """
    def __init__(self):
        self.model = None
        self.device = "cpu"
        if TORCH_AVAILABLE:
            self._load_model()

    def _load_model(self):
        # Stub: user can drop a model at "voice_enhancer.pt"
        model_path = "voice_enhancer.pt"
        if not os.path.exists(model_path):
            return
        try:
            self.model = torch.jit.load(model_path, map_location="cpu")
            self.model.eval()
        except Exception:
            self.model = None

    def process(self, frame: np.ndarray, sr: int) -> np.ndarray:
        if self.model is None or not TORCH_AVAILABLE:
            return frame
        try:
            with torch.no_grad():
                x = torch.from_numpy(frame.astype(np.float32)).unsqueeze(0)
                y = self.model(x)
                y = y.squeeze(0).cpu().numpy()
                return y.astype(np.float32)
        except Exception:
            return frame

# -----------------------------
# EQ + Latency Compensation + Voice Macros
# -----------------------------
def get_eq_profile_for_game(game_name: str) -> Dict[str, float]:
    name = (game_name or "").lower()
    profiles = config.get("eq_profiles", {})
    for key, prof in profiles.items():
        if key in name:
            return prof
    return {"low": 0.0, "mid": 0.0, "high": 0.0}

def apply_simple_eq(frame: np.ndarray, sr: int, eq: Dict[str, float]) -> np.ndarray:
    """
    Very simple 3-band EQ:
    - low: 0-300 Hz
    - mid: 300-3000 Hz
    - high: 3000+ Hz
    Gains in dB.
    """
    if frame.size == 0:
        return frame
    if frame.ndim > 1:
        frame = frame[:, 0]
    N = frame.shape[0]
    spec = np.fft.rfft(frame)
    freqs = np.fft.rfftfreq(N, 1.0 / sr)

    def db_to_lin(db):
        return 10 ** (db / 20.0)

    low_gain = db_to_lin(eq.get("low", 0.0))
    mid_gain = db_to_lin(eq.get("mid", 0.0))
    high_gain = db_to_lin(eq.get("high", 0.0))

    low_mask = freqs < 300
    mid_mask = (freqs >= 300) & (freqs < 3000)
    high_mask = freqs >= 3000

    spec[low_mask] *= low_gain
    spec[mid_mask] *= mid_gain
    spec[high_mask] *= high_gain

    out = np.fft.irfft(spec, n=N)
    return out.astype(np.float32)

class LatencyCompensator:
    """
    Simple latency compensation:
    - Maintains a small buffer and can delay or align frames.
    - Here we just simulate a fixed delay buffer.
    """
    def __init__(self, target_delay_ms=30, sr=16000):
        self.target_delay_ms = target_delay_ms
        self.sr = sr
        self.buffer = np.zeros(0, dtype=np.float32)

    def process(self, frame: np.ndarray) -> np.ndarray:
        if self.target_delay_ms <= 0:
            return frame
        delay_samples = int(self.sr * self.target_delay_ms / 1000.0)
        self.buffer = np.concatenate([self.buffer, frame])
        if self.buffer.shape[0] < delay_samples:
            return np.zeros_like(frame)
        out = self.buffer[:frame.shape[0]]
        self.buffer = self.buffer[frame.shape[0]:]
        return out

class VoiceMacroEngine:
    """
    Voice-triggered macros / automation:
    - This stub assumes you have some external speech-to-text or
      you map certain energy patterns to macros.
    - Here we just expose a method to trigger macros by keyword.
    """
    def __init__(self):
        self.macros = config.get("voice_macros", {})

    def trigger_macro(self, keyword: str):
        macro = self.macros.get(keyword.lower())
        if not macro:
            return
        action = macro.get("action")
        if action == "macro_mute_all":
            # Stub: integrate with your own mute logic
            print("[VOICE MACRO] Mute all triggered")
        elif action == "macro_unmute_all":
            print("[VOICE MACRO] Unmute all triggered")
        elif action == "macro_clip":
            print("[VOICE MACRO] Clip that triggered")
        # Log macro
        log_event({"type": "voice_macro", "keyword": keyword, "action": action, "timestamp": time.time()})

# -----------------------------
# AUDIO INTELLIGENCE (V3 core)
# -----------------------------
class NoiseSuppressor:
    def __init__(self, noise_floor=0.0005, attenuation=0.2):
        self.noise_floor = noise_floor
        self.attenuation = attenuation

    def process(self, frame: np.ndarray, energy: float) -> np.ndarray:
        if energy < self.noise_floor:
            return frame * self.attenuation
        return frame

class AutomaticGainControl:
    def __init__(self, target_rms=0.05, max_gain=10.0, smooth=0.1):
        self.target_rms = target_rms
        self.max_gain = max_gain
        self.smooth = smooth
        self.current_gain = 1.0

    def process(self, frame: np.ndarray, energy: float) -> np.ndarray:
        if energy <= 0:
            return frame
        rms = math.sqrt(energy)
        if rms <= 0:
            return frame
        desired_gain = self.target_rms / rms
        desired_gain = max(0.1, min(self.max_gain, desired_gain))
        self.current_gain = (1 - self.smooth) * self.current_gain + self.smooth * desired_gain
        return frame * self.current_gain

class VoiceFingerprintDB:
    def __init__(self):
        self.profiles: Dict[str, Dict[str, float]] = {}

    def _spectral_centroid(self, frame: np.ndarray, sr: int) -> float:
        if frame.size == 0:
            return 0.0
        mag = np.abs(np.fft.rfft(frame))
        freqs = np.fft.rfftfreq(len(frame), 1.0 / sr)
        if np.sum(mag) == 0:
            return 0.0
        return float(np.sum(freqs * mag) / np.sum(mag))

    def enroll(self, speaker_id: str, frame: np.ndarray, sr: int):
        energy = float(np.mean(frame ** 2))
        centroid = self._spectral_centroid(frame, sr)
        self.profiles[speaker_id] = {
            "energy": energy,
            "centroid": centroid
        }

    def identify(self, frame: np.ndarray, sr: int) -> Optional[str]:
        if not self.profiles:
            return None
        energy = float(np.mean(frame ** 2))
        centroid = self._spectral_centroid(frame, sr)
        best_id = None
        best_dist = 1e9
        for sid, prof in self.profiles.items():
            de = energy - prof["energy"]
            dc = centroid - prof["centroid"]
            dist = de * de + dc * dc
            if dist < best_dist:
                best_dist = dist
                best_id = sid
        return best_id

class ClarityOptimizerModel:
    def __init__(self):
        self.weights = np.array([0.6, -0.8, -0.4], dtype=np.float32)
        self.bias = 0.2

    def predict(self, energy: float, clip_ratio: float, noise_estimate: float) -> float:
        x = np.array([energy, clip_ratio, noise_estimate], dtype=np.float32)
        score = float(np.dot(self.weights, x) + self.bias)
        return max(0.0, min(1.0, score))

# -----------------------------
# VOICE MONITOR (V4 chain)
# -----------------------------
class VoiceMonitor:
    def __init__(self):
        self.enabled = bool(config.get("voice_monitor_enabled", True))
        self.sample_rate = int(config.get("voice_sample_rate", 16000))
        self.frame_ms = int(config.get("voice_frame_ms", 30))
        self.energy_threshold = float(config.get("voice_energy_threshold", 0.01))
        self.clip_threshold = float(config.get("voice_clip_threshold", 0.95))

        self.running = False
        self.last_energy = 0.0
        self.last_clip_ratio = 0.0
        self.last_quality_score = 0.0
        self.is_speaking = False
        self.last_error = ""
        self.last_noise_estimate = 0.0
        self.last_clarity_score = 0.0
        self.last_speaker_id: Optional[str] = None

        # Audio chain
        self.noise_suppressor = NoiseSuppressor()
        self.agc = AutomaticGainControl()
        self.voice_db = VoiceFingerprintDB()
        self.clarity_model = ClarityOptimizerModel()
        self.rnnoise = RNNoiseWrapper(config.get("rnnoise_dll_path", "rnnoise.dll"))
        self.deep_enhancer = DeepEnhancer()
        self.latency_comp = LatencyCompensator(sr=self.sample_rate)
        self.macro_engine = VoiceMacroEngine()

        self.noise_history: List[float] = []

    def _maybe_apply_rnnoise(self, frame: np.ndarray) -> np.ndarray:
        if not config.get("rnnoise_enabled", False):
            return frame
        return self.rnnoise.process(frame)

    def _maybe_apply_deep_enhancement(self, frame: np.ndarray) -> np.ndarray:
        if not config.get("deep_enhancement_enabled", False):
            return frame
        return self.deep_enhancer.process(frame, self.sample_rate)

    def _maybe_apply_eq(self, frame: np.ndarray) -> np.ndarray:
        if not config.get("per_game_eq_enabled", True):
            return frame
        eq = get_eq_profile_for_game(CURRENT_GAME_NAME)
        return apply_simple_eq(frame, self.sample_rate, eq)

    def _maybe_apply_latency_comp(self, frame: np.ndarray) -> np.ndarray:
        if not config.get("latency_compensation_enabled", True):
            return frame
        return self.latency_comp.process(frame)

    def _maybe_trigger_macros(self, energy: float, clip_ratio: float):
        if not config.get("voice_macros_enabled", True):
            return
        # Stub: simple heuristic macro trigger
        # Example: if energy is high and clipping is low, treat as "clip that"
        if energy > 0.02 and clip_ratio < 0.1:
            # This is just a demo; in reality you'd use STT
            # to detect actual keywords.
            # We throttle by time if needed.
            pass

    def _audio_callback(self, indata, frames, time_info, status):
        try:
            data = np.array(indata, dtype=np.float32)
            if data.size == 0:
                return

            if data.ndim > 1:
                data = data[:, 0]

            # Basic features
            energy = float(np.mean(data ** 2))
            max_abs = float(np.max(np.abs(data)))
            clip_ratio = float(np.mean(np.abs(data) >= self.clip_threshold))

            # Noise estimate
            if energy < self.energy_threshold:
                self.noise_history.append(energy)
                if len(self.noise_history) > 100:
                    self.noise_history = self.noise_history[-100:]
            noise_est = float(np.mean(self.noise_history)) if self.noise_history else 0.0

            # RNNoise (if enabled)
            data = self._maybe_apply_rnnoise(data)

            # Deep-learning enhancement (if enabled)
            data = self._maybe_apply_deep_enhancement(data)

            # Noise suppression
            if config.get("noise_suppression_enabled", True):
                data = self.noise_suppressor.process(data, energy)

            # AGC
            if config.get("agc_enabled", True):
                data = self.agc.process(data, energy)

            # Per-game EQ
            data = self._maybe_apply_eq(data)

            # Latency compensation
            data = self._maybe_apply_latency_comp(data)

            # VAD + clarity
            self.last_energy = energy
            self.last_clip_ratio = clip_ratio
            self.last_noise_estimate = noise_est
            self.is_speaking = energy > self.energy_threshold

            if config.get("clarity_optimizer_enabled", False):
                clarity = self.clarity_model.predict(energy, clip_ratio, noise_est)
            else:
                clarity = max(0.0, 1.0 - clip_ratio)
            self.last_quality_score = clarity
            self.last_clarity_score = clarity

            # Voice fingerprinting
            if config.get("voice_fingerprint_enabled", False) and self.is_speaking:
                sid = self.voice_db.identify(data, self.sample_rate)
                self.last_speaker_id = sid

            # Voice macros (stub)
            self._maybe_trigger_macros(energy, clip_ratio)

        except Exception as e:
            self.last_error = str(e)

    def loop(self):
        if not self.enabled:
            return
        self.running = True
        frame_len = int(self.sample_rate * (self.frame_ms / 1000.0))
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                blocksize=frame_len,
                callback=self._audio_callback
            ):
                while self.running:
                    time.sleep(0.1)
        except Exception as e:
            self.last_error = f"VoiceMonitor error: {e}"
            self.running = False

# -----------------------------
# MIC SELF-HEALING + PREDICTION + WATCHDOG + FORENSICS
# -----------------------------
EXCLUSIVE_APPS = [
    "discord.exe",
    "teams.exe",
    "zoom.exe",
    "obs64.exe",
    "obs32.exe",
    "slobs.exe",
    "skype.exe"
]

VOICE_APPS = [
    "discord.exe",
    "steam.exe"
]

def get_voice_profile_for_game(game_name: str) -> Optional[Dict[str, Any]]:
    name = (game_name or "").lower()
    profiles = config.get("voice_profiles", {})
    for key, prof in profiles.items():
        if key in name:
            return prof
    return None

class MicSelfHealer:
    def __init__(self, gui_callback=None, voice_monitor: Optional[VoiceMonitor] = None):
        self.gui_callback = gui_callback
        self.running = True
        self.last_state = True
        self.history: List[bool] = []
        self.prediction_window = int(config.get("mic_prediction_window", 10))
        self.prediction_threshold = float(config.get("mic_prediction_threshold", 0.6))
        self.check_interval = float(config.get("mic_check_interval", 5.0))
        self.voice_monitor = voice_monitor

    def restart_audio_service(self):
        start_ts = time.time()
        try:
            subprocess.run(["net", "stop", "audiosrv"], capture_output=True, text=True)
            subprocess.run(["net", "start", "audiosrv"], capture_output=True, text=True)
            time.sleep(3)
        except Exception as e:
            print("Audio restart error:", e)
        end_ts = time.time()
        ev = {
            "type": "audio_crash_forensics",
            "timestamp": time.time(),
            "duration_s": end_ts - start_ts,
            "mic_history": self.history[-20:],
            "method": "service_restart"
        }
        log_event(ev)

    def soft_audio_recovery(self):
        start_ts = time.time()
        ok = False
        try:
            sd.default.reset()
            devices = sd.query_devices()
            input_devices = [d for d in devices if d["max_input_channels"] > 0]
            if input_devices:
                sd.default.device = (None, input_devices[0]["name"])
                ok = True
        except Exception as e:
            print("Soft audio recovery error:", e)
        end_ts = time.time()
        ev = {
            "type": "audio_crash_forensics",
            "timestamp": time.time(),
            "duration_s": end_ts - start_ts,
            "mic_history": self.history[-20:],
            "method": "soft_reinit",
            "success": ok
        }
        log_event(ev)
        return ok

    def mic_available(self):
        try:
            devices = sd.query_devices()
            input_devices = [d for d in devices if d["max_input_channels"] > 0]
            return len(input_devices) > 0
        except Exception:
            return False

    def enforce_preferred_device(self):
        if not config.get("mic_priority_enforcement_enabled", True):
            return
        preferred = config.get("mic_preferred_device_name", "").strip().lower()
        if not preferred:
            return
        try:
            devices = sd.query_devices()
            for idx, d in enumerate(devices):
                name = str(d.get("name", "")).lower()
                if preferred in name and d["max_input_channels"] > 0:
                    sd.default.device = (None, idx)
                    break
        except Exception:
            pass

    def apply_game_voice_profile(self):
        global CURRENT_GAME_NAME
        if not CURRENT_GAME_NAME:
            return
        prof = get_voice_profile_for_game(CURRENT_GAME_NAME)
        if not prof:
            return
        mic_name = str(prof.get("mic_device", "")).strip()
        sr = prof.get("sample_rate", None)
        if mic_name:
            config["mic_preferred_device_name"] = mic_name
            save_config(config)
            self.enforce_preferred_device()
        if sr and self.voice_monitor:
            self.voice_monitor.sample_rate = int(sr)

    def usb_watchdog_reset(self):
        if not config.get("mic_usb_watchdog_enabled", True):
            return
        try:
            cmd = [
                "powershell",
                "-Command",
                "Get-PnpDevice -Class 'AudioEndpoint','Media' | "
                "Where-Object { $_.Status -eq 'OK' -and $_.InstanceId -like '*USB*' } | "
                "Restart-PnpDevice -Confirm:$false"
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except Exception as e:
            print("USB watchdog reset error:", e)

    def kill_exclusive_apps(self):
        if not config.get("mic_exclusive_killer_enabled", True):
            return
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if name in EXCLUSIVE_APPS:
                try:
                    print(f"Killing exclusive-mode app: {name}")
                    proc.terminate()
                except Exception:
                    continue

    def restart_voice_apps(self):
        if not config.get("mic_voice_reconnect_enabled", True):
            return
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            name = (proc.info.get("name") or "").lower()
            exe = proc.info.get("exe") or ""
            if name in VOICE_APPS:
                try:
                    print(f"Restarting voice app: {name}")
                    proc.terminate()
                    time.sleep(2)
                    if exe and os.path.exists(exe):
                        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    continue

    def predict_crash_risk(self) -> float:
        if len(self.history) < self.prediction_window:
            return 0.0
        window = self.history[-self.prediction_window:]
        failures = window.count(False)
        return failures / len(window)

    def heal_loop(self):
        while self.running:
            ok = self.mic_available()
            self.history.append(ok)
            if len(self.history) > 200:
                self.history = self.history[-200:]

            risk = self.predict_crash_risk()
            if risk >= self.prediction_threshold and ok:
                print(f"Mic crash prediction: risk={risk:.2f} — preemptive actions")
                if self.gui_callback:
                    self.gui_callback("Mic instability detected — preemptive stabilization…")
                self.apply_game_voice_profile()
                self.enforce_preferred_device()

            if not ok and self.last_state is True:
                print("Mic failure detected — running auto-repair")
                if self.gui_callback:
                    self.gui_callback("Mic failure detected — repairing…")

                self.kill_exclusive_apps()
                self.usb_watchdog_reset()

                soft_ok = self.soft_audio_recovery()
                if not soft_ok:
                    self.restart_audio_service()

                self.apply_game_voice_profile()
                self.enforce_preferred_device()
                self.restart_voice_apps()

                ok2 = self.mic_available()
                if ok2:
                    print("Mic restored successfully")
                    if self.gui_callback:
                        self.gui_callback("Mic restored automatically")
                    log_event({"type": "mic_repair", "status": "restored", "timestamp": time.time()})
                else:
                    print("Mic still missing after repair")
                    if self.gui_callback:
                        self.gui_callback("Mic still missing — check hardware")
                    log_event({"type": "mic_repair", "status": "failed", "timestamp": time.time()})

            self.last_state = ok
            time.sleep(self.check_interval)

# -----------------------------
# System tray icon
# -----------------------------
def create_tray_icon(on_restore):
    img = Image.new("RGB", (64, 64), "black")
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, 56, 56], outline="white", width=2)
    d.line([8, 56, 56, 8], fill="red", width=2)

    def on_clicked(icon, item):
        if str(item) == "Restore":
            on_restore()

    menu = pystray.Menu(
        pystray.MenuItem("Restore", on_clicked),
        pystray.MenuItem("Exit", lambda icon, item: os._exit(0))
    )
    icon = pystray.Icon("GGUltraAICluster", img, "Game Guardian", menu)
    return icon

# -----------------------------
# GUI
# -----------------------------
class GameGuardianGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("System Network Helper")
        self.root.geometry("1600x900")

        self.dark_mode = config.get("dark_mode", False)
        self.apply_theme()

        self.tray_icon = None
        self.running = True
        self.device_health = DeviceHealth()
        self.last_latencies: List[float] = []
        self.threat_events: List[Dict[str, Any]] = []
        self.playback_running = False

        load_plugins()
        start_ti_server()

        # Voice monitor
        self.voice_monitor = VoiceMonitor()
        threading.Thread(target=self.voice_monitor.loop, daemon=True).start()

        top_frame = tk.Frame(self.root, bg=self.bg_color)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top_frame, text=f"Game Guardian ULTRA AI CLUSTER v{CURRENT_VERSION}", bg=self.bg_color, fg=self.fg_color).pack(side=tk.LEFT, padx=5)

        self.block_selected_btn = tk.Button(
            top_frame, text="Block Selected IP (Admin)", command=self.block_selected_ip, bg=self.btn_bg, fg=self.btn_fg
        )
        self.block_selected_btn.pack(side=tk.LEFT, padx=5)

        self.replay_btn = tk.Button(
            top_frame, text="Replay Log", command=self.replay_log, bg=self.btn_bg, fg=self.btn_fg
        )
        self.replay_btn.pack(side=tk.LEFT, padx=5)

        self.dns_btn = tk.Button(
            top_frame, text="Check DNS Hijack", command=self.check_dns_hijack, bg=self.btn_bg, fg=self.btn_fg
        )
        self.dns_btn.pack(side=tk.LEFT, padx=5)

        self.opt_btn = tk.Button(
            top_frame, text="Optimize Network", command=self.run_optimize_network, bg=self.btn_bg, fg=self.btn_fg
        )
        self.opt_btn.pack(side=tk.LEFT, padx=5)

        self.settings_btn = tk.Button(
            top_frame, text="Settings", command=self.open_settings, bg=self.btn_bg, fg=self.btn_fg
        )
        self.settings_btn.pack(side=tk.LEFT, padx=5)

        self.dark_toggle_btn = tk.Button(
            top_frame, text="Toggle Dark Mode", command=self.toggle_dark_mode, bg=self.btn_bg, fg=self.btn_fg
        )
        self.dark_toggle_btn.pack(side=tk.LEFT, padx=5)

        self.minimize_tray_btn = tk.Button(
            top_frame, text="Minimize to Tray", command=self.minimize_to_tray, bg=self.btn_bg, fg=self.btn_fg
        )
        self.minimize_tray_btn.pack(side=tk.LEFT, padx=5)

        self.playback_btn = tk.Button(
            top_frame, text="Threat Playback", command=self.play_threat_animation, bg=self.btn_bg, fg=self.btn_fg
        )
        self.playback_btn.pack(side=tk.LEFT, padx=5)

        self.update_btn = tk.Button(
            top_frame, text="Check Update", command=self.check_update_button, bg=self.btn_bg, fg=self.btn_fg
        )
        self.update_btn.pack(side=tk.LEFT, padx=5)

        self.cloud_btn = tk.Button(
            top_frame, text="Cloud Sync Now", command=self.cloud_sync_now, bg=self.btn_bg, fg=self.btn_fg
        )
        self.cloud_btn.pack(side=tk.LEFT, padx=5)

        self.train_btn = tk.Button(
            top_frame, text="Train ML Model", command=self.train_ml_button, bg=self.btn_bg, fg=self.btn_fg
        )
        self.train_btn.pack(side=tk.LEFT, padx=5)

        self.audio_map_btn = tk.Button(
            top_frame, text="Audio Routing Map", command=self.show_audio_map, bg=self.btn_bg, fg=self.btn_fg
        )
        self.audio_map_btn.pack(side=tk.LEFT, padx=5)

        self.audio_graph_btn = tk.Button(
            top_frame, text="Audio Graph View", command=self.show_audio_graph_view, bg=self.btn_bg, fg=self.btn_fg
        )
        self.audio_graph_btn.pack(side=tk.LEFT, padx=5)

        mid_frame = tk.Frame(self.root, bg=self.bg_color)
        mid_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ("game", "pid", "local", "remote", "latency",
                   "loss", "country", "region", "isp", "status", "risk", "score")
        self.tree = ttk.Treeview(mid_frame, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col.capitalize())
            self.tree.column(col, width=110)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(mid_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)

        side_frame = tk.Frame(mid_frame, bg=self.bg_color)
        side_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=5)

        tk.Label(side_frame, text="Threat Graph (ML Risk per IP)", bg=self.bg_color, fg=self.fg_color).pack()
        self.graph_canvas = tk.Canvas(side_frame, width=260, height=260, bg="black")
        self.graph_canvas.pack(pady=5)

        tk.Label(side_frame, text="Latency Graph (per scan)", bg=self.bg_color, fg=self.fg_color).pack()
        self.latency_canvas = tk.Canvas(side_frame, width=260, height=260, bg="black")
        self.latency_canvas.pack(pady=5)

        tk.Label(side_frame, text="Threat / Anomaly Summary (AI-style)", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        self.summary_text = tk.Text(side_frame, width=32, height=8, bg="#101010", fg="#FFFFFF", wrap="word")
        self.summary_text.pack(pady=5)
        self.summary_text.insert("1.0", "Threat and anomaly summaries will appear here when events are detected.")
        self.summary_text.config(state="disabled")

        tk.Label(side_frame, text="Audio Intelligence (Voice / Clarity)", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        self.audio_text = tk.Text(side_frame, width=32, height=6, bg="#101010", fg="#FFFFFF", wrap="word")
        self.audio_text.pack(pady=5)
        self.audio_text.insert("1.0", "Voice/VAD/clarity status will appear here.")
        self.audio_text.config(state="disabled")

        bottom_frame = tk.Frame(self.root, bg=self.bg_color)
        bottom_frame.pack(fill=tk.X, padx=5, pady=5)

        self.mouse_label = tk.Label(bottom_frame, text="Mouse: checking...", fg=BLUE, bg=self.bg_color)
        self.mouse_label.pack(side=tk.LEFT, padx=10)

        self.keyboard_label = tk.Label(bottom_frame, text="Keyboard: checking...", fg=BLUE, bg=self.bg_color)
        self.keyboard_label.pack(side=tk.LEFT, padx=10)

        self.mic_label = tk.Label(bottom_frame, text="Mic: checking...", fg=BLUE, bg=self.bg_color)
        self.mic_label.pack(side=tk.LEFT, padx=10)

        self.voice_label = tk.Label(bottom_frame, text="Voice: monitoring…", fg=BLUE, bg=self.bg_color)
        self.voice_label.pack(side=tk.LEFT, padx=10)

        self.dns_label = tk.Label(bottom_frame, text="DNS: unknown", fg=BLUE, bg=self.bg_color)
        self.dns_label.pack(side=tk.LEFT, padx=10)

        self.tree_menu = tk.Menu(self.root, tearoff=0)
        self.tree_menu.add_command(label="Add / Edit IP Note", command=self.add_ip_note)
        self.tree_menu.add_command(label="View IP Note", command=self.view_ip_note)
        self.tree.bind("<Button-3>", self.on_tree_right_click)

        threading.Thread(target=self.device_health.loop, daemon=True).start()
        threading.Thread(target=self.update_loop, daemon=True).start()

        self.mic_healer = MicSelfHealer(gui_callback=self.update_mic_status_text, voice_monitor=self.voice_monitor)
        threading.Thread(target=self.mic_healer.heal_loop, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    # Theme
    def apply_theme(self):
        if self.dark_mode:
            self.bg_color = "#1A1A1A"
            self.fg_color = "#FFFFFF"
            self.btn_bg = "#333333"
            self.btn_fg = "#FFFFFF"
        else:
            self.bg_color = "#F0F0F0"
            self.fg_color = "#000000"
            self.btn_bg = "#DDDDDD"
            self.btn_fg = "#000000"

        style = ttk.Style()
        if self.dark_mode:
            style.theme_use("clam")
            style.configure("Treeview", background="#2A2A2A", foreground="white", fieldbackground="#2A2A2A")
            style.map("Treeview", background=[("selected", "#444444")])
        else:
            style.theme_use("default")

    def toggle_dark_mode(self):
        self.dark_mode = not self.dark_mode
        config["dark_mode"] = self.dark_mode
        save_config(config)
        self.apply_theme()

    # Tray
    def minimize_to_tray(self):
        if self.tray_icon is not None:
            return
        self.root.withdraw()
        self.tray_icon = create_tray_icon(self.restore_from_tray)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def restore_from_tray(self):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.deiconify()

    # Settings
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("650x800")
        win.configure(bg=self.bg_color)

        tk.Label(win, text="Scan Interval (seconds):", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        scan_var = tk.DoubleVar(value=config.get("scan_interval", 3.0))
        tk.Entry(win, textvariable=scan_var).pack(pady=5)

        tk.Label(win, text="Ping Timeout (ms):", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        ping_var = tk.IntVar(value=config.get("ping_timeout_ms", 800))
        tk.Entry(win, textvariable=ping_var).pack(pady=5)

        tk.Label(win, text="GeoIP Timeout (s):", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        geo_var = tk.DoubleVar(value=config.get("geoip_timeout", 2.0))
        tk.Entry(win, textvariable=geo_var).pack(pady=5)

        auto_export_var = tk.BooleanVar(value=config.get("auto_export_logs", True))
        tk.Checkbutton(win, text="Auto-export logs daily", variable=auto_export_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        cloud_var = tk.BooleanVar(value=config.get("cloud_sync_enabled", False))
        tk.Checkbutton(win, text="Enable cloud sync", variable=cloud_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        tk.Label(win, text="Cloud endpoint URL:", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        cloud_ep_var = tk.StringVar(value=config.get("cloud_endpoint", ""))
        tk.Entry(win, textvariable=cloud_ep_var).pack(pady=5)

        ti_var = tk.BooleanVar(value=config.get("ti_enabled", False))
        tk.Checkbutton(win, text="Enable Threat Intelligence Node", variable=ti_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        tk.Label(win, text="TI Port:", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        ti_port_var = tk.IntVar(value=config.get("ti_port", 8099))
        tk.Entry(win, textvariable=ti_port_var).pack(pady=5)

        tk.Label(win, text="TI Peers (comma-separated URLs):", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        ti_peers_var = tk.StringVar(value=",".join(config.get("ti_peers", [])))
        tk.Entry(win, textvariable=ti_peers_var).pack(pady=5)

        tk.Label(win, text="Preferred Mic Device (partial name):", bg=self.bg_color, fg=self.fg_color).pack(pady=5)
        mic_pref_var = tk.StringVar(value=config.get("mic_preferred_device_name", ""))
        tk.Entry(win, textvariable=mic_pref_var).pack(pady=5)

        noise_var = tk.BooleanVar(value=config.get("noise_suppression_enabled", True))
        tk.Checkbutton(win, text="Enable Noise Suppression", variable=noise_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        agc_var = tk.BooleanVar(value=config.get("agc_enabled", True))
        tk.Checkbutton(win, text="Enable Automatic Gain Control (AGC)", variable=agc_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        fp_var = tk.BooleanVar(value=config.get("voice_fingerprint_enabled", False))
        tk.Checkbutton(win, text="Enable Voice Fingerprinting (experimental)", variable=fp_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        clarity_var = tk.BooleanVar(value=config.get("clarity_optimizer_enabled", False))
        tk.Checkbutton(win, text="Enable Clarity Optimizer (experimental)", variable=clarity_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        rnnoise_var = tk.BooleanVar(value=config.get("rnnoise_enabled", False))
        tk.Checkbutton(win, text="Enable RNNoise DLL (if present)", variable=rnnoise_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        deep_var = tk.BooleanVar(value=config.get("deep_enhancement_enabled", False))
        tk.Checkbutton(win, text="Enable Deep Voice Enhancement (PyTorch)", variable=deep_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        eq_var = tk.BooleanVar(value=config.get("per_game_eq_enabled", True))
        tk.Checkbutton(win, text="Enable Per-Game EQ", variable=eq_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        lat_var = tk.BooleanVar(value=config.get("latency_compensation_enabled", True))
        tk.Checkbutton(win, text="Enable Latency Compensation", variable=lat_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        macro_var = tk.BooleanVar(value=config.get("voice_macros_enabled", True))
        tk.Checkbutton(win, text="Enable Voice Macros (stub)", variable=macro_var,
                       bg=self.bg_color, fg=self.fg_color, selectcolor=self.bg_color).pack(pady=5)

        def save_and_close():
            config["scan_interval"] = float(scan_var.get())
            config["ping_timeout_ms"] = int(ping_var.get())
            config["geoip_timeout"] = float(geo_var.get())
            config["auto_export_logs"] = bool(auto_export_var.get())
            config["cloud_sync_enabled"] = bool(cloud_var.get())
            config["cloud_endpoint"] = cloud_ep_var.get().strip()
            config["ti_enabled"] = bool(ti_var.get())
            config["ti_port"] = int(ti_port_var.get())
            peers_str = ti_peers_var.get().strip()
            config["ti_peers"] = [p.strip() for p in peers_str.split(",") if p.strip()]
            config["mic_preferred_device_name"] = mic_pref_var.get().strip()
            config["noise_suppression_enabled"] = bool(noise_var.get())
            config["agc_enabled"] = bool(agc_var.get())
            config["voice_fingerprint_enabled"] = bool(fp_var.get())
            config["clarity_optimizer_enabled"] = bool(clarity_var.get())
            config["rnnoise_enabled"] = bool(rnnoise_var.get())
            config["deep_enhancement_enabled"] = bool(deep_var.get())
            config["per_game_eq_enabled"] = bool(eq_var.get())
            config["latency_compensation_enabled"] = bool(lat_var.get())
            config["voice_macros_enabled"] = bool(macro_var.get())
            save_config(config)
            global SCAN_INTERVAL, PING_TIMEOUT_MS, GEOIP_TIMEOUT
            SCAN_INTERVAL = config["scan_interval"]
            PING_TIMEOUT_MS = int(config["ping_timeout_ms"])
            GEOIP_TIMEOUT = float(config["geoip_timeout"])
            win.destroy()

        tk.Button(win, text="Save", command=save_and_close, bg=self.btn_bg, fg=self.btn_fg).pack(pady=10)

    # Close
    def on_close(self):
        self.running = False
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    # Mic status callback
    def update_mic_status_text(self, text):
        self.mic_label.config(text=text)

    # Actions
    def block_selected_ip(self):
        item = self.tree.selection()
        if not item:
            messagebox.showinfo("Info", "No row selected.")
            return
        values = self.tree.item(item[0], "values")
        remote = values[3]
        if not remote:
            messagebox.showinfo("Info", "No remote IP for this row.")
            return
        ip = remote.split(":")[0]
        if firewall_block_ip(ip):
            messagebox.showinfo("Firewall", f"Blocked IP: {ip}")
            ev = {"type": "manual_block", "ip": ip, "timestamp": time.time()}
            log_event(ev)
            run_plugins(ev)
            send_discord_alert(f"GameGuardian: ADMIN blocked {ip}")
            ti_broadcast_event(ev)
        else:
            messagebox.showerror("Firewall", f"Failed to block IP: {ip}")

    def replay_log(self):
        if not os.path.exists(LOG_FILE):
            messagebox.showinfo("Replay", "No log file found.")
            return
        self.tree.delete(*self.tree.get_children())
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("type") != "connection":
                        continue
                    vals = ev.get("values", [])
                    if vals:
                        self.tree.insert("", tk.END, values=vals)
        except Exception:
            messagebox.showerror("Replay", "Failed to read log file.")

    def check_dns_hijack(self):
        status = dns_hijack_status()
        servers = status["servers"]
        hosts_suspicious = status["hosts_suspicious"]
        text = f"DNS servers: {', '.join(servers) if servers else 'unknown'}; hosts suspicious: {hosts_suspicious}"
        self.dns_label.config(text=f"DNS: {text}", fg=("red" if hosts_suspicious else "green"))
        ev = {"type": "dns_check", "status": status, "timestamp": time.time()}
        log_event(ev)
        run_plugins(ev)
        ti_broadcast_event(ev)
        messagebox.showinfo("DNS Status", text)

    def run_optimize_network(self):
        ok = optimize_network()
        if ok:
            messagebox.showinfo("Network", "Network optimization commands executed.\nYou may need to reconnect.")
            ev = {"type": "optimize_network", "timestamp": time.time()}
            log_event(ev)
            run_plugins(ev)
            ti_broadcast_event(ev)
        else:
            messagebox.showerror("Network", "Failed to run optimization commands.")

    def check_update_button(self):
        latest = check_for_update()
        if latest:
            messagebox.showinfo("Update", f"New version available: {latest}\n(Current: {CURRENT_VERSION})")
        else:
            messagebox.showinfo("Update", "No update information or already up to date.")

    def cloud_sync_now(self):
        cloud_sync_push()
        cloud_sync_pull()
        messagebox.showinfo("Cloud Sync", "Cloud sync attempted (stub).")

    def train_ml_button(self):
        ok = train_ml_model_from_logs()
        if ok:
            load_ml_model()
            messagebox.showinfo("ML Training", "Training complete. Model saved and loaded.")
        else:
            messagebox.showerror("ML Training", "Training failed or not enough data.")

    def show_audio_map(self):
        info = get_audio_routing_map()
        win = tk.Toplevel(self.root)
        win.title("Audio Routing Map")
        win.geometry("640x480")
        txt = tk.Text(win, wrap="word")
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert("1.0", json.dumps(info, indent=2))
        txt.config(state="disabled")

    def show_audio_graph_view(self):
        info = get_audio_routing_map()
        devices = info.get("devices", [])
        hostapis = info.get("hostapis", [])

        win = tk.Toplevel(self.root)
        win.title("Audio Graph Visualization")
        win.geometry("700x500")
        canvas = tk.Canvas(win, bg="black")
        canvas.pack(fill=tk.BOTH, expand=True)

        w = 700
        h = 500
        margin = 60

        api_positions = {}
        if hostapis:
            step = (w - 2 * margin) / max(1, len(hostapis))
            for i, api in enumerate(hostapis):
                x = margin + i * step + step / 2
                y = margin
                name = api.get("name", f"API{i}")
                canvas.create_oval(x-40, y-20, x+40, y+20, outline="cyan", width=2)
                canvas.create_text(x, y, text=name, fill="white", font=("Arial", 9))
                api_positions[i] = (x, y)

        if devices:
            step = (w - 2 * margin) / max(1, len(devices))
            for i, dev in enumerate(devices):
                x = margin + i * step + step / 2
                y = h - margin
                name = dev.get("name", f"Dev{i}")
                api_index = dev.get("hostapi", 0)
                canvas.create_rectangle(x-50, y-20, x+50, y+20, outline="green", width=2)
                canvas.create_text(x, y, text=name[:18], fill="white", font=("Arial", 8))
                if api_index in api_positions:
                    ax, ay = api_positions[api_index]
                    canvas.create_line(ax, ay+20, x, y-20, fill="yellow")

    # IP notes
    def on_tree_right_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if row_id:
            self.tree.selection_set(row_id)
            self.tree_menu.post(event.x_root, event.y_root)

    def get_selected_ip(self) -> Optional[str]:
        item = self.tree.selection()
        if not item:
            return None
        values = self.tree.item(item[0], "values")
        remote = values[3]
        if not remote:
            return None
        return remote.split(":")[0]

    def add_ip_note(self):
        ip = self.get_selected_ip()
        if not ip:
            messagebox.showinfo("IP Note", "No IP selected.")
            return
        current = ip_notes.get(ip, "")
        note = simpledialog.askstring("IP Note", f"Note for {ip}:", initialvalue=current)
        if note is not None:
            ip_notes[ip] = note
            save_ip_notes(ip_notes)

    def view_ip_note(self):
        ip = self.get_selected_ip()
        if not ip:
            messagebox.showinfo("IP Note", "No IP selected.")
            return
        note = ip_notes.get(ip, "(no note)")
        messagebox.showinfo("IP Note", f"{ip}:\n\n{note}")

    # Loop
    def update_loop(self):
        while self.running:
            self.update_table()
            self.update_device_panel()
            self.update_threat_graph()
            self.update_latency_graph()
            self.update_voice_panel()
            auto_export_logs()
            cloud_sync_push()
            time.sleep(SCAN_INTERVAL)

    def update_device_panel(self):
        if self.device_health.mouse_ok:
            self.mouse_label.config(text="Mouse: OK", fg="green")
        else:
            self.mouse_label.config(text="Mouse: no activity yet", fg=BLUE)

        if self.device_health.keyboard_ok:
            self.keyboard_label.config(text="Keyboard: OK", fg="green")
        else:
            self.keyboard_label.config(text="Keyboard: no activity yet", fg=BLUE)

        if self.device_health.mic_ok:
            if "Mic restored" in self.mic_label.cget("text"):
                pass
            else:
                self.mic_label.config(text="Mic: OK", fg="green")
        else:
            if "Mic failure" not in self.mic_label.cget("text") and "Mic still missing" not in self.mic_label.cget("text"):
                self.mic_label.config(text="Mic: NOT DETECTED", fg="red")

    def update_voice_panel(self):
        vm = self.voice_monitor
        if not vm.enabled:
            self.voice_label.config(text="Voice: monitor disabled", fg=BLUE)
            return
        if vm.last_error:
            self.voice_label.config(text=f"Voice: error ({vm.last_error})", fg="red")
            return
        speaking = "speaking" if vm.is_speaking else "idle"
        quality = vm.last_quality_score
        clip = vm.last_clip_ratio
        clarity = vm.last_clarity_score
        sid = vm.last_speaker_id or "unknown"

        self.voice_label.config(
            text=f"Voice: {speaking}, clarity={clarity:.2f}, clip={clip:.2f}",
            fg=("green" if clarity > 0.7 else BLUE)
        )

        self.audio_text.config(state="normal")
        self.audio_text.delete("1.0", "end")
        self.audio_text.insert(
            "1.0",
            f"Energy: {vm.last_energy:.5f}\n"
            f"Clip ratio: {clip:.2f}\n"
            f"Noise estimate: {vm.last_noise_estimate:.5f}\n"
            f"Clarity score: {clarity:.2f}\n"
            f"Speaker ID (guess): {sid}\n"
            f"Noise suppression: {'ON' if config.get('noise_suppression_enabled', True) else 'OFF'}\n"
            f"AGC: {'ON' if config.get('agc_enabled', True) else 'OFF'}\n"
            f"RNNoise: {'ON' if config.get('rnnoise_enabled', False) else 'OFF'}\n"
            f"Deep enhancement: {'ON' if config.get('deep_enhancement_enabled', False) else 'OFF'}\n"
            f"Per-game EQ: {'ON' if config.get('per_game_eq_enabled', True) else 'OFF'}\n"
            f"Latency comp: {'ON' if config.get('latency_compensation_enabled', True) else 'OFF'}\n"
        )
        self.audio_text.config(state="disabled")

    def update_table(self):
        global CURRENT_GAME_NAME
        self.tree.delete(*self.tree.get_children())

        games = detect_games()
        self.last_latencies.clear()

        for proc in games:
            pid = proc.info["pid"]
            exe = proc.info.get("name") or "unknown"
            CURRENT_GAME_NAME = exe

            try:
                conns = proc.connections(kind="inet")
            except Exception:
                continue

            for c in conns:
                local = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
                remote = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
                status = c.status

                latency_str = ""
                loss_str = ""
                latency_ms = None
                loss_pct = None
                country = "?"
                region = "?"
                isp = "?"
                risk = "OK"
                score = 0

                if c.raddr:
                    host = c.raddr.ip
                    ping_out = ping_host_raw(host)
                    stats = parse_ping_stats(ping_out or "")
                    latency_ms = stats["avg_ms"]
                    loss_pct = stats["loss_pct"]
                    latency_str = f"{latency_ms:.1f} ms" if latency_ms is not None else "timeout"
                    loss_str = f"{loss_pct:.1f}%" if loss_pct is not None else "?"

                    if latency_ms is not None:
                        self.last_latencies.append(latency_ms)

                    geo = geoip_lookup(host)
                    country = geo["country"]
                    region = geo["region"]
                    isp = geo["isp"]

                    base_score = compute_base_risk(host, latency_ms, loss_pct, geo)
                    score = compute_ml_risk(host, base_score, exe, latency_ms, loss_pct)
                    risk = risk_label(score)

                    anomaly = detect_anomaly(latency_ms, loss_pct)

                    if risk in ("SUSPICIOUS", "DANGEROUS"):
                        ev = {
                            "type": "high_risk",
                            "ip": host,
                            "risk": risk,
                            "score": score,
                            "timestamp": time.time(),
                            "latency_ms": latency_ms,
                            "loss_pct": loss_pct,
                            "geo": geo
                        }
                        log_event(ev)
                        run_plugins(ev)
                        self.threat_events.append(ev)
                        summary = generate_threat_summary(ev)
                        self.update_summary_text(summary)
                        send_discord_alert(f"GameGuardian: HIGH RISK {host} ({risk}, score={score})")
                        ti_broadcast_event(ev)

                    if anomaly:
                        ev_anom = {
                            "type": "anomaly",
                            "ip": host,
                            "timestamp": time.time(),
                            "latency_ms": latency_ms,
                            "loss_pct": loss_pct
                        }
                        log_event(ev_anom)
                        run_plugins(ev_anom)
                        summary = generate_anomaly_summary(latency_ms, loss_pct)
                        self.update_summary_text(summary)
                        send_discord_alert("GameGuardian: Network anomaly detected.")
                        ti_broadcast_event(ev_anom)

                values = (
                    exe, pid, local, remote, latency_str,
                    loss_str, country, region, isp, status, risk, score
                )
                self.tree.insert("", tk.END, values=values)

                ev_conn = {
                    "type": "connection",
                    "timestamp": time.time(),
                    "values": values
                }
                log_event(ev_conn)
                run_plugins(ev_conn)

    def update_summary_text(self, text: str):
        self.summary_text.config(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", text)
        self.summary_text.config(state="disabled")

    def update_threat_graph(self):
        self.graph_canvas.delete("all")
        items = self.tree.get_children()
        if not items:
            return

        scores = []
        for item in items[:10]:
            vals = self.tree.item(item, "values")
            try:
                score = int(vals[11])
            except Exception:
                score = 0
            scores.append(score)

        if not scores:
            return

        max_score = max(scores) or 1
        width = 260
        height = 260
        bar_width = width / len(scores)

        for i, score in enumerate(scores):
            x0 = i * bar_width + 5
            x1 = (i + 1) * bar_width - 5
            bar_height = (score / max_score) * (height - 40)
            y0 = height - 10 - bar_height
            y1 = height - 10

            color = "green"
            if score >= 80:
                color = "red"
            elif score >= 50:
                color = BLUE
            elif score >= 20:
                color = BLUE

            self.graph_canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            self.graph_canvas.create_text(
                (x0 + x1) / 2, y0 - 10,
                text=str(score),
                fill="white",
                font=("Arial", 8)
            )

    def update_latency_graph(self):
        self.latency_canvas.delete("all")
        if not self.last_latencies:
            return

        latencies = self.last_latencies[:20]
        max_lat = max(latencies) or 1
        width = 260
        height = 260
        bar_width = width / len(latencies)

        for i, lat in enumerate(latencies):
            x0 = i * bar_width + 5
            x1 = (i + 1) * bar_width - 5
            bar_height = (lat / max_lat) * (height - 40)
            y0 = height - 10 - bar_height
            y1 = height - 10

            color = "green"
            if lat > 150:
                color = "red"
            elif lat > 80:
                color = BLUE
            elif lat > 40:
                color = BLUE

            self.latency_canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            self.latency_canvas.create_text(
                (x0 + x1) / 2, y0 - 10,
                text=f"{lat:.0f}",
                fill="white",
                font=("Arial", 8)
            )

    def play_threat_animation(self):
        if self.playback_running:
            return
        if not self.threat_events:
            messagebox.showinfo("Threat Playback", "No high-risk events recorded yet.")
            return

        self.playback_running = True

        def run():
            self.graph_canvas.delete("all")
            width = 260
            height = 260
            for ev in self.threat_events[-20:]:
                if not self.playback_running:
                    break
                ip = ev.get("ip", "?")
                score = ev.get("score", 0)
                self.graph_canvas.delete("all")
                bar_height = (score / 100.0) * (height - 40)
                x0, x1 = 50, width - 50
                y0 = height - 10 - bar_height
                y1 = height - 10
                color = "green"
                if score >= 80:
                    color = "red"
                elif score >= 50:
                    color = BLUE
                elif score >= 20:
                    color = BLUE
                self.graph_canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
                self.graph_canvas.create_text(
                    width / 2, y0 - 15,
                    text=f"{ip} ({score})",
                    fill="white",
                    font=("Arial", 9)
                )
                time.sleep(0.7)
            self.playback_running = False

        threading.Thread(target=run, daemon=True).start()

# -----------------------------
# Entry
# -----------------------------
if __name__ == "__main__":
    GameGuardianGUI()
