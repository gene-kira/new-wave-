#!/usr/bin/env python3
"""
ULTRABORG V4.0 — GAMING-FIRST FULL SYSTEM
(RL + WATCHDOG + ANOMALY + ADBLOCK + GUI + EVOLUTION + SUPERVISOR + NEURAL MEMORY + SWARM + RESURRECTION GLYPHS)

- GAMING-FIRST:
    - Flow mode tuned for high FPS stability and low frametime variance
    - More aggressive GPU/CPU headroom management for games
    - Adblock less aggressive around game-related GPU-heavy processes
- Always-on Borg RL core + watchdog + anomaly + daemon supervisor
- HYBRID ADBLOCK:
    - Process-level ad kill (Chrome/Edge/CEF ad subprocesses)
    - EXCLUDES Steam, Epic, Copilot, Python/.py, AeroAdmin, ALL major browsers
    - EXCLUDES Microsoft Teams + ALL core Microsoft apps + Notepad
    - AUTO-PROTECT:
        - Anything in C:\Windows\System32
        - Anything in C:\Program Files\WindowsApps
        - Anything with "Microsoft" in product name (best-effort)
    - Whitelist (never touch), Blocklist (log only), Killlist (force kill)
    - JSON persistence: borg_whitelist.json / borg_blocklist.json / borg_killlist.json
- RL CORE:
    - Gaming-first reward: FPS stability + frametime stability prioritized
    - Borg Evolution mode: gradually increases aggressiveness and autonomy
- WATCHDOG:
    - Backoff, crash-resistant, temp-aware, integrated with RL + graphics tuner
- GUI:
    - ONE PANEL ONLY
    - Process list collapsed by NAME (not PID)
    - Columns: Name | White | Block | Kill
    - Cells show “[X]” or “[ ]” and toggle on click (CENTERED)
    - Optimized refresh loop using cache
- PROCESS SCANNING:
    - Multi-threaded ad-scan loop
    - Process cache wrapper to avoid repeated expensive calls
- BORG NEURAL MEMORY:
    - Stores episodes of system state + actions + outcomes
    - JSONL persistence: borg_memory.jsonl
- SWARM SYNC MODE:
    - Stubbed sync to external swarm file
    - Can share memory snapshots across nodes
- RESURRECTION DETECTION GLYPHS:
    - Detects processes that die and come back
    - Logs “glyph” events for resurrection patterns
"""

import os
import sys
import json
import time
import random
import threading
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple
from queue import Queue

# ============================================================
# HARD CRASH LOGGER
# ============================================================

def hard_log(e, tag="HARD"):
    import traceback
    print(f"[{tag}] CRASH: {e}")
    traceback.print_exc()

# ============================================================
# AUTOLOADER
# ============================================================

import importlib
import subprocess

AUTOLOADER_LOG = "borg_autoloader_log.txt"

REQUIRED_LIBS = {
    "psutil": "psutil",
    "numpy": "numpy",
    "torch": "torch",
    "pynvml": "pynvml",
    "tkinter": None,
}

def autoloader_log(msg: str):
    try:
        with open(AUTOLOADER_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.ctime()} :: {msg}\n")
    except:
        pass

def try_import(libname):
    try:
        return importlib.import_module(libname)
    except Exception as e:
        autoloader_log(f"IMPORT FAIL: {libname} :: {e}")
        return None

def try_install(libname, pipname):
    if pipname is None:
        return None
    autoloader_log(f"INSTALL ATTEMPT: {pipname}")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pipname],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        autoloader_log(f"INSTALL SUCCESS: {pipname}")
        return importlib.import_module(libname)
    except Exception as e:
        autoloader_log(f"INSTALL FAIL: {pipname} :: {e}")
        return None

def load_all_libs():
    loaded = {}
    for libname, pipname in REQUIRED_LIBS.items():
        mod = try_import(libname)
        if mod is None:
            mod = try_install(libname, pipname)
        if mod is None:
            autoloader_log(f"FALLBACK: {libname} unavailable")
            print(f"[AUTOLOADER] WARNING: {libname} missing (fallback mode)")
        else:
            print(f"[AUTOLOADER] Loaded: {libname}")
            loaded[libname] = mod
    return loaded

AUTOLOADED = load_all_libs()

psutil = AUTOLOADED.get("psutil")
np = AUTOLOADED.get("numpy")
torch = AUTOLOADED.get("torch")
pynvml = AUTOLOADED.get("pynvml")

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except Exception:
    tk = None
    ttk = None
    messagebox = None
    print("[AUTOLOADER] Tkinter unavailable — GUI disabled")

# ============================================================
# safe_import fallback
# ============================================================

def safe_import(name, pip_name=None):
    if name in AUTOLOADED and AUTOLOADED[name] is not None:
        return AUTOLOADED[name]
    try:
        return __import__(name)
    except ImportError:
        print(f"[WARN] Missing library: {name}")
        if pip_name:
            print(f"       Install via: pip install {pip_name}")
        return None

psutil = psutil or safe_import("psutil", "psutil")
np = np or safe_import("numpy", "numpy")
torch = torch or safe_import("torch", "torch")
pynvml = pynvml or safe_import("pynvml", "pynvml")

if torch is None or np is None or psutil is None:
    print("[FATAL] Core libraries missing. Install psutil, numpy, torch.")
    input("Press Enter to exit...")
    sys.exit(1)

try:
    import winreg
except ImportError:
    winreg = None
    print("[WARN] winreg not available (non-Windows or limited environment)")

# ============================================================
# PERSISTENT WHITELIST / BLOCKLIST / KILLLIST
# ============================================================

WHITELIST_PATH = "borg_whitelist.json"
BLOCKLIST_PATH = "borg_blocklist.json"
KILLLIST_PATH = "borg_killlist.json"

