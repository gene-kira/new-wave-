import os
import sys
import subprocess
import time
import psutil
import base64
import logging
import tkinter as tk
from tkinter import ttk
import threading
import socket
import json
import statistics

# ============================================================
# AUTOLOADER FOR REQUIRED LIBRARIES
# ============================================================

REQUIRED_LIBS = ["psutil"]

def autoloader():
    for lib in REQUIRED_LIBS:
        try:
            __import__(lib)
        except ImportError:
            subprocess.call([sys.executable, "-m", "pip", "install", lib])

autoloader()

# ============================================================
# LOGGER SETUP
# ============================================================

logging.basicConfig(
    filename="colonel_hook.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def log(msg):
    print(msg)
    logging.info(msg)

# ============================================================
# WHITELIST (SAFE PROCESSES)
# ============================================================

WHITELIST = [
    "python.exe",
    "pythonw.exe",
    "explorer.exe",
    "cmd.exe",
    "powershell.exe",
    "System",
    "Registry",
]

# ============================================================
# AI-STYLE ANOMALY DETECTION (SIMPLE STATS)
# ============================================================

SCORE_HISTORY = []

def register_score(score):
    SCORE_HISTORY.append(score)
    if len(SCORE_HISTORY) > 500:
        SCORE_HISTORY.pop(0)

def is_anomalous(score):
    if len(SCORE_HISTORY) < 20:
        return False
    mean = statistics.mean(SCORE_HISTORY)
    stdev = statistics.pstdev(SCORE_HISTORY)
    if stdev == 0:
        return False
    return score > mean + 2 * stdev

# ============================================================
# LIVE SYSTEM DATA ENGINE HELPERS
# ============================================================

def get_live_system_data(proc):
    data = {
        "cpu": 0.0,
        "mem": 0.0,
        "connections": 0,
        "threads": 0
    }
    try:
        data["cpu"] = proc.cpu_percent(interval=0.0)
        data["mem"] = proc.memory_info().rss / (1024 * 1024)  # MB
        data["connections"] = len(proc.connections(kind="inet"))
        data["threads"] = proc.num_threads()
    except Exception:
        pass
    return data

# ============================================================
# BEHAVIOR SCORING ENGINE
# ============================================================

def score_powershell_behavior(cmdline, parent_name, proc):
    score = 0

    # Classic behavioral indicators
    if "-nop" in cmdline.lower(): score += 20
    if "-w hidden" in cmdline.lower(): score += 20
    if "-enc" in cmdline.lower(): score += 30

    bad_parents = ["chrome.exe", "winword.exe", "excel.exe", "svchost.exe"]
    if parent_name.lower() in bad_parents:
        score += 40

    if "-enc" in cmdline.lower():
        try:
            encoded = cmdline.split("-enc")[1].strip()
            base64.b64decode(encoded + "===")
            score += 30
        except:
            pass

    net_keywords = ["invoke-webrequest", "downloadstring", "restmethod", "http", "https"]
    if any(k in cmdline.lower() for k in net_keywords):
        score += 40

    # Memory-style heuristics / fileless hints
    live = get_live_system_data(proc)
    if not cmdline.strip():
        score += 20  # no visible script → possible fileless
    if live["connections"] > 3:
        score += 20
    if live["threads"] > 20:
        score += 10
    if live["cpu"] > 50.0:
        score += 10

    return score, live

# ============================================================
# AGGRESSIVE BLOCKING (RESPECTS WHITELIST)
# ============================================================

def kill_process(pid, name):
    if name.lower() in WHITELIST:
        log(f"[SAFE] {name} is whitelisted — not killing")
        return False

    try:
        p = psutil.Process(pid)
        p.terminate()
        log(f"[BLOCKED] Terminated suspicious PowerShell PID {pid}")
        return True
    except Exception as e:
        log(f"[ERROR] Could not terminate PID {pid}: {e}")
        return False

# ============================================================
# SWARM SYNC (LAN BROADCAST)
# ============================================================

SWARM_PORT = 50000
SWARM_GROUP = "<broadcast>"

class SwarmSync:
    def __init__(self, gui):
        self.gui = gui
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.listener = threading.Thread(target=self.listen_loop, daemon=True)
        self.listener.start()

    def broadcast_event(self, event):
        try:
            data = json.dumps(event).encode("utf-8")
            self.sock.sendto(data, (SWARM_GROUP, SWARM_PORT))
        except Exception as e:
            log(f"[SWARM] Broadcast error: {e}")

    def listen_loop(self):
        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listen_sock.bind(("", SWARM_PORT))
        except Exception as e:
            log(f"[SWARM] Bind error: {e}")
            return

        while self.running:
            try:
                data, addr = listen_sock.recvfrom(4096)
                event = json.loads(data.decode("utf-8"))
                self.gui.add_timeline_entry_remote(event, addr[0])
            except Exception:
                pass

# ============================================================
# GUI DASHBOARD CLASS
# ============================================================

class ColonelHookGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Colonel Hook — Live Security Dashboard")
        self.root.geometry("1300x750")
        self.root.configure(bg="#0a0f14")

        title = tk.Label(self.root, text="Colonel Hook Security Dashboard",
                         font=("Segoe UI", 20, "bold"), fg="#00ffff", bg="#0a0f14")
        title.pack(pady=10)

        main_frame = tk.Frame(self.root, bg="#0a0f14")
        main_frame.pack(fill="both", expand=True)

        left_frame = tk.Frame(main_frame, bg="#0a0f14")
        left_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        right_frame = tk.Frame(main_frame, bg="#0a0f14")
        right_frame.pack(side="right", fill="y", padx=5, pady=5)

        columns = ("PID", "Parent", "Score", "Status", "CPU%", "Mem(MB)", "Conns", "Threads", "Command")
        self.tree = ttk.Treeview(left_frame, columns=columns, show="headings", height=20)

        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=120 if col != "Command" else 300)

        self.tree.pack(fill="both", expand=True)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#111820", foreground="white",
                        rowheight=25, fieldbackground="#111820")
        style.map("Treeview", background=[("selected", "#0080ff")])

        sev_frame = tk.Frame(right_frame, bg="#0a0f14")
        sev_frame.pack(fill="x", pady=10)

        sev_label = tk.Label(sev_frame, text="Severity Level", font=("Segoe UI", 12, "bold"),
                             fg="#ffffff", bg="#0a0f14")
        sev_label.pack(anchor="w")

        self.sev_canvas = tk.Canvas(sev_frame, width=250, height=25, bg="#111820", highlightthickness=0)
        self.sev_canvas.pack(pady=5)
        self.sev_rect = self.sev_canvas.create_rectangle(0, 0, 0, 25, fill="#00ff00")

        timeline_frame = tk.Frame(right_frame, bg="#0a0f14")
        timeline_frame.pack(fill="both", expand=True, pady=10)

        timeline_label = tk.Label(timeline_frame, text="Threat Timeline", font=("Segoe UI", 12, "bold"),
                                  fg="#ffffff", bg="#0a0f14")
        timeline_label.pack(anchor="w")

        self.timeline_list = tk.Listbox(timeline_frame, bg="#111820", fg="#ffffff",
                                        font=("Consolas", 9), height=18)
        self.timeline_list.pack(fill="both", expand=True)

        swarm_frame = tk.Frame(right_frame, bg="#0a0f14")
        swarm_frame.pack(fill="x", pady=10)

        swarm_label = tk.Label(swarm_frame, text="Swarm Sync Nodes", font=("Segoe UI", 12, "bold"),
                               fg="#ffffff", bg="#0a0f14")
        swarm_label.pack(anchor="w")

        self.swarm_status = tk.Label(swarm_frame, text="Status: Local node active, listening for swarm events",
                                     font=("Segoe UI", 9), fg="#00ff80", bg="#0a0f14")
        self.swarm_status.pack(anchor="w")

        self.swarm = SwarmSync(self)

    def add_event(self, pid, parent, score, status, cmd, live, anomaly=False):
        status_display = status
        if anomaly and status != "BLOCKED":
            status_display = "ANOMALY"

        self.tree.insert(
            "",
            "end",
            values=(
                pid,
                parent,
                score,
                status_display,
                round(live["cpu"], 1),
                round(live["mem"], 1),
                live["connections"],
                live["threads"],
                cmd
            )
        )
        self.update_severity_bar(score)
        self.add_timeline_entry_local(pid, parent, score, status_display, live)

        event = {
            "pid": pid,
            "parent": parent,
            "score": score,
            "status": status_display,
            "cmd": cmd,
            "cpu": live["cpu"],
            "mem": live["mem"],
            "connections": live["connections"],
            "threads": live["threads"],
            "timestamp": time.time()
        }
        self.swarm.broadcast_event(event)

        if status_display in ("ALERT", "BLOCKED", "ANOMALY"):
            self.show_toast(f"{status_display}: PID {pid} | Score {score} | Parent {parent}")

    def update_severity_bar(self, score):
        max_width = 250
        width = max(10, min(max_width, int((score / 100) * max_width)))

        if score < 30:
            color = "#00ff00"
        elif score < 60:
            color = "#ffff00"
        else:
            color = "#ff0000"

        self.sev_canvas.coords(self.sev_rect, 0, 0, width, 25)
        self.sev_canvas.itemconfig(self.sev_rect, fill=color)

    def add_timeline_entry_local(self, pid, parent, score, status, live):
        entry = (
            f"{time.strftime('%H:%M:%S')} | LOCAL | {status} | "
            f"PID {pid} | Score {score} | Parent {parent} | "
            f"CPU {round(live['cpu'],1)}% | Mem {round(live['mem'],1)}MB | "
            f"Conns {live['connections']} | Threads {live['threads']}"
        )
        self.timeline_list.insert("end", entry)
        self.timeline_list.yview_moveto(1.0)

    def add_timeline_entry_remote(self, event, ip):
        entry = (
            f"{time.strftime('%H:%M:%S')} | REMOTE {ip} | {event['status']} | "
            f"PID {event['pid']} | Score {event['score']} | Parent {event['parent']} | "
            f"CPU {round(event.get('cpu',0),1)}% | Mem {round(event.get('mem',0),1)}MB | "
            f"Conns {event.get('connections',0)} | Threads {event.get('threads',0)}"
        )
        self.timeline_list.insert("end", entry)
        self.timeline_list.yview_moveto(1.0)

    def show_toast(self, message):
        toast = tk.Toplevel(self.root)
        toast.title("Colonel Hook Alert")
        toast.geometry("350x80+50+50")
        toast.configure(bg="#111820")
        toast.attributes("-topmost", True)

        label = tk.Label(toast, text=message, font=("Segoe UI", 9),
                         fg="#ffffff", bg="#111820", wraplength=330, justify="left")
        label.pack(padx=10, pady=10)

        def auto_close():
            try:
                toast.destroy()
            except:
                pass

        toast.after(4000, auto_close)

    def run(self):
        self.root.mainloop()

