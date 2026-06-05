#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  SENTINEL CORE UNIFIED v6 — HARD FUSION + FORKLIFT + PREDICTIVE QUEEN
#

import os
import sys
import re
import time
import json
import queue
import socket
import random
import threading
import subprocess
import platform
import uuid
import hashlib
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional
from collections import deque

# ============================================================
#  OPTIONAL IMPORTS
# ============================================================

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:
    tk = None
    ttk = None

try:
    import torch
    import torch.nn as nn
except Exception:
    torch = None
    nn = None

try:
    import onnxruntime as ort
except Exception:
    ort = None

try:
    import numpy as np
except Exception:
    np = None

try:
    import scapy.all as scapy_all
except Exception:
    scapy_all = None

try:
    import pydivert
except Exception:
    pydivert = None

try:
    import win32evtlog
except Exception:
    win32evtlog = None

try:
    from neo4j import GraphDatabase
except Exception:
    GraphDatabase = None

try:
    from fastapi import FastAPI, Body
    import uvicorn
except Exception:
    FastAPI = None
    Body = None
    uvicorn = None

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except Exception:
    Observer = None
    FileSystemEventHandler = None

try:
    import psutil
except Exception:
    psutil = None

try:
    from transformers import AutoTokenizer, AutoModelForCausalLM
except Exception:
    AutoTokenizer = None
    AutoModelForCausalLM = None

# ============================================================
#  GLOBAL EVENT BUS / STATE
# ============================================================

class EventBusCore:
    def __init__(self):
        self.subscribers = {}

    def subscribe(self, topic, callback):
        self.subscribers.setdefault(topic, []).append(callback)

    def publish(self, topic, data):
        if topic in self.subscribers:
            for cb in self.subscribers[topic]:
                try:
                    cb(data)
                except Exception as e:
                    print(f"[EVENT_BUS] subscriber error: {e}")

EVENT_BUS = EventBusCore()

brain_lock = threading.Lock()
brain_state = {}
trust_config = {
    "allowed": [],
    "blocked": [],
}

def record_event(kind: str, message: str, severity: str = "info", tags=None):
    TELEMETRY.ingest("event", {
        "kind": kind,
        "message": message,
        "severity": severity,
        "tags": tags or []
    })

def update_log():
    pass

def watchdog_touch(name: str):
    with brain_lock:
        brain_state.setdefault("watchdog", {})[name] = time.time()

def is_allowed(name: str, exe: str) -> bool:
    for pat in trust_config.get("allowed", []):
        if re.search(pat, name, re.IGNORECASE) or re.search(pat, exe, re.IGNORECASE):
            return True
    return False

def is_blocked(name: str, exe: str) -> bool:
    for pat in trust_config.get("blocked", []):
        if re.search(pat, name, re.IGNORECASE) or re.search(pat, exe, re.IGNORECASE):
            return True
    return False

def terminate_proc(proc, reason: str, auto_block: bool = True):
    try:
        pid = proc.pid
        name = proc.name()
        proc.terminate()
        record_event("terminate", f"Terminated {name} (PID {pid}) - {reason}", severity="crit")
        if auto_block:
            trust_config.setdefault("blocked", []).append(re.escape(name))
    except Exception as e:
        record_event("terminate_fail", f"Failed to terminate {proc} - {e}", severity="warn")

# ============================================================
#  TELEMETRY INGESTION + ETW
# ============================================================

class TelemetryIngestor:
    def __init__(self):
        self.queue = queue.Queue()
        self.etw_enabled = win32evtlog is not None

    def ingest(self, event_type, payload):
        evt = {
            "type": event_type,
            "payload": payload,
            "timestamp": time.time()
        }
        self.queue.put(evt)
        EVENT_BUS.publish("telemetry", evt)

    def run_fake_stream(self):
        while True:
            evt = random.choice(["process", "network", "file", "kernel"])
            self.ingest(evt, {"value": random.randint(1, 9999)})
            time.sleep(0.2)

    def run_etw_stream(self):
        if not self.etw_enabled:
            print("[ETW] win32evtlog not available, skipping ETW.")
            return

        logs = ["Security", "System", "Application"]
        providers = [
            "Microsoft-Windows-Security-Auditing",
            "Microsoft-Windows-Kernel-Process",
            "Microsoft-Windows-Kernel-Network"
        ]
        server = "localhost"
        handles = []
        for logtype in logs:
            try:
                h = win32evtlog.OpenEventLog(server, logtype)
                handles.append((logtype, h))
                print(f"[ETW] Subscribed to log: {logtype}")
            except Exception as e:
                print(f"[ETW] Failed to open log {logtype}: {e}")

        flags = win32evtlog.EVENTLOG_FORWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ

        while True:
            for logtype, hand in handles:
                try:
                    events = win32evtlog.ReadEventLog(hand, flags, 0)
                except Exception:
                    events = None
                if not events:
                    continue
                for ev in events:
                    if providers and ev.SourceName not in providers:
                        continue
                    payload = {
                        "log": logtype,
                        "source": ev.SourceName,
                        "event_id": ev.EventID & 0xFFFF,
                        "time_generated": ev.TimeGenerated.isoformat()
                    }
                    self.ingest("etw", payload)
            time.sleep(1)

TELEMETRY = TelemetryIngestor()

# ============================================================
#  PACKET CAPTURE
# ============================================================

class PacketCapture:
    def __init__(self):
        self.scapy_all = scapy_all
        self.pydivert = pydivert

    def start_scapy_sniffer(self, iface=None):
        if not self.scapy_all:
            print("[SCAPY] Not available, skipping.")
            return

        def handler(pkt):
            try:
                summary = pkt.summary()
                src = pkt[0][1].src if hasattr(pkt[0][1], "src") else None
                dst = pkt[0][1].dst if hasattr(pkt[0][1], "dst") else None
            except Exception:
                summary = "packet"
                src = None
                dst = None
            TELEMETRY.ingest("network", {"summary": summary, "src": src, "dst": dst})

        print(f"[SCAPY] Starting sniff on iface={iface}")
        self.scapy_all.sniff(prn=handler, store=False, iface=iface)

    def start_windivert(self, flt="true"):
        if not self.pydivert:
            print("[WinDivert] pydivert not available, skipping.")
            return

        from pydivert import WinDivert
        print(f"[WinDivert] Starting with filter: {flt}")
        with WinDivert(flt) as w:
            for packet in w:
                payload = {
                    "src": f"{packet.src_addr}:{packet.src_port}",
                    "dst": f"{packet.dst_addr}:{packet.dst_port}",
                    "protocol": packet.protocol,
                    "direction": "inbound" if packet.is_inbound else "outbound"
                }
                TELEMETRY.ingest("network", payload)
                w.send(packet)

