import os
import sys
import json
import ctypes
import socket
import logging
import subprocess
import threading
import time
import queue
import random
import tempfile
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import messagebox, scrolledtext
import winreg
from collections import deque, defaultdict

# ============================================================
# ADMIN ELEVATION
# ============================================================

def ensure_admin():
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            script = os.path.abspath(sys.argv[0])
            ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                sys.executable,
                f'"{script}"',
                None,
                1
            )
            sys.exit()
    except Exception:
        pass

ensure_admin()

# ============================================================
# PATHS / FILES
# ============================================================

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "proxy_config.json")
BACKUP_PATH = os.path.join(BASE, "backup.json")
PAC_PATH = os.path.join(BASE, "proxy.pac")
LOG_PATH = os.path.join(BASE, "proxy_controller.log")
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "ProxyControllerPro"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

config_lock = threading.Lock()
ui_queue = queue.Queue()
event_bus = queue.Queue()

# ============================================================
# HEARTBEAT / WATCHDOG CORE
# ============================================================

heartbeat = {
    "auto_rotate": 0,
    "borg_ai": 0,
    "registry_monitor": 0,
    "logs": 0,
    "cluster_sync": 0,
    "pac_self_heal": 0,
    "per_app": 0,
    "event_bus": 0,
    "health_monitor": 0,
    "policy_engine": 0,
    "predictive_latency": 0,
    "fusion_engine": 0,
    "ai_governor": 0,
    "daemon_watchdog": 0,
    "emergent_engine": 0,
    "self_tune": 0,
    "rl_agent": 0,
    "proxy_graph": 0,
    "anomaly_cluster": 0,
    "raft_consensus": 0,
    "ml_routing": 0,
}

def touch_heartbeat(name: str):
    heartbeat[name] = time.time()

# ============================================================
# DEFAULT CONFIG (SCHEMA v6 / HybridBrain Fusion)
# ============================================================

SCHEMA_VERSION = 6

DEFAULT_CONFIG = {
    "schema_version": SCHEMA_VERSION,
    "proxy": "127.0.0.1:8888",
    "proxies": [
        "127.0.0.1:8888",
        "127.0.0.1:8889"
    ],
    "proxy_index": 0,
    "auto_rotate": False,
    "rotation_interval": 300,
    "failover_enabled": True,
    "borg_ai": False,
    "borg_interval": 60,
    "latency_threshold_ms": 800,
    "proxy_stats": {},
    "startup": True,
    "stealth_mode": False,
    "pac_key": "borgkey",
    "cluster_sync": False,
    "cluster_url": "",
    "cluster_interval": 600,
    "node_id": socket.gethostname(),
    "cluster_role": "follower",
    "profiles_routing": {
        "work": {
            "preferred_proxies": ["127.0.0.1:8888"],
            "latency_threshold_ms": 600
        },
        "gaming": {
            "preferred_proxies": ["127.0.0.1:8889"],
            "latency_threshold_ms": 300
        },
        "stealth": {
            "preferred_proxies": ["127.0.0.1:8888"],
            "latency_threshold_ms": 1000
        }
    },
    "current_profile": "work",
    "health_window": 10,
    "health_history": [],
    "policy_rules": [],
    "app_routes": {},
    "bypass": [
        "*.teams.microsoft.com",
        "*.teams.live.com",
        "*.cloud.microsoft",
        "*.skype.com",
        "*.lync.com",
        "*.microsoft.com",
        "*.microsoftonline.com",
        "*.office.com",
        "*.office365.com",
        "*.onedrive.com",
        "*.azure.com",
        "*.azureedge.net",
        "localhost"
    ],
    # Predictive / AI governor config
    "predictive_enabled": True,
    "predictive_window": 20,
    "predictive_sensitivity": 0.7,
    "fusion_enabled": True,
    "ai_governor_enabled": True,
    # Emergent / self-tuning
    "self_tune_enabled": True,
    "emergent_enabled": True,
    # RL agent
    "rl_enabled": True,
    "rl_exploration": 0.2,
    # Proxy graph
    "graph_enabled": True,
    # Anomaly clustering
    "cluster_anomaly_enabled": True,
    # ML routing
    "ml_routing_enabled": True,
}

# ============================================================
# CONFIG LOAD / SAVE / VALIDATION
# ============================================================

def validate_config(data):
    changed = False
    for k, v in DEFAULT_CONFIG.items():
        if k not in data:
            data[k] = v
            changed = True

    if data.get("schema_version") != SCHEMA_VERSION:
        data["schema_version"] = SCHEMA_VERSION
        changed = True

    if not isinstance(data.get("proxies"), list):
        data["proxies"] = DEFAULT_CONFIG["proxies"]
        changed = True

    if not isinstance(data.get("proxy_stats"), dict):
        data["proxy_stats"] = {}
        changed = True

    if not isinstance(data.get("bypass"), list):
        data["bypass"] = DEFAULT_CONFIG["bypass"]
        changed = True

    if not isinstance(data.get("startup"), bool):
        data["startup"] = DEFAULT_CONFIG["startup"]
        changed = True

    if not isinstance(data.get("app_routes"), dict):
        data["app_routes"] = {}
        changed = True

    if not isinstance(data.get("profiles_routing"), dict):
        data["profiles_routing"] = DEFAULT_CONFIG["profiles_routing"]
        changed = True

    if "current_profile" not in data:
        data["current_profile"] = "work"
        changed = True

    if not isinstance(data.get("health_history"), list):
        data["health_history"] = []
        changed = True

    if not isinstance(data.get("policy_rules"), list):
        data["policy_rules"] = []
        changed = True

    if "node_id" not in data:
        data["node_id"] = socket.gethostname()
        changed = True

    if "cluster_role" not in data:
        data["cluster_role"] = "follower"
        changed = True

    for k in [
        "predictive_enabled", "predictive_window", "predictive_sensitivity",
        "fusion_enabled", "ai_governor_enabled",
        "self_tune_enabled", "emergent_enabled",
        "rl_enabled", "rl_exploration",
        "graph_enabled", "cluster_anomaly_enabled",
        "ml_routing_enabled"
    ]:
        if k not in data:
            data[k] = DEFAULT_CONFIG[k]
            changed = True

    return data, changed

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            data, changed = validate_config(data)
            if changed:
                save_config(data)
            return data
        except Exception as e:
            logging.error(f"Config load failed, using default: {e}")
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()

def save_config(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"Config save failed: {e}")

with config_lock:
    config = load_config()

# ============================================================
# SAFE REGISTRY ACCESS
# ============================================================

def safe_open_reg_key(root, path, access=winreg.KEY_READ):
    try:
        return winreg.OpenKey(root, path, 0, access)
    except Exception as e:
        logging.error(f"[REG] Failed to open key {path}: {e}")
        return None

def safe_get_autoconfig_url():
    key = safe_open_reg_key(winreg.HKEY_CURRENT_USER, REG_PATH)
    if not key:
        return "None"
    try:
        value, _ = winreg.QueryValueEx(key, "AutoConfigURL")
        return value
    except FileNotFoundError:
        return "None"
    except Exception as e:
        logging.error(f"[REG] Query AutoConfigURL failed: {e}")
        return "None"
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass

