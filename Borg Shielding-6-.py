# ============================================================
# Borg Hybrid Brain + Shield (Next Evolution)
# - Auto-elevation (Windows)
# - Forklift-governed LLM node
# - Hybrid network sentinel (GeoIP, baseline, firewall, per-process)
# - DPI stub, anomaly scoring, distributed swarm, kernel hooks stub
# - Autonomous quarantine, visual threat graph, LLM threat reasoning
# - Tabbed GUI with manual threat reclassification + autopilot
# ============================================================

import os
import sys
import platform
import subprocess
import threading
import queue
import time
import json
import ctypes
import socket
import math

import tkinter as tk
from tkinter import ttk
import importlib
import traceback

# =========================
# Optional external libs
# =========================

try:
    from scapy.all import sniff, IP, TCP, UDP, Raw
    SCAPY_AVAILABLE = True
except Exception:
    SCAPY_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except Exception:
    REQUESTS_AVAILABLE = False

try:
    import torch
    import torch.nn as nn
    from transformers import AutoTokenizer, AutoModelForCausalLM
    TORCH_AVAILABLE = True
except Exception:
    TORCH_AVAILABLE = False

# =========================
# AUTO-ELEVATION (Windows)
# =========================

def ensure_admin():
    if platform.system().lower().startswith("windows"):
        try:
            if not ctypes.windll.shell32.IsUserAnAdmin():
                script = os.path.abspath(sys.argv[0])
                params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
                ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "runas",
                    sys.executable,
                    f'"{script}" {params}',
                    None,
                    1
                )
                sys.exit()
        except Exception as e:
            print(f"[Codex Sentinel] Elevation failed: {e}")
            sys.exit()

ensure_admin()

# =========================
# Borg Threat Levels
# =========================

THREAT_NONE = 0
THREAT_MEDIUM = 1
THREAT_HIGH = 2

THREAT_COLORS = {
    THREAT_NONE:  "#00aa00",
    THREAT_MEDIUM:"#ffaa00",
    THREAT_HIGH:  "#ff0000",
}

LEVEL_NAMES = {
    THREAT_NONE: "GREEN",
    THREAT_MEDIUM: "YELLOW",
    THREAT_HIGH: "RED",
}

class ThreatEvent:
    def __init__(self, source, description, level, details=None):
        self.source = source
        self.description = description
        self.level = level
        self.details = details or {}
        self.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    def __str__(self):
        lvl = LEVEL_NAMES[self.level]
        return f"[{self.timestamp}] [{lvl}] {self.source}: {self.description}"

# =========================
# DataVault (encrypt + mirror + chameleon)
# =========================

class DataVault:
    def __init__(self, key=None):
        self.key = key or "dummy-key"

    def encrypt(self, plaintext: str) -> str:
        return "ENC:" + plaintext[::-1]

    def decrypt(self, token: str) -> str:
        if not token.startswith("ENC:"):
            return token
        core = token[4:]
        return core[::-1]

    def mirror_text(self, text: str) -> str:
        return text[::-1]

    def chameleon_text(self, text: str) -> str:
        return text

# =========================
# Autoloader for unnecessary libs
# =========================

class AutoLoader:
    def __init__(self, queen_queue):
        self.queen_queue = queen_queue
        self.unnecessary_libs = [
            "numpy","pandas","matplotlib","scapy","requests","bs4","PIL",
            "wmi","psutil","paramiko","Crypto","OpenSSL",
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
                        details={"library": lib, "error": str(e),
                                 "trace": traceback.format_exc()}
                    )
                )

# =========================
# GeoIP classifier
# =========================

class GeoIPClassifier:
    def __init__(self):
        self.friendly = {"US","CA","GB","FR","DE","JP","AU"}
        self.adversary = {"RU","CN","KP","IR"}

    def lookup_country(self, ip: str) -> str:
        if ip.startswith(("10.","192.168.","172.16.","127.","::1")):
            return "LOCAL"
        if not REQUESTS_AVAILABLE:
            return "??"
        try:
            r = requests.get(f"https://ipapi.co/{ip}/json/", timeout=2)
            if r.status_code == 200:
                data = r.json()
                return data.get("country", "??")
        except Exception:
            return "??"
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
# Baseline Engine (5-day) + anomaly scoring
# =========================