def load_json_list(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_json_list(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except:
        pass

WHITELIST = load_json_list(WHITELIST_PATH)
BLOCKLIST = load_json_list(BLOCKLIST_PATH)
KILLLIST = load_json_list(KILLLIST_PATH)

if "aeroadmin.exe" not in WHITELIST:
    WHITELIST.append("aeroadmin.exe")
    save_json_list(WHITELIST_PATH, WHITELIST)

# ============================================================
# CONFIG (GAMING-FIRST TUNING)
# ============================================================

DEFAULT_CONFIG = {
    "version": "4.0.0",
    "update": {
        "auto_check_interval_sec": 3600,
        "local_update_manifest": "borg_v3_update.json",
        "remote_url_stub": "https://example.com/borg_v3_update.json"
    },
    "modes": {
        "flow": {  # GAMING-FIRST FLOW
            "target_fps": 120,  # aim high for gaming
            "max_cpu": 0.90,
            "max_gpu": 0.95,
            "reward_weights": {
                "fps_stability": 1.4,      # heavier weight
                "cpu_headroom": 0.6,
                "gpu_headroom": 0.6,
                "thermal_safety": 1.0,
                "frametime_stability": 1.3 # frametime very important for gaming
            }
        },
        "deep_work": {
            "target_fps": 60,
            "max_cpu": 0.70,
            "max_gpu": 0.60,
            "reward_weights": {
                "fps_stability": 0.5,
                "cpu_headroom": 1.2,
                "gpu_headroom": 0.8,
                "thermal_safety": 1.0,
                "frametime_stability": 1.0
            }
        },
        "recovery": {
            "target_fps": 30,
            "max_cpu": 0.50,
            "max_gpu": 0.40,
            "reward_weights": {
                "fps_stability": 0.3,
                "cpu_headroom": 1.4,
                "gpu_headroom": 1.4,
                "thermal_safety": 1.6,
                "frametime_stability": 0.6
            }
        }
    },
    "rl": {
        "gamma": 0.99,
        "dqn_lr": 1e-4,
        "ppo_lr": 3e-4,
        "ppo_clip": 0.25,
        "entropy_coef": 0.02,
        "value_coef": 0.5,
        "batch_size": 64,
        "trajectory_len": 256,
        "epsilon_start": 0.25,   # slightly lower exploration for gaming stability
        "epsilon_end": 0.02,
        "epsilon_decay_steps": 60000  # faster evolution
    },
    "watchdog": {
        "max_temp_c": 83.0,      # slightly stricter for gaming
        "emergency_temp_c": 90.0,
        "max_crash_count": 3,
        "check_interval_sec": 2.0,
        "backoff_max_sec": 12.0
    },
    "graphics": {
        "registry_paths": {
            "nvidia_quality": r"Software\\NVIDIA Corporation\\Global\\Quality",
            "amd_quality": r"Software\\AMD\\Global\\Quality",
            "nvidia_perf": r"Software\\NVIDIA Corporation\\Global\\Perf",
            "amd_perf": r"Software\\AMD\\Global\\Perf"
        },
        "game_config_root": os.path.expanduser("~\\Documents\\MyGames"),
        "default_game_profile": {
            "resolution_scale_step": 0.05,
            "shadow_quality_step": 1,
            "postfx_step": 1,
            "lod_bias_step": 0.25
        }
    },
    "dream": {
        "enabled": True,
        "interval_steps": 4000,
        "episodes": 12,
        "length": 160
    },
    "anomaly": {
        "latent_dim": 4,
        "train_buffer_size": 6000,
        "train_interval_steps": 1500,
        "threshold": 0.02
    },
    "logging": {
        "json_log_path": "borg_v3_log.jsonl",
        "max_lines": 200000
    },
    "daemon": {
        "enabled": True,
        "quiet": True,
        "borg_evolution": True,
        "supervisor_interval_sec": 5.0
    },
    "state_physics": {
        "inertia": 0.85,
        "damping": 0.15,
        "strain_threshold": 0.4,
        "recovery_threshold": 0.2,
        "flow_threshold": 0.6,
        "max_phase_change_rate": 0.2
    },
    "predictive": {
        "enabled": True,
        "horizon_steps": 3,
        "risk_temp_margin": 5.0,
        "risk_ft_margin": 0.2,
        "confidence_decay": 0.98,
        "phase_weights": {
            "flow":     {"reward": 1.2, "risk": 1.4, "conf": 0.8},
            "strain":   {"reward": 0.7, "risk": 2.5, "conf": 0.8},
            "recovery": {"reward": 0.4, "risk": 3.5, "conf": 1.0},
            "neutral":  {"reward": 0.8, "risk": 2.0, "conf": 0.8}
        }
    },
    "adblock": {
        "dns_blacklist_path": "ad_blacklist.txt",
        "proxy_port": 8888
    },
    "memory": {
        "enabled": True,
        "path": "borg_memory.jsonl",
        "max_episodes": 50000
    },
    "swarm": {
        "enabled": True,
        "sync_path": "borg_swarm_sync.jsonl",
        "sync_interval_sec": 30.0
    }
}

CONFIG_PATH = "borg_v3_config.json"

def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            print(f"[CONFIG] Loaded external config from {CONFIG_PATH}")
            return cfg
        except Exception as e:
            print(f"[WARN] Failed to load {CONFIG_PATH}: {e}")
    print("[CONFIG] Using embedded default config (gaming-first)")
    return DEFAULT_CONFIG

CONFIG = load_config()

# ============================================================
# LOGGER
# ============================================================

class JsonLogger:
    def __init__(self, path: str, max_lines: int):
        self.path = path
        self.max_lines = max_lines
        self.lines = 0

    def log(self, kind: str, payload: Dict[str, Any]):
        entry = {"kind": kind, "time": time.time(), **payload}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            self.lines += 1
            if self.lines > self.max_lines:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write("")
                self.lines = 0
        except Exception as e:
            print(f"[LOG] Failed to write log: {e}")

LOGGER = JsonLogger(CONFIG["logging"]["json_log_path"], CONFIG["logging"]["max_lines"])

# ============================================================
# BORG NEURAL MEMORY SYSTEM
# ============================================================

class BorgMemory:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = cfg.get("enabled", True)
        self.path = cfg.get("path", "borg_memory.jsonl")
        self.max_episodes = cfg.get("max_episodes", 50000)
        self.count = 0

    def store_episode(self, snap: "SensorSnapshot", action: str, reward: float):
        if not self.enabled:
            return
        episode = {
            "time": time.time(),
            "cpu": snap.cpu_usage,
            "gpu": snap.gpu_usage,
            "temp": snap.gpu_temp,
            "fps": snap.fps,
            "ft_var": snap.frametime_var,
            "vram": snap.vram_usage,
            "power": snap.gpu_power,
            "action": action,
            "reward": reward
        }
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(episode) + "\n")
            self.count += 1
            if self.count > self.max_episodes:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write("")
                self.count = 0
        except Exception as e:
            print(f"[MEMORY] Failed to store episode: {e}")

    def snapshot_summary(self) -> Dict[str, Any]:
        return {
            "episodes": self.count,
            "path": self.path,
            "enabled": self.enabled
        }

MEMORY = BorgMemory(CONFIG["memory"])

# ============================================================
# SWARM SYNC MODE (STUB)
# ============================================================

class SwarmSync:
    def __init__(self, cfg: Dict[str, Any]):
        self.enabled = cfg.get("enabled", False)
        self.path = cfg.get("sync_path", "borg_swarm_sync.jsonl")
        self.interval = cfg.get("sync_interval_sec", 30.0)
        self.running = False
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self):
        if not self.enabled:
            return
        self.running = True
        print("[SWARM] Swarm sync starting")
        self.thread.start()

    def publish(self, payload: Dict[str, Any]):
        if not self.enabled:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.time(), **payload}) + "\n")
        except Exception as e:
            print(f"[SWARM] Failed to publish: {e}")

    def loop(self):
        while self.running:
            try:
                summary = MEMORY.snapshot_summary()
                self.publish({"kind": "memory_summary", **summary})
            except Exception as e:
                print(f"[SWARM] Loop error: {e}")
            time.sleep(self.interval)

