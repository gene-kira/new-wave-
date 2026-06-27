import os
import platform
import subprocess
import threading
import queue
import time
import json
import tkinter as tk
from tkinter import ttk
import importlib
import traceback

# Optional external libs (if installed) for pcap-like capture
try:
    from scapy.all import sniff, IP
    SCAPY_AVAILABLE = True
except Exception:
    SCAPY_AVAILABLE = False

# =========================
# Threat levels and events
# =========================

THREAT_NONE = 0   # Green
THREAT_MEDIUM = 1 # Yellow
THREAT_HIGH = 2   # Red

THREAT_COLORS = {
    THREAT_NONE:  "#00aa00",  # green
    THREAT_MEDIUM:"#ffaa00",  # yellow
    THREAT_HIGH:  "#ff0000",  # red
}

class ThreatEvent:
    def __init__(self, source, description, level, details=None):
        self.source = source
        self.description = description
        self.level = level
        self.details = details or {}
        self.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    def __str__(self):
        lvl = {THREAT_NONE:"GREEN", THREAT_MEDIUM:"YELLOW", THREAT_HIGH:"RED"}[self.level]
        return f"[{self.timestamp}] [{lvl}] {self.source}: {self.description}"


# =========================
# Data vault (encryption + obfuscation)
# =========================

class DataVault:
    """
    Handles encryption of sensitive data and provides
    mirror + chameleon views for GUI.
    """

    def __init__(self, key=None):
        self.key = key or "dummy-key"

    def encrypt(self, plaintext: str) -> str:
        # Simple reversible placeholder; swap with real crypto if desired.
        return "ENC:" + plaintext[::-1]

    def decrypt(self, token: str) -> str:
        if not token.startswith("ENC:"):
            return token
        core = token[4:]
        return core[::-1]

    def mirror_text(self, text: str) -> str:
        # MAN -> NAM
        return text[::-1]

    def chameleon_text(self, text: str) -> str:
        # GUI hides it by making foreground == background
        return text


# =========================
# Autoloader for unnecessary libraries
# =========================

class AutoLoader:
    """
    Attempts to load unnecessary or optional libraries.
    Reports results to the Borg Queen for evaluation.
    """

    def __init__(self, queen_queue):
        self.queen_queue = queen_queue
        self.unnecessary_libs = [
            "numpy",
            "pandas",
            "matplotlib",
            "scapy",
            "requests",
            "bs4",
            "PIL",
            "wmi",
            "psutil",
            "paramiko",
            "Crypto",
            "OpenSSL",
        ]

    def scan_and_load(self):
        for lib in self.unnecessary_libs:
            try:
                importlib.import_module(lib)
                self.queen_queue.put(
                    ThreatEvent(
                        source="AutoLoader",
                        description=f"Unnecessary library '{lib}' is installed.",
                        level=THREAT_MEDIUM,
                        details={"library": lib}
                    )
                )
            except ImportError:
                self.queen_queue.put(
                    ThreatEvent(
                        source="AutoLoader",
                        description=f"Library '{lib}' not found.",
                        level=THREAT_NONE,
                        details={"library": lib}
                    )
                )
            except Exception as e:
                self.queen_queue.put(
                    ThreatEvent(
                        source="AutoLoader",
                        description=f"Library '{lib}' caused abnormal exception.",
                        level=THREAT_HIGH,
                        details={
                            "library": lib,
                            "error": str(e),
                            "trace": traceback.format_exc()
                        }
                    )
                )


# =========================
# GeoIP classifier (friendly vs adversary)
# =========================

class GeoIPClassifier:
    """
    Stubbed GeoIP classifier with adversary/friendly logic.
    Replace lookup_country() with real GeoIP if desired.
    """

    def __init__(self):
        self.friendly = {"US", "CA", "GB", "FR", "DE", "JP", "AU"}
        self.adversary = {"RU", "CN", "KP", "IR"}

    def lookup_country(self, ip: str) -> str:
        # Placeholder: classify private/local vs unknown.
        if ip.startswith(("10.", "192.168.", "172.16.", "127.", "::1")):
            return "LOCAL"
        # You can plug real GeoIP here.
        return "??"

    def classify(self, ip: str):
        country = self.lookup_country(ip)
        if country in self.friendly:
            return "FRIENDLY", country
        if country in self.adversary:
            return "ADVERSARY", country
        if country == "LOCAL":
            return "LOCAL", country
        return "UNKNOWN", country


# =========================
# Baseline engine (5-day learning)
# =========================