class BaselineEngine:
    def __init__(self, baseline_file="borg_baseline.json", learning_days=5):
        self.baseline_file = baseline_file
        self.learning_days = learning_days
        self.start_time = time.time()
        self.data = {"connections": {}}
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

    def anomaly_score(self, ip_port: str):
        conn_map = self.data.get("connections", {})
        freq = conn_map.get(ip_port, 0)
        if freq == 0:
            return 1.0
        avg = sum(conn_map.values()) / max(len(conn_map), 1)
        return max(0.0, min(1.0, abs(freq - avg) / (avg + 1e-6)))

# =========================
# DPI stub (payload inspection)
# =========================

class DPIEngine:
    def __init__(self):
        self.signatures = [
            b"malware",
            b"exploit",
            b"ransom",
            b"cmd.exe",
            b"/bin/sh",
        ]

    def inspect_payload(self, payload: bytes):
        if not payload:
            return False, None
        for sig in self.signatures:
            if sig in payload:
                return True, sig
        return False, None

# =========================
# Distributed Borg Swarm (UDP broadcast)
# =========================

class BorgSwarm:
    def __init__(self, port=55555):
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def broadcast_event(self, event: ThreatEvent):
        try:
            msg = json.dumps({
                "timestamp": event.timestamp,
                "source": event.source,
                "level": event.level,
                "description": event.description,
                "details": event.details,
            }).encode("utf-8")
            self.sock.sendto(msg, ("255.255.255.255", self.port))
        except Exception:
            pass

# =========================
# OS helpers (netstat/ss + firewall + process mapping + quarantine)
# =========================

def run_command(cmd):
    try:
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
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
        out = run_command("netstat -anp")
        if not out:
            out = run_command("ss -ntp")
        return out

def parse_connections_with_pid(netstat_output):
    system = platform.system().lower()
    results = []

    if "windows" in system:
        for line in netstat_output.splitlines():
            line = line.strip()
            if not line or line.startswith("Proto"):
                continue
            parts = line.split()
            if len(parts) >= 5 and ("TCP" in parts[0] or "UDP" in parts[0]):
                foreign = parts[2]
                pid = parts[-1]
                if ":" in foreign and not foreign.startswith("127.") and not foreign.startswith("::1"):
                    results.append({"ip_port": foreign, "pid": pid})
    else:
        for line in netstat_output.splitlines():
            line = line.strip()
            if not line:
                continue
            if "tcp" in line.lower() or "udp" in line.lower():
                parts = line.split()
                ip_port = None
                pid = None
                for p in parts:
                    if ":" in p and not p.startswith("127.") and not p.startswith("::1"):
                        ip_port = p
                    if "pid=" in p:
                        try:
                            pid = p.split("pid=")[1].split(",")[0]
                        except Exception:
                            pid = None
                if ip_port:
                    results.append({"ip_port": ip_port, "pid": pid})
    return results

def map_pid_to_process_name(pid):
    system = platform.system().lower()
    if not pid:
        return None
    try:
        if "windows" in system:
            out = run_command(f'tasklist /FI "PID eq {pid}"')
            for line in out.splitlines():
                if line.strip().startswith("Image Name"):
                    continue
                if line.strip().startswith("="):
                    continue
                if pid in line:
                    return line.split()[0]
        else:
            out = run_command(f"ps -p {pid} -o comm=")
            name = out.strip()
            return name if name else None
    except Exception:
        return None
    return None

def raise_firewall_shields():
    system = platform.system().lower()
    if "windows" in system:
        run_command('netsh advfirewall set allprofiles state on')
    elif "linux" in system:
        out = run_command("which ufw")
        if out.strip():
            run_command("ufw enable")
        else:
            run_command("iptables -P OUTPUT DROP")
    elif "darwin" in system:
        run_command("pfctl -e")

def lower_firewall_shields():
    system = platform.system().lower()
    if "windows" in system:
        run_command('netsh advfirewall set allprofiles state on')  # adjust as needed
    elif "linux" in system:
        out = run_command("which ufw")
        if out.strip():
            run_command("ufw disable")
        else:
            run_command("iptables -P OUTPUT ACCEPT")
    elif "darwin" in system:
        run_command("pfctl -d")

def quarantine_process(pid):
    system = platform.system().lower()
    if not pid:
        return
    try:
        if "windows" in system:
            run_command(f"taskkill /PID {pid} /F")
        else:
            run_command(f"kill -9 {pid}")
    except Exception:
        pass

def cut_network_interface():
    system = platform.system().lower()
    if "windows" in system:
        run_command('netsh interface set interface name="Ethernet" admin=disabled')
    elif "linux" in system:
        run_command("ip link set down dev eth0")
    elif "darwin" in system:
        run_command("ifconfig en0 down")