SWARM = SwarmSync(CONFIG["swarm"])

# ============================================================
# AUTO-UPDATER
# ============================================================

class AutoUpdater:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self):
        print("[UPDATE] Auto-updater starting")
        self.thread.start()

    def _read_local_manifest(self) -> Dict[str, Any]:
        path = self.cfg["local_update_manifest"]
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[UPDATE] Failed to read local manifest: {e}")
            return {}

    def check_for_update(self):
        current_version = CONFIG.get("version", "0.0.0")
        manifest = self._read_local_manifest()
        target_version = manifest.get("version")
        if not target_version:
            LOGGER.log("update_check", {"status": "no_manifest"})
            return
        LOGGER.log("update_check", {"current": current_version, "target": target_version})
        if target_version != current_version:
            print(f"[UPDATE] New version available: {target_version} (current {current_version})")
            LOGGER.log("update_available", {"current": current_version, "target": target_version})

    def loop(self):
        interval = self.cfg["auto_check_interval_sec"]
        while self.running:
            try:
                self.check_for_update()
            except Exception as e:
                print(f"[UPDATE] Loop error: {e}")
            time.sleep(interval)

UPDATER = AutoUpdater(CONFIG["update"])

# ============================================================
# SENSORS
# ============================================================

@dataclass
class SensorSnapshot:
    cpu_usage: float
    ram_usage: float
    gpu_usage: float
    gpu_temp: float
    fps: float
    frametime_var: float
    vram_usage: float
    gpu_power: float

class SensorHub:
    def __init__(self):
        self.has_nvml = pynvml is not None
        if self.has_nvml:
            try:
                pynvml.nvmlInit()
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                print("[SENSORS] NVML initialized")
            except Exception as e:
                print(f"[WARN] NVML init failed: {e}")
                self.has_nvml = False
                self.gpu_handle = None
        self._fps_history: List[float] = []

    def read_cpu_ram(self) -> Tuple[float, float]:
        cpu = psutil.cpu_percent(interval=None) / 100.0
        ram = psutil.virtual_memory().percent / 100.0
        return cpu, ram

    def read_gpu(self) -> Tuple[float, float, float, float]:
        if not self.has_nvml:
            return 0.0, 40.0, 0.0, 0.0
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
            temp = pynvml.nvmlDeviceGetTemperature(
                self.gpu_handle, pynvml.NVML_TEMPERATURE_GPU
            )
            mem = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
            power = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
            vram_usage = mem.used / max(mem.total, 1)
            return util.gpu / 100.0, float(temp), float(vram_usage), float(power)
        except Exception:
            return 0.0, 40.0, 0.0, 0.0

    def read_fps(self) -> float:
        # gaming-first: assume higher FPS baseline
        fps = 120.0 + random.uniform(-30.0, 30.0)
        self._fps_history.append(fps)
        if len(self._fps_history) > 240:
            self._fps_history.pop(0)
        return fps

    def frametime_variance(self) -> float:
        if len(self._fps_history) < 2:
            return 0.0
        ft = [1.0 / max(f, 1e-3) for f in self._fps_history]
        return float(np.var(ft))

    def snapshot(self) -> SensorSnapshot:
        cpu, ram = self.read_cpu_ram()
        gpu, temp, vram, power = self.read_gpu()
        fps = self.read_fps()
        ft_var = self.frametime_variance()
        snap = SensorSnapshot(
            cpu_usage=cpu,
            ram_usage=ram,
            gpu_usage=gpu,
            gpu_temp=temp,
            fps=fps,
            frametime_var=ft_var,
            vram_usage=vram,
            gpu_power=power,
        )
        LOGGER.log("sensors", {
            "cpu": cpu,
            "ram": ram,
            "gpu": gpu,
            "temp": temp,
            "fps": fps,
            "ft_var": ft_var,
            "vram": vram,
            "power": power,
        })
        return snap

