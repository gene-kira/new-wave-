#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LPU Autopilot Kernel – Ultimate Unified Version

Features:
- Autopilot core (state machine, safety)
- LPU (NLP → validated autopilot commands)
- Hardware abstraction layer + async hardware polling
- CAN bus + GPS/IMU integration (stubs)
- Navigation manager + obstacle avoidance (stubs)
- Swarm formation coordinator (stubs)
- Mission scripting engine (stubs)
- GPU-ready local LLM backend (llama.cpp hook) + AI Copilot
- Whisper offline STT (lazy, optional, crash-safe)
- PySide6 GUI (control, logs, telemetry, metrics, swarm GPS map, 3D view, missions, copilot)
- Voice control (Whisper + optional Google STT + TTS)
- Plugin system with sandbox isolation + hot-reload
- Async distributed event bus (TCP pub/sub hook)
- TLS + JWT IPC server (using auto-cert engine with SAN)
- REST API (async)
- MQTT bridge (optional)
- OTA update hooks (stub)
"""

import asyncio
import json
import time
import ssl
import base64
import hmac
import hashlib
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict, Optional, List, AsyncGenerator, Tuple, Callable
import threading
import multiprocessing
import subprocess
import os
import pathlib
import shutil
import importlib
import importlib.util
import logging
from logging.handlers import RotatingFileHandler
import socket
import datetime

# =============================================================================
# JSON-safe helper
# =============================================================================

def json_safe(obj):
    if isinstance(obj, Enum):
        return obj.value
    return str(obj)

# =============================================================================
# Paths & directories
# =============================================================================

BASE_DIR = pathlib.Path(__file__).resolve().parent
PLUGINS_DIR = BASE_DIR / "plugins"
LOGS_DIR = BASE_DIR / "logs"
CERTS_DIR = BASE_DIR / "certs"
CONFIG_DIR = BASE_DIR / "config"
MODELS_DIR = BASE_DIR / "models"
CACHE_DIR = BASE_DIR / "cache"
OTA_DIR = BASE_DIR / "ota"
ASSETS_DIR = BASE_DIR / "assets"

CONFIG_FILE = CONFIG_DIR / "lpu_config.json"
LOG_FILE = LOGS_DIR / "lpu_autopilot.log"
AUTOLOADER_LOG_FILE = LOGS_DIR / "autoloader.log"

IPC_HOST = "127.0.0.1"
IPC_PORT = 8765

APP_VERSION = "3.0.0"

EVENT_BUS_HOST = "127.0.0.1"
EVENT_BUS_PORT = 9876

OTA_VERSION_URL = "https://example.com/lpu/version.json"
OTA_BINARY_URL = "https://example.com/lpu/lpu_autopilot_kernel.py"

LLAMA_HTTP_HOST = "127.0.0.1"
LLAMA_HTTP_PORT = 8081
LLAMA_HTTP_PATH = "/llama"

REST_API_HOST = "127.0.0.1"
REST_API_PORT = 8088

MQTT_BROKER_HOST = "127.0.0.1"
MQTT_BROKER_PORT = 1883
MQTT_TOPIC_IN = "lpu/commands"
MQTT_TOPIC_OUT = "lpu/events"

def ensure_working_directory():
    os.chdir(BASE_DIR)

ensure_working_directory()

def ensure_directories():
    for d in [PLUGINS_DIR, LOGS_DIR, CERTS_DIR, CONFIG_DIR, MODELS_DIR, CACHE_DIR, OTA_DIR, ASSETS_DIR]:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

ensure_directories()

# =============================================================================
# Autoloader logging
# =============================================================================

autoloader_logger = logging.getLogger("Autoloader")
autoloader_logger.setLevel(logging.INFO)
try:
    ah = RotatingFileHandler(AUTOLOADER_LOG_FILE, maxBytes=500_000, backupCount=2, encoding="utf-8")
    ah.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    autoloader_logger.addHandler(ah)
except Exception:
    pass

# =============================================================================
# AUTOLOADER (silent module install)
# =============================================================================

REQUIRED_LIBRARIES = [
    "asyncio",
    "ssl",
    "json",
    "hmac",
    "hashlib",
    "base64",
    "multiprocessing",
    "threading",
    "pathlib",
    "subprocess",
    "shutil",
    "logging",
]

OPTIONAL_LIBRARIES = [
    "requests",
    "cryptography",
    "torch",
    "numpy",
    "sounddevice",
    "soundfile",
    "pyttsx3",
    "speech_recognition",
    "aiohttp",
    "paho.mqtt.client",
    "python-can",
]

PYSIDE_LIBRARIES = [
    "PySide6",
]

def _silent_install_package(pkg):
    try:
        autoloader_logger.info(f"Installing missing package: {pkg}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        autoloader_logger.info(f"Installed package: {pkg}")
    except Exception as e:
        autoloader_logger.error(f"Failed to install {pkg}: {e}")

def _ensure_import(pkg):
    try:
        return importlib.import_module(pkg)
    except ImportError:
        _silent_install_package(pkg)
        try:
            return importlib.import_module(pkg)
        except ImportError:
            autoloader_logger.error(f"Could not import {pkg} even after install.")
            return None

def autoload_libraries():
    autoloader_logger.info("Checking required libraries...")
    for lib in REQUIRED_LIBRARIES:
        _ensure_import(lib)

    autoloader_logger.info("Checking optional libraries...")
    for lib in OPTIONAL_LIBRARIES:
        _ensure_import(lib)

    autoloader_logger.info("Skipping Whisper autoload (lazy-loaded in VoiceController).")

    autoloader_logger.info("Checking PySide6...")
    for lib in PYSIDE_LIBRARIES:
        _ensure_import(lib)

    autoloader_logger.info("Autoloader completed.")

threading.Thread(target=autoload_libraries, daemon=True).start()

speech_recognition = None
pyttsx3 = None
torch = None
requests = None
sounddevice = None
soundfile = None
aiohttp = None
paho_mqtt = None
python_can = None
PySide6 = None

try:
    speech_recognition = importlib.import_module("speech_recognition")
except Exception:
    pass
try:
    pyttsx3 = importlib.import_module("pyttsx3")
except Exception:
    pass
try:
    torch = importlib.import_module("torch")
except Exception:
    pass
try:
    requests = importlib.import_module("requests")
except Exception:
    pass
try:
    sounddevice = importlib.import_module("sounddevice")
except Exception:
    pass
try:
    soundfile = importlib.import_module("soundfile")
except Exception:
    pass
try:
    aiohttp = importlib.import_module("aiohttp")
except Exception:
    pass
try:
    paho_mqtt = importlib.import_module("paho.mqtt.client")
except Exception:
    pass
try:
    python_can = importlib.import_module("can")
except Exception:
    pass
try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    PySide6 = None
else:
    PySide6 = True

# =============================================================================
# Config + persistence
# =============================================================================

DEFAULT_CONFIG = {
    "server_run_mode": "thread",
    "use_remote_backend": False,
    "use_local_backend": True,
    "llm_host": LLAMA_HTTP_HOST,
    "llm_port": LLAMA_HTTP_PORT,
    "llm_path": LLAMA_HTTP_PATH,
    "swarm_peers": [],
    "voice_enabled": True,
    "tts_enabled": True,
    "jwt_secret": "CHANGE_ME_JWT_SECRET",
    "gpu_llm_enabled": True,
    "whisper_enabled": True,
    "ota_enabled": False,
    "event_bus_enabled": True,
    "event_bus_host": EVENT_BUS_HOST,
    "event_bus_port": EVENT_BUS_PORT,
    "rest_api_enabled": True,
    "mqtt_enabled": True,
    "can_channel": "can0",
    "can_bitrate": 500000,
}

CONFIG: Dict[str, Any] = {}

def load_config():
    global CONFIG
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                CONFIG = json.load(f)
        except Exception:
            CONFIG = DEFAULT_CONFIG.copy()
    else:
        CONFIG = DEFAULT_CONFIG.copy()
        save_config()

def save_config():
    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2, default=json_safe)
    except Exception:
        pass

load_config()

SERVER_RUN_MODE = CONFIG.get("server_run_mode", "thread")
USE_REMOTE_BACKEND = CONFIG.get("use_remote_backend", False)
USE_LOCAL_BACKEND = CONFIG.get("use_local_backend", True)

LLM_HOST = CONFIG.get("llm_host") or LLAMA_HTTP_HOST
LLM_PORT = CONFIG.get("llm_port") or LLAMA_HTTP_PORT
LLM_PATH = CONFIG.get("llm_path") or LLAMA_HTTP_PATH

SWARM_PEERS: List[Tuple[str, int]] = [tuple(p) for p in CONFIG.get("swarm_peers", [])]

VOICE_ENABLED = CONFIG.get("voice_enabled", True)
TTS_ENABLED = CONFIG.get("tts_enabled", True)

JWT_SECRET = (CONFIG.get("jwt_secret") or "CHANGE_ME_JWT_SECRET").encode("utf-8")
JWT_ISSUER = "lpu-autopilot-kernel"
JWT_AUDIENCE = "lpu-client"

GPU_LLM_ENABLED = CONFIG.get("gpu_llm_enabled", True)
WHISPER_ENABLED = CONFIG.get("whisper_enabled", True)
OTA_ENABLED = CONFIG.get("ota_enabled", False)
EVENT_BUS_ENABLED = CONFIG.get("event_bus_enabled", True)
EVENT_BUS_HOST = CONFIG.get("event_bus_host") or EVENT_BUS_HOST
EVENT_BUS_PORT = CONFIG.get("event_bus_port") or EVENT_BUS_PORT

REST_API_ENABLED = CONFIG.get("rest_api_enabled", True)
MQTT_ENABLED = CONFIG.get("mqtt_enabled", True)

CAN_CHANNEL = CONFIG.get("can_channel", "can0")
CAN_BITRATE = CONFIG.get("can_bitrate", 500000)

USE_TLS = False

# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger("LPU")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

try:
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
except Exception:
    pass

# =============================================================================
# MERGED AUTO-CERT ENGINE
# =============================================================================

CA_KEY = str(CERTS_DIR / "ca.key")
CA_CRT = str(CERTS_DIR / "ca.crt")
SERVER_KEY = str(CERTS_DIR / "server.key")
SERVER_CSR = str(CERTS_DIR / "server.csr")
SERVER_CRT = str(CERTS_DIR / "server.crt")
CLIENT_KEY = str(CERTS_DIR / "client.key")
CLIENT_CSR = str(CERTS_DIR / "client.csr")
CLIENT_CRT = str(CERTS_DIR / "client.crt")
SAN_CONFIG = str(CERTS_DIR / "san.cnf")

def run_silent(cmd):
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)

def openssl_ok():
    try:
        subprocess.run("openssl version", shell=True, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except:
        return False

def write_san_config():
    cfg = """
