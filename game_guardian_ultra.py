#!/usr/bin/env python
# game_guardian_ultra.py
#
# Windows 11 Game Guardian ULTRA:
# - GUI showing game connections (ports, IPs, latency, GeoIP, risk)
# - Threat graph panel (risk bars)
# - AI-style risk engine (scoring based on latency, GeoIP, IP type)
# - Auto-installs required libs (psutil, sounddevice, pynput, requests)
# - Detects Steam/Epic/major games
# - GeoIP lookup for remote IPs
# - Auto-block suspicious IPs via Windows Firewall
# - Device health panel (mouse / keyboard / mic)
# - Logging + replay of events
# - Discord alert bot (webhook) for high-risk events
# - Stealth mode (generic window title, no tray, no overlays)
#
# Run as Administrator.

import os
import sys
import time
import threading
import subprocess
import json
from typing import List, Dict, Any, Optional

# -----------------------------
# Autoloader
# -----------------------------
REQUIRED_LIBS = [
    "psutil",
    "sounddevice",
    "pynput",
    "requests",
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
from tkinter import ttk, messagebox

# -----------------------------
# Admin check
# -----------------------------
def is_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def relaunch_as_admin():
    if is_admin():
        return
    import ctypes
    params = " ".join([f'"{arg}"' for arg in sys.argv])
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )
    sys.exit(0)

relaunch_as_admin()

# -----------------------------
# Config
# -----------------------------
GAME_HINTS = [
    "steam", "epic", "fortnite", "cs2", "apex", "valorant", "gta", "warzone"
]

SCAN_INTERVAL = 3.0
PING_TIMEOUT_MS = 800
GEOIP_TIMEOUT = 2.0

AUTO_BLOCK_SUSPICIOUS = True
LOG_FILE = "game_guardian_log.jsonl"
DISCORD_WEBHOOK_URL = ""  # put your webhook URL here if you want alerts

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

def ping_host(host: str) -> Optional[float]:
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(PING_TIMEOUT_MS), host],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if "Average" in line:
                avg = line.split("Average =")[-1].strip()
                if avg.endswith("ms"):
                    avg = avg[:-2]
                return float(avg)
        return None
    except Exception:
        return None

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

# -----------------------------
# AI-style risk engine
# -----------------------------
def compute_risk(ip: str, latency_ms: Optional[float], geo: Dict[str, Any]) -> int:
    score = 0

    # Base: private IPs are high risk in online gaming
    if is_private_ip(ip):
        score += 60

    # Latency-based risk
    if latency_ms is None:
        score += 20
    else:
        if latency_ms > 150:
            score += 30
        elif latency_ms > 80:
            score += 15

    # GeoIP-based risk
    isp = geo.get("isp", "").lower()
    asn = geo.get("as", "").lower()

    # Datacenter / VPN hints
    risky_keywords = ["vpn", "hosting", "datacenter", "cloud", "colo", "m247", "ovh", "digitalocean"]
    if any(k in isp for k in risky_keywords) or any(k in asn for k in risky_keywords):
        score += 30

    # Country-based mild risk (example: unknown or ?)
    if geo.get("country", "?") == "?":
        score += 10

    # Clamp
    score = max(0, min(100, score))
    return score