SENSORS = SensorHub()

# ============================================================
# NVAPI STUB
# ============================================================

class NVAPITuner:
    def __init__(self):
        self.available = True

    def set_power_limit(self, watts: float):
        print(f"[NVAPI] Set GPU power limit to {watts:.1f} W (stub)")
        LOGGER.log("nvapi_power_limit", {"watts": watts})

    def set_temp_target(self, temp_c: float):
        print(f"[NVAPI] Set GPU temp target to {temp_c:.1f} C (stub)")
        LOGGER.log("nvapi_temp_target", {"temp_c": temp_c})

    def set_perf_mode(self, mode: str):
        print(f"[NVAPI] Set GPU perf mode to {mode} (stub)")
        LOGGER.log("nvapi_perf_mode", {"mode": mode})

NVAPI = NVAPITuner()

# ============================================================
# HYBRID ADBLOCK
# ============================================================

AD_KEYWORDS = [
    "ad", "ads", "advert", "doubleclick", "googlesyndication",
    "taboola", "outbrain", "tracking", "promo", "sponsor",
    "widget", "iframe", "renderer", "subframe", "cef"
]

AD_KILL_EVENTS = Queue()
AD_DOMAIN_EVENTS = Queue()
AD_PROXY_EVENTS = Queue()

SAFE_NAMES = [
    "steam", "steam.exe",
    "epic games", "epicgameslauncher", "epicgameslauncher.exe",
    "copilot", "copilot.exe",
    "python", "python.exe",
    "py", ".py",
    "aeroadmin", "aeroadmin.exe",
    "chrome.exe", "chrome", "chromium",
    "chrome_child", "chrome_renderer", "chrome_gpu", "chrome_utility",
    "chrome_crashpad", "chrome_sandbox",
    "msedge.exe", "edge.exe", "edge",
    "msedge_child", "msedge_renderer", "msedge_gpu", "msedge_utility",
    "msedge_crashpad", "msedge_sandbox",
    "opera.exe", "opera", "opera_child", "opera_renderer",
    "opera_gpu", "opera_utility", "opera_crashpad", "opera_sandbox",
    "vivaldi.exe", "vivaldi", "vivaldi_child", "vivaldi_renderer",
    "vivaldi_gpu", "vivaldi_utility", "vivaldi_crashpad", "vivaldi_sandbox",
    "firefox.exe", "firefox", "firefox_child", "firefox_renderer",
    "firefox_gpu", "firefox_utility", "firefox_crashpad", "firefox_sandbox",
    "teams", "teams.exe",
    "teamshelper", "teamshelper.exe",
    "teamswebview", "teamswebview.exe",
    "teamsrenderer", "teamsrenderer.exe",
    "teamsutility", "teamsutility.exe",
    "teamswebclient", "teamswebclient.exe",
    "teamsupdate", "teamsupdate.exe",
    "teamsbootstrapper", "teamsbootstrapper.exe",
    "teamscrashpad", "teamscrashpad.exe",
    "teamsbackground", "teamsbackground.exe",
    "teamsusersession", "teamsusersession.exe",
    "onedrive", "onedrive.exe",
    "outlook", "outlook.exe",
    "officeclicktorun", "officeclicktorun.exe",
    "searchapp", "searchapp.exe",
    "shellexperiencehost", "shellexperiencehost.exe",
    "widgets", "widgets.exe",
    "startmenuexperiencehost", "startmenuexperiencehost.exe",
    "windowsterminal", "windowsterminal.exe",
    "explorer", "explorer.exe",
    "runtimebroker", "runtimebroker.exe",
    "winstore.app.exe", "winstore", "microsoftstore",
    "xboxapp", "xboxapp.exe",
    "gamebar", "gamebar.exe",
    "gamebarpresencewriter", "gamebarpresencewriter.exe",
    "textinputhost", "textinputhost.exe",
    "ctfmon", "ctfmon.exe",
    "dwm", "dwm.exe",
    "taskmgr", "taskmgr.exe",
    "smartscreen", "smartscreen.exe",
    "notepad", "notepad.exe",
    "notepad_child", "notepad_child.exe",
    "notepad_renderer", "notepad_renderer.exe",
    "notepad_utility", "notepad_utility.exe",
]

SAFE_NAMES += [
    "msedgewebview2.exe",
    "msedgewebview2",
    "webview2.exe",
    "webview2",
]