class BaselineEngine:
    """
    Learns normal patterns over N days:
    - IP:port combinations
    Tightens shield by marking deviations as higher threat.
    """

    def __init__(self, baseline_file="borg_baseline.json", learning_days=5):
        self.baseline_file = baseline_file
        self.learning_days = learning_days
        self.start_time = time.time()
        self.data = {
            "connections": {},  # key: ip:port, value: count
        }
        self.load()

    def load(self):
        try:
            if os.path.exists(self.baseline_file):
                with open(self.baseline_file, "r") as f:
                    self.data = json.load(f)
        except Exception:
            self.data = {"connections": {}}

    def save(self):
        try:
            with open(self.baseline_file, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def is_learning_phase(self):
        days = (time.time() - self.start_time) / 86400.0
        return days < self.learning_days

    def record_connection(self, ip_port: str):
        conn_map = self.data.setdefault("connections", {})
        conn_map[ip_port] = conn_map.get(ip_port, 0) + 1
        if self.is_learning_phase():
            self.save()

    def is_baseline_connection(self, ip_port: str):
        conn_map = self.data.get("connections", {})
        return ip_port in conn_map


# =========================
# OS helpers (hybrid capture)
# =========================

def run_command(cmd):
    try:
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            return result.stdout
        else:
            return ""
    except Exception:
        return ""

def get_netstat_output():
    system = platform.system().lower()
    if "windows" in system:
        return run_command("netstat -ano")
    else:
        out = run_command("netstat -an")
        if not out:
            out = run_command("ss -nt")
        return out

def parse_connections(netstat_output):
    connections = []
    for line in netstat_output.splitlines():
        line = line.strip()
        if not line:
            continue
        if "tcp" in line.lower() or "udp" in line.lower():
            parts = line.split()
            for p in parts:
                if ":" in p and not p.startswith("127.") and not p.startswith("::1"):
                    connections.append(p)
    return connections


# =========================
# Borg Workers (hybrid: netstat + optional pcap)
# =========================

class HybridWorker(threading.Thread):
    """
    Cross-platform worker using hybrid capture:
    - OS-native commands (netstat/ss)
    - Optional pcap via scapy if available
    """

    def __init__(self, name, queen_queue, stop_event,
                 data_vault: DataVault,
                 autoloader: AutoLoader,
                 geoip: GeoIPClassifier,
                 baseline: BaselineEngine):
        super().__init__(daemon=True)
        self.name = name
        self.queen_queue = queen_queue
        self.stop_event = stop_event
        self.data_vault = data_vault
        self.autoloader = autoloader
        self.geoip = geoip
        self.baseline = baseline

    def run(self):
        if SCAPY_AVAILABLE:
            t = threading.Thread(target=self.pcap_loop, daemon=True)
            t.start()

        while not self.stop_event.is_set():
            self.autoloader.scan_and_load()
            self.collect_connections()
            time.sleep(5)

    def collect_connections(self):
        net_out = get_netstat_output()
        conns = parse_connections(net_out)

        for conn in conns:
            ip_port = conn
            if ":" in ip_port:
                ip, port = ip_port.rsplit(":", 1)
            else:
                ip, port = ip_port, "?"

            enc_ip = self.data_vault.encrypt(ip)
            role, country = self.geoip.classify(ip)

            self.baseline.record_connection(ip_port)
            is_baseline = self.baseline.is_baseline_connection(ip_port)

            if self.baseline.is_learning_phase():
                if role in ("LOCAL", "FRIENDLY"):
                    level = THREAT_NONE
                    desc = f"[LEARN] {role} connection {ip_port}"
                elif role == "ADVERSARY":
                    level = THREAT_HIGH
                    desc = f"[LEARN] Adversary connection {ip_port}"
                else:
                    level = THREAT_MEDIUM
                    desc = f"[LEARN] Unknown external connection {ip_port}"
            else:
                if is_baseline:
                    if role in ("LOCAL", "FRIENDLY"):
                        level = THREAT_NONE
                        desc = f"Baseline {role} connection {ip_port}"
                    elif role == "ADVERSARY":
                        level = THREAT_HIGH
                        desc = f"Baseline adversary connection {ip_port}"
                    else:
                        level = THREAT_MEDIUM
                        desc = f"Baseline unknown connection {ip_port}"
                else:
                    if role == "ADVERSARY":
                        level = THREAT_HIGH
                        desc = f"NEW adversary connection {ip_port}"
                    else:
                        level = THREAT_HIGH
                        desc = f"NEW non-baseline connection {ip_port}"

            self.queen_queue.put(
                ThreatEvent(
                    source=self.name,
                    description=desc,
                    level=level,
                    details={
                        "encrypted_ip": enc_ip,
                        "port": port,
                        "role": role,
                        "country": country,
                        "baseline": is_baseline
                    }
                )
            )

    def pcap_loop(self):
        def handle_packet(pkt):
            if IP in pkt:
                dst = pkt[IP].dst
                try:
                    if dst.startswith(("127.", "10.", "192.168.", "172.16.")):
                        return
                except Exception:
                    return

                enc_ip = self.data_vault.encrypt(dst)
                role, country = self.geoip.classify(dst)
                ip_port = f"{dst}:pcap"

                self.baseline.record_connection(ip_port)
                is_baseline = self.baseline.is_baseline_connection(ip_port)

                if self.baseline.is_learning_phase():
                    if role in ("LOCAL", "FRIENDLY"):
                        level = THREAT_NONE
                        desc = f"[PCAP LEARN] {role} packet to {dst}"
                    elif role == "ADVERSARY":
                        level = THREAT_HIGH
                        desc = f"[PCAP LEARN] Adversary packet to {dst}"
                    else:
                        level = THREAT_MEDIUM
                        desc = f"[PCAP LEARN] Unknown external packet to {dst}"
                else:
                    if is_baseline:
                        if role in ("LOCAL", "FRIENDLY"):
                            level = THREAT_NONE
                            desc = f"[PCAP] Baseline {role} packet to {dst}"
                        elif role == "ADVERSARY":
                            level = THREAT_HIGH
                            desc = f"[PCAP] Baseline adversary packet to {dst}"
                        else:
                            level = THREAT_MEDIUM
                            desc = f"[PCAP] Baseline unknown packet to {dst}"
                    else:
                        if role == "ADVERSARY":
                            level = THREAT_HIGH
                            desc = f"[PCAP] NEW adversary packet to {dst}"
                        else:
                            level = THREAT_HIGH
                            desc = f"[PCAP] NEW non-baseline packet to {dst}"

                self.queen_queue.put(
                    ThreatEvent(
                        source=self.name + "-PCAP",
                        description=desc,
                        level=level,
                        details={
                            "encrypted_ip": enc_ip,
                            "role": role,
                            "country": country,
                            "baseline": is_baseline
                        }
                    )
                )

        try:
            sniff(prn=handle_packet, store=False)
        except Exception:
            pass


# =========================
# Borg Queen (decision core)
# =========================

class BorgQueen(threading.Thread):
    def __init__(self, event_queue, gui_callback, stop_event):
        super().__init__(daemon=True)
        self.event_queue = event_queue
        self.gui_callback = gui_callback
        self.stop_event = stop_event
        self.shields_up = False

    def run(self):
        while not self.stop_event.is_set():
            try:
                event = self.event_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self.gui_callback("event", event)
            decision = self.evaluate_with_three_queens(event)
            self.gui_callback("decision", decision)

            if decision["final_threat_level"] == THREAT_HIGH:
                if not self.shields_up:
                    self.shields_up = True
                    self.gui_callback("shield", {"state": "UP", "reason": event})

    def evaluate_with_three_queens(self, event: ThreatEvent):
        levels = []
        for i in range(3):
            levels.append(self.single_queen_vote(i, event))
        final_level = max(set(levels), key=levels.count)
        return {
            "event": event,
            "votes": levels,
            "final_threat_level": final_level
        }

    def single_queen_vote(self, idx, event: ThreatEvent):
        return event.level


# =========================
# GUI (Tkinter dashboard)
# =========================

class BorgShieldGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Borg Shield Command (Hybrid, GeoIP, Baseline, Autonomous)")

        self.stop_event = threading.Event()
        self.event_queue = queue.Queue()
        self.data_vault = DataVault()
        self.autoloader = AutoLoader(self.event_queue)
        self.geoip = GeoIPClassifier()
        self.baseline = BaselineEngine()

        self.queen = BorgQueen(self.event_queue, self.gui_callback, self.stop_event)

        self.workers = [
            HybridWorker("HybridWorker-Net", self.event_queue, self.stop_event,
                         self.data_vault, self.autoloader, self.geoip, self.baseline),
        ]

        self.build_layout()
        self.start_borg()

    def build_layout(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        shield_frame = ttk.LabelFrame(main, text="Shield Status")
        shield_frame.pack(fill=tk.X, pady=5)

        self.shield_label = ttk.Label(shield_frame, text="Shields: DOWN", font=("Segoe UI", 14, "bold"))
        self.shield_label.pack(side=tk.LEFT, padx=5)

        self.shield_reason = ttk.Label(shield_frame, text="", foreground="#ff0000")
        self.shield_reason.pack(side=tk.LEFT, padx=10)

        summary_frame = ttk.LabelFrame(main, text="Threat Summary")
        summary_frame.pack(fill=tk.X, pady=5)

        self.var_green = tk.StringVar(value="0")
        self.var_yellow = tk.StringVar(value="0")
        self.var_red = tk.StringVar(value="0")

        ttk.Label(summary_frame, text="Green (No Threat):").grid(row=0, column=0, sticky="w")
        ttk.Label(summary_frame, textvariable=self.var_green,
                  foreground=THREAT_COLORS[THREAT_NONE]).grid(row=0, column=1, sticky="w")

        ttk.Label(summary_frame, text="Yellow (Medium):").grid(row=1, column=0, sticky="w")
        ttk.Label(summary_frame, textvariable=self.var_yellow,
                  foreground=THREAT_COLORS[THREAT_MEDIUM]).grid(row=1, column=1, sticky="w")

        ttk.Label(summary_frame, text="Red (High):").grid(row=2, column=0, sticky="w")
        ttk.Label(summary_frame, textvariable=self.var_red,
                  foreground=THREAT_COLORS[THREAT_HIGH]).grid(row=2, column=1, sticky="w")

        log_frame = ttk.LabelFrame(main, text="Event Log (Normal view)")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = tk.Text(log_frame, height=12, wrap="none")
        self.log_text.pack(fill=tk.BOTH, expand=True)

        obf_frame = ttk.LabelFrame(main, text="Sensitive Data Matrix (Chameleon + Mirror, Autonomous)")
        obf_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.obf_text = tk.Text(obf_frame, height=6, wrap="none")
        self.obf_text.pack(fill=tk.BOTH, expand=True)

        bg = self.obf_text.cget("background")
        self.obf_text.config(foreground=bg)

        ctrl_frame = ttk.Frame(main)
        ctrl_frame.pack(fill=tk.X, pady=5)

        self.btn_lower_shields = ttk.Button(ctrl_frame, text="Admin: Lower Shields", command=self.lower_shields)
        self.btn_lower_shields.pack(side=tk.LEFT, padx=5)

        self.btn_quit = ttk.Button(ctrl_frame, text="Quit", command=self.quit_app)
        self.btn_quit.pack(side=tk.RIGHT, padx=5)

        self.count_green = 0
        self.count_yellow = 0
        self.count_red = 0

    def start_borg(self):
        self.queen.start()
        for w in self.workers:
            w.start()

    def gui_callback(self, kind, payload):
        if kind == "event":
            self.root.after(0, self.handle_event, payload)
        elif kind == "decision":
            self.root.after(0, self.handle_decision, payload)
        elif kind == "shield":
            self.root.after(0, self.handle_shield_change, payload)

    def handle_event(self, event: ThreatEvent):
        self.log_text.insert(tk.END, str(event) + "\n")
        self.log_text.see(tk.END)

        if event.level == THREAT_NONE:
            self.count_green += 1
        elif event.level == THREAT_MEDIUM:
            self.count_yellow += 1
        elif event.level == THREAT_HIGH:
            self.count_red += 1

        self.var_green.set(str(self.count_green))
        self.var_yellow.set(str(self.count_yellow))
        self.var_red.set(str(self.count_red))

        if "encrypted_ip" in event.details:
            token = event.details["encrypted_ip"]
            mirrored = self.data_vault.mirror_text(token)
            chameleon = self.data_vault.chameleon_text(mirrored)
            self.obf_text.insert(tk.END, chameleon + "\n")
            self.obf_text.see(tk.END)

    def handle_decision(self, decision):
        event = decision["event"]
        votes = decision["votes"]
        final_level = decision["final_threat_level"]
        lvl_str = {THREAT_NONE:"GREEN", THREAT_MEDIUM:"YELLOW", THREAT_HIGH:"RED"}[final_level]
        msg = f"Decision: {lvl_str} (votes={votes}) for event from {event.source}"
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def handle_shield_change(self, info):
        state = info["state"]
        if state == "UP":
            self.shield_label.config(text="Shields: UP", foreground="#ff0000")
            reason = info["reason"]
            self.shield_reason.config(text=f"Reason: {reason.description}")
        else:
            self.shield_label.config(text="Shields: DOWN", foreground="#00aa00")
            self.shield_reason.config(text="")

    def lower_shields(self):
        self.shield_label.config(text="Shields: DOWN", foreground="#00aa00")
        self.shield_reason.config(text="")
        self.log_text.insert(tk.END, "[ADMIN] Shields manually lowered.\n")
        self.log_text.see(tk.END)

    def quit_app(self):
        self.stop_event.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = BorgShieldGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