def safe_set_autoconfig_url(url):
    key = safe_open_reg_key(
        winreg.HKEY_CURRENT_USER,
        REG_PATH,
        access=winreg.KEY_SET_VALUE
    )
    if not key:
        return
    try:
        if url:
            winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, url)
        else:
            try:
                winreg.DeleteValue(key, "AutoConfigURL")
            except FileNotFoundError:
                pass
    except Exception as e:
        logging.error(f"[REG] Set AutoConfigURL failed: {e}")
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass

# ============================================================
# STARTUP REGISTRATION
# ============================================================

def set_startup(enabled: bool):
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE
        )
        if enabled:
            exe = sys.executable
            script = os.path.abspath(sys.argv[0])
            cmd = f'"{exe}" "{script}"'
            winreg.SetValueEx(
                key,
                RUN_VALUE_NAME,
                0,
                winreg.REG_SZ,
                cmd
            )
            logging.info("Startup enabled")
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
                logging.info("Startup disabled")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        logging.error(f"Failed to set startup: {e}")

def sync_startup_from_config():
    with config_lock:
        enabled = bool(config.get("startup", False))
    set_startup(enabled)

# ============================================================
# ENCRYPTED PAC HELPERS
# ============================================================

def _xor_bytes(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def encrypt_pac(content: str) -> bytes:
    with config_lock:
        key = config.get("pac_key", "borgkey").encode("utf-8")
    return _xor_bytes(content.encode("utf-8"), key)

def decrypt_pac(data: bytes) -> str:
    with config_lock:
        key = config.get("pac_key", "borgkey").encode("utf-8")
    return _xor_bytes(data, key).decode("utf-8", errors="ignore")

# ============================================================
# PAC PATH (STEALTH MODE)
# ============================================================

def get_pac_path():
    with config_lock:
        stealth = config.get("stealth_mode", False)
    if not stealth:
        return PAC_PATH
    temp_dir = tempfile.gettempdir()
    name = f"sys_{random.randint(100000, 999999)}.dat"
    return os.path.join(temp_dir, name)

# ============================================================
# ADAPTIVE PAC BUILDER
# ============================================================

def adapt_bypass_lists():
    with config_lock:
        stats = config.get("proxy_stats", {})
        current = config.get("proxy", DEFAULT_CONFIG["proxy"])
        bypass = config.get("bypass", [])
    ps = stats.get(current, {})
    lat = ps.get("latency_avg", None)
    failures = ps.get("failures", 0)

    new_entries = []
    if lat and lat > 800:
        new_entries.extend(["*.speedtest.net", "*.fast.com"])
    if failures >= 3:
        new_entries.extend(["*.windowsupdate.com", "*.update.microsoft.com"])

    changed = False
    with config_lock:
        for e in new_entries:
            if e not in bypass:
                bypass.append(e)
                changed = True
        if changed:
            config["bypass"] = bypass
            save_config(config)
            logging.info(f"[ADAPT_BYPASS] Updated bypass list: {new_entries}")

def build_pac():
    adapt_bypass_lists()
    with config_lock:
        proxy = config.get("proxy", "127.0.0.1:8888")
        bypass_list = config.get("bypass", [])[:]
        stealth = config.get("stealth_mode", False)

    rules = ""
    for x in bypass_list:
        x_safe = x.replace('"', r'\"')
        rules += f'shExpMatch(host,"{x_safe}") ||\n'

    pac_content = f"""
function FindProxyForURL(url,host)
{{
    if(
        {rules}
        shExpMatch(host,"localhost")
    )
    {{
        return "DIRECT";
    }}

    return "PROXY {proxy}";
}}
"""
    pac_path = get_pac_path()
    try:
        with open(pac_path, "w", encoding="utf-8") as f:
            f.write(pac_content)
        enc = encrypt_pac(pac_content)
        with open(pac_path + ".enc", "wb") as f:
            f.write(enc)
        safe_set_autoconfig_url("file:///" + pac_path.replace("\\", "/"))
        logging.info(f"[PAC] written ({'stealth' if stealth else 'normal'}): {pac_path}")
    except Exception as e:
        logging.error(f"[PAC] Failed to write PAC: {e}")

    return pac_path

# ============================================================
# BACKUP / RESTORE REGISTRY SETTINGS
# ============================================================

def backup_settings():
    data = {}
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_PATH
        )
        for x in ["ProxyEnable", "ProxyServer", "AutoConfigURL"]:
            try:
                val, typ = winreg.QueryValueEx(key, x)
                data[x] = {"value": val, "type": typ}
            except Exception:
                pass
        winreg.CloseKey(key)

        with open(BACKUP_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        logging.info("[BACKUP] Backup created")
    except Exception as e:
        logging.error(f"[BACKUP] Backup failed: {e}")

def _apply_restore():
    if not os.path.exists(BACKUP_PATH):
        ui_queue.put(("messagebox_error", ("Restore", "No backup found")))
        return
    try:
        with open(BACKUP_PATH, encoding="utf-8") as f:
            data = json.load(f)

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_PATH,
            0,
            winreg.KEY_SET_VALUE
        )

        for k, obj in data.items():
            v = obj["value"]
            t = obj["type"]
            winreg.SetValueEx(key, k, 0, t, v)

        winreg.CloseKey(key)
        logging.info("[RESTORE] Settings restored")
    except Exception as e:
        logging.error(f"[RESTORE] Restore failed: {e}")

def restore_threaded():
    def worker():
        try:
            _apply_restore()
        except Exception as e:
            logging.error(f"[RESTORE] worker failed: {e}")
        publish_event("proxy_changed")
    threading.Thread(target=worker, daemon=True).start()

# ============================================================
# ENABLE / DISABLE PROXY
# ============================================================

def _apply_enable_proxy():
    backup_settings()
    pac_path = build_pac()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_PATH,
            0,
            winreg.KEY_SET_VALUE
        )

        winreg.SetValueEx(
            key,
            "AutoConfigURL",
            0,
            winreg.REG_SZ,
            "file:///" + pac_path.replace("\\", "/")
        )

        winreg.SetValueEx(
            key,
            "ProxyEnable",
            0,
            winreg.REG_DWORD,
            0
        )

        winreg.CloseKey(key)

        subprocess.run("netsh winhttp reset proxy", shell=True)
        logging.info("[PROXY] Enabled via PAC")
    except Exception as e:
        logging.error(f"[PROXY] Enable proxy failed: {e}")

def enable_proxy_threaded():
    def worker():
        try:
            touch_heartbeat("enable_proxy")
            _apply_enable_proxy()
        except Exception as e:
            logging.error(f"[PROXY] Enable proxy worker failed: {e}")
        publish_event("proxy_changed")
        ui_queue.put(("refresh_status", None))
    threading.Thread(target=worker, daemon=True).start()

def _apply_disable_proxy():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_PATH,
            0,
            winreg.KEY_SET_VALUE
        )

        winreg.SetValueEx(
            key,
            "AutoConfigURL",
            0,
            winreg.REG_SZ,
            ""
        )

        winreg.SetValueEx(
            key,
            "ProxyEnable",
            0,
            winreg.REG_DWORD,
            0
        )

        winreg.CloseKey(key)
        logging.info("[PROXY] Disabled PAC")
    except Exception as e:
        logging.error(f"[PROXY] Disable proxy failed: {e}")

def disable_proxy_threaded():
    def worker():
        try:
            touch_heartbeat("disable_proxy")
            _apply_disable_proxy()
        except Exception as e:
            logging.error(f"[PROXY] Disable proxy worker failed: {e}")
        publish_event("proxy_changed")
        ui_queue.put(("refresh_status", None))
    threading.Thread(target=worker, daemon=True).start()

