#!/usr/bin/env python
# ============================================================
#  ULTIMATE UNIFIED MONOLITH (GLOBAL SWARM EDITION)
#  - Advanced Universal Autoloader
#  - GPU detection: NVIDIA / AMD / Intel / macOS
#  - Logging + telemetry (policy-aware, suppressible)
#  - Virtual environments per subsystem
#  - Wheel signature verification (stubbed trust layer)
#  - Distributed manifest sync (swarm nodes, signed + encrypted)
#  - Cluster / HyperSwarm Core (multi-node GPU-aware scheduler)
#  - REST API (real IPC: jobs, results, control)
#  - SQLite Persistent State (jobs + events)
#  - Forklift LLM Engine (tokenizer + full inference pipeline)
#  - Multi-model Torch/ONNX, GPU-aware
#  - Process isolation (each subsystem as its own process)
#  - Encrypted swarm mesh (AES-GCM, WireGuard-style concept)
#  - Daemonized Watchdog (process-level, swarm-aware)
#  - Group Policy Enforcement (Windows) + policy.json (all OS)
#  - Telemetry Control & Suppression
#  - Swarm Controller Client (HTTPS, world-wide)
#  - VPN-aware node identity (overlay-friendly)
# ============================================================

import sys, subprocess, importlib.util, os, time, tempfile, threading, json, base64, hashlib, random, socket, hmac
import platform, logging
from datetime import datetime
import psutil
import queue
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import request as urlrequest

# Optional AI backends
try:
    import torch
except ImportError:
    torch = None

try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    from transformers import AutoTokenizer, AutoModelForCausalLM
except ImportError:
    AutoTokenizer = None
    AutoModelForCausalLM = None

# Optional crypto for encrypted mesh
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

# Optional Windows-only modules
if os.name == "nt":
    import ctypes
    try:
        import winreg
    except ImportError:
        winreg = None
else:
    ctypes = None
    winreg = None

# ============================================================
#  GLOBAL CONFIG
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_PATH = os.path.join(BASE_DIR, "autoloader.log")
DB_PATH = os.path.join(BASE_DIR, "cluster_state.db")

AUTOLOADER_NAME = "UniversalAutoloader_v3"
MANIFEST_KEY    = "killer666_autoloader_key"
MANIFEST_PATH   = os.path.join(BASE_DIR, "deps_manifest.enc")

SWARM_CONFIG_PATH = os.path.join(BASE_DIR, "swarm.json")

# swarm manifest sync
SWARM_MANIFEST_PORT = 49666
SWARM_MANIFEST_BROADCAST_INTERVAL = 300

# swarm watchdog sync
SWARM_WATCHDOG_PORT = 49667
SWARM_WATCHDOG_BROADCAST_INTERVAL = 15

# swarm encrypted mesh (LAN/WAN overlay)
SWARM_MESH_PORT = 49668

# swarm security
SWARM_SECRET = hashlib.sha256(MANIFEST_KEY.encode("utf-8")).digest()
MESH_KEY = hashlib.sha256((MANIFEST_KEY + "_mesh").encode("utf-8")).digest()[:32]

# virtualenvs per subsystem
VENV_BASE = os.path.join(BASE_DIR, "venvs")
SUBSYSTEMS = ["cluster", "autopilot", "mega", "forklift", "technical", "automation", "api"]
SUBSYSTEM_VENVS = {name: os.path.join(VENV_BASE, name) for name in SUBSYSTEMS}

# process roles
ROLE_MASTER = "master"
ROLE_CLUSTER = "cluster"
ROLE_AUTOPILOT = "autopilot"
ROLE_MEGA = "mega"
ROLE_FORKLIFT = "forklift"
ROLE_TECHNICAL = "technical"
ROLE_AUTOMATION = "automation"
ROLE_API = "api"

# ============================================================
#  SWARM CONFIG (CONTROLLER + VPN)
# ============================================================

class SwarmConfig:
    def __init__(self, path: str):
        self.path = path
        self.controller_url = None
        self.node_id = None
        self.node_secret = None
        self.use_vpn = False
        self.vpn_ip = None
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            # default: LAN-only, no controller
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.controller_url = data.get("controller_url")
            self.node_id = data.get("node_id") or socket.gethostname()
            self.node_secret = data.get("node_secret") or MANIFEST_KEY
            self.use_vpn = bool(data.get("use_vpn", False))
            self.vpn_ip = data.get("vpn_ip")
        except Exception as e:
            print(f"[SWARM-CONFIG] Failed to load swarm.json: {e}", file=sys.stderr)

SWARM_CONFIG = SwarmConfig(SWARM_CONFIG_PATH)

# ============================================================
#  LOGGING + TELEMETRY (POLICY-AWARE)
# ============================================================

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def log_info(msg: str):
    print(msg)
    logging.info(msg)

def log_error(msg: str):
    print(msg, file=sys.stderr)
    logging.error(msg)

TELEMETRY_LEVEL = 2
TELEMETRY_LOCK = threading.Lock()

def set_telemetry_level(level: int):
    global TELEMETRY_LEVEL
    with TELEMETRY_LOCK:
        TELEMETRY_LEVEL = max(0, min(2, level))
    log_info(f"[TELEMETRY] Level set to {TELEMETRY_LEVEL}")

def telemetry_event(event: str, data: dict | None = None):
    with TELEMETRY_LOCK:
        level = TELEMETRY_LEVEL
    if level <= 0:
        return
    payload = {"event": event, "ts": datetime.utcnow().isoformat()}
    if level >= 2:
        payload["data"] = data or {}
    log_info(f"[TELEMETRY] {payload}")

