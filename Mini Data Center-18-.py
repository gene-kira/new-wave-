import socket
import threading
import json
import time
import http.server
import socketserver
import os
import random
import argparse
from datetime import datetime
import http.client
import traceback
import subprocess
import sys
import hashlib
import pathlib

import psutil

# ============================================================
#  OPTIONAL GPU / SERVICE / DL / TRACING LIBS
# ============================================================

try:
    import pynvml
    pynvml.nvmlInit()
    HAS_GPU = True
except Exception:
    HAS_GPU = False

HAS_AMD = False
HAS_INTEL_GPU = False
HAS_NPU = False

try:
    result = subprocess.run(
        ["rocm-smi", "--showuse"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=1
    )
    if result.returncode == 0:
        HAS_AMD = True
except Exception:
    HAS_AMD = False

try:
    result = subprocess.run(
        ["intel_gpu_top", "-J", "-s", "100"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=1
    )
    if result.returncode == 0:
        HAS_INTEL_GPU = True
except Exception:
    HAS_INTEL_GPU = False

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
    HAS_WIN_SERVICE = True
except ImportError:
    HAS_WIN_SERVICE = False

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    HAS_TORCH = True
except Exception:
    HAS_TORCH = False

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter
    HAS_OTEL = True
except Exception:
    HAS_OTEL = False

TRACER = None

def init_tracing(service_name="hyper_swarm"):
    global TRACER
    if not HAS_OTEL:
        return
    provider = TracerProvider()
    # Console exporter (always)
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    # Jaeger exporter (if reachable)
    try:
        jaeger_exporter = JaegerExporter(
            agent_host_name="localhost",
            agent_port=6831,
        )
        provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
    except Exception:
        pass
    trace.set_tracer_provider(provider)
    TRACER = trace.get_tracer(service_name)

def traced_span(name):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            if TRACER is None:
                return fn(*args, **kwargs)
            with TRACER.start_as_current_span(name):
                return fn(*args, **kwargs)
        return wrapper
    return decorator

# ============================================================
#  ARGS
# ============================================================

parser = argparse.ArgumentParser()
parser.add_argument("--role", choices=["queen", "node", "agent"], default="node")
parser.add_argument("--name", default="Node-A")
parser.add_argument("--queen-host", default="127.0.0.1")
parser.add_argument("--queen-port", type=int, default=5050)
parser.add_argument("--queen-api-port", type=int, default=8080)
parser.add_argument("--peer-queen", action="append", default=[])
parser.add_argument("--config", default=None)
parser.add_argument("--agent-port", type=int, default=9000)
args, _ = parser.parse_known_args()

ROLE = args.role
NAME = args.name

QUEEN_HOST = args.queen_host
QUEEN_TCP_PORT = args.queen_port
QUEEN_API_PORT = args.queen_api_port
PEER_QUEENS = args.peer_queen
CONFIG_PATH = args.config or f"config_{NAME}.json"
AGENT_PORT = args.agent_port

# ============================================================
#  AUTO PORT
# ============================================================

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.close()
    return port

# ============================================================
#  CONFIG + BORG DEFAULTS
# ============================================================

DEFAULT_CONFIG = {
    "name": NAME,
    "role": ROLE,
    "heartbeat_interval": 2,
    "snapshot_folder": f"snapshots_{NAME}",
    "backup_folder": f"backups_{NAME}",
    "suction_folder": f"suction_{NAME}",
    "allow_telemetry": True,
    "strictness": 1,
    "stress_enabled": True,
    "node_class": None,
    "autoscale": {
        "enabled": True,
        "cpu_high": 75,
        "cpu_low": 25,
        "min_nodes": 1,
        "max_nodes": 10,
        "mode": "both",
        "remote_agents": [],
        "predictive": True,
        "history_len": 60
    },
    "storage": {
        "type": "json",
        "path": f"state_{NAME}.json"
    },
    "borg": {
        "enabled": False,
        "paths": []
    },
    "borg_index": {
        "path": f"borg_index_{NAME}.json"
    },
    "ml": {
        "enabled": True,
        "default_model_type": "zscore",
        "input_keys": [
            "cpu", "gpu", "gpu_mem", "gpu_temp",
            "disk", "net", "cpu_proc", "gpu_proc",
            "vram_proc", "gpu_amd", "gpu_intel", "npu"
        ],
        "output_key": "anomaly_score",
        "training_min_samples": 20,
        "per_class": {
            "gpu": {
                "model_type": "autoencoder",
                "model_hash": None
            },
            "storage": {
                "model_type": "iso_forest",
                "model_hash": None
            },
            "general": {
                "model_type": "zscore",
                "model_hash": None
            }
        },
        "automl": {
            "enabled": True,
            "candidates": [
                "zscore",
                "iso_forest",
                "mlp",
                "autoencoder",
                "lstm",
                "transformer",
                "tcn",
                "gnn"
            ],
            "max_epochs": 40
        }
    }
}

CONFIG_LOCK = threading.Lock()
CONFIG = DEFAULT_CONFIG.copy()
CONFIG_MTIME = None

AVAILABLE_DRIVES = []
BORG_PATHS = []

BORG_INDEX_LOCK = threading.Lock()
BORG_INDEX = {}

def ensure_dirs():
    os.makedirs(CONFIG["snapshot_folder"], exist_ok=True)
    os.makedirs(CONFIG["backup_folder"], exist_ok=True)
    os.makedirs(CONFIG["suction_folder"], exist_ok=True)

def save_config_file():
    with CONFIG_LOCK:
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(CONFIG, f, indent=2)
        except Exception:
            traceback.print_exc()

def load_borg_index():
    global BORG_INDEX
    idx_path = CONFIG.get("borg_index", {}).get("path", f"borg_index_{NAME}.json")
    if os.path.exists(idx_path):
        try:
            with open(idx_path, "r") as f:
                BORG_INDEX = json.load(f)
        except Exception:
            traceback.print_exc()
            BORG_INDEX = {}
    else:
        BORG_INDEX = {}

def save_borg_index():
    idx_path = CONFIG.get("borg_index", {}).get("path", f"borg_index_{NAME}.json")
    try:
        with open(idx_path, "w") as f:
            json.dump(BORG_INDEX, f, indent=2)
    except Exception:
        traceback.print_exc()

def load_config():
    global CONFIG, CONFIG_MTIME
    with CONFIG_LOCK:
        cfg = DEFAULT_CONFIG.copy()
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    user_cfg = json.load(f)
                cfg.update(user_cfg)
                if "ml" in user_cfg:
                    cfg["ml"].update(user_cfg["ml"])
                    if "per_class" in user_cfg["ml"]:
                        cfg["ml"]["per_class"].update(user_cfg["ml"]["per_class"])
                    if "automl" in user_cfg["ml"]:
                        cfg["ml"]["automl"].update(user_cfg["ml"]["automl"])
            except Exception:
                traceback.print_exc()
        CONFIG = cfg
        ensure_dirs()
        CONFIG_MTIME = os.path.getmtime(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else None
        init_borg()
        load_borg_index()

def config_hot_reload_loop():
    global CONFIG_MTIME
    while True:
        try:
            if os.path.exists(CONFIG_PATH):
                mtime = os.path.getmtime(CONFIG_PATH)
                if CONFIG_MTIME is None or mtime > CONFIG_MTIME:
                    print(f"[{NAME}] Config changed, reloading...")
                    load_config()
            time.sleep(2)
        except Exception:
            traceback.print_exc()
            time.sleep(5)

# ============================================================
#  BORG BRAIN
# ============================================================

def scan_drives():
    global AVAILABLE_DRIVES
    AVAILABLE_DRIVES = []
    try:
        parts = psutil.disk_partitions(all=False)
        for p in parts:
            mount = p.mountpoint
            try:
                usage = psutil.disk_usage(mount)
                total = usage.total
                free = usage.free
            except Exception:
                total = 0
                free = 0
            AVAILABLE_DRIVES.append({
                "device": p.device,
                "mountpoint": mount,
                "fstype": p.fstype,
                "opts": p.opts,
                "total": total,
                "free": free
            })
        print(f"[{NAME}] Drives:")
        for d in AVAILABLE_DRIVES:
            print(f"  - {d['device']} -> {d['mountpoint']} ({d['fstype']}) total={d['total']} free={d['free']}")
    except Exception:
        traceback.print_exc()

def init_borg():
    global BORG_PATHS
    BORG_PATHS = []
    scan_drives()
    borg_cfg = CONFIG.get("borg", {})
    if not borg_cfg.get("enabled", False):
        print(f"[{NAME}] Borg disabled.")
        return
    paths = borg_cfg.get("paths", [])
    if not paths:
        print(f"[{NAME}] Borg enabled but no paths.")
        return
    for p in paths:
        try:
            os.makedirs(p, exist_ok=True)
            BORG_PATHS.append(os.path.abspath(p))
        except Exception:
            traceback.print_exc()
    if BORG_PATHS:
        print(f"[{NAME}] Borg active on:")
        for p in BORG_PATHS:
            print(f"  - {p}")

def _borg_paths_for_hash(h: str):
    if not BORG_PATHS:
        return []
    paths = []
    for base in BORG_PATHS:
        base_path = pathlib.Path(base)
        paths.append(base_path / h[0:2] / h[2:4] / h)
    return paths

def borg_put_bytes(data: bytes, tags=None, obj_type="generic") -> str:
    if tags is None:
        tags = []
    h = hashlib.sha256(data).hexdigest()
    paths = _borg_paths_for_hash(h)
    if not paths:
        return h
    try:
        for path in paths:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
        with BORG_INDEX_LOCK:
            BORG_INDEX.setdefault(h, {
                "paths": [str(p) for p in paths],
                "tags": [],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "type": obj_type
            })
            BORG_INDEX[h]["tags"] = list(sorted(set(BORG_INDEX[h]["tags"] + tags)))
            save_borg_index()
    except Exception:
        traceback.print_exc()
    return h

def borg_verify_and_repair(h: str) -> bool:
    with BORG_INDEX_LOCK:
        meta = BORG_INDEX.get(h)
    if not meta:
        return False
    good_data = None
    for p_str in meta.get("paths", []):
        p = pathlib.Path(p_str)
        if p.exists():
            try:
                with open(p, "rb") as f:
                    d = f.read()
                if hashlib.sha256(d).hexdigest() == h:
                    good_data = d
                    break
            except Exception:
                traceback.print_exc()
    if good_data is None:
        return False
    for p_str in meta.get("paths", []):
        p = pathlib.Path(p_str)
        if not p.exists():
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "wb") as f:
                    f.write(good_data)
            except Exception:
                traceback.print_exc()
    return True

def borg_get_bytes(h: str) -> bytes | None:
    with BORG_INDEX_LOCK:
        meta = BORG_INDEX.get(h)
    if not meta:
        paths = _borg_paths_for_hash(h)
        for path in paths:
            if path.exists():
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                    if hashlib.sha256(data).hexdigest() == h:
                        return data
                except Exception:
                    traceback.print_exc()
        return None
    if not borg_verify_and_repair(h):
        return None
    for p_str in meta.get("paths", []):
        p = pathlib.Path(p_str)
        if p.exists():
            try:
                with open(p, "rb") as f:
                    return f.read()
            except Exception:
                traceback.print_exc()
    return None

def borg_list_hashes(limit: int = 1000):
    with BORG_INDEX_LOCK:
        hashes = list(BORG_INDEX.keys())[:limit]
    return hashes

def borg_rebuild_index():
    global BORG_INDEX
    new_index = {}
    if not BORG_PATHS:
        return
    try:
        for base in BORG_PATHS:
            base_path = pathlib.Path(base)
            for root, dirs, files in os.walk(base_path):
                for fn in files:
                    if len(fn) == 64:
                        h = fn
                        p = pathlib.Path(root) / fn
                        try:
                            with open(p, "rb") as f:
                                d = f.read()
                            if hashlib.sha256(d).hexdigest() != h:
                                continue
                        except Exception:
                            continue
                        meta = new_index.setdefault(h, {
                            "paths": [],
                            "tags": [],
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                            "type": "unknown"
                        })
                        if str(p) not in meta["paths"]:
                            meta["paths"].append(str(p))
        with BORG_INDEX_LOCK:
            BORG_INDEX = new_index
            save_borg_index()
    except Exception:
        traceback.print_exc()

load_config()
threading.Thread(target=config_hot_reload_loop, daemon=True).start()

# ============================================================
#  NODE CLASS DETECTION
# ============================================================

def detect_node_class():
    with CONFIG_LOCK:
        cfg_class = CONFIG.get("node_class")
    if cfg_class:
        return cfg_class
    if HAS_GPU or HAS_AMD or HAS_INTEL_GPU:
        return "gpu"
    if len(AVAILABLE_DRIVES) > 2:
        return "storage"
    return "general"

NODE_CLASS = detect_node_class()

# ============================================================
#  NODE STATE
# ============================================================

WORKER_STATE = {
    "cpu": 0,
    "gpu": 0,
    "gpu_mem": 0,
    "gpu_temp": 0,
    "disk": 0,
    "net": 0,
    "purge": "idle",
    "jobs": [],
    "cpu_proc": 0,
    "gpu_proc": 0,
    "vram_proc": 0,
    "gpu_amd": 0,
    "gpu_intel": 0,
    "npu": 0,
    "fusion": {
        "cpu_avg": 0,
        "gpu_avg": 0,
        "trend": "stable"
    },
    "ml": {
        "enabled": False,
        "model_hash": None,
        "anomaly_score": 0.0,
        "last_inference": None,
        "model_type": None,
        "node_class": NODE_CLASS
    }
}

NODE_JOBS = []
API_PORT = None

FUSION_LOCK = threading.Lock()
FUSION_WINDOW = {
    "cpu": [],
    "gpu": [],
    "max_len": 60
}

PROCESS = psutil.Process(os.getpid())

# ============================================================
#  QUEEN STATE + RAFT CONSENSUS
# ============================================================

CLUSTER_STATE = {}
JOBS_QUEUE = []
QUEEN_LOCK = threading.Lock()

QUEENS_STATE = {}
LEADER_NAME = None
LEADER_LOCK = threading.Lock()
LEADER_TIMEOUT = 10

AUTOSCALE_STATE = {
    "desired_nodes": 1,
    "last_decision": None,
    "history": []
}

LOGS_LOCK = threading.Lock()
CLUSTER_LOGS = []

THREAT_LOCK = threading.Lock()
THREAT_MATRIX = {
    "nodes": {},
    "global_score": 0,
    "last_update": None
}

ML_LOCK = threading.Lock()
ML_TRAINING_STATE = {
    "last_train_time": None,
    "status": "idle",
    "per_class": {}
}

CONSENSUS_LOCK = threading.Lock()
CONSENSUS_STATE = {
    "term": 0,
    "voted_for": None,
    "leader_id": None,
    "log": [],
    "commit_index": 0,
    "last_applied": 0,
    "next_index": {},
    "match_index": {},
    "last_heartbeat": None
}

def raft_last_log_index_term():
    log = CONSENSUS_STATE["log"]
    if not log:
        return 0, 0
    last = log[-1]
    return last["index"], last["term"]

def raft_append_local(entry_type, data):
    with CONSENSUS_LOCK:
        last_index, _ = raft_last_log_index_term()
        new_index = last_index + 1
        e = {
            "index": new_index,
            "term": CONSENSUS_STATE["term"],
            "entry": {
                "type": entry_type,
                "data": data
            }
        }
        CONSENSUS_STATE["log"].append(e)
    return new_index

def raft_apply_entry(e):
    etype = e["entry"]["type"]
    data = e["entry"]["data"]
    if etype == "job":
        with QUEEN_LOCK:
            JOBS_QUEUE.append(data)
    elif etype == "autoscale":
        with QUEEN_LOCK:
            AUTOSCALE_STATE.update(data)
    elif etype == "config_ml":
        with CONFIG_LOCK:
            CONFIG.setdefault("ml", {})
            if "per_class" in data:
                CONFIG["ml"].setdefault("per_class", {})
                CONFIG["ml"]["per_class"].update(data["per_class"])
            for k, v in data.items():
                if k != "per_class":
                    CONFIG["ml"][k] = v
        save_config_file()

def raft_apply_committed():
    with CONSENSUS_LOCK:
        while CONSENSUS_STATE["last_applied"] < CONSENSUS_STATE["commit_index"]:
            CONSENSUS_STATE["last_applied"] += 1
            idx = CONSENSUS_STATE["last_applied"]
            for e in CONSENSUS_STATE["log"]:
                if e["index"] == idx:
                    raft_apply_entry(e)
                    break

def raft_mark_heartbeat_from_leader(leader_id, term):
    global LEADER_NAME
    with CONSENSUS_LOCK:
        if term >= CONSENSUS_STATE["term"]:
            CONSENSUS_STATE["term"] = term
            CONSENSUS_STATE["leader_id"] = leader_id
            CONSENSUS_STATE["voted_for"] = None
            CONSENSUS_STATE["last_heartbeat"] = time.time()
            LEADER_NAME = leader_id

def raft_request_vote_handler(msg):
    with CONSENSUS_LOCK:
        term = CONSENSUS_STATE["term"]
        voted_for = CONSENSUS_STATE["voted_for"]
        last_index, last_term = raft_last_log_index_term()

        candidate_term = msg.get("term", 0)
        candidate_id = msg.get("candidate_id")
        cand_last_index = msg.get("last_log_index", 0)
        cand_last_term = msg.get("last_log_term", 0)

        if candidate_term < term:
            return {"term": term, "vote_granted": False}

        if candidate_term > term:
            CONSENSUS_STATE["term"] = candidate_term
            CONSENSUS_STATE["voted_for"] = None
            CONSENSUS_STATE["leader_id"] = None

        up_to_date = (cand_last_term > last_term) or (cand_last_term == last_term and cand_last_index >= last_index)

        if (CONSENSUS_STATE["voted_for"] in (None, candidate_id)) and up_to_date:
            CONSENSUS_STATE["voted_for"] = candidate_id
            return {"term": CONSENSUS_STATE["term"], "vote_granted": True}
        else:
            return {"term": CONSENSUS_STATE["term"], "vote_granted": False}

def raft_append_entries_handler(msg):
    global LEADER_NAME
    with CONSENSUS_LOCK:
        term = CONSENSUS_STATE["term"]
        if msg["term"] < term:
            return {"term": term, "success": False, "match_index": CONSENSUS_STATE["commit_index"]}

        CONSENSUS_STATE["term"] = msg["term"]
        CONSENSUS_STATE["leader_id"] = msg["leader_id"]
        CONSENSUS_STATE["last_heartbeat"] = time.time()
        LEADER_NAME = msg["leader_id"]

        prev_index = msg.get("prev_log_index", 0)
        prev_term = msg.get("prev_log_term", 0)
        entries = msg.get("entries", [])
        leader_commit = msg.get("leader_commit", 0)

        if prev_index > 0:
            if len(CONSENSUS_STATE["log"]) < prev_index:
                return {"term": CONSENSUS_STATE["term"], "success": False, "match_index": len(CONSENSUS_STATE["log"])}
            local_prev = CONSENSUS_STATE["log"][prev_index - 1]
            if local_prev["term"] != prev_term:
                CONSENSUS_STATE["log"] = CONSENSUS_STATE["log"][:prev_index - 1]
                return {"term": CONSENSUS_STATE["term"], "success": False, "match_index": len(CONSENSUS_STATE["log"])}

        for e in entries:
            idx = e["index"]
            if len(CONSENSUS_STATE["log"]) >= idx:
                if CONSENSUS_STATE["log"][idx - 1]["term"] != e["term"]:
                    CONSENSUS_STATE["log"] = CONSENSUS_STATE["log"][:idx - 1]
                    CONSENSUS_STATE["log"].append(e)
            else:
                CONSENSUS_STATE["log"].append(e)

        if leader_commit > CONSENSUS_STATE["commit_index"]:
            CONSENSUS_STATE["commit_index"] = min(leader_commit, len(CONSENSUS_STATE["log"]))

    raft_apply_committed()
    return {"term": CONSENSUS_STATE["term"], "success": True, "match_index": len(CONSENSUS_STATE["log"])}

def raft_broadcast_append_entries():
    with CONSENSUS_LOCK:
        if CONSENSUS_STATE["leader_id"] != NAME:
            return
        term = CONSENSUS_STATE["term"]
        commit_index = CONSENSUS_STATE["commit_index"]
        log = list(CONSENSUS_STATE["log"])
        next_index = dict(CONSENSUS_STATE["next_index"])

    for peer in PEER_QUEENS:
        try:
            host, port_str = peer.split(":")
            port = int(port_str)
            ni = next_index.get(peer, len(log) + 1)
            prev_index = ni - 1
            if prev_index == 0:
                prev_term = 0
            else:
                prev_term = log[prev_index - 1]["term"]
            entries = [e for e in log if e["index"] >= ni]
            payload = {
                "term": term,
                "leader_id": NAME,
                "prev_log_index": prev_index,
                "prev_log_term": prev_term,
                "entries": entries,
                "leader_commit": commit_index
            }
            body = json.dumps(payload).encode()
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("POST", "/consensus/append_entries", body=body,
                         headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            try:
                r = json.loads(data.decode() or "{}")
            except Exception:
                r = {}
            with CONSENSUS_LOCK:
                if not r.get("success", False):
                    CONSENSUS_STATE["next_index"][peer] = max(1, ni - 1)
                else:
                    match_index = r.get("match_index", ni - 1)
                    CONSENSUS_STATE["next_index"][peer] = match_index + 1
                    CONSENSUS_STATE["match_index"][peer] = match_index
        except Exception:
            pass

    with CONSENSUS_LOCK:
        match_indexes = [len(CONSENSUS_STATE["log"])]
        for peer in PEER_QUEENS:
            mi = CONSENSUS_STATE["match_index"].get(peer, 0)
            match_indexes.append(mi)
        match_indexes.sort()
        if match_indexes:
            majority_index = match_indexes[len(match_indexes) // 2]
            if majority_index > CONSENSUS_STATE["commit_index"]:
                if CONSENSUS_STATE["log"][majority_index - 1]["term"] == CONSENSUS_STATE["term"]:
                    CONSENSUS_STATE["commit_index"] = majority_index
    raft_apply_committed()

def consensus_election_loop():
    global LEADER_NAME
    while True:
        time.sleep(1.5)
        now = time.time()
        with CONSENSUS_LOCK:
            last_hb = CONSENSUS_STATE["last_heartbeat"]
            leader_id = CONSENSUS_STATE["leader_id"]
            term = CONSENSUS_STATE["term"]

        timeout = random.uniform(5, 8)
        if last_hb is None or (now - last_hb) > timeout:
            with CONSENSUS_LOCK:
                CONSENSUS_STATE["term"] = term + 1
                CONSENSUS_STATE["voted_for"] = NAME
                CONSENSUS_STATE["leader_id"] = None
                current_term = CONSENSUS_STATE["term"]
                last_index, last_term = raft_last_log_index_term()
            votes = 1
            for peer in PEER_QUEENS:
                try:
                    host, port_str = peer.split(":")
                    port = int(port_str)
                    payload = {
                        "term": current_term,
                        "candidate_id": NAME,
                        "last_log_index": last_index,
                        "last_log_term": last_term
                    }
                    body = json.dumps(payload).encode()
                    conn = http.client.HTTPConnection(host, port, timeout=2)
                    conn.request("POST", "/consensus/request_vote", body=body,
                                 headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
                    resp = conn.getresponse()
                    data = resp.read()
                    conn.close()
                    try:
                        r = json.loads(data.decode() or "{}")
                    except Exception:
                        r = {}
                    if r.get("vote_granted"):
                        votes += 1
                except Exception:
                    pass
            if votes >= (len(PEER_QUEENS) + 1) // 2 + 1:
                with CONSENSUS_LOCK:
                    CONSENSUS_STATE["leader_id"] = NAME
                    CONSENSUS_STATE["last_heartbeat"] = time.time()
                    for peer in PEER_QUEENS:
                        CONSENSUS_STATE["next_index"][peer] = len(CONSENSUS_STATE["log"]) + 1
                        CONSENSUS_STATE["match_index"][peer] = 0
                LEADER_NAME = NAME
                print(f"[QUEEN {NAME}] Elected leader for term {current_term} with {votes} votes.")
            else:
                print(f"[QUEEN {NAME}] Election failed, votes={votes}.")
        else:
            with CONSENSUS_LOCK:
                LEADER_NAME = CONSENSUS_STATE["leader_id"]

def consensus_leader_heartbeat_loop():
    while True:
        time.sleep(1.5)
        with CONSENSUS_LOCK:
            leader_id = CONSENSUS_STATE["leader_id"]
        if leader_id != NAME:
            continue
        raft_broadcast_append_entries()

# ============================================================
#  STRESS / JOBS / PURGE
# ============================================================

def stress_cpu(duration=2):
    end = time.time() + duration
    while time.time() < end:
        _ = 3.14159 * 2.71828 * random.random()

def stress_disk(duration=2):
    path = os.path.join(CONFIG["suction_folder"], f"stress_{int(time.time())}.bin")
    end = time.time() + duration
    with open(path, "wb") as f:
        while time.time() < end:
            f.write(os.urandom(4096))

def stress_net(duration=2):
    end = time.time() + duration
    while time.time() < end:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.2)
            s.connect(("127.0.0.1", 9))
        except Exception:
            pass
        finally:
            s.close()

def execute_stress(job):
    if not CONFIG.get("stress_enabled", True):
        return
    kind = job["payload"].get("kind", "cpu")
    dur = job["payload"].get("duration", 2)
    if kind == "cpu":
        stress_cpu(dur)
    elif kind == "disk":
        stress_disk(dur)
    elif kind == "net":
        stress_net(dur)

def log_event(level, msg, log_type="node"):
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "node": NAME,
        "level": level,
        "msg": msg,
        "type": log_type
    }
    print(f"[{NAME}][{level}] {msg}")
    if ROLE == "node":
        try:
            payload = {
                "type": "log",
                "node": NAME,
                "entry": entry
            }
            node_send_to_queen(payload, expect_response=False)
        except Exception:
            pass

def execute_python_job(job):
    code = job["payload"].get("code", "")
    safe_globals = {
        "__builtins__": {
            "print": print,
            "len": len,
            "range": range,
            "min": min,
            "max": max,
            "sum": sum
        }
    }
    safe_locals = {}
    try:
        with TRACER.start_as_current_span("job_python_exec") if TRACER else dummy_context():
            exec(code, safe_globals, safe_locals)
        log_event("INFO", f"Python job {job['id']} executed successfully", "python")
    except Exception as e:
        log_event("ERROR", f"Python job {job['id']} failed: {e}", "python")

def execute_shell_job(job):
    cmd = job["payload"].get("cmd")
    if not cmd:
        return
    try:
        with TRACER.start_as_current_span("job_shell_exec") if TRACER else dummy_context():
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        log_event("INFO", f"Shell job {job['id']} exit={result.returncode} out={result.stdout[:200]} err={result.stderr[:200]}", "shell")
    except Exception as e:
        log_event("ERROR", f"Shell job {job['id']} failed: {e}", "shell")

def execute_ml_job(job):
    dur = job["payload"].get("duration", 2)
    log_event("INFO", f"ML job {job['id']} starting (simulated, {dur}s)", "ml")
    end = time.time() + dur
    while time.time() < end:
        _ = random.random() * random.random()
    log_event("INFO", f"ML job {job['id']} completed (simulated)", "ml")

def update_threat_matrix_from_policy(policy):
    with THREAT_LOCK:
        node_entry = THREAT_MATRIX["nodes"].setdefault(NAME, {"score": 0, "last": None})
        score = 0
        strictness = int(policy.get("strictness", CONFIG.get("strictness", 1)))
        allow_telemetry = bool(policy.get("allow_telemetry", CONFIG.get("allow_telemetry", True)))
        if not allow_telemetry:
            score += 2
        if strictness >= 2:
            score += 3
        elif strictness == 1:
            score += 1
        node_entry["score"] = score
        node_entry["last"] = datetime.utcnow().isoformat() + "Z"
        THREAT_MATRIX["global_score"] = sum(n["score"] for n in THREAT_MATRIX["nodes"].values())
        THREAT_MATRIX["last_update"] = datetime.utcnow().isoformat() + "Z"

def apply_purge_policy(job):
    policy = job["payload"].get("policy", {})
    with CONFIG_LOCK:
        if "allow_telemetry" in policy:
            CONFIG["allow_telemetry"] = bool(policy["allow_telemetry"])
        if "strictness" in policy:
            CONFIG["strictness"] = int(policy["strictness"])
    save_config_file()
    update_threat_matrix_from_policy(policy)
    log_event("INFO", f"Purge policy applied: {policy}", "purge")

# ============================================================
#  METRICS + ML (Z-SCORE + DEEP + ISO FOREST + TCN + GNN)
# ============================================================

ML_MODEL = None
ML_MODEL_HASH = None
ML_MODEL_TYPE = None

def update_fusion_insights(cpu_val, gpu_val):
    with FUSION_LOCK:
        FUSION_WINDOW["cpu"].append(cpu_val)
        FUSION_WINDOW["gpu"].append(gpu_val)
        if len(FUSION_WINDOW["cpu"]) > FUSION_WINDOW["max_len"]:
            FUSION_WINDOW["cpu"].pop(0)
        if len(FUSION_WINDOW["gpu"]) > FUSION_WINDOW["max_len"]:
            FUSION_WINDOW["gpu"].pop(0)

        cpu_avg = sum(FUSION_WINDOW["cpu"]) / len(FUSION_WINDOW["cpu"]) if FUSION_WINDOW["cpu"] else 0
        gpu_avg = sum(FUSION_WINDOW["gpu"]) / len(FUSION_WINDOW["gpu"]) if FUSION_WINDOW["gpu"] else 0

        trend = "stable"
        if len(FUSION_WINDOW["cpu"]) >= 20:
            first = sum(FUSION_WINDOW["cpu"][:10]) / 10
            last = sum(FUSION_WINDOW["cpu"][-10:]) / 10
            if last > first * 1.1:
                trend = "rising"
            elif last < first * 0.9:
                trend = "falling"

        WORKER_STATE["fusion"] = {
            "cpu_avg": round(cpu_avg, 1),
            "gpu_avg": round(gpu_avg, 1),
            "trend": trend
        }

def get_per_process_gpu_nvml():
    if not HAS_GPU:
        return 0, 0
    try:
        count = pynvml.nvmlDeviceGetCount()
        total_gpu = 0
        total_vram = 0
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
            meminfo = pynvml.nvmlDeviceGetMemoryInfo(handle)
            for p in procs:
                if p.pid == os.getpid():
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    total_gpu += util.gpu
                    if meminfo.total > 0:
                        total_vram += int((p.usedGpuMemory / meminfo.total) * 100)
        return total_gpu, total_vram
    except Exception:
        return 0, 0

def get_amd_gpu_usage():
    if not HAS_AMD:
        return 0
    try:
        result = subprocess.run(
            ["rocm-smi", "--showuse"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1
        )
        if result.returncode != 0:
            return 0
        for line in result.stdout.splitlines():
            if "GPU use (%)" in line:
                parts = line.split()
                for p in parts:
                    if p.strip().isdigit():
                        return int(p.strip())
        return 0
    except Exception:
        return 0

def get_intel_gpu_usage():
    if not HAS_INTEL_GPU:
        return 0
    try:
        result = subprocess.run(
            ["intel_gpu_top", "-J", "-s", "100"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1
        )
        if result.returncode != 0:
            return 0
        for line in result.stdout.splitlines():
            if '"busy"' in line:
                try:
                    val = int(''.join(ch for ch in line if ch.isdigit()))
                    return val
                except Exception:
                    pass
        return 0
    except Exception:
        return 0

def get_npu_usage():
    return 0

# ----------------- Deep architectures -----------------------

if HAS_TORCH:
    class SimpleMLP(nn.Module):
        def __init__(self, input_dim):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, 32),
                nn.ReLU(),
                nn.Linear(32, 16),
                nn.ReLU(),
                nn.Linear(16, 1)
            )

        def forward(self, x):
            return self.net(x)

    class AutoEncoder(nn.Module):
        def __init__(self, input_dim):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Linear(input_dim, 64),
                nn.ReLU(),
                nn.Linear(64, 16),
                nn.ReLU()
            )
            self.decoder = nn.Sequential(
                nn.Linear(16, 64),
                nn.ReLU(),
                nn.Linear(64, input_dim)
            )

        def forward(self, x):
            z = self.encoder(x)
            out = self.decoder(z)
            return out

    class LSTMRegressor(nn.Module):
        def __init__(self, input_dim, hidden_dim=32):
            super().__init__()
            self.lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True)
            self.fc = nn.Linear(hidden_dim, 1)

        def forward(self, x):
            out, _ = self.lstm(x)
            last = out[:, -1, :]
            return self.fc(last)

    class TransformerRegressor(nn.Module):
        def __init__(self, input_dim, d_model=32, nhead=4, num_layers=2):
            super().__init__()
            self.input_proj = nn.Linear(input_dim, d_model)
            encoder_layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead, batch_first=True)
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
            self.fc = nn.Linear(d_model, 1)

        def forward(self, x):
            x = self.input_proj(x)
            out = self.encoder(x)
            last = out[:, -1, :]
            return self.fc(last)

    class TCNBlock(nn.Module):
        def __init__(self, in_ch, out_ch, k=3, d=1):
            super().__init__()
            self.conv = nn.Conv1d(in_ch, out_ch, kernel_size=k, padding=d*(k-1), dilation=d)
            self.relu = nn.ReLU()
            self.bn = nn.BatchNorm1d(out_ch)

        def forward(self, x):
            out = self.conv(x)
            out = self.relu(out)
            out = self.bn(out)
            return out

    class TCNRegressor(nn.Module):
        def __init__(self, input_dim, hidden=32):
            super().__init__()
            self.block1 = TCNBlock(input_dim, hidden, k=3, d=1)
            self.block2 = TCNBlock(hidden, hidden, k=3, d=2)
            self.fc = nn.Linear(hidden, 1)

        def forward(self, x):
            # x: (B, T, F) -> (B, F, T)
            x = x.transpose(1, 2)
            out = self.block1(x)
            out = self.block2(out)
            out = out[:, :, -1]
            return self.fc(out)

    class SimpleGNN(nn.Module):
        def __init__(self, input_dim, hidden=32):
            super().__init__()
            self.fc1 = nn.Linear(input_dim, hidden)
            self.fc2 = nn.Linear(hidden, hidden)
            self.out = nn.Linear(hidden, 1)

        def forward(self, x):
            # x: (B, F) treat as fully connected graph of features
            h = torch.relu(self.fc1(x))
            # simple "graph" mixing: average features then add
            mean = h.mean(dim=1, keepdim=True)
            h = h + mean
            h = torch.relu(self.fc2(h))
            return self.out(h)

