import os
import sys
import time
import json
import glob
import queue
import zipfile
import socket
import threading
import traceback
import tkinter as tk
import tkinter.ttk as ttk
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# Optional imports
try:
    import psutil
except Exception:
    psutil = None

try:
    import winreg
except Exception:
    winreg = None

try:
    import ctypes
except Exception:
    ctypes = None

# ============================================================
# CONFIG / PATHS
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_ROOT = os.path.join(BASE_DIR, "storage")
LOG_ROOT = os.path.join(BASE_DIR, "logs")
BACKUP_ROOT = os.path.join(BASE_DIR, "backups")
REPLICA_ROOT = os.path.join(BASE_DIR, "replicas")
CRASH_LOG = os.path.join(BASE_DIR, "crash.log")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SERVICE_REGISTRY_PATH = os.path.join(BASE_DIR, "services.json")
API_KEYS_PATH = os.path.join(BASE_DIR, "api_keys.json")

for p in [STORAGE_ROOT, LOG_ROOT, BACKUP_ROOT, REPLICA_ROOT]:
    os.makedirs(p, exist_ok=True)

DEFAULT_CONFIG = {
    "node_count": 3,
    "api_host": "127.0.0.1",
    "api_port": 8080,
    "backup_interval_sec": 600,
    "stress_default_seconds": 10
}

DEFAULT_API_KEYS = {
    "keys": {
        "demo-admin-key": "admin",
        "demo-view-key": "viewer"
    }
}

ENABLE_ELEVATION = False  # set to True if you really want admin prompt

def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        cfg = DEFAULT_CONFIG.copy()
        cfg.update(data)
        return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()

def load_api_keys():
    if not os.path.exists(API_KEYS_PATH):
        with open(API_KEYS_PATH, "w") as f:
            json.dump(DEFAULT_API_KEYS, f, indent=2)
        return DEFAULT_API_KEYS["keys"]
    try:
        with open(API_KEYS_PATH, "r") as f:
            data = json.load(f)
        return data.get("keys", {})
    except Exception:
        return DEFAULT_API_KEYS["keys"]

CONFIG = load_config()
API_KEYS = load_api_keys()

# ============================================================
# SIMPLE ENCRYPTION STUB (XOR)
# ============================================================

def xor_bytes(data: bytes, key: int = 0x5A) -> bytes:
    return bytes(b ^ key for b in data)

def encrypt_bytes(data: bytes) -> bytes:
    return xor_bytes(data, 0x5A)

def decrypt_bytes(data: bytes) -> bytes:
    return xor_bytes(data, 0x5A)

# ============================================================
# AUTO-ELEVATION (DISABLED BY DEFAULT)
# ============================================================

def ensure_admin():
    if not ENABLE_ELEVATION:
        return
    if ctypes is None:
        return
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False
    if not is_admin:
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, " ".join(sys.argv), None, 1
            )
            sys.exit()
        except Exception as e:
            print(f"[ADMIN] Elevation failed, continuing without admin: {e}")

# ============================================================
# EVENT BUS
# ============================================================

class EventBus:
    def __init__(self):
        self.subscribers = {}
        self.queue = queue.Queue()

    def subscribe(self, topic, callback):
        self.subscribers.setdefault(topic, []).append(callback)

    def publish(self, topic, message):
        self.queue.put((topic, message))

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            topic, msg = self.queue.get()
            for cb in self.subscribers.get(topic, []):
                try:
                    cb(msg)
                except:
                    pass

# ============================================================
# SYNTHETIC DEVICES
# ============================================================

class SyntheticComputeCluster:
    def __init__(self, name, cores):
        self.name = name
        self.cores = cores
        self.load = 0.0
        self.cpu_percent = 0.0

class SyntheticVolume:
    def __init__(self, name, capacity_gb):
        self.name = name
        self.capacity_gb = capacity_gb
        self.used_gb = 0.0
        self.disk_percent = 0.0

class SyntheticNIC:
    def __init__(self, name):
        self.name = name
        self.tx_mbps = 0.0
        self.rx_mbps = 0.0

class SyntheticSensor:
    def __init__(self, name, kind):
        self.name = name
        self.kind = kind
        self.value = None

# ============================================================
# PURGE SHELL (REGISTRY POLICY)
# ============================================================