def risk_label(score: int) -> str:
    if score < 20:
        return "OK"
    elif score < 50:
        return "CAUTION"
    elif score < 80:
        return "SUSPICIOUS"
    else:
        return "DANGEROUS"

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
# GUI
# -----------------------------
class GameGuardianGUI:
    def __init__(self):
        # Stealth mode: generic title
        self.root = tk.Tk()
        self.root.title("System Network Helper")
        self.root.geometry("1250x650")

        # Top frame: controls
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        self.auto_block_var = tk.BooleanVar(value=AUTO_BLOCK_SUSPICIOUS)
        tk.Checkbutton(
            top_frame,
            text="Auto-block suspicious IPs",
            variable=self.auto_block_var
        ).pack(side=tk.LEFT, padx=5)

        self.block_selected_btn = tk.Button(
            top_frame, text="Block Selected IP", command=self.block_selected_ip
        )
        self.block_selected_btn.pack(side=tk.LEFT, padx=5)

        self.replay_btn = tk.Button(
            top_frame, text="Replay Log", command=self.replay_log
        )
        self.replay_btn.pack(side=tk.LEFT, padx=5)

        # Middle frame: table + threat graph
        mid_frame = tk.Frame(self.root)
        mid_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ("game", "pid", "local", "remote", "latency",
                   "country", "region", "isp", "status", "risk", "score")
        self.tree = ttk.Treeview(mid_frame, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col.capitalize())
            self.tree.column(col, width=110)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(mid_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)

        # Threat graph panel (simple bar graph)
        graph_frame = tk.Frame(mid_frame)
        graph_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=5)

        tk.Label(graph_frame, text="Threat Graph (Risk per IP)").pack()
        self.graph_canvas = tk.Canvas(graph_frame, width=250, height=400, bg="black")
        self.graph_canvas.pack()

        # Bottom frame: device health
        bottom_frame = tk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=5, pady=5)

        self.mouse_label = tk.Label(bottom_frame, text="Mouse: checking...", fg="yellow")
        self.mouse_label.pack(side=tk.LEFT, padx=10)

        self.keyboard_label = tk.Label(bottom_frame, text="Keyboard: checking...", fg="yellow")
        self.keyboard_label.pack(side=tk.LEFT, padx=10)

        self.mic_label = tk.Label(bottom_frame, text="Mic: checking...", fg="yellow")
        self.mic_label.pack(side=tk.LEFT, padx=10)

        self.running = True
        self.device_health = DeviceHealth()

        threading.Thread(target=self.device_health.loop, daemon=True).start()
        threading.Thread(target=self.update_loop, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        self.running = False
        self.root.destroy()

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
            log_event({"type": "manual_block", "ip": ip, "timestamp": time.time()})
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

    def update_loop(self):
        while self.running:
            self.update_table()
            self.update_device_panel()
            self.update_threat_graph()
            time.sleep(SCAN_INTERVAL)

    def update_device_panel(self):
        if self.device_health.mouse_ok:
            self.mouse_label.config(text="Mouse: OK", fg="green")
        else:
            self.mouse_label.config(text="Mouse: no activity yet", fg="orange")

        if self.device_health.keyboard_ok:
            self.keyboard_label.config(text="Keyboard: OK", fg="green")
        else:
            self.keyboard_label.config(text="Keyboard: no activity yet", fg="orange")

        if self.device_health.mic_ok:
            self.mic_label.config(text="Mic: OK", fg="green")
        else:
            self.mic_label.config(text="Mic: NOT DETECTED", fg="red")

    def update_table(self):
        self.tree.delete(*self.tree.get_children())

        games = detect_games()
        seen_ips = set()

        for proc in games:
            pid = proc.info["pid"]
            exe = proc.info.get("name") or "unknown"

            try:
                conns = proc.connections(kind="inet")
            except Exception:
                continue

            for c in conns:
                local = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
                remote = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
                status = c.status

                latency_str = ""
                latency_ms = None
                country = "?"
                region = "?"
                isp = "?"
                risk = "OK"
                score = 0

                if c.raddr:
                    host = c.raddr.ip
                    latency_ms = ping_host(host)
                    latency_str = f"{latency_ms:.1f} ms" if latency_ms is not None else "timeout"

                    geo = geoip_lookup(host)
                    country = geo["country"]
                    region = geo["region"]
                    isp = geo["isp"]

                    score = compute_risk(host, latency_ms, geo)
                    risk = risk_label(score)

                    if self.auto_block_var.get() and risk in ("SUSPICIOUS", "DANGEROUS"):
                        if host not in seen_ips:
                            if firewall_block_ip(host):
                                log_event({
                                    "type": "auto_block",
                                    "ip": host,
                                    "risk": risk,
                                    "score": score,
                                    "timestamp": time.time()
                                })
                                send_discord_alert(f"GameGuardian: auto-blocked {host} (risk={risk}, score={score})")
                            seen_ips.add(host)

                values = (
                    exe, pid, local, remote, latency_str,
                    country, region, isp, status, risk, score
                )
                self.tree.insert("", tk.END, values=values)

                log_event({
                    "type": "connection",
                    "timestamp": time.time(),
                    "values": values
                })

    def update_threat_graph(self):
        self.graph_canvas.delete("all")
        items = self.tree.get_children()
        if not items:
            return

        # Collect risk scores per IP
        scores = []
        labels = []
        for item in items[:10]:  # limit to top 10
            vals = self.tree.item(item, "values")
            remote = vals[3]
            score = int(vals[10])
            if remote:
                ip = remote.split(":")[0]
                labels.append(ip)
                scores.append(score)

        if not scores:
            return

        max_score = max(scores) or 1
        width = 250
        height = 400
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
                color = "orange"
            elif score >= 20:
                color = "yellow"

            self.graph_canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            self.graph_canvas.create_text(
                (x0 + x1) / 2, y0 - 10,
                text=str(score),
                fill="white",
                font=("Arial", 8)
            )

# -----------------------------
# Entry
# -----------------------------
if __name__ == "__main__":
    GameGuardianGUI()
