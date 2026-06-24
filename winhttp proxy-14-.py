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
import tkinter as tk
from tkinter import messagebox, scrolledtext
import winreg

# ============================================================
# ADMIN
# ============================================================

def ensure_admin():
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

ensure_admin()

# ============================================================
# FILES / PATHS
# ============================================================

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")
BACKUP = os.path.join(BASE, "backup.json")
PAC = os.path.join(BASE, "proxy.pac")
LOG = os.path.join(BASE, "proxy.log")

logging.basicConfig(
    filename=LOG,
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)

# ============================================================
# DEFAULT SETTINGS + SCHEMA
# ============================================================

SCHEMA_VERSION = 1

DEFAULT = {
    "schema_version": SCHEMA_VERSION,
    "proxy": "127.0.0.1:8888",
    "startup": True,
    "profiles": {
        "Teams": True,
        "Office365": True,
        "OneDrive": True
    },
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
        "*.azureedge.net"
    ],
    # Proxy rotation / failover
    "proxies": [
        "127.0.0.1:8888",
        "127.0.0.1:8889"
    ],
    "proxy_index": 0,
    "auto_rotate": False,
    "rotation_interval": 300,
    "failover_enabled": True,
    # Borg AI / ML-like scoring
    "borg_ai": False,
    "borg_interval": 60,
    "latency_threshold_ms": 800,
    "proxy_stats": {},  # {proxy: {"latency_avg": float, "failures": int}}
    # Cloud sync
    "cloud_sync": False,
    "cloud_url": "",
    "cloud_interval": 600,
    # Stealth mode
    "stealth_mode": False
}

config_lock = threading.Lock()
ui_queue = queue.Queue()

# ============================================================
# WATCHDOG HEARTBEATS
# ============================================================

heartbeat = {
    "auto_rotate": 0,
    "borg_ai": 0,
    "registry": 0,
    "logs": 0,
    "cloud": 0
}

def touch_heartbeat(name: str):
    heartbeat[name] = time.time()

# ============================================================
# CONFIG VALIDATION
# ============================================================

def validate_config(data):
    changed = False

    for k, v in DEFAULT.items():
        if k not in data:
            data[k] = v
            changed = True

    if data.get("schema_version") != SCHEMA_VERSION:
        data["schema_version"] = SCHEMA_VERSION
        changed = True

    if not isinstance(data.get("proxies"), list):
        data["proxies"] = DEFAULT["proxies"]
        changed = True

    if not isinstance(data.get("proxy_stats"), dict):
        data["proxy_stats"] = {}
        changed = True

    if not isinstance(data.get("bypass"), list):
        data["bypass"] = DEFAULT["bypass"]
        changed = True

    if not isinstance(data.get("startup"), bool):
        data["startup"] = DEFAULT["startup"]
        changed = True

    return data, changed

def load_config():
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, "r") as f:
                data = json.load(f)
            data, changed = validate_config(data)
            if changed:
                save_config(data)
            return data
        except Exception as e:
            logging.error(f"Config load failed, using default: {e}")
    save_config(DEFAULT)
    return DEFAULT

def save_config(data):
    try:
        with open(CONFIG, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Config save failed: {e}")

config = load_config()

# ============================================================
# STARTUP REGISTRATION
# ============================================================

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "ProxyControllerPro"

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
# PAC PATH (STEALTH MODE)
# ============================================================

def get_pac_path():
    with config_lock:
        stealth = config.get("stealth_mode", False)
    if not stealth:
        return PAC
    temp_dir = tempfile.gettempdir()
    name = f"sys_{random.randint(100000, 999999)}.dat"
    return os.path.join(temp_dir, name)

# ============================================================
# PAC BUILDER
# ============================================================

def build_pac():
    with config_lock:
        proxy = config["proxy"]
        bypass_list = config["bypass"][:]
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
        with open(pac_path, "w") as f:
            f.write(pac_content)
        logging.info(f"PAC written ({'stealth' if stealth else 'normal'}): {pac_path}")
    except Exception as e:
        logging.error(f"Failed to write PAC: {e}")

    return pac_path

# ============================================================
# BACKUP
# ============================================================

def backup_settings():
    data = {}
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        )

        for x in ["ProxyEnable", "ProxyServer", "AutoConfigURL"]:
            try:
                val, typ = winreg.QueryValueEx(key, x)
                data[x] = {"value": val, "type": typ}
            except:
                pass

        winreg.CloseKey(key)

        with open(BACKUP, "w") as f:
            json.dump(data, f, indent=4)

        logging.info("Backup created")
    except Exception as e:
        logging.error(f"Backup failed: {e}")

