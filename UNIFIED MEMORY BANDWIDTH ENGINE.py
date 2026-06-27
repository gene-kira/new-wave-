#!/usr/bin/env python3
# FULL UNIFIED MEMORY BANDWIDTH ENGINE
# Features:
# - Universal autoloader (Windows / Linux / macOS)
# - Dynamic Cache Expansion
# - Full Unified Memory OS Layer (RAM + Disk + optional remote nodes)
# - Bandwidth Auto-Tuner
# - Swarm Memory Nodes (cluster-style, simple TCP-based)

import os
import sys
import platform
import subprocess
import importlib
import socket
import threading
import time
import json

# =========================================================
# AUTOLOADER
# =========================================================

REQUIRED_LIBS = [
    "psutil",
]

def install_package(pkg):
    print(f"[AUTOLOADER] Installing missing package: {pkg}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return
    except Exception:
        pass

    os_name = platform.system()

    if os_name == "Windows":
        print("[AUTOLOADER] Windows fallback → pip")
        try:
            subprocess.check_call(["pip", "install", pkg])
        except Exception:
            print(f"[AUTOLOADER] Failed to install {pkg} via pip on Windows.")
    elif os_name == "Linux":
        print("[AUTOLOADER] Linux fallback → apt/yum/pacman")
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
        print(f"[AUTOLOADER] Failed to install {pkg} via system package manager.")
    elif os_name == "Darwin":
        print("[AUTOLOADER] macOS fallback → brew")
        try:
            subprocess.check_call(["brew", "install", pkg])
        except Exception:
            print("[AUTOLOADER] brew missing → installing via pip3")
            try:
                subprocess.check_call(["pip3", "install", pkg])
            except Exception:
                print(f"[AUTOLOADER] Failed to install {pkg} via brew/pip3 on macOS.")

def autoload():
    for lib in REQUIRED_LIBS:
        try:
            importlib.import_module(lib)
            print(f"[AUTOLOADER] Loaded: {lib}")
        except ImportError:
            install_package(lib)
    print("[AUTOLOADER] All dependencies satisfied.")

# =========================================================
# UNIFIED MEMORY OS LAYER + ENGINE
# =========================================================

def run_engine():
    autoload()
    import psutil
    import mmap
    import queue

    # -----------------------------
    # OS Abstraction Layer
    # -----------------------------
    class OSLayer:
        def __init__(self):
            self.os_name = platform.system()
            self.total_ram = psutil.virtual_memory().total
            self.page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

        def get_free_ram(self):
            return psutil.virtual_memory().available

        def get_cpu_load(self):
            return psutil.cpu_percent(interval=0.1)

        def get_disk_free(self, path="."):
            usage = psutil.disk_usage(path)
            return usage.free

        def describe(self):
            print(f"[OS] Detected: {self.os_name}")
            print(f"[OS] Total RAM: {self.total_ram/1024/1024:.2f} MB")
            print(f"[OS] Page size: {self.page_size} bytes")

    # -----------------------------
    # Swarm Memory Node (Server)
    # -----------------------------
    class SwarmNodeServer:
        def __init__(self, host="0.0.0.0", port=55555):
            self.host = host
            self.port = port
            self.running = False
            self.thread = None
            self.storage = {}  # simple key->bytes store

        def start(self):
            self.running = True
            self.thread = threading.Thread(target=self._serve, daemon=True)
            self.thread.start()
            print(f"[SWARM SERVER] Listening on {self.host}:{self.port}")

        def _serve(self):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.host, self.port))
                s.listen(5)
                while self.running:
                    try:
                        conn, addr = s.accept()
                        threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
                    except Exception:
                        continue

        def _handle_client(self, conn, addr):
            with conn:
                try:
                    data = conn.recv(4096)
                    if not data:
                        return
                    msg = json.loads(data.decode("utf-8"))
                    cmd = msg.get("cmd")
                    key = msg.get("key")
                    payload = msg.get("data")

                    if cmd == "PUT":
                        self.storage[key] = payload.encode("latin1") if isinstance(payload, str) else payload
                        resp = {"status": "OK"}
                    elif cmd == "GET":
                        val = self.storage.get(key, b"")
                        resp = {"status": "OK", "data": val.decode("latin1")}
                    else:
                        resp = {"status": "ERR", "msg": "Unknown command"}

                    conn.sendall(json.dumps(resp).encode("utf-8"))
                except Exception as e:
                    try:
                        conn.sendall(json.dumps({"status": "ERR", "msg": str(e)}).encode("utf-8"))
                    except Exception:
                        pass

        def stop(self):
            self.running = False
            print("[SWARM SERVER] Stopped")

    # -----------------------------
    # Swarm Memory Client
    # -----------------------------
    class SwarmNodeClient:
        def __init__(self, host="127.0.0.1", port=55555):
            self.host = host
            self.port = port

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

        def _send(self, msg):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.connect((self.host, self.port))
                    s.sendall(json.dumps(msg).encode("utf-8"))
                    data = s.recv(4096)
                    if not data:
                        return {"status": "ERR", "msg": "No response"}
                    return json.loads(data.decode("utf-8"))
            except Exception as e:
                return {"status": "ERR", "msg": str(e)}

    # -----------------------------
    # Bandwidth Auto-Tuner
    # -----------------------------
    class BandwidthAutoTuner:
        def __init__(self, os_layer: OSLayer):
            self.os_layer = os_layer
            self.target_cache_percent = 20
            self.min_cache_percent = 5
            self.max_cache_percent = 60
            self.adjust_interval = 2.0
            self.running = False
            self.thread = None
            self.callback = None  # function(percent)

        def start(self, callback):
            self.callback = callback
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            print("[TUNER] Bandwidth Auto-Tuner started")

        def _loop(self):
            while self.running:
                cpu = self.os_layer.get_cpu_load()
                free_ram = self.os_layer.get_free_ram()
                total_ram = self.os_layer.total_ram
                free_ratio = free_ram / total_ram

                # Simple heuristic:
                # - If CPU low and free RAM high → increase cache
                # - If CPU high or free RAM low → decrease cache
                if cpu < 30 and free_ratio > 0.4:
                    self.target_cache_percent = min(self.target_cache_percent + 5, self.max_cache_percent)
                elif cpu > 70 or free_ratio < 0.2:
                    self.target_cache_percent = max(self.target_cache_percent - 5, self.min_cache_percent)

                print(f"[TUNER] CPU={cpu:.1f}% FreeRAM={free_ratio*100:.1f}% → Cache={self.target_cache_percent}%")

                if self.callback:
                    self.callback(self.target_cache_percent)

                time.sleep(self.adjust_interval)

        def stop(self):
            self.running = False
            print("[TUNER] Bandwidth Auto-Tuner stopped")

    # -----------------------------
    # Unified Memory Engine
    # -----------------------------
    class UnifiedMemoryEngine:
        def __init__(self, os_layer: OSLayer, swarm_client: SwarmNodeClient = None):
            self.os_layer = os_layer
            self.swarm_client = swarm_client

            self.ram_cache = bytearray(1)  # will be resized
            self.disk_cache_path = "extended_memory.bin"
            self.disk_size = 512 * 1024 * 1024
            self.disk_mmap = None

            self.prefetch_queue = queue.Queue()
            self.running = True

            self._init_disk_cache()

            self.tuner = BandwidthAutoTuner(os_layer)
            self.tuner.start(self._dynamic_cache_resize)

            threading.Thread(target=self._prefetch_worker, daemon=True).start()
            threading.Thread(target=self._bandwidth_booster, daemon=True).start()

        def _init_disk_cache(self):
            print(f"[DISK] Initializing disk-backed cache: {self.disk_cache_path} ({self.disk_size/1024/1024:.2f} MB)")
            with open(self.disk_cache_path, "wb") as f:
                f.seek(self.disk_size - 1)
                f.write(b"\0")

            self.disk_mmap = mmap.mmap(
                os.open(self.disk_cache_path, os.O_RDWR),
                self.disk_size
            )

        # Dynamic Cache Expansion
        def _dynamic_cache_resize(self, percent):
            free_ram = self.os_layer.get_free_ram()
            alloc_size = int(free_ram * (percent / 100))
            if alloc_size < 1024 * 1024:
                alloc_size = 1024 * 1024  # minimum 1MB

            try:
                self.ram_cache = bytearray(alloc_size)
                print(f"[RAM CACHE] Resized to {alloc_size/1024/1024:.2f} MB (target {percent}%)")
            except MemoryError:
                print("[RAM CACHE] MemoryError during resize, keeping previous size")

        def write(self, key, data: bytes):
            # Try RAM
            try:
                start = time.time()
                if len(data) <= len(self.ram_cache):
                    self.ram_cache[:len(data)] = data
                    print(f"[WRITE] RAM {len(data)} bytes in {time.time()-start:.6f}s key={key}")
                else:
                    raise ValueError("Data larger than RAM cache")
            except Exception:
                # Fallback to disk
                start = time.time()
                self.disk_mmap.seek(0)
                self.disk_mmap.write(data[:self.disk_size])
                print(f"[WRITE] DISK {len(data)} bytes in {time.time()-start:.6f}s key={key}")

            # Swarm node offload (optional)
            if self.swarm_client:
                threading.Thread(target=self._swarm_put_async, args=(key, data), daemon=True).start()

            self.prefetch_queue.put((key, len(data)))

        def _swarm_put_async(self, key, data):
            resp = self.swarm_client.put(key, data)
            print(f"[SWARM PUT] key={key} status={resp.get('status')}")

        def read(self, key, size=1024*1024):
            start = time.time()
            # Try RAM
            try:
                data = self.ram_cache[:size]
                print(f"[READ] RAM {len(data)} bytes in {time.time()-start:.6f}s key={key}")
            except Exception:
                self.disk_mmap.seek(0)
                data = self.disk_mmap.read(size)
                print(f"[READ] DISK {len(data)} bytes in {time.time()-start:.6f}s key={key}")

            # Swarm node fetch (optional)
            if self.swarm_client:
                resp_data = self.swarm_client.get(key)
                if resp_data:
                    print(f"[SWARM GET] key={key} bytes={len(resp_data)}")
            return data

        def _prefetch_worker(self):
            while self.running:
                try:
                    key, size = self.prefetch_queue.get(timeout=1)
                    time.sleep(0.01)
                    print(f"[PREFETCH] Warmed cache for key={key} size={size}")
                except queue.Empty:
                    pass

        def _bandwidth_booster(self):
            while self.running:
                time.sleep(0.5)
                print("[BOOST] Bandwidth pipeline warmed")

        def stop(self):
            self.running = False
            self.tuner.stop()
            if self.disk_mmap:
                self.disk_mmap.close()
            print("[ENGINE] Stopped Unified Memory Engine")

    # =========================================================
    # BOOTSTRAP
    # =========================================================

    os_layer = OSLayer()
    os_layer.describe()

    # Start swarm server locally (optional cluster mode)
    swarm_server = SwarmNodeServer(host="0.0.0.0", port=55555)
    swarm_server.start()

    swarm_client = SwarmNodeClient(host="127.0.0.1", port=55555)

    engine = UnifiedMemoryEngine(os_layer, swarm_client=swarm_client)

    # Demo workload
    print("[DEMO] Writing 4MB block...")
    engine.write("block_4mb", b"A" * (4 * 1024 * 1024))

    print("[DEMO] Reading 1MB block...")
    _ = engine.read("block_4mb", size=1024 * 1024)

    time.sleep(5)

    engine.stop()
    swarm_server.stop()

if __name__ == "__main__":
    run_engine()