# ============================================================
# TEST CONNECTION + LATENCY
# ============================================================

def measure_latency(host="google.com", port=80, timeout=5):
    start = time.time()
    try:
        s = socket.create_connection((host, port), timeout)
        s.close()
        return (time.time() - start) * 1000.0, True
    except Exception:
        return None, False

def _test_connection():
    latency_ms, ok = measure_latency()
    if ok:
        ui_queue.put(("messagebox_info", ("Network", f"Internet OK ({latency_ms:.0f} ms)")))
    else:
        ui_queue.put(("messagebox_error", ("Network", "Connection failed")))

def test_threaded():
    threading.Thread(target=_test_connection, daemon=True).start()

# ============================================================
# PROXY ROTATION + FAILOVER
# ============================================================

def rotate_proxy_once():
    with config_lock:
        proxies = config.get("proxies", [])
        if not proxies:
            return
        idx = config.get("proxy_index", 0)
        idx = (idx + 1) % len(proxies)
        config["proxy_index"] = idx
        config["proxy"] = proxies[idx]
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
        logging.info(f"[ROTATE] Proxy rotated to {config['proxy']}")
    build_pac()
    publish_event("proxy_changed")
    ui_queue.put(("refresh_status", None))

def rotate_proxy_button():
    threading.Thread(target=rotate_proxy_once, daemon=True).start()

def select_best_proxy():
    with config_lock:
        proxies = config.get("proxies", [])
        stats = config.get("proxy_stats", {})
        if not proxies:
            return None
        best = None
        best_score = float("inf")
        for p in proxies:
            s = stats.get(p, {})
            lat = s.get("latency_avg", None)
            if lat is None:
                lat = 1000.0
            if lat < best_score:
                best_score = lat
                best = p
        return best or proxies[0]

def apply_best_proxy():
    best = select_best_proxy()
    if best is None:
        return
    with config_lock:
        config["proxy"] = best
        try:
            config["proxy_index"] = config.get("proxies", []).index(best)
        except ValueError:
            config["proxy_index"] = 0
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
        logging.info(f"[BORG] Selected best proxy: {best}")
    build_pac()
    publish_event("proxy_changed")
    ui_queue.put(("refresh_status", None))

def auto_rotate_thread():
    while True:
        try:
            touch_heartbeat("auto_rotate")
            with config_lock:
                auto = config.get("auto_rotate", False)
                interval = config.get("rotation_interval", 300)
            if auto:
                rotate_proxy_once()
            time.sleep(max(60, interval))
        except Exception as e:
            logging.error(f"[AUTO_ROTATE] thread error: {e}")
            time.sleep(20)

# ============================================================
# PROFILE-BASED ROUTING / MORPHING
# ============================================================

def apply_profile(profile_name):
    with config_lock:
        profiles = config.get("profiles_routing", {})
        if profile_name not in profiles:
            logging.warning(f"[PROFILE] {profile_name} not found")
            return
        config["current_profile"] = profile_name
        profile = profiles[profile_name]
        preferred = profile.get("preferred_proxies", [])
        if preferred:
            for p in preferred:
                if p in config.get("proxies", []):
                    config["proxy"] = p
                    try:
                        config["proxy_index"] = config["proxies"].index(p)
                    except ValueError:
                        config["proxy_index"] = 0
                    break
        if "latency_threshold_ms" in profile:
            config["latency_threshold_ms"] = profile["latency_threshold_ms"]
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    build_pac()
    logging.info(f"[PROFILE] Applied: {profile_name}")
    publish_event("profile_changed")
    ui_queue.put(("refresh_status", None))

def morph_profiles_based_on_health():
    with config_lock:
        history = config.get("health_history", [])
        profiles = config.get("profiles_routing", {})
    if not history or not profiles:
        return
    avg_health = sum(history) / len(history)
    with config_lock:
        for name, prof in profiles.items():
            base = prof.get("latency_threshold_ms", 500)
            if avg_health < 50:
                prof["latency_threshold_ms"] = base * 0.8
            elif avg_health > 80:
                prof["latency_threshold_ms"] = base * 1.1
        config["profiles_routing"] = profiles
        save_config(config)
    logging.info(f"[MORPH] Profiles morphed based on health={avg_health:.1f}")

# ============================================================
# SYSTEM HEALTH SCORING
# ============================================================

def compute_health_score(latency_ms, failures_recent):
    if latency_ms is None:
        latency_ms = 2000
    latency_score = max(0, 100 - min(100, latency_ms / 10))
    failure_penalty = min(50, failures_recent * 10)
    health = max(0, min(100, latency_score - failure_penalty))
    return health

def health_monitor_thread():
    while True:
        try:
            touch_heartbeat("health_monitor")
            with config_lock:
                stats = config.get("proxy_stats", {})
                current = config.get("proxy", DEFAULT_CONFIG["proxy"])
                health_window = config.get("health_window", 10)
                history = config.get("health_history", [])
            ps = stats.get(current, {})
            lat = ps.get("last_latency", None)
            failures = ps.get("failures", 0)
            health = compute_health_score(lat, failures)
            with config_lock:
                history.append(health)
                if len(history) > health_window:
                    history = history[-health_window:]
                config["health_history"] = history
                save_config(config)
            publish_event("health_updated", {"proxy": current, "health": health})
            time.sleep(30)
        except Exception as e:
            logging.error(f"[HEALTH] thread error: {e}")
            time.sleep(30)

# ============================================================
# POLICY ENGINE
# ============================================================

def eval_condition(cond_str, context):
    try:
        return bool(eval(cond_str, {"__builtins__": {}}, context))
    except Exception as e:
        logging.error(f"[POLICY] condition eval error '{cond_str}': {e}")
        return False

def policy_engine_thread():
    while True:
        try:
            touch_heartbeat("policy_engine")
            with config_lock:
                rules = config.get("policy_rules", [])
                history = config.get("health_history", [])
                current_profile = config.get("current_profile", "work")
            if history:
                avg_health = sum(history) / len(history)
            else:
                avg_health = 100
            context = {
                "health": avg_health,
                "profile": current_profile
            }
            for rule in rules:
                cond = rule.get("condition", "")
                action = rule.get("action", "")
                target = rule.get("target", "")
                if cond and eval_condition(cond, context):
                    logging.info(f"[POLICY] Triggered: {cond} -> {action}({target})")
                    if action == "switch_profile" and target:
                        apply_profile(target)
                    elif action == "rotate_proxy":
                        rotate_proxy_once()
                    elif action == "apply_best_proxy":
                        apply_best_proxy()
                    publish_event("policy_action", {"rule": rule})
            time.sleep(45)
        except Exception as e:
            logging.error(f"[POLICY] thread error: {e}")
            time.sleep(30)

# ============================================================
# BORG AI (ANOMALY DETECTION)
# ============================================================