gui = ColonelHookGUI()

# ============================================================
# MONITOR LOOP USING TKINTER AFTER()
# ============================================================

def monitor_tick():
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'ppid']):
            try:
                if proc.info['name'] and "powershell" in proc.info['name'].lower():
                    pid = proc.info['pid']
                    cmdline = " ".join(proc.info['cmdline']) if proc.info['cmdline'] else ""
                    parent = psutil.Process(proc.info['ppid']).name()

                    score, live = score_powershell_behavior(cmdline, parent, proc)
                    register_score(score)
                    anomaly = is_anomalous(score)

                    status = "SAFE"
                    if score >= 60 or anomaly:
                        killed = kill_process(pid, proc.info['name'])
                        status = "BLOCKED" if killed else "ALLOWED"
                    elif score >= 30:
                        status = "ALERT"

                    gui.add_event(pid, parent, score, status, cmdline, live, anomaly=anomaly)
                    log(f"[{status}{' ANOMALY' if anomaly else ''}] PowerShell PID {pid} | Score {score} | Parent {parent}")
            except Exception:
                pass
    finally:
        gui.root.after(1000, monitor_tick)

if __name__ == "__main__":
    log("=== Colonel Hook: Live Data + Memory Heuristics + Anomaly + Swarm Sync ===")
    gui.root.after(1000, monitor_tick)
    gui.run()