# =========================
# Kernel hooks stub (simulated syscall watch)
# =========================

class KernelHookMonitor(threading.Thread):
    def __init__(self, queen_queue, stop_event):
        super().__init__(daemon=True)
        self.queen_queue = queen_queue
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set():
            # Simulated kernel-level watch: check for suspicious processes
            out = run_command("ps aux" if platform.system().lower() != "windows" else "tasklist")
            if "nc.exe" in out or "netcat" in out or "powershell" in out:
                self.queen_queue.put(
                    ThreatEvent(
                        source="KernelHook",
                        description="Suspicious process detected (nc/powershell).",
                        level=THREAT_HIGH,
                        details={"hint": "Possible reverse shell"}
                    )
                )
            time.sleep(10)

# =========================
# Hybrid Worker (netstat + optional pcap + DPI + anomaly)
# =========================

class HybridWorker(threading.Thread):
    def __init__(self, name, queen_queue, stop_event,
                 data_vault: DataVault,
                 autoloader: AutoLoader,
                 geoip: GeoIPClassifier,
                 baseline: BaselineEngine,
                 dpi: DPIEngine):
        super().__init__(daemon=True)
        self.name = name
        self.queen_queue = queen_queue
        self.stop_event = stop_event
        self.data_vault = data_vault
        self.autoloader = autoloader
        self.geoip = geoip
        self.baseline = baseline
        self.dpi = dpi

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
        conns = parse_connections_with_pid(net_out)

        for item in conns:
            ip_port = item["ip_port"]
            pid = item["pid"]
            if ":" in ip_port:
                ip, port = ip_port.rsplit(":", 1)
            else:
                ip, port = ip_port, "?"

            enc_ip = self.data_vault.encrypt(ip)
            role, country = self.geoip.classify(ip)
            proc_name = map_pid_to_process_name(pid)

            self.baseline.record_connection(ip_port)
            is_baseline = self.baseline.is_baseline_connection(ip_port)
            anomaly = self.baseline.anomaly_score(ip_port)

            if self.baseline.is_learning_phase():
                if role in ("LOCAL","FRIENDLY"):
                    level = THREAT_NONE
                    desc = f"[LEARN] {role} {ip_port} ({country}) pid={pid} proc={proc_name}"
                elif role == "ADVERSARY":
                    level = THREAT_HIGH
                    desc = f"[LEARN] Adversary {ip_port} ({country}) pid={pid} proc={proc_name}"
                else:
                    level = THREAT_MEDIUM
                    desc = f"[LEARN] Unknown {ip_port} ({country}) pid={pid} proc={proc_name}"
            else:
                if is_baseline and anomaly < 0.3:
                    if role in ("LOCAL","FRIENDLY"):
                        level = THREAT_NONE
                        desc = f"Baseline {role} {ip_port} ({country}) pid={pid} proc={proc_name}"
                    elif role == "ADVERSARY":
                        level = THREAT_HIGH
                        desc = f"Baseline adversary {ip_port} ({country}) pid={pid} proc={proc_name}"
                    else:
                        level = THREAT_MEDIUM
                        desc = f"Baseline unknown {ip_port} ({country}) pid={pid} proc={proc_name}"
                else:
                    if role == "ADVERSARY":
                        level = THREAT_HIGH
                        desc = f"ANOMALOUS adversary {ip_port} ({country}) pid={pid} proc={proc_name} anomaly={anomaly:.2f}"
                    else:
                        level = THREAT_HIGH
                        desc = f"ANOMALOUS non-baseline {ip_port} ({country}) pid={pid} proc={proc_name} anomaly={anomaly:.2f}"

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
                        "baseline": is_baseline,
                        "pid": pid,
                        "process": proc_name,
                        "anomaly": anomaly
                    }
                )
            )

    def pcap_loop(self):
        def handle_packet(pkt):
            if IP in pkt:
                dst = pkt[IP].dst
                try:
                    if dst.startswith(("127.","10.","192.168.","172.16.")):
                        return
                except Exception:
                    return

                payload = b""
                if Raw in pkt:
                    payload = bytes(pkt[Raw].load)

                suspicious, sig = self.dpi.inspect_payload(payload)

                enc_ip = self.data_vault.encrypt(dst)
                role, country = self.geoip.classify(dst)
                ip_port = f"{dst}:pcap"

                self.baseline.record_connection(ip_port)
                is_baseline = self.baseline.is_baseline_connection(ip_port)
                anomaly = self.baseline.anomaly_score(ip_port)

                if suspicious:
                    level = THREAT_HIGH
                    desc = f"[DPI] Signature {sig} detected to {dst} ({country}) anomaly={anomaly:.2f}"
                else:
                    if self.baseline.is_learning_phase():
                        if role in ("LOCAL","FRIENDLY"):
                            level = THREAT_NONE
                            desc = f"[PCAP LEARN] {role} packet to {dst} ({country})"
                        elif role == "ADVERSARY":
                            level = THREAT_HIGH
                            desc = f"[PCAP LEARN] Adversary packet to {dst} ({country})"
                        else:
                            level = THREAT_MEDIUM
                            desc = f"[PCAP LEARN] Unknown packet to {dst} ({country})"
                    else:
                        if is_baseline and anomaly < 0.3:
                            if role in ("LOCAL","FRIENDLY"):
                                level = THREAT_NONE
                                desc = f"[PCAP] Baseline {role} packet to {dst} ({country})"
                            elif role == "ADVERSARY":
                                level = THREAT_HIGH
                                desc = f"[PCAP] Baseline adversary packet to {dst} ({country})"
                            else:
                                level = THREAT_MEDIUM
                                desc = f"[PCAP] Baseline unknown packet to {dst} ({country})"
                        else:
                            if role == "ADVERSARY":
                                level = THREAT_HIGH
                                desc = f"[PCAP] ANOMALOUS adversary packet to {dst} ({country}) anomaly={anomaly:.2f}"
                            else:
                                level = THREAT_HIGH
                                desc = f"[PCAP] ANOMALOUS non-baseline packet to {dst} ({country}) anomaly={anomaly:.2f}"

                self.queen_queue.put(
                    ThreatEvent(
                        source=self.name + "-PCAP",
                        description=desc,
                        level=level,
                        details={
                            "encrypted_ip": enc_ip,
                            "role": role,
                            "country": country,
                            "baseline": is_baseline,
                            "anomaly": anomaly,
                            "dpi_suspicious": suspicious,
                            "dpi_signature": sig.decode("utf-8") if sig else None
                        }
                    )
                )

        try:
            sniff(prn=handle_packet, store=False)
        except Exception:
            pass