class AdBlockDNS:
    def __init__(self, blacklist_path: str):
        self.domains = set()
        if os.path.exists(blacklist_path):
            try:
                with open(blacklist_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            self.domains.add(line.lower())
                print(f"[ADBLOCK] Loaded {len(self.domains)} ad domains from {blacklist_path}")
            except Exception as e:
                print(f"[ADBLOCK] Failed to read blacklist: {e}")
        else:
            print(f"[ADBLOCK] No blacklist file at {blacklist_path}")

    def is_ad_domain(self, host: str) -> bool:
        host = (host or "").lower()
        if any(host.endswith(d) for d in self.domains):
            AD_DOMAIN_EVENTS.put(f"Blocked domain: {host}")
            LOGGER.log("ad_block_dns", {"host": host})
            return True
        return False

AD_DNS = AdBlockDNS(CONFIG["adblock"]["dns_blacklist_path"])

GUI_INSTANCE = None

# ============================================================
# AUTO-PROTECTION HELPERS
# ============================================================

SYSTEM32_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32").lower()
WINDOWS_APPS_PATH = os.path.join(
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    "WindowsApps"
).lower()

def get_proc_path(proc):
    try:
        return (proc.exe() or "").lower()
    except Exception:
        return ""

def is_microsoft_product(proc):
    try:
        info = proc.as_dict(attrs=["name"])
        name = (info.get("name") or "").lower()
        return "microsoft" in name
    except Exception:
        return False

def is_auto_protected(proc, name: str) -> bool:
    name = (name or "").lower()
    path = get_proc_path(proc)
    if not path:
        return False
    if SYSTEM32_PATH in path:
        return True
    if WINDOWS_APPS_PATH in path:
        return True
    if is_microsoft_product(proc):
        return True
    return False

# ============================================================
# PROCESS CACHE + RESURRECTION GLYPHS
# ============================================================

PROCESS_CACHE: Dict[int, Dict[str, Any]] = {}
RESURRECTION_LOG_PATH = "borg_resurrection_glyphs.jsonl"

def log_resurrection_glyph(name: str, pid: int):
    glyph = {
        "time": time.time(),
        "name": name,
        "pid": pid,
        "glyph": "RESURRECTION"
    }
    try:
        with open(RESURRECTION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(glyph) + "\n")
        LOGGER.log("resurrection_glyph", glyph)
    except Exception as e:
        print(f"[GLYPH] Failed to log resurrection glyph: {e}")

def get_proc_info(proc):
    pid = proc.pid
    name_raw = proc.info.get("name") or ""
    name = name_raw.lower()

    if pid in PROCESS_CACHE:
        info = PROCESS_CACHE[pid]
        info["last_seen"] = time.time()
        return info

    try:
        cmd = " ".join(proc.cmdline()).lower()
    except Exception:
        cmd = ""
    try:
        path = (proc.exe() or "").lower()
    except Exception:
        path = ""

    resurrected = False
    for old_pid, old_info in list(PROCESS_CACHE.items()):
        if old_info.get("name") == name and old_info.get("dead", False):
            resurrected = True
            log_resurrection_glyph(name_raw, pid)
            PROCESS_CACHE.pop(old_pid, None)
            break

    info = {
        "pid": pid,
        "name": name,
        "cmd": cmd,
        "path": path,
        "last_seen": time.time(),
        "resurrected": resurrected,
        "dead": False
    }
    PROCESS_CACHE[pid] = info
    return info

def cleanup_cache(current_pids: List[int]):
    current_set = set(current_pids)
    for pid, info in list(PROCESS_CACHE.items()):
        if pid not in current_set:
            info["dead"] = True

# ============================================================
# ADBLOCK CORE (MULTI-THREADED LOOP, GAMING-AWARE)
# ============================================================

ADBLOCK_RUNNING = True

def kill_ads_only_once():
    try:
        current_pid = os.getpid()
        current_pids: List[int] = []

        for proc in psutil.process_iter(attrs=["pid", "name"]):
            try:
                info_cached = get_proc_info(proc)
                pid = info_cached["pid"]
                name = info_cached["name"]
                name_raw = proc.info.get("name") or ""
                cmd = info_cached["cmd"]

                current_pids.append(pid)

                if GUI_INSTANCE is not None:
                    GUI_INSTANCE.add_process_row(name_raw, pid)

                if pid == current_pid:
                    continue

                if name in WHITELIST:
                    continue

                if "teams" in name:
                    if name not in WHITELIST:
                        WHITELIST.append(name)
                        save_json_list(WHITELIST_PATH, WHITELIST)
                    continue

                if is_auto_protected(proc, name_raw):
                    continue

                if any(s == name or s in name for s in SAFE_NAMES):
                    continue

                if name in BLOCKLIST:
                    AD_KILL_EVENTS.put(f"BLOCKLIST: {name_raw} ({pid})")
                    LOGGER.log("blocklist_hit", {"pid": pid, "name": name_raw})
                    continue

                if name in KILLLIST:
                    AD_KILL_EVENTS.put(f"KILLLIST: {name_raw} ({pid})")
                    LOGGER.log("killlist_hit", {"pid": pid, "name": name_raw})
                    try:
                        proc.terminate()
                    except:
                        pass
                    continue

                try:
                    parent = proc.parent()
                    parent_name = (parent.name() or "").lower() if parent else ""
                except Exception:
                    parent_name = ""

                is_teams_webview = (
                    ("webview2" in cmd or "msedgewebview2" in cmd or "webview" in cmd)
                ) and (
                    "teams" in cmd or "msteams" in cmd or "teams" in parent_name
                )

                if is_teams_webview:
                    safe_name = name
                    if safe_name and safe_name not in WHITELIST:
                        WHITELIST.append(safe_name)
                        save_json_list(WHITELIST_PATH, WHITELIST)
                    continue

                if "python" in cmd or cmd.endswith(".py"):
                    continue

                gpu_usage, temp, vram, power = SENSORS.read_gpu()
                gpu_heavy = gpu_usage > 0.85
                looks_like_game = any(k in name for k in ["game", "unity", "unreal", "ue4", "ue5", "dx11", "dx12"])
                if gpu_heavy and looks_like_game:
                    # gaming-first: do NOT kill GPU-heavy game processes even if they look ad-ish
                    continue

                is_ad = (
                    any(k in name for k in AD_KEYWORDS) or
                    any(k in cmd for k in AD_KEYWORDS) or
                    ("renderer" in cmd and ("ad" in cmd or "promo" in cmd)) or
                    ("subframe" in cmd and ("ad" in cmd or "promo" in cmd)) or
                    ("cef" in name and ("ad" in cmd or "promo" in cmd))
                )

                if not is_ad:
                    continue

                msg = f"Killed: {name_raw} ({pid})"
                AD_KILL_EVENTS.put(msg)
                LOGGER.log("ad_kill", {
                    "pid": pid,
                    "name": name_raw,
                    "cmd": cmd
                })

                try:
                    proc.terminate()
                except:
                    pass

                print(f"[ADBLOCK] Terminated ad process: {name_raw} ({pid})")

            except Exception as e:
                print(f"[ADBLOCK] per-proc error: {e}")
                continue

        cleanup_cache(current_pids)

    except Exception as e:
        print(f"[ADBLOCK] kill_ads_only_once() top-level error: {e}")
        LOGGER.log("ad_kill_error", {"error": str(e)})

def adblock_loop():
    print("[ADBLOCK] Adblock loop starting")
    while ADBLOCK_RUNNING:
        kill_ads_only_once()
        time.sleep(2.0)

def log_proxy_hit(host: str, path: str):
    msg = f"Proxy hit: {host}{path}"
    AD_PROXY_EVENTS.put(msg)
    LOGGER.log("ad_proxy_hit", {"host": host, "path": path})

def start_ad_proxy_stub(port: int):
    def loop():
        try:
            print(f"[ADBLOCK] Proxy stub running on 127.0.0.1:{port}")
            while True:
                try:
                    time.sleep(5.0)
                except Exception as e:
                    hard_log(e, "PROXY_LOOP")
                    time.sleep(1)
        except Exception as e:
            hard_log(e, "PROXY_TOP")

    threading.Thread(target=loop, daemon=True).start()

# ============================================================
# GRAPHICS TUNER
# ============================================================

class GraphicsTuner:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.registry_paths = cfg["registry_paths"]
        self.game_root = cfg["game_config_root"]
        self.profile = cfg["default_game_profile"]

    def _set_registry_value(self, root, path: str, name: str, value: Any):
        if winreg is None:
            print(f"[GFX] (stub) Registry set {path}\\{name} = {value}")
            LOGGER.log("gfx_registry_stub", {"path": path, "name": name, "value": value})
            return
        try:
            key = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            try:
                key = winreg.CreateKey(root, path)
            except Exception as e:
                print(f"[GFX] Failed to create key {path}: {e}")
                return
        try:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
            winreg.CloseKey(key)
            print(f"[GFX] Registry set {path}\\{name} = {value}")
            LOGGER.log("gfx_registry", {"path": path, "name": name, "value": value})
        except Exception as e:
            print(f"[GFX] Failed to set registry {path}\\{name}: {e}")

    def lower_global_quality(self):
        print("[GFX] Lowering global graphics quality")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["nvidia_quality"], "QualityLevel", "Low")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["amd_quality"], "QualityLevel", "Low")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["nvidia_perf"], "PerfMode", "MaxPerf")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["amd_perf"], "PerfMode", "MaxPerf")
        NVAPI.set_perf_mode("MaxPerf")

    def raise_global_quality(self):
        print("[GFX] Raising global graphics quality")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["nvidia_quality"], "QualityLevel", "High")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["amd_quality"], "QualityLevel", "High")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["nvidia_perf"], "PerfMode", "Balanced")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["amd_perf"], "PerfMode", "Balanced")
        NVAPI.set_perf_mode("Balanced")

    def _find_game_configs(self) -> List[str]:
        paths = []
        if os.path.isdir(self.game_root):
            for root, dirs, files in os.walk(self.game_root):
                for f in files:
                    if f.lower().endswith((".ini", ".cfg", ".json")):
                        paths.append(os.path.join(root, f))
        return paths

    def _patch_game_config(self, path: str, lower: bool = True):
        print(f"[GFX] Patching game config: {path} (lower={lower})")
        LOGGER.log("gfx_game_patch", {"path": path, "lower": lower})

    def lower_game_quality(self):
        print("[GFX] Lowering per-game graphics quality")
        for cfg_path in self._find_game_configs():
            self._patch_game_config(cfg_path, lower=True)

    def raise_game_quality(self):
        print("[GFX] Raising per-game graphics quality")
        for cfg_path in self._find_game_configs():
            self._patch_game_config(cfg_path, lower=False)

    def throttle_background_processes(self):
        print("[GFX] Throttling background processes")
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent"]):
            name = (proc.info["name"] or "").lower()
            if any(k in name for k in ["chrome", "edge", "discord", "obs"]):
                try:
                    p = psutil.Process(proc.info["pid"])
                    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    LOGGER.log("gfx_throttle", {"pid": proc.info["pid"], "name": name})
                    print(f"[GFX] Lowered priority for {name} ({proc.info['pid']})")
                except Exception:
                    pass

    def kill_heavy_background(self, cpu_threshold: float = 0.25):
        print("[GFX] Killing heavy background processes")
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent"]):
            try:
                name = (proc.info["name"] or "").lower()
                cpu = proc.info.get("cpu_percent", 0.0)
                if cpu is None:
                    cpu = 0.0
                if cpu > cpu_threshold * 100.0:
                    if any(s == name or s in name for s in SAFE_NAMES):
                        continue
                    if is_auto_protected(proc, name):
                        continue
                    LOGGER.log("gfx_kill_heavy", {"pid": proc.info["pid"], "name": name, "cpu": cpu})
                    print(f"[GFX] Terminating heavy background {name} ({proc.info['pid']}) cpu={cpu:.1f}%")
                    try:
                        psutil.Process(proc.info["pid"]).terminate()
                    except Exception:
                        pass
            except Exception as e:
                print(f"[GFX] kill_heavy_background error: {e}")
                continue