# ----------------- Isolation Forest helpers -----------------

def iso_forest_build_trees(rows, n_trees=50, subsample_size=64, min_size=2):
    if not rows:
        return []

    n = len(rows)
    subsample_size = min(subsample_size, n)

    def build_tree(data, depth=0):
        if len(data) <= min_size:
            return {"leaf": True, "size": len(data), "depth": depth}
        n_features = len(data[0])
        feat = random.randint(0, n_features - 1)
        col_vals = [r[feat] for r in data]
        min_v, max_v = min(col_vals), max(col_vals)
        if min_v == max_v:
            return {"leaf": True, "size": len(data), "depth": depth}
        thr = random.uniform(min_v, max_v)
        left = [r for r in data if r[feat] < thr]
        right = [r for r in data if r[feat] >= thr]
        if not left or not right:
            return {"leaf": True, "size": len(data), "depth": depth}
        return {
            "leaf": False,
            "feat": feat,
            "thr": thr,
            "left": build_tree(left, depth + 1),
            "right": build_tree(right, depth + 1)
        }

    trees = []
    for _ in range(n_trees):
        subset = random.sample(rows, subsample_size)
        trees.append(build_tree(subset, 0))
    return trees

def iso_forest_path_length(tree, x):
    if tree.get("leaf", False):
        return tree.get("depth", 0)
    feat = tree["feat"]
    thr = tree["thr"]
    if x[feat] < thr:
        return iso_forest_path_length(tree["left"], x)
    else:
        return iso_forest_path_length(tree["right"], x)