def borg_ai_thread():
    while True:
        try:
            touch_heartbeat("borg_ai")
            with config_lock:
                borg = config.get("borg_ai", False)
                interval = config.get("borg_interval", 60)
                threshold = config.get("latency_threshold_ms", 800)
                proxies = config.get("proxies", [])
                stats = config.get("proxy_stats", {})
                current = config.get("proxy", DEFAULT_CONFIG["proxy"])
            if borg and proxies:
                latency_ms, ok = measure_latency()
                anomaly = False
                with config_lock:
                    ps = stats.get(current, {"latency_avg": None, "failures": 0, "last_latency": None})
                    if ok and latency_ms is not None:
                        if ps["latency_avg"] is None:
                            ps["latency_avg"] = latency_ms
                        else:
                            ps["latency_avg"] = (ps["latency_avg"] * 0.7) + (latency_ms * 0.3)
                        if ps["last_latency"] is not None:
                            jitter = abs(latency_ms - ps["last_latency"])
                            if jitter > threshold * 1.5:
                                anomaly = True
                                logging.warning(f"[BORG] Jitter spike on {current} ({jitter:.0f} ms)")
                        ps["last_latency"] = latency_ms
                    else:
                        ps["failures"] += 1
                        if ps["failures"] >= 3:
                            anomaly = True
                            logging.warning(f"[BORG] Repeated failures on {current} ({ps['failures']})")
                    stats[current] = ps
                    config["proxy_stats"] = stats
                    save_config(config)

                if (not ok) or (latency_ms is not None and latency_ms > threshold) or anomaly:
                    logging.info(f"[BORG] latency={latency_ms}, ok={ok}, anomaly={anomaly}, triggering failover/selection")
                    with config_lock:
                        failover = config.get("failover_enabled", True)
                    if failover:
                        apply_best_proxy()
                    else:
                        rotate_proxy_once()
            time.sleep(max(30, interval))
        except Exception as e:
            logging.error(f"[BORG] thread error: {e}")
            time.sleep(20)

# ============================================================
# CLUSTER SYNC + RAFT-LIKE CONSENSUS
# ============================================================

def get_node_descriptor():
    with config_lock:
        hist = config.get("health_history", [100])
        avg_health = sum(hist) / max(len(hist), 1)
        return {
            "node_id": config.get("node_id"),
            "role": config.get("cluster_role", "follower"),
            "config_version": config.get("config_version", 1),
            "health": {
                "avg_health": avg_health,
            },
            "proxy": config.get("proxy"),
            "profile": config.get("current_profile", "work"),
        }

def apply_cluster_config(remote_cfg):
    with config_lock:
        local_ver = config.get("config_version", 1)
        remote_ver = remote_cfg.get("config_version", 1)

        if remote_ver > local_ver:
            for k in [
                "proxy", "proxies", "auto_rotate", "borg_ai",
                "stealth_mode", "cluster_sync", "current_profile",
                "rotation_interval", "latency_threshold_ms",
                "predictive_enabled", "fusion_enabled",
                "ai_governor_enabled", "self_tune_enabled",
                "emergent_enabled", "rl_enabled", "ml_routing_enabled"
            ]:
                if k in remote_cfg:
                    config[k] = remote_cfg[k]
            config["config_version"] = remote_ver
            save_config(config)
            logging.info(f"[CLUSTER] Applied remote config v{remote_ver}")
            build_pac()
            publish_event("proxy_changed")
        else:
            logging.info(f"[CLUSTER] Local config v{local_ver} >= remote v{remote_ver}, skipping apply")

