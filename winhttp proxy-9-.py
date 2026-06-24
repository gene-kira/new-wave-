import os
import sys
import json
import ctypes
import tkinter as tk
import winreg
import tempfile


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

    except Exception as e:

        print("Admin error:", e)
        sys.exit()


ensure_admin()



# ============================================================
# CONFIG FILE
# ============================================================

CONFIG_FILE = "config.json"


DEFAULT_CONFIG = {

    "proxy": "127.0.0.1:8888",

    "teams_bypass": [

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

        try:

            with open(
                CONFIG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                return json.load(f)

        except:

            pass


    save_config(DEFAULT_CONFIG)

    return DEFAULT_CONFIG



def save_config(data):

    with open(
        CONFIG_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4
        )



config = load_config()



# ============================================================
# SETTINGS
# ============================================================

PROXY = config["proxy"]


PAC_FILE = os.path.join(
    tempfile.gettempdir(),
    "proxy_controller.pac"
)



# ============================================================
# PAC GENERATOR
# ============================================================

def create_pac():

    rules = ""


    for domain in config["teams_bypass"]:

        rules += (
            f'shExpMatch(host,"{domain}") ||\n'
        )


    pac = f"""

function FindProxyForURL(url, host)
{{

    if (

        {rules}

        shExpMatch(host,"localhost")

    )

    {{

        return "DIRECT";

    }}


    return "PROXY {PROXY}";

}}

"""


    with open(
        PAC_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(pac)



create_pac()



# ============================================================
# PROXY CONTROL
# ============================================================

def set_proxy(enabled):

    try:


        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_SET_VALUE
        )



        if enabled:


            winreg.SetValueEx(
                key,
                "AutoConfigURL",
                0,
                winreg.REG_SZ,
                "file://" + PAC_FILE
            )


            winreg.SetValueEx(
                key,
                "ProxyEnable",
                0,
                winreg.REG_DWORD,
                0
            )


            os.system(
                "netsh winhttp reset proxy"
            )


            status.set(
                "Proxy ON\nTeams DIRECT"
            )



        else:


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


            os.system(
                "netsh winhttp reset proxy"
            )


            status.set(
                "Proxy OFF"
            )



        winreg.CloseKey(key)



    except Exception as e:


        status.set(
            "ERROR\n" + str(e)
        )




# ============================================================
# GUI
# ============================================================

root = tk.Tk()

root.title(
    "Proxy Controller"
)

root.geometry(
    "400x260"
)

root.resizable(
    False,
    False
)



tk.Label(
    root,
    text="Proxy Controller",
    font=("Segoe UI",16,"bold")
).pack(
    pady=15
)



status = tk.StringVar(
    value="Loaded config.json"
)



tk.Label(
    root,
    textvariable=status
).pack(
    pady=10
)



tk.Button(
    root,
    text="ENABLE PROXY",
    width=25,
    bg="green",
    fg="white",
    command=lambda:set_proxy(True)
).pack(
    pady=5
)



tk.Button(
    root,
    text="DISABLE PROXY",
    width=25,
    bg="red",
    fg="white",
    command=lambda:set_proxy(False)
).pack(
    pady=5
)



def save_now():

    save_config(config)

    status.set(
        "config.json saved"
    )



tk.Button(
    root,
    text="SAVE SETTINGS",
    width=25,
    command=save_now
).pack(
    pady=5
)



root.mainloop()