def iso_forest_c_factor(n):
    if n <= 1:
        return 1.0
    import math
    return 2.0 * (math.log(n - 1) + 0.5772156649) - 2.0 * (n - 1) / n

# ----------------- ML model loading & inference --------------

def ml_get_class_config():
    ml_cfg = CONFIG.get("ml", {})
    per_class = ml_cfg.get("per_class", {})
    default_type = ml_cfg.get("default_model_type", "zscore")
    cls_cfg = per_class.get(NODE_CLASS, {})
    model_type = cls_cfg.get("model_type", default_type)
    model_hash = cls_cfg.get("model_hash")
    return model_type, model_hash

def ml_load_model_if_needed():
    global ML_MODEL, ML_MODEL_HASH, ML_MODEL_TYPE
    ml_cfg = CONFIG.get("ml", {})
    model_type, model_hash = ml_get_class_config()
    if not model_hash:
        return
    if ML_MODEL_HASH == model_hash and ML_MODEL is not None and ML_MODEL_TYPE == model_type:
        return
    data = borg_get_bytes(model_hash)
    if data is None:
        print(f"[{NAME}] ML model hash {model_hash} not found in BORG.")
        return
    try:
        obj = json.loads(data.decode())
        mtype = obj.get("type")
        if model_type == "zscore" and mtype == "zscore":
            ML_MODEL = obj
            ML_MODEL_HASH = model_hash
            ML_MODEL_TYPE = "zscore"
        elif model_type == "iso_forest" and mtype == "iso_forest":
            ML_MODEL = obj
            ML_MODEL_HASH = model_hash
            ML_MODEL_TYPE = "iso_forest"
        elif model_type in ("mlp", "autoencoder", "lstm", "transformer", "tcn", "gnn") and mtype == "deep" and HAS_TORCH:
            arch = obj["arch"]
            keys = obj["keys"]
            weights = obj["weights"]
            if arch == "mlp":
                net = SimpleMLP(len(keys))
            elif arch == "autoencoder":
                net = AutoEncoder(len(keys))
            elif arch == "lstm":
                net = LSTMRegressor(len(keys))
            elif arch == "transformer":
                net = TransformerRegressor(len(keys))
            elif arch == "tcn":
                net = TCNRegressor(len(keys))
            elif arch == "gnn":
                net = SimpleGNN(len(keys))
            else:
                print(f"[{NAME}] Unknown deep arch {arch}")
                return
            with torch.no_grad():
                state = net.state_dict()
                for name, param in state.items():
                    if name in weights:
                        param.copy_(torch.tensor(weights[name]))
            ML_MODEL = {"type": "deep", "arch": arch, "keys": keys, "net": net}
            ML_MODEL_HASH = model_hash
            ML_MODEL_TYPE = model_type
        else:
            print(f"[{NAME}] ML model type mismatch: cfg={model_type}, stored={mtype}")
            return
        WORKER_STATE["ml"]["enabled"] = True
        WORKER_STATE["ml"]["model_hash"] = model_hash
        WORKER_STATE["ml"]["model_type"] = ML_MODEL_TYPE
        WORKER_STATE["ml"]["node_class"] = NODE_CLASS
    except Exception:
        traceback.print_exc()
        ML_MODEL = None
        ML_MODEL_HASH = None
        ML_MODEL_TYPE = None
        WORKER_STATE["ml"]["enabled"] = False