def fetch_cluster_config():
    with config_lock:
        url = config.get("cluster_url", "").strip()
    if not url:
        return None
    try:
        req = urllib.request.Request(url + "/config", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except Exception as e:
        logging.warning(f"[CLUSTER] Failed to fetch config: {e}")
        return None

def push_node_state():
    desc = get_node_descriptor()
    with config_lock:
        url = config.get("cluster_url", "").strip()
    if not url:
        return
    try:
        payload = json.dumps({"node": desc, "config": config}).encode("utf-8")
        req = urllib.request.Request(url + "/state", data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10):
            pass
        logging.info("[CLUSTER] Pushed node state")
    except Exception as e:
        logging.warning(f"[CLUSTER] Failed to push state: {e}")

def raft_elect_leader(nodes):
    if not nodes:
        return None
    best = None
    for n in nodes:
        h = n.get("health", {}).get("avg_health", 0)
        v = n.get("config_version", 0)
        if best is None:
            best = n
        else:
            bh = best.get("health", {}).get("avg_health", 0)
            bv = best.get("config_version", 0)
            if h > bh or (h == bh and v > bv):
                best = n
    return best

def cluster_sync_thread():
    while True:
        try:
            touch_heartbeat("cluster_sync")
            with config_lock:
                enabled = config.get("cluster_sync", False)
                role = config.get("cluster_role", "follower")
                interval = config.get("cluster_interval", 600)
            if not enabled:
                time.sleep(10)
                continue

            if role == "leader":
                push_node_state()
            else:
                remote_cfg = fetch_cluster_config()
                if remote_cfg:
                    nodes = remote_cfg.get("nodes", [])
                    if nodes:
                        leader = raft_elect_leader(nodes)
                        if leader and leader.get("node_id") == config.get("node_id"):
                            with config_lock:
                                config["cluster_role"] = "leader"
                                save_config(config)
                            logging.info("[CLUSTER] Promoted to leader via Raft-like election")
                        else:
                            apply_cluster_config(remote_cfg.get("config", remote_cfg))
                    else:
                        apply_cluster_config(remote_cfg)
            time.sleep(max(60, interval))
        except Exception as e:
            logging.error(f"[CLUSTER] thread error: {e}")
            time.sleep(30)

# ============================================================
# REGISTRY MONITOR
# ============================================================

def registry_monitor_thread():
    last_pac = None
    while True:
        try:
            touch_heartbeat("registry_monitor")
            pac = safe_get_autoconfig_url()
            if pac != last_pac:
                logging.info(f"[REG_MON] PAC changed: {pac}")
                last_pac = pac
            time.sleep(20)
        except Exception as e:
            logging.error(f"[REG_MON] thread error: {e}")
            time.sleep(30)

# ============================================================
# PAC SELF-HEAL
# ============================================================

def pac_self_heal_thread():
    while True:
        try:
            touch_heartbeat("pac_self_heal")
            pac_url = safe_get_autoconfig_url()
            path = None
            if pac_url.startswith("file:///"):
                path = pac_url.replace("file:///", "").replace("/", "\\")
            if path and (not os.path.exists(path) or os.path.getsize(path) == 0):
                logging.warning("[PAC_HEAL] PAC missing or empty, attempting restore from encrypted backup")
                enc_path = path + ".enc"
                if os.path.exists(enc_path):
                    try:
                        with open(enc_path, "rb") as f:
                            data = f.read()
                        content = decrypt_pac(data)
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(content)
                        logging.info("[PAC_HEAL] Restored from encrypted backup")
                    except Exception as e:
                        logging.error(f"[PAC_HEAL] Failed to restore from encrypted backup: {e}")
                        build_pac()
                else:
                    logging.warning("[PAC_HEAL] No encrypted backup, rebuilding PAC")
                    build_pac()
            time.sleep(60)
        except Exception as e:
            logging.error(f"[PAC_HEAL] thread error: {e}")
            time.sleep(30)

# ============================================================
# AUTO-REFRESH LOGS
# ============================================================

def auto_refresh_logs_thread():
    last_size = -1
    while True:
        try:
            touch_heartbeat("logs")
            if os.path.exists(LOG_PATH):
                size = os.path.getsize(LOG_PATH)
                if size != last_size:
                    last_size = size
                    ui_queue.put(("load_logs", None))
        except Exception as e:
            logging.error(f"[LOG_AUTO] Auto log refresh error: {e}")
        time.sleep(5)

# ============================================================
# PER-APP ROUTING + ML-STYLE SCORING
# ============================================================

def get_foreground_process_name():
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h_process = kernel32.OpenProcess(0x0410, False, pid.value)
        if not h_process:
            return None

        exe_name = (ctypes.c_wchar * 260)()
        if psapi.GetModuleBaseNameW(h_process, None, exe_name, 260) == 0:
            kernel32.CloseHandle(h_process)
            return None

        kernel32.CloseHandle(h_process)
        return exe_name.value
    except Exception:
        return None

ml_app_scores = defaultdict(lambda: {"gaming": 0.0, "work": 0.0, "stealth": 0.0})

def ml_update_app_profile(app_name, profile, reward):
    s = ml_app_scores[app_name]
    s[profile] = (s[profile] * 0.9) + (reward * 0.1)

def ml_best_profile_for_app(app_name):
    s = ml_app_scores[app_name]
    return max(s.keys(), key=lambda k: s[k])

def per_app_routing_thread():
    last_app = None
    while True:
        try:
            touch_heartbeat("per_app")
            app = get_foreground_process_name()
            if app and app != last_app:
                last_app = app
                with config_lock:
                    routes = config.get("app_routes", {})
                    target_proxy = routes.get(app)
                    proxies = config.get("proxies", [])
                    ml_enabled = config.get("ml_routing_enabled", True)
                if ml_enabled:
                    profile = ml_best_profile_for_app(app)
                    apply_profile(profile)
                    logging.info(f"[ML_ROUTE] App={app} -> Profile={profile}")
                if target_proxy and target_proxy in proxies:
                    with config_lock:
                        config["proxy"] = target_proxy
                        try:
                            config["proxy_index"] = proxies.index(target_proxy)
                        except ValueError:
                            config["proxy_index"] = 0
                        config["config_version"] = config.get("config_version", 1) + 1
                        save_config(config)
                    logging.info(f"[PER_APP] {app} -> {target_proxy}")
                    build_pac()
                    publish_event("proxy_changed")
                    ui_queue.put(("refresh_status", None))
            time.sleep(3)
        except Exception as e:
            logging.error(f"[PER_APP] thread error: {e}")
            time.sleep(10)

# ============================================================
# WATCHDOG
# ============================================================

def watchdog_thread():
    while True:
        try:
            now = time.time()
            for name, ts in heartbeat.items():
                if ts and now - ts > 600:
                    logging.warning(f"[WATCHDOG] {name} heartbeat stale ({int(now - ts)}s)")
            time.sleep(120)
        except Exception as e:
            logging.error(f"[WATCHDOG] thread error: {e}")
            time.sleep(60)

# ============================================================
# EVENT BUS
# ============================================================

def publish_event(event_type, payload=None):
    event_bus.put({"type": event_type, "payload": payload, "ts": time.time()})

def event_bus_dispatcher():
    while True:
        try:
            touch_heartbeat("event_bus")
            event = event_bus.get()
            etype = event.get("type")
            payload = event.get("payload")
            if etype == "proxy_changed":
                ui_queue.put(("refresh_status", None))
            elif etype == "health_updated":
                pass
            elif etype == "profile_changed":
                ui_queue.put(("refresh_status", None))
            elif etype == "policy_action":
                pass
        except Exception as e:
            logging.error(f"[EVENT_BUS] dispatcher error: {e}")
        time.sleep(0.05)

# ============================================================
# RL AGENT + PROXY GRAPH + ANOMALY CLUSTERING
# ============================================================

class RLAgent:
    def __init__(self):
        self.q = defaultdict(lambda: defaultdict(float))
        self.exploration = 0.2

    def set_exploration(self, eps):
        self.exploration = max(0.0, min(1.0, eps))

    def choose_action(self, state, actions):
        if not actions:
            return None
        if random.random() < self.exploration:
            return random.choice(actions)
        return max(actions, key=lambda a: self.q[state][a])

    def update(self, state, action, reward, alpha=0.1, gamma=0.9):
        old = self.q[state][action]
        self.q[state][action] = old + alpha * (reward + gamma * max(self.q[state].values() or [0.0]) - old)

rl_agent = RLAgent()

class ProxyGraph:
    def __init__(self):
        self.edges = defaultdict(dict)

    def update_edge(self, a, b, weight):
        self.edges[a][b] = weight

    def best_neighbor(self, a):
        if a not in self.edges or not self.edges[a]:
            return None
        return min(self.edges[a].keys(), key=lambda k: self.edges[a][k])

proxy_graph = ProxyGraph()

anomaly_clusters = []

def anomaly_cluster_thread():
    while True:
        try:
            touch_heartbeat("anomaly_cluster")
            with config_lock:
                stats = config.get("proxy_stats", {})
            cluster = []
            for proxy, ps in stats.items():
                lat = ps.get("latency_avg", 0)
                failures = ps.get("failures", 0)
                if lat > 800 or failures >= 3:
                    cluster.append((proxy, lat, failures))
            global anomaly_clusters
            anomaly_clusters = cluster
            time.sleep(30)
        except Exception as e:
            logging.error(f"[ANOM_CLUSTER] thread error: {e}")
            time.sleep(30)

def rl_agent_thread():
    while True:
        try:
            touch_heartbeat("rl_agent")
            with config_lock:
                proxies = config.get("proxies", [])
                current = config.get("proxy", DEFAULT_CONFIG["proxy"])
                stats = config.get("proxy_stats", {})
                rl_enabled = config.get("rl_enabled", True)
                eps = config.get("rl_exploration", 0.2)
            if not rl_enabled or not proxies:
                time.sleep(20)
                continue
            rl_agent.set_exploration(eps)
            ps = stats.get(current, {})
            lat = ps.get("latency_avg", 500)
            failures = ps.get("failures", 0)
            reward = - (lat / 1000.0) - failures
            state = current
            actions = proxies
            action = rl_agent.choose_action(state, actions)
            rl_agent.update(state, action, reward)
            proxy_graph.update_edge(current, action, max(1.0, lat))
            if action != current:
                logging.info(f"[RL] Switching {current} -> {action} (reward={reward:.2f})")
                with config_lock:
                    config["proxy"] = action
                    try:
                        config["proxy_index"] = proxies.index(action)
                    except ValueError:
                        config["proxy_index"] = 0
                    config["config_version"] = config.get("config_version", 1) + 1
                    save_config(config)
                build_pac()
                publish_event("proxy_changed")
            time.sleep(40)
        except Exception as e:
            logging.error(f"[RL] thread error: {e}")
            time.sleep(30)

# ============================================================
# PREDICTIVE / FLUID-PHYSICS / FUSION / EMERGENT
# ============================================================

class ProbabilisticField:
    def __init__(self, mean: float, var: float):
        self.mean = mean
        self.var = var

    def sample(self):
        return random.gauss(self.mean, self.var)

    def update(self, observation: float, weight: float = 1.0):
        if observation is None:
            return
        self.mean = (self.mean + weight * observation) / (1.0 + weight)
        self.var = max(1e-6, self.var * 0.9)

class FluidLatencyField(ProbabilisticField):
    def __init__(self, mean: float, var: float):
        super().__init__(mean, var)
        self.velocity = 0.0
        self.viscosity = 1.0

    def update(self, observation: float, weight: float = 1.0):
        if observation is None:
            return
        prev_mean = self.mean
        super().update(observation, weight)
        self.velocity = self.mean - prev_mean
        self.viscosity = max(0.1, 1.0 - min(0.9, self.var / 1000.0))

class Queen:
    def __init__(self):
        self.nodes = {}

    def update(self, node, events):
        self.nodes[node] = events

    def global_risk(self):
        risk = {}
        for node, evts in self.nodes.items():
            for e in evts:
                risk[e["entity"]] = risk.get(e["entity"], 0) + e["score"]
        return {k: v for k, v in risk.items() if v > 1.5}

class SecEvent:
    def __init__(self, etype, entity, meta=None):
        self.ts = time.time()
        self.type = etype
        self.entity = entity
        self.meta = meta or {}

class AttackChainEngine:
    def __init__(self):
        self.events = deque()
        self.window = 120

    def add_event(self, event_type, data):
        now = time.time()
        self.events.append((now, event_type, data))
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
        return chains

queen = Queen()
attack_chain = AttackChainEngine()
latency_field = FluidLatencyField(mean=300.0, var=100.0)

fusion_lock = threading.Lock()
fusion_state = {
    "latency_mean": 300.0,
    "latency_var": 100.0,
    "latency_velocity": 0.0,
    "latency_viscosity": 1.0,
    "health_avg": 100.0,
    "failures": 0,
    "profile": "work",
    "proxy": "127.0.0.1:8888",
    "risk_score": 0.0,
    "heatmap": [],
}

def fusion_update_from_stats():
    with config_lock:
        stats = config.get("proxy_stats", {})
        current = config.get("proxy", DEFAULT_CONFIG["proxy"])
        history = config.get("health_history", [])
    ps = stats.get(current, {})
    lat = ps.get("last_latency", None)
    failures = ps.get("failures", 0)
    if history:
        avg_health = sum(history) / len(history)
    else:
        avg_health = 100.0

    latency_field.update(lat or latency_field.mean, weight=1.0)

    with fusion_lock:
        fusion_state["latency_mean"] = latency_field.mean
        fusion_state["latency_var"] = latency_field.var
        fusion_state["latency_velocity"] = latency_field.velocity
        fusion_state["latency_viscosity"] = latency_field.viscosity
        fusion_state["health_avg"] = avg_health
        fusion_state["failures"] = failures
        fusion_state["profile"] = config.get("current_profile", "work")
        fusion_state["proxy"] = current

        risk = 0.0
        if lat is not None:
            risk += max(0.0, (lat - 300.0) / 800.0)
        risk += failures * 0.2
        risk += max(0.0, (100.0 - avg_health) / 100.0)
        risk += max(0.0, abs(latency_field.velocity) / 500.0)
        fusion_state["risk_score"] = risk

        heatmap = []
        if lat is not None:
            if lat > 1000:
                heatmap.append("LATENCY_RED")
            elif lat > 600:
                heatmap.append("LATENCY_ORANGE")
            elif lat > 300:
                heatmap.append("LATENCY_YELLOW")
        if failures > 0:
            heatmap.append(f"FAIL_{failures}")
        if avg_health < 50:
            heatmap.append("HEALTH_LOW")
        if risk > 2.0:
            heatmap.append("RISK_HIGH")
        fusion_state["heatmap"] = heatmap

def fusion_engine_thread():
    while True:
        try:
            touch_heartbeat("fusion_engine")
            with config_lock:
                enabled = config.get("fusion_enabled", True)
            if enabled:
                fusion_update_from_stats()
            time.sleep(15)
        except Exception as e:
            logging.error(f"[FUSION] thread error: {e}")
            time.sleep(20)

def predictive_latency_thread():
    while True:
        try:
            touch_heartbeat("predictive_latency")
            with config_lock:
                enabled = config.get("predictive_enabled", True)
                sensitivity = config.get("predictive_sensitivity", 0.7)
            if enabled:
                predicted = latency_field.sample()
                with fusion_lock:
                    risk = fusion_state.get("risk_score", 0.0)
                    vel = fusion_state.get("latency_velocity", 0.0)
                if predicted > 800.0 * sensitivity or risk > 1.5 or vel > 200.0:
                    logging.info(f"[PREDICT] predicted={predicted:.1f}ms risk={risk:.2f} vel={vel:.1f}, pre-emptive action")
                    with config_lock:
                        failover = config.get("failover_enabled", True)
                    if failover:
                        apply_best_proxy()
                    else:
                        rotate_proxy_once()
            time.sleep(20)
        except Exception as e:
            logging.error(f"[PREDICT] thread error: {e}")
            time.sleep(20)

def ai_governor_thread():
    while True:
        try:
            touch_heartbeat("ai_governor")
            with config_lock:
                enabled = config.get("ai_governor_enabled", True)
                proxies = config.get("proxies", [])
                current_profile = config.get("current_profile", "work")
            if not enabled or not proxies:
                time.sleep(10)
                continue

            with fusion_lock:
                lat_mean = fusion_state["latency_mean"]
                health_avg = fusion_state["health_avg"]
                failures = fusion_state["failures"]
                risk = fusion_state["risk_score"]
                vel = fusion_state["latency_velocity"]

            target_profile = current_profile
            if health_avg < 50 or risk > 2.0:
                target_profile = "stealth"
            else:
                app = get_foreground_process_name()
                if app and "game" in app.lower():
                    target_profile = "gaming"
                else:
                    target_profile = "work"

            if target_profile != current_profile:
                logging.info(f"[AI_GOV] Profile {current_profile} -> {target_profile} (health={health_avg:.1f}, risk={risk:.2f})")
                apply_profile(target_profile)

            if lat_mean > 700.0 or failures >= 3 or vel > 200.0:
                logging.info(f"[AI_GOV] lat_mean={lat_mean:.1f}, failures={failures}, vel={vel:.1f}, selecting best proxy")
                apply_best_proxy()

            time.sleep(25)
        except Exception as e:
            logging.error(f"[AI_GOV] thread error: {e}")
            time.sleep(20)

def emergent_behavior_thread():
    while True:
        try:
            touch_heartbeat("emergent_engine")
            with config_lock:
                enabled = config.get("emergent_enabled", True)
            if not enabled:
                time.sleep(20)
                continue
            morph_profiles_based_on_health()
            time.sleep(60)
        except Exception as e:
            logging.error(f"[EMERGENT] thread error: {e}")
            time.sleep(30)

def self_tuning_thread():
    while True:
        try:
            touch_heartbeat("self_tune")
            with config_lock:
                enabled = config.get("self_tune_enabled", True)
                stats = config.get("proxy_stats", {})
                current = config.get("proxy", DEFAULT_CONFIG["proxy"])
                threshold = config.get("latency_threshold_ms", 800)
                sensitivity = config.get("predictive_sensitivity", 0.7)
            if not enabled:
                time.sleep(60)
                continue
            ps = stats.get(current, {})
            lat_avg = ps.get("latency_avg", threshold)
            failures = ps.get("failures", 0)
            with config_lock:
                if failures >= 3 or lat_avg > threshold:
                    config["latency_threshold_ms"] = max(400, threshold * 0.9)
                    config["predictive_sensitivity"] = min(1.0, sensitivity + 0.05)
                else:
                    config["latency_threshold_ms"] = min(1200, threshold * 1.02)
                    config["predictive_sensitivity"] = max(0.5, sensitivity - 0.01)
                save_config(config)
            logging.info(f"[TUNE] threshold={config['latency_threshold_ms']} sensitivity={config['predictive_sensitivity']:.2f}")
            time.sleep(90)
        except Exception as e:
            logging.error(f"[TUNE] thread error: {e}")
            time.sleep(60)

# ============================================================
# DAEMON WATCHDOG (EXTERNAL PROCESS HOOK SIMULATION)
# ============================================================

def daemon_watchdog_thread():
    while True:
        try:
            touch_heartbeat("daemon_watchdog")
            now = time.time()
            stale = []
            for name, ts in heartbeat.items():
                if ts and now - ts > 900:
                    stale.append(name)
            if stale:
                logging.error(f"[DAEMON] Subsystems stale: {stale} -> altered state")
            with fusion_lock:
                risk = fusion_state.get("risk_score", 0.0)
            if risk > 3.0:
                logging.error(f"[DAEMON] Global risk={risk:.2f} -> HIGH ALTERED STATE")
            time.sleep(60)
        except Exception as e:
            logging.error(f"[DAEMON] thread error: {e}")
            time.sleep(60)

# ============================================================
# STATUS / DASHBOARD
# ============================================================

def refresh():
    try:
        pac = safe_get_autoconfig_url()
        with config_lock:
            proxy = config.get("proxy", DEFAULT_CONFIG["proxy"])
            auto_rotate = config.get("auto_rotate", False)
            borg_ai = config.get("borg_ai", False)
            startup = config.get("startup", False)
            stealth = config.get("stealth_mode", False)
            cluster_sync = config.get("cluster_sync", False)
            current_profile = config.get("current_profile", "work")
            history = config.get("health_history", [])
            predictive_enabled = config.get("predictive_enabled", True)
            fusion_enabled = config.get("fusion_enabled", True)
            ai_governor_enabled = config.get("ai_governor_enabled", True)
            emergent_enabled = config.get("emergent_enabled", True)
            self_tune_enabled = config.get("self_tune_enabled", True)
            rl_enabled = config.get("rl_enabled", True)
            ml_enabled = config.get("ml_routing_enabled", True)
        if history:
            avg_health = sum(history) / len(history)
        else:
            avg_health = 100.0

        with fusion_lock:
            risk = fusion_state.get("risk_score", 0.0)
            lat_mean = fusion_state.get("latency_mean", 0.0)
            vel = fusion_state.get("latency_velocity", 0.0)
            visc = fusion_state.get("latency_viscosity", 1.0)
            heatmap = fusion_state.get("heatmap", [])

        heat_str = ", ".join(heatmap) if heatmap else "None"

        status.set(
            "Status\n\n"
            f"PAC: {pac}\n"
            f"Proxy: {proxy}\n"
            f"Profile: {current_profile}\n"
            f"Health: {avg_health:.1f}/100\n"
            f"Latency Mean: {lat_mean:.1f} ms\n"
            f"Latency Velocity: {vel:.1f} ms/step\n"
            f"Latency Viscosity: {visc:.2f}\n"
            f"Risk Score: {risk:.2f}\n"
            f"Anomaly Heatmap: {heat_str}\n"
            f"Auto-Rotate: {'ON' if auto_rotate else 'OFF'}\n"
            f"Borg AI: {'ON' if borg_ai else 'OFF'}\n"
            f"Predictive: {'ON' if predictive_enabled else 'OFF'}\n"
            f"Fusion Engine: {'ON' if fusion_enabled else 'OFF'}\n"
            f"AI Governor: {'ON' if ai_governor_enabled else 'OFF'}\n"
            f"Emergent: {'ON' if emergent_enabled else 'OFF'}\n"
            f"Self-Tune: {'ON' if self_tune_enabled else 'OFF'}\n"
            f"RL Agent: {'ON' if rl_enabled else 'OFF'}\n"
            f"ML Routing: {'ON' if ml_enabled else 'OFF'}\n"
            f"Startup: {'ON' if startup else 'OFF'}\n"
            f"Stealth: {'ON' if stealth else 'OFF'}\n"
            f"Cluster Sync: {'ON' if cluster_sync else 'OFF'}"
        )
    except Exception as e:
        status.set(str(e))

# ============================================================
# GUI
# ============================================================

root = tk.Tk()
root.title("Proxy Controller Pro - HybridBrain Fusion vX")
root.geometry("900x760")
root.configure(bg="#202020")

tk.Label(
    root,
    text="Proxy Controller Pro - HybridBrain Fusion vX",
    fg="lime",
    bg="#202020",
    font=("Segoe UI", 18, "bold")
).pack(pady=15)

status = tk.StringVar()

tk.Label(
    root,
    textvariable=status,
    fg="cyan",
    bg="#202020",
    justify="left",
    font=("Consolas", 10)
).pack()

btn_frame = tk.Frame(root, bg="#202020")
btn_frame.pack(pady=10)

tk.Button(
    btn_frame,
    text="ENABLE PROXY",
    width=20,
    command=enable_proxy_threaded
).grid(row=0, column=0, padx=5, pady=5)

tk.Button(
    btn_frame,
    text="DISABLE PROXY",
    width=20,
    command=disable_proxy_threaded
).grid(row=0, column=1, padx=5, pady=5)

tk.Button(
    btn_frame,
    text="RESTORE BACKUP",
    width=20,
    command=restore_threaded
).grid(row=1, column=0, padx=5, pady=5)

tk.Button(
    btn_frame,
    text="TEST CONNECTION",
    width=20,
    command=test_threaded
).grid(row=1, column=1, padx=5, pady=5)

tk.Button(
    btn_frame,
    text="ROTATE PROXY",
    width=20,
    command=rotate_proxy_button
).grid(row=2, column=0, padx=5, pady=5)

options_frame = tk.Frame(root, bg="#202020")
options_frame.pack(pady=5)

with config_lock:
    auto_rotate_var = tk.BooleanVar(value=config.get("auto_rotate", False))
    borg_ai_var = tk.BooleanVar(value=config.get("borg_ai", False))
    startup_var = tk.BooleanVar(value=config.get("startup", True))
    stealth_var = tk.BooleanVar(value=config.get("stealth_mode", False))
    cluster_var = tk.BooleanVar(value=config.get("cluster_sync", False))
    predictive_var = tk.BooleanVar(value=config.get("predictive_enabled", True))
    fusion_var = tk.BooleanVar(value=config.get("fusion_enabled", True))
    ai_gov_var = tk.BooleanVar(value=config.get("ai_governor_enabled", True))
    emergent_var = tk.BooleanVar(value=config.get("emergent_enabled", True))
    self_tune_var = tk.BooleanVar(value=config.get("self_tune_enabled", True))
    rl_var = tk.BooleanVar(value=config.get("rl_enabled", True))
    ml_var = tk.BooleanVar(value=config.get("ml_routing_enabled", True))

def on_auto_rotate_toggle():
    with config_lock:
        config["auto_rotate"] = auto_rotate_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Auto-rotate set to {config['auto_rotate']}")
    refresh()

def on_borg_ai_toggle():
    with config_lock:
        config["borg_ai"] = borg_ai_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Borg AI set to {config['borg_ai']}")
    refresh()

def on_startup_toggle():
    with config_lock:
        config["startup"] = startup_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    sync_startup_from_config()
    logging.info(f"[UI] Startup set to {config['startup']}")
    refresh()

def on_stealth_toggle():
    with config_lock:
        config["stealth_mode"] = stealth_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Stealth mode set to {config['stealth_mode']}")
    enable_proxy_threaded()
    refresh()

def on_cluster_toggle():
    with config_lock:
        config["cluster_sync"] = cluster_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Cluster sync set to {config['cluster_sync']}")
    refresh()

def on_predictive_toggle():
    with config_lock:
        config["predictive_enabled"] = predictive_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Predictive set to {config['predictive_enabled']}")
    refresh()

def on_fusion_toggle():
    with config_lock:
        config["fusion_enabled"] = fusion_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Fusion engine set to {config['fusion_enabled']}")
    refresh()

def on_ai_gov_toggle():
    with config_lock:
        config["ai_governor_enabled"] = ai_gov_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] AI Governor set to {config['ai_governor_enabled']}")
    refresh()

