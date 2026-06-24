import os
import sys
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

        print(e)
        sys.exit()



ensure_admin()



# ============================================================
# SETTINGS
# ============================================================

PROXY = "127.0.0.1:8888"

PAC_FILE = os.path.join(
    tempfile.gettempdir(),
    "teams_proxy.pac"
)



# ============================================================
# CREATE PAC FILE
# ============================================================

PAC_DATA = r'''
function FindProxyForURL(url, host)
{

    if (
        shExpMatch(host,"*.teams.microsoft.com") ||
        shExpMatch(host,"*.teams.live.com") ||
        shExpMatch(host,"*.cloud.microsoft") ||
        shExpMatch(host,"*.skype.com") ||
        shExpMatch(host,"*.lync.com") ||
        shExpMatch(host,"*.microsoftonline.com") ||
        shExpMatch(host,"*.microsoft.com") ||
        shExpMatch(host,"*.office.com") ||
        shExpMatch(host,"*.office365.com")
    )
    {
        return "DIRECT";
    }


    return "PROXY 127.0.0.1:8888";
}
'''



def write_pac():

    with open(
        PAC_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(PAC_DATA)



write_pac()



# ============================================================
# PROXY CONTROL
# ============================================================

def set_proxy(enable):

    try:

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_SET_VALUE
        )


        if enable:


            # Enable PAC

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


            # Keep WinHTTP direct for Teams

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
    "360x220"
)


status = tk.StringVar(
    value="Ready"
)



tk.Label(
    root,
    text="Proxy Controller",
    font=("Segoe UI",16,"bold")
).pack(
    pady=15
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
    bg="green",
    fg="white",
    width=20,
    command=lambda:set_proxy(True)
).pack(
    pady=5
)



tk.Button(
    root,
    text="DISABLE PROXY",
    bg="red",
    fg="white",
    width=20,
    command=lambda:set_proxy(False)
).pack(
    pady=5
)



root.mainloop()