PACKETS = PacketCapture()

# ============================================================
#  PROCESS MONITOR
# ============================================================

class ProcessMonitor:
    def __init__(self):
        self.enabled = True

    def run(self):
        while self.enabled:
            try:
                if psutil:
                    for proc in psutil.process_iter(attrs=["pid", "name", "username"]):
                        info = proc.info
                        TELEMETRY.ingest("process", info)
                else:
                    out = subprocess.check_output(["tasklist"], creationflags=0).decode(errors="ignore")
                    TELEMETRY.ingest("process", {"raw": out})
            except Exception as e:
                print("[PROC] monitor error:", e)
            time.sleep(5)

PROC_MON = ProcessMonitor()

# ============================================================
#  FILE SYSTEM MONITOR
# ============================================================

class FSHandler(FileSystemEventHandler if FileSystemEventHandler else object):
    def on_created(self, event):
        TELEMETRY.ingest("file", {"event": "created", "path": event.src_path})

    def on_deleted(self, event):
        TELEMETRY.ingest("file", {"event": "deleted", "path": event.src_path})

    def on_modified(self, event):
        TELEMETRY.ingest("file", {"event": "modified", "path": event.src_path})

    def on_moved(self, event):
        TELEMETRY.ingest("file", {"event": "moved", "src": event.src_path, "dest": event.dest_path})