def on_emergent_toggle():
    with config_lock:
        config["emergent_enabled"] = emergent_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Emergent set to {config['emergent_enabled']}")
    refresh()

def on_self_tune_toggle():
    with config_lock:
        config["self_tune_enabled"] = self_tune_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Self-tune set to {config['self_tune_enabled']}")
    refresh()

def on_rl_toggle():
    with config_lock:
        config["rl_enabled"] = rl_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] RL agent set to {config['rl_enabled']}")
    refresh()

def on_ml_toggle():
    with config_lock:
        config["ml_routing_enabled"] = ml_var.get()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] ML routing set to {config['ml_routing_enabled']}")
    refresh()

tk.Checkbutton(
    options_frame,
    text="Auto-Rotate Proxies",
    variable=auto_rotate_var,
    command=on_auto_rotate_toggle,
    fg="white",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=0, column=0, padx=10)

tk.Checkbutton(
    options_frame,
    text="Borg AI Mode",
    variable=borg_ai_var,
    command=on_borg_ai_toggle,
    fg="lime",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=0, column=1, padx=10)

tk.Checkbutton(
    options_frame,
    text="Run at Startup",
    variable=startup_var,
    command=on_startup_toggle,
    fg="white",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=0, column=2, padx=10)

tk.Checkbutton(
    options_frame,
    text="Stealth Mode",
    variable=stealth_var,
    command=on_stealth_toggle,
    fg="orange",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=1, column=0, padx=10)

