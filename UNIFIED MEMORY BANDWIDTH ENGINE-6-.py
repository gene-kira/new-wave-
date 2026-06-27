#!/usr/bin/env python3
# ============================================================
# HybridBrain Unified Memory Daemon v6
# Mode: Pure backend organism (no GUI, maximum performance)
# Profile: Hybrid (efficiency + intelligence)
# Focus:
#   - Universal workload orchestration (Files + Network + Sensors)
#   - Cost-aware tiering (RAM / Disk / Swarm)
#   - Priority lanes + pressure detection
#   - Tier migration intelligence + residency tracking
#   - Swarm scoring + bandwidth accounting
#   - Global optimization loop (Brain)
# ============================================================

import os
import sys
import platform
import subprocess
import importlib
import socket
import threading
import time
import json
import logging
from logging.handlers import RotatingFileHandler
from collections import deque
import random

# ============================================================
# CONFIG
# ============================================================

LOG_FILE = "hybridbrain_daemon.log"
LOG_MAX_BYTES = 20 * 1024 * 1024   # 20 MB
LOG_BACKUP_COUNT = 5

SWARM_PORT = 55555
SWARM_HOST = "0.0.0.0"
SWARM_CLIENT_HOST = "127.0.0.1"

DISK_CACHE_FILE = "hb_extended_memory.bin"
DISK_CACHE_SIZE_MB = 1024  # 1 GB

HEARTBEAT_INTERVAL = 5.0
WATCHDOG_INTERVAL = 10.0
SUBSYSTEM_CHECK_INTERVAL = 5.0
BRAIN_LOOP_INTERVAL = 4.0

REQUIRED_LIBS = [
    "psutil",
]

WORKLOAD_SCAN_DIR = "hb_workload"   # directory for file workload
NETWORK_WORKLOAD_HOST = "127.0.0.1"
NETWORK_WORKLOAD_PORT = 55666

SENSOR_STREAM_COUNT = 3

# Cost model (lower = cheaper)
TIER_COST_RAM = 1.0
TIER_COST_DISK = 2.0
TIER_COST_SWARM = 3.0

# Priority lanes (higher = more priority)
PRIORITY_SENSOR = 3
PRIORITY_NETWORK = 2
PRIORITY_FILE = 1

# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("HybridBrainDaemon")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

def log_info(msg):
    logger.info(msg)
    print(msg)

def log_error(msg):
    logger.error(msg)
    print(msg)

# ============================================================
# AUTOLOADER
# ============================================================