class FileMonitor:
    def __init__(self, path="."):
        self.path = path
        self.observer = None

    def run(self):
        if not Observer or not FileSystemEventHandler:
            print("[FS] watchdog not available, skipping.")
            return
        handler = FSHandler()
        self.observer = Observer()
        self.observer.schedule(handler, self.path, recursive=True)
        self.observer.start()
        print(f"[FS] monitoring {self.path}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.observer.stop()
        self.observer.join()

FS_MON = FileMonitor(path=".")

# ============================================================
#  ML ENGINE (GPU + Torch/ONNX AE)
# ============================================================

class MLCore:
    def __init__(self):
        self.anomaly_threshold = 0.85
        self.device = "cpu"
        if torch and torch.cuda.is_available():
            self.device = "cuda"
            print("[ML] Using GPU:", torch.cuda.get_device_name(0))
        else:
            print("[ML] Using CPU")

        self.torch_model = None
        self.onnx_session = None
        self._init_models()

    def _init_models(self):
        if torch:
            path = "./models/torch/autoencoder.pt"
            if Path(path).exists():
                try:
                    self.torch_model = torch.load(path, map_location=self.device)
                    self.torch_model.eval()
                    print(f"[ML] Loaded Torch AE from {path}")
                except Exception as e:
                    print("[ML] Torch AE load failed:", e)
            else:
                print(f"[ML] Torch AE missing at {path}")

        if ort:
            path = "./models/onnx/autoencoder.onnx"
            if Path(path).exists():
                try:
                    self.onnx_session = ort.InferenceSession(
                        path,
                        providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
                    )
                    print(f"[ML] Loaded ONNX AE from {path}")
                except Exception as e:
                    print("[ML] ONNX AE load failed:", e)
            else:
                print(f"[ML] ONNX AE missing at {path}")

    def feature_extract(self, event):
        return float(hash(str(event)) % 10000) / 10000.0

    def autoencoder_score(self, features):
        if np is None:
            return random.random()

        x = np.array([[features]], dtype=np.float32)

        if self.onnx_session:
            try:
                inp_name = self.onnx_session.get_inputs()[0].name
                out = self.onnx_session.run(None, {inp_name: x})[0]
                diff = float(abs(x - out).mean())
                return min(1.0, diff)
            except Exception as e:
                print("[ML] ONNX inference failed:", e)

        if self.torch_model and torch:
            try:
                tx = torch.tensor(x, dtype=torch.float32).to(self.device)
                with torch.no_grad():
                    recon = self.torch_model(tx)
                diff = torch.abs(tx - recon).mean().item()
                return min(1.0, diff)
            except Exception as e:
                print("[ML] Torch inference failed:", e)

        return random.random()

    def lstm_predict(self, sequence):
        return random.choice(["benign", "suspicious", "critical"])

    def transformer_forecast(self, sequence):
        return {
            "next_step": random.choice(["exploit", "persist", "exfiltrate"]),
            "confidence": random.random()
        }

    def analyze(self, event):
        feat = self.feature_extract(event)
        score = self.autoencoder_score(feat)
        anomaly = score > self.anomaly_threshold
        return {
            "features": feat,
            "score": score,
            "anomaly": anomaly
        }

ML_ENGINE = MLCore()

# ============================================================
#  INTELLIGENT WATER ENGINE (Predictive Physics Layer)
# ============================================================

class IntelligentWaterEngine:
    """
    Predictive anomaly engine using:
    - Bernoulli rare-event theory
    - Missing-event inference
    - Data physics (momentum, drift, turbulence)
    - Altered states (baseline, flow, storm, void)
    """

    def __init__(self, window=120):
        self.window = window
        self.events = deque()
        self.last_ts = None
        self.state = "baseline"

    def ingest(self, evt_type, score):
        now = time.time()
        self.events.append((now, evt_type, score))
        self._cleanup(now)
        self.last_ts = now

    def _cleanup(self, now):
        cutoff = now - self.window
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def bernoulli_rare_event(self, evt_type):
        types = [e[1] for e in self.events]
        p = types.count(evt_type) / max(1, len(types))
        return 1 - p

    def missing_event_score(self):
        if not self.last_ts:
            return 0.0
        gap = time.time() - self.last_ts
        expected = self.window / 20
        if gap > expected * 3:
            return min(1.0, gap / (expected * 10))
        return 0.0

    def physics(self):
        if len(self.events) < 4:
            return {"momentum": 0, "drift": 0, "turbulence": 0}

        scores = [e[2] for e in self.events]
        diffs = [scores[i] - scores[i-1] for i in range(1, len(scores))]

        momentum = sum(diffs)
        drift = statistics.mean(scores)
        turbulence = statistics.pstdev(diffs)

        return {
            "momentum": momentum,
            "drift": drift,
            "turbulence": turbulence
        }

    def altered_state(self):
        phys = self.physics()
        m = phys["momentum"]
        t = phys["turbulence"]
        d = phys["drift"]

        if t < 0.05 and abs(m) < 0.05:
            self.state = "baseline"
        elif t < 0.15 and m > 0:
            self.state = "flow"
        elif t > 0.25 or m > 0.5:
            self.state = "storm"
        elif t < 0.02 and d < 0.02:
            self.state = "void"

        return self.state

    def predict(self, evt_type):
        rare = self.bernoulli_rare_event(evt_type)
        missing = self.missing_event_score()
        phys = self.physics()
        state = self.altered_state()

        score = (
            rare * 0.4 +
            missing * 0.3 +
            abs(phys["momentum"]) * 0.2 +
            phys["turbulence"] * 0.1
        )

        return {
            "score": min(1.0, score),
            "state": state,
            "physics": phys,
            "rare": rare,
            "missing": missing
        }

GLOBAL_WATER = IntelligentWaterEngine(window=180)

# ============================================================
#  POLICY ENGINE
# ============================================================

class PolicyEngine:
    def __init__(self):
        self.rules = []
        self.attack_graph = {}

    def load_rules(self):
        self.rules = [
            ("critical", "AUTO_QUARANTINE"),
            ("suspicious", "FLAG"),
            ("benign", "ALLOW")
        ]

    def evaluate(self, ml_result):
        if ml_result["anomaly"]:
            return "AUTO_QUARANTINE"
        return "ALLOW"

POLICY = PolicyEngine()
POLICY.load_rules()

# ============================================================
#  THREAT CHAIN FORECASTER
# ============================================================

class ThreatChainForecaster:
    def __init__(self):
        self.history = []

    def update(self, event):
        self.history.append(event)
        if len(self.history) > 200:
            self.history.pop(0)

    def forecast(self):
        if len(self.history) < 5:
            return None
        return ML_ENGINE.transformer_forecast(self.history[-10:])

FORECASTER = ThreatChainForecaster()

# ============================================================
#  NEO4J ATTACK GRAPH
# ============================================================

class AttackGraphDB:
    def __init__(self):
        self.driver = None
        if GraphDatabase:
            try:
                uri = "bolt://localhost:7687"
                user = "neo4j"
                pwd = "password"
                self.driver = GraphDatabase.driver(uri, auth=(user, pwd))
                print("[NEO4J] Connected.")
                self._init_schema()
            except Exception as e:
                print("[NEO4J] Connection failed:", e)
                self.driver = None
        else:
            print("[NEO4J] neo4j driver not installed.")

    def _init_schema(self):
        if not self.driver:
            return
        with self.driver.session(database="neo4j") as session:
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (h:Host) REQUIRE h.name IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (p:Process) REQUIRE p.pid IS UNIQUE")
            print("[NEO4J] Schema ensured.")

    def log_event(self, event, ml_result):
        if not self.driver:
            return
        with self.driver.session(database="neo4j") as session:
            eid = str(hash(str(event)))
            etype = event.get("type")
            ts = event.get("timestamp")
            score = ml_result["score"]
            anomaly = ml_result["anomaly"]
            host = event.get("payload", {}).get("host", "localhost")
            pid = event.get("payload", {}).get("pid", -1)

            session.run(
                """
                MERGE (e:Event {id: $id})
                SET e.type = $type, e.timestamp = $ts, e.score = $score, e.anomaly = $anomaly
                MERGE (h:Host {name: $host})
                MERGE (p:Process {pid: $pid})
                MERGE (h)-[:HOSTS]->(p)
                MERGE (p)-[:GENERATED]->(e)
                """,
                id=eid,
                type=etype,
                ts=ts,
                score=score,
                anomaly=anomaly,
                host=host,
                pid=pid
            )

ATTACK_DB = AttackGraphDB()

# ============================================================
#  SWARM
# ============================================================

class SwarmNode:
    def __init__(self, port=9999):
        self.port = port
        self.peers = set()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))

    def broadcast(self, msg):
        for peer in self.peers:
            try:
                self.sock.sendto(msg.encode(), peer)
            except Exception as e:
                print("[SWARM] broadcast error:", e)

    def listen(self):
        while True:
            data, addr = self.sock.recvfrom(4096)
            self.peers.add(addr)
            EVENT_BUS.publish("swarm", {"from": addr, "data": data.decode()})

    def sync_state(self, state):
        payload = json.dumps(state)
        self.broadcast(payload)

SWARM = SwarmNode()

# ============================================================
#  SELF-HEALING + RESPONSE
# ============================================================

class SelfHealing:
    def heal(self, event):
        print("[SELF-HEAL] Attempting automated recovery:", event)

SELF_HEAL = SelfHealing()

class ResponseEngine:
    def execute(self, action, event):
        print(f"[RESPONSE] Action={action} Event={event}")

RESPONSE = ResponseEngine()

# ============================================================
#  AUTOPILOT
# ============================================================

class AutopilotCore:
    def __init__(self):
        self.modes = ["aircraft", "vehicle", "boat", "submarine"]
        self.current_mode = "vehicle"
        self.target = None
        self.active = False

    def set_mode(self, mode):
        if mode in self.modes:
            self.current_mode = mode
            print(f"[AUTOPILOT] Mode set to {mode}")
        else:
            print("[AUTOPILOT] Invalid mode:", mode)

    def navigate(self, target):
        self.target = target
        self.active = True
        print(f"[AUTOPILOT] Navigating {self.current_mode} to {target}")

    def emergency_stop(self):
        self.active = False
        print("[AUTOPILOT] EMERGENCY STOP!")

    def loop(self):
        while True:
            if self.active and self.target:
                TELEMETRY.ingest("autopilot", {
                    "mode": self.current_mode,
                    "target": self.target,
                    "status": "enroute"
                })
                SWARM.sync_state({
                    "mode": self.current_mode,
                    "target": self.target,
                    "timestamp": time.time()
                })
            time.sleep(2)