# =========================
# Forklift / LLM Node
# =========================

PRIMARY_MODEL_NAME = "gpt2"  # placeholder
HAS_CUDA = TORCH_AVAILABLE and torch.cuda.is_available()
NUM_GPUS = torch.cuda.device_count() if HAS_CUDA else 0
DEFAULT_DEVICE = "cuda" if HAS_CUDA else "cpu"

class DummyExecutor:
    def __init__(self):
        self._stats = {}

    def reset_stats(self, clear_router_data=False):
        self._stats = {}

    def linear(self, layer_name, weight, bias, x, layer_depth):
        return torch.nn.functional.linear(x, weight, bias)

    def stats(self):
        return {"dummy": True}

EXECUTOR = DummyExecutor()

class TinyFallback(nn.Module):
    def __init__(self, vocab_size=256, hidden=64):
        super().__init__()
        self.emb = nn.Embedding(vocab_size, hidden)
        self.fc = nn.Linear(hidden, vocab_size)

    def forward(self, input_ids):
        x = self.emb(input_ids)
        x = x.mean(dim=1)
        return self.fc(x)

    def generate(self, input_ids, max_new_tokens=32, **kwargs):
        return input_ids

def get_system_telemetry():
    return {"cpu": 0.5, "mem": 0.5}

def train_policy_net_step(sys_tel, latency_ms):
    pass

class ForkliftLinear(nn.Module):
    def __init__(self, base: nn.Linear, name: str, executor: DummyExecutor, depth: int = 0):
        super().__init__()
        self.base = base
        self.name = name
        self.executor = executor
        self.depth = depth

    def forward(self, x):
        return self.executor.linear(
            layer_name=self.name,
            weight=self.base.weight,
            bias=self.base.bias,
            x=x,
            layer_depth=self.depth,
        )

def _patch_module_with_forklift(module: nn.Module, prefix: str = "", depth: int = 0):
    for child_name, child in list(module.named_children()):
        full_name = f"{prefix}{child_name}"
        if isinstance(child, nn.Linear):
            setattr(
                module,
                child_name,
                ForkliftLinear(child, full_name, EXECUTOR, depth),
            )
        else:
            _patch_module_with_forklift(child, full_name + ".", depth + 1)