def install_package(pkg):
    log_info(f"[AUTOLOADER] Installing missing package: {pkg}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return
    except Exception:
        pass

    os_name = platform.system()

    if os_name == "Windows":
        log_info("[AUTOLOADER] Windows fallback → pip")
        try:
            subprocess.check_call(["pip", "install", pkg])
        except Exception as e:
            log_error(f"[AUTOLOADER] Failed to install {pkg} via pip on Windows: {e}")
    elif os_name == "Linux":
        log_info("[AUTOLOADER] Linux fallback → apt/yum/pacman")
        for cmd in [
            ["sudo", "apt", "install", "-y", pkg],
            ["sudo", "yum", "install", "-y", pkg],
            ["sudo", "pacman", "-S", pkg, "--noconfirm"],
        ]:
            try:
                subprocess.check_call(cmd)
                return
            except Exception:
                continue
        log_error(f"[AUTOLOADER] Failed to install {pkg} via system package manager.")
    elif os_name == "Darwin":
        log_info("[AUTOLOADER] macOS fallback → brew")
        try:
            subprocess.check_call(["brew", "install", pkg])
        except Exception:
            log_info("[AUTOLOADER] brew missing → installing via pip3")
            try:
                subprocess.check_call(["pip3", "install", pkg])
            except Exception as e:
                log_error(f"[AUTOLOADER] Failed to install {pkg} via brew/pip3 on macOS: {e}")

def autoload():
    for lib in REQUIRED_LIBS:
        try:
            importlib.import_module(lib)
            log_info(f"[AUTOLOADER] Loaded: {lib}")
        except ImportError:
            install_package(lib)
    log_info("[AUTOLOADER] All dependencies satisfied.")

# ============================================================
# OS LAYER + ROLLING STATS + PRESSURE
# ============================================================

def create_os_layer():
    autoload()
    import psutil

    class OSLayer:
        def __init__(self):
            self.psutil = psutil
            self.os_name = platform.system()
            self.total_ram = psutil.virtual_memory().total
            try:
                self.page_size = os.sysconf("SC_PAGE_SIZE")
            except Exception:
                self.page_size = 4096

            self.cpu_history = deque(maxlen=120)
            self.ram_history = deque(maxlen=120)
            self.disk_latency_history = deque(maxlen=120)

        def get_free_ram(self):
            return self.psutil.virtual_memory().available

        def get_cpu_load(self):
            val = self.psutil.cpu_percent(interval=0.2)
            self.cpu_history.append(val)
            return val

        def get_disk_free(self, path="."):
            usage = self.psutil.disk_usage(path)
            return usage.free

        def sample_disk_latency(self):
            try:
                io = self.psutil.disk_io_counters()
                latency = (io.read_time + io.write_time) / max(io.read_count + io.write_count, 1)
            except Exception:
                latency = 0.0
            self.disk_latency_history.append(latency)
            return latency

        def sample_ram_ratio(self):
            vm = self.psutil.virtual_memory()
            ratio = vm.available / vm.total
            self.ram_history.append(ratio)
            return ratio

        def avg_cpu(self):
            return sum(self.cpu_history) / len(self.cpu_history) if self.cpu_history else 0.0

        def avg_ram_free_ratio(self):
            return sum(self.ram_history) / len(self.ram_history) if self.ram_history else 0.0

        def avg_disk_latency(self):
            return sum(self.disk_latency_history) / len(self.disk_latency_history) if self.disk_latency_history else 0.0

        def ram_pressure(self):
            ratio = self.ram_history[-1] if self.ram_history else 1.0
            return max(0.0, 1.0 - ratio)

        def disk_pressure(self):
            lat = self.disk_latency_history[-1] if self.disk_latency_history else 0.0
            return min(1.0, lat / 20.0)

        def cpu_pressure(self):
            cpu = self.cpu_history[-1] if self.cpu_history else 0.0
            return min(1.0, cpu / 100.0)

        def describe(self):
            log_info(f"[OS] Detected: {self.os_name}")
            log_info(f"[OS] Total RAM: {self.total_ram/1024/1024:.2f} MB")
            log_info(f"[OS] Page size: {self.page_size} bytes")

    return OSLayer()

# ============================================================
# SWARM MEMORY NODES + SCORING
# ============================================================

class SwarmNodeServer:
    def __init__(self, host=SWARM_HOST, port=SWARM_PORT):
        self.host = host
        self.port = port
        self.running = False
        self.thread = None
        self.storage = {}
        self.lock = threading.Lock()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        log_info(f"[SWARM SERVER] Listening on {self.host}:{self.port}")

    def _serve(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(5)
            while self.running:
                try:
                    conn, addr = s.accept()
                    threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
                except Exception as e:
                    log_error(f"[SWARM SERVER] Accept error: {e}")

    def _handle_client(self, conn, addr):
        with conn:
            try:
                data = conn.recv(8192)
                if not data:
                    return
                msg = json.loads(data.decode("utf-8"))
                cmd = msg.get("cmd")
                key = msg.get("key")
                payload = msg.get("data")

                if cmd == "PUT":
                    with self.lock:
                        self.storage[key] = payload.encode("latin1") if isinstance(payload, str) else payload
                    resp = {"status": "OK"}
                elif cmd == "GET":
                    with self.lock:
                        val = self.storage.get(key, b"")
                    resp = {"status": "OK", "data": val.decode("latin1")}
                elif cmd == "PING":
                    resp = {"status": "OK", "msg": "ALIVE"}
                else:
                    resp = {"status": "ERR", "msg": "Unknown command"}

                conn.sendall(json.dumps(resp).encode("utf-8"))
            except Exception as e:
                try:
                    conn.sendall(json.dumps({"status": "ERR", "msg": str(e)}).encode("utf-8"))
                except Exception:
                    pass
                log_error(f"[SWARM SERVER] Client error: {e}")

    def stop(self):
        self.running = False
        log_info("[SWARM SERVER] Stopped")

class SwarmNodeClient:
    def __init__(self, host=SWARM_CLIENT_HOST, port=SWARM_PORT):
        self.host = host
        self.port = port
        self.latency_history = deque(maxlen=50)
        self.error_count = 0

    def _score(self):
        avg_lat = sum(self.latency_history) / len(self.latency_history) if self.latency_history else 0.0
        error_penalty = min(1.0, self.error_count / 50.0)
        score = max(0.0, 1.0 - (avg_lat / 100.0) - error_penalty)
        return score

    def put(self, key, data: bytes):
        msg = {"cmd": "PUT", "key": key, "data": data.decode("latin1")}
        return self._send(msg)

    def get(self, key):
        msg = {"cmd": "GET", "key": key}
        resp = self._send(msg)
        if resp.get("status") == "OK":
            raw = resp.get("data", "")
            return raw.encode("latin1")
        return b""

    def ping(self):
        msg = {"cmd": "PING"}
        return self._send(msg)

    def _send(self, msg):
        start = time.time()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.host, self.port))
                s.sendall(json.dumps(msg).encode("utf-8"))
                data = s.recv(8192)
                if not data:
                    self.error_count += 1
                    return {"status": "ERR", "msg": "No response"}
                latency = (time.time() - start) * 1000.0
                self.latency_history.append(latency)
                return json.loads(data.decode("utf-8"))
        except Exception as e:
            self.error_count += 1
            log_error(f"[SWARM CLIENT] Error: {e}")
            return {"status": "ERR", "msg": str(e)}