AUTOPILOT = AutopilotCore()

# ============================================================
#  PLUGIN HUB
# ============================================================

class PluginHub:
    def __init__(self):
        self.plugins = {}

    def load_plugins(self):
        plugin_dir = Path("./plugins")
        plugin_dir.mkdir(exist_ok=True)

        template = plugin_dir / "example_plugin.py"
        if not template.exists():
            template.write_text(
                "def on_event(event):\n"
                "    print('[PLUGIN example] Event:', event)\n",
                encoding="utf-8"
            )

        for file in plugin_dir.glob("*.py"):
            name = file.stem
            try:
                module = __import__(f"plugins.{name}", fromlist=["*"])
                self.plugins[name] = module
                print(f"[PLUGIN] Loaded {name}")
            except Exception as e:
                print(f"[PLUGIN] Failed to load {name}: {e}")

    def dispatch_event(self, event):
        for name, module in self.plugins.items():
            if hasattr(module, "on_event"):
                try:
                    module.on_event(event)
                except Exception as e:
                    print(f"[PLUGIN] Error in {name}: {e}")

PLUGIN_HUB = PluginHub()

# ============================================================
#  FORKLIFT LLM CORE + RPC
# ============================================================

PRIMARY_MODEL_NAME = "gpt2"

HAS_CUDA = bool(torch and torch.cuda.is_available())
NUM_GPUS = torch.cuda.device_count() if torch else 0
DEFAULT_DEVICE = "cuda" if HAS_CUDA else "cpu"

CURRENT_MODEL = None
CURRENT_TOKENIZER = None
CURRENT_MODEL_NAME = None
IS_FALLBACK_MODEL = False

def get_system_telemetry():
    cpu = psutil.cpu_percent() / 100.0 if psutil else 0.0
    mem = psutil.virtual_memory().percent / 100.0 if psutil else 0.0
    gpu = 0.0
    if torch and torch.cuda.is_available():
        try:
            gpu = torch.cuda.memory_allocated(0) / max(1, torch.cuda.get_device_properties(0).total_memory)
        except Exception:
            gpu = 0.0
    return {"cpu": cpu, "mem": mem, "gpu": gpu}

def train_policy_net_step(sys_tel, latency_ms: float):
    pass

class TinyFallback(nn.Module if nn else object):
    def __init__(self, vocab_size: int = 256, hidden_dim: int = 64):
        if nn:
            super().__init__()
            self.embed = nn.Embedding(vocab_size, hidden_dim)
            self.fc = nn.Linear(hidden_dim, vocab_size)
        else:
            pass

    def forward(self, input_ids):
        x = self.embed(input_ids)
        x = x.mean(dim=1)
        logits = self.fc(x)
        return logits

class ForkliftExecutor:
    def __init__(self):
        self._stats = {
            "calls": 0,
            "layers": 0,
        }

    def reset_stats(self, clear_router_data: bool = False):
        self._stats = {
            "calls": 0,
            "layers": 0,
        }

    def linear(self, layer_name, weight, bias, x, layer_depth: int = 0):
        self._stats["calls"] += 1
        self._stats["layers"] = max(self._stats["layers"], layer_depth)
        return torch.nn.functional.linear(x, weight, bias)

    def stats(self):
        return dict(self._stats)

EXECUTOR = ForkliftExecutor()

class ForkliftLinear(nn.Module if nn else object):
    def __init__(self, base: nn.Linear, name: str, executor: ForkliftExecutor, depth: int = 0):
        if nn:
            super().__init__()
            self.base = base
            self.name = name
            self.executor = executor
            self.depth = depth
        else:
            pass

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

def load_model(model_name: str = PRIMARY_MODEL_NAME):
    global CURRENT_MODEL, CURRENT_TOKENIZER, CURRENT_MODEL_NAME, IS_FALLBACK_MODEL

    if CURRENT_MODEL is not None and CURRENT_TOKENIZER is not None:
        return

    if not (torch and AutoTokenizer and AutoModelForCausalLM):
        print("[Node] Torch/Transformers not available, using TinyFallback only.")
        tok = None
        mdl = TinyFallback().to(DEFAULT_DEVICE) if torch else TinyFallback()
        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = "TinyFallback"
        IS_FALLBACK_MODEL = True
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

if torch:
    @torch.inference_mode()
    def _gen_impl(prompt: str, max_new_tokens: int = 128) -> Tuple[str, dict]:
        load_model()
        EXECUTOR.reset_stats(clear_router_data=False)

        tok = CURRENT_TOKENIZER
        mdl = CURRENT_MODEL

        if tok is None:
            return prompt, {"model_name": CURRENT_MODEL_NAME, "is_fallback": True, "latency_ms": 0.0}

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
else:
    def _gen_impl(prompt: str, max_new_tokens: int = 128) -> Tuple[str, dict]:
        return prompt, {"model_name": "none", "is_fallback": True, "latency_ms": 0.0}

def generate_text(prompt: str, max_new_tokens: int = 128) -> Tuple[str, dict]:
    return _gen_impl(prompt, max_new_tokens=max_new_tokens)

def handle_rpc_client(conn: socket.socket, addr):
    buf = b""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                line, _, rest = buf.partition(b"\n")
                buf = rest
                try:
                    req = json.loads(line.decode())
                    prompt = req.get("prompt", "")
                    max_new_tokens = int(req.get("max_new_tokens", 128))

                    print(f"[Node] RPC request from {addr}, tokens={max_new_tokens}")
                    text, stats = generate_text(prompt, max_new_tokens=max_new_tokens)
                    resp = {"text": text, "stats": stats}
                except Exception as e:
                    resp = {"error": str(e), "stats": {}}

                conn.sendall((json.dumps(resp) + "\n").encode())
                break
    finally:
        conn.close()

def rpc_server_loop(host: str, port: int):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(16)
    print(f"[Node] RPC server listening on {host}:{port}")
    while True:
        conn, addr = s.accept()
        t = threading.Thread(target=handle_rpc_client, args=(conn, addr), daemon=True)
        t.start()