def patch_model_with_forklift(model: nn.Module):
    _patch_module_with_forklift(model, prefix="", depth=0)

CURRENT_MODEL = None
CURRENT_TOKENIZER = None
CURRENT_MODEL_NAME = None
IS_FALLBACK_MODEL = False

def load_model(model_name: str = PRIMARY_MODEL_NAME):
    global CURRENT_MODEL, CURRENT_TOKENIZER, CURRENT_MODEL_NAME, IS_FALLBACK_MODEL

    if CURRENT_MODEL is not None and CURRENT_TOKENIZER is not None:
        return

    if not TORCH_AVAILABLE:
        mdl = TinyFallback().to(DEFAULT_DEVICE)
        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = None
        CURRENT_MODEL_NAME = "TinyFallback"
        IS_FALLBACK_MODEL = True
        print("[Node] Torch not available, using TinyFallback.")
        return

    print(f"[Node] Loading model: {model_name}")
    try:
        tok = AutoTokenizer.from_pretrained(model_name)
        mdl = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16 if HAS_CUDA else torch.float32,
            device_map="auto" if HAS_CUDA and NUM_GPUS > 1 else None,
        )
        mdl.to(DEFAULT_DEVICE)
        mdl.eval()
        patch_model_with_forklift(mdl)

        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = model_name
        IS_FALLBACK_MODEL = False
        print(f"[Node] Loaded HF model: {model_name}")
    except Exception as e:
        print(f"[Node] Failed to load {model_name}, falling back to TinyFallback: {e}")
        try:
            tok = AutoTokenizer.from_pretrained("gpt2")
        except Exception:
            class DummyTok:
                def __init__(self):
                    self.eos_token_id = 0
                def __call__(self, text, return_tensors=None):
                    ids = [ord(c) % 256 for c in text]
                    t = torch.tensor([ids], dtype=torch.long)
                    return {"input_ids": t}
                def decode(self, ids, skip_special_tokens=True):
                    return "".join(chr(int(i) % 256) for i in ids)
            tok = DummyTok()

        mdl = TinyFallback().to(DEFAULT_DEVICE)
        mdl.eval()

        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = "TinyFallback"
        IS_FALLBACK_MODEL = True
        print("[Node] Using TinyFallback model.")

@torch.inference_mode() if TORCH_AVAILABLE else (lambda f: f)
def generate_text(prompt: str, max_new_tokens: int = 128):
    load_model()

    EXECUTOR.reset_stats(clear_router_data=False)

    tok = CURRENT_TOKENIZER
    mdl = CURRENT_MODEL

    if tok is None:
        input_ids = torch.tensor([[ord(c) % 256 for c in prompt]], dtype=torch.long).to(DEFAULT_DEVICE)
        inputs = {"input_ids": input_ids}
    else:
        inputs = tok(prompt, return_tensors="pt")
        if isinstance(inputs, dict):
            for k in inputs:
                if isinstance(inputs[k], torch.Tensor):
                    inputs[k] = inputs[k].to(DEFAULT_DEVICE)

    t0 = time.time()

    if isinstance(mdl, TinyFallback):
        out_ids = inputs["input_ids"]
    else:
        out_ids = mdl.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=True,
            top_p=0.9,
            temperature=0.8,
            pad_token_id=getattr(tok, "eos_token_id", None),
        )

    latency_ms = (time.time() - t0) * 1000.0

    if tok is None:
        text = prompt
    else:
        text = tok.decode(out_ids[0], skip_special_tokens=True)

    stats = EXECUTOR.stats()
    stats["model_name"] = CURRENT_MODEL_NAME
    stats["is_fallback"] = IS_FALLBACK_MODEL
    stats["latency_ms"] = latency_ms

    try:
        sys_tel = get_system_telemetry()
        train_policy_net_step(sys_tel, latency_ms)
    except Exception:
        pass

    return text, stats

# =========================
# Borg Queen (decision + firewall + LLM reasoning + quarantine + swarm)
# =========================