class PurgeShell:
    def __init__(self, bus):
        self.bus = bus
        self.policy = "Balanced"

    def _log(self, target, result):
        self.bus.publish("purge.action", {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target": target,
            "result": result
        })

    def set_policy(self, policy):
        self.policy = policy
        self.bus.publish("purge.policy", {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "policy": self.policy
        })

    def _set_reg_dword(self, root, path, name, value):
        if winreg is None:
            return False
        try:
            key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def apply_telemetry_policy(self):
        if self.policy == "Strict":
            val = 0
        elif self.policy == "Balanced":
            val = 1
        else:
            val = 2
        ok = self._set_reg_dword(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Policies\Microsoft\Windows\DataCollection",
            "AllowTelemetry",
            val
        )
        self._log("AllowTelemetry", "ok" if ok else "failed")

    def execute_purge(self, target="telemetry"):
        if target == "telemetry":
            self.apply_telemetry_policy()
        else:
            self._log(target, "noop")

# ============================================================
# WORKER BASE
# ============================================================

class WorkerBase(threading.Thread):
    def __init__(self, name, bus, node_id, devices, queen_ref=None):
        super().__init__(daemon=True)
        self.name = name
        self.bus = bus
        self.node_id = node_id
        self.devices = devices
        self.running = True
        self.queen_ref = queen_ref

    def heartbeat(self, extra=None):
        msg = {
            "worker": self.name,
            "node": self.node_id,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "alive"
        }
        if extra:
            msg.update(extra)
        self.bus.publish("worker.status", msg)

# ============================================================
# CONCRETE WORKERS
# ============================================================

class ComputeWorker(WorkerBase):
    def run(self):
        cluster = self.devices.get("compute")
        while self.running:
            load = round((time.time() % 1) * 100, 2)
            if psutil:
                try:
                    cluster.cpu_percent = psutil.cpu_percent(interval=None)
                except Exception:
                    cluster.cpu_percent = 0.0
            if cluster:
                cluster.load = load
            self.heartbeat({"load": load, "cpu_percent": getattr(cluster, "cpu_percent", None)})
            time.sleep(2)

class TelemetryWorker(WorkerBase):
    def run(self):
        sensor = self.devices.get("sensor")
        while self.running:
            data = {}
            if psutil:
                try:
                    data["mem_percent"] = psutil.virtual_memory().percent
                except Exception:
                    data["mem_percent"] = None
                try:
                    du = psutil.disk_usage("/")
                    data["disk_percent"] = du.percent
                except Exception:
                    data["disk_percent"] = None
            if sensor:
                sensor.value = "OK"
            data["telemetry"] = "OK"
            self.heartbeat(data)
            time.sleep(3)

class StorageWorker(WorkerBase):
    def run(self):
        volume = self.devices.get("volume")
        while self.running:
            if volume:
                if psutil:
                    try:
                        du = psutil.disk_usage("/")
                        volume.disk_percent = du.percent
                        volume.used_gb = round(du.used / (1024**3), 2)
                    except Exception:
                        volume.used_gb = round((time.time() % volume.capacity_gb), 2)
                else:
                    volume.used_gb = round((time.time() % volume.capacity_gb), 2)
            self.heartbeat({
                "storage_used": getattr(volume, "used_gb", None),
                "disk_percent": getattr(volume, "disk_percent", None)
            })
            time.sleep(4)

class NetWorker(WorkerBase):
    def run(self):
        nic = self.devices.get("nic")
        last_bytes = None
        while self.running:
            if psutil:
                try:
                    net = psutil.net_io_counters()
                    if last_bytes:
                        tx_delta = net.bytes_sent - last_bytes[0]
                        rx_delta = net.bytes_recv - last_bytes[1]
                        nic.tx_mbps = round((tx_delta * 8) / (1024*1024), 3)
                        nic.rx_mbps = round((rx_delta * 8) / (1024*1024), 3)
                    last_bytes = (net.bytes_sent, net.bytes_recv)
                except Exception:
                    nic.tx_mbps = round((time.time() % 1) * 50, 2)
                    nic.rx_mbps = round((time.time() % 1) * 50, 2)
            else:
                nic.tx_mbps = round((time.time() % 1) * 50, 2)
                nic.rx_mbps = round((time.time() % 1) * 50, 2)
            self.heartbeat({
                "tx": getattr(nic, "tx_mbps", None),
                "rx": getattr(nic, "rx_mbps", None)
            })
            time.sleep(3)

class PurgeWorker(WorkerBase):
    def __init__(self, name, bus, node_id, devices, purge_shell, queen_ref=None):
        super().__init__(name, bus, node_id, devices, queen_ref)
        self.purge_shell = purge_shell

    def run(self):
        while self.running:
            self.heartbeat({"purge_scan": "idle"})
            time.sleep(10)

# ============================================================
# PODS
# ============================================================