# ============================================================
# ENABLE / DISABLE / RESTORE
# ============================================================

def _apply_enable_proxy():
    backup_settings()
    pac_path = build_pac()

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
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

        logging.info("Proxy enabled")
    except Exception as e:
        logging.error(f"Enable proxy failed: {e}")

def enable_proxy_threaded():
    def worker():
        try:
            _apply_enable_proxy()
        except Exception as e:
            logging.error(f"Enable proxy worker failed: {e}")
        ui_queue.put(("refresh_status", None))
    threading.Thread(target=worker, daemon=True).start()

def _apply_disable_proxy():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
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

        logging.info("Proxy disabled")
    except Exception as e:
        logging.error(f"Disable proxy failed: {e}")

def disable_proxy_threaded():
    def worker():
        try:
            _apply_disable_proxy()
        except Exception as e:
            logging.error(f"Disable proxy worker failed: {e}")
        ui_queue.put(("refresh_status", None))
    threading.Thread(target=worker, daemon=True).start()

def _apply_restore():
    if not os.path.exists(BACKUP):
        ui_queue.put(("messagebox_error", ("Restore", "No backup found")))
        return

    try:
        with open(BACKUP) as f:
            data = json.load(f)

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_SET_VALUE
        )

        for k, obj in data.items():
            v = obj["value"]
            t = obj["type"]
            winreg.SetValueEx(key, k, 0, t, v)

        winreg.CloseKey(key)

        logging.info("Settings restored")
    except Exception as e:
        logging.error(f"Restore failed: {e}")

def restore_threaded():
    def worker():
        try:
            _apply_restore()
        except Exception as e:
            logging.error(f"Restore worker failed: {e}")
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
        save_config(config)
        logging.info(f"Proxy rotated to {config['proxy']}")
    build_pac()
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
        save_config(config)
        logging.info(f"Borg AI selected best proxy: {best}")
    build_pac()
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
            logging.error(f"Auto-rotate thread error: {e}")
            time.sleep(20)

# ============================================================
# BORG AI MODE (ML-like scoring)
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
                current = config["proxy"]
            if borg and proxies:
                latency_ms, ok = measure_latency()
                with config_lock:
                    ps = stats.get(current, {"latency_avg": None, "failures": 0})
                    if ok and latency_ms is not None:
                        if ps["latency_avg"] is None:
                            ps["latency_avg"] = latency_ms
                        else:
                            ps["latency_avg"] = (ps["latency_avg"] * 0.7) + (latency_ms * 0.3)
                    else:
                        ps["failures"] += 1
                    stats[current] = ps
                    config["proxy_stats"] = stats
                    save_config(config)

                if (not ok) or (latency_ms is not None and latency_ms > threshold):
                    logging.info(f"Borg AI: latency={latency_ms}, ok={ok}, triggering failover/selection")
                    with config_lock:
                        failover = config.get("failover_enabled", True)
                    if failover:
                        apply_best_proxy()
                    else:
                        rotate_proxy_once()
            time.sleep(max(30, interval))
        except Exception as e:
            logging.error(f"Borg AI thread error: {e}")
            time.sleep(20)