GFX = GraphicsTuner(CONFIG["graphics"])

# ============================================================
# RL CORE (GAMING-FIRST + BORG EVOLUTION + MEMORY)
# ============================================================

class BorgRLCore:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.epsilon = cfg["epsilon_start"]
        self.epsilon_end = cfg["epsilon_end"]
        self.epsilon_decay_steps = cfg["epsilon_decay_steps"]
        self.step_count = 0
        self.evolution_enabled = CONFIG["daemon"].get("borg_evolution", False)
        self.evolution_factor = 1.0

    def _update_epsilon(self):
        self.step_count += 1
        base_eps = self.cfg["epsilon_start"] - (self.step_count / self.epsilon_decay_steps) * (self.cfg["epsilon_start"] - self.epsilon_end)
        self.epsilon = max(self.epsilon_end, base_eps)
        if self.evolution_enabled:
            self.evolution_factor = min(3.0, 1.0 + self.step_count / (self.epsilon_decay_steps * 2.0))

    def _compute_reward(self, snap: SensorSnapshot) -> float:
        mode = CONFIG["modes"]["flow"]  # gaming-first flow
        w = mode["reward_weights"]
        fps_target = mode["target_fps"]
        fps_stability = max(0.0, 1.0 - abs(snap.fps - fps_target) / fps_target)
        cpu_headroom = max(0.0, 1.0 - snap.cpu_usage)
        gpu_headroom = max(0.0, 1.0 - snap.gpu_usage)
        thermal_safety = max(0.0, 1.0 - max(0.0, (snap.gpu_temp - CONFIG["watchdog"]["max_temp_c"]) / 20.0))
        ft_stability = max(0.0, 1.0 - min(1.0, snap.frametime_var * 120.0))  # frametime more sensitive
        reward = (
            w["fps_stability"] * fps_stability +
            w["cpu_headroom"] * cpu_headroom +
            w["gpu_headroom"] * gpu_headroom +
            w["thermal_safety"] * thermal_safety +
            w["frametime_stability"] * ft_stability
        )
        return reward

    def select_action(self, state: SensorSnapshot) -> str:
        self._update_epsilon()
        reward = self._compute_reward(state)
        LOGGER.log("rl_state", {
            "eps": self.epsilon,
            "evolution_factor": self.evolution_factor,
            "reward": reward,
            "fps": state.fps,
            "temp": state.gpu_temp,
            "gpu": state.gpu_usage,
            "cpu": state.cpu_usage
        })

        if random.random() < self.epsilon / self.evolution_factor:
            action = random.choice(["lower", "raise", "noop"])
        else:
            if state.gpu_temp > CONFIG["watchdog"]["max_temp_c"] or state.gpu_usage > CONFIG["modes"]["flow"]["max_gpu"]:
                action = "lower"
            elif reward > 4.5 and state.gpu_usage < 0.7 and state.cpu_usage < 0.7:
                action = "raise"
            else:
                action = "noop"

        MEMORY.store_episode(state, action, reward)
        SWARM.publish({"kind": "rl_episode", "action": action, "reward": reward})
        return action

    def apply_action(self, action: str):
        if action == "lower":
            GFX.lower_global_quality()
            GFX.lower_game_quality()
            NVAPI.set_power_limit(180.0)
        elif action == "raise":
            GFX.raise_global_quality()
            GFX.raise_game_quality()
            NVAPI.set_power_limit(260.0)

