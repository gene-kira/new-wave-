#!/usr/bin/env python
# game_guardian_ultra_pro.py
#
# Game Guardian ULTRA PRO:
# - Real-time game connection monitor (ports, IPs, latency, loss, GeoIP, risk)
# - Tuned ML-style risk engine (per-IP history + per-game profiles)
# - Settings menu (JSON config + GUI toggles)
# - System tray mode (minimize to tray, restore)
# - Dark-mode toggle (light/dark themes)
# - Per-game profiles (risk multipliers per game)
# - Auto-export logs (daily export)
# - Live latency graph (per-scan latency bars)
# - DNS hijack detection
# - Network auto-optimization
# - Device health panel (mouse / keyboard / mic)
# - Manual-only firewall blocking (admin decides)
# - Stealth window title
#
# Run as Administrator.

import os
import sys
import time
import threading
import subprocess
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

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
import pystray
from PIL import Image, ImageDraw

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
# Config / Settings
# -----------------------------
CONFIG_FILE = "gg_ultra_settings.json"
LOG_FILE = "game_guardian_log.jsonl"
DISCORD_WEBHOOK_URL = ""  # optional

DEFAULT_CONFIG = {
    "scan_interval": 3.0,
    "ping_timeout_ms": 800,
    "geoip_timeout": 2.0,
    "dark_mode": False,
    "auto_export_logs": True,
    "game_profiles": {
        "cs2": 1.0,
        "fortnite": 1.0,
        "apex": 1.0,
        "valorant": 1.0,
        "warzone": 1.0
    }
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # merge defaults
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
# ML-style risk engine
# -----------------------------
ip_risk_history: Dict[str, List[int]] = {}

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

def compute_ml_risk(ip: str, base_score: int, game_name: str) -> int:
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

    mult = get_game_profile_multiplier(game_name)
    base_score = int(base_score * mult)

    base_score = max(0, min(100, base_score))
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
    icon = pystray.Icon("GGUltra", img, "Game Guardian", menu)
    return icon

# -----------------------------
# GUI
# -----------------------------
class GameGuardianGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("System Network Helper")
        self.root.geometry("1400x750")

        self.dark_mode = config.get("dark_mode", False)
        self.apply_theme()

        # System tray
        self.tray_icon = None

        # Top frame: controls
        top_frame = tk.Frame(self.root, bg=self.bg_color)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top_frame, text="Game Guardian ULTRA PRO", bg=self.bg_color, fg=self.fg_color).pack(side=tk.LEFT, padx=5)

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

        # Middle frame: table + threat graph + latency graph
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
        self.graph_canvas = tk.Canvas(side_frame, width=250, height=300, bg="black")
        self.graph_canvas.pack(pady=5)

        tk.Label(side_frame, text="Latency Graph (per scan)", bg=self.bg_color, fg=self.fg_color).pack()
        self.latency_canvas = tk.Canvas(side_frame, width=250, height=300, bg="black")
        self.latency_canvas.pack(pady=5)

        # Bottom frame: device health + DNS status
        bottom_frame = tk.Frame(self.root, bg=self.bg_color)
        bottom_frame.pack(fill=tk.X, padx=5, pady=5)

        self.mouse_label = tk.Label(bottom_frame, text="Mouse: checking...", fg=BLUE, bg=self.bg_color)
        self.mouse_label.pack(side=tk.LEFT, padx=10)

        self.keyboard_label = tk.Label(bottom_frame, text="Keyboard: checking...", fg=BLUE, bg=self.bg_color)
        self.keyboard_label.pack(side=tk.LEFT, padx=10)

        self.mic_label = tk.Label(bottom_frame, text="Mic: checking...", fg=BLUE, bg=self.bg_color)
        self.mic_label.pack(side=tk.LEFT, padx=10)

        self.dns_label = tk.Label(bottom_frame, text="DNS: unknown", fg=BLUE, bg=self.bg_color)
        self.dns_label.pack(side=tk.LEFT, padx=10)

        self.running = True
        self.device_health = DeviceHealth()
        self.last_latencies: List[float] = []

        threading.Thread(target=self.device_health.loop, daemon=True).start()
        threading.Thread(target=self.update_loop, daemon=True).start()

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
        win.geometry("400x300")
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

        def save_and_close():
            config["scan_interval"] = float(scan_var.get())
            config["ping_timeout_ms"] = int(ping_var.get())
            config["geoip_timeout"] = float(geo_var.get())
            config["auto_export_logs"] = bool(auto_export_var.get())
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
            log_event({"type": "manual_block", "ip": ip, "timestamp": time.time()})
            send_discord_alert(f"GameGuardian: ADMIN blocked {ip}")
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
        messagebox.showinfo("DNS Status", text)
        log_event({"type": "dns_check", "status": status, "timestamp": time.time()})

    def run_optimize_network(self):
        ok = optimize_network()
        if ok:
            messagebox.showinfo("Network", "Network optimization commands executed.\nYou may need to reconnect.")
            log_event({"type": "optimize_network", "timestamp": time.time()})
        else:
            messagebox.showerror("Network", "Failed to run optimization commands.")

    # Loop
    def update_loop(self):
        while self.running:
            self.update_table()
            self.update_device_panel()
            self.update_threat_graph()
            self.update_latency_graph()
            auto_export_logs()
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
            self.mic_label.config(text="Mic: OK", fg="green")
        else:
            self.mic_label.config(text="Mic: NOT DETECTED", fg="red")

    def update_table(self):
        self.tree.delete(*self.tree.get_children())

        games = detect_games()
        self.last_latencies.clear()

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
                    score = compute_ml_risk(host, base_score, exe)
                    risk = risk_label(score)

                    if risk in ("SUSPICIOUS", "DANGEROUS"):
                        log_event({
                            "type": "high_risk",
                            "ip": host,
                            "risk": risk,
                            "score": score,
                            "timestamp": time.time()
                        })
                        send_discord_alert(f"GameGuardian: HIGH RISK {host} ({risk}, score={score})")

                values = (
                    exe, pid, local, remote, latency_str,
                    loss_str, country, region, isp, status, risk, score
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

        scores = []
        for item in items[:10]:
            vals = self.tree.item(item, "values")
            score = int(vals[11])
            scores.append(score)

        if not scores:
            return

        max_score = max(scores) or 1
        width = 250
        height = 300
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

    def update_latency_graph(self):
        self.latency_canvas.delete("all")
        if not self.last_latencies:
            return

        latencies = self.last_latencies[:20]
        max_lat = max(latencies) or 1
        width = 250
        height = 300
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
                color = "orange"
            elif lat > 40:
                color = "yellow"

            self.latency_canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            self.latency_canvas.create_text(
                (x0 + x1) / 2, y0 - 10,
                text=f"{lat:.0f}",
                fill="white",
                font=("Arial", 8)
            )

# -----------------------------
# Entry
# -----------------------------
if __name__ == "__main__":
    GameGuardianGUI()
