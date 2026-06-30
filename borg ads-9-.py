#!/usr/bin/env python3
"""
ULTRABORG V3.7 — SINGLE PANEL PROCESS GOVERNOR (TEXT TOGGLES, CENTERED)
- Always‑on Borg RL core + watchdog + anomaly
- HYBRID ADBLOCK:
    - Process‑level ad kill (Chrome/Edge/CEF ad subprocesses)
    - EXCLUDES Steam, Epic, Copilot, Python/.py, AeroAdmin, ALL major browsers
    - EXCLUDES Microsoft Teams + ALL core Microsoft apps
    - Whitelist (never touch), Blocklist (log only), Killlist (force kill)
    - JSON persistence: borg_whitelist.json / borg_blocklist.json / borg_killlist.json
- GUI:
    - ONE TAB ONLY
    - Process list collapsed by NAME (not PID)
    - Columns: Name | White | Block | Kill
    - Cells show “[X]” or “[ ]” and toggle on click (CENTERED)
    - No embedded widgets, no drifting checkboxes
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
# CONFIG
# ============================================================

DEFAULT_CONFIG = {
    "version": "3.7.0",
    "update": {
        "auto_check_interval_sec": 3600,
        "local_update_manifest": "borg_v3_update.json",
        "remote_url_stub": "https://example.com/borg_v3_update.json"
    },
    "modes": {
        "flow": {
            "target_fps": 90,
            "max_cpu": 0.85,
            "max_gpu": 0.90,
            "reward_weights": {
                "fps_stability": 1.0,
                "cpu_headroom": 0.7,
                "gpu_headroom": 0.7,
                "thermal_safety": 1.0,
                "frametime_stability": 0.8
            }
        },
        "deep_work": {
            "target_fps": 60,
            "max_cpu": 0.70,
            "max_gpu": 0.60,
            "reward_weights": {
                "fps_stability": 0.6,
                "cpu_headroom": 1.0,
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
                "fps_stability": 0.4,
                "cpu_headroom": 1.2,
                "gpu_headroom": 1.2,
                "thermal_safety": 1.5,
                "frametime_stability": 0.5
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
        "epsilon_start": 0.3,
        "epsilon_end": 0.02,
        "epsilon_decay_steps": 80000
    },
    "watchdog": {
        "max_temp_c": 85.0,
        "emergency_temp_c": 92.0,
        "max_crash_count": 3,
        "check_interval_sec": 3.0
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
        "enabled": False,
        "quiet": True
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
            "flow":     {"reward": 1.0, "risk": 1.5, "conf": 0.7},
            "strain":   {"reward": 0.7, "risk": 2.5, "conf": 0.8},
            "recovery": {"reward": 0.4, "risk": 3.5, "conf": 1.0},
            "neutral":  {"reward": 0.8, "risk": 2.0, "conf": 0.8}
        }
    },
    "adblock": {
        "dns_blacklist_path": "ad_blacklist.txt",
        "proxy_port": 8888
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
    print("[CONFIG] Using embedded default config")
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
        fps = 60.0 + random.uniform(-15.0, 15.0)
        self._fps_history.append(fps)
        if len(self._fps_history) > 120:
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
    "widget", "iframe", "renderer", "subframe"
]

AD_KILL_EVENTS = Queue()
AD_DOMAIN_EVENTS = Queue()
AD_PROXY_EVENTS = Queue()

SAFE_NAMES = [
    # CORE SAFE APPS
    "steam", "steam.exe",
    "epic games", "epicgameslauncher", "epicgameslauncher.exe",
    "copilot", "copilot.exe",
    "python", "python.exe",
    "py", ".py",
    "aeroadmin", "aeroadmin.exe",

    # FULL BROWSER PROTECTION
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

    # MICROSOFT TEAMS PROTECTION
    "teams", "teams.exe",
    "teamshelper", "teamshelper.exe",
    "teamswebview", "teamswebview.exe",
    "teamsrenderer", "teamsrenderer.exe",
    "teamsutility", "teamsutility.exe",
    "teamswebclient", "teamswebclient.exe",

    # CORE MICROSOFT / WINDOWS APPS PROTECTION
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

GUI_INSTANCE = None  # set when GUI is created

def kill_ads_only():
    print("[ADBLOCK] Killing ad processes only (safe)")
    try:
        current_pid = os.getpid()

        for proc in psutil.process_iter(attrs=["pid", "name"]):
            try:
                info = proc.info
                pid = info.get("pid")
                name_raw = info.get("name") or ""
                name = name_raw.lower()

                if GUI_INSTANCE is not None:
                    GUI_INSTANCE.add_process_row(name_raw, pid)

                if pid == current_pid:
                    continue

                if name in WHITELIST:
                    continue

                # SAFE NAMES: NEVER TOUCH
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
                    cmdline_list = proc.cmdline()
                except:
                    cmdline_list = []

                cmd = " ".join(cmdline_list).lower()

                if "python" in cmd or cmd.endswith(".py"):
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
                    "cmd": cmdline_list
                })

                try:
                    proc.terminate()
                except:
                    pass

                print(f"[ADBLOCK] Terminated ad process: {name_raw} ({pid})")

            except Exception as e:
                print(f"[ADBLOCK] per-proc error: {e}")
                continue

    except Exception as e:
        print(f"[ADBLOCK] kill_ads_only() top-level error: {e}")
        LOGGER.log("ad_kill_error", {"error": str(e)})

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

    def kill_heavy_background(self):
        print("[GFX] Killing heavy background processes (aggressive)")
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent"]):
            name = (proc.info["name"] or "").lower()
            cpu = proc.info["cpu_percent"] or 0.0
            if cpu > 20.0 and any(k in name for k in ["chrome", "edge", "discord", "obs"]):
                try:
                    LOGGER.log("gfx_kill", {"pid": proc.info["pid"], "name": name, "cpu": cpu})
                    proc.terminate()
                    print(f"[GFX] Terminated {name} ({proc.info['pid']}) cpu={cpu}")
                except Exception:
                    pass

    def emergency_downclock_stub(self):
        print("[GFX] Emergency downclock (stub)")
        LOGGER.log("gfx_emergency_downclock", {})
        NVAPI.set_power_limit(150.0)
        NVAPI.set_temp_target(CONFIG["watchdog"]["max_temp_c"])

    def kill_ad_processes_only(self):
        kill_ads_only()

GFX = GraphicsTuner(CONFIG["graphics"])

# ============================================================
# ENV / RL
# ============================================================

@dataclass
class BorgState:
    snapshot: SensorSnapshot
    mode_name: str

class BorgEnv:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mode_name = "flow"
        self.mode_cfg = self.config["modes"][self.mode_name]

    def set_mode(self, mode_name: str):
        if mode_name in self.config["modes"]:
            self.mode_name = mode_name
            self.mode_cfg = self.config["modes"][mode_name]
            print(f"[ENV] Switched mode to {mode_name}")
            LOGGER.log("mode_switch", {"mode": mode_name})
        else:
            print(f"[WARN] Unknown mode: {mode_name}")

    def get_state_vector(self, snap: SensorSnapshot) -> np.ndarray:
        temp_norm = snap.gpu_temp / 100.0
        fps_norm = snap.fps / max(self.mode_cfg["target_fps"], 1)
        ft_norm = min(snap.frametime_var * 100.0, 1.0)
        power_norm = snap.gpu_power / 300.0
        return np.array(
            [
                snap.cpu_usage,
                snap.ram_usage,
                snap.gpu_usage,
                temp_norm,
                fps_norm,
                ft_norm,
                snap.vram_usage,
                power_norm,
            ],
            dtype=np.float32,
        )

    def reward(self, snap: SensorSnapshot) -> float:
        w = self.mode_cfg["reward_weights"]
        target_fps = self.mode_cfg["target_fps"]
        fps_stability = -abs(snap.fps - target_fps) / max(target_fps, 1)
        cpu_headroom = 1.0 - snap.cpu_usage
        gpu_headroom = 1.0 - snap.gpu_usage
        thermal_penalty = max(0.0, (snap.gpu_temp - CONFIG["watchdog"]["max_temp_c"]) / 20.0)
        ft_penalty = min(snap.frametime_var * 100.0, 1.0)
        r = (
            w["fps_stability"] * fps_stability
            + w["cpu_headroom"] * cpu_headroom
            + w["gpu_headroom"] * gpu_headroom
            - w["thermal_safety"] * thermal_penalty
            - w["frametime_stability"] * ft_penalty
        )
        return float(r)

    def apply_action(self, action: int):
        LOGGER.log("action", {"mode": self.mode_name, "action": action})
        if action == 0:
            return
        elif action == 1:
            GFX.lower_global_quality()
            GFX.lower_game_quality()
        elif action == 2:
            GFX.raise_global_quality()
            GFX.raise_game_quality()
        elif action == 3:
            GFX.throttle_background_processes()
        elif action == 4:
            GFX.kill_heavy_background()
        elif action == 5:
            self.set_mode("flow")
        elif action == 6:
            self.set_mode("deep_work")
        elif action == 7:
            self.set_mode("recovery")
        elif action == 8:
            GFX.kill_ad_processes_only()
        else:
            print(f"[ENV] Unknown action {action}")

    def step(self, action: int) -> Tuple[np.ndarray, float]:
        self.apply_action(action)
        snap = SENSORS.snapshot()
        r = self.reward(snap)
        s_vec = self.get_state_vector(snap)
        LOGGER.log("step", {
            "mode": self.mode_name,
            "action": action,
            "reward": r,
            "cpu": snap.cpu_usage,
            "gpu": snap.gpu_usage,
            "temp": snap.gpu_temp,
            "fps": snap.fps,
            "ft_var": snap.frametime_var,
            "vram": snap.vram_usage,
            "power": snap.gpu_power,
        })
        return s_vec, r

    def reset(self) -> np.ndarray:
        snap = SENSORS.snapshot()
        return self.get_state_vector(snap)

ENV = BorgEnv(CONFIG)

STATE_DIM = 8
ACTION_DIM = 9

class HybridAgent(torch.nn.Module):
    def __init__(self, state_dim: int, action_dim: int, cfg: Dict[str, Any]):
        super().__init__()
        self.cfg = cfg
        hidden = 128
        self.body = torch.nn.Sequential(
            torch.nn.Linear(state_dim, hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(hidden, hidden),
            torch.nn.ReLU(),
        )
        self.q_head = torch.nn.Linear(hidden, action_dim)
        self.pi_head = torch.nn.Linear(hidden, action_dim)
        self.v_head = torch.nn.Linear(hidden, 1)
        self.oracle_head = torch.nn.Linear(hidden, 1)
        self.dqn_opt = torch.optim.Adam(
            list(self.body.parameters()) + list(self.q_head.parameters()),
            lr=self.cfg["dqn_lr"],
        )
        self.ppo_opt = torch.optim.Adam(
            list(self.body.parameters())
            + list(self.pi_head.parameters())
            + list(self.v_head.parameters())
            + list(self.oracle_head.parameters()),
            lr=self.cfg["ppo_lr"],
        )

    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        h = self.body(x)
        q = self.q_head(h)
        logits = self.pi_head(h)
        v = self.v_head(h).squeeze(-1)
        oracle = self.oracle_head(h).squeeze(-1)
        return {"q": q, "logits": logits, "v": v, "oracle": oracle}

    def act(self, state: np.ndarray, epsilon: float = 0.1) -> int:
        s = torch.tensor(state, dtype=torch.float32).unsqueeze(0)
        out = self.forward(s)
        q = out["q"].detach().cpu().numpy()[0]
        if random.random() < epsilon:
            a = random.randint(0, q.shape[0] - 1)
        else:
            a = int(np.argmax(q))
        LOGGER.log("policy_act", {"epsilon": epsilon, "action": a})
        return a

    def ppo_update(self, traj: Dict[str, List[float]]):
        pass

ANOM_CFG = CONFIG["anomaly"]

class AnomalyAutoencoder(torch.nn.Module):
    def __init__(self, input_dim: int, latent_dim: int):
        super().__init__()
        self.encoder = torch.nn.Sequential(
            torch.nn.Linear(input_dim, 16),
            torch.nn.ReLU(),
            torch.nn.Linear(16, latent_dim),
        )
        self.decoder = torch.nn.Sequential(
            torch.nn.Linear(latent_dim, 16),
            torch.nn.ReLU(),
            torch.nn.Linear(16, input_dim),
        )
        self.opt = torch.optim.Adam(self.parameters(), lr=1e-3)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.encoder(x)
        recon = self.decoder(z)
        return recon

    def train_batch(self, batch: np.ndarray) -> float:
        x = torch.tensor(batch, dtype=torch.float32)
        recon = self.forward(x)
        loss = torch.nn.functional.mse_loss(recon, x)
        self.opt.zero_grad()
        loss.backward()
        self.opt.step()
        LOGGER.log("anomaly_train_loss", {"loss": float(loss.item())})
        return float(loss.item())

    def anomaly_score(self, x: np.ndarray) -> float:
        with torch.no_grad():
            t = torch.tensor(x, dtype=torch.float32).unsqueeze(0)
            recon = self.forward(t)
            loss = torch.nn.functional.mse_loss(recon, t)
        score = float(loss.item())
        LOGGER.log("anomaly_score", {"score": score})
        return score

AUTOENC = AnomalyAutoencoder(input_dim=STATE_DIM, latent_dim=ANOM_CFG["latent_dim"])
ANOM_BUFFER: List[np.ndarray] = []

@dataclass
class TeacherBrain:
    agent: HybridAgent
    name: str = "Teacher"
    crash_count: int = 0
    active: bool = True
    dream_mode: bool = False
    mood: str = "flow"

@dataclass
class ShadowBrain:
    agent: HybridAgent
    name: str = "Shadow"
    active: bool = False
    anomaly_active: bool = False

@dataclass
class OracleBrain:
    agent: HybridAgent
    name: str = "Oracle"
    active: bool = True

TEACHER = TeacherBrain(agent=HybridAgent(STATE_DIM, ACTION_DIM, CONFIG["rl"]))
SHADOW = ShadowBrain(agent=HybridAgent(STATE_DIM, ACTION_DIM, CONFIG["rl"]))
ORACLE = OracleBrain(agent=HybridAgent(STATE_DIM, ACTION_DIM, CONFIG["rl"]))

# ============================================================
# WATCHDOG
# ============================================================

class Watchdog:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self):
        print("[WATCHDOG] Starting")
        self.thread.start()

    def loop(self):
        try:
            while True:
                if not SERVICE_RUNNING:
                    time.sleep(0.5)
                    continue
                try:
                    snap = SENSORS.snapshot()
                    s_vec = ENV.get_state_vector(snap)

                    if snap.gpu_temp > self.cfg["emergency_temp_c"]:
                        print(f"[WATCHDOG] EMERGENCY temp {snap.gpu_temp:.1f}C — forcing recovery, downclock, kill background.")
                        ENV.set_mode("recovery")
                        GFX.emergency_downclock_stub()
                        GFX.kill_heavy_background()

                    elif snap.gpu_temp > self.cfg["max_temp_c"]:
                        print(f"[WATCHDOG] High temp {snap.gpu_temp:.1f}C — lowering graphics, throttling background.")
                        GFX.lower_global_quality()
                        GFX.lower_game_quality()
                        GFX.throttle_background_processes()

                    if TEACHER.crash_count >= self.cfg["max_crash_count"]:
                        print("[WATCHDOG] Teacher crash limit reached — activating Shadow.")
                        TEACHER.active = False
                        SHADOW.active = True

                    score = AUTOENC.anomaly_score(s_vec)
                    if score > ANOM_CFG["threshold"]:
                        print(f"[WATCHDOG] Anomaly {score:.4f} > {ANOM_CFG['threshold']:.4f} — Shadow anomaly mode, kill ads only.")
                        SHADOW.anomaly_active = True
                        TEACHER.active = False
                        SHADOW.active = True
                        GFX.kill_ad_processes_only()

                    LOGGER.log("watchdog", {
                        "temp": snap.gpu_temp,
                        "anomaly_score": score,
                        "teacher_active": TEACHER.active,
                        "shadow_active": SHADOW.active,
                    })
                except Exception as e:
                    print(f"[WATCHDOG] Error: {e}")
                    LOGGER.log("watchdog_error", {"error": str(e)})
                time.sleep(self.cfg["check_interval_sec"])
        except Exception as e:
            hard_log(e, "WATCHDOG_TOP")

WATCHDOG = Watchdog(CONFIG["watchdog"])

# ============================================================
# SINGLE PANEL GUI — PROCESS CONTROL WITH TEXT TOGGLES (CENTERED)
# ============================================================

TOGGLE_ON = "[X]"
TOGGLE_OFF = "[ ]"

class ProcessControlGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Borg V3.7 — Process Control")

        self.tree = ttk.Treeview(
            root,
            columns=("name", "white", "block", "kill"),
            show="headings",
            height=20
        )

        self.tree.heading("name", text="Name")
        self.tree.heading("white", text="White")
        self.tree.heading("block", text="Block")
        self.tree.heading("kill", text="Kill")

        self.tree.column("name", width=260)
        self.tree.column("white", width=70, anchor="center")
        self.tree.column("block", width=70, anchor="center")
        self.tree.column("kill", width=70, anchor="center")

        self.tree.pack(fill="both", expand=True)

        # name -> dict flags + display name
        self.proc_flags = {}  # name -> {"white": bool, "block": bool, "kill": bool, "display": str}

        self.refresh_button = ttk.Button(root, text="Refresh Process List", command=self.refresh_processes)
        self.refresh_button.pack(pady=5)

        self.status_label = ttk.Label(root, text="Service: RUNNING (always on)")
        self.status_label.pack(pady=2)

        # Bind click on cells
        self.tree.bind("<Button-1>", self.on_tree_click)

        self.root.after(1000, self.periodic_refresh)

    def add_process_row(self, name_raw, pid=None):
        try:
            name_raw = name_raw or ""
            name = name_raw.lower()
            key = name  # ALWAYS collapse by NAME

            if key in self.proc_flags:
                return

            flags = {
                "white": (name in WHITELIST),
                "block": (name in BLOCKLIST),
                "kill": (name in KILLLIST),
                "display": name_raw,
            }
            self.proc_flags[key] = flags

            self.tree.insert(
                "",
                "end",
                iid=key,
                values=(
                    name_raw,
                    TOGGLE_ON if flags["white"] else TOGGLE_OFF,
                    TOGGLE_ON if flags["block"] else TOGGLE_OFF,
                    TOGGLE_ON if flags["kill"] else TOGGLE_OFF,
                )
            )
        except Exception as e:
            print(f"[GUI] add_process_row error: {e}")

    def refresh_processes(self):
        try:
            for proc in psutil.process_iter(attrs=["pid", "name"]):
                info = proc.info
                name_raw = info.get("name") or ""
                self.add_process_row(name_raw, info.get("pid"))
        except Exception as e:
            print(f"[GUI] refresh_processes error: {e}")

    def periodic_refresh(self):
        self.refresh_processes()
        self.root.after(5000, self.periodic_refresh)

    def on_tree_click(self, event):
        try:
            region = self.tree.identify("region", event.x, event.y)
            if region != "cell":
                return

            row_id = self.tree.identify_row(event.y)
            col_id = self.tree.identify_column(event.x)

            if not row_id or col_id not in ("#2", "#3", "#4"):
                return

            name = row_id.lower()
            if name not in self.proc_flags:
                return

            col_map = {"#2": "white", "#3": "block", "#4": "kill"}
            flag_name = col_map[col_id]

            flags = self.proc_flags[name]
            flags[flag_name] = not flags[flag_name]

            # Update JSON lists
            self.update_process_lists(name, flags)

            # ⭐ FIX: rewrite entire row to clear old text (no double boxes)
            display_name = flags["display"]
            self.tree.item(row_id, values=(
                display_name,
                TOGGLE_ON if flags["white"] else TOGGLE_OFF,
                TOGGLE_ON if flags["block"] else TOGGLE_OFF,
                TOGGLE_ON if flags["kill"] else TOGGLE_OFF
            ))

        except Exception as e:
            print(f"[GUI] on_tree_click error: {e}")

    def update_process_lists(self, name, flags):
        try:
            name = name.lower()

            # WHITE
            if flags["white"]:
                if name not in WHITELIST:
                    WHITELIST.append(name)
            else:
                if name in WHITELIST:
                    WHITELIST.remove(name)
            save_json_list(WHITELIST_PATH, WHITELIST)

            # BLOCK
            if flags["block"]:
                if name not in BLOCKLIST:
                    BLOCKLIST.append(name)
            else:
                if name in BLOCKLIST:
                    BLOCKLIST.remove(name)
            save_json_list(BLOCKLIST_PATH, BLOCKLIST)

            # KILL
            if flags["kill"]:
                if name not in KILLLIST:
                    KILLLIST.append(name)
            else:
                if name in KILLLIST:
                    KILLLIST.remove(name)
            save_json_list(KILLLIST_PATH, KILLLIST)

            print(f"[GUI] Updated lists for {name} (white={flags['white']}, block={flags['block']}, kill={flags['kill']})")
        except Exception as e:
            print(f"[GUI] update_process_lists error: {e}")

def launch_gui():
    global GUI_INSTANCE
    if tk is None:
        print("[GUI] Tkinter not available — GUI disabled.")
        return
    root = tk.Tk()
    gui = ProcessControlGUI(root)
    GUI_INSTANCE = gui
    root.mainloop()

# ============================================================
# DREAM TEACHER
# ============================================================

def run_dream_teacher():
    if not CONFIG["dream"]["enabled"]:
        return
    print("[DREAM] Dream Teacher starting synthetic episodes")
    episodes = CONFIG["dream"]["episodes"]
    length = CONFIG["dream"]["length"]
    for ep in range(episodes):
        s = ENV.reset()
        traj = {"s": [], "a": [], "r": [], "logp": [], "v": []}
        for t in range(length):
            a = TEACHER.agent.act(s, epsilon=0.4)
            s_torch = torch.tensor(s, dtype=torch.float32)
            noise = torch.randn_like(s_torch) * 0.03
            s2_torch = s_torch + noise
            s2 = s2_torch.detach().cpu().numpy()
            r = random.uniform(-0.2, 0.2)
            traj["s"].append(s)
            traj["a"].append(a)
            traj["r"].append(r)
            traj["logp"].append(0.0)
            traj["v"].append(0.0)
            s = s2
        TEACHER.agent.ppo_update(traj)
        LOGGER.log("dream_episode", {"ep": ep})

# ============================================================
# SERVICE CONTROL + RL LOOP (ALWAYS ON)
# ============================================================

SERVICE_RUNNING = False
SERVICE_THREADS = []

def rl_loop():
    try:
        while True:
            if not SERVICE_RUNNING:
                time.sleep(0.5)
                continue
            try:
                s = ENV.reset()
                epsilon = CONFIG["rl"]["epsilon_start"]
                eps_end = CONFIG["rl"]["epsilon_end"]
                eps_decay = CONFIG["rl"]["epsilon_decay_steps"]
                step_count = 0

                while True:
                    if not SERVICE_RUNNING:
                        time.sleep(0.5)
                        break
                    try:
                        a = TEACHER.agent.act(s, epsilon=epsilon)
                        s2, r = ENV.step(a)

                        ANOM_BUFFER.append(s2)
                        if len(ANOM_BUFFER) > ANOM_CFG["train_buffer_size"]:
                            ANOM_BUFFER.pop(0)

                        if step_count % ANOM_CFG["train_interval_steps"] == 0 and len(ANOM_BUFFER) > 32:
                            batch = np.stack(random.sample(ANOM_BUFFER, 32), axis=0)
                            AUTOENC.train_batch(batch)

                        s = s2
                        step_count += 1
                        epsilon = max(eps_end, epsilon - (CONFIG["rl"]["epsilon_start"] - eps_end) / eps_decay)

                        if CONFIG["dream"]["enabled"] and step_count % CONFIG["dream"]["interval_steps"] == 0:
                            run_dream_teacher()

                        time.sleep(0.1)

                    except Exception as e:
                        print(f"[RL LOOP] Inner error: {e}")
                        LOGGER.log("rl_loop_inner_error", {"error": str(e)})
                        time.sleep(0.5)

            except Exception as e:
                print(f"[RL LOOP] Outer error: {e}")
                LOGGER.log("rl_loop_outer_error", {"error": str(e)})
                time.sleep(1)
    except Exception as e:
        hard_log(e, "RL_TOP")

def start_borg_service():
    global SERVICE_RUNNING, SERVICE_THREADS
    if SERVICE_RUNNING:
        return

    SERVICE_RUNNING = True
    SERVICE_THREADS = []

    try:
        t1 = threading.Thread(target=rl_loop, daemon=True)
        SERVICE_THREADS.append(t1)
        t1.start()
    except Exception as e:
        hard_log(e, "RL_START")

    try:
        t2 = threading.Thread(target=WATCHDOG.loop, daemon=True)
        SERVICE_THREADS.append(t2)
        t2.start()
    except Exception as e:
        hard_log(e, "WATCHDOG_START")

    try:
        t3 = threading.Thread(
            target=lambda: start_ad_proxy_stub(CONFIG["adblock"]["proxy_port"]),
            daemon=True
        )
        SERVICE_THREADS.append(t3)
        t3.start()
    except Exception as e:
        hard_log(e, "PROXY_START")

    print("[SERVICE] Borg background service started (always on)")

def stop_borg_service():
    global SERVICE_RUNNING
    SERVICE_RUNNING = False
    print("[SERVICE] Borg background service stopped (manual call only)")

# ============================================================
# MAIN
# ============================================================

def main_loop():
    print("[BORG] Starting main loop (single panel GUI, Borg service ALWAYS ON)")
    UPDATER.start()
    start_borg_service()
    launch_gui()

if __name__ == "__main__":
    try:
        main_loop()
    except Exception as e:
        hard_log(e, "MAIN")
        input("Press Enter to exit...")