# ============================================================
# CLOUD-SYNCED PROXY LISTS
# ============================================================

def cloud_sync_thread():
    while True:
        try:
            touch_heartbeat("cloud")
            with config_lock:
                enabled = config.get("cloud_sync", False)
                url = config.get("cloud_url", "")
                interval = config.get("cloud_interval", 600)
            if enabled and url:
                try:
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        data = resp.read().decode("utf-8")
                    remote = json.loads(data)
                    if isinstance(remote, list) and remote:
                        with config_lock:
                            config["proxies"] = remote
                            if config["proxy"] not in remote:
                                config["proxy"] = remote[0]
                                config["proxy_index"] = 0
                            save_config(config)
                        logging.info(f"Cloud sync updated proxies: {len(remote)} entries")
                        build_pac()
                        ui_queue.put(("refresh_status", None))
                except Exception as e:
                    logging.error(f"Cloud sync fetch failed: {e}")
            time.sleep(max(120, interval))
        except Exception as e:
            logging.error(f"Cloud sync thread error: {e}")
            time.sleep(30)

# ============================================================
# REAL-TIME REGISTRY MONITORING (FIXED VIA CTYPES)
# ============================================================

def registry_monitor_thread():
    REG_NOTIFY_CHANGE_LAST_SET = 0x00000004

    advapi32 = ctypes.WinDLL("Advapi32.dll")
    NotifyChangeKeyValue = advapi32.RegNotifyChangeKeyValue
    NotifyChangeKeyValue.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_bool
    ]
    NotifyChangeKeyValue.restype = ctypes.c_long

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_NOTIFY
        )
    except Exception as e:
        logging.error(f"Registry monitor failed to open key: {e}")
        return

    hKey = ctypes.c_void_p(key.handle)

    while True:
        try:
            touch_heartbeat("registry")
            result = NotifyChangeKeyValue(
                hKey,
                True,
                REG_NOTIFY_CHANGE_LAST_SET,
                None,
                False
            )
            if result == 0:
                logging.info("Registry change detected (Internet Settings)")
                ui_queue.put(("refresh_status", None))
            else:
                logging.error(f"RegNotifyChangeKeyValue returned error code: {result}")
                time.sleep(10)
        except Exception as e:
            logging.error(f"Registry monitor error: {e}")
            time.sleep(10)

# ============================================================
# AUTO-REFRESH LOGS
# ============================================================

def auto_refresh_logs_thread():
    last_size = -1
    while True:
        try:
            touch_heartbeat("logs")
            if os.path.exists(LOG):
                size = os.path.getsize(LOG)
                if size != last_size:
                    last_size = size
                    ui_queue.put(("load_logs", None))
        except Exception as e:
            logging.error(f"Auto log refresh error: {e}")
        time.sleep(5)

# ============================================================
# WATCHDOG (CRASH / STALL DETECTION)
# ============================================================

def watchdog_thread():
    while True:
        try:
            now = time.time()
            for name, ts in heartbeat.items():
                if ts and now - ts > 600:
                    logging.warning(f"Watchdog: {name} heartbeat stale ({int(now - ts)}s)")
            time.sleep(120)
        except Exception as e:
            logging.error(f"Watchdog thread error: {e}")
            time.sleep(60)

# ============================================================
# STATUS
# ============================================================

def refresh():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        )
        try:
            pac = winreg.QueryValueEx(key, "AutoConfigURL")[0]
        except:
            pac = "None"
        winreg.CloseKey(key)

        with config_lock:
            proxy = config["proxy"]
            auto_rotate = config.get("auto_rotate", False)
            borg_ai = config.get("borg_ai", False)
            startup = config.get("startup", False)
            stealth = config.get("stealth_mode", False)
            cloud_sync = config.get("cloud_sync", False)

        status.set(
            "Status\n\n"
            f"PAC: {pac}\n"
            f"Proxy: {proxy}\n"
            f"Teams: DIRECT\n"
            f"Auto-Rotate: {'ON' if auto_rotate else 'OFF'}\n"
            f"Borg AI: {'ON' if borg_ai else 'OFF'}\n"
            f"Startup: {'ON' if startup else 'OFF'}\n"
            f"Stealth: {'ON' if stealth else 'OFF'}\n"
            f"Cloud Sync: {'ON' if cloud_sync else 'OFF'}"
        )
    except Exception as e:
        status.set(str(e))