def ml_run_inference():
    if ML_MODEL is None:
        return
    try:
        if ML_MODEL_TYPE == "zscore":
            keys = ML_MODEL.get("keys", [])
            means = ML_MODEL.get("means", [])
            stds = ML_MODEL.get("stds", [])
            if not keys or not means or not stds:
                return
            vals = [float(WORKER_STATE.get(k, 0)) for k in keys]
            zscores = []
            for v, m, s in zip(vals, means, stds):
                if s <= 1e-6:
                    z = 0.0
                else:
                    z = (v - m) / s
                zscores.append(abs(z))
            score = sum(zscores) / len(zscores) if zscores else 0.0

        elif ML_MODEL_TYPE == "iso_forest":
            keys = ML_MODEL["keys"]
            trees = ML_MODEL["trees"]
            n = ML_MODEL["n_samples"]
            c_n = iso_forest_c_factor(n)
            x = [float(WORKER_STATE.get(k, 0)) for k in keys]
            path_lengths = []
            for t in trees:
                path_lengths.append(iso_forest_path_length(t, x))
            if not path_lengths:
                return
            import math
            avg_h = sum(path_lengths) / len(path_lengths)
            score = math.exp(-avg_h / c_n)

        elif ML_MODEL_TYPE in ("mlp", "autoencoder", "lstm", "transformer", "tcn", "gnn") and HAS_TORCH:
            arch = ML_MODEL["arch"]
            keys = ML_MODEL["keys"]
            net = ML_MODEL["net"]
            vals = [float(WORKER_STATE.get(k, 0)) for k in keys]
            if arch == "autoencoder":
                x = torch.tensor([vals], dtype=torch.float32)
                with torch.no_grad():
                    out = net(x)
                    mse = ((out - x) ** 2).mean().item()
                score = float(abs(mse))
            elif arch in ("lstm", "transformer", "tcn"):
                x = torch.tensor([[vals]], dtype=torch.float32)
                with torch.no_grad():
                    out = net(x).item()
                score = float(abs(out))
            elif arch in ("mlp", "gnn"):
                x = torch.tensor([vals], dtype=torch.float32)
                with torch.no_grad():
                    out = net(x).item()
                score = float(abs(out))
            else:
                return
        else:
            return

        WORKER_STATE["ml"]["anomaly_score"] = float(score)
        WORKER_STATE["ml"]["last_inference"] = datetime.utcnow().isoformat() + "Z"
        with THREAT_LOCK:
            node_entry = THREAT_MATRIX["nodes"].setdefault(NAME, {"score": 0, "last": None})
            node_entry["score"] += score
            node_entry["last"] = datetime.utcnow().isoformat() + "Z"
            THREAT_MATRIX["global_score"] = sum(n["score"] for n in THREAT_MATRIX["nodes"].values())
            THREAT_MATRIX["last_update"] = datetime.utcnow().isoformat() + "Z"
    except Exception:
        traceback.print_exc()