class VirtualPod:
    def __init__(self, pod_id, cpu_quota=50.0, mem_quota=1024):
        self.pod_id = pod_id
        self.cpu_quota = cpu_quota
        self.mem_quota = mem_quota

class PodWorker(WorkerBase):
    def __init__(self, name, bus, node_id, devices, queen_ref=None):
        super().__init__(name, bus, node_id, devices, queen_ref)
        self.job_queue = queue.Queue()

    def submit_job(self, job):
        self.job_queue.put(job)

    def run(self):
        while self.running:
            try:
                job = self.job_queue.get(timeout=2)
            except queue.Empty:
                self.heartbeat({"pod_status": "idle"})
                continue
            start = time.time()
            try:
                time.sleep(job.get("duration", 1))
                result = "ok"
            except Exception as e:
                result = f"error: {e}"
            self.heartbeat({
                "pod_job": job.get("id"),
                "result": result,
                "elapsed": round(time.time() - start, 2)
            })

# ============================================================
# LOGGING WORKER
# ============================================================

class LogWorker(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.log_queue = queue.Queue()
        self.log_file = os.path.join(LOG_ROOT, f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

        topics = [
            "worker.status", "backup.event", "stress.event", "scheduler.event",
            "purge.policy", "purge.action", "anomaly.event", "service.register",
            "system.api", "worker.restart"
        ]
        for t in topics:
            bus.subscribe(t, self.on_event)

    def on_event(self, msg):
        self.log_queue.put(msg)

    def run(self):
        while True:
            msg = self.log_queue.get()
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg) + "\n")
            except:
                pass

# ============================================================
# REPLICATION WORKER
# ============================================================

class ReplicationWorker(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus

    def run(self):
        while True:
            try:
                for root, dirs, files in os.walk(STORAGE_ROOT):
                    for f in files:
                        src = os.path.join(root, f)
                        rel = os.path.relpath(src, STORAGE_ROOT)
                        dst = os.path.join(REPLICA_ROOT, rel + ".v1")
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        if not os.path.exists(dst):
                            with open(src, "rb") as s, open(dst, "wb") as d:
                                d.write(s.read())
            except:
                pass
            time.sleep(15)

# ============================================================
# BACKUP WORKER
# ============================================================

class BackupWorker(threading.Thread):
    def __init__(self, bus, interval):
        super().__init__(daemon=True)
        self.bus = bus
        self.interval = interval
        bus.subscribe("backup.trigger", self.on_trigger)

    def on_trigger(self, msg):
        self._do_backup(source=msg.get("source", "manual"), job_id=msg.get("job_id"))

    def _do_backup(self, source="manual", job_id=None):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{ts}.zip"
        backup_path = os.path.join(BACKUP_ROOT, backup_name)
        try:
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(STORAGE_ROOT):
                    for f in files:
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, STORAGE_ROOT)
                        z.write(full, rel)
            self.bus.publish("backup.event", {
                "timestamp": ts,
                "event": "backup_created",
                "backup": backup_name,
                "source": source,
                "job_id": job_id
            })
        except Exception as e:
            self.bus.publish("backup.event", {
                "timestamp": ts,
                "event": "backup_failed",
                "error": str(e),
                "source": source,
                "job_id": job_id
            })

    def run(self):
        while True:
            self._do_backup(source="interval")
            time.sleep(self.interval)

# ============================================================
# STRESS WORKER
# ============================================================