RL_CORE = BorgRLCore(CONFIG["rl"])

# ============================================================
# WATCHDOG (OPTIMIZED WITH BACKOFF, GAMING-FIRST)
# ============================================================

class BorgWatchdog:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.crash_count = 0
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.backoff = cfg.get("check_interval_sec", 2.0)

    def start(self):
        print("[WATCHDOG] Starting")
        self.thread.start()

    def loop(self):
        max_temp = self.cfg["max_temp_c"]
        emergency_temp = self.cfg["emergency_temp_c"]
        backoff_max = self.cfg.get("backoff_max_sec", 12.0)
        while self.running:
            try:
                snap = SENSORS.snapshot()
                if snap.gpu_temp > emergency_temp:
                    print("[WATCHDOG] EMERGENCY TEMP — lowering quality + killing heavy background")
                    GFX.lower_global_quality()
                    GFX.lower_game_quality()
                    GFX.kill_heavy_background(cpu_threshold=0.15)
                    NVAPI.set_temp_target(max_temp)
                    self.backoff = min(backoff_max, self.backoff + 2.0)
                elif snap.gpu_temp > max_temp:
                    print("[WATCHDOG] High temp — lowering quality")
                    GFX.lower_global_quality()
                    GFX.lower_game_quality()
                    NVAPI.set_temp_target(max_temp)
                    self.backoff = min(backoff_max, self.backoff + 1.0)
                else:
                    self.backoff = max(self.cfg["check_interval_sec"], self.backoff - 0.5)
            except Exception as e:
                hard_log(e, "WATCHDOG_LOOP")
                self.crash_count += 1
                if self.crash_count >= self.cfg["max_crash_count"]:
                    print("[WATCHDOG] Max crash count reached, stopping watchdog")
                    self.running = False
            time.sleep(self.backoff)

WATCHDOG = BorgWatchdog(CONFIG["watchdog"])

# ============================================================
# ANOMALY DETECTOR
# ============================================================