class BorgQueen(threading.Thread):
    def __init__(self, event_queue, gui_callback, stop_event, swarm: BorgSwarm, autopilot=True):
        super().__init__(daemon=True)
        self.event_queue = event_queue
        self.gui_callback = gui_callback
        self.stop_event = stop_event
        self.shields_up = False
        self.swarm = swarm
        self.autopilot = autopilot

    def run(self):
        while not self.stop_event.is_set():
            try:
                event = self.event_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self.gui_callback("event", event)
            decision = self.evaluate_with_three_queens(event)
            self.gui_callback("decision", decision)

            self.swarm.broadcast_event(event)

            if decision["final_threat_level"] == THREAT_HIGH:
                if self.autopilot:
                    self.handle_high_threat(event, decision)

    def evaluate_with_three_queens(self, event: ThreatEvent):
        levels = []
        for i in range(3):
            levels.append(self.single_queen_vote(i, event))
        final_level = max(set(levels), key=levels.count)
        return {"event": event, "votes": levels,
                "final_threat_level": final_level}

    def single_queen_vote(self, idx, event: ThreatEvent):
        return event.level

    def handle_high_threat(self, event: ThreatEvent, decision):
        if not self.shields_up:
            self.shields_up = True
            raise_firewall_shields()
            self.gui_callback("shield", {"state": "UP", "reason": event})

        pid = event.details.get("pid")
        if pid:
            quarantine_process(pid)

        try:
            prompt = (
                "You are a Borg security AI. Analyze this threat event and respond with:\n"
                "1) Short explanation\n"
                "2) Likely escalation risk\n"
                "3) Recommended action\n"
                f"Event: {event.description}\nDetails: {event.details}\n"
            )
            text, stats = generate_text(prompt, max_new_tokens=128)
            self.gui_callback("llm", {"text": text, "stats": stats})
        except Exception:
            pass

# =========================
# GUI (Tabbed Threat Matrix + Visual Graph)
# =========================

class BorgShieldGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Borg Hybrid Brain + Shield (Next Evolution)")

        self.stop_event = threading.Event()
        self.event_queue = queue.Queue()
        self.data_vault = DataVault()
        self.autoloader = AutoLoader(self.event_queue)
        self.geoip = GeoIPClassifier()
        self.baseline = BaselineEngine()
        self.dpi = DPIEngine()
        self.swarm = BorgSwarm()

        self.queen = BorgQueen(self.event_queue, self.gui_callback, self.stop_event, self.swarm, autopilot=True)

        self.workers = [
            HybridWorker("HybridWorker-Net", self.event_queue, self.stop_event,
                         self.data_vault, self.autoloader, self.geoip, self.baseline, self.dpi),
        ]

        self.kernel_monitor = KernelHookMonitor(self.event_queue, self.stop_event)

        self.events_all = []
        self.events_green = []
        self.events_yellow = []
        self.events_red = []

        self.selected_event = None

        self.graph_nodes = {}   # process -> (x,y)
        self.graph_edges = []   # (process, ip_port, level)

        self.build_layout()
        self.start_borg()

    def build_layout(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        shield_frame = ttk.LabelFrame(main, text="Shield Status")
        shield_frame.pack(fill=tk.X, pady=5)

        self.shield_label = ttk.Label(shield_frame, text="Shields: DOWN",
                                      font=("Segoe UI", 14, "bold"))
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

        notebook_frame = ttk.LabelFrame(main, text="Threat Matrix")
        notebook_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.notebook = ttk.Notebook(notebook_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_all = ttk.Frame(self.notebook)
        self.tab_green = ttk.Frame(self.notebook)
        self.tab_yellow = ttk.Frame(self.notebook)
        self.tab_red = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_all, text="All")
        self.notebook.add(self.tab_green, text="Green")
        self.notebook.add(self.tab_yellow, text="Yellow")
        self.notebook.add(self.tab_red, text="Red")

        self.list_all = tk.Listbox(self.tab_all)
        self.list_all.pack(fill=tk.BOTH, expand=True)
        self.list_all.bind("<<ListboxSelect>>", lambda e: self.on_select_event(self.list_all, "all"))

        self.list_green = tk.Listbox(self.tab_green)
        self.list_green.pack(fill=tk.BOTH, expand=True)
        self.list_green.bind("<<ListboxSelect>>", lambda e: self.on_select_event(self.list_green, "green"))

        self.list_yellow = tk.Listbox(self.tab_yellow)
        self.list_yellow.pack(fill=tk.BOTH, expand=True)
        self.list_yellow.bind("<<ListboxSelect>>", lambda e: self.on_select_event(self.list_yellow, "yellow"))

        self.list_red = tk.Listbox(self.tab_red)
        self.list_red.pack(fill=tk.BOTH, expand=True)
        self.list_red.bind("<<ListboxSelect>>", lambda e: self.on_select_event(self.list_red, "red"))

        detail_frame = ttk.LabelFrame(main, text="Selected Threat Detail + LLM Reasoning")
        detail_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.detail_text = tk.Text(detail_frame, height=10, wrap="word")
        self.detail_text.pack(fill=tk.BOTH, expand=True)

        obf_frame = ttk.LabelFrame(main, text="Sensitive Data Matrix (Chameleon + Mirror)")
        obf_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.obf_text = tk.Text(obf_frame, height=4, wrap="none")
        self.obf_text.pack(fill=tk.BOTH, expand=True)
        bg = self.obf_text.cget("background")
        self.obf_text.config(foreground=bg)

        graph_frame = ttk.LabelFrame(main, text="Visual Threat Graph (Processes ↔ Connections)")
        graph_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.graph_canvas = tk.Canvas(graph_frame, height=250, bg="#111111")
        self.graph_canvas.pack(fill=tk.BOTH, expand=True)

        ctrl_frame = ttk.Frame(main)
        ctrl_frame.pack(fill=tk.X, pady=5)

        self.btn_mark_green = ttk.Button(ctrl_frame, text="Mark Selected as GREEN",
                                         command=lambda: self.reclassify_selected(THREAT_NONE))
        self.btn_mark_green.pack(side=tk.LEFT, padx=5)

        self.btn_mark_yellow = ttk.Button(ctrl_frame, text="Mark Selected as YELLOW",
                                          command=lambda: self.reclassify_selected(THREAT_MEDIUM))
        self.btn_mark_yellow.pack(side=tk.LEFT, padx=5)

        self.btn_mark_red = ttk.Button(ctrl_frame, text="Mark Selected as RED",
                                       command=lambda: self.reclassify_selected(THREAT_HIGH))
        self.btn_mark_red.pack(side=tk.LEFT, padx=5)

        self.btn_lower_shields = ttk.Button(ctrl_frame, text="Admin: Lower Shields",
                                            command=self.lower_shields)
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
        self.kernel_monitor.start()

    def gui_callback(self, kind, payload):
        if kind == "event":
            self.root.after(0, self.handle_event, payload)
        elif kind == "decision":
            self.root.after(0, self.handle_decision, payload)
        elif kind == "shield":
            self.root.after(0, self.handle_shield_change, payload)
        elif kind == "llm":
            self.root.after(0, self.handle_llm_output, payload)

    def handle_event(self, event: ThreatEvent):
        self.events_all.append(event)
        idx_all = len(self.events_all) - 1
        self.list_all.insert(tk.END, f"{idx_all}: {str(event)}")

        if event.level == THREAT_NONE:
            self.events_green.append(event)
            self.list_green.insert(tk.END, f"{len(self.events_green)-1}: {str(event)}")
            self.count_green += 1
        elif event.level == THREAT_MEDIUM:
            self.events_yellow.append(event)
            self.list_yellow.insert(tk.END, f"{len(self.events_yellow)-1}: {str(event)}")
            self.count_yellow += 1
        elif event.level == THREAT_HIGH:
            self.events_red.append(event)
            self.list_red.insert(tk.END, f"{len(self.events_red)-1}: {str(event)}")
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

        self.update_graph(event)

    def handle_decision(self, decision):
        event = decision["event"]
        votes = decision["votes"]
        final_level = decision["final_threat_level"]
        lvl_str = LEVEL_NAMES[final_level]
        msg = f"Decision: {lvl_str} (votes={votes}) for event from {event.source}"
        self.detail_text.insert(tk.END, msg + "\n")
        self.detail_text.see(tk.END)

    def handle_shield_change(self, info):
        state = info["state"]
        if state == "UP":
            self.shield_label.config(text="Shields: UP", foreground="#ff0000")
            reason = info["reason"]
            self.shield_reason.config(text=f"Reason: {reason.description}")
        else:
            self.shield_label.config(text="Shields: DOWN", foreground="#00aa00")
            self.shield_reason.config(text="")

    def handle_llm_output(self, payload):
        text = payload["text"]
        stats = payload["stats"]
        self.detail_text.insert(tk.END, "\n[LLM Threat Reasoning]\n")
        self.detail_text.insert(tk.END, text + "\n")
        self.detail_text.insert(tk.END, f"[Model: {stats.get('model_name')} | latency={stats.get('latency_ms'):.1f} ms]\n")
        self.detail_text.see(tk.END)

    def on_select_event(self, listbox, tab):
        try:
            sel = listbox.curselection()
            if not sel:
                return
            idx = int(listbox.get(sel[0]).split(":")[0])
        except Exception:
            return

        if tab == "all":
            event = self.events_all[idx]
        elif tab == "green":
            event = self.events_green[idx]
        elif tab == "yellow":
            event = self.events_yellow[idx]
        elif tab == "red":
            event = self.events_red[idx]
        else:
            return

        self.selected_event = event
        self.show_event_detail(event)

    def show_event_detail(self, event: ThreatEvent):
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, f"Timestamp: {event.timestamp}\n")
        self.detail_text.insert(tk.END, f"Source: {event.source}\n")
        self.detail_text.insert(tk.END, f"Level: {LEVEL_NAMES[event.level]}\n")
        self.detail_text.insert(tk.END, f"Description: {event.description}\n")
        self.detail_text.insert(tk.END, "Details:\n")
        for k, v in event.details.items():
            self.detail_text.insert(tk.END, f"  {k}: {v}\n")
        self.detail_text.see(tk.END)

    def reclassify_selected(self, new_level):
        if not self.selected_event:
            return
        old_level = self.selected_event.level
        self.selected_event.level = new_level

        if old_level == THREAT_NONE:
            self.count_green -= 1
        elif old_level == THREAT_MEDIUM:
            self.count_yellow -= 1
        elif old_level == THREAT_HIGH:
            self.count_red -= 1

        if new_level == THREAT_NONE:
            self.count_green += 1
        elif new_level == THREAT_MEDIUM:
            self.count_yellow += 1
        elif new_level == THREAT_HIGH:
            self.count_red += 1

        self.var_green.set(str(self.count_green))
        self.var_yellow.set(str(self.count_yellow))
        self.var_red.set(str(self.count_red))

        self.list_all.delete(0, tk.END)
        self.list_green.delete(0, tk.END)
        self.list_yellow.delete(0, tk.END)
        self.list_red.delete(0, tk.END)

        self.events_green = []
        self.events_yellow = []
        self.events_red = []

        for i, ev in enumerate(self.events_all):
            self.list_all.insert(tk.END, f"{i}: {str(ev)}")
            if ev.level == THREAT_NONE:
                self.events_green.append(ev)
                self.list_green.insert(tk.END, f"{len(self.events_green)-1}: {str(ev)}")
            elif ev.level == THREAT_MEDIUM:
                self.events_yellow.append(ev)
                self.list_yellow.insert(tk.END, f"{len(self.events_yellow)-1}: {str(ev)}")
            elif ev.level == THREAT_HIGH:
                self.events_red.append(ev)
                self.list_red.insert(tk.END, f"{len(self.events_red)-1}: {str(ev)}")

        self.detail_text.insert(tk.END, f"\n[ADMIN] Reclassified event to {LEVEL_NAMES[new_level]}.\n")
        self.detail_text.see(tk.END)

    def lower_shields(self):
        lower_firewall_shields()
        self.shield_label.config(text="Shields: DOWN", foreground="#00aa00")
        self.shield_reason.config(text="")
        self.detail_text.insert(tk.END, "[ADMIN] Shields manually lowered.\n")
        self.detail_text.see(tk.END)

    def quit_app(self):
        self.stop_event.set()
        self.root.destroy()

    def update_graph(self, event: ThreatEvent):
        proc = event.details.get("process") or "unknown"
        ip_port = event.details.get("port") and event.details.get("encrypted_ip")
        level = event.level

        if proc not in self.graph_nodes:
            angle = len(self.graph_nodes) * (2 * math.pi / 12.0)
            cx, cy, r = 200, 125, 80
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            self.graph_nodes[proc] = (x, y)

        self.graph_edges.append((proc, ip_port, level))
        self.redraw_graph()

    def redraw_graph(self):
        self.graph_canvas.delete("all")
        for proc, (x, y) in self.graph_nodes.items():
            self.graph_canvas.create_oval(x-10, y-10, x+10, y+10,
                                          fill="#333333", outline="#ffffff")
            self.graph_canvas.create_text(x, y-18, text=proc, fill="#ffffff", font=("Segoe UI", 8))
        for proc, ip_port, level in self.graph_edges[-50:]:
            if ip_port is None:
                continue
            x, y = self.graph_nodes.get(proc, (200, 125))
            color = THREAT_COLORS.get(level, "#ffffff")
            self.graph_canvas.create_line(x, y, x+40, y, fill=color)
            self.graph_canvas.create_text(x+50, y, text=str(ip_port), fill=color, font=("Segoe UI", 7))

# =========================
# Main
# =========================

def main():
    root = tk.Tk()
    app = BorgShieldGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
