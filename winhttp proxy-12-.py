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
import tkinter as tk
from tkinter import messagebox, scrolledtext
import winreg
import datetime

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
# DEFAULT SETTINGS
# ============================================================

DEFAULT = {
    "proxy": "127.0.0.1:8888",
    "startup": True,  # default: ON at boot
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
    # Proxy rotation
    "proxies": [
        "127.0.0.1:8888",
        "127.0.0.1:8889"
    ],
    "proxy_index": 0,
    "auto_rotate": False,
    "rotation_interval": 300,  # seconds
    # Borg AI
    "borg_ai": False,
    "borg_interval": 60  # seconds
}

config_lock = threading.Lock()

def load_config():
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, "r") as f:
                data = json.load(f)
            # ensure new keys exist
            changed = False
            for k, v in DEFAULT.items():
                if k not in data:
                    data[k] = v
                    changed = True
            if changed:
                save_config(data)
            return data
        except:
            pass
    save_config(DEFAULT)
    return DEFAULT

def save_config(data):
    with open(CONFIG, "w") as f:
        json.dump(data, f, indent=4)

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
    set_startup(bool(config.get("startup", False)))

# ============================================================
# PAC BUILDER
# ============================================================

def build_pac():
    with config_lock:
        proxy = config["proxy"]
        bypass_list = config["bypass"][:]

    rules = ""
    for x in bypass_list:
        # basic escaping of quotes
        x_safe = x.replace('"', r'\"')
        rules += f'shExpMatch(host,"{x_safe}") ||\n'

    pac = f"""
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
    with open(PAC, "w") as f:
        f.write(pac)

# ============================================================
# BACKUP
# ============================================================

def backup_settings():
    data = {}
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

# ============================================================
# ENABLE / DISABLE / RESTORE
# ============================================================

def _apply_enable_proxy():
    backup_settings()
    build_pac()

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
        "file:///" + PAC.replace("\\", "/")
    )

    # PAC mode: ProxyEnable = 0 (WinINET uses PAC)
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

def enable_proxy_threaded():
    def worker():
        try:
            _apply_enable_proxy()
        except Exception as e:
            logging.error(f"Enable proxy failed: {e}")
        ui_queue.put(("refresh_status", None))
    threading.Thread(target=worker, daemon=True).start()

def _apply_disable_proxy():
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

def disable_proxy_threaded():
    def worker():
        try:
            _apply_disable_proxy()
        except Exception as e:
            logging.error(f"Disable proxy failed: {e}")
        ui_queue.put(("refresh_status", None))
    threading.Thread(target=worker, daemon=True).start()

def _apply_restore():
    if not os.path.exists(BACKUP):
        ui_queue.put(("messagebox_error", ("Restore", "No backup found")))
        return

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

def restore_threaded():
    def worker():
        try:
            _apply_restore()
        except Exception as e:
            logging.error(f"Restore failed: {e}")
        ui_queue.put(("refresh_status", None))
    threading.Thread(target=worker, daemon=True).start()

# ============================================================
# TEST CONNECTION
# ============================================================

def _test_connection():
    try:
        socket.create_connection(("google.com", 80), 5)
        ui_queue.put(("messagebox_info", ("Network", "Internet OK")))
    except:
        ui_queue.put(("messagebox_error", ("Network", "Connection failed")))

def test_threaded():
    threading.Thread(target=_test_connection, daemon=True).start()

# ============================================================
# PROXY ROTATION
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

def auto_rotate_thread():
    while True:
        with config_lock:
            auto = config.get("auto_rotate", False)
            interval = config.get("rotation_interval", 300)
        if auto:
            rotate_proxy_once()
        time.sleep(max(10, interval))

# ============================================================
# BORG AI MODE (AUTONOMOUS)
# ============================================================

def borg_ai_thread():
    while True:
        with config_lock:
            borg = config.get("borg_ai", False)
            interval = config.get("borg_interval", 60)
        if borg:
            # simple heuristic: if connection fails, rotate proxy
            try:
                socket.create_connection(("google.com", 80), 5).close()
                # OK, do nothing
            except:
                logging.info("Borg AI: connection failed, rotating proxy")
                rotate_proxy_once()
        time.sleep(max(10, interval))

# ============================================================
# REAL-TIME REGISTRY MONITORING
# ============================================================

def registry_monitor_thread():
    path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            path,
            0,
            winreg.KEY_NOTIFY
        )
    except Exception as e:
        logging.error(f"Registry monitor failed to open key: {e}")
        return

    while True:
        try:
            winreg.NotifyChangeKeyValue(
                key,
                True,
                winreg.REG_NOTIFY_CHANGE_LAST_SET,
                None,
                False
            )
            logging.info("Registry change detected (Internet Settings)")
            ui_queue.put(("refresh_status", None))
        except Exception as e:
            logging.error(f"Registry monitor error: {e}")
            time.sleep(5)

# ============================================================
# AUTO-REFRESH LOGS
# ============================================================

def auto_refresh_logs_thread():
    last_size = -1
    while True:
        try:
            if os.path.exists(LOG):
                size = os.path.getsize(LOG)
                if size != last_size:
                    last_size = size
                    ui_queue.put(("load_logs", None))
        except Exception as e:
            logging.error(f"Auto log refresh error: {e}")
        time.sleep(3)

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

        status.set(
            "Status\n\n"
            f"PAC: {pac}\n"
            f"Proxy: {proxy}\n"
            f"Teams: DIRECT\n"
            f"Auto-Rotate: {'ON' if auto_rotate else 'OFF'}\n"
            f"Borg AI: {'ON' if borg_ai else 'OFF'}\n"
            f"Startup: {'ON' if startup else 'OFF'}"
        )
    except Exception as e:
        status.set(str(e))

# ============================================================
# GUI
# ============================================================

root = tk.Tk()
root.title("Proxy Controller Pro - Borg Edition")
root.geometry("700x600")
root.configure(bg="#202020")

tk.Label(
    root,
    text="Proxy Controller Pro - Borg AI",
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

# Checkbuttons for auto-rotate, Borg AI, startup
options_frame = tk.Frame(root, bg="#202020")
options_frame.pack(pady=5)

auto_rotate_var = tk.BooleanVar(value=config.get("auto_rotate", False))
borg_ai_var = tk.BooleanVar(value=config.get("borg_ai", False))
startup_var = tk.BooleanVar(value=config.get("startup", True))

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
    text="Borg AI Mode (Autonomous)",
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

ui_queue = queue.Queue()

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
    root.after(200, process_ui_queue)

# ============================================================
# INITIALIZATION
# ============================================================

sync_startup_from_config()
refresh()
load_logs()

# Start background threads
threading.Thread(target=auto_rotate_thread, daemon=True).start()
threading.Thread(target=borg_ai_thread, daemon=True).start()
threading.Thread(target=registry_monitor_thread, daemon=True).start()
threading.Thread(target=auto_refresh_logs_thread, daemon=True).start()

process_ui_queue()
root.mainloop()