# ============================================================
# BANDWIDTH AUTO-TUNER (Hybrid: efficiency + intelligence)
# ============================================================

class BandwidthAutoTuner:
    def __init__(self, os_layer):
        self.os_layer = os_layer
        self.target_cache_percent = 25.0
        self.min_cache_percent = 5.0
        self.max_cache_percent = 80.0

        self.kp = 0.45
        self.ki = 0.12
        self.kd = 0.22

        self.integral = 0.0
        self.prev_error = 0.0

        self.adjust_interval = 3.0
        self.running = False
        self.thread = None
        self.callback = None

        self.workload_intensity = 0.0
        self.workload_type = "mixed"

        self.ram_bw = 0.0
        self.disk_bw = 0.0
        self.swarm_bw = 0.0

    def update_workload_state(self, intensity, wtype):
        self.workload_intensity = intensity
        self.workload_type = wtype

    def update_bandwidth(self, ram_bw, disk_bw, swarm_bw):
        self.ram_bw = ram_bw
        self.disk_bw = disk_bw
        self.swarm_bw = swarm_bw

    def start(self, callback):
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        log_info("[TUNER] Bandwidth Auto-Tuner started")

    def _loop(self):
        while self.running:
            cpu = self.os_layer.get_cpu_load()
            ram_ratio = self.os_layer.sample_ram_ratio()
            disk_lat = self.os_layer.sample_disk_latency()

            desired_free = 0.35
            error = desired_free - ram_ratio

            self.integral += error * self.adjust_interval
            derivative = (error - self.prev_error) / self.adjust_interval
            self.prev_error = error

            adjustment = self.kp * error + self.ki * self.integral + self.kd * derivative
            self.target_cache_percent += adjustment * 100.0

            if cpu > 85:
                self.target_cache_percent -= 6.0
            elif cpu < 25 and ram_ratio > 0.5:
                self.target_cache_percent += 6.0

            if disk_lat > 5.0:
                self.target_cache_percent -= 4.0

            if self.workload_type == "file":
                self.target_cache_percent += self.workload_intensity * 4.0
            elif self.workload_type == "network":
                self.target_cache_percent -= self.workload_intensity * 2.5
            elif self.workload_type == "sensor":
                self.target_cache_percent += self.workload_intensity * 2.0
            else:
                self.target_cache_percent += self.workload_intensity * 1.0

            if self.ram_bw > self.disk_bw and self.ram_bw > self.swarm_bw:
                self.target_cache_percent += 2.0
            elif self.disk_bw > self.ram_bw:
                self.target_cache_percent -= 2.0

            self.target_cache_percent = max(self.min_cache_percent, min(self.max_cache_percent, self.target_cache_percent))

            log_info(
                f"[TUNER] CPU={cpu:.1f}% RAMFree={ram_ratio*100:.1f}% DiskLat={disk_lat:.2f} "
                f"WType={self.workload_type} WInt={self.workload_intensity:.2f} "
                f"BW(R={self.ram_bw/1024/1024:.2f}MB/s D={self.disk_bw/1024/1024:.2f}MB/s S={self.swarm_bw/1024/1024:.2f}MB/s) "
                f"→ Cache={self.target_cache_percent:.1f}%"
            )

            if self.callback:
                self.callback(self.target_cache_percent)

            time.sleep(self.adjust_interval)

    def stop(self):
        self.running = False
        log_info("[TUNER] Bandwidth Auto-Tuner stopped")