class StressWorker(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        bus.subscribe("stress.trigger", self.on_trigger)

    def on_trigger(self, msg):
        stype = msg.get("stress_type", "cpu")
        seconds = int(msg.get("seconds", CONFIG["stress_default_seconds"]))
        threading.Thread(target=self._run_stress, args=(stype, seconds), daemon=True).start()

    def _run_stress(self, stype, seconds):
        start = time.time()
        self.bus.publish("stress.event", {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": "stress_start",
            "type": stype,
            "seconds": seconds
        })
        try:
            if stype == "cpu":
                end = start + seconds
                while time.time() < end:
                    _ = 3.14159 ** 5.12345
            elif stype == "disk":
                tmp_path = os.path.join(STORAGE_ROOT, "stress.tmp")
                with open(tmp_path, "wb") as f:
                    f.write(os.urandom(1024 * 1024 * 10))
                time.sleep(seconds)
                try:
                    os.remove(tmp_path)
                except:
                    pass
            elif stype == "net":
                time.sleep(seconds)
        finally:
            self.bus.publish("stress.event", {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "event": "stress_end",
                "type": stype,
                "seconds": seconds
            })

    def run(self):
        while True:
            time.sleep(1)

# ============================================================
# ANOMALY WORKER
# ============================================================

class AnomalyWorker(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.window = []
        self.max_window = 50
        bus.subscribe("worker.status", self.on_status)

    def on_status(self, msg):
        metrics = {}
        for k in ("cpu_percent", "disk_percent", "tx", "rx", "mem_percent"):
            if k in msg and isinstance(msg[k], (int, float)):
                metrics[k] = msg[k]
        if metrics:
            self.window.append(metrics)
            if len(self.window) > self.max_window:
                self.window.pop(0)

    def run(self):
        while True:
            if self.window:
                latest = self.window[-1]
                if latest.get("cpu_percent", 0) > 90 or latest.get("disk_percent", 0) > 95:
                    self.bus.publish("anomaly.event", {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "type": "resource_spike",
                        "metrics": latest
                    })
            time.sleep(5)

# ============================================================
# CONFIG WATCHER
# ============================================================

class ConfigWorker(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.last_mtime = os.path.getmtime(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else 0

    def run(self):
        global CONFIG
        while True:
            try:
                if os.path.exists(CONFIG_PATH):
                    mtime = os.path.getmtime(CONFIG_PATH)
                    if mtime != self.last_mtime:
                        self.last_mtime = mtime
                        CONFIG = load_config()
                        self.bus.publish("config.update", {
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "config": CONFIG
                        })
            except:
                pass
            time.sleep(5)

# ============================================================
# SERVICE REGISTRY
# ============================================================

class ServiceRegistry:
    def __init__(self, bus):
        self.bus = bus
        self.services = {}
        self.lock = threading.Lock()
        bus.subscribe("service.register", self.on_register)

    def on_register(self, msg):
        name = msg.get("service")
        node = msg.get("node")
        worker = msg.get("worker")
        if not name or not node or not worker:
            return
        with self.lock:
            self.services.setdefault(name, []).append({
                "node": node,
                "worker": worker,
                "timestamp": msg.get("timestamp")
            })
        try:
            with open(SERVICE_REGISTRY_PATH, "w") as f:
                json.dump(self.services, f, indent=2)
        except:
            pass

    def list_services(self):
        with self.lock:
            return dict(self.services)

# ============================================================
# SCHEDULER
# ============================================================

class SchedulerWorker(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.jobs = []
        self.lock = threading.Lock()

    def submit_job(self, job):
        with self.lock:
            job["id"] = job.get("id") or f"job-{int(time.time()*1000)}"
            job["submitted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.jobs.append(job)
        self.bus.publish("scheduler.event", {
            "timestamp": job["submitted_at"],
            "event": "job_submitted",
            "job": job
        })

    def list_jobs(self):
        with self.lock:
            return list(self.jobs)

    def run(self):
        while True:
            now = time.time()
            with self.lock:
                pending = [j for j in self.jobs if not j.get("done")]
            for job in pending:
                if job.get("run_at") and job["run_at"] > now:
                    continue
                jtype = job.get("type")
                if jtype == "backup":
                    self.bus.publish("backup.trigger", {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "scheduler",
                        "job_id": job["id"]
                    })
                elif jtype == "stress":
                    self.bus.publish("stress.trigger", {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "scheduler",
                        "job_id": job["id"],
                        "stress_type": job.get("stress_type", "cpu"),
                        "seconds": job.get("seconds", CONFIG["stress_default_seconds"])
                    })
                job["done"] = True
                self.bus.publish("scheduler.event", {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "event": "job_executed",
                    "job": job
                })
            time.sleep(5)

# ============================================================
# SWARM NODE & QUEEN
# ============================================================

class SwarmNode:
    def __init__(self, node_id, bus, purge_shell, queen_ref=None):
        self.node_id = node_id
        self.bus = bus
        self.purge_shell = purge_shell
        self.queen_ref = queen_ref
        self.devices = {
            "compute": SyntheticComputeCluster(f"Cluster-{node_id}", cores=4),
            "volume": SyntheticVolume(f"Volume-{node_id}", capacity_gb=128),
            "nic": SyntheticNIC(f"NIC-{node_id}"),
            "sensor": SyntheticSensor(f"Sensor-{node_id}", kind="health")
        }
        self.workers = []
        self.pod_worker = None

    def start(self):
        self.workers = [
            ComputeWorker("ComputeWorker", self.bus, self.node_id, self.devices, self.queen_ref),
            TelemetryWorker("TelemetryWorker", self.bus, self.node_id, self.devices, self.queen_ref),
            StorageWorker("StorageWorker", self.bus, self.node_id, self.devices, self.queen_ref),
            NetWorker("NetWorker", self.bus, self.node_id, self.devices, self.queen_ref),
            PurgeWorker("PurgeWorker", self.bus, self.node_id, self.devices, self.purge_shell, self.queen_ref)
        ]
        self.pod_worker = PodWorker("PodWorker", self.bus, self.node_id, self.devices, self.queen_ref)
        self.workers.append(self.pod_worker)
        for w in self.workers:
            w.start()
        for svc in ("compute", "storage", "network", "telemetry", "pods"):
            self.bus.publish("service.register", {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "service": svc,
                "node": self.node_id,
                "worker": "PodWorker" if svc == "pods" else f"{svc.capitalize()}Worker"
            })

class Queen:
    def __init__(self, bus, node_count=3):
        self.bus = bus
        self.node_count = node_count
        self.nodes = []
        self.purge_shell = PurgeShell(bus)
        self.worker_heartbeats = {}
        self.lock = threading.Lock()
        self.lb_index = 0
        self.pods = []
        self.service_registry = ServiceRegistry(bus)

        bus.subscribe("worker.status", self._on_worker_status)

    def _on_worker_status(self, msg):
        key = (msg["node"], msg["worker"])
        with self.lock:
            self.worker_heartbeats[key] = time.time()

    def start(self):
        self.bus.publish("queen.status", {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "queen_online",
            "nodes": self.node_count
        })
        for i in range(self.node_count):
            node = SwarmNode(f"Node-{i+1}", self.bus, self.purge_shell, queen_ref=self)
            self.nodes.append(node)
            node.start()
        for i in range(5):
            self.pods.append(VirtualPod(f"Pod-{i+1}", cpu_quota=50.0, mem_quota=1024))
        threading.Thread(target=self._self_heal_loop, daemon=True).start()

    def _self_heal_loop(self):
        while True:
            now = time.time()
            with self.lock:
                for node in self.nodes:
                    for w in list(node.workers):
                        key = (node.node_id, w.name)
                        last = self.worker_heartbeats.get(key, now)
                        if now - last > 20:
                            try:
                                w.running = False
                            except:
                                pass
                            try:
                                node.workers.remove(w)
                            except ValueError:
                                pass
                            if isinstance(w, PurgeWorker):
                                new_worker = PurgeWorker(w.name, self.bus, node.node_id, node.devices, node.purge_shell, self)
                            elif isinstance(w, PodWorker):
                                new_worker = PodWorker(w.name, self.bus, node.node_id, node.devices, self)
                                node.pod_worker = new_worker
                            else:
                                new_worker = type(w)(w.name, self.bus, node.node_id, node.devices, self)
                            node.workers.append(new_worker)
                            new_worker.start()
                            self.bus.publish("worker.restart", {
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "node": node.node_id,
                                "worker": w.name
                            })
            time.sleep(5)

    def set_purge_policy(self, policy):
        self.purge_shell.set_policy(policy)

    def execute_purge(self, target="telemetry"):
        self.purge_shell.execute_purge(target)

    def choose_node_for_request(self):
        with self.lock:
            node = self.nodes[self.lb_index % len(self.nodes)]
            self.lb_index += 1
            return node

    def schedule_pod_job(self, job):
        with self.lock:
            pod = self.pods[0] if self.pods else None
        if not pod:
            return
        node = self.choose_node_for_request()
        if node.pod_worker:
            node.pod_worker.submit_job(job)

# ============================================================
# AUTH
# ============================================================

def check_auth(headers):
    key = headers.get("X-API-Key")
    if not key:
        return None
    return API_KEYS.get(key)

# ============================================================
# API SERVER
# ============================================================

class MiniAPI(BaseHTTPRequestHandler):
    queen_ref = None
    bus_ref = None
    scheduler_ref = None

    def _send_json(self, obj, code=200):
        data = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _require_auth(self, admin=False):
        role = check_auth(self.headers)
        if not role:
            self._send_json({"error": "unauthorized"}, 401)
            return None
        if admin and role != "admin":
            self._send_json({"error": "forbidden"}, 403)
            return None
        return role

    def do_GET(self):
        try:
            if self.path == "/status":
                self.handle_status()
            elif self.path == "/backups":
                self.handle_backups()
            elif self.path == "/services":
                self.handle_services()
            elif self.path == "/jobs":
                self.handle_jobs()
            else:
                self._send_json({"error": "not_found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": "exception", "detail": str(e)}, 500)

    def do_POST(self):
        try:
            if self.path == "/backup":
                self.handle_backup_trigger()
            elif self.path == "/stress":
                self.handle_stress()
            elif self.path == "/jobs/submit":
                self.handle_job_submit()
            elif self.path.startswith("/files/upload"):
                self.handle_upload()
            elif self.path.startswith("/backup/restore"):
                self.handle_restore()
            else:
                self._send_json({"error": "not_found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"error": "exception", "detail": str(e)}, 500)

    def handle_status(self):
        role = self._require_auth(admin=False)
        if role is None:
            return
        q = self.queen_ref
        if not q:
            self._send_json({"error": "queen_not_ready"}, 503)
            return
        nodes = []
        for node in q.nodes:
            ninfo = {
                "node_id": node.node_id,
                "compute_load": node.devices["compute"].load,
                "cpu_percent": node.devices["compute"].cpu_percent,
                "disk_used_gb": node.devices["volume"].used_gb,
                "disk_percent": node.devices["volume"].disk_percent,
                "tx_mbps": node.devices["nic"].tx_mbps,
                "rx_mbps": node.devices["nic"].rx_mbps
            }
            nodes.append(ninfo)
        self._send_json({"nodes": nodes, "policy": q.purge_shell.policy})

    def handle_backups(self):
        role = self._require_auth(admin=False)
        if role is None:
            return
        backups = [os.path.basename(p) for p in glob.glob(os.path.join(BACKUP_ROOT, "backup_*.zip"))]
        self._send_json({"backups": backups})

    def handle_services(self):
        role = self._require_auth(admin=False)
        if role is None:
            return
        q = self.queen_ref
        if not q:
            self._send_json({"error": "queen_not_ready"}, 503)
            return
        self._send_json({"services": q.service_registry.list_services()})

    def handle_jobs(self):
        role = self._require_auth(admin=False)
        if role is None:
            return
        if not self.scheduler_ref:
            self._send_json({"error": "scheduler_not_ready"}, 503)
            return
        self._send_json({"jobs": self.scheduler_ref.list_jobs()})

    def handle_backup_trigger(self):
        role = self._require_auth(admin=True)
        if role is None:
            return
        self.bus_ref.publish("backup.trigger", {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "api",
            "job_id": None
        })
        self._send_json({"ok": True})

    def handle_stress(self):
        role = self._require_auth(admin=True)
        if role is None:
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}
        stype = data.get("type", "cpu")
        seconds = int(data.get("seconds", CONFIG["stress_default_seconds"]))
        self.bus_ref.publish("stress.trigger", {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "api",
            "job_id": None,
            "stress_type": stype,
            "seconds": seconds
        })
        self._send_json({"started": True, "type": stype, "seconds": seconds})

    def handle_job_submit(self):
        role = self._require_auth(admin=True)
        if role is None:
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        try:
            job = json.loads(body) if body else {}
        except Exception:
            self._send_json({"error": "invalid_json"}, 400)
            return
        if not job.get("type"):
            self._send_json({"error": "missing_type"}, 400)
            return
        if self.scheduler_ref:
            self.scheduler_ref.submit_job(job)
            self._send_json({"submitted": job})
        else:
            self._send_json({"error": "scheduler_not_ready"}, 503)

    def handle_upload(self):
        role = self._require_auth(admin=True)
        if role is None:
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            self._send_json({"error": "no_body"}, 400)
            return
        enc_flag = "enc=1" in self.path
        data = self.rfile.read(length)
        if enc_flag:
            data = encrypt_bytes(data)
        fname = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + (".enc" if enc_flag else ".bin")
        path = os.path.join(STORAGE_ROOT, fname)
        try:
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:
            self._send_json({"error": "write_failed", "detail": str(e)}, 500)
            return
        self._send_json({"stored_as": fname, "encrypted": enc_flag})

    def handle_restore(self):
        role = self._require_auth(admin=True)
        if role is None:
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}
        name = data.get("name")
        if not name:
            self._send_json({"error": "missing_name"}, 400)
            return
        backup_path = os.path.join(BACKUP_ROOT, name)
        if not os.path.exists(backup_path):
            self._send_json({"error": "backup_not_found"}, 404)
            return
        try:
            with zipfile.ZipFile(backup_path, "r") as z:
                z.extractall(STORAGE_ROOT)
            self._send_json({"restored": name})
        except Exception as e:
            self._send_json({"error": "restore_failed", "detail": str(e)}, 500)

def start_api(bus, queen, scheduler, host, port):
    MiniAPI.bus_ref = bus
    MiniAPI.queen_ref = queen
    MiniAPI.scheduler_ref = scheduler
    try:
        server = HTTPServer((host, port), MiniAPI)
    except OSError as e:
        bus.publish("system.api", {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": f"failed to bind {host}:{port} ({e})"
        })
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bus.publish("system.api", {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": f"listening on {host}:{port}"
    })

# ============================================================
# GUI
# ============================================================

class BorgGUI:
    def __init__(self, bus, queen):
        self.bus = bus
        self.queen = queen

        self.root = tk.Tk()
        self.root.title("Borg Data Center — Full Build")
        self.root.geometry("1200x750")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.overview_frame = tk.Frame(self.notebook, bg="black")
        self.workers_frame = tk.Frame(self.notebook, bg="black")
        self.devices_frame = tk.Frame(self.notebook, bg="black")
        self.purge_frame = tk.Frame(self.notebook, bg="black")
        self.events_frame = tk.Frame(self.notebook, bg="black")
        self.logs_frame = tk.Frame(self.notebook, bg="black")
        self.jobs_frame = tk.Frame(self.notebook, bg="black")
        self.services_frame = tk.Frame(self.notebook, bg="black")

        self.notebook.add(self.overview_frame, text="Overview")
        self.notebook.add(self.workers_frame, text="Workers")
        self.notebook.add(self.devices_frame, text="Devices")
        self.notebook.add(self.purge_frame, text="Purge Shell")
        self.notebook.add(self.events_frame, text="Events")
        self.notebook.add(self.logs_frame, text="Logs")
        self.notebook.add(self.jobs_frame, text="Jobs")
        self.notebook.add(self.services_frame, text="Services")

        self.overview_text = tk.Text(self.overview_frame, bg="black", fg="lime", font=("Consolas", 10))
        self.overview_text.pack(fill="both", expand=True)

        self.workers_text = tk.Text(self.workers_frame, bg="black", fg="cyan", font=("Consolas", 10))
        self.workers_text.pack(fill="both", expand=True)

        self.devices_text = tk.Text(self.devices_frame, bg="black", fg="yellow", font=("Consolas", 10))
        self.devices_text.pack(fill="both", expand=True)

        self.purge_controls = tk.Frame(self.purge_frame, bg="black")
        self.purge_controls.pack(fill="x", side="top")

        self.policy_var = tk.StringVar(value="Balanced")
        tk.Label(self.purge_controls, text="Policy:", bg="black", fg="white").pack(side="left")
        tk.OptionMenu(self.purge_controls, self.policy_var, "Strict", "Balanced", "Custom").pack(side="left")
        tk.Button(self.purge_controls, text="Apply Policy", command=self.apply_policy).pack(side="left", padx=5)
        tk.Button(self.purge_controls, text="Execute Purge", command=self.execute_purge).pack(side="left", padx=5)

        self.purge_text = tk.Text(self.purge_frame, bg="black", fg="magenta", font=("Consolas", 10))
        self.purge_text.pack(fill="both", expand=True)

        self.events_text = tk.Text(self.events_frame, bg="black", fg="white", font=("Consolas", 9))
        self.events_text.pack(fill="both", expand=True)

        self.logs_text = tk.Text(self.logs_frame, bg="black", fg="white", font=("Consolas", 9))
        self.logs_text.pack(fill="both", expand=True)

        self.jobs_text = tk.Text(self.jobs_frame, bg="black", fg="white", font=("Consolas", 9))
        self.jobs_text.pack(fill="both", expand=True)

        self.services_text = tk.Text(self.services_frame, bg="black", fg="white", font=("Consolas", 9))
        self.services_text.pack(fill="both", expand=True)

        bus.subscribe("queen.status", self.on_queen_status)
        bus.subscribe("worker.status", self.on_worker_status)
        bus.subscribe("purge.policy", self.on_purge_policy)
        bus.subscribe("purge.action", self.on_purge_action)
        bus.subscribe("system.api", self.on_api_status)
        bus.subscribe("worker.restart", self.on_worker_restart)
        bus.subscribe("anomaly.event", self.on_anomaly)
        bus.subscribe("backup.event", self.on_backup)
        bus.subscribe("scheduler.event", self.on_scheduler)
        bus.subscribe("stress.event", self.on_stress)
        bus.subscribe("config.update", self.on_config_update)
        bus.subscribe("service.register", self.on_service_register)

        self.root.after(5000, self.refresh_logs_view)

    def apply_policy(self):
        self.queen.set_purge_policy(self.policy_var.get())

    def execute_purge(self):
        self.queen.execute_purge("telemetry")

    def _log_overview(self, line):
        self.overview_text.insert("end", line + "\n")
        self.overview_text.see("end")

    def _log_workers(self, line):
        self.workers_text.insert("end", line + "\n")
        self.workers_text.see("end")

    def _log_devices(self, line):
        self.devices_text.insert("end", line + "\n")
        self.devices_text.see("end")

    def _log_purge(self, line):
        self.purge_text.insert("end", line + "\n")
        self.purge_text.see("end")

    def _log_event(self, line):
        self.events_text.insert("end", line + "\n")
        self.events_text.see("end")

    def _log_jobs(self, line):
        self.jobs_text.insert("end", line + "\n")
        self.jobs_text.see("end")

    def _log_services(self, line):
        self.services_text.insert("end", line + "\n")
        self.services_text.see("end")

    def on_queen_status(self, msg):
        line = f"[{msg['timestamp']}] QUEEN — {msg['status']} (nodes={msg.get('nodes')})"
        self._log_overview(line)
        self._log_event(line)

    def on_worker_status(self, msg):
        line = f"[{msg['timestamp']}] {msg['node']}::{msg['worker']} — {json.dumps(msg)}"
        self._log_workers(line)
        self._log_event(line)
        if any(k in msg for k in ("load", "storage_used", "tx", "rx", "cpu_percent", "disk_percent", "mem_percent")):
            self._log_devices(line)

    def on_purge_policy(self, msg):
        line = f"[{msg['timestamp']}] PURGE POLICY — {msg['policy']}"
        self._log_purge(line)
        self._log_event(line)

    def on_purge_action(self, msg):
        line = f"[{msg['timestamp']}] PURGE ACTION — target={msg['target']} result={msg['result']}"
        self._log_purge(line)
        self._log_event(line)

    def on_api_status(self, msg):
        line = f"[{msg['timestamp']}] API — {msg['status']}"
        self._log_overview(line)
        self._log_event(line)

    def on_worker_restart(self, msg):
        line = f"[{msg['timestamp']}] WORKER RESTART — {msg['node']}::{msg['worker']}"
        self._log_workers(line)
        self._log_event(line)

    def on_anomaly(self, msg):
        line = f"[{msg['timestamp']}] ANOMALY — {msg['type']} {msg.get('metrics')}"
        self._log_overview(line)
        self._log_event(line)

    def on_backup(self, msg):
        line = f"[{msg['timestamp']}] BACKUP — {msg['event']} {msg.get('backup', '')}"
        self._log_overview(line)
        self._log_event(line)

    def on_scheduler(self, msg):
        line = f"[{msg['timestamp']}] SCHEDULER — {msg['event']} {msg.get('job', {}).get('id')}"
        self._log_jobs(line)
        self._log_event(line)

    def on_stress(self, msg):
        line = f"[{msg['timestamp']}] STRESS — {msg['event']} {msg.get('type')} {msg.get('seconds', '')}"
        self._log_overview(line)
        self._log_event(line)

    def on_config_update(self, msg):
        line = f"[{msg['timestamp']}] CONFIG UPDATE — {msg['config']}"
        self._log_overview(line)
        self._log_event(line)

    def on_service_register(self, msg):
        line = f"[{msg['timestamp']}] SERVICE REGISTER — {msg['service']} @ {msg['node']}::{msg['worker']}"
        self._log_services(line)
        self._log_event(line)

    def refresh_logs_view(self):
        try:
            log_files = sorted(glob.glob(os.path.join(LOG_ROOT, "log_*.log")))
            if log_files:
                latest = log_files[-1]
                with open(latest, "r", encoding="utf-8") as f:
                    content = f.read()
                self.logs_text.delete("1.0", "end")
                self.logs_text.insert("end", content)
                self.logs_text.see("end")
        except:
            pass
        self.root.after(5000, self.refresh_logs_view)

    def run(self):
        self.root.mainloop()

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    try:
        print("[BOOT] Starting Borg Data Center (Full Build)...")

        ensure_admin()

        bus = EventBus()
        bus.start()

        queen = Queen(bus, node_count=CONFIG["node_count"])
        queen.start()

        log_worker = LogWorker(bus)
        log_worker.start()

        replication_worker = ReplicationWorker(bus)
        replication_worker.start()

        anomaly_worker = AnomalyWorker(bus)
        anomaly_worker.start()

        backup_worker = BackupWorker(bus, interval=CONFIG["backup_interval_sec"])
        backup_worker.start()

        stress_worker = StressWorker(bus)
        stress_worker.start()

        config_worker = ConfigWorker(bus)
        config_worker.start()

        scheduler_worker = SchedulerWorker(bus)
        scheduler_worker.start()

        start_api(bus, queen, scheduler_worker, host=CONFIG["api_host"], port=CONFIG["api_port"])

        gui = BorgGUI(bus, queen)
        gui.run()

    except Exception:
        with open(CRASH_LOG, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        print(f"\nCRASH — details written to: {CRASH_LOG}")
        input("Press Enter to exit...")