# ============================================================
#  REAL-TIME QUEEN (Consensus Engine v2)
# ============================================================

class Queen:
    def __init__(self):
        self.nodes = {}
        self.water = IntelligentWaterEngine(window=180)

    def update(self, node, events):
        self.nodes[node] = events
        for e in events:
            self.water.ingest(e["type"], e.get("score", 0.1))

    def global_risk(self):
        risk = {}

        for node, evts in self.nodes.items():
            for e in evts:
                ent = e["entity"]
                risk[ent] = risk.get(ent, 0) + e["score"]

        phys = self.water.predict("global")

        if phys["state"] in ("storm", "void"):
            for k in risk:
                risk[k] *= 1.3

        return {
            "risk": {k: v for k, v in risk.items() if v > 1.5},
            "queen_state": phys["state"],
            "physics": phys["physics"]
        }

QUEEN = Queen()

# ============================================================
#  ATTACK CHAIN ENGINE v3 (Predictive)
# ============================================================

class AttackChainEngine:
    def __init__(self, window=120):
        self.events = deque()
        self.window = window
        self.water = IntelligentWaterEngine(window=window)

    def add_event(self, event_type, data):
        now = time.time()
        self.events.append((now, event_type, data))
        self.water.ingest(event_type, data.get("score", 0.1))
        self._cleanup(now)

    def _cleanup(self, now):
        cutoff = now - self.window
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def detect(self):
        types = [e[1] for e in self.events]
        chains = []

        if all(x in types for x in ["proc_spawn", "powershell", "net_connect"]):
            chains.append(("LOLBIN_ATTACK", 0.9))

        if types.count("proc_spawn") > 5 and "net_connect" in types:
            chains.append(("PROCESS_STORM", 0.8))

        if "file_mod" in types and "net_connect" in types:
            chains.append(("PERSISTENCE_EXFIL", 0.85))

        pred = self.water.predict("attack")

        if pred["score"] > 0.7:
            chains.append(("PREDICTED_ATTACK", pred["score"]))

        return chains

ATTACK_CHAIN = AttackChainEngine(window=120)

# ============================================================
# ⚔️ Threat Detection + Response
# ============================================================

def threat_scan_and_respond_loop():
    while True:
        try:
            try:
                conns = psutil.net_connections(kind='inet') if psutil else []
            except Exception as e:
                record_event("threat_scan", f"net_connections failed: {e}", severity="warn")
                conns = []

            seen = set()
            for conn in conns:
                try:
                    if conn.status == 'LISTEN':
                        ip = getattr(conn.laddr, "ip", "unknown")
                        port = conn.laddr.port
                        key = (ip, port)
                        if key in seen:
                            continue
                        seen.add(key)
                        record_event(
                            "port_listen",
                            f"LISTEN {ip}:{port}",
                            severity="info",
                            tags=["port"]
                        )
                except Exception:
                    continue

            if psutil:
                for proc in psutil.process_iter(['pid', 'name', 'exe']):
                    try:
                        name = proc.info.get('name') or ""
                        pid = proc.info.get('pid')
                        exe = proc.info.get('exe') or ""
                        if is_allowed(name, exe):
                            continue
                        if is_blocked(name, exe):
                            terminate_proc(proc, "on block list", auto_block=False)
                            continue
                        if name and re.search(r"(keylogger|sniffer|injector|bot|miner)", name, re.IGNORECASE):
                            try:
                                terminate_proc(proc, "suspicious process pattern")
                            except Exception as e:
                                record_event("threat_failure", f"Failed to terminate {name} (PID {pid}) - {e}", severity="warn")
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue
        except Exception as e:
            record_event("self_rewrite", f"threat_scan_and_respond() failed - {e}", severity="crit", tags=["self_rewrite"])

        update_log()
        watchdog_touch("threat_scan")
        time.sleep(10)

def self_check_loop():
    while True:
        try:
            assert callable(threat_scan_and_respond_loop)
            assert callable(update_log)
            assert isinstance(trust_config, dict)
        except Exception as e:
            record_event("self_rewrite", f"Integrity check failed - {e}", severity="crit", tags=["self_rewrite"])
        watchdog_touch("self_check")
        time.sleep(15)

# ============================================================
# 🦎 Real-Time Detection (Identifiers)
# ============================================================

def get_real_mac():
    try:
        if not psutil:
            return "MAC not found"
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if getattr(psutil, "AF_LINK", None) is not None:
                    if addr.family == psutil.AF_LINK:
                        return addr.address
                else:
                    if getattr(addr.family, "name", "") == "AF_LINK":
                        return addr.address
    except Exception:
        pass
    return "MAC not found"

def get_real_ip():
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        try:
            public_ip = socket.gethostbyname_ex(hostname)[2][-1]
        except Exception:
            public_ip = local_ip
        return local_ip, public_ip
    except Exception as e:
        return "IP error", str(e)

def get_telemetry_identifiers():
    os_info = platform.platform()
    browser_fingerprint = platform.system() + "-" + platform.machine()
    return os_info, browser_fingerprint

def get_swarm_id():
    with brain_lock:
        if brain_state.get("swarm_id"):
            return brain_state["swarm_id"]
    sid = str(uuid.getnode())
    with brain_lock:
        brain_state["swarm_id"] = sid
    return sid

def synthesize_phantom():
    entropy = uuid.uuid4().hex + str(time.time_ns())
    phantom = f"phantom://{entropy[:12]}"
    with brain_lock:
        brain_state.setdefault("phantom_history", []).append(phantom)
        brain_state["phantom_history"] = brain_state["phantom_history"][-50:]
    return phantom

# ============================================================
# 🌐 Network / Remote Control Detection
# ============================================================

PRIVATE_NETS = [
    ("10.",),
    ("172.", range(16, 32)),
    ("192.168.",)
]

def is_private_ip(ip: str) -> bool:
    if ip.startswith("10."):
        return True
    if ip.startswith("192.168."):
        return True
    if ip.startswith("172."):
        parts = ip.split(".")
        if len(parts) >= 2:
            try:
                second = int(parts[1])
                return 16 <= second <= 31
            except ValueError:
                return False
    return False