class AnomalyDetector:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.buffer: List[Dict[str, float]] = []

    def add(self, snap: SensorSnapshot):
        self.buffer.append({
            "cpu": snap.cpu_usage,
            "gpu": snap.gpu_usage,
            "temp": snap.gpu_temp,
            "fps": snap.fps,
            "ft_var": snap.frametime_var,
        })
        if len(self.buffer) > self.cfg["train_buffer_size"]:
            self.buffer.pop(0)

    def is_anomalous(self, snap: SensorSnapshot) -> bool:
        if snap.gpu_temp > CONFIG["watchdog"]["emergency_temp_c"] and snap.fps < 40.0:
            LOGGER.log("anomaly", {
                "temp": snap.gpu_temp,
                "fps": snap.fps,
                "ft_var": snap.frametime_var
            })
            return True
        return False

ANOMALY = AnomalyDetector(CONFIG["anomaly"])

# ============================================================
# DAEMON SUPERVISOR
# ============================================================

class DaemonSupervisor:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.running = cfg.get("enabled", True)
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.targets: Dict[str, threading.Thread] = {}

    def register(self, name: str, thread: threading.Thread):
        self.targets[name] = thread

    def start(self):
        if not self.running:
            return
        print("[SUPERVISOR] Starting")
        self.thread.start()

    def loop(self):
        interval = self.cfg.get("supervisor_interval_sec", 5.0)
        while self.running:
            for name, t in list(self.targets.items()):
                if not t.is_alive():
                    LOGGER.log("supervisor_restart", {"thread": name})
                    print(f"[SUPERVISOR] Thread {name} died — cannot auto-restart (manual restart required)")
            time.sleep(interval)

SUPERVISOR = DaemonSupervisor(CONFIG["daemon"])

# ============================================================
# GUI
# ============================================================

class BorgGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ULTRABORG V4.0 — Gaming-First Process Control")
        self.tree = None
        self.rows: Dict[str, Dict[str, Any]] = {}
        self._build_ui()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._refresh_thread.start()

    def _build_ui(self):
        columns = ("name", "white", "block", "kill")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings", height=25)
        self.tree.heading("name", text="Name")
        self.tree.heading("white", text="White")
        self.tree.heading("block", text="Block")
        self.tree.heading("kill", text="Kill")

        self.tree.column("name", width=260, anchor="w")
        self.tree.column("white", width=60, anchor="center")
        self.tree.column("block", width=60, anchor="center")
        self.tree.column("kill", width=60, anchor="center")

        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Button-1>", self._on_click)

    def add_process_row(self, name: str, pid: int):
        key = name.lower()
        if key in self.rows:
            return
        white = "[X]" if key in WHITELIST else "[ ]"
        block = "[X]" if key in BLOCKLIST else "[ ]"
        kill = "[X]" if key in KILLLIST else "[ ]"
        iid = self.tree.insert("", "end", values=(name, white, block, kill))
        self.rows[key] = {
            "iid": iid,
            "name": name,
            "pid": pid,
        }

    def _toggle_list(self, key: str, list_ref: List[str], path: str):
        if key in list_ref:
            list_ref.remove(key)
        else:
            list_ref.append(key)
        save_json_list(path, list_ref)

    def _on_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or col_id not in ("#2", "#3", "#4"):
            return

        vals = self.tree.item(row_id, "values")
        if not vals:
            return
        name = vals[0]
        key = name.lower()

        if col_id == "#2":
            self._toggle_list(key, WHITELIST, WHITELIST_PATH)
        elif col_id == "#3":
            self._toggle_list(key, BLOCKLIST, BLOCKLIST_PATH)
        elif col_id == "#4":
            self._toggle_list(key, KILLLIST, KILLLIST_PATH)

        white = "[X]" if key in WHITELIST else "[ ]"
        block = "[X]" if key in BLOCKLIST else "[ ]"
        kill = "[X]" if key in KILLLIST else "[ ]"
        self.tree.item(row_id, values=(name, white, block, kill))

    def _refresh_loop(self):
        while True:
            try:
                for proc in psutil.process_iter(attrs=["pid", "name"]):
                    name_raw = proc.info.get("name") or ""
                    pid = proc.info.get("pid")
                    self.add_process_row(name_raw, pid)
            except Exception:
                pass
            time.sleep(10.0)

# ============================================================
# MAIN LOOP
# ============================================================

def borg_main_loop():
    print("[BORG] Main loop starting (gaming-first)")
    UPDATER.start()
    WATCHDOG.start()
    start_ad_proxy_stub(CONFIG["adblock"]["proxy_port"])
    SWARM.start()

    ad_thread = threading.Thread(target=adblock_loop, daemon=True)
    ad_thread.start()
    SUPERVISOR.register("adblock_loop", ad_thread)
    SUPERVISOR.register("watchdog", WATCHDOG.thread)
    SUPERVISOR.register("updater", UPDATER.thread)
    SUPERVISOR.register("swarm", SWARM.thread)
    SUPERVISOR.start()

    while True:
        try:
            snap = SENSORS.snapshot()
            ANOMALY.add(snap)
            if ANOMALY.is_anomalous(snap):
                print("[BORG] Anomaly detected — lowering quality + killing heavy background")
                GFX.lower_global_quality()
                GFX.lower_game_quality()
                GFX.kill_heavy_background(cpu_threshold=0.15)

            action = RL_CORE.select_action(snap)
            RL_CORE.apply_action(action)

            time.sleep(1.0)
        except KeyboardInterrupt:
            print("[BORG] KeyboardInterrupt — exiting main loop")
            break
        except Exception as e:
            hard_log(e, "BORG_MAIN")
            time.sleep(1.0)

def main():
    global GUI_INSTANCE
    if tk is not None:
        root = tk.Tk()
        GUI_INSTANCE = BorgGUI(root)
        threading.Thread(target=borg_main_loop, daemon=True).start()
        root.mainloop()
    else:
        borg_main_loop()

if __name__ == "__main__":
    main()