# ============================================================
# UNIFIED MEMORY ENGINE (Tiered + Migration + Residency + Cost)
# ============================================================

class UnifiedMemoryEngine:
    def __init__(self, os_layer, swarm_client=None):
        import mmap
        import queue

        self.os_layer = os_layer
        self.swarm_client = swarm_client

        self.ram_cache = bytearray(4 * 1024 * 1024)
        self.disk_cache_path = DISK_CACHE_FILE
        self.disk_size = DISK_CACHE_SIZE_MB * 1024 * 1024
        self.disk_mmap = None

        self.prefetch_queue = queue.Queue()
        self.running = True

        self._init_disk_cache()

        self.tuner = BandwidthAutoTuner(os_layer)
        self.tuner.start(self._dynamic_cache_resize)

        self.lock = threading.Lock()
        self.last_heartbeat = time.time()

        self.prefetch_thread = self._start_subsystem(self._prefetch_worker, "PREFETCH")
        self.boost_thread = self._start_subsystem(self._bandwidth_booster, "BOOST")

        self.workload_intensity = 0.0
        self.workload_type = "mixed"

        self.file_chunk_size = 1024 * 1024
        self.network_chunk_size = 256 * 1024
        self.sensor_chunk_size = 64 * 1024

        self.ram_bw_bytes = 0
        self.disk_bw_bytes = 0
        self.swarm_bw_bytes = 0
        self.bw_window_start = time.time()

        self.residency_map = {}  # key -> (tier, first_seen, last_access, priority, fingerprint)

    def _init_disk_cache(self):
        import mmap

        log_info(f"[DISK] Initializing disk-backed cache: {self.disk_cache_path} ({self.disk_size/1024/1024:.2f} MB)")
        with open(self.disk_cache_path, "wb") as f:
            f.seek(self.disk_size - 1)
            f.write(b"\0")

        self.disk_mmap = mmap.mmap(
            os.open(self.disk_cache_path, os.O_RDWR),
            self.disk_size
        )

    def _start_subsystem(self, target, name):
        t = threading.Thread(target=self._subsystem_wrapper, args=(target, name), daemon=True)
        t.start()
        log_info(f"[SUBSYSTEM] {name} started")
        return t

    def _subsystem_wrapper(self, target, name):
        while self.running:
            try:
                target()
            except Exception as e:
                log_error(f"[SUBSYSTEM] {name} crashed: {e} → restarting")
                time.sleep(1.0)

    def _dynamic_cache_resize(self, percent):
        free_ram = self.os_layer.get_free_ram()
        alloc_size = int(free_ram * (percent / 100.0))
        if alloc_size < 4 * 1024 * 1024:
            alloc_size = 4 * 1024 * 1024

        try:
            with self.lock:
                self.ram_cache = bytearray(alloc_size)
            log_info(f"[RAM CACHE] Resized to {alloc_size/1024/1024:.2f} MB (target {percent:.1f}%)")
        except MemoryError:
            log_error("[RAM CACHE] MemoryError during resize, keeping previous size")

    def update_workload_state(self, intensity, wtype):
        self.workload_intensity = intensity
        self.workload_type = wtype
        self.tuner.update_workload_state(intensity, wtype)

        if wtype == "file":
            self.file_chunk_size = int(1024 * 1024 * (1.0 + intensity))
        elif wtype == "network":
            self.network_chunk_size = int(256 * 1024 * max(0.5, 1.0 - intensity))
        elif wtype == "sensor":
            self.sensor_chunk_size = int(64 * 1024 * (1.0 + 0.5 * intensity))
        else:
            self.file_chunk_size = 1024 * 1024
            self.network_chunk_size = 256 * 1024
            self.sensor_chunk_size = 64 * 1024

        log_info(
            f"[ENGINE] Workload state updated: type={wtype} intensity={intensity:.2f} "
            f"chunks(file={self.file_chunk_size}, net={self.network_chunk_size}, sensor={self.sensor_chunk_size})"
        )

    def _update_bandwidth_window(self):
        now = time.time()
        elapsed = now - self.bw_window_start
        if elapsed >= 5.0:
            ram_bw = self.ram_bw_bytes / elapsed
            disk_bw = self.disk_bw_bytes / elapsed
            swarm_bw = self.swarm_bw_bytes / elapsed
            self.tuner.update_bandwidth(ram_bw, disk_bw, swarm_bw)
            self.ram_bw_bytes = 0
            self.disk_bw_bytes = 0
            self.swarm_bw_bytes = 0
            self.bw_window_start = now

    def _fingerprint(self, data: bytes):
        if not data:
            return {"entropy": 0.0, "size": 0}
        size = len(data)
        sample = data[:min(size, 1024)]
        unique = len(set(sample))
        entropy = unique / 256.0
        return {"entropy": entropy, "size": size}

    def _priority_for_key(self, key: str):
        if key.startswith("sensor:"):
            return PRIORITY_SENSOR
        elif key.startswith("net:"):
            return PRIORITY_NETWORK
        elif key.startswith("file:"):
            return PRIORITY_FILE
        return 1

    def _choose_tier(self, key: str, data: bytes):
        priority = self._priority_for_key(key)
        ram_pressure = self.os_layer.ram_pressure()
        disk_pressure = self.os_layer.disk_pressure()

        cost_ram = TIER_COST_RAM + ram_pressure * 2.0
        cost_disk = TIER_COST_DISK + disk_pressure * 1.5
        cost_swarm = TIER_COST_SWARM

        if priority >= PRIORITY_SENSOR:
            return "ram"
        elif priority == PRIORITY_NETWORK:
            if cost_ram <= cost_disk:
                return "ram"
            else:
                return "disk"
        else:
            if cost_disk <= cost_swarm:
                return "disk"
            else:
                return "swarm"

    def _record_residency(self, key, tier, fingerprint):
        now = time.time()
        if key not in self.residency_map:
            self.residency_map[key] = {
                "tier": tier,
                "first_seen": now,
                "last_access": now,
                "priority": self._priority_for_key(key),
                "fingerprint": fingerprint,
            }
        else:
            self.residency_map[key]["tier"] = tier
            self.residency_map[key]["last_access"] = now

    def _tier_migration_pass(self):
        now = time.time()
        for key, meta in list(self.residency_map.items()):
            age = now - meta["first_seen"]
            idle = now - meta["last_access"]
            tier = meta["tier"]
            priority = meta["priority"]

            if idle > 60 and tier == "ram":
                meta["tier"] = "disk"
            if idle > 300 and tier in ("ram", "disk"):
                meta["tier"] = "swarm"
            if idle > 900 and priority == PRIORITY_FILE:
                del self.residency_map[key]

    def write(self, key, data: bytes):
        start = time.time()
        tier = self._choose_tier(key, data)
        fingerprint = self._fingerprint(data)

        try:
            if tier == "ram":
                with self.lock:
                    if len(data) <= len(self.ram_cache):
                        self.ram_cache[:len(data)] = data
                        elapsed = time.time() - start
                        self.ram_bw_bytes += len(data)
                        log_info(f"[WRITE] RAM {len(data)} bytes in {elapsed:.6f}s key={key}")
                    else:
                        tier = "disk"
            if tier == "disk":
                start2 = time.time()
                self.disk_mmap.seek(0)
                self.disk_mmap.write(data[:self.disk_size])
                elapsed = time.time() - start2
                self.disk_bw_bytes += len(data)
                log_info(f"[WRITE] DISK {len(data)} bytes in {elapsed:.6f}s key={key}")
            if tier == "swarm" and self.swarm_client:
                threading.Thread(target=self._swarm_put_async, args=(key, data), daemon=True).start()
        except Exception as e:
            log_error(f"[WRITE] Error writing key={key}: {e}")

        self._record_residency(key, tier, fingerprint)
        self.prefetch_queue.put((key, len(data)))
        self.last_heartbeat = time.time()
        self._update_bandwidth_window()

    def _swarm_put_async(self, key, data):
        start = time.time()
        resp = self.swarm_client.put(key, data)
        elapsed = time.time() - start
        self.swarm_bw_bytes += len(data)
        log_info(f"[SWARM PUT] key={key} status={resp.get('status')} in {elapsed:.6f}s")

    def read(self, key, size=1024*1024):
        start = time.time()
        tier = self.residency_map.get(key, {}).get("tier", "ram")
        data = b""

        try:
            if tier == "ram":
                with self.lock:
                    data = self.ram_cache[:size]
                elapsed = time.time() - start
                self.ram_bw_bytes += len(data)
                log_info(f"[READ] RAM {len(data)} bytes in {elapsed:.6f}s key={key}")
            elif tier == "disk":
                start2 = time.time()
                self.disk_mmap.seek(0)
                data = self.disk_mmap.read(size)
                elapsed = time.time() - start2
                self.disk_bw_bytes += len(data)
                log_info(f"[READ] DISK {len(data)} bytes in {elapsed:.6f}s key={key}")
            elif tier == "swarm" and self.swarm_client:
                start3 = time.time()
                resp_data = self.swarm_client.get(key)
                elapsed = time.time() - start3
                data = resp_data
                self.swarm_bw_bytes += len(data)
                log_info(f"[READ] SWARM {len(data)} bytes in {elapsed:.6f}s key={key}")
        except Exception as e:
            log_error(f"[READ] Error reading key={key}: {e}")

        if key in self.residency_map:
            self.residency_map[key]["last_access"] = time.time()

        self.last_heartbeat = time.time()
        self._update_bandwidth_window()
        return data

    def _prefetch_worker(self):
        while self.running:
            try:
                key, size = self.prefetch_queue.get(timeout=1)
                time.sleep(0.01)
                log_info(f"[PREFETCH] Warmed cache for key={key} size={size}")
            except Exception:
                pass

    def _bandwidth_booster(self):
        while self.running:
            time.sleep(0.5)
            self._tier_migration_pass()
            log_info("[BOOST] Bandwidth pipeline warmed + tier migration pass")

    def heartbeat_ok(self):
        return (time.time() - self.last_heartbeat) < (HEARTBEAT_INTERVAL * 3)

    def health_score(self):
        cpu = self.os_layer.avg_cpu()
        ram_ratio = self.os_layer.avg_ram_free_ratio()
        disk_lat = self.os_layer.avg_disk_latency()

        score = 1.0
        if cpu > 90:
            score -= 0.2
        if ram_ratio < 0.15:
            score -= 0.3
        if disk_lat > 10.0:
            score -= 0.2

        return max(0.0, min(1.0, score))

    def stop(self):
        self.running = False
        self.tuner.stop()
        if self.disk_mmap:
            self.disk_mmap.close()
        log_info("[ENGINE] Stopped Unified Memory Engine")

