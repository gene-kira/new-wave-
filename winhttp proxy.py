# ============================================================
# Proxy Controller
# - Auto-elevation (UAC)
# - Auto dependency installer
# - WinINET proxy toggle
# - WinHTTP proxy toggle
# ============================================================

import os
import sys
import ctypes
import subprocess

# ------------------------------------------------------------
# AUTO-ELEVATION
# ------------------------------------------------------------

def ensure_admin():
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            script = os.path.abspath(sys.argv[0])
            params = " ".join(f'"{arg}"' for arg in sys.argv[1:])

            ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                sys.executable,
                f'"{script}" {params}',
                None,
                1
            )
            sys.exit()

    except Exception as e:
        print(f"Elevation failed: {e}")
        sys.exit()

ensure_admin()

# ------------------------------------------------------------
# AUTO INSTALL DEPENDENCIES
# ------------------------------------------------------------

REQUIRED_PACKAGES = {
    "pywin32": "win32api",
    "customtkinter": "customtkinter"
}

for package, import_name in REQUIRED_PACKAGES.items():
    try:
        __import__(import_name)
    except ImportError:
        print(f"Installing {package}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package]
        )

# ------------------------------------------------------------
# STANDARD IMPORTS
# ------------------------------------------------------------

import tkinter as tk
import winreg

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

PROXY = "127.0.0.1:8888"

# ------------------------------------------------------------
# PROXY FUNCTIONS
# ------------------------------------------------------------

def set_proxy(enable):
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_SET_VALUE,
        )

        if enable:

            winreg.SetValueEx(
                key,
                "ProxyEnable",
                0,
                winreg.REG_DWORD,
                1
            )

            winreg.SetValueEx(
                key,
                "ProxyServer",
                0,
                winreg.REG_SZ,
                PROXY
            )

            subprocess.run(
                ["netsh", "winhttp", "set", "proxy", PROXY],
                capture_output=True,
                shell=True
            )

            status_var.set(f"Proxy: ON ({PROXY})")

        else:

            winreg.SetValueEx(
                key,
                "ProxyEnable",
                0,
                winreg.REG_DWORD,
                0
            )

            subprocess.run(
                ["netsh", "winhttp", "reset", "proxy"],
                capture_output=True,
                shell=True
            )

            status_var.set("Proxy: OFF")

        winreg.CloseKey(key)

    except Exception as e:
        status_var.set(f"Error: {e}")

# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------

root = tk.Tk()
root.title("Proxy Controller")
root.geometry("350x180")
root.resizable(False, False)

status_var = tk.StringVar(value="Proxy: OFF")

title = tk.Label(
    root,
    text="Proxy Controller",
    font=("Segoe UI", 14, "bold")
)
title.pack(pady=10)

status = tk.Label(
    root,
    textvariable=status_var,
    font=("Segoe UI", 10)
)
status.pack(pady=5)

on_btn = tk.Button(
    root,
    text="Enable Proxy",
    width=20,
    bg="green",
    fg="white",
    command=lambda: set_proxy(True)
)
on_btn.pack(pady=5)

off_btn = tk.Button(
    root,
    text="Disable Proxy",
    width=20,
    bg="red",
    fg="white",
    command=lambda: set_proxy(False)
)
off_btn.pack(pady=5)

root.mainloop()