tk.Checkbutton(
    options_frame,
    text="Cluster Sync",
    variable=cluster_var,
    command=on_cluster_toggle,
    fg="cyan",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=1, column=1, padx=10)

tk.Checkbutton(
    options_frame,
    text="Predictive Latency",
    variable=predictive_var,
    command=on_predictive_toggle,
    fg="magenta",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=1, column=2, padx=10)

tk.Checkbutton(
    options_frame,
    text="Fusion Engine",
    variable=fusion_var,
    command=on_fusion_toggle,
    fg="yellow",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=2, column=0, padx=10)

tk.Checkbutton(
    options_frame,
    text="AI Governor",
    variable=ai_gov_var,
    command=on_ai_gov_toggle,
    fg="red",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=2, column=1, padx=10)

tk.Checkbutton(
    options_frame,
    text="Emergent Behavior",
    variable=emergent_var,
    command=on_emergent_toggle,
    fg="cyan",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=2, column=2, padx=10)

tk.Checkbutton(
    options_frame,
    text="Self-Tuning",
    variable=self_tune_var,
    command=on_self_tune_toggle,
    fg="white",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=3, column=0, padx=10)

tk.Checkbutton(
    options_frame,
    text="RL Agent",
    variable=rl_var,
    command=on_rl_toggle,
    fg="magenta",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=3, column=1, padx=10)