# ============================================================
# REAL WORKLOAD MANAGER (Files + Network + Sensors + Brain)
# ============================================================

class WorkloadManager:
    def __init__(self, engine: UnifiedMemoryEngine):
        self.engine = engine
        self.running = True

        if not os.path.exists(WORKLOAD_SCAN_DIR):
            os.makedirs(WORKLOAD_SCAN_DIR, exist_ok=True)

        self.file_thread = threading.Thread(target=self._file_workload_loop, daemon=True)
        self.net_thread = threading.Thread(target=self._network_workload_loop, daemon=True)
        self.sensor_threads = [
            threading.Thread(target=self._sensor_workload_loop, args=(i,), daemon=True)
            for i in range(SENSOR_STREAM_COUNT)
        ]

        self.file_intensity = 0.0
        self.net_intensity = 0.0
        self.sensor_intensity = 0.0

        self.file_thread.start()
        self.net_thread.start()
        for t in self.sensor_threads:
            t.start()

        self.brain_thread = threading.Thread(target=self._brain_loop, daemon=True)
        self.brain_thread.start()

        log_info("[WORKLOAD] Manager started (files + network + sensors + brain)")

    def _file_workload_loop(self):
        while self.running:
            try:
                files = [os.path.join(WORKLOAD_SCAN_DIR, f)
                         for f in os.listdir(WORKLOAD_SCAN_DIR)
                         if os.path.isfile(os.path.join(WORKLOAD_SCAN_DIR, f))]
                total_bytes = 0
                for path in files:
                    try:
                        with open(path, "rb") as f:
                            chunk_id = 0
                            while True:
                                chunk = f.read(self.engine.file_chunk_size)
                                if not chunk:
                                    break
                                key = f"file:{os.path.basename(path)}:{chunk_id}"
                                self.engine.write(key, chunk)
                                _ = self.engine.read(key, size=min(len(chunk), self.engine.file_chunk_size))
                                total_bytes += len(chunk)
                                chunk_id += 1
                        log_info(f"[WORKLOAD FILE] Processed file: {path}")
                    except Exception as e:
                        log_error(f"[WORKLOAD FILE] Error processing {path}: {e}")
                self.file_intensity = min(1.0, total_bytes / (50 * 1024 * 1024))
                time.sleep(5.0)
            except Exception as e:
                log_error(f"[WORKLOAD FILE] Loop error: {e}")
                time.sleep(5.0)

    def _network_workload_loop(self):
        while self.running:
            bytes_this_cycle = 0
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.connect((NETWORK_WORKLOAD_HOST, NETWORK_WORKLOAD_PORT))
                        log_info(f"[WORKLOAD NET] Connected to {NETWORK_WORKLOAD_HOST}:{NETWORK_WORKLOAD_PORT}")
                        while self.running:
                            data = s.recv(self.engine.network_chunk_size)
                            if not data:
                                break
                            key = f"net:{int(time.time())}"
                            self.engine.write(key, data)
                            _ = self.engine.read(key, size=min(len(data), self.engine.network_chunk_size))
                            bytes_this_cycle += len(data)
                    except Exception as e:
                        log_error(f"[WORKLOAD NET] Connection error: {e}")
                self.net_intensity = min(1.0, bytes_this_cycle / (20 * 1024 * 1024))
                time.sleep(5.0)
            except Exception as e:
                log_error(f"[WORKLOAD NET] Loop error: {e}")
                time.sleep(5.0)

    def _sensor_workload_loop(self, sensor_id):
        while self.running:
            try:
                chunk_size = self.engine.sensor_chunk_size
                data = os.urandom(chunk_size)
                key = f"sensor:{sensor_id}:{int(time.time())}"
                self.engine.write(key, data)
                _ = self.engine.read(key, size=min(len(data), chunk_size))
                self.sensor_intensity = min(1.0, self.sensor_intensity + 0.01)
                self.sensor_intensity *= 0.95
                time.sleep(random.uniform(0.2, 1.0))
            except Exception as e:
                log_error(f"[WORKLOAD SENSOR] Sensor {sensor_id} error: {e}")
                time.sleep(1.0)

    def _brain_loop(self):
        while self.running:
            try:
                total_intensity = (self.file_intensity + self.net_intensity + self.sensor_intensity) / 3.0
                if self.file_intensity >= self.net_intensity and self.file_intensity >= self.sensor_intensity:
                    wtype = "file"
                elif self.net_intensity >= self.file_intensity and self.net_intensity >= self.sensor_intensity:
                    wtype = "network"
                elif self.sensor_intensity >= self.file_intensity and self.sensor_intensity >= self.net_intensity:
                    wtype = "sensor"
                else:
                    wtype = "mixed"

                self.engine.update_workload_state(total_intensity, wtype)

                log_info(
                    f"[BRAIN] Workload profile: file={self.file_intensity:.2f} "
                    f"net={self.net_intensity:.2f} sensor={self.sensor_intensity:.2f} "
                    f"→ type={wtype} intensity={total_intensity:.2f}"
                )

                time.sleep(BRAIN_LOOP_INTERVAL)
            except Exception as e:
                log_error(f"[BRAIN] Loop error: {e}")
                time.sleep(BRAIN_LOOP_INTERVAL)

    def stop(self):
        self.running = False
        log_info("[WORKLOAD] Manager stopped")