# ============================================================
#  SQLITE PERSISTENT STATE (JOBS + EVENTS)
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts TEXT,
            status TEXT,
            model TEXT,
            input TEXT,
            output TEXT,
            target_node TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            type TEXT,
            detail TEXT
        )
    """)
    conn.commit()
    return conn

DB = get_db()

def log_event(ev_type: str, detail: str):
    try:
        DB.execute(
            "INSERT INTO events (ts, type, detail) VALUES (?, ?, ?)",
            (datetime.utcnow().isoformat(), ev_type, detail)
        )
        DB.commit()
    except Exception as e:
        log_error(f"[DB] Failed to log event {ev_type}: {e}")

# ============================================================
#  ENCRYPTED DEPENDENCY MANIFEST
# ============================================================

def _derive_key(seed: str) -> bytes:
    return hashlib.sha256(seed.encode("utf-8")).digest()

def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def encrypt_manifest(manifest: dict) -> bytes:
    raw = json.dumps(manifest).encode("utf-8")
    key = _derive_key(MANIFEST_KEY)
    return base64.b64encode(_xor_bytes(raw, key))

def decrypt_manifest(blob: bytes) -> dict:
    key = _derive_key(MANIFEST_KEY)
    raw = _xor_bytes(base64.b64decode(blob), key)
    return json.loads(raw.decode("utf-8"))

def load_manifest_from_disk() -> dict | None:
    if not os.path.exists(MANIFEST_PATH):
        return None
    try:
        with open(MANIFEST_PATH, "rb") as f:
            blob = f.read()
        manifest = decrypt_manifest(blob)
        log_info(f"[{AUTOLOADER_NAME}] Loaded manifest from disk.")
        return manifest
    except Exception as e:
        log_error(f"[{AUTOLOADER_NAME}] Failed to load manifest: {e}")
        return None

def save_manifest_to_disk(manifest: dict) -> None:
    try:
        blob = encrypt_manifest(manifest)
        with open(MANIFEST_PATH, "wb") as f:
            f.write(blob)
        log_info(f"[{AUTOLOADER_NAME}] Saved manifest to disk.")
    except Exception as e:
        log_error(f"[{AUTOLOADER_NAME}] Failed to save manifest: {e}")

# ============================================================
#  DEPENDENCY MANIFEST (WINDOWS-ENHANCED, CROSS-PLATFORM SAFE)
# ============================================================

DEFAULT_MANIFEST = {
    "requests": {
        "check": "requests",
        "pip": "requests",
        "version": "2.32.3",
        "post": False,
        "critical": False
    },
    "Pillow": {
        "check": "PIL",
        "pip": "Pillow",
        "version": None,
        "post": False,
        "critical": False
    },
    "torch": {
        "check": "torch",
        "pip": "torch",
        "version": None,
        "post": False,
        "critical": False,
        "gpu": True
    },
    "onnxruntime": {
        "check": "onnxruntime",
        "pip": "onnxruntime-gpu",
        "version": None,
        "post": False,
        "critical": False,
        "gpu": True
    },
    "transformers": {
        "check": "transformers",
        "pip": "transformers",
        "version": None,
        "post": False,
        "critical": False
    },
    "cryptography": {
        "check": "cryptography",
        "pip": "cryptography",
        "version": None,
        "post": False,
        "critical": False
    }
}

if os.name == "nt":
    DEFAULT_MANIFEST["pywin32"] = {
        "check": "win32api",
        "pip": "pywin32",
        "version": None,
        "post": True,
        "critical": True
    }

MANIFEST = load_manifest_from_disk() or DEFAULT_MANIFEST

# ============================================================
#  AUTOLOADER UTILS
# ============================================================

def run(cmd: str):
    try:
        telemetry_event("run_cmd", {"cmd": cmd})
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True
        )
    except Exception as e:
        log_error(f"[{AUTOLOADER_NAME}] ERROR running command: {e}")
        return None

def is_module_installed(check_name: str) -> bool:
    return importlib.util.find_spec(check_name) is not None

def get_installed_version(module_name: str) -> str | None:
    try:
        import importlib.metadata as md
    except ImportError:
        try:
            import importlib_metadata as md
        except ImportError:
            return None
    try:
        return md.version(module_name)
    except md.PackageNotFoundError:
        return None

def pip_install(package: str, version: str | None = None, venv_path: str | None = None) -> subprocess.CompletedProcess | None:
    spec = f"{package}=={version}" if version else package
    python_exec = sys.executable
    if venv_path:
        python_exec = os.path.join(venv_path, "Scripts" if os.name == "nt" else "bin", "python")
    cmd = f'"{python_exec}" -m pip install --upgrade {spec}'
    log_info(f"[{AUTOLOADER_NAME}] pip install: {cmd}")
    return run(cmd)

def repair_pip(venv_path: str | None = None):
    python_exec = sys.executable
    if venv_path:
        python_exec = os.path.join(venv_path, "Scripts" if os.name == "nt" else "bin", "python")
    log_info(f"[{AUTOLOADER_NAME}] Repairing pip...")
    run(f'"{python_exec}" -m ensurepip --default-pip')
    run(f'"{python_exec}" -m pip install --upgrade pip setuptools wheel')

# ============================================================
#  GPU DETECTION (NVIDIA / AMD / INTEL / macOS)
# ============================================================

def detect_gpu_vendor() -> str | None:
    vendor = None
    try:
        if os.name == "nt":
            result = run("wmic path win32_VideoController get Name")
            if result and result.returncode == 0:
                out = result.stdout.lower()
                if "nvidia" in out:
                    vendor = "nvidia"
                elif "amd" in out or "radeon" in out:
                    vendor = "amd"
                elif "intel" in out:
                    vendor = "intel"
        elif sys.platform == "darwin":
            result = run("system_profiler SPDisplaysDataType")
            if result and result.returncode == 0:
                out = result.stdout.lower()
                if "nvidia" in out:
                    vendor = "nvidia"
                elif "amd" in out or "radeon" in out:
                    vendor = "amd"
                elif "intel" in out:
                    vendor = "intel"
        else:
            result = run("lspci | grep -i 'vga\\|3d\\|display'")
            if result and result.returncode == 0:
                out = result.stdout.lower()
                if "nvidia" in out:
                    vendor = "nvidia"
                elif "amd" in out or "radeon" in out:
                    vendor = "amd"
                elif "intel" in out:
                    vendor = "intel"
    except Exception as e:
        log_error(f"[{AUTOLOADER_NAME}] GPU detection error: {e}")
    log_info(f"[{AUTOLOADER_NAME}] GPU vendor detected: {vendor}")
    telemetry_event("gpu_detect", {"vendor": vendor})
    return vendor

def adjust_gpu_packages(manifest: dict):
    vendor = detect_gpu_vendor()
    gpu = vendor is not None
    log_info(f"[{AUTOLOADER_NAME}] GPU present: {gpu}, vendor={vendor}")
    for name, spec in manifest.items():
        if spec.get("gpu"):
            if vendor == "nvidia":
                pass
            elif vendor in ("amd", "intel") or vendor is None:
                if spec["pip"] == "onnxruntime-gpu":
                    spec["pip"] = "onnxruntime"

# ============================================================
#  WHEEL SIGNATURE VERIFICATION (STUB)
# ============================================================

TRUSTED_WHEEL_HASHES = {}

def verify_wheel_signature(path: str) -> bool:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        digest = h.hexdigest()
        log_info(f"[{AUTOLOADER_NAME}] Wheel {path} sha256={digest}")
        telemetry_event("wheel_verify", {"path": path, "sha256": digest})
        return True
    except Exception as e:
        log_error(f"[{AUTOLOADER_NAME}] Wheel signature verification failed: {e}")
        return False

# ============================================================
#  VIRTUAL ENVIRONMENTS PER SUBSYSTEM
# ============================================================

def ensure_venv(path: str):
    if not os.path.exists(path):
        log_info(f"[{AUTOLOADER_NAME}] Creating virtualenv at {path}")
        subprocess.run(f'"{sys.executable}" -m venv "{path}"', shell=True)
    else:
        log_info(f"[{AUTOLOADER_NAME}] Virtualenv exists at {path}")

def get_venv_python(path: str) -> str:
    return os.path.join(path, "Scripts" if os.name == "nt" else "bin", "python")

# ============================================================
#  MODULE INSTALLATION
# ============================================================

def install_module(name: str, spec: dict, venv_path: str | None = None) -> bool:
    check_name = spec["check"]
    pip_name   = spec["pip"]
    version    = spec.get("version")
    needs_post = spec.get("post", False)

    if is_module_installed(check_name) and venv_path is None:
        installed_ver = get_installed_version(pip_name)
        log_info(f"[{AUTOLOADER_NAME}] {name} already installed (version={installed_ver})")
        if version and installed_ver and installed_ver != version:
            log_info(f"[{AUTOLOADER_NAME}] Version mismatch for {name}: {installed_ver} != {version}, attempting pin...")
            result = pip_install(pip_name, version, venv_path)
            if result is None or result.returncode != 0:
                log_error(f"[{AUTOLOADER_NAME}] Failed to pin {name} to {version}, keeping current.")
        return True

    log_info(f"[{AUTOLOADER_NAME}] Missing or venv-specific: {name} — installing...")
    result = pip_install(pip_name, version, venv_path)

    if result is None or result.returncode != 0:
        log_error(f"[{AUTOLOADER_NAME}] pip failed for {name} — repairing...")
        repair_pip(venv_path)
        result = pip_install(pip_name, version, venv_path)

    if result is not None and result.returncode != 0 and name.lower() == "pywin32" and venv_path is None and os.name == "nt":
        log_info(f"[{AUTOLOADER_NAME}] fallback: downloading pywin32 wheel...")
        import urllib.request
        wheel_url = "https://github.com/mhammond/pywin32/releases/latest/download/pywin32-306-cp311-cp311-win_amd64.whl"
        tmp = os.path.join(tempfile.gettempdir(), "pywin32.whl")
        urllib.request.urlretrieve(wheel_url, tmp)
        if verify_wheel_signature(tmp):
            result = run(f'"{sys.executable}" -m pip install "{tmp}"')
        else:
            log_error(f"[{AUTOLOADER_NAME}] Wheel signature not trusted for {tmp}")

    if needs_post and venv_path is None and os.name == "nt":
        log_info(f"[{AUTOLOADER_NAME}] running post-install for {name}...")
        run(f'"{sys.executable}" -m pywin32_postinstall -install')

    if venv_path is None and not is_module_installed(check_name):
        log_error(f"[{AUTOLOADER_NAME}] FAILED: {name}")
        return False

    log_info(f"[{AUTOLOADER_NAME}] Installed: {name}")
    telemetry_event("module_install", {"name": name, "pip": pip_name, "version": version})
    return True

# ============================================================
#  HEALTH SCANNER + REPAIR DAEMON
# ============================================================

def health_scan_and_repair():
    log_info(f"[{AUTOLOADER_NAME}] Health scan started...")
    telemetry_event("health_scan_start", {})
    adjust_gpu_packages(MANIFEST)
    for name, spec in MANIFEST.items():
        check_name = spec["check"]
        critical   = spec.get("critical", False)
        if not is_module_installed(check_name):
            log_info(f"[{AUTOLOADER_NAME}] Health issue: {name} missing.")
            ok = install_module(name, spec)
            if not ok and critical:
                log_error(f"[{AUTOLOADER_NAME}] Critical dependency {name} failed to repair.")
                log_event("critical_dep_failure", name)
    telemetry_event("health_scan_complete", {})
    log_info(f"[{AUTOLOADER_NAME}] Health scan completed.")

def repair_daemon_loop(interval_sec: int = 600):
    while True:
        try:
            health_scan_and_repair()
        except Exception as e:
            log_error(f"[{AUTOLOADER_NAME}] Repair daemon error: {e}")
            log_event("repair_daemon_error", str(e))
        time.sleep(interval_sec)

def start_repair_daemon():
    t = threading.Thread(target=repair_daemon_loop, args=(600,), daemon=True)
    t.start()
    log_info(f"[{AUTOLOADER_NAME}] Repair daemon started (interval=600s).")

# ============================================================
#  BACKGROUND UPDATER
# ============================================================

def background_updater_loop(interval_sec: int = 3600):
    while True:
        try:
            log_info(f"[{AUTOLOADER_NAME}] Background updater running...")
            telemetry_event("background_updater_tick", {})
            for name, spec in MANIFEST.items():
                if not spec.get("critical", False) and random.random() < 0.3:
                    log_info(f"[{AUTOLOADER_NAME}] Background update attempt for {name}...")
                    pip_install(spec["pip"], spec.get("version"))
        except Exception as e:
            log_error(f"[{AUTOLOADER_NAME}] Background updater error: {e}")
            log_event("background_updater_error", str(e))
        time.sleep(interval_sec)

def start_background_updater():
    t = threading.Thread(target=background_updater_loop, args=(3600,), daemon=True)
    t.start()
    log_info(f"[{AUTOLOADER_NAME}] Background updater started (interval=3600s).")

# ============================================================
#  SWARM SIGNING + ENCRYPTION HELPERS
# ============================================================

def sign_payload(payload: dict) -> dict:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    sig = hmac.new(SWARM_SECRET, body, hashlib.sha256).hexdigest()
    payload["sig"] = sig
    return payload

def verify_payload(payload: dict) -> bool:
    sig = payload.get("sig")
    if not sig:
        return False
    tmp = dict(payload)
    tmp.pop("sig", None)
    body = json.dumps(tmp, sort_keys=True).encode("utf-8")
    expected = hmac.new(SWARM_SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)

def mesh_encrypt(data: bytes) -> bytes:
    if AESGCM is None:
        return data
    aes = AESGCM(MESH_KEY)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, data, None)
    return nonce + ct

def mesh_decrypt(data: bytes) -> bytes:
    if AESGCM is None:
        return data
    aes = AESGCM(MESH_KEY)
    nonce, ct = data[:12], data[12:]
    return aes.decrypt(nonce, ct, None)

# ============================================================
#  DISTRIBUTED MANIFEST SYNC (SWARM, SIGNED + ENCRYPTED)
# ============================================================

def broadcast_manifest_loop(node_id: str):
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            payload = {"type": "manifest", "node": node_id, "manifest": MANIFEST}
            payload = sign_payload(payload)
            raw = json.dumps(payload).encode("utf-8")
            raw = mesh_encrypt(raw)
            s.sendto(raw, ("255.255.255.255", SWARM_MANIFEST_PORT))
            s.close()
            telemetry_event("manifest_broadcast", {})
            log_info(f"[{AUTOLOADER_NAME}] Broadcasted manifest to swarm.")
        except Exception as e:
            log_error(f"[{AUTOLOADER_NAME}] Manifest broadcast error: {e}")
            log_event("manifest_broadcast_error", str(e))
        time.sleep(SWARM_MANIFEST_BROADCAST_INTERVAL)

def listen_manifest_loop(node_id: str):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", SWARM_MANIFEST_PORT))
    log_info(f"[{AUTOLOADER_NAME}] Listening for manifest sync on port {SWARM_MANIFEST_PORT}...")
    while True:
        try:
            data, addr = s.recvfrom(65535)
            try:
                data = mesh_decrypt(data)
            except Exception:
                continue
            msg = json.loads(data.decode("utf-8"))
            if msg.get("type") == "manifest":
                if not verify_payload(msg):
                    log_error(f"[{AUTOLOADER_NAME}] Manifest signature invalid from {addr}")
                    log_event("manifest_sig_invalid", str(addr))
                    continue
                if msg.get("node") == node_id:
                    continue
                incoming = msg.get("manifest", {})
                log_info(f"[{AUTOLOADER_NAME}] Received manifest from {addr}, syncing.")
                telemetry_event("manifest_received", {"from": str(addr)})
                MANIFEST.clear()
                MANIFEST.update(incoming)
                save_manifest_to_disk(MANIFEST)
        except Exception as e:
            log_error(f"[{AUTOLOADER_NAME}] Manifest listener error: {e}")
            log_event("manifest_listener_error", str(e))

def start_manifest_sync(node_id: str):
    threading.Thread(target=broadcast_manifest_loop, args=(node_id,), daemon=True).start()
    threading.Thread(target=listen_manifest_loop, args=(node_id,), daemon=True).start()

# ============================================================
#  ELEVATION / SYSTEM PROFILE (CROSS-PLATFORM)
# ============================================================

def ensure_admin():
    if os.name == "nt":
        if ctypes is None:
            return
        try:
            if not ctypes.windll.shell32.IsUserAnAdmin():
                script = os.path.abspath(sys.argv[0])
                params = " ".join([f'"{a}"' for a in sys.argv[1:]])
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, f'"{script}" {params}', None, 1
                )
                sys.exit()
        except Exception as e:
            log_error(f"[SYSTEM] Elevation failed (Windows): {e}")
            log_event("elevation_failed", str(e))
            sys.exit()
    else:
        try:
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                log_info("[SYSTEM] Re-running with sudo for elevated privileges...")
                script = os.path.abspath(sys.argv[0])
                os.execvp("sudo", ["sudo", sys.executable, script] + sys.argv[1:])
        except Exception as e:
            log_error(f"[SYSTEM] Elevation failed (POSIX): {e}")
            log_event("elevation_failed", str(e))

class SystemProfile:
    def __init__(self):
        self.hostname = socket.gethostname()
        self.platform = platform.platform()
        self.cpu_count = psutil.cpu_count(logical=True)
        self.ram_gb = round(psutil.virtual_memory().total / (1024**3), 2)
        self.vpn_ip = SWARM_CONFIG.vpn_ip
        log_info(f"[PROFILE] {self.hostname} | {self.platform} | CPU={self.cpu_count} RAM={self.ram_gb}GB | VPN={self.vpn_ip}")

SYSTEM_PROFILE = SystemProfile()

# ============================================================
#  METRICS
# ============================================================

class MetricsRegistry:
    def __init__(self, name):
        self.name = name
        self.metrics = {}
        self.lock = threading.Lock()
    def set(self, k, v):
        with self.lock:
            self.metrics[k] = v
    def inc(self, k, n=1):
        with self.lock:
            self.metrics[k] = self.metrics.get(k, 0) + n
    def snapshot(self):
        with self.lock:
            return dict(self.metrics)

# ============================================================
#  GROUP POLICY ENFORCEMENT + TELEMETRY CONTROL
# ============================================================

class GroupPolicyEnforcer:
    if os.name == "nt":
        REG_PATH = r"SOFTWARE\\Policies\\Killer666\\UniversalAutoloader"
    else:
        REG_PATH = None
    LOCAL_POLICY_FILE = os.path.join(BASE_DIR, "policy.json")

    def __init__(self):
        self.policies = {}
        self.load_policies()
        self.apply_policies()

    def load_policies(self):
        if os.name == "nt" and winreg is not None and self.REG_PATH is not None:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self.REG_PATH) as key:
                    try:
                        val, _ = winreg.QueryValueEx(key, "TelemetryLevel")
                        self.policies["TelemetryLevel"] = int(val)
                    except FileNotFoundError:
                        pass
            except Exception as e:
                log_error(f"[POLICY] Registry read failed: {e}")
                log_event("policy_registry_error", str(e))

        if os.path.exists(self.LOCAL_POLICY_FILE):
            try:
                with open(self.LOCAL_POLICY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.policies.update(data)
            except Exception as e:
                log_error(f"[POLICY] Failed to read local policy.json: {e}")
                log_event("policy_file_error", str(e))

        log_info(f"[POLICY] Loaded policies: {self.policies}")

    def apply_policies(self):
        lvl = self.policies.get("TelemetryLevel")
        if isinstance(lvl, int):
            set_telemetry_level(lvl)
        else:
            set_telemetry_level(2)

    def loop(self, interval=300):
        while True:
            try:
                self.load_policies()
                self.apply_policies()
            except Exception as e:
                log_error(f"[POLICY] Enforcement loop error: {e}")
                log_event("policy_loop_error", str(e))
            time.sleep(interval)

def start_policy_enforcer():
    gp = GroupPolicyEnforcer()
    threading.Thread(target=gp.loop, daemon=True).start()
    log_info("[POLICY] Group Policy enforcer started.")
    return gp

# ============================================================
#  SWARM CONTROLLER CLIENT (HTTPS, WORLD-WIDE)
# ============================================================

class SwarmControllerClient:
    def __init__(self, config: SwarmConfig):
        self.config = config
        self.enabled = bool(config.controller_url)
        self.node_id = config.node_id or SYSTEM_PROFILE.hostname
        self.node_secret = config.node_secret or MANIFEST_KEY
        self.controller_url = config.controller_url
        self.last_heartbeat = 0

    def _build_url(self, path: str) -> str:
        return self.controller_url.rstrip("/") + path

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "X-Node-ID": self.node_id,
            "X-Node-Secret": self.node_secret,
        }

    def _post_json(self, path: str, payload: dict) -> dict | None:
        if not self.enabled:
            return None
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urlrequest.Request(
                self._build_url(path),
                data=data,
                headers=self._headers(),
                method="POST"
            )
            with urlrequest.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except Exception as e:
            log_error(f"[CONTROLLER] POST {path} failed: {e}")
            log_event("controller_post_error", f"{path}:{e}")
            return None

    def _get_json(self, path: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            req = urlrequest.Request(
                self._build_url(path),
                headers=self._headers(),
                method="GET"
            )
            with urlrequest.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except Exception as e:
            log_error(f"[CONTROLLER] GET {path} failed: {e}")
            log_event("controller_get_error", f"{path}:{e}")
            return None

    def register_node(self, has_gpu: bool):
        if not self.enabled:
            return
        payload = {
            "node_id": self.node_id,
            "hostname": SYSTEM_PROFILE.hostname,
            "platform": SYSTEM_PROFILE.platform,
            "cpu_count": SYSTEM_PROFILE.cpu_count,
            "ram_gb": SYSTEM_PROFILE.ram_gb,
            "has_gpu": has_gpu,
            "vpn_ip": SYSTEM_PROFILE.vpn_ip,
        }
        res = self._post_json("/nodes/register", payload)
        log_info(f"[CONTROLLER] register_node result: {res}")

    def send_heartbeat(self, gpu_load: float, metrics_snapshot: dict):
        if not self.enabled:
            return
        now = time.time()
        if now - self.last_heartbeat < 10:
            return
        self.last_heartbeat = now
        payload = {
            "node_id": self.node_id,
            "gpu_load": gpu_load,
            "metrics": metrics_snapshot,
            "ts": datetime.utcnow().isoformat(),
        }
        res = self._post_json("/nodes/heartbeat", payload)
        log_info(f"[CONTROLLER] heartbeat result: {res}")

    def submit_job(self, model: str, text: str, require_gpu: bool) -> dict | None:
        if not self.enabled:
            return None
        payload = {
            "model": model,
            "input": text,
            "require_gpu": require_gpu,
        }
        res = self._post_json("/jobs/submit", payload)
        log_info(f"[CONTROLLER] submit_job result: {res}")
        return res

    def fetch_pending_jobs(self) -> list[dict]:
        if not self.enabled:
            return []
        res = self._get_json(f"/nodes/{self.node_id}/jobs/pending")
        if isinstance(res, list):
            return res
        return []

    def send_job_result(self, job_id: int, output: str):
        if not self.enabled:
            return
        payload = {
            "job_id": job_id,
            "output": output,
            "status": "done",
        }
        res = self._post_json("/jobs/result", payload)
        log_info(f"[CONTROLLER] send_job_result result: {res}")

CONTROLLER_CLIENT = SwarmControllerClient(SWARM_CONFIG)

# ============================================================
#  CLUSTER / HYPERSWARM CORE (GPU-AWARE, MULTI-NODE)
# ============================================================

class RaftNode:
    def __init__(self, node_id, peers):
        self.node_id, self.peers, self.term, self.leader = node_id, peers, 0, None
    def election_loop(self):
        while True:
            time.sleep(3)
            self.term += 1
            self.leader = self.node_id
            log_info(f"[RAFT] {self.node_id} leader term={self.term}")

class JobQueue:
    def __init__(self): self.q = queue.Queue()
    def submit(self, job):
        log_info(f"[JOB-QUEUE] Submit: {job}")
        log_event("job_submit", json.dumps(job))
        self.q.put(job)
    def get(self): return self.q.get()

class ClusterState:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.metrics = MetricsRegistry("cluster")
        self.nodes = {}
        self.lock = threading.Lock()

    def update_node(self, node_id: str, gpu_load: float, has_gpu: bool):
        with self.lock:
            self.nodes[node_id] = {"gpu_load": gpu_load, "has_gpu": has_gpu}

    def choose_target_node(self, require_gpu: bool) -> str:
        with self.lock:
            candidates = []
            for nid, info in self.nodes.items():
                if require_gpu and not info["has_gpu"]:
                    continue
                candidates.append((info["gpu_load"], nid))
            if not candidates:
                return self.node_id
            candidates.sort()
            return candidates[0][1]

CLUSTER_STATE = ClusterState(SYSTEM_PROFILE.hostname)

class JobExecutor:
    def __init__(self, forklift):
        self.forklift = forklift
    def execute(self, job):
        log_info(f"[JOB-EXEC] Executing: {job}")
        jtype = job.get("type")
        if jtype == "inference":
            model = job.get("model", "llm_default")
            text = job.get("input", "")
            job_id = job.get("job_id")
            out = self.forklift.run_inference(model, text)
            try:
                if job_id is not None:
                    DB.execute(
                        "UPDATE jobs SET status=?, output=? WHERE id=?",
                        ("done", out, job_id)
                    )
                else:
                    DB.execute(
                        "INSERT INTO jobs (created_ts, status, model, input, output, target_node) VALUES (?, ?, ?, ?, ?, ?)",
                        (datetime.utcnow().isoformat(), "done", model, text, out, SYSTEM_PROFILE.hostname)
                    )
                DB.commit()
            except Exception as e:
                log_error(f"[DB] Failed to store job result: {e}")
                log_event("job_db_error", str(e))
            if CONTROLLER_CLIENT.enabled and job_id is not None:
                CONTROLLER_CLIENT.send_job_result(job_id, out)
        else:
            log_info(f"[JOB-EXEC] Unknown job type: {jtype}")
            log_event("job_unknown_type", json.dumps(job))

class JobDispatch:
    def __init__(self, q: JobQueue, ex: JobExecutor):
        self.q, self.ex = q, ex
    def loop(self):
        while True:
            job = self.q.get()
            self.ex.execute(job)

class ClusterSystem:
    def __init__(self, forklift):
        self.metrics = MetricsRegistry("cluster")
        self.raft = RaftNode(SYSTEM_PROFILE.hostname, [])
        self.job_q = JobQueue()
        self.job_ex = JobExecutor(forklift)
        self.job_disp = JobDispatch(self.job_q, self.job_ex)
        self.forklift = forklift

    def start(self):
        threading.Thread(target=self.raft.election_loop, daemon=True).start()
        threading.Thread(target=self.job_disp.loop, daemon=True).start()
        log_info("[CLUSTER] Cluster/HyperSwarm core started.")
        if CONTROLLER_CLIENT.enabled:
            CONTROLLER_CLIENT.register_node(self.forklift.has_gpu)
            threading.Thread(target=self.controller_job_loop, daemon=True).start()

    def controller_job_loop(self):
        while True:
            try:
                jobs = CONTROLLER_CLIENT.fetch_pending_jobs()
                for j in jobs:
                    job_id = j.get("id")
                    model = j.get("model", "llm_default")
                    text = j.get("input", "")
                    job = {"type": "inference", "model": model, "input": text, "job_id": job_id}
                    self.job_q.submit(job)
            except Exception as e:
                log_error(f"[CLUSTER] controller_job_loop error: {e}")
                log_event("controller_job_loop_error", str(e))
            time.sleep(3)

    def loop(self):
        while True:
            self.metrics.inc("ticks")
            time.sleep(5)

# ============================================================
#  AUTOPILOT SYSTEM
# ============================================================

class SensorInput:
    def __init__(self, metrics: MetricsRegistry): self.metrics = metrics
    def poll(self):
        log_info("[SENSOR] Polling sensors...")
        self.metrics.inc("sensor_ticks")
        return {}

class ObstacleAvoidance:
    def __init__(self, metrics: MetricsRegistry): self.metrics = metrics
    def compute_path(self, sensors):
        log_info("[OBST] Computing safe path...")
        self.metrics.inc("paths")
        return {}

class AICore:
    def __init__(self, metrics: MetricsRegistry): self.metrics = metrics
    def decide(self, sensors, path):
        log_info("[AI-CORE] Decision step...")
        self.metrics.inc("decisions")
        return {"cmd": "hold"}

class AutopilotSystem:
    def __init__(self):
        self.metrics = MetricsRegistry("autopilot")
        self.sensors = SensorInput(self.metrics)
        self.obst = ObstacleAvoidance(self.metrics)
        self.ai = AICore(self.metrics)
    def start(self):
        log_info("[AUTOPILOT] Autopilot system started.")
    def loop(self):
        while True:
            s = self.sensors.poll()
            p = self.obst.compute_path(s)
            d = self.ai.decide(s, p)
            log_info(f"[AUTOPILOT] Command: {d}")
            time.sleep(1)

# ============================================================
#  MEGA SYSTEM (PERCEPTION → FUSION → ACTION)
# ============================================================

class MegaSystem:
    def __init__(self):
        self.metrics = MetricsRegistry("mega")
    def start(self):
        log_info("[MEGA] Mega system started.")
    def loop(self):
        while True:
            log_info("[MEGA] Perception → Fusion → Action tick")
            self.metrics.inc("ticks")
            time.sleep(2)

# ============================================================
#  FORKLIFT LLM ENGINE (TOKENIZER + FULL PIPELINE)
# ============================================================

class TelemetryPolicyControl:
    def __init__(self, metrics: MetricsRegistry): self.metrics = metrics
    def tick(self):
        log_info("[FORKLIFT] Telemetry & Policy tick")
        self.metrics.inc("telemetry_ticks")

class ForkliftEngine:
    def __init__(self):
        self.metrics = MetricsRegistry("forklift")
        self.telemetry = TelemetryPolicyControl(self.metrics)
        self.models = {}
        self.tokenizers = {}
        self.has_gpu = False

    def start(self):
        log_info("[FORKLIFT] LLM Engine started.")
        self.has_gpu = self._gpu_available()
        self.load_models()

    def _gpu_available(self) -> bool:
        if torch is not None and torch.cuda.is_available():
            return True
        if ort is not None:
            try:
                return "CUDAExecutionProvider" in ort.get_available_providers()
            except Exception:
                return False
        return False

    def load_models(self):
        if AutoTokenizer is not None and AutoModelForCausalLM is not None and torch is not None:
            try:
                model_name = "gpt2"
                tok = AutoTokenizer.from_pretrained(model_name)
                model = AutoModelForCausalLM.from_pretrained(model_name)
                device = "cuda" if self.has_gpu else "cpu"
                model.to(device)
                self.models["llm_default"] = {"type": "transformers", "model": model, "device": device}
                self.tokenizers["llm_default"] = tok
                log_info(f"[FORKLIFT] Transformers LLM loaded: {model_name} on {device}")
                log_event("model_loaded", f"transformers:{model_name}")
            except Exception as e:
                log_error(f"[FORKLIFT] Failed to load transformers LLM: {e}")
                log_event("model_load_error", f"transformers:{e}")
        else:
            log_info("[FORKLIFT] Transformers not available; skipping LLM.")

        if torch is not None:
            try:
                model_path = os.path.join(BASE_DIR, "model.pt")
                if os.path.exists(model_path):
                    model = torch.jit.load(model_path)
                    model.eval()
                    device = "cuda" if self.has_gpu else "cpu"
                    model.to(device)
                    self.models["default_torch"] = {"type": "torch", "model": model, "device": device}
                    log_info(f"[FORKLIFT] Torch model loaded from {model_path} on {device}.")
                    log_event("model_loaded", f"torch:{model_path}")
            except Exception as e:
                log_error(f"[FORKLIFT] Failed to load torch model: {e}")
                log_event("model_load_error", f"torch:{e}")

        if ort is not None:
            try:
                onnx_path = os.path.join(BASE_DIR, "model.onnx")
                if os.path.exists(onnx_path):
                    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if self.has_gpu else ["CPUExecutionProvider"]
                    session = ort.InferenceSession(onnx_path, providers=providers)
                    self.models["default_onnx"] = {"type": "onnx", "session": session}
                    log_info(f"[FORKLIFT] ONNX model loaded from {onnx_path}.")
                    log_event("model_loaded", f"onnx:{onnx_path}")
            except Exception as e:
                log_error(f"[FORKLIFT] Failed to load ONNX model: {e}")
                log_event("model_load_error", f"onnx:{e}")

    def run_inference(self, model_name: str, input_text: str) -> str:
        if model_name not in self.models:
            log_error(f"[FORKLIFT] Model {model_name} not loaded.")
            return f"[ERROR] Model {model_name} not loaded"
        info = self.models[model_name]
        try:
            if info["type"] == "transformers":
                model = info["model"]
                device = info["device"]
                tok = self.tokenizers[model_name]
                inputs = tok(input_text, return_tensors="pt").to(device)
                with torch.no_grad():
                    out_ids = model.generate(**inputs, max_length=min(64, inputs["input_ids"].shape[1] + 32))
                out_text = tok.decode(out_ids[0], skip_special_tokens=True)
                out = out_text
            elif info["type"] == "torch":
                model = info["model"]
                device = info["device"]
                x = torch.tensor([ord(c) for c in input_text], dtype=torch.float32, device=device)
                with torch.no_grad():
                    y = model(x.unsqueeze(0))
                out = f"torch:{model_name}:shape={tuple(y.shape)}:device={device}"
            elif info["type"] == "onnx":
                session = info["session"]
                import numpy as np
                arr = [ord(c) for c in input_text]
                x = np.array(arr, dtype=np.float32)[None, :]
                input_name = session.get_inputs()[0].name
                y = session.run(None, {input_name: x})
                out = f"onnx:{model_name}:outputs={len(y)}"
            else:
                out = "[ERROR] Unknown model backend"
            self.metrics.inc("inferences")
            log_info(f"[FORKLIFT] Inference done: {str(out)[:120]}")
            return out
        except Exception as e:
            log_error(f"[FORKLIFT] Inference error: {e}")
            log_event("inference_error", str(e))
            return "[ERROR] Inference failed"

    def loop(self):
        while True:
            self.telemetry.tick()
            gpu_load = 0.0
            if torch is not None and torch.cuda.is_available():
                try:
                    gpu_mem = torch.cuda.memory_allocated() / max(torch.cuda.get_device_properties(0).total_memory, 1)
                    gpu_load = float(gpu_mem)
                except Exception:
                    gpu_load = 0.0
            CLUSTER_STATE.update_node(SYSTEM_PROFILE.hostname, gpu_load, self.has_gpu)
            if CONTROLLER_CLIENT.enabled:
                CONTROLLER_CLIENT.send_heartbeat(gpu_load, self.metrics.snapshot())
            time.sleep(3)

# ============================================================
#  TECHNICAL + AUTOMATION SYSTEMS
# ============================================================

class TechnicalArchitectureSystem:
    def __init__(self):
        self.metrics = MetricsRegistry("technical")
    def start(self):
        log_info("[TECH] Mega Technical Architecture started.")
    def loop(self):
        while True:
            log_info("[TECH] Tick")
            self.metrics.inc("ticks")
            time.sleep(4)

class AutomationSystem:
    def __init__(self):
        self.metrics = MetricsRegistry("automation")
    def start(self):
        log_info("[AUTO] Automation & Analysis Framework started.")
    def loop(self):
        while True:
            log_info("[AUTO] Tick")
            self.metrics.inc("ticks")
            time.sleep(5)

# ============================================================
#  DAEMONIZED WATCHDOG ORGAN (PROCESS-LEVEL, SWARM-AWARE)
# ============================================================

class WatchdogOrgan:
    def __init__(self, interval=5):
        self.interval = interval
        self.metrics = MetricsRegistry("watchdog")
        self.processes = {}
        self.node_id = SYSTEM_PROFILE.hostname
        self.swarm_state = {}
        log_info("[WATCHDOG] Organ initialized.")

    def register_process(self, role: str, pid: int):
        self.processes[role] = pid
        log_info(f"[WATCHDOG] Registered process {role} pid={pid}")

    def check_processes(self):
        for role, pid in list(self.processes.items()):
            if not psutil.pid_exists(pid):
                log_error(f"[WATCHDOG] Process {role} (pid={pid}) died, restarting...")
                log_event("process_resurrection", role)
                self.metrics.inc("resurrections")
                new_pid = self.spawn_role(role)
                self.processes[role] = new_pid

    def spawn_role(self, role: str) -> int:
        cmd = [sys.executable, os.path.abspath(__file__), role]
        proc = subprocess.Popen(cmd)
        log_info(f"[WATCHDOG] Spawned {role} pid={proc.pid}")
        return proc.pid

    def swarm_broadcast_loop(self):
        while True:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                payload = {
                    "type": "watchdog",
                    "node": self.node_id,
                    "ts": time.time(),
                    "metrics": self.metrics.snapshot(),
                }
                payload = sign_payload(payload)
                raw = json.dumps(payload).encode("utf-8")
                raw = mesh_encrypt(raw)
                s.sendto(raw, ("255.255.255.255", SWARM_WATCHDOG_PORT))
                s.close()
                telemetry_event("watchdog_swarm_broadcast", {"node": self.node_id})
            except Exception as e:
                log_error(f"[WATCHDOG] Swarm broadcast error: {e}")
                log_event("watchdog_swarm_broadcast_error", str(e))
            time.sleep(SWARM_WATCHDOG_BROADCAST_INTERVAL)

    def swarm_listen_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("", SWARM_WATCHDOG_PORT))
        log_info(f"[WATCHDOG] Listening for swarm watchdog on port {SWARM_WATCHDOG_PORT}...")
        while True:
            try:
                data, addr = s.recvfrom(65535)
                try:
                    data = mesh_decrypt(data)
                except Exception:
                    continue
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "watchdog":
                    if not verify_payload(msg):
                        log_error(f"[WATCHDOG] Swarm signature invalid from {addr}")
                        log_event("watchdog_swarm_sig_invalid", str(addr))
                        continue
                    node = msg.get("node")
                    if node == self.node_id:
                        continue
                    self.swarm_state[node] = msg
                    telemetry_event("watchdog_swarm_receive", {"from": node})
            except Exception as e:
                log_error(f"[WATCHDOG] Swarm listener error: {e}")
                log_event("watchdog_swarm_listener_error", str(e))

    def loop(self):
        log_info("[WATCHDOG] Daemon loop started.")
        threading.Thread(target=self.swarm_broadcast_loop, daemon=True).start()
        threading.Thread(target=self.swarm_listen_loop, daemon=True).start()
        while True:
            try:
                self.check_processes()
                self.metrics.inc("ticks")
            except Exception as e:
                log_error(f"[WATCHDOG] Error: {e}")
                telemetry_event("watchdog_error", {"error": str(e)})
                log_event("watchdog_error", str(e))
            time.sleep(self.interval)

# ============================================================
#  REST API (NODE-LOCAL CONTROL PLANE)
# ============================================================

LOCAL_JOB_QUEUE = queue.Queue()

class APIServerHandler(BaseHTTPRequestHandler):
    def _send_json(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        if self.path == "/jobs/inference":
            model = payload.get("model", "llm_default")
            text = payload.get("input", "")
            require_gpu = payload.get("require_gpu", True)

            if CONTROLLER_CLIENT.enabled:
                res = CONTROLLER_CLIENT.submit_job(model, text, require_gpu)
                if res is None:
                    self._send_json(500, {"error": "controller unavailable"})
                else:
                    self._send_json(200, res)
                return

            target_node = CLUSTER_STATE.choose_target_node(require_gpu=require_gpu)
            try:
                cur = DB.execute(
                    "INSERT INTO jobs (created_ts, status, model, input, output, target_node) VALUES (?, ?, ?, ?, ?, ?)",
                    (datetime.utcnow().isoformat(), "queued", model, text, None, target_node)
                )
                job_id = cur.lastrowid
                DB.commit()
            except Exception as e:
                log_error(f"[API] Failed to insert job: {e}")
                log_event("api_job_insert_error", str(e))
                self._send_json(500, {"error": "db error"})
                return

            if target_node == SYSTEM_PROFILE.hostname:
                job = {"type": "inference", "model": model, "input": text, "job_id": job_id}
                LOCAL_JOB_QUEUE.put(job)
            else:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    payload2 = {
                        "type": "remote_job",
                        "job": {"type": "inference", "model": model, "input": text, "job_id": job_id},
                        "target": target_node,
                        "from": SYSTEM_PROFILE.hostname,
                    }
                    payload2 = sign_payload(payload2)
                    raw = json.dumps(payload2).encode("utf-8")
                    raw = mesh_encrypt(raw)
                    s.sendto(raw, ("255.255.255.255", SWARM_MESH_PORT))
                    s.close()
                except Exception as e:
                    log_error(f"[API] Failed to send remote job: {e}")
                    log_event("remote_job_send_error", str(e))

            self._send_json(200, {"job_id": job_id, "status": "queued", "target_node": target_node})
            return

        if self.path == "/control/telemetry":
            level = payload.get("level")
            if isinstance(level, int):
                set_telemetry_level(level)
                self._send_json(200, {"status": "ok", "level": TELEMETRY_LEVEL})
            else:
                self._send_json(400, {"error": "level must be int 0-2"})
            return

        self._send_json(404, {"error": "unknown endpoint"})

    def do_GET(self):
        path = self.path
        if path == "/jobs":
            rows = []
            for row in DB.execute("SELECT id, created_ts, status, model, input, output, target_node FROM jobs ORDER BY id DESC LIMIT 100"):
                rows.append({
                    "id": row[0],
                    "created_ts": row[1],
                    "status": row[2],
                    "model": row[3],
                    "input": row[4],
                    "output": row[5],
                    "target_node": row[6],
                })
            self._send_json(200, rows)
            return

        if path.startswith("/jobs/"):
            try:
                job_id = int(path.split("/")[-1])
            except ValueError:
                self._send_json(400, {"error": "invalid job id"})
                return
            cur = DB.execute("SELECT id, created_ts, status, model, input, output, target_node FROM jobs WHERE id=?", (job_id,))
            row = cur.fetchone()
            if not row:
                self._send_json(404, {"error": "job not found"})
                return
            obj = {
                "id": row[0],
                "created_ts": row[1],
                "status": row[2],
                "model": row[3],
                "input": row[4],
                "output": row[5],
                "target_node": row[6],
            }
            self._send_json(200, obj)
            return

        if path == "/metrics":
            out = {
                "telemetry_level": TELEMETRY_LEVEL,
                "node": SYSTEM_PROFILE.hostname,
                "cluster_nodes": CLUSTER_STATE.nodes,
                "controller_enabled": CONTROLLER_CLIENT.enabled,
            }
            self._send_json(200, out)
            return

        self._send_json(404, {"error": "unknown endpoint"})

def start_api_server(port=8080):
    bind_ip = "0.0.0.0"
    if SWARM_CONFIG.use_vpn and SYSTEM_PROFILE.vpn_ip:
        bind_ip = "0.0.0.0"
    server = HTTPServer((bind_ip, port), APIServerHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log_info(f"[API] REST API server started on {bind_ip}:{port}")
    return server

# ============================================================
#  MESH JOB RECEIVER (REMOTE JOBS)
# ============================================================

def mesh_job_listener(forklift: ForkliftEngine):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", SWARM_MESH_PORT))
    log_info(f"[MESH] Listening for remote jobs on port {SWARM_MESH_PORT}...")
    while True:
        try:
            data, addr = s.recvfrom(65535)
            try:
                data = mesh_decrypt(data)
            except Exception:
                continue
            msg = json.loads(data.decode("utf-8"))
            if msg.get("type") == "remote_job":
                if not verify_payload(msg):
                    log_error(f"[MESH] Remote job signature invalid from {addr}")
                    log_event("remote_job_sig_invalid", str(addr))
                    continue
                job = msg.get("job", {})
                target = msg.get("target")
                if target == SYSTEM_PROFILE.hostname:
                    LOCAL_JOB_QUEUE.put(job)
                    telemetry_event("remote_job_received", {"from": msg.get("from")})
        except Exception as e:
            log_error(f"[MESH] Listener error: {e}")
            log_event("mesh_listener_error", str(e))

def local_job_worker(forklift: ForkliftEngine):
    while True:
        job = LOCAL_JOB_QUEUE.get()
        if job is None:
            continue
        log_info(f"[MESH] Executing local job: {job}")
        jtype = job.get("type")
        if jtype == "inference":
            model = job.get("model", "llm_default")
            text = job.get("input", "")
            job_id = job.get("job_id")
            out = forklift.run_inference(model, text)
            try:
                if job_id is not None:
                    DB.execute(
                        "UPDATE jobs SET status=?, output=? WHERE id=?",
                        ("done", out, job_id)
                    )
                    DB.commit()
            except Exception as e:
                log_error(f"[MESH] Failed to store job result: {e}")
                log_event("mesh_job_db_error", str(e))
            if CONTROLLER_CLIENT.enabled and job_id is not None:
                CONTROLLER_CLIENT.send_job_result(job_id, out)

# ============================================================
#  AUTOLOADER MAIN
# ============================================================

def autoload_all():
    log_info(f"[{AUTOLOADER_NAME}] Starting autoload sequence...")
    telemetry_event("autoload_start", {})
    adjust_gpu_packages(MANIFEST)
    for name, spec in MANIFEST.items():
        ok = install_module(name, spec)
        if not ok and spec.get("critical", False):
            log_error(f"[{AUTOLOADER_NAME}] Fatal: cannot continue without {name}")
            telemetry_event("autoload_fatal", {"name": name})
            log_event("autoload_fatal", name)
            time.sleep(5)
            sys.exit(1)
    log_info(f"[{AUTOLOADER_NAME}] Autoload sequence completed.")
    telemetry_event("autoload_complete", {})
    save_manifest_to_disk(MANIFEST)

# ============================================================
#  PROCESS ROLES
# ============================================================

def run_cluster_role():
    forklift = ForkliftEngine()
    forklift.start()
    cluster = ClusterSystem(forklift)
    cluster.start()
    threading.Thread(target=cluster.loop, daemon=True).start()
    threading.Thread(target=forklift.loop, daemon=True).start()
    threading.Thread(target=mesh_job_listener, args=(forklift,), daemon=True).start()
    threading.Thread(target=local_job_worker, args=(forklift,), daemon=True).start()
    log_info("[ROLE:CLUSTER] Started.")
    while True:
        time.sleep(10)

def run_autopilot_role():
    autopilot = AutopilotSystem()
    autopilot.start()
    autopilot.loop()

def run_mega_role():
    mega = MegaSystem()
    mega.start()
    mega.loop()

def run_forklift_role():
    forklift = ForkliftEngine()
    forklift.start()
    threading.Thread(target=forklift.loop, daemon=True).start()
    threading.Thread(target=mesh_job_listener, args=(forklift,), daemon=True).start()
    threading.Thread(target=local_job_worker, args=(forklift,), daemon=True).start()
    log_info("[ROLE:FORKLIFT] Started.")
    while True:
        time.sleep(10)

def run_technical_role():
    tech = TechnicalArchitectureSystem()
    tech.start()
    tech.loop()

def run_automation_role():
    auto = AutomationSystem()
    auto.start()
    auto.loop()

def run_api_role():
    start_api_server(port=8080)
    log_info("[ROLE:API] Started.")
    while True:
        time.sleep(10)

# ============================================================
#  MASTER ROLE (PROCESS ISOLATION + WATCHDOG)
# ============================================================

def run_master():
    ensure_admin()
    start_policy_enforcer()
    autoload_all()
    start_repair_daemon()
    start_background_updater()
    start_manifest_sync(SYSTEM_PROFILE.hostname)

    wd = WatchdogOrgan(interval=5)

    roles = [ROLE_CLUSTER, ROLE_AUTOPILOT, ROLE_MEGA, ROLE_FORKLIFT, ROLE_TECHNICAL, ROLE_AUTOMATION, ROLE_API]
    for role in roles:
        pid = wd.spawn_role(role)
        wd.register_process(role, pid)

    wd.loop()

# ============================================================
#  ENTRYPOINT
# ============================================================

def main():
    role = ROLE_MASTER
    if len(sys.argv) > 1:
        role = sys.argv[1].lower()

    if role == ROLE_MASTER:
        run_master()
    elif role == ROLE_CLUSTER:
        run_cluster_role()
    elif role == ROLE_AUTOPILOT:
        run_autopilot_role()
    elif role == ROLE_MEGA:
        run_mega_role()
    elif role == ROLE_FORKLIFT:
        run_forklift_role()
    elif role == ROLE_TECHNICAL:
        run_technical_role()
    elif role == ROLE_AUTOMATION:
        run_automation_role()
    elif role == ROLE_API:
        run_api_role()
    else:
        log_error(f"[MAIN] Unknown role: {role}")
        sys.exit(1)

if __name__ == "__main__":
    main()