tk.Checkbutton(
    options_frame,
    text="ML Routing",
    variable=ml_var,
    command=on_ml_toggle,
    fg="red",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=3, column=2, padx=10)

cluster_url_entry = tk.Entry(options_frame, width=40)
cluster_url_entry.grid(row=4, column=0, columnspan=2, padx=10)
with config_lock:
    cluster_url_entry.insert(0, config.get("cluster_url", ""))

def on_cluster_url_save():
    with config_lock:
        config["cluster_url"] = cluster_url_entry.get().strip()
        config["config_version"] = config.get("config_version", 1) + 1
        save_config(config)
    logging.info(f"[UI] Cluster URL set to {config['cluster_url']}")

tk.Button(
    options_frame,
    text="Save Cluster URL",
    command=on_cluster_url_save
).grid(row=4, column=2, padx=5)

profile_frame = tk.Frame(root, bg="#202020")
profile_frame.pack(pady=5)

tk.Label(
    profile_frame,
    text="Profile:",
    fg="white",
    bg="#202020"
).grid(row=0, column=0, padx=5)

with config_lock:
    profile_var = tk.StringVar(value=config.get("current_profile", "work"))

def on_profile_change(*args):
    apply_profile(profile_var.get())
    refresh()

profile_dropdown = tk.OptionMenu(
    profile_frame,
    profile_var,
    "work",
    "gaming",
    "stealth",
    command=lambda _: on_profile_change()
)
profile_dropdown.config(bg="#303030", fg="white")
profile_dropdown["menu"].config(bg="#303030", fg="white")
profile_dropdown.grid(row=0, column=1, padx=5)

logbox = scrolledtext.ScrolledText(
    root,
    height=12,
    bg="#101010",
    fg="#e0e0e0",
    insertbackground="white"
)
logbox.pack(fill="both", expand=True, padx=10, pady=10)

def load_logs():
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                content = f.read()
            logbox.delete("1.0", tk.END)
            logbox.insert(tk.END, content)
        except Exception as e:
            logging.error(f"[LOG] Failed to load logs: {e}")

tk.Button(
    root,
    text="REFRESH LOGS",
    command=load_logs
).pack()

# ============================================================
# UI QUEUE
# ============================================================

def process_ui_queue():
    try:
        while True:
            action, payload = ui_queue.get_nowait()
            if action == "refresh_status":
                refresh()
            elif action == "load_logs":
                load_logs()
            elif action == "messagebox_info":
                title, msg = payload
                messagebox.showinfo(title, msg)
            elif action == "messagebox_error":
                title, msg = payload
                messagebox.showerror(title, msg)
    except queue.Empty:
        pass
    root.after(500, process_ui_queue)

# ============================================================
# INITIALIZATION + THREADS
# ============================================================

sync_startup_from_config()
refresh()
load_logs()

threading.Thread(target=auto_rotate_thread, daemon=True).start()
threading.Thread(target=borg_ai_thread, daemon=True).start()
threading.Thread(target=registry_monitor_thread, daemon=True).start()
threading.Thread(target=auto_refresh_logs_thread, daemon=True).start()
threading.Thread(target=cluster_sync_thread, daemon=True).start()
threading.Thread(target=pac_self_heal_thread, daemon=True).start()
threading.Thread(target=per_app_routing_thread, daemon=True).start()
threading.Thread(target=watchdog_thread, daemon=True).start()
threading.Thread(target=event_bus_dispatcher, daemon=True).start()
threading.Thread(target=health_monitor_thread, daemon=True).start()
threading.Thread(target=policy_engine_thread, daemon=True).start()
threading.Thread(target=fusion_engine_thread, daemon=True).start()
threading.Thread(target=anomaly_cluster_thread, daemon=True).start()
threading.Thread(target=rl_agent_thread, daemon=True).start()
threading.Thread(target=daemon_watchdog_thread, daemon=True).start()
threading.Thread(target=predictive_latency_thread, daemon=True).start()
threading.Thread(target=ai_governor_thread, daemon=True).start()
threading.Thread(target=emergent_behavior_thread, daemon=True).start()
threading.Thread(target=self_tuning_thread, daemon=True).start()

touch_heartbeat("event_bus")

process_ui_queue()
root.mainloop()