# ============================================================
# GUI
# ============================================================

root = tk.Tk()
root.title("Proxy Controller Pro - Borg ML Edition")
root.geometry("780x640")
root.configure(bg="#202020")

tk.Label(
    root,
    text="Proxy Controller Pro - Borg ML",
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

auto_rotate_var = tk.BooleanVar(value=config.get("auto_rotate", False))
borg_ai_var = tk.BooleanVar(value=config.get("borg_ai", False))
startup_var = tk.BooleanVar(value=config.get("startup", True))
stealth_var = tk.BooleanVar(value=config.get("stealth_mode", False))
cloud_var = tk.BooleanVar(value=config.get("cloud_sync", False))

def on_auto_rotate_toggle():
    with config_lock:
        config["auto_rotate"] = auto_rotate_var.get()
        save_config(config)
    logging.info(f"Auto-rotate set to {config['auto_rotate']}")
    refresh()

def on_borg_ai_toggle():
    with config_lock:
        config["borg_ai"] = borg_ai_var.get()
        save_config(config)
    logging.info(f"Borg AI set to {config['borg_ai']}")
    refresh()

def on_startup_toggle():
    with config_lock:
        config["startup"] = startup_var.get()
        save_config(config)
    sync_startup_from_config()
    refresh()

def on_stealth_toggle():
    with config_lock:
        config["stealth_mode"] = stealth_var.get()
        save_config(config)
    logging.info(f"Stealth mode set to {config['stealth_mode']}")
    enable_proxy_threaded()
    refresh()

def on_cloud_toggle():
    with config_lock:
        config["cloud_sync"] = cloud_var.get()
        save_config(config)
    logging.info(f"Cloud sync set to {config['cloud_sync']}")
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
    text="Borg AI Mode (ML Scoring)",
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
    text="Cloud-Synced Proxies",
    variable=cloud_var,
    command=on_cloud_toggle,
    fg="cyan",
    bg="#202020",
    selectcolor="#202020",
    activebackground="#202020"
).grid(row=1, column=1, padx=10)

cloud_url_entry = tk.Entry(options_frame, width=40)
cloud_url_entry.grid(row=1, column=2, padx=10)
cloud_url_entry.insert(0, config.get("cloud_url", ""))

def on_cloud_url_save():
    with config_lock:
        config["cloud_url"] = cloud_url_entry.get().strip()
        save_config(config)
    logging.info(f"Cloud URL set to {config['cloud_url']}")

tk.Button(
    options_frame,
    text="Save Cloud URL",
    command=on_cloud_url_save
).grid(row=1, column=3, padx=5)

logbox = scrolledtext.ScrolledText(
    root,
    height=12,
    bg="#101010",
    fg="#e0e0e0",
    insertbackground="white"
)
logbox.pack(fill="both", expand=True, padx=10, pady=10)

def load_logs():
    if os.path.exists(LOG):
        try:
            with open(LOG) as f:
                content = f.read()
            logbox.delete("1.0", tk.END)
            logbox.insert(tk.END, content)
        except Exception as e:
            logging.error(f"Failed to load logs: {e}")

tk.Button(
    root,
    text="REFRESH LOGS",
    command=load_logs
).pack()

# ============================================================
# UI QUEUE (THREAD -> MAIN)
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
threading.Thread(target=cloud_sync_thread, daemon=True).start()
threading.Thread(target=watchdog_thread, daemon=True).start()

process_ui_queue()
root.mainloop()
