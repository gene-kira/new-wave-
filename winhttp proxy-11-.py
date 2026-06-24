import os
import sys
import json
import ctypes
import socket
import logging
import subprocess
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
# FILES
# ============================================================

BASE = os.path.dirname(
    os.path.abspath(__file__)
)

CONFIG = os.path.join(BASE,"config.json")

BACKUP = os.path.join(BASE,"backup.json")

PAC = os.path.join(BASE,"proxy.pac")

LOG = os.path.join(BASE,"proxy.log")



logging.basicConfig(
    filename=LOG,
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)



# ============================================================
# DEFAULT SETTINGS
# ============================================================

DEFAULT = {

    "proxy":
    "127.0.0.1:8888",

    "startup":
    False,

    "profiles":
    {

        "Teams": True,

        "Office365": True,

        "OneDrive": True

    },

    "bypass":

    [

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

    ]

}



def load_config():

    if os.path.exists(CONFIG):

        try:

            with open(CONFIG,"r") as f:

                return json.load(f)

        except:

            pass


    save_config(DEFAULT)

    return DEFAULT



def save_config(data):

    with open(CONFIG,"w") as f:

        json.dump(
            data,
            f,
            indent=4
        )



config = load_config()



# ============================================================
# PAC BUILDER
# ============================================================

def build_pac():

    rules=""

    for x in config["bypass"]:

        rules += (
            f'shExpMatch(host,"{x}") ||\n'
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


    with open(PAC,"w") as f:

        f.write(pac)



# ============================================================
# BACKUP
# ============================================================

def backup_settings():

    data={}

    key=winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    )


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


    with open(BACKUP,"w") as f:

        json.dump(
            data,
            f,
            indent=4
        )


    logging.info(
        "Backup created"
    )



# ============================================================
# ENABLE
# ============================================================

def enable_proxy():

    backup_settings()

    build_pac()


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
        "file:///"+PAC
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


    refresh()



# ============================================================
# DISABLE
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


    refresh()



# ============================================================
# RESTORE
# ============================================================

def restore():

    if not os.path.exists(BACKUP):

        messagebox.showerror(
            "Restore",
            "No backup found"
        )

        return


    with open(BACKUP) as f:

        data=json.load(f)



    key=winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        0,
        winreg.KEY_SET_VALUE
    )


    for k,v in data.items():

        winreg.SetValueEx(
            key,
            k,
            0,
            winreg.REG_SZ if isinstance(v,str)
            else winreg.REG_DWORD,
            v
        )


    winreg.CloseKey(key)


    logging.info(
        "Settings restored"
    )


    refresh()



# ============================================================
# TEST
# ============================================================

def test():

    try:

        socket.create_connection(
            ("google.com",80),
            5
        )


        messagebox.showinfo(
            "Network",
            "Internet OK"
        )


    except:

        messagebox.showerror(
            "Network",
            "Connection failed"
        )



# ============================================================
# STATUS
# ============================================================

def refresh():

    try:

        key=winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        )


        try:

            pac=winreg.QueryValueEx(
                key,
                "AutoConfigURL"
            )[0]

        except:

            pac="None"



        status.set(

            "Status\n\n"
            f"PAC: {pac}\n"
            f"Proxy: {config['proxy']}\n"
            "Teams: DIRECT"

        )


    except Exception as e:

        status.set(
            str(e)
        )



# ============================================================
# GUI
# ============================================================

root=tk.Tk()

root.title(
    "Proxy Controller Pro"
)

root.geometry(
    "600x520"
)

root.configure(
    bg="#202020"
)



tk.Label(
    root,
    text="Proxy Controller Pro",
    fg="white",
    bg="#202020",
    font=("Segoe UI",18,"bold")
).pack(
    pady=15
)



status=tk.StringVar()


tk.Label(
    root,
    textvariable=status,
    fg="cyan",
    bg="#202020",
    justify="left"
).pack()



tk.Button(
    root,
    text="ENABLE PROXY",
    width=30,
    command=enable_proxy
).pack(pady=5)



tk.Button(
    root,
    text="DISABLE PROXY",
    width=30,
    command=disable_proxy
).pack(pady=5)



tk.Button(
    root,
    text="RESTORE BACKUP",
    width=30,
    command=restore
).pack(pady=5)



tk.Button(
    root,
    text="TEST CONNECTION",
    width=30,
    command=test
).pack(pady=5)



logbox=scrolledtext.ScrolledText(
    root,
    height=10
)

logbox.pack(
    fill="both",
    expand=True,
    padx=10,
    pady=10
)



def load_logs():

    if os.path.exists(LOG):

        with open(LOG) as f:

            logbox.delete(
                "1.0",
                tk.END
            )

            logbox.insert(
                tk.END,
                f.read()
            )



tk.Button(
    root,
    text="REFRESH LOGS",
    command=load_logs
).pack()



refresh()


root.mainloop()