# ============================================================
#  NODE WORKERS
# ============================================================

def cpu_worker():
    while True:
        cpu_val = psutil.cpu_percent(interval=1)
        WORKER_STATE["cpu"] = cpu_val
        try:
            WORKER_STATE["cpu_proc"] = PROCESS.cpu_percent(interval=None)
        except Exception:
            WORKER_STATE["cpu_proc"] = 0
        update_fusion_insights(cpu_val, WORKER_STATE.get("gpu", 0))
        ml_load_model_if_needed()
        ml_run_inference()

def gpu_worker():
    while True:
        gpu_val = 0
        gpu_mem = 0
        gpu_temp = 0

        if HAS_GPU:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                gpu_val = util.gpu
                if mem.total > 0:
                    gpu_mem = int((mem.used / mem.total) * 100)
                gpu_temp = temp
            except Exception:
                pass

        amd_val = get_amd_gpu_usage()
        if amd_val:
            WORKER_STATE["gpu_amd"] = amd_val

        intel_val = get_intel_gpu_usage()
        if intel_val:
            WORKER_STATE["gpu_intel"] = intel_val

        npu_val = get_npu_usage()
        WORKER_STATE["npu"] = npu_val

        WORKER_STATE["gpu"] = gpu_val
        WORKER_STATE["gpu_mem"] = gpu_mem
        WORKER_STATE["gpu_temp"] = gpu_temp

        gpu_proc, vram_proc = get_per_process_gpu_nvml()
        WORKER_STATE["gpu_proc"] = gpu_proc
        WORKER_STATE["vram_proc"] = vram_proc

        update_fusion_insights(WORKER_STATE.get("cpu", 0), gpu_val)

        time.sleep(1)

def disk_worker():
    while True:
        try:
            usage = psutil.disk_usage('/')
            WORKER_STATE["disk"] = usage.percent
        except Exception:
            WORKER_STATE["disk"] = 0
        time.sleep(1)

def net_worker():
    old = psutil.net_io_counters()
    while True:
        time.sleep(1)
        try:
            new = psutil.net_io_counters()
            sent = new.bytes_sent - old.bytes_sent
            recv = new.bytes_recv - old.bytes_recv
            WORKER_STATE["net"] = sent + recv
            old = new
        except Exception:
            WORKER_STATE["net"] = 0

def purge_worker():
    while True:
        WORKER_STATE["purge"] = "scanning"
        time.sleep(3)
        WORKER_STATE["purge"] = "idle"
        time.sleep(2)

def start_node_workers():
    for w in [cpu_worker, gpu_worker, disk_worker, net_worker, purge_worker]:
        threading.Thread(target=w, daemon=True).start()

# ============================================================
#  PERSISTENT STORAGE
# ============================================================

def save_state(obj):
    storage = CONFIG.get("storage", {})
    if storage.get("type") == "json":
        path = storage.get("path", f"state_{NAME}.json")
        try:
            with open(path, "w") as f:
                json.dump(obj, f, indent=2)
        except Exception:
            traceback.print_exc()

def load_state():
    storage = CONFIG.get("storage", {})
    if storage.get("type") == "json":
        path = storage.get("path", f"state_{NAME}.json")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                traceback.print_exc()
    return None

# ============================================================
#  SNAPSHOT / BACKUP / BORG SNAPSHOT
# ============================================================

def create_snapshot():
    filename = os.path.join(CONFIG["snapshot_folder"], f"snapshot_{int(time.time())}.json")
    with open(filename, "w") as f:
        json.dump(WORKER_STATE, f, indent=2)
    return filename

def create_backup():
    filename = os.path.join(CONFIG["backup_folder"], f"backup_{int(time.time())}.json")
    with open(filename, "w") as f:
        json.dump(WORKER_STATE, f, indent=2)
    return filename

def create_borg_snapshot():
    data = json.dumps(WORKER_STATE, indent=2).encode()
    h = borg_put_bytes(data, tags=["snapshot"], obj_type="snapshot")
    return h

def create_borg_backup():
    data = json.dumps(WORKER_STATE, indent=2).encode()
    h = borg_put_bytes(data, tags=["backup"], obj_type="backup")
    return h

# ============================================================
#  QUEEN TCP
# ============================================================

def queen_handle_client(conn, addr):
    global JOBS_QUEUE
    try:
        data = conn.recv(8192).decode()
        if not data:
            return
        msg = json.loads(data)
        msg_type = msg.get("type")

        if msg_type == "register":
            node = msg.get("node")
            with QUEEN_LOCK:
                CLUSTER_STATE.setdefault(node, {})
                CLUSTER_STATE[node]["registered_at"] = datetime.utcnow().isoformat() + "Z"
                CLUSTER_STATE[node]["info"] = msg
            print(f"[QUEEN {NAME}] Node registered: {node} from {addr}")

        elif msg_type == "heartbeat":
            node = msg.get("node")
            msg["last_seen"] = datetime.utcnow().isoformat() + "Z"
            with QUEEN_LOCK:
                CLUSTER_STATE.setdefault(node, {})
                CLUSTER_STATE[node].update(msg)

        elif msg_type == "pull_job":
            node = msg.get("node")
            with LEADER_LOCK:
                leader = LEADER_NAME
            if leader is not None and leader != NAME:
                resp = {"type": "no_job", "reason": "not_leader", "leader": leader}
            else:
                with QUEEN_LOCK:
                    if JOBS_QUEUE:
                        job = JOBS_QUEUE.pop(0)
                        job["assigned_to"] = node
                        job["assigned_at"] = datetime.utcnow().isoformat() + "Z"
                        resp = {"type": "job", "job": job}
                        print(f"[QUEEN {NAME}] Assigned job {job['id']} to {node}")
                    else:
                        resp = {"type": "no_job", "reason": "empty"}
            conn.sendall(json.dumps(resp).encode())

        elif msg_type == "log":
            entry = msg.get("entry")
            if entry:
                with LOGS_LOCK:
                    CLUSTER_LOGS.append(entry)
                    if len(CLUSTER_LOGS) > 5000:
                        CLUSTER_LOGS = CLUSTER_LOGS[-5000:]

    except Exception:
        traceback.print_exc()
    finally:
        conn.close()

def queen_tcp_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", QUEEN_TCP_PORT))
    s.listen(50)
    print(f"[QUEEN {NAME}] TCP listening on 0.0.0.0:{QUEEN_TCP_PORT}")

    while True:
        conn, addr = s.accept()
        threading.Thread(target=queen_handle_client, args=(conn, addr), daemon=True).start()

