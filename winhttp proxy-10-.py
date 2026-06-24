import os
import sys
import json
import ctypes
import time
import socket
import logging
import subprocess
import tkinter as tk
from tkinter import messagebox
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
# FILES
# ============================================================

BASE = os.path.dirname(
    os.path.abspath(__file__)
)


CONFIG_FILE = os.path.join(
    BASE,
    "config.json"
)


BACKUP_FILE = os.path.join(
    BASE,
    "backup.json"
)


PAC_FILE = os.path.join(
    BASE,
    "proxy.pac"
)


LOG_FILE = os.path.join(
    BASE,
    "proxy_controller.log"
)



logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(message)s"
)



# ============================================================
# DEFAULT CONFIG
# ============================================================

DEFAULT = {

    "proxy": "127.0.0.1:8888",

    "startup": False,

    "teams_bypass":[

        "*.teams.microsoft.com",
        "*.teams.live.com",
        "*.cloud.microsoft",
        "*.skype.com",
        "*.lync.com",
        "*.microsoftonline.com",
        "*.microsoft.com",
        "*.office.com",
        "*.office365.com"

    ]

}



def load_config():

    if os.path.exists(CONFIG_FILE):

        with open(CONFIG_FILE,"r") as f:
            return json.load(f)

    save_config(DEFAULT)

    return DEFAULT



def save_config(data):

    with open(CONFIG_FILE,"w") as f:
        json.dump(
            data,
            f,
            indent=4
        )



config = load_config()



# ============================================================
# BACKUP ORIGINAL PROXY
# ============================================================

def backup_proxy():

    try:

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        )

        data={}

        for x in [
            "ProxyEnable",
            "ProxyServer",
            "AutoConfigURL"
        ]:

            try:

                data[x]=winreg.QueryValueEx(
                    key,x
                )[0]

            except:

                pass


        with open(BACKUP_FILE,"w") as f:
            json.dump(data,f,indent=4)


        winreg.CloseKey(key)


    except Exception as e:

        logging.error(e)



# ============================================================
# PAC CREATOR
# ============================================================

def create_pac():

    rules=""

    for domain in config["teams_bypass"]:

        rules += (
            f'shExpMatch(host,"{domain}") ||'
        )


    pac=f"""

function FindProxyForURL(url,host)
{{

if(
{rules}
shExpMatch(host,"localhost")
)

{{
return "DIRECT";
}}

return "PROXY {config["proxy"]}";

}}

"""


    with open(PAC_FILE,"w") as f:

        f.write(pac)



# ============================================================
# ENABLE PROXY
# ============================================================

def enable_proxy():

    backup_proxy()

    create_pac()


    key=winreg.OpenKey(
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
        "file:///"+PAC_FILE
    )


    winreg.SetValueEx(
        key,
        "ProxyEnable",
        0,
        winreg.REG_DWORD,
        0
    )


    winreg.CloseKey(key)


    subprocess.run(
        "netsh winhttp reset proxy",
        shell=True
    )


    logging.info(
        "Proxy enabled"
    )


    status.set(
        "PROXY ON\nTeams DIRECT"
    )



# ============================================================
# DISABLE PROXY
# ============================================================

def disable_proxy():

    key=winreg.OpenKey(
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


    logging.info(
        "Proxy disabled"
    )


    status.set(
        "PROXY OFF"
    )



# ============================================================
# TEST
# ============================================================

def test_connection():

    try:

        socket.create_connection(
            ("google.com",80),
            5
        )


        messagebox.showinfo(
            "Test",
            "Internet connection OK"
        )


    except:

        messagebox.showerror(
            "Test",
            "Connection failed"
        )



# ============================================================
# SAVE SETTINGS
# ============================================================

def save():

    config["proxy"]=proxy_entry.get()

    save_config(config)

    create_pac()

    status.set(
        "Settings saved"
    )



# ============================================================
# GUI
# ============================================================

root=tk.Tk()

root.title(
    "Proxy Controller"
)

root.geometry(
    "500x420"
)

root.configure(
    bg="#1e1e1e"
)


font=("Segoe UI",11)


tk.Label(
    root,
    text="Proxy Controller",
    fg="white",
    bg="#1e1e1e",
    font=("Segoe UI",18,"bold")
).pack(pady=15)



proxy_entry=tk.Entry(
    root,
    width=35
)

proxy_entry.insert(
    0,
    config["proxy"]
)

proxy_entry.pack()



status=tk.StringVar(
    value="Ready"
)



tk.Label(
    root,
    textvariable=status,
    fg="cyan",
    bg="#1e1e1e"
).pack(pady=15)



tk.Button(
    root,
    text="ENABLE PROXY",
    width=25,
    command=enable_proxy
).pack(pady=5)



tk.Button(
    root,
    text="DISABLE PROXY",
    width=25,
    command=disable_proxy
).pack(pady=5)



tk.Button(
    root,
    text="TEST CONNECTION",
    width=25,
    command=test_connection
).pack(pady=5)



tk.Button(
    root,
    text="SAVE SETTINGS",
    width=25,
    command=save
).pack(pady=5)



root.mainloop()