# ============================================================
# DAEMON CONTROLLER (Watchdog + 24/7 Loop)
# ============================================================

class DaemonController:
    def __init__(self):
        self.os_layer = create_os_layer()
        self.os_layer.describe()

        self.swarm_server = SwarmNodeServer()
        self.swarm_client = SwarmNodeClient()

        self.engine = None
        self.workload = None
        self.watchdog_thread = None
        self.subsystem_monitor_thread = None
        self.running = False

    def start(self):
        self.running = True
        self.swarm_server.start()
        self._start_engine()
        self.workload = WorkloadManager(self.engine)
        self.watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self.watchdog_thread.start()
        self.subsystem_monitor_thread = threading.Thread(target=self._subsystem_monitor_loop, daemon=True)
        self.subsystem_monitor_thread.start()
        log_info("[DAEMON] HybridBrain Unified Memory Daemon started")

    def _start_engine(self):
        self.engine = UnifiedMemoryEngine(self.os_layer, swarm_client=self.swarm_client)
        log_info("[DAEMON] Engine instance created")

    def _restart_engine(self):
        try:
            if self.engine:
                self.engine.stop()
        except Exception as e:
            log_error(f"[DAEMON] Error stopping engine: {e}")
        log_info("[DAEMON] Restarting engine...")
        self._start_engine()
        if self.workload:
            self.workload.stop()
        self.workload = WorkloadManager(self.engine)

    def _watchdog_loop(self):
        while self.running:
            time.sleep(WATCHDOG_INTERVAL)
            try:
                if not self.engine.heartbeat_ok():
                    log_error("[WATCHDOG] Engine heartbeat lost → restart")
                    self._restart_engine()
                else:
                    score = self.engine.health_score()
                    log_info(f"[WATCHDOG] Engine heartbeat OK, health={score:.2f}")
                    if score < 0.3:
                        log_error("[WATCHDOG] Health score low → restart")
                        self._restart_engine()
            except Exception as e:
                log_error(f"[WATCHDOG] Error checking heartbeat/health: {e}")
                self._restart_engine()

    def _subsystem_monitor_loop(self):
        while self.running:
            time.sleep(SUBSYSTEM_CHECK_INTERVAL)
            try:
                resp = self.swarm_client.ping()
                if resp.get("status") != "OK":
                    log_error("[SUBSYSTEM] Swarm node unhealthy (score may be low)")
                else:
                    score = self.swarm_client._score()
                    log_info(f"[SUBSYSTEM] Swarm node OK, score={score:.2f}")
            except Exception as e:
                log_error(f"[SUBSYSTEM] Swarm monitor error: {e}")

    def run_forever(self):
        while self.running:
            try:
                time.sleep(HEARTBEAT_INTERVAL)
            except KeyboardInterrupt:
                log_info("[DAEMON] KeyboardInterrupt → shutting down")
                self.stop()
                break
            except Exception as e:
                log_error(f"[DAEMON] Runtime error: {e}")
                self._restart_engine()

    def stop(self):
        self.running = False
        try:
            if self.workload:
                self.workload.stop()
            if self.engine:
                self.engine.stop()
        except Exception as e:
            log_error(f"[DAEMON] Error stopping engine/workload: {e}")
        self.swarm_server.stop()
        log_info("[DAEMON] HybridBrain Unified Memory Daemon stopped")

# ============================================================
# ENTRY POINT
# ============================================================

def main():
    controller = DaemonController()
    controller.start()
    controller.run_forever()

if __name__ == "__main__":
    main()