# ============================================================
#  QUEEN JOB GENERATOR / AUTOSCALE / ML TRAINING (AUTOML + GPU)
# ============================================================

def queen_job_generator():
    job_id = 1
    while True:
        with LEADER_LOCK:
            leader = LEADER_NAME
        if leader == NAME:
            if random.random() < 0.1:
                job = {
                    "id": job_id,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "payload": {
                        "type": "stress",
                        "kind": "cpu",
                        "duration": random.randint(1, 3)
                    }
                }
                idx = raft_append_local("job", job)
                print(f"[QUEEN {NAME}] Appended job {job_id} at log index {idx}")
                job_id += 1
        time.sleep(3)

def queen_autoscale_loop():
    while True:
        with QUEEN_LOCK:
            nodes = list(CLUSTER_STATE.values())
        autoscale_cfg = CONFIG.get("autoscale", {})
        if not autoscale_cfg.get("enabled", True):
            time.sleep(5)
            continue

        if nodes:
            avg_cpu = sum(n.get("workers", {}).get("cpu", 0) for n in nodes) / len(nodes)
        else:
            avg_cpu = 0

        with QUEEN_LOCK:
            AUTOSCALE_STATE["history"].append({"ts": time.time(), "cpu": avg_cpu})
            if len(AUTOSCALE_STATE["history"]) > autoscale_cfg.get("history_len", 60):
                AUTOSCALE_STATE["history"] = AUTOSCALE_STATE["history"][-autoscale_cfg.get("history_len", 60):]

        desired = AUTOSCALE_STATE.get("desired_nodes", 1)
        cpu_high = autoscale_cfg.get("cpu_high", 75)
        cpu_low = autoscale_cfg.get("cpu_low", 25)
        min_nodes = autoscale_cfg.get("min_nodes", 1)
        max_nodes = autoscale_cfg.get("max_nodes", 10)
        predictive = autoscale_cfg.get("predictive", True)

        predicted = avg_cpu
        if predictive and len(AUTOSCALE_STATE["history"]) >= 5:
            xs = [i for i in range(len(AUTOSCALE_STATE["history"]))]
            ys = [p["cpu"] for p in AUTOSCALE_STATE["history"]]
            n = len(xs)
            sx = sum(xs)
            sy = sum(ys)
            sxx = sum(x * x for x in xs)
            sxy = sum(x * y for x, y in zip(xs, ys))
            denom = n * sxx - sx * sx
            if denom != 0:
                a = (n * sxy - sx * sy) / denom
                b = (sy - a * sx) / n
                predicted = a * (n + 5) + b

        decision = None
        if predicted > cpu_high and desired < max_nodes:
            desired += 1
            decision = f"predictive_scale_up at {predicted:.1f}%"
        elif predicted < cpu_low and desired > min_nodes:
            desired -= 1
            decision = f"predictive_scale_down at {predicted:.1f}%"

        if decision:
            data = {
                "desired_nodes": desired,
                "last_decision": decision,
                "history": AUTOSCALE_STATE["history"]
            }
            raft_append_local("autoscale", data)

        time.sleep(5)