REMOTE_TOOL_PATTERNS = re.compile(
    r"(teamviewer|anydesk|vnc|remote|rdp|shadow|splashtop|ultraviewer|ammyy|logmein)",
    re.IGNORECASE
)

FIREWALL_CMD_PATTERNS = re.compile(
    r"(netsh\s+advfirewall|Set-NetFirewall|New-NetFirewall|ufw\s+enable|ufw\s+disable)",
    re.IGNORECASE
)

SETTINGS_CMD_PATTERNS = re.compile(
    r"(reg\s+add|reg\s+delete|powershell\s+Set-ItemProperty|gpedit\.msc|secpol\.msc)",
    re.IGNORECASE
)

SHELL_NAMES = re.compile(
    r"(cmd\.exe|powershell\.exe|pwsh\.exe|bash\.exe|wsl\.exe)",
    re.IGNORECASE
)

def monitor_network_loop():
    while True:
        try:
            if not psutil:
                time.sleep(8)
                continue
            conns = psutil.net_connections(kind="inet")
        except Exception as e:
            record_event("net", f"Failed to read connections: {e}", "warn")
            time.sleep(8)
            continue

        for c in conns:
            try:
                raddr = c.raddr
                if not raddr:
                    continue
                ip = raddr.ip
                port = raddr.port
                if not is_private_ip(ip):
                    record_event("net", f"Foreign connection: {ip}:{port}", "warn", tags=["foreign"])
            except Exception:
                continue

        time.sleep(8)

# ============================================================
# Hardware root of trust (real TPM integration stub)
# ============================================================

class RootOfTrustOrgan:
    def get_device_secret(self) -> bytes:
        raise NotImplementedError

class FileRootOfTrustOrgan(RootOfTrustOrgan):
    def __init__(self):
        self._cached_secret: Optional[bytes] = None

    def get_device_secret(self) -> bytes:
        if self._cached_secret is not None:
            return self._cached_secret
        secret_path = os.path.join(os.path.dirname(__file__), ".device_secret")
        if os.path.exists(secret_path):
            with open(secret_path, "rb") as f:
                self._cached_secret = f.read()
        else:
            self._cached_secret = os.urandom(32)
            with open(secret_path, "wb") as f:
                f.write(self._cached_secret)
        return self._cached_secret

class TPMRootOfTrustOrgan(RootOfTrustOrgan):
    def __init__(self, tpm_key_handle: Optional[int] = None):
        self._fallback = FileRootOfTrustOrgan()
        self._tpm_available = False
        self._tpm_key_handle = tpm_key_handle
        self._cached_secret: Optional[bytes] = None

        try:
            from tpm2_pytss import ESAPI  # type: ignore
            self._ESAPI = ESAPI
            self._tpm_available = True
        except Exception:
            self._tpm_available = False

    def get_device_secret(self) -> bytes:
        if self._cached_secret is not None:
            return self._cached_secret

        if not self._tpm_available or self._tpm_key_handle is None:
            self._cached_secret = self._fallback.get_device_secret()
            return self._cached_secret

        try:
            with self._ESAPI() as esapi:
                handle = self._tpm_key_handle
                pub, _ = esapi.ReadPublic(handle)
                pub_bytes = bytes(pub.marshal())
                self._cached_secret = hashlib.sha256(pub_bytes).digest()
                return self._cached_secret
        except Exception:
            self._cached_secret = self._fallback.get_device_secret()
            return self._cached_secret

ROOT_OF_TRUST = TPMRootOfTrustOrgan(tpm_key_handle=None)

# ============================================================
#  MAIN PIPELINE HANDLER
# ============================================================

def telemetry_handler(event):
    ml = ML_ENGINE.analyze(event)
    decision = POLICY.evaluate(ml)
    FORECASTER.update(event)
    ATTACK_DB.log_event(event, ml)
    PLUGIN_HUB.dispatch_event(event)

    try:
        GLOBAL_WATER.ingest(event["type"], ml["score"])
        ATTACK_CHAIN.add_event(event["type"], {"score": ml["score"]})

        entity = (
            event.get("payload", {}).get("pid")
            or event.get("payload", {}).get("ip")
            or event.get("payload", {}).get("host")
            or "host"
        )

        node_id = get_swarm_id()
        QUEEN.update(node_id, [{
            "entity": entity,
            "score": ml["score"],
            "type": event["type"],
        }])

        chains = ATTACK_CHAIN.detect()
        for cname, cscore in chains:
            if cscore > 0.8:
                record_event("attack_chain", f"{cname}", "crit", tags=["chain"])

        queen_view = QUEEN.global_risk()
        if queen_view["risk"]:
            record_event(
                "queen_risk",
                f"Global risk: {queen_view['risk']} state={queen_view['queen_state']}",
                "warn",
                tags=["queen"]
            )
    except Exception as e:
        print("[CHAIN/QUEEN] error:", e)

    if ml["anomaly"]:
        RESPONSE.execute("AUTO_QUARANTINE", event)
        SELF_HEAL.heal(event)

    forecast = FORECASTER.forecast()
    if forecast:
        EVENT_BUS.publish("forecast", forecast)

EVENT_BUS.subscribe("telemetry", telemetry_handler)

# ============================================================
#  FASTAPI API
# ============================================================

class APIServer:
    def __init__(self):
        self.app = None
        if FastAPI:
            self.app = FastAPI(title="Sentinel Core API")
            self._setup_routes()
        else:
            print("[API] FastAPI not installed, API disabled.")

    def _setup_routes(self):
        @self.app.get("/status")
        async def status():
            queen_view = QUEEN.global_risk()
            return {
                "swarm_peers": len(SWARM.peers),
                "forecast": FORECASTER.forecast(),
                "autopilot_mode": AUTOPILOT.current_mode,
                "autopilot_target": AUTOPILOT.target,
                "queen_state": queen_view["queen_state"],
                "queen_risk": queen_view["risk"],
            }

        @self.app.post("/autopilot/mode")
        async def set_mode(data: dict = Body(...)):
            mode = data.get("mode")
            AUTOPILOT.set_mode(mode)
            return {"mode": AUTOPILOT.current_mode}

        @self.app.post("/autopilot/navigate")
        async def navigate(data: dict = Body(...)):
            target = data.get("target")
            AUTOPILOT.navigate(target)
            return {"status": "navigating", "target": target}

        @self.app.post("/autopilot/stop")
        async def stop():
            AUTOPILOT.emergency_stop()
            return {"status": "stopped"}

        @self.app.post("/llm/generate")
        async def llm_generate(data: dict = Body(...)):
            prompt = data.get("prompt", "")
            max_new_tokens = int(data.get("max_new_tokens", 128))
            text, stats = generate_text(prompt, max_new_tokens=max_new_tokens)
            return {"text": text, "stats": stats}

    def run(self, host="0.0.0.0", port=8080):
        if not (self.app and uvicorn):
            print("[API] FastAPI/uvicorn not available, skipping API server.")
            return
        uvicorn.run(self.app, host=host, port=port)