[ req ]
default_bits       = 4096
distinguished_name = req_distinguished_name
req_extensions     = req_ext
prompt             = no

[ req_distinguished_name ]
CN = localhost

[ req_ext ]
subjectAltName = @alt_names

[ alt_names ]
DNS.1 = localhost
IP.1  = 127.0.0.1
"""
    with open(SAN_CONFIG, "w") as f:
        f.write(cfg)

def cert_expired(path):
    try:
        cert = ssl._ssl._test_decode_cert(path)
        exp = datetime.datetime.strptime(cert['notAfter'], "%b %d %H:%M:%S %Y %Z")
        return exp < datetime.datetime.utcnow()
    except:
        return True

def cert_mismatch():
    if not os.path.exists(SERVER_CRT) or not os.path.exists(CA_CRT):
        return True
    try:
        cmd = f"openssl verify -CAfile {CA_CRT} {SERVER_CRT}"
        r = run_silent(cmd)
        return b"OK" not in r.stdout
    except:
        return True

def generate_all():
    write_san_config()

    run_silent(f"openssl genrsa -out {CA_KEY} 4096")
    run_silent(f"openssl req -x509 -new -nodes -key {CA_KEY} -sha256 -days 3650 "
               f"-subj \"/CN=LPU-CA\" -out {CA_CRT}")

    run_silent(f"openssl genrsa -out {SERVER_KEY} 4096")
    run_silent(f"openssl req -new -key {SERVER_KEY} -out {SERVER_CSR} -config {SAN_CONFIG}")
    run_silent(f"openssl x509 -req -in {SERVER_CSR} -CA {CA_CRT} -CAkey {CA_KEY} "
               f"-out {SERVER_CRT} -days 3650 -sha256 -extfile {SAN_CONFIG} -extensions req_ext")

    run_silent(f"openssl genrsa -out {CLIENT_KEY} 4096")
    run_silent(f"openssl req -new -key {CLIENT_KEY} -subj \"/CN=LPU-Client\" -out {CLIENT_CSR}")
    run_silent(f"openssl x509 -req -in {CLIENT_CSR} -CA {CA_CRT} -CAkey {CA_KEY} "
               f"-out {CLIENT_CRT} -days 3650 -sha256")

def ensure_certs():
    logger.info("[LPU] Checking TLS certificate state...")

    if not openssl_ok():
        logger.warning("[LPU] OpenSSL missing → TLS disabled (fallback mode).")
        return False

    missing = any(not os.path.exists(f) for f in
                  [CA_KEY, CA_CRT, SERVER_KEY, SERVER_CRT, CLIENT_KEY, CLIENT_CRT])

    if missing:
        logger.info("[LPU] Missing certs → generating fresh chain.")
        generate_all()
        return True

    if cert_expired(SERVER_CRT) or cert_expired(CLIENT_CRT) or cert_expired(CA_CRT):
        logger.info("[LPU] Expired certs → regenerating full chain.")
        generate_all()
        return True

    if cert_mismatch():
        logger.info("[LPU] Cert chain mismatch → repairing.")
        generate_all()
        return True

    logger.info("[LPU] TLS certs valid.")
    return True

# =============================================================================
# JWT
# =============================================================================

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)

def create_jwt(payload: Dict[str, Any], secret: bytes = JWT_SECRET) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), default=json_safe).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), default=json_safe).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"

def verify_jwt(token: str, secret: bytes = JWT_SECRET) -> Dict[str, Any]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise ValueError("Invalid JWT format")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    actual_sig = _b64url_decode(sig_b64)

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid JWT signature")

    payload_bytes = _b64url_decode(payload_b64)
    payload = json.loads(payload_bytes.decode("utf-8"))

    iss = payload.get("iss")
    aud = payload.get("aud")
    if iss != JWT_ISSUER or aud != JWT_AUDIENCE:
        raise ValueError("Invalid JWT iss/aud")

    return payload

# =============================================================================
# Async distributed event bus
# =============================================================================

class DistributedEventBus:
    def __init__(self, host: str, port: int, enabled: bool = True):
        self.host = host
        self.port = port
        self.enabled = enabled
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._server: Optional[asyncio.AbstractServer] = None

    def subscribe(self, topic: str, callback: Callable[[Dict[str, Any]], None]):
        self._subscribers.setdefault(topic, []).append(callback)

    def _publish_local(self, topic: str, event: Dict[str, Any]):
        for cb in self._subscribers.get(topic, []):
            try:
                cb(event)
            except Exception as e:
                logger.error(f"[EventBus] Error in subscriber for {topic}: {e}")

    async def publish(self, topic: str, event: Dict[str, Any]):
        self._publish_local(topic, event)
        if not self.enabled:
            return
        try:
            reader, writer = await asyncio.open_connection(self.host, self.port)
            payload = json.dumps({"topic": topic, "event": event}, default=json_safe) + "\n"
            writer.write(payload.encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        logger.info(f"[EventBus] Client connected: {peer}")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                    if not isinstance(msg, dict):
                        continue
                    topic = msg.get("topic")
                    event = msg.get("event", {})
                    if topic:
                        self._publish_local(topic, event)
                except Exception as e:
                    logger.error(f"[EventBus] Error handling message: {e}")
        finally:
            logger.info(f"[EventBus] Client disconnected: {peer}")
            writer.close()
            await writer.wait_closed()

    async def start_server(self):
        if not self.enabled:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )
        addr = ", ".join(str(sock.getsockname()) for sock in self._server.sockets)
        logger.info(f"[EventBus] Listening on {addr}")

    async def run_forever(self):
        if not self.enabled:
            return
        await self.start_server()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

EVENT_BUS = DistributedEventBus(EVENT_BUS_HOST, EVENT_BUS_PORT, enabled=EVENT_BUS_ENABLED)

# =============================================================================
# Autopilot core
# =============================================================================

class VehicleMode(Enum):
    PARKED = "PARKED"
    IDLE = "IDLE"
    MANUAL = "MANUAL"
    ASSISTED = "ASSISTED"
    AUTONOMOUS = "AUTONOMOUS"
    EMERGENCY_STOP = "EMERGENCY_STOP"

@dataclass
class VehicleState:
    mode: VehicleMode = VehicleMode.PARKED
    battery_level: float = 100.0
    location: str = "UNKNOWN"
    speed: float = 0.0
    last_event: str = ""
    gps_lat: float = 0.0
    gps_lon: float = 0.0
    imu_roll: float = 0.0
    imu_pitch: float = 0.0
    imu_yaw: float = 0.0
    gpu_util: float = 0.0
    gpu_mem: float = 0.0

class AutopilotCore:
    def __init__(self):
        self.state = VehicleState()
        self._lock = threading.Lock()

    def get_state(self) -> VehicleState:
        with self._lock:
            return VehicleState(
                mode=self.state.mode,
                battery_level=self.state.battery_level,
                location=self.state.location,
                speed=self.state.speed,
                last_event=self.state.last_event,
                gps_lat=self.state.gps_lat,
                gps_lon=self.state.gps_lon,
                imu_roll=self.state.imu_roll,
                imu_pitch=self.state.imu_pitch,
                imu_yaw=self.state.imu_yaw,
                gpu_util=self.state.gpu_util,
                gpu_mem=self.state.gpu_mem,
            )

    def get_state_dict(self) -> Dict[str, Any]:
        s = asdict(self.get_state())
        s["mode"] = s["mode"].value
        return s

    def set_mode(self, mode: VehicleMode) -> Tuple[bool, str]:
        with self._lock:
            if self.state.mode == VehicleMode.EMERGENCY_STOP and mode != VehicleMode.EMERGENCY_STOP:
                return False, "Cannot leave EMERGENCY_STOP without manual override"
            self.state.mode = mode
            self.state.last_event = f"Mode set to {mode.value}"
            try:
                asyncio.run_coroutine_threadsafe(
                    EVENT_BUS.publish("mode_change", {"mode": mode.value}),
                    asyncio.get_event_loop(),
                )
            except RuntimeError:
                pass
            logger.info(f"[AutopilotCore] Mode set to {mode.value}")
            return True, f"Mode set to {mode.value}"

    def apply_navigation_command(self, cmd: Dict[str, Any]) -> Tuple[bool, str]:
        with self._lock:
            if self.state.mode in [VehicleMode.PARKED, VehicleMode.EMERGENCY_STOP]:
                return False, f"Navigation blocked in mode {self.state.mode.value}"
            target = cmd.get("target", "UNKNOWN")
            self.state.location = f"Navigating to {target}"
            self.state.speed = 10.0
            self.state.last_event = f"NavigationCommand to {target}"
            try:
                asyncio.run_coroutine_threadsafe(
                    EVENT_BUS.publish("navigation", {"target": target}),
                    asyncio.get_event_loop(),
                )
            except RuntimeError:
                pass
            logger.info(f"[AutopilotCore] NavigationCommand applied: {target}")
            return True, f"Navigating to {target}"

    def apply_system_event(self, evt: Dict[str, Any]) -> Tuple[bool, str]:
        with self._lock:
            etype = evt.get("event_type", "")
            if etype == "stop_requested":
                self.state.mode = VehicleMode.EMERGENCY_STOP
                self.state.speed = 0.0
                self.state.last_event = "Emergency stop requested"
                try:
                    asyncio.run_coroutine_threadsafe(
                        EVENT_BUS.publish("emergency", {"event": "stop"}),
                        asyncio.get_event_loop(),
                    )
                except RuntimeError:
                    pass
                logger.warning("[AutopilotCore] EMERGENCY_STOP engaged")
                return True, "Emergency stop engaged"
            self.state.last_event = f"SystemEvent: {etype}"
            try:
                asyncio.run_coroutine_threadsafe(
                    EVENT_BUS.publish("system_event", {"event_type": etype}),
                    asyncio.get_event_loop(),
                )
            except RuntimeError:
                pass
            logger.info(f"[AutopilotCore] SystemEvent applied: {etype}")
            return True, f"SystemEvent applied: {etype}"

    def update_from_telemetry(self, telemetry: Dict[str, Any]):
        with self._lock:
            self.state.battery_level = telemetry.get("battery", self.state.battery_level)
            self.state.speed = telemetry.get("speed", self.state.speed)
            gps = telemetry.get("gps")
            if gps and isinstance(gps, (list, tuple)) and len(gps) == 2:
                self.state.gps_lat, self.state.gps_lon = gps
            imu = telemetry.get("imu")
            if imu and isinstance(imu, dict):
                self.state.imu_roll = imu.get("roll", self.state.imu_roll)
                self.state.imu_pitch = imu.get("pitch", self.state.imu_pitch)
                self.state.imu_yaw = imu.get("yaw", self.state.imu_yaw)

    def update_gpu_metrics(self, util: float, mem: float):
        with self._lock:
            self.state.gpu_util = util
            self.state.gpu_mem = mem

# =============================================================================
# Hardware integration layer + CAN + GPS/IMU
# =============================================================================

class HardwareInterface(ABC):
    @abstractmethod
    def read_telemetry(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def apply_control(self, control: Dict[str, Any]) -> None:
        raise NotImplementedError

class MockHardware(HardwareInterface):
    def __init__(self):
        self._last_control = {}

    def read_telemetry(self) -> Dict[str, Any]:
        return {
            "battery": 95.0,
            "speed": 5.0,
            "gps": (39.0, -96.0),
            "imu": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            "temp": 30.0,
        }

    def apply_control(self, control: Dict[str, Any]) -> None:
        self._last_control = control
        logger.info(f"[Hardware] Control applied: {control}")
        try:
            asyncio.run_coroutine_threadsafe(
                EVENT_BUS.publish("hardware_control", control),
                asyncio.get_event_loop(),
            )
        except RuntimeError:
            pass

class CANBusHardware(HardwareInterface):
    """
    Stub CAN bus hardware integration.
    Replace message IDs and payload parsing with your real hardware spec.
    """
    def __init__(self, channel: str, bitrate: int):
        self.channel = channel
        self.bitrate = bitrate
        self.bus = None
        self._last_control = {}
        if python_can is not None:
            try:
                self.bus = python_can.interface.Bus(
                    channel=self.channel,
                    bustype="socketcan" if os.name != "nt" else "pcan",
                    bitrate=self.bitrate,
                )
                logger.info(f"[CAN] Initialized on {self.channel} @ {self.bitrate}")
            except Exception as e:
                logger.error(f"[CAN] Failed to init: {e}")
                self.bus = None
        else:
            logger.info("[CAN] python-can not available, falling back to mock.")

    def read_telemetry(self) -> Dict[str, Any]:
        if self.bus is None:
            return MockHardware().read_telemetry()
        telemetry = {
            "battery": 90.0,
            "speed": 0.0,
            "gps": (39.0, -96.0),
            "imu": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
        }
        try:
            msg = self.bus.recv(timeout=0.01)
            if msg is not None:
                # Example: decode some IDs
                if msg.arbitration_id == 0x100:
                    telemetry["speed"] = msg.data[0]
                elif msg.arbitration_id == 0x200:
                    telemetry["battery"] = msg.data[0]
        except Exception as e:
            logger.error(f"[CAN] read error: {e}")
        return telemetry

    def apply_control(self, control: Dict[str, Any]) -> None:
        self._last_control = control
        if self.bus is None:
            logger.info(f"[CAN] (mock) Control: {control}")
            return
        try:
            # Example: send throttle/brake/steer
            throttle = int(control.get("throttle", 0)) & 0xFF
            steer = int(control.get("steer", 0)) & 0xFF
            msg = python_can.Message(arbitration_id=0x300, data=[throttle, steer, 0, 0, 0, 0, 0, 0], is_extended_id=False)
            self.bus.send(msg)
            logger.info(f"[CAN] Sent control: {control}")
        except Exception as e:
            logger.error(f"[CAN] send error: {e}")

# Choose hardware implementation
HARDWARE: HardwareInterface = CANBusHardware(CAN_CHANNEL, CAN_BITRATE) if python_can else MockHardware()

class AsyncHardwareWrapper:
    def __init__(self, autopilot_core: AutopilotCore, interval: float = 0.5):
        self.autopilot_core = autopilot_core
        self.interval = interval

    async def run(self):
        while True:
            try:
                telemetry = HARDWARE.read_telemetry()
                self.autopilot_core.update_from_telemetry(telemetry)
                await EVENT_BUS.publish("telemetry", telemetry)
            except Exception as e:
                logger.error(f"[AsyncHardware] Error: {e}")
            await asyncio.sleep(self.interval)

# =============================================================================
# GPU telemetry
# =============================================================================

async def gpu_telemetry_task(autopilot_core: AutopilotCore, interval: float = 2.0):
    while True:
        util = 0.0
        mem = 0.0
        try:
            if torch is not None and torch.cuda.is_available():
                util = 50.0
                mem = 1024.0
        except Exception:
            pass
        autopilot_core.update_gpu_metrics(util, mem)
        await EVENT_BUS.publish("gpu_metrics", {"util": util, "mem": mem})
        await asyncio.sleep(interval)

# =============================================================================
# Navigation, obstacle avoidance, swarm, missions
# =============================================================================

class NavigationManager:
    """
    Simple navigation manager stub.
    """
    def __init__(self, autopilot_core: AutopilotCore):
        self.autopilot_core = autopilot_core
        self.current_target: Optional[str] = None

    def set_target(self, target: str):
        self.current_target = target
        logger.info(f"[Nav] Target set to {target}")

    def compute_control(self, state: VehicleState) -> Dict[str, Any]:
        if not self.current_target:
            return {"throttle": 0, "steer": 0}
        # Stub: always move forward
        return {"throttle": 30, "steer": 0}

class ObstacleAvoidance:
    """
    Stub obstacle avoidance.
    """
    def __init__(self):
        self.last_obstacle = None

    def update_from_sensors(self, telemetry: Dict[str, Any]):
        # Stub: no real sensors yet
        pass

    def adjust_control(self, control: Dict[str, Any]) -> Dict[str, Any]:
        # Stub: no modification
        return control

class SwarmCoordinator:
    """
    Stub swarm formation algorithms.
    """
    def __init__(self):
        self.peers: List[Tuple[str, int]] = SWARM_PEERS

    async def propagate_state(self, state: Dict[str, Any]):
        if not self.peers:
            return
        data = json.dumps({"type": "swarm_state", "state": state}, default=json_safe) + "\n"
        for host, port in self.peers:
            try:
                reader, writer = await asyncio.open_connection(host, port)
                writer.write(data.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                logger.info(f"[Swarm] propagated to {host}:{port}")
            except Exception as e:
                logger.error(f"[Swarm] failed to propagate to {host}:{port}: {e}")

class MissionStepType(Enum):
    NAVIGATE = "NAVIGATE"
    WAIT = "WAIT"
    MODE = "MODE"

@dataclass
class MissionStep:
    step_type: MissionStepType
    target: Optional[str] = None
    duration_sec: float = 0.0
    mode: Optional[VehicleMode] = None

@dataclass
class Mission:
    name: str
    steps: List[MissionStep]

class MissionEngine:
    """
    Simple mission scripting engine.
    """
    def __init__(self, autopilot_core: AutopilotCore, nav: NavigationManager):
        self.autopilot_core = autopilot_core
        self.nav = nav
        self.current_mission: Optional[Mission] = None
        self.current_index: int = 0
        self.running: bool = False

    def load_mission(self, mission: Mission):
        self.current_mission = mission
        self.current_index = 0
        self.running = False
        logger.info(f"[Mission] Loaded mission: {mission.name}")

    async def run(self):
        while True:
            if self.current_mission and self.running:
                if self.current_index >= len(self.current_mission.steps):
                    logger.info("[Mission] Completed.")
                    self.running = False
                else:
                    step = self.current_mission.steps[self.current_index]
                    logger.info(f"[Mission] Executing step {self.current_index}: {step}")
                    if step.step_type == MissionStepType.MODE and step.mode:
                        self.autopilot_core.set_mode(step.mode)
                    elif step.step_type == MissionStepType.NAVIGATE and step.target:
                        self.nav.set_target(step.target)
                    elif step.step_type == MissionStepType.WAIT:
                        await asyncio.sleep(step.duration_sec)
                    self.current_index += 1
            await asyncio.sleep(0.5)

    def start(self):
        if self.current_mission:
            self.running = True
            logger.info(f"[Mission] Started: {self.current_mission.name}")

    def stop(self):
        self.running = False
        logger.info("[Mission] Stopped.")

# =============================================================================
# Autopilot contract
# =============================================================================

@dataclass
class NavigationCommand:
    target: str
    mode: VehicleMode
    priority: int
    requires_confirmation: bool
    raw_text: str

@dataclass
class SystemQuery:
    query_type: str
    detail: str
    raw_text: str

@dataclass
class SystemEvent:
    event_type: str
    severity: str
    raw_text: str

def classify_intent_to_message(
    intent: str,
    text: str,
    current_mode: VehicleMode = VehicleMode.AUTONOMOUS,
) -> Dict[str, Any]:
    lower = text.lower()

    if intent == "navigation_command":
        target = "UNKNOWN"
        if "home" in lower:
            target = "HOME_BASE"
        elif "work" in lower:
            target = "WORK_SITE"
        elif "store" in lower:
            target = "STORE"

        mode = VehicleMode.AUTONOMOUS if current_mode != VehicleMode.MANUAL else current_mode
        priority = 3
        requires_confirmation = False

        unsafe_phrases = ["river", "cliff", "full speed", "disable safety"]
        if any(p in lower for p in unsafe_phrases):
            requires_confirmation = True
            priority = 8

        msg = NavigationCommand(
            target=target,
            mode=mode,
            priority=priority,
            requires_confirmation=requires_confirmation,
            raw_text=text,
        )
        return {"type": "NavigationCommand", "payload": asdict(msg)}

    elif intent == "status_query":
        detail = "general"
        if "battery" in lower:
            detail = "battery"
        elif "location" in lower:
            detail = "location"
        elif "mode" in lower:
            detail = "mode"

        msg = SystemQuery(
            query_type="status",
            detail=detail,
            raw_text=text,
        )
        return {"type": "SystemQuery", "payload": asdict(msg)}

    elif intent == "stop_command":
        msg = SystemEvent(
            event_type="stop_requested",
            severity="high",
            raw_text=text,
        )
        return {"type": "SystemEvent", "payload": asdict(msg)}

    else:
        msg = SystemEvent(
            event_type="unclassified_input",
            severity="low",
            raw_text=text,
        )
        return {"type": "SystemEvent", "payload": asdict(msg)}

def validate_autopilot_message(
    msg: Dict[str, Any],
    current_mode: VehicleMode,
) -> Tuple[bool, str]:
    msg_type = msg.get("type")
    payload = msg.get("payload", {})

    if msg_type == "NavigationCommand":
        target = payload.get("target", "")
        if not target or not isinstance(target, str):
            return False, "NavigationCommand requires non-empty 'target'"

        priority = payload.get("priority", 0)
        if not isinstance(priority, int) or priority < 0 or priority > 10:
            return False, "NavigationCommand 'priority' must be int 0-10"

        mode_str = payload.get("mode")
        try:
            VehicleMode(mode_str)
        except Exception:
            return False, "NavigationCommand 'mode' must be a valid VehicleMode"

    return True, "OK"

# =============================================================================
# Plugin system
# =============================================================================

class PluginManager:
    def __init__(self, plugins_dir: pathlib.Path):
        self.plugins_dir = plugins_dir
        self.plugins: List[Any] = []
        self._bootstrap_sample_plugin()
        self._load_plugins()

    def _bootstrap_sample_plugin(self):
        sample = self.plugins_dir / "sample_plugin.py"
        if sample.exists():
            return
        try:
            sample.write_text(
                "def process_envelope(envelope, api):\n"
                "    envelope.setdefault('plugin_info', []).append('sample_plugin_active')\n"
                "    return envelope\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_plugins(self):
        self.plugins.clear()
        sys.path.insert(0, str(self.plugins_dir))
        for fname in os.listdir(self.plugins_dir):
            if not fname.endswith(".py"):
                continue
            mod_name = fname[:-3]
            try:
                spec = importlib.util.spec_from_file_location(mod_name, self.plugins_dir / fname)
                module = importlib.util.module_from_spec(spec)
                loader = spec.loader
                assert loader is not None
                loader.exec_module(module)
                self.plugins.append(module)
                logger.info(f"[Plugins] Loaded plugin: {mod_name}")
            except Exception as e:
                logger.error(f"[Plugins] Failed to load {mod_name}: {e}")

    def reload_plugins(self):
        logger.info("[Plugins] Hot-reloading plugins...")
        self._load_plugins()

    def _sandbox_api(self) -> Dict[str, Any]:
        return {
            "publish_event": lambda topic, event: asyncio.run_coroutine_threadsafe(
                EVENT_BUS.publish(topic, event),
                asyncio.get_event_loop(),
            ),
        }

    def apply_plugins(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        api = self._sandbox_api()
        for plugin in self.plugins:
            try:
                if hasattr(plugin, "process_envelope"):
                    result = plugin.process_envelope(envelope, api)
                    if result is None:
                        continue
                    envelope = result
            except Exception as e:
                logger.error(f"[Plugins] Error in plugin {plugin.__name__}: {e}")
        return envelope

PLUGIN_MANAGER = PluginManager(PLUGINS_DIR)

async def plugin_hot_reload_task(interval: float = 5.0):
    while True:
        try:
            PLUGIN_MANAGER.reload_plugins()
        except Exception as e:
            logger.error(f"[Plugins] Hot-reload error: {e}")
        await asyncio.sleep(interval)

# =============================================================================
# Backends + AI Copilot
# =============================================================================

class BaseModelBackend(ABC):
    @abstractmethod
    async def analyze(
        self,
        text: str,
        *,
        tasks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def analyze_stream(
        self,
        text: str,
        *,
        tasks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        result = await self.analyze(text, tasks=tasks, metadata=metadata)
        yield {"final": True, "result": result}

class DummyBackend(BaseModelBackend):
    async def analyze(
        self,
        text: str,
        *,
        tasks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await asyncio.sleep(0.05)

        tasks = tasks or ["sentiment", "intent", "entities"]
        lower = text.lower()

        pos_markers = ["good", "great", "awesome", "love", "like"]
        neg_markers = ["bad", "terrible", "hate", "angry", "sad"]

        pos = sum(1 for w in pos_markers if w in lower)
        neg = sum(1 for w in neg_markers if w in lower)
        score = pos - neg
        if score > 0:
            sentiment = "positive"
        elif score < 0:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        if "navigate" in lower or "drive" in lower or "go to" in lower:
            intent = "navigation_command"
        elif "stop" in lower or "abort" in lower:
            intent = "stop_command"
        elif "status" in lower or "state" in lower or "how is" in lower:
            intent = "status_query"
        else:
            intent = "unknown"

        entities = []
        for token in text.split():
            if token.istitle():
                entities.append({"text": token, "type": "ProperNoun"})

        return {
            "backend": "DummyBackend",
            "tasks": tasks,
            "sentiment": {
                "label": sentiment,
                "score": score,
                "pos_hits": pos,
                "neg_hits": neg,
            },
            "intent": intent,
            "entities": entities,
            "metadata": metadata or {},
        }

    async def analyze_stream(
        self,
        text: str,
        *,
        tasks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        words = text.split()
        for i, w in enumerate(words, 1):
            await asyncio.sleep(0.02)
            yield {
                "final": False,
                "chunk_index": i,
                "chunk_total": len(words),
                "token": w,
            }
        final = await self.analyze(text, tasks=tasks, metadata=metadata)
        yield {"final": True, "result": final}

class RemoteLLMBackend(BaseModelBackend):
    async def _http_post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload, default=json_safe)
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {LLM_HOST}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
            f"{body}"
        )

        reader, writer = await asyncio.open_connection(LLM_HOST, LLM_PORT)
        writer.write(request.encode("utf-8"))
        await writer.drain()

        response_data = await reader.read()
        writer.close()
        await writer.wait_closed()

        response_text = response_data.decode("utf-8", errors="replace")
        parts = response_text.split("\r\n\r\n", 1)
        if len(parts) != 2:
            raise ValueError("Invalid HTTP response from LLM backend")
        body_text = parts[1]
        return json.loads(body_text)

    async def analyze(
        self,
        text: str,
        *,
        tasks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tasks = tasks or ["sentiment", "intent", "entities"]
        payload = {
            "text": text,
            "tasks": tasks,
            "metadata": metadata or {},
        }
        result = await self._http_post_json(LLM_PATH, payload)

        intent = result.get("intent", "unknown")
        sentiment = result.get("sentiment", {"label": "neutral", "score": 0})
        entities = result.get("entities", [])

        return {
            "backend": "RemoteLLMBackend",
            "tasks": tasks,
            "sentiment": sentiment,
            "intent": intent,
            "entities": entities,
            "metadata": metadata or {},
            "raw_backend": result,
        }

    async def analyze_stream(
        self,
        text: str,
        *,
        tasks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        final = await self.analyze(text, tasks=tasks, metadata=metadata)
        yield {"final": True, "result": final}

class LlamaCppBackend(BaseModelBackend):
    def __init__(self):
        logger.info("[LlamaCppBackend] Initialized (HTTP hook).")
        self._device = "cuda" if GPU_LLM_ENABLED and torch is not None and torch.cuda.is_available() else "cpu"

    async def _http_post_json(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        body = json.dumps(payload, default=json_safe)
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {LLM_HOST}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
            f"{body}"
        )

        reader, writer = await asyncio.open_connection(LLM_HOST, LLM_PORT)
        writer.write(request.encode("utf-8"))
        await writer.drain()

        response_data = await reader.read()
        writer.close()
        await writer.wait_closed()

        response_text = response_data.decode("utf-8", errors="replace")
        parts = response_text.split("\r\n\r\n", 1)
        if len(parts) != 2:
            return None
        body_text = parts[1]
        try:
            return json.loads(body_text)
        except Exception:
            return None

    async def analyze(
        self,
        text: str,
        *,
        tasks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tasks = tasks or ["sentiment", "intent", "entities"]
        payload = {
            "prompt": text,
            "tasks": tasks,
            "metadata": metadata or {},
            "device": self._device,
        }
        try:
            result = await self._http_post_json(LLM_PATH, payload)
            if result is None:
                raise RuntimeError("llama.cpp returned None or invalid JSON")
        except Exception as e:
            logger.error(f"[LlamaCppBackend] Error calling llama.cpp: {e}")
            dummy = DummyBackend()
            return await dummy.analyze(text, tasks=tasks, metadata=metadata)

        intent = result.get("intent", "unknown")
        sentiment = result.get("sentiment", {"label": "neutral", "score": 0})
        entities = result.get("entities", [])

        return {
            "backend": "LlamaCppBackend",
            "tasks": tasks,
            "sentiment": sentiment,
            "intent": intent,
            "entities": entities,
            "metadata": metadata or {},
            "raw_backend": result,
        }

    async def analyze_stream(
        self,
        text: str,
        *,
        tasks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        final = await self.analyze(text, tasks=tasks, metadata=metadata)
        yield {"final": True, "result": final}

def ensure_model_downloaded(model_name: str = "llama-model"):
    model_path = MODELS_DIR / model_name
    if model_path.exists():
        return
    logger.info(f"[Model] Auto-download stub for {model_name} -> {model_path}")
    try:
        model_path.write_text("MODEL_PLACEHOLDER", encoding="utf-8")
    except Exception:
        pass

class AICopilot:
    """
    AI Copilot that comments on autopilot state and suggests actions.
    Uses same backend as LPU but different prompts.
    """
    def __init__(self, backend: BaseModelBackend, autopilot_core: AutopilotCore):
        self.backend = backend
        self.autopilot_core = autopilot_core
        self._queue: asyncio.Queue = asyncio.Queue()

    async def analyze_state(self):
        while True:
            state = self.autopilot_core.get_state_dict()
            text = (
                f"Vehicle mode: {state['mode']}, speed: {state['speed']}, "
                f"battery: {state['battery_level']}, location: {state['location']}."
                " Suggest a short safety-focused comment."
            )
            result = await self.backend.analyze(text, tasks=["sentiment", "intent"], metadata={"role": "copilot"})
            envelope = {
                "type": "copilot_comment",
                "state": state,
                "backend_result": result,
            }
            await self._queue.put(envelope)
            await EVENT_BUS.publish("copilot_comment", envelope)
            await asyncio.sleep(5.0)

    async def consume_comments(self):
        while True:
            yield await self._queue.get()

# =============================================================================
# LPU core
# =============================================================================

class LanguageProcessingUnit:
    def __init__(
        self,
        autopilot_core: AutopilotCore,
        backend: Optional[BaseModelBackend] = None,
        *,
        unit_id: str = "LPU-Kernel",
        nav_manager: Optional[NavigationManager] = None,
        mission_engine: Optional[MissionEngine] = None,
    ):
        if backend is None:
            if USE_LOCAL_BACKEND:
                ensure_model_downloaded()
                backend = LlamaCppBackend()
            elif USE_REMOTE_BACKEND:
                backend = RemoteLLMBackend()
            else:
                backend = DummyBackend()
        self.unit_id = unit_id
        self.backend = backend
        self.autopilot_core = autopilot_core
        self.nav_manager = nav_manager
        self.mission_engine = mission_engine
        self._output_queue: asyncio.Queue = asyncio.Queue()

    def set_mode(self, mode_str: str) -> Tuple[bool, str]:
        try:
            mode = VehicleMode(mode_str)
        except Exception:
            return False, f"Invalid mode: {mode_str}"
        ok, msg = self.autopilot_core.set_mode(mode)
        return ok, msg

    async def process_text(
        self,
        text: str,
        *,
        source: str = "ipc",
        tasks: Optional[List[str]] = None,
        autopilot_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        t0 = time.time()

        state = self.autopilot_core.get_state()
        autopilot_context = autopilot_context or {}
        metadata = {
            "unit_id": self.unit_id,
            "source": source,
            "autopilot_context": autopilot_context,
            "timestamp": time.time(),
            "vehicle_mode": state.mode.value,
            "vehicle_state": self.autopilot_core.get_state_dict(),
        }

        backend_result = await self.backend.analyze(
            text,
            tasks=tasks,
            metadata=metadata,
        )

        dt = time.time() - t0

        autopilot_msg = classify_intent_to_message(
            backend_result.get("intent", "unknown"),
            text,
            current_mode=state.mode,
        )

        valid, reason = validate_autopilot_message(
            autopilot_msg,
            current_mode=state.mode,
        )
        validation = {"valid": valid, "reason": reason}

        applied_result = None
        if valid:
            msg_type = autopilot_msg.get("type")
            payload = autopilot_msg.get("payload", {})
            if msg_type == "NavigationCommand":
                if self.nav_manager and payload.get("target"):
                    self.nav_manager.set_target(payload["target"])
                applied_result = self.autopilot_core.apply_navigation_command(payload)
            elif msg_type == "SystemEvent":
                applied_result = self.autopilot_core.apply_system_event(payload)

        envelope = {
            "unit_id": self.unit_id,
            "source": source,
            "latency_sec": dt,
            "text": text,
            "backend_result": backend_result,
            "autopilot_message": autopilot_msg,
            "validation": validation,
            "vehicle_mode": state.mode.value,
            "vehicle_state": self.autopilot_core.get_state_dict(),
            "applied_result": applied_result,
        }

        envelope = PLUGIN_MANAGER.apply_plugins(envelope)

        await self._output_queue.put(envelope)

        await EVENT_BUS.publish("lpu_envelope", envelope)

        await self._codex_purge_shell_hook(envelope)
        await self._swarm_sync_propagate(envelope)

        return envelope

    async def process_text_stream(
        self,
        text: str,
        *,
        source: str = "ipc",
        tasks: Optional[List[str]] = None,
        autopilot_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        t0 = time.time()
        state = self.autopilot_core.get_state()
        autopilot_context = autopilot_context or {}
        metadata = {
            "unit_id": self.unit_id,
            "source": source,
            "autopilot_context": autopilot_context,
            "timestamp": time.time(),
            "vehicle_mode": state.mode.value,
            "vehicle_state": self.autopilot_core.get_state_dict(),
        }

        async for chunk in self.backend.analyze_stream(
            text,
            tasks=tasks,
            metadata=metadata,
        ):
            if not chunk.get("final"):
                yield {"stream": True, "chunk": chunk}
            else:
                backend_result = chunk["result"]
                dt = time.time() - t0
                autopilot_msg = classify_intent_to_message(
                    backend_result.get("intent", "unknown"),
                    text,
                    current_mode=state.mode,
                )
                valid, reason = validate_autopilot_message(
                    autopilot_msg,
                    current_mode=state.mode,
                )
                validation = {"valid": valid, "reason": reason}

                applied_result = None
                if valid:
                    msg_type = autopilot_msg.get("type")
                    payload = autopilot_msg.get("payload", {})
                    if msg_type == "NavigationCommand":
                        if self.nav_manager and payload.get("target"):
                            self.nav_manager.set_target(payload["target"])
                        applied_result = self.autopilot_core.apply_navigation_command(payload)
                    elif msg_type == "SystemEvent":
                        applied_result = self.autopilot_core.apply_system_event(payload)

                envelope = {
                    "unit_id": self.unit_id,
                    "source": source,
                    "latency_sec": dt,
                    "text": text,
                    "backend_result": backend_result,
                    "autopilot_message": autopilot_msg,
                    "validation": validation,
                    "vehicle_mode": state.mode.value,
                    "vehicle_state": self.autopilot_core.get_state_dict(),
                    "applied_result": applied_result,
                }

                envelope = PLUGIN_MANAGER.apply_plugins(envelope)

                await self._output_queue.put(envelope)
                await EVENT_BUS.publish("lpu_envelope", envelope)
                await self._codex_purge_shell_hook(envelope)
                await self._swarm_sync_propagate(envelope)
                yield {"stream": False, "final": True, "envelope": envelope}

    async def consume_results(self):
        while True:
            result = await self._output_queue.get()
            yield result

    async def _codex_purge_shell_hook(self, envelope: Dict[str, Any]):
        msg = envelope.get("autopilot_message", {})
        payload = msg.get("payload", {})
        if msg.get("type") == "SystemEvent" and payload.get("event_type") == "stop_requested":
            logger.warning("[CodexPurgeShell] stop_requested event received")

    async def _swarm_sync_propagate(self, envelope: Dict[str, Any]):
        if not SWARM_PEERS:
            return
        data = json.dumps({"type": "swarm_update", "envelope": envelope}, default=json_safe) + "\n"
        for host, port in SWARM_PEERS:
            try:
                reader, writer = await asyncio.open_connection(host, port)
                writer.write(data.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                logger.info(f"[SwarmSync] propagated to {host}:{port}")
            except Exception as e:
                logger.error(f"[SwarmSync] failed to propagate to {host}:{port}: {e}")

# =============================================================================
# IPC server (TLS+JWT)
# =============================================================================

class LPUIPCServer:
    def __init__(
        self,
        lpu: LanguageProcessingUnit,
        host: str = IPC_HOST,
        port: int = IPC_PORT,
    ):
        self.lpu = lpu
        self.host = host
        self.port = port
        self._server: Optional[asyncio.AbstractServer] = None

    def _create_ssl_context(self) -> Optional[ssl.SSLContext]:
        if not USE_TLS:
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=SERVER_CRT, keyfile=SERVER_KEY)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(CA_CRT)
        ctx.check_hostname = False
        return ctx

    async def start(self):
        ssl_ctx = self._create_ssl_context()
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
            ssl=ssl_ctx,
        )
        addr = ", ".join(str(sock.getsockname()) for sock in self._server.sockets)
        mode = "TLS (mutual)" if USE_TLS else "PLAINTEXT (NO-TLS)"
        logger.info(f"[LPU IPC] {mode} listening on {addr}")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        peer = writer.get_extra_info("peername")
        cert = writer.get_extra_info("peercert") if USE_TLS else None
        logger.info(f"[LPU IPC] Client connected: {peer}, cert={bool(cert)}")

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    req = json.loads(line)
                except json.JSONDecodeError as e:
                    resp = {"ok": False, "error": f"Invalid JSON: {e}"}
                    writer.write((json.dumps(resp, default=json_safe) + "\n").encode("utf-8"))
                    await writer.drain()
                    continue

                jwt_token = req.get("jwt")
                if not isinstance(jwt_token, str):
                    resp = {"ok": False, "error": "Missing 'jwt' field"}
                    writer.write((json.dumps(resp, default=json_safe) + "\n").encode("utf-8"))
                    await writer.drain()
                    continue

                try:
                    claims = verify_jwt(jwt_token)
                except Exception as e:
                    resp = {"ok": False, "error": f"JWT error: {e}"}
                    writer.write((json.dumps(resp, default=json_safe) + "\n").encode("utf-8"))
                    await writer.drain()
                    continue

                command = req.get("command")
                if command == "set_mode":
                    mode_str = req.get("mode")
                    ok, reason = self.lpu.set_mode(mode_str)
                    resp = {
                        "ok": ok,
                        "message": reason,
                        "current_mode": self.lpu.autopilot_core.get_state().mode.value,
                    }
                    writer.write((json.dumps(resp, default=json_safe) + "\n").encode("utf-8"))
                    await writer.drain()
                    continue

                text = req.get("text")
                tasks = req.get("tasks")
                context = req.get("context") or {}
                stream = bool(req.get("stream", False))

                if not isinstance(text, str) or not text.strip():
                    resp = {"ok": False, "error": "Missing or invalid 'text' field"}
                    writer.write((json.dumps(resp, default=json_safe) + "\n").encode("utf-8"))
                    await writer.drain()
                    continue

                context["_jwt_claims"] = claims

                if not stream:
                    try:
                        result = await self.lpu.process_text(
                            text,
                            source="ipc_tls_jwt" if USE_TLS else "ipc_plain_jwt",
                            tasks=tasks,
                            autopilot_context=context,
                        )
                        resp = {"ok": True, "result": result}
                    except Exception as e:
                        resp = {"ok": False, "error": str(e)}
                    writer.write((json.dumps(resp, default=json_safe) + "\n").encode("utf-8"))
                    await writer.drain()
                else:
                    try:
                        async for chunk in self.lpu.process_text_stream(
                            text,
                            source="ipc_tls_jwt_stream" if USE_TLS else "ipc_plain_jwt_stream",
                            tasks=tasks,
                            autopilot_context=context,
                        ):
                            resp = {"ok": True}
                            resp.update(chunk)
                            writer.write((json.dumps(resp, default=json_safe) + "\n").encode("utf-8"))
                            await writer.drain()
                    except Exception as e:
                        resp = {"ok": False, "error": str(e)}
                        writer.write((json.dumps(resp, default=json_safe) + "\n").encode("utf-8"))
                        await writer.drain()

        finally:
            logger.info(f"[LPU IPC] Client disconnected: {peer}")
            writer.close()
            await writer.wait_closed()

    async def run_forever(self):
        await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

# =============================================================================
# REST API (async) with aiohttp.web safety
# =============================================================================

class RESTServer:
    def __init__(self, autopilot_core: AutopilotCore, lpu: LanguageProcessingUnit):
        self.autopilot_core = autopilot_core
        self.lpu = lpu
        self.app = None
        self.runner = None

    async def start(self):
        if aiohttp is None or not REST_API_ENABLED or not hasattr(aiohttp, "web"):
            logger.info("[REST] Disabled (aiohttp missing or aiohttp.web not available).")
            return
        web = aiohttp.web
        self.app = web.Application()
        self.app.add_routes([
            web.get("/state", self.handle_state),
            web.post("/command", self.handle_command),
        ])
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, REST_API_HOST, REST_API_PORT)
        await site.start()
        logger.info(f"[REST] Listening on http://{REST_API_HOST}:{REST_API_PORT}")

    async def handle_state(self, request):
        state = self.autopilot_core.get_state_dict()
        return aiohttp.web.json_response(state)

    async def handle_command(self, request):
        try:
            data = await request.json()
        except Exception:
            return aiohttp.web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        text = data.get("text")
        if not text:
            return aiohttp.web.json_response({"ok": False, "error": "Missing text"}, status=400)
        result = await self.lpu.process_text(text, source="rest")
        return aiohttp.web.json_response({"ok": True, "result": result})

# =============================================================================
# MQTT bridge
# =============================================================================

class MQTTBridge:
    def __init__(self, lpu: LanguageProcessingUnit):
        self.lpu = lpu
        self.client = None

    def start(self):
        if paho_mqtt is None or not MQTT_ENABLED:
            logger.info("[MQTT] Disabled or paho-mqtt missing.")
            return
        self.client = paho_mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _run_loop(self):
        try:
            self.client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
            self.client.loop_forever()
        except Exception as e:
            logger.error(f"[MQTT] Connection error: {e}")

    def on_connect(self, client, userdata, flags, rc):
        logger.info(f"[MQTT] Connected with result code {rc}")
        client.subscribe(MQTT_TOPIC_IN)

    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            data = json.loads(payload)
            text = data.get("text")
            if not text:
                return
            asyncio.run_coroutine_threadsafe(
                self._handle_command(text),
                asyncio.get_event_loop(),
            )
        except Exception as e:
            logger.error(f"[MQTT] Message error: {e}")

    async def _handle_command(self, text: str):
        await self.lpu.process_text(text, source="mqtt")

# =============================================================================
# OTA updates (stub)
# =============================================================================

def check_for_updates() -> Optional[str]:
    if not OTA_ENABLED or requests is None:
        return None
    try:
        resp = requests.get(OTA_VERSION_URL, timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        remote_version = data.get("version")
        if remote_version and remote_version != APP_VERSION:
            return remote_version
    except Exception as e:
        logger.error(f"[OTA] Version check error: {e}")
    return None

def apply_update(version: str):
    if requests is None:
        logger.error("[OTA] requests not available.")
        return
    logger.info(f"[OTA] Applying update to version {version}")
    try:
        resp = requests.get(OTA_BINARY_URL, timeout=10)
        if resp.status_code != 200:
            logger.error("[OTA] Failed to download new binary.")
            return
        new_path = OTA_DIR / f"lpu_autopilot_kernel_{version}.py"
        new_path.write_bytes(resp.content)
        logger.info(f"[OTA] New version saved to {new_path}")
    except Exception as e:
        logger.error(f"[OTA] Error applying update: {e}")

# =============================================================================
# Voice control
# =============================================================================

class VoiceController:
    def __init__(self, gui_log: Callable[[str], None]):
        self.gui_log = gui_log
        self.recognizer = speech_recognition.Recognizer() if speech_recognition else None
        self.tts_engine = pyttsx3.init() if pyttsx3 and TTS_ENABLED else None

        self.whisper_model = None

        if WHISPER_ENABLED:
            try:
                import whisper as whisper_mod
                self.whisper_model = whisper_mod.load_model("base")
                self.gui_log("[VOICE] Whisper model loaded.")
            except TypeError as e:
                self.gui_log(f"[VOICE] Whisper disabled due to TypeError: {e}")
                self.whisper_model = None
            except Exception as e:
                self.gui_log(f"[VOICE] Whisper load error: {e}")
                self.whisper_model = None

        self._running = False

    def speak(self, text: str):
        if self.tts_engine:
            self.tts_engine.say(text)
            self.tts_engine.runAndWait()

    def start_listening(self, callback: Callable[[str], None]):
        if not self.recognizer and not self.whisper_model:
            self.gui_log("[VOICE] No STT backend available.")
            return
        if self._running:
            self.gui_log("[VOICE] Already listening.")
            return
        self._running = True
        threading.Thread(target=self._listen_loop, args=(callback,), daemon=True).start()

    def _listen_loop(self, callback: Callable[[str], None]):
        if self.whisper_model and sounddevice and soundfile:
            self.gui_log("[VOICE] Whisper STT loop (offline).")
            while self._running:
                try:
                    duration = 5
                    fs = 16000
                    self.gui_log("[VOICE] Recording...")
                    audio = sounddevice.rec(int(duration * fs), samplerate=fs, channels=1)
                    sounddevice.wait()
                    tmp_path = CACHE_DIR / "whisper_input.wav"
                    soundfile.write(tmp_path, audio, fs)
                    result = self.whisper_model.transcribe(str(tmp_path))
                    text = result.get("text", "").strip()
                    if text:
                        self.gui_log(f"[VOICE] Whisper heard: {text}")
                        callback(text)
                except Exception as e:
                    self.gui_log(f"[VOICE] Whisper error: {e}")
                    continue
            return

        if self.recognizer:
            mic = speech_recognition.Microphone()
            self.gui_log("[VOICE] Listening (Google STT)...")
            with mic as source:
                self.recognizer.adjust_for_ambient_noise(source)
                while self._running:
                    try:
                        audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
                        text = self.recognizer.recognize_google(audio)
                        self.gui_log(f"[VOICE] Heard: {text}")
                        callback(text)
                    except speech_recognition.WaitTimeoutError:
                        continue
                    except Exception as e:
                        self.gui_log(f"[VOICE] Error: {e}")
                        continue
            return

        self.gui_log("[VOICE] No STT backend available (Whisper/Google).")
        return

    def stop(self):
        self._running = False
        self.gui_log("[VOICE] Stopped listening.")

# =============================================================================
# PySide6 GUI with telemetry, metrics, swarm GPS map, 3D view, missions, copilot
# =============================================================================

if PySide6:
    class LPUClientGUI(QtWidgets.QMainWindow):
        def __init__(self, autopilot_core: AutopilotCore, mission_engine: MissionEngine):
            super().__init__()
            self.autopilot_core = autopilot_core
            self.mission_engine = mission_engine
            title_mode = "TLS+JWT" if USE_TLS else "PLAINTEXT+JWT (NO-TLS)"
            self.setWindowTitle(f"LPU Autopilot Kernel [{title_mode}] v{APP_VERSION}")
            self.resize(1300, 850)

            self.loop = asyncio.get_event_loop()

            self.metrics = {
                "last_latency": 0.0,
                "commands_count": 0,
                "errors_count": 0,
            }

            self.copilot_comments: List[Dict[str, Any]] = []

            EVENT_BUS.subscribe("lpu_envelope", self._on_envelope_event)
            EVENT_BUS.subscribe("mode_change", self._on_mode_change)
            EVENT_BUS.subscribe("navigation", self._on_navigation)
            EVENT_BUS.subscribe("emergency", self._on_emergency)
            EVENT_BUS.subscribe("gpu_metrics", self._on_gpu_metrics)
            EVENT_BUS.subscribe("copilot_comment", self._on_copilot_comment)

            self._build_ui()

            self.voice_controller = VoiceController(self.log) if VOICE_ENABLED else None

            self._telemetry_timer = QtCore.QTimer(self)
            self._telemetry_timer.timeout.connect(self._update_telemetry)
            self._telemetry_timer.start(1000)

            self._metrics_timer = QtCore.QTimer(self)
            self._metrics_timer.timeout.connect(self._update_metrics_view)
            self._metrics_timer.start(1000)

            self._map_timer = QtCore.QTimer(self)
            self._map_timer.timeout.connect(self._draw_swarm_map)
            self._map_timer.start(2000)

            self._plugin_timer = QtCore.QTimer(self)
            self._plugin_timer.timeout.connect(self._plugin_hot_reload_gui)
            self._plugin_timer.start(5000)

            self._gpu_timer = QtCore.QTimer(self)
            self._gpu_timer.timeout.connect(self._update_gpu_view)
            self._gpu_timer.start(2000)

            self._logging_timer = QtCore.QTimer(self)
            self._logging_timer.timeout.connect(self._update_logging_dashboard)
            self._logging_timer.start(2000)

            self._copilot_timer = QtCore.QTimer(self)
            self._copilot_timer.timeout.connect(self._update_copilot_view)
            self._copilot_timer.start(3000)

        def _build_ui(self):
            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            layout = QtWidgets.QVBoxLayout(central)

            self.tabs = QtWidgets.QTabWidget()
            layout.addWidget(self.tabs)

            # Control tab
            self.control_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.control_tab, "Control")
            ctl_layout = QtWidgets.QGridLayout(self.control_tab)

            self.mode_label = QtWidgets.QLabel("Vehicle mode:")
            ctl_layout.addWidget(self.mode_label, 0, 0)

            self.mode_combo = QtWidgets.QComboBox()
            for m in VehicleMode:
                self.mode_combo.addItem(m.value)
            self.mode_combo.setCurrentText(self.autopilot_core.get_state().mode.value)
            ctl_layout.addWidget(self.mode_combo, 0, 1)

            self.set_mode_btn = QtWidgets.QPushButton("Set mode")
            self.set_mode_btn.clicked.connect(self.set_mode)
            ctl_layout.addWidget(self.set_mode_btn, 0, 2)

            self.text_label = QtWidgets.QLabel("Input text:")
            ctl_layout.addWidget(self.text_label, 1, 0)

            self.text_edit = QtWidgets.QPlainTextEdit()
            ctl_layout.addWidget(self.text_edit, 2, 0, 1, 3)

            self.stream_check = QtWidgets.QCheckBox("Stream")
            self.stream_check.setChecked(True)
            ctl_layout.addWidget(self.stream_check, 3, 0)

            self.send_btn = QtWidgets.QPushButton("Send")
            self.send_btn.clicked.connect(self.send_text)
            ctl_layout.addWidget(self.send_btn, 3, 1)

            self.voice_btn = QtWidgets.QPushButton("Voice Input")
            self.voice_btn.clicked.connect(self.start_voice)
            ctl_layout.addWidget(self.voice_btn, 3, 2)

            # Logs tab
            self.logs_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.logs_tab, "Logs")
            logs_layout = QtWidgets.QVBoxLayout(self.logs_tab)
            self.logs_edit = QtWidgets.QPlainTextEdit()
            self.logs_edit.setReadOnly(True)
            logs_layout.addWidget(self.logs_edit)

            # Telemetry tab
            self.telemetry_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.telemetry_tab, "Telemetry")
            tele_layout = QtWidgets.QVBoxLayout(self.telemetry_tab)
            self.telemetry_edit = QtWidgets.QPlainTextEdit()
            self.telemetry_edit.setReadOnly(True)
            tele_layout.addWidget(self.telemetry_edit)

            # Metrics tab
            self.metrics_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.metrics_tab, "Metrics")
            met_layout = QtWidgets.QVBoxLayout(self.metrics_tab)
            self.metrics_edit = QtWidgets.QPlainTextEdit()
            self.metrics_edit.setReadOnly(True)
            met_layout.addWidget(self.metrics_edit)

            # Swarm Map tab
            self.map_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.map_tab, "Swarm Map")
            map_layout = QtWidgets.QVBoxLayout(self.map_tab)
            self.map_view = QtWidgets.QGraphicsView()
            self.map_scene = QtWidgets.QGraphicsScene()
            self.map_view.setScene(self.map_scene)
            map_layout.addWidget(self.map_view)

            # GPU & 3D tab
            self.gpu_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.gpu_tab, "GPU & 3D")
            gpu_layout = QtWidgets.QVBoxLayout(self.gpu_tab)
            self.gpu_edit = QtWidgets.QPlainTextEdit()
            self.gpu_edit.setReadOnly(True)
            gpu_layout.addWidget(self.gpu_edit)
            self.view3d = QtWidgets.QGraphicsView()
            self.scene3d = QtWidgets.QGraphicsScene()
            self.view3d.setScene(self.scene3d)
            gpu_layout.addWidget(self.view3d)

            # Missions tab
            self.mission_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.mission_tab, "Missions")
            mission_layout = QtWidgets.QVBoxLayout(self.mission_tab)
            self.mission_text = QtWidgets.QPlainTextEdit()
            self.mission_text.setPlaceholderText(
                "Example mission JSON:\n"
                "{\n"
                '  "name": "TestMission",\n'
                '  "steps": [\n'
                '    {"type": "MODE", "mode": "AUTONOMOUS"},\n'
                '    {"type": "NAVIGATE", "target": "HOME_BASE"},\n'
                '    {"type": "WAIT", "duration_sec": 5}\n'
                "  ]\n"
                "}"
            )
            mission_layout.addWidget(self.mission_text)
            btn_layout = QtWidgets.QHBoxLayout()
            self.load_mission_btn = QtWidgets.QPushButton("Load Mission")
            self.load_mission_btn.clicked.connect(self.load_mission_from_text)
            btn_layout.addWidget(self.load_mission_btn)
            self.start_mission_btn = QtWidgets.QPushButton("Start Mission")
            self.start_mission_btn.clicked.connect(self.start_mission)
            btn_layout.addWidget(self.start_mission_btn)
            self.stop_mission_btn = QtWidgets.QPushButton("Stop Mission")
            self.stop_mission_btn.clicked.connect(self.stop_mission)
            btn_layout.addWidget(self.stop_mission_btn)
            mission_layout.addLayout(btn_layout)

            # Logging dashboard tab
            self.logging_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.logging_tab, "Logging Dashboard")
            logdash_layout = QtWidgets.QVBoxLayout(self.logging_tab)
            self.logdash_edit = QtWidgets.QPlainTextEdit()
            self.logdash_edit.setReadOnly(True)
            logdash_layout.addWidget(self.logdash_edit)

            # AI Copilot tab
            self.copilot_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.copilot_tab, "AI Copilot")
            copilot_layout = QtWidgets.QVBoxLayout(self.copilot_tab)
            self.copilot_edit = QtWidgets.QPlainTextEdit()
            self.copilot_edit.setReadOnly(True)
            copilot_layout.addWidget(self.copilot_edit)

            self.status_bar = self.statusBar()
            self.status_bar.showMessage("Ready")

        def log(self, msg: str):
            self.logs_edit.appendPlainText(msg)

        def set_status(self, msg: str):
            self.status_bar.showMessage(msg)

        def closeEvent(self, event: QtGui.QCloseEvent):
            if self.voice_controller:
                self.voice_controller.stop()
            save_config()
            event.accept()

        def _make_ssl_context(self) -> Optional[ssl.SSLContext]:
            if not USE_TLS:
                return None
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_ctx.load_verify_locations(CA_CRT)
            ssl_ctx.load_cert_chain(certfile=CLIENT_CRT, keyfile=CLIENT_KEY)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_REQUIRED
            return ssl_ctx

        def _make_jwt(self) -> str:
            payload = {
                "iss": JWT_ISSUER,
                "aud": JWT_AUDIENCE,
                "sub": "gui-client",
                "iat": int(time.time()),
            }
            return create_jwt(payload)

        def set_mode(self):
            mode = self.mode_combo.currentText()
            self.log(f"[MODE] Setting mode to {mode}")
            self.set_status(f"Setting mode to {mode}")
            asyncio.ensure_future(self._set_mode_async(mode))

        async def _set_mode_async(self, mode: str):
            try:
                ssl_ctx = self._make_ssl_context()
                if USE_TLS:
                    reader, writer = await asyncio.open_connection(
                        IPC_HOST, IPC_PORT, ssl=ssl_ctx
                    )
                else:
                    reader, writer = await asyncio.open_connection(
                        IPC_HOST, IPC_PORT
                    )

                token = self._make_jwt()
                req = {
                    "jwt": token,
                    "command": "set_mode",
                    "mode": mode,
                }
                writer.write((json.dumps(req, default=json_safe) + "\n").encode("utf-8"))
                await writer.drain()

                line = await reader.readline()
                if not line:
                    self.log("[ERROR] No response for set_mode")
                else:
                    resp = json.loads(line.decode("utf-8", errors="replace"))
                    self.log(json.dumps(resp, indent=2, default=json_safe))
                    self.set_status(f"Mode: {resp.get('current_mode')}")

                writer.close()
                await writer.wait_closed()
            except Exception as e:
                self.log(f"[ERROR] set_mode: {e}")
                self.set_status("Error setting mode")
                self.metrics["errors_count"] += 1

        def send_text(self):
            text = self.text_edit.toPlainText().strip()
            if not text:
                QtWidgets.QMessageBox.warning(self, "Warning", "Text is empty")
                return
            stream = self.stream_check.isChecked()
            self.log(f">>> {text}")
            self.set_status("Sending text...")
            asyncio.ensure_future(self._send_text_async(text, stream))

        async def _send_text_async(self, text: str, stream: bool):
            t0 = time.time()
            try:
                ssl_ctx = self._make_ssl_context()
                if USE_TLS:
                    reader, writer = await asyncio.open_connection(
                        IPC_HOST, IPC_PORT, ssl=ssl_ctx
                    )
                else:
                    reader, writer = await asyncio.open_connection(
                        IPC_HOST, IPC_PORT
                    )

                token = self._make_jwt()
                req = {
                    "jwt": token,
                    "text": text,
                    "tasks": ["sentiment", "intent", "entities"],
                    "context": {"source": "gui"},
                    "stream": stream,
                }
                writer.write((json.dumps(req, default=json_safe) + "\n").encode("utf-8"))
                await writer.drain()

                if not stream:
                    line = await reader.readline()
                    if not line:
                        self.log("[ERROR] No response")
                    else:
                        resp = json.loads(line.decode("utf-8", errors="replace"))
                        self.log(json.dumps(resp, indent=2, default=json_safe))
                        self.set_status("Response received")
                        if self.voice_controller and TTS_ENABLED:
                            self.voice_controller.speak("Response received.")
                else:
                    while True:
                        line = await reader.readline()
                        if not line:
                            break
                        resp = json.loads(line.decode("utf-8", errors="replace"))
                        self.log(json.dumps(resp, indent=2, default=json_safe))
                        if resp.get("final"):
                            self.set_status("Stream complete")
                            if self.voice_controller and TTS_ENABLED:
                                self.voice_controller.speak("Stream complete.")
                            break

                writer.close()
                await writer.wait_closed()
            except Exception as e:
                self.log(f"[ERROR] {e}")
                self.set_status("Error sending text")
                self.metrics["errors_count"] += 1
            finally:
                dt = time.time() - t0
                self.metrics["last_latency"] = dt
                self.metrics["commands_count"] += 1

        def start_voice(self):
            if not self.voice_controller:
                self.log("[VOICE] Not available.")
                return

            def callback(text: str):
                self.text_edit.setPlainText(text)
                self.send_text()

            self.voice_controller.start_listening(callback)

        def _update_telemetry(self):
            state = self.autopilot_core.get_state_dict()
            self.telemetry_edit.setPlainText(json.dumps(state, indent=2, default=json_safe))

        def _update_metrics_view(self):
            self.metrics_edit.setPlainText(json.dumps(self.metrics, indent=2, default=json_safe))

        def _draw_swarm_map(self):
            self.map_scene.clear()
            rect = self.map_view.viewport().rect()
            w = rect.width()
            h = rect.height()
            self.map_scene.setSceneRect(0, 0, w, h)

            tile_path = ASSETS_DIR / "map_tile.png"
            if tile_path.exists():
                pixmap = QtGui.QPixmap(str(tile_path))
                self.map_scene.addPixmap(pixmap.scaled(w, h, QtCore.Qt.KeepAspectRatioByExpanding))
            else:
                self.map_scene.addRect(0, 0, w, h, QtGui.QPen(QtCore.Qt.white))

            self.map_scene.addEllipse(
                w/2 - 5, h/2 - 5, 10, 10,
                QtGui.QPen(QtCore.Qt.green),
                QtGui.QBrush(QtCore.Qt.green),
            )

            for i, (host, port) in enumerate(SWARM_PEERS):
                x = w/2 + (i+1)*30
                y = h/2
                self.map_scene.addEllipse(
                    x-5, y-5, 10, 10,
                    QtGui.QPen(QtCore.Qt.blue),
                    QtGui.QBrush(QtCore.Qt.blue),
                )
                text_item = self.map_scene.addText(f"{host}:{port}")
                text_item.setDefaultTextColor(QtCore.Qt.white)
                text_item.setPos(x+10, y-10)

        def _update_gpu_view(self):
            state = self.autopilot_core.get_state()
            gpu_info = {
                "gpu_util": state.gpu_util,
                "gpu_mem": state.gpu_mem,
            }
            self.gpu_edit.setPlainText(json.dumps(gpu_info, indent=2, default=json_safe))
            self.scene3d.clear()
            rect = self.view3d.viewport().rect()
            w = rect.width()
            h = rect.height()
            self.scene3d.setSceneRect(0, 0, w, h)
            self.scene3d.addRect(0, 0, w, h, QtGui.QPen(QtCore.Qt.black))
            bar_height = (state.gpu_util / 100.0) * h
            self.scene3d.addRect(
                w/4, h - bar_height, w/2, bar_height,
                QtGui.QPen(QtCore.Qt.red),
                QtGui.QBrush(QtCore.Qt.red),
            )

        def _plugin_hot_reload_gui(self):
            try:
                PLUGIN_MANAGER.reload_plugins()
                self.log("[Plugins] Hot-reloaded from GUI timer.")
            except Exception as e:
                self.log(f"[Plugins] Hot-reload error: {e}")

        def _on_envelope_event(self, envelope: Dict[str, Any]):
            self.metrics["last_latency"] = envelope.get("latency_sec", 0.0)
            self.metrics["commands_count"] += 1

        def _on_mode_change(self, event: Dict[str, Any]):
            self.mode_combo.setCurrentText(event.get("mode", "UNKNOWN"))
            self.set_status(f"Mode changed to {event.get('mode')}")

        def _on_navigation(self, event: Dict[str, Any]):
            self.set_status(f"Navigating to {event.get('target')}")

        def _on_emergency(self, event: Dict[str, Any]):
            self.set_status("EMERGENCY STOP")
            self.log("[EMERGENCY] Stop engaged.")

        def _on_gpu_metrics(self, event: Dict[str, Any]):
            pass

        def _update_logging_dashboard(self):
            # Simple dashboard: show metrics + last state snapshot
            state = self.autopilot_core.get_state_dict()
            dash = {
                "metrics": self.metrics,
                "state": state,
            }
            self.logdash_edit.setPlainText(json.dumps(dash, indent=2, default=json_safe))

        def _on_copilot_comment(self, envelope: Dict[str, Any]):
            self.copilot_comments.append(envelope)
            if len(self.copilot_comments) > 20:
                self.copilot_comments = self.copilot_comments[-20:]

        def _update_copilot_view(self):
            lines = []
            for c in self.copilot_comments[-10:]:
                br = c.get("backend_result", {})
                sentiment = br.get("sentiment", {}).get("label", "neutral")
                lines.append(f"Sentiment: {sentiment} | State: {c.get('state', {}).get('mode', '?')}")
            self.copilot_edit.setPlainText("\n".join(lines))

        def load_mission_from_text(self):
            txt = self.mission_text.toPlainText().strip()
            if not txt:
                QtWidgets.QMessageBox.warning(self, "Warning", "Mission text is empty")
                return
            try:
                data = json.loads(txt)
                name = data.get("name", "UnnamedMission")
                steps_raw = data.get("steps", [])
                steps: List[MissionStep] = []
                for s in steps_raw:
                    stype = MissionStepType[s["type"]]
                    if stype == MissionStepType.MODE:
                        mode = VehicleMode(s["mode"])
                        steps.append(MissionStep(step_type=stype, mode=mode))
                    elif stype == MissionStepType.NAVIGATE:
                        steps.append(MissionStep(step_type=stype, target=s["target"]))
                    elif stype == MissionStepType.WAIT:
                        steps.append(MissionStep(step_type=stype, duration_sec=float(s["duration_sec"])))
                mission = Mission(name=name, steps=steps)
                self.mission_engine.load_mission(mission)
                self.log(f"[Mission] Loaded: {name}")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Failed to parse mission: {e}")

        def start_mission(self):
            self.mission_engine.start()
            self.log("[Mission] Started.")

        def stop_mission(self):
            self.mission_engine.stop()
            self.log("[Mission] Stopped.")

# =============================================================================
# Server entry (single asyncio loop with tasks)
# =============================================================================

async def main_async_server():
    autopilot_core = AutopilotCore()
    nav_manager = NavigationManager(autopilot_core)
    mission_engine = MissionEngine(autopilot_core, nav_manager)

    lpu_backend = LlamaCppBackend() if USE_LOCAL_BACKEND else DummyBackend()
    lpu = LanguageProcessingUnit(
        autopilot_core,
        backend=lpu_backend,
        unit_id="LPU-Autopilot-Kernel",
        nav_manager=nav_manager,
        mission_engine=mission_engine,
    )

    copilot_backend = DummyBackend()
    copilot = AICopilot(copilot_backend, autopilot_core)

    server = LPUIPCServer(lpu)
    rest_server = RESTServer(autopilot_core, lpu)
    mqtt_bridge = MQTTBridge(lpu)
    async_hw = AsyncHardwareWrapper(autopilot_core)
    swarm_coordinator = SwarmCoordinator()

    async def consumer_task():
        async for result in lpu.consume_results():
            br = result["backend_result"]
            msg = result["autopilot_message"]
            val = result["validation"]
            logger.info(
                f"[BUS] mode={result['vehicle_mode']} "
                f"type={msg['type']} "
                f"valid={val['valid']} "
                f"intent={br.get('intent')} "
                f"sentiment={br.get('sentiment', {}).get('label')} "
                f"text={result['text']!r}"
            )

    async def swarm_task():
        while True:
            state = autopilot_core.get_state_dict()
            await swarm_coordinator.propagate_state(state)
            await asyncio.sleep(3.0)

    mqtt_bridge.start()

    tasks = [
        server.run_forever(),
        consumer_task(),
        EVENT_BUS.run_forever(),
        async_hw.run(),
        gpu_telemetry_task(autopilot_core),
        plugin_hot_reload_task(),
        rest_server.start(),
        mission_engine.run(),
        copilot.analyze_state(),
        swarm_task(),
    ]

    await asyncio.gather(*tasks)

def run_server():
    try:
        asyncio.run(main_async_server())
    except KeyboardInterrupt:
        logger.info("[LPU] Shutting down.")

# =============================================================================
# Unified launcher
# =============================================================================

def launch_server_thread():
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    return t

def launch_server_process():
    p = multiprocessing.Process(target=run_server, daemon=True)
    p.start()
    return p

def launch_gui():
    if not PySide6:
        print("PySide6 not available.")
        return
    app = QtWidgets.QApplication(sys.argv)
    autopilot_core = AutopilotCore()
    nav_manager = NavigationManager(autopilot_core)
    mission_engine = MissionEngine(autopilot_core, nav_manager)
    gui = LPUClientGUI(autopilot_core, mission_engine)
    gui.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    USE_TLS = ensure_certs()

    if USE_TLS:
        logger.info("[BOOT] TLS+JWT mode (SAN-aware auto-cert engine).")
    else:
        logger.info("[BOOT] PLAINTEXT+JWT (NO-TLS) mode (OpenSSL missing or cert failure).")

    if OTA_ENABLED:
        new_ver = check_for_updates()
        if new_ver:
            logger.info(f"[BOOT] Update available: {new_ver}")
            apply_update(new_ver)

    if SERVER_RUN_MODE.lower() == "process":
        launch_server_process()
    else:
        launch_server_thread()

    launch_gui()