def queen_build_ml_dataset_for_class(node_class):
    samples = []
    with QUEEN_LOCK:
        for node_name, node_state in CLUSTER_STATE.items():
            if node_state.get("node_class") == node_class:
                workers = node_state.get("workers", {})
                samples.append(workers)
    ml_cfg = CONFIG.get("ml", {})
    keys = ml_cfg.get("input_keys", [])
    rows = []
    for s in samples:
        row = []
        for k in keys:
            row.append(float(s.get(k, 0)))
        rows.append(row)
    if not rows:
        return None
    dataset = {
        "type": "ml_dataset",
        "keys": keys,
        "rows": rows,
        "node_class": node_class,
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    data = json.dumps(dataset, indent=2).encode()
    h = borg_put_bytes(data, tags=["ml_dataset", f"class_{node_class}"], obj_type="ml_dataset")
    return h

def _train_zscore_model(keys, rows, dataset_hash, node_class):
    cols = len(keys)
    sums = [0.0] * cols
    sums2 = [0.0] * cols
    n = len(rows)
    for r in rows:
        for i, v in enumerate(r):
            sums[i] += v
            sums2[i] += v * v
    means = [s / n for s in sums]
    stds = []
    for i in range(cols):
        mean = means[i]
        var = (sums2[i] / n) - (mean * mean)
        if var < 0:
            var = 0.0
        std = var ** 0.5
        if std < 1e-6:
            std = 1e-6
        stds.append(std)
    model = {
        "type": "zscore",
        "keys": keys,
        "means": means,
        "stds": stds,
        "trained_at": datetime.utcnow().isoformat() + "Z",
        "dataset_hash": dataset_hash,
        "node_class": node_class
    }
    return model, "zscore"

def _train_iso_forest_model(keys, rows, dataset_hash, node_class):
    trees = iso_forest_build_trees(rows, n_trees=50, subsample_size=64, min_size=2)
    model = {
        "type": "iso_forest",
        "keys": keys,
        "trees": trees,
        "n_samples": len(rows),
        "trained_at": datetime.utcnow().isoformat() + "Z",
        "dataset_hash": dataset_hash,
        "node_class": node_class
    }
    return model, "iso_forest"

def _train_deep_model(arch, keys, rows, dataset_hash, node_class, max_epochs=40):
    if not HAS_TORCH:
        return None, None
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    x = torch.tensor(rows, dtype=torch.float32, device=device)
    if arch == "mlp":
        net = SimpleMLP(len(keys)).to(device)
        target = torch.zeros((len(rows), 1), dtype=torch.float32, device=device)
        loss_fn = nn.MSELoss()
        def forward_fn(xb):
            return net(xb)
        def target_fn(xb):
            return target
    elif arch == "autoencoder":
        net = AutoEncoder(len(keys)).to(device)
        loss_fn = nn.MSELoss()
        def forward_fn(xb):
            return net(xb)
        def target_fn(xb):
            return xb
    elif arch == "lstm":
        net = LSTMRegressor(len(keys)).to(device)
        x = x.unsqueeze(1)
        target = torch.zeros((len(rows), 1), dtype=torch.float32, device=device)
        loss_fn = nn.MSELoss()
        def forward_fn(xb):
            return net(xb)
        def target_fn(xb):
            return target
    elif arch == "transformer":
        net = TransformerRegressor(len(keys)).to(device)
        x = x.unsqueeze(1)
        target = torch.zeros((len(rows), 1), dtype=torch.float32, device=device)
        loss_fn = nn.MSELoss()
        def forward_fn(xb):
            return net(xb)
        def target_fn(xb):
            return target
    elif arch == "tcn":
        net = TCNRegressor(len(keys)).to(device)
        x = x.unsqueeze(1)
        target = torch.zeros((len(rows), 1), dtype=torch.float32, device=device)
        loss_fn = nn.MSELoss()
        def forward_fn(xb):
            return net(xb)
        def target_fn(xb):
            return target
    elif arch == "gnn":
        net = SimpleGNN(len(keys)).to(device)
        target = torch.zeros((len(rows), 1), dtype=torch.float32, device=device)
        loss_fn = nn.MSELoss()
        def forward_fn(xb):
            return net(xb)
        def target_fn(xb):
            return target
    else:
        return None, None

    opt = optim.Adam(net.parameters(), lr=1e-3)
    for _ in range(max_epochs):
        opt.zero_grad()
        out = forward_fn(x)
        tgt = target_fn(x)
        loss = loss_fn(out, tgt)
        loss.backward()
        opt.step()

    weights = {}
    for name, param in net.state_dict().items():
        weights[name] = param.detach().cpu().tolist()
    model = {
        "type": "deep",
        "arch": arch,
        "keys": keys,
        "weights": weights,
        "trained_at": datetime.utcnow().isoformat() + "Z",
        "dataset_hash": dataset_hash,
        "node_class": node_class
    }
    return model, arch

def _evaluate_model_on_dataset(model_obj, model_type, keys, rows):
    # simple evaluation: average anomaly score on training data
    try:
        if model_type == "zscore":
            means = model_obj["means"]
            stds = model_obj["stds"]
            scores = []
            for r in rows:
                zscores = []
                for v, m, s in zip(r, means, stds):
                    if s <= 1e-6:
                        z = 0.0
                    else:
                        z = (v - m) / s
                    zscores.append(abs(z))
                scores.append(sum(zscores) / len(zscores))
            return sum(scores) / len(scores)
        elif model_type == "iso_forest":
            trees = model_obj["trees"]
            n = model_obj["n_samples"]
            c_n = iso_forest_c_factor(n)
            import math
            scores = []
            for r in rows:
                path_lengths = []
                for t in trees:
                    path_lengths.append(iso_forest_path_length(t, r))
                if not path_lengths:
                    continue
                avg_h = sum(path_lengths) / len(path_lengths)
                scores.append(math.exp(-avg_h / c_n))
            return sum(scores) / len(scores) if scores else 0.0
        elif model_type in ("mlp", "autoencoder", "lstm", "transformer", "tcn", "gnn") and HAS_TORCH:
            arch = model_obj["arch"]
            weights = model_obj["weights"]
            if arch == "mlp":
                net = SimpleMLP(len(keys))
            elif arch == "autoencoder":
                net = AutoEncoder(len(keys))
            elif arch == "lstm":
                net = LSTMRegressor(len(keys))
            elif arch == "transformer":
                net = TransformerRegressor(len(keys))
            elif arch == "tcn":
                net = TCNRegressor(len(keys))
            elif arch == "gnn":
                net = SimpleGNN(len(keys))
            else:
                return 0.0
            with torch.no_grad():
                state = net.state_dict()
                for name, param in state.items():
                    if name in weights:
                        param.copy_(torch.tensor(weights[name]))
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            net = net.to(device)
            x = torch.tensor(rows, dtype=torch.float32, device=device)
            if arch in ("lstm", "transformer", "tcn"):
                x = x.unsqueeze(1)
            scores = []
            with torch.no_grad():
                if arch == "autoencoder":
                    out = net(x)
                    mse = ((out - x) ** 2).mean(dim=1)
                    scores = mse.cpu().tolist()
                else:
                    out = net(x)
                    scores = [abs(v) for v in out.view(-1).cpu().tolist()]
            return sum(scores) / len(scores) if scores else 0.0
        else:
            return 0.0
    except Exception:
        traceback.print_exc()
        return 0.0

@traced_span("queen_train_ml_model_for_class")
def queen_train_ml_model_for_class(node_class):
    global ML_TRAINING_STATE
    with ML_LOCK:
        ML_TRAINING_STATE["status"] = "training"
        ML_TRAINING_STATE.setdefault("per_class", {})
        ML_TRAINING_STATE["per_class"].setdefault(node_class, {})

    try:
        dataset_hash = queen_build_ml_dataset_for_class(node_class)
        if dataset_hash is None:
            with ML_LOCK:
                ML_TRAINING_STATE["per_class"][node_class]["status"] = "no_data"
            print(f"[QUEEN {NAME}] ML training skipped for class {node_class}: no data.")
            return
        data = borg_get_bytes(dataset_hash)
        if data is None:
            with ML_LOCK:
                ML_TRAINING_STATE["per_class"][node_class]["status"] = "dataset_missing"
            print(f"[QUEEN {NAME}] ML training failed for class {node_class}: dataset missing.")
            return
        ds = json.loads(data.decode())
        keys = ds.get("keys", [])
        rows = ds.get("rows", [])
        if not keys or not rows:
            with ML_LOCK:
                ML_TRAINING_STATE["per_class"][node_class]["status"] = "dataset_empty"
            print(f"[QUEEN {NAME}] ML training failed for class {node_class}: dataset empty.")
            return
        ml_cfg = CONFIG.get("ml", {})
        min_samples = ml_cfg.get("training_min_samples", 20)
        if len(rows) < min_samples:
            with ML_LOCK:
                ML_TRAINING_STATE["per_class"][node_class]["status"] = f"not_enough_samples ({len(rows)}/{min_samples})"
            print(f"[QUEEN {NAME}] ML training skipped for class {node_class}: not enough samples.")
            return

        automl_cfg = ml_cfg.get("automl", {})
        automl_enabled = automl_cfg.get("enabled", True)
        candidates = automl_cfg.get("candidates", [])
        max_epochs = automl_cfg.get("max_epochs", 40)

        best_model = None
        best_type = None
        best_score = None

        # Always include zscore as baseline
        z_model, z_type = _train_zscore_model(keys, rows, dataset_hash, node_class)
        z_score = _evaluate_model_on_dataset(z_model, z_type, keys, rows)
        best_model, best_type, best_score = z_model, z_type, z_score

        # Isolation Forest
        if "iso_forest" in candidates:
            iso_model, iso_type = _train_iso_forest_model(keys, rows, dataset_hash, node_class)
            iso_score = _evaluate_model_on_dataset(iso_model, iso_type, keys, rows)
            if iso_score < best_score:
                best_model, best_type, best_score = iso_model, iso_type, iso_score

        # Deep models (GPU-accelerated if available)
        if HAS_TORCH and automl_enabled:
            for arch in candidates:
                if arch in ("mlp", "autoencoder", "lstm", "transformer", "tcn", "gnn"):
                    d_model, d_type = _train_deep_model(arch, keys, rows, dataset_hash, node_class, max_epochs=max_epochs)
                    if d_model is None:
                        continue
                    d_score = _evaluate_model_on_dataset(d_model, d_type, keys, rows)
                    if best_score is None or d_score < best_score:
                        best_model, best_type, best_score = d_model, d_type, d_score

        model_bytes = json.dumps(best_model, indent=2).encode()
        model_hash = borg_put_bytes(model_bytes, tags=["ml_model", f"class_{node_class}"], obj_type="ml_model")

        cfg_update = {
            "per_class": {
                node_class: {
                    "model_type": best_type,
                    "model_hash": model_hash
                }
            }
        }
        raft_append_local("config_ml", cfg_update)

        with ML_LOCK:
            ML_TRAINING_STATE["last_train_time"] = datetime.utcnow().isoformat() + "Z"
            ML_TRAINING_STATE["per_class"][node_class]["last_model_hash"] = model_hash
            ML_TRAINING_STATE["per_class"][node_class]["last_dataset_hash"] = dataset_hash
            ML_TRAINING_STATE["per_class"][node_class]["status"] = f"trained ({best_type}, score={best_score:.4f})"
        print(f"[QUEEN {NAME}] ML model ({best_type}) trained for class {node_class} and stored as {model_hash} (score={best_score:.4f})")
    except Exception:
        traceback.print_exc()
        with ML_LOCK:
            ML_TRAINING_STATE["per_class"][node_class]["status"] = "error"

def queen_train_ml_model_all_classes():
    ml_cfg = CONFIG.get("ml", {})
    per_class = ml_cfg.get("per_class", {})
    classes = list(per_class.keys()) or ["general"]
    for cls in classes:
        queen_train_ml_model_for_class(cls)

# ============================================================
#  QUEEN HTTP API + PROMETHEUS
# ============================================================

class QueenHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/cluster":
            with QUEEN_LOCK:
                data = {
                    "cluster": CLUSTER_STATE,
                    "jobs_queue_len": len(JOBS_QUEUE),
                    "autoscale": AUTOSCALE_STATE,
                    "leader": LEADER_NAME
                }
            self.respond_json(data)
        elif self.path == "/leader":
            self.respond_json({"leader": LEADER_NAME})
        elif self.path.startswith("/borg/get/"):
            h = self.path.split("/borg/get/")[-1]
            data = borg_get_bytes(h)
            if data is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/borg/list":
            self.respond_json({"hashes": borg_list_hashes()})
        elif self.path == "/borg/drives":
            self.respond_json({"drives": AVAILABLE_DRIVES, "paths": BORG_PATHS})
        elif self.path == "/borg/rebuild_index":
            borg_rebuild_index()
            self.respond_json({"status": "rebuilding"})
        elif self.path == "/logs":
            with LOGS_LOCK:
                self.respond_json({"logs": CLUSTER_LOGS[-500:]})
        elif self.path == "/threat":
            with THREAT_LOCK:
                self.respond_json(THREAT_MATRIX)
        elif self.path == "/ml/status":
            with ML_LOCK:
                self.respond_json({
                    "training": ML_TRAINING_STATE,
                    "config_ml": CONFIG.get("ml", {})
                })
        elif self.path == "/consensus/state":
            with CONSENSUS_LOCK:
                self.respond_json(CONSENSUS_STATE)
        elif self.path == "/metrics":
            self.respond_prometheus()
        else:
            self.respond_json({"error": "unknown endpoint"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            msg = json.loads(body.decode() or "{}")
        except Exception:
            msg = {}
        if self.path == "/ml/train":
            threading.Thread(target=queen_train_ml_model_all_classes, daemon=True).start()
            self.respond_json({"status": "started"})
        elif self.path == "/consensus/heartbeat":
            leader_id = msg.get("leader_id")
            term = int(msg.get("term", 0))
            raft_mark_heartbeat_from_leader(leader_id, term)
            self.respond_json({"status": "ok"})
        elif self.path == "/consensus/request_vote":
            resp = raft_request_vote_handler(msg)
            self.respond_json(resp)
        elif self.path == "/consensus/append_entries":
            resp = raft_append_entries_handler(msg)
            self.respond_json(resp)
        else:
            self.respond_json({"error": "unknown endpoint"})

    def log_message(self, format, *args):
        return

    def respond_json(self, obj):
        raw = json.dumps(obj, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(raw))
        self.end_headers()
        self.wfile.write(raw)

    def respond_prometheus(self):
        with QUEEN_LOCK:
            cluster_size = len(CLUSTER_STATE)
            jobs_len = len(JOBS_QUEUE)
            autoscale_desired = AUTOSCALE_STATE.get("desired_nodes", 1)
        with THREAT_LOCK:
            global_threat = THREAT_MATRIX.get("global_score", 0)
        with CONSENSUS_LOCK:
            is_leader = 1 if CONSENSUS_STATE.get("leader_id") == NAME else 0
            term = CONSENSUS_STATE.get("term", 0)
        lines = []
        lines.append("# HELP queen_cluster_nodes Number of nodes in cluster")
        lines.append("# TYPE queen_cluster_nodes gauge")
        lines.append(f"queen_cluster_nodes {cluster_size}")
        lines.append("# HELP queen_jobs_queue_len Number of pending jobs")
        lines.append("# TYPE queen_jobs_queue_len gauge")
        lines.append(f"queen_jobs_queue_len {jobs_len}")
        lines.append("# HELP queen_autoscale_desired Desired node count from autoscaler")
        lines.append("# TYPE queen_autoscale_desired gauge")
        lines.append(f"queen_autoscale_desired {autoscale_desired}")
        lines.append("# HELP queen_threat_global_score Global threat score")
        lines.append("# TYPE queen_threat_global_score gauge")
        lines.append(f"queen_threat_global_score {global_threat}")
        lines.append("# HELP queen_is_leader Whether this queen is leader (1/0)")
        lines.append("# TYPE queen_is_leader gauge")
        lines.append(f"queen_is_leader {is_leader}")
        lines.append("# HELP queen_raft_term Current Raft term")
        lines.append("# TYPE queen_raft_term gauge")
        lines.append(f"queen_raft_term {term}")
        data = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

def queen_api_server():
    with socketserver.TCPServer(("0.0.0.0", QUEEN_API_PORT), QueenHandler) as httpd:
        print(f"[QUEEN {NAME}] API listening on 0.0.0.0:{QUEEN_API_PORT}")
        httpd.serve_forever()

# ============================================================
#  NODE -> QUEEN
# ============================================================

def node_send_to_queen(payload, expect_response=True):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((QUEEN_HOST, QUEEN_TCP_PORT))
        s.sendall(json.dumps(payload).encode())
        if expect_response and payload.get("type") == "pull_job":
            data = s.recv(8192).decode()
            if data:
                s.close()
                return json.loads(data)
        s.close()
    except Exception as e:
        print(f"[{NAME}] Failed to reach Queen: {e}")
    return None

def node_register_with_queen():
    payload = {
        "type": "register",
        "node": NAME,
        "api_port": API_PORT,
        "node_class": NODE_CLASS,
    }
    node_send_to_queen(payload, expect_response=False)

def node_heartbeat_loop():
    while API_PORT is None:
        time.sleep(0.5)

    node_register_with_queen()

    while True:
        payload = {
            "type": "heartbeat",
            "node": NAME,
            "api_port": API_PORT,
            "workers": WORKER_STATE,
            "node_class": NODE_CLASS,
        }
        node_send_to_queen(payload, expect_response=False)
        time.sleep(CONFIG.get("heartbeat_interval", 2))

def node_job_pull_loop():
    while API_PORT is None:
        time.sleep(0.5)

    while True:
        resp = node_send_to_queen({"type": "pull_job", "node": NAME})
        if resp and resp.get("type") == "job":
            job = resp["job"]
            NODE_JOBS.append(job)
            WORKER_STATE["jobs"].append(job["id"])
            print(f"[{NAME}] Executing job {job['id']} payload={job['payload']}")
            payload_type = job["payload"].get("type")
            if payload_type == "stress":
                execute_stress(job)
            elif payload_type == "purge":
                apply_purge_policy(job)
            elif payload_type == "python":
                execute_python_job(job)
            elif payload_type == "shell":
                execute_shell_job(job)
            elif payload_type == "ml":
                execute_ml_job(job)
            else:
                time.sleep(random.randint(1, 3))
        time.sleep(2)

# ============================================================
#  NODE HTTP API + PROMETHEUS
# ============================================================

class NodeHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            self.respond_json({
                "node": NAME,
                "role": ROLE,
                "api_port": API_PORT,
                "node_class": NODE_CLASS,
                "workers": WORKER_STATE,
                "jobs_executed": len(NODE_JOBS),
                "config": {
                    "allow_telemetry": CONFIG.get("allow_telemetry"),
                    "strictness": CONFIG.get("strictness")
                }
            })
        elif self.path == "/snapshot":
            file = create_snapshot()
            self.respond_json({"snapshot": file})
        elif self.path == "/backup":
            file = create_backup()
            self.respond_json({"backup": file})
        elif self.path == "/snapshot_borg":
            h = create_borg_snapshot()
            self.respond_json({"borg_hash": h})
        elif self.path == "/backup_borg":
            h = create_borg_backup()
            self.respond_json({"borg_hash": h})
        elif self.path == "/metrics":
            self.respond_prometheus()
        else:
            self.respond_json({"error": "unknown endpoint"})

    def log_message(self, format, *args):
        return

    def respond_json(self, obj):
        raw = json.dumps(obj, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(raw))
        self.end_headers()
        self.wfile.write(raw)

    def respond_prometheus(self):
        cpu = WORKER_STATE.get("cpu", 0)
        gpu = WORKER_STATE.get("gpu", 0)
        disk = WORKER_STATE.get("disk", 0)
        net = WORKER_STATE.get("net", 0)
        anomaly = WORKER_STATE["ml"].get("anomaly_score", 0.0)
        lines = []
        lines.append("# HELP node_cpu_percent CPU utilization percent")
        lines.append("# TYPE node_cpu_percent gauge")
        lines.append(f"node_cpu_percent{{node=\"{NAME}\",class=\"{NODE_CLASS}\"}} {cpu}")
        lines.append("# HELP node_gpu_percent GPU utilization percent")
        lines.append("# TYPE node_gpu_percent gauge")
        lines.append(f"node_gpu_percent{{node=\"{NAME}\",class=\"{NODE_CLASS}\"}} {gpu}")
        lines.append("# HELP node_disk_percent Disk utilization percent")
        lines.append("# TYPE node_disk_percent gauge")
        lines.append(f"node_disk_percent{{node=\"{NAME}\",class=\"{NODE_CLASS}\"}} {disk}")
        lines.append("# HELP node_net_bytes_per_sec Network bytes per second")
        lines.append("# TYPE node_net_bytes_per_sec gauge")
        lines.append(f"node_net_bytes_per_sec{{node=\"{NAME}\",class=\"{NODE_CLASS}\"}} {net}")
        lines.append("# HELP node_ml_anomaly_score ML anomaly score")
        lines.append("# TYPE node_ml_anomaly_score gauge")
        lines.append(f"node_ml_anomaly_score{{node=\"{NAME}\",class=\"{NODE_CLASS}\"}} {anomaly}")
        data = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

def node_api_server():
    global API_PORT
    API_PORT = get_free_port()
    with socketserver.TCPServer(("0.0.0.0", API_PORT), NodeHandler) as httpd:
        print(f"[{NAME}] API listening on 0.0.0.0:{API_PORT} (class={NODE_CLASS})")
        httpd.serve_forever()

# ============================================================
#  AGENT
# ============================================================

class AgentHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/spawn_node":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                msg = json.loads(body.decode())
                node_name = msg.get("name", f"Node-{int(time.time())}")
                qhost = msg.get("queen_host", "127.0.0.1")
                qport = msg.get("queen_port", 5050)
                cmd = [sys.executable, os.path.abspath(__file__),
                       "--role", "node",
                       "--name", node_name,
                       "--queen-host", qhost,
                       "--queen-port", str(qport)]
                print(f"[AGENT {NAME}] Spawning node {node_name} for Queen {qhost}:{qport}")
                subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
                self.respond_json({"status": "spawned", "name": node_name})
            except Exception as e:
                traceback.print_exc()
                self.respond_json({"status": "error", "error": str(e)})
        else:
            self.respond_json({"error": "unknown endpoint"})

    def do_GET(self):
        if self.path == "/health":
            self.respond_json({"status": "ok", "agent": NAME})
        else:
            self.respond_json({"error": "unknown endpoint"})

    def log_message(self, format, *args):
        return

    def respond_json(self, obj):
        raw = json.dumps(obj, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(raw))
        self.end_headers()
        self.wfile.write(raw)

def agent_server():
    with socketserver.TCPServer(("0.0.0.0", AGENT_PORT), AgentHandler) as httpd:
        print(f"[AGENT {NAME}] Listening on 0.0.0.0:{AGENT_PORT}")
        httpd.serve_forever()

# ============================================================
#  STARTUP VALIDATION
# ============================================================

class dummy_context:
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False

def validate_startup():
    errors = []

    required_functions = [
        "get_free_port",
        "queen_tcp_server",
        "queen_api_server",
        "consensus_election_loop",
        "consensus_leader_heartbeat_loop",
        "queen_job_generator",
        "queen_autoscale_loop",
        "start_node_workers",
        "node_api_server",
        "node_heartbeat_loop",
        "node_job_pull_loop",
        "agent_server",
    ]

    g = globals()
    for fn in required_functions:
        if fn not in g or not callable(g[fn]):
            errors.append(f"Missing required function: {fn}")

    if ROLE == "queen":
        if not isinstance(QUEEN_TCP_PORT, int) or QUEEN_TCP_PORT <= 0:
            errors.append("Invalid QUEEN_TCP_PORT")
        if not isinstance(QUEEN_API_PORT, int) or QUEEN_API_PORT <= 0:
            errors.append("Invalid QUEEN_API_PORT")

    if ROLE == "agent":
        if not isinstance(AGENT_PORT, int) or AGENT_PORT <= 0:
            errors.append("Invalid AGENT_PORT")

    if errors:
        print("\n[STARTUP VALIDATION FAILED]")
        for e in errors:
            print(" -", e)
        print("Aborting startup due to validation errors.\n")
        sys.exit(1)
    else:
        print("[STARTUP VALIDATION] OK")

# ============================================================
#  WINDOWS SERVICE
# ============================================================

if HAS_WIN_SERVICE:
    class HyperSwarmService(win32serviceutil.ServiceFramework):
        _svc_name_ = "HyperSwarmService"
        _svc_display_name_ = "Hyper Swarm Orchestrator"
        _svc_description_ = "Queen/Node/Agent orchestrator for Hyper Swarm cluster."

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self.running = True

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.running = False
            win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, "")
            )
            t = threading.Thread(target=main_entry, daemon=True)
            t.start()
            win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)