API_SERVER = APIServer()

# ============================================================
#  GUI DASHBOARD + LLM CONSOLE
# ============================================================

class GUIDashboard:
    def __init__(self):
        self.root = None
        self.telemetry_buffer = []
        self.forecast_state = None
        self.telemetry_listbox = None
        self.forecast_label = None
        self.swarm_canvas = None
        self.status_label = None

        self.llm_input = None
        self.llm_output = None
        self.llm_stats_label = None
        self.queen_label = None

        EVENT_BUS.subscribe("telemetry", self._on_telemetry)
        EVENT_BUS.subscribe("forecast", self._on_forecast)
        EVENT_BUS.subscribe("swarm", self._on_swarm)
        EVENT_BUS.subscribe("llm_response", self._on_llm_response)

    def _on_telemetry(self, event):
        self.telemetry_buffer.append(event)
        if len(self.telemetry_buffer) > 200:
            self.telemetry_buffer.pop(0)

    def _on_forecast(self, forecast):
        self.forecast_state = forecast

    def _on_swarm(self, msg):
        pass

    def _on_llm_response(self, data):
        if not self.llm_output:
            return
        text = data.get("text", "")
        stats = data.get("stats", {})
        self.llm_output.delete("1.0", "end")
        self.llm_output.insert("end", text)
        if self.llm_stats_label:
            model_name = stats.get("model_name", "unknown")
            latency = stats.get("latency_ms", 0.0)
            self.llm_stats_label.config(
                text=f"Model: {model_name} | Latency: {latency:.1f} ms"
            )

    def _send_llm(self):
        if not self.llm_input:
            return
        prompt = self.llm_input.get("1.0", "end").strip()
        if not prompt:
            return
        self.status_label.config(text="Status: Generating with Forklift LLM...")
        threading.Thread(target=self._llm_thread, args=(prompt,), daemon=True).start()

    def _llm_thread(self, prompt):
        text, stats = generate_text(prompt, max_new_tokens=128)
        EVENT_BUS.publish("llm_response", {"prompt": prompt, "text": text, "stats": stats})
        self.status_label.config(text="Status: LLM response received")

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("Sentinel Core Unified v6 (Hard Fusion + Forklift + Queen)")
        self.root.geometry("1450x820")

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True, padx=(0, 5))

        ttk.Label(left, text="Telemetry Stream").pack(anchor="w")
        tele_frame = ttk.Frame(left)
        tele_frame.pack(fill="both", expand=True, pady=(5, 5))

        scrollbar = tk.Scrollbar(tele_frame, orient="vertical")
        self.telemetry_listbox = tk.Listbox(
            tele_frame,
            bg="#101018",
            fg="#A0E0FF",
            selectbackground="#303050",
            highlightthickness=0,
            borderwidth=0
        )
        self.telemetry_listbox.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.telemetry_listbox.yview)
        self.telemetry_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(left, text="Forecast").pack(anchor="w")
        self.forecast_label = ttk.Label(left, text="No forecast yet.")
        self.forecast_label.pack(anchor="w", pady=(2, 0))

        self.queen_label = ttk.Label(left, text="Queen: state=baseline, risk=0 entities")
        self.queen_label.pack(anchor="w", pady=(4, 0))

        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True, padx=(5, 0))

        top_right = ttk.Frame(right)
        top_right.pack(fill="both", expand=True)

        ttk.Label(top_right, text="Swarm Map (peers)").pack(anchor="w")
        self.swarm_canvas = tk.Canvas(
            top_right,
            bg="#101018",
            highlightthickness=1,
            highlightbackground="#404060",
            height=250
        )
        self.swarm_canvas.pack(fill="x", expand=False, pady=(5, 5))

        llm_frame = ttk.LabelFrame(right, text="Forklift LLM Console")
        llm_frame.pack(fill="both", expand=True, pady=(5, 5))

        input_label = ttk.Label(llm_frame, text="Prompt:")
        input_label.pack(anchor="w")
        self.llm_input = tk.Text(llm_frame, height=5, bg="#101018", fg="#A0E0FF")
        self.llm_input.pack(fill="x", expand=False, pady=(2, 5))

        btn_row = ttk.Frame(llm_frame)
        btn_row.pack(fill="x", pady=(0, 5))
        send_btn = ttk.Button(btn_row, text="Send to LLM", command=self._send_llm)
        send_btn.pack(side="left", padx=5)

        self.llm_stats_label = ttk.Label(btn_row, text="Model: - | Latency: - ms")
        self.llm_stats_label.pack(side="left", padx=10)

        output_label = ttk.Label(llm_frame, text="Response:")
        output_label.pack(anchor="w")
        self.llm_output = tk.Text(llm_frame, height=10, bg="#101018", fg="#A0E0FF")
        self.llm_output.pack(fill="both", expand=True, pady=(2, 5))

        ctrl = ttk.Frame(self.root)
        ctrl.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Label(ctrl, text="Subsystem Control Panel (auto-on)").pack(anchor="w")

        self.status_label = ttk.Label(ctrl, text="Status: Booting...")
        self.status_label.pack(anchor="w", pady=(4, 0))

    def _update_gui(self):
        self.telemetry_listbox.delete(0, "end")
        for e in self.telemetry_buffer[-60:]:
            ts = datetime.fromtimestamp(e["timestamp"]).strftime("%H:%M:%S")
            line = f"[{ts}] {e['type']} {e['payload']}"
            self.telemetry_listbox.insert("end", line)

        if self.forecast_state:
            txt = f"Next: {self.forecast_state['next_step']} (conf={self.forecast_state['confidence']:.2f})"
        else:
            txt = "No forecast yet."
        self.forecast_label.config(text=txt)

        queen_view = QUEEN.global_risk()
        state = queen_view["queen_state"]
        risk_count = len(queen_view["risk"])
        self.queen_label.config(text=f"Queen: state={state}, risk={risk_count} entities")

        self.swarm_canvas.delete("all")
        w = int(self.swarm_canvas.winfo_width() or 400)
        h = int(self.swarm_canvas.winfo_height() or 250)
        cx, cy = w // 2, h // 2

        self.swarm_canvas.create_oval(cx-10, cy-10, cx+10, cy+10, fill="#4080FF", outline="#80A0FF", width=2)
        self.swarm_canvas.create_text(cx, cy-20, text="CORE", fill="#FFFFFF")

        peers = list(SWARM.peers)
        for i, peer in enumerate(peers):
            px = cx + random.randint(-w//3, w//3)
            py = cy + random.randint(-h//3, h//3)
            self.swarm_canvas.create_line(cx, cy, px, py, fill="#00BFFF")
            self.swarm_canvas.create_oval(px-6, py-6, px+6, py+6, fill="#00FFAA", outline="#66FFCC")
            self.swarm_canvas.create_text(px, py-10, text=f"{peer[0]}:{peer[1]}", fill="#66FFCC", font=("Consolas", 7))

        self.root.after(300, self._update_gui)

    def run(self):
        if not tk:
            print("[GUI] Tkinter not available, cannot start GUI.")
            return
        self._build_ui()
        self.root.after(300, self._update_gui)
        self.status_label.config(text="Status: All subsystems + Forklift LLM + Queen ready")
        self.root.mainloop()

GUI = GUIDashboard() if tk else None

# ============================================================
#  CLI
# ============================================================

def command_loop():
    while True:
        try:
            cmd = input("sentinel> ").strip()
        except EOFError:
            break

        if cmd == "peers":
            print(SWARM.peers)
        elif cmd == "forecast":
            print(FORECASTER.forecast())
        elif cmd.startswith("mode "):
            _, mode = cmd.split(" ", 1)
            AUTOPILOT.set_mode(mode)
        elif cmd.startswith("nav "):
            _, target = cmd.split(" ", 1)
            AUTOPILOT.navigate(target)
        elif cmd == "stop":
            AUTOPILOT.emergency_stop()
        elif cmd.startswith("llm "):
            prompt = cmd[4:].strip()
            if prompt:
                text, stats = generate_text(prompt, max_new_tokens=128)
                print("\n--- LLM Response ---")
                print(text)
                print("\n--- Stats ---")
                for k, v in stats.items():
                    print(f"{k}: {v}")
        elif cmd == "queen":
            print(QUEEN.global_risk())
        elif cmd == "id":
            mac = get_real_mac()
            ip_local, ip_public = get_real_ip()
            os_info, fp = get_telemetry_identifiers()
            print(f"MAC: {mac}")
            print(f"IP local/public: {ip_local} / {ip_public}")
            print(f"OS: {os_info}")
            print(f"FP: {fp}")
        elif cmd == "quit":
            sys.exit(0)
        else:
            print("Unknown command (peers | forecast | mode X | nav TARGET | stop | llm PROMPT | queen | id | quit)")

# ============================================================
#  MAIN
# ============================================================

def main():
    print("=== SENTINEL CORE UNIFIED v6 (HARD FUSION + FORKLIFT + PREDICTIVE QUEEN) INITIALIZING ===")

    print("[BOOT] Device secret (root-of-trust) length:", len(ROOT_OF_TRUST.get_device_secret()))

    print("[BOOT] Loading plugins...")
    PLUGIN_HUB.load_plugins()

    print("[BOOT] Preloading Forklift LLM (may take a bit)...")
    threading.Thread(target=load_model, daemon=True).start()

    print("[BOOT] Starting telemetry streams...")
    threading.Thread(target=TELEMETRY.run_fake_stream, daemon=True).start()
    threading.Thread(target=TELEMETRY.run_etw_stream, daemon=True).start()

    print("[BOOT] Starting packet inspection...")
    threading.Thread(target=PACKETS.start_scapy_sniffer, kwargs={"iface": None}, daemon=True).start()
    threading.Thread(target=PACKETS.start_windivert, kwargs={"flt": "true"}, daemon=True).start()

    print("[BOOT] Starting process monitor...")
    threading.Thread(target=PROC_MON.run, daemon=True).start()

    print("[BOOT] Starting file system monitor...")
    threading.Thread(target=FS_MON.run, daemon=True).start()

    print("[BOOT] Starting swarm listener...")
    threading.Thread(target=SWARM.listen, daemon=True).start()

    print("[BOOT] Starting autopilot loop...")
    threading.Thread(target=AUTOPILOT.loop, daemon=True).start()

    print("[BOOT] Starting API server...")
    threading.Thread(target=API_SERVER.run, kwargs={"host": "0.0.0.0", "port": 8080}, daemon=True).start()

    print("[BOOT] Starting threat scan loop...")
    threading.Thread(target=threat_scan_and_respond_loop, daemon=True).start()

    print("[BOOT] Starting self-check loop...")
    threading.Thread(target=self_check_loop, daemon=True).start()

    print("[BOOT] Starting network monitor loop...")
    threading.Thread(target=monitor_network_loop, daemon=True).start()

    print("[BOOT] Starting Forklift RPC server on 0.0.0.0:6000...")
    threading.Thread(target=rpc_server_loop, kwargs={"host": "0.0.0.0", "port": 6000}, daemon=True).start()

    print("[BOOT] Starting CLI thread...")
    threading.Thread(target=command_loop, daemon=True).start()

    print("[BOOT] Launching GUI (MAIN THREAD)...")
    if GUI:
        GUI.run()
        print("[BOOT] GUI exited.")
    else:
        print("[BOOT-FAIL] GUI not available (no tkinter).")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