# ============================================================
#  MAIN ENTRY
# ============================================================

def main_entry():
    init_tracing(service_name=f"hyper_swarm_{ROLE}_{NAME}")
    validate_startup()

    if ROLE == "queen":
        threading.Thread(target=queen_tcp_server, daemon=True).start()
        threading.Thread(target=queen_api_server, daemon=True).start()
        threading.Thread(target=consensus_election_loop, daemon=True).start()
        threading.Thread(target=consensus_leader_heartbeat_loop, daemon=True).start()
        threading.Thread(target=queen_job_generator, daemon=True).start()
        threading.Thread(target=queen_autoscale_loop, daemon=True).start()
        while True:
            time.sleep(1)

    elif ROLE == "node":
        start_node_workers()
        threading.Thread(target=node_api_server, daemon=True).start()
        threading.Thread(target=node_heartbeat_loop, daemon=True).start()
        threading.Thread(target=node_job_pull_loop, daemon=True).start()
        while True:
            time.sleep(1)

    elif ROLE == "agent":
        agent_server()

if __name__ == "__main__":
    if HAS_WIN_SERVICE and len(sys.argv) > 1 and sys.argv[1] in ("install", "remove", "start", "stop", "restart"):
        win32serviceutil.HandleCommandLine(HyperSwarmService)
    else:
        main_entry()
