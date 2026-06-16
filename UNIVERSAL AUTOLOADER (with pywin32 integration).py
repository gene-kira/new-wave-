import sys, subprocess, importlib.util, os, time, tempfile

# ============================================================
#  UNIVERSAL AUTOLOADER (with pywin32 integration)
# ============================================================

REQUIRED_MODULES = {
    "pywin32": {
        "check": "win32api",
        "pip": "pywin32",
        "post": True
    },
    # Add more modules here:
    # "requests": {"check": "requests", "pip": "requests", "post": False},
    # "pillow":   {"check": "PIL",       "pip": "Pillow",   "post": False},
}

def run(cmd):
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True
        )
    except Exception as e:
        print("[Autoloader] ERROR:", e)
        return None


def install_module(name, spec):
    check_name = spec["check"]
    pip_name   = spec["pip"]
    needs_post = spec["post"]

    # Already installed?
    if importlib.util.find_spec(check_name) is not None:
        return True

    print(f"[Autoloader] Missing: {name} — installing...")

    # 1. Try normal pip install
    result = run(f'"{sys.executable}" -m pip install --upgrade {pip_name}')

    # 2. Repair pip if needed
    if result is None or result.returncode != 0:
        print("[Autoloader] pip failed — repairing...")
        run(f'"{sys.executable}" -m ensurepip --default-pip')
        run(f'"{sys.executable}" -m pip install --upgrade pip setuptools wheel')
        result = run(f'"{sys.executable}" -m pip install --upgrade {pip_name}')

    # 3. Fallback: direct wheel download (pywin32 only)
    if result.returncode != 0 and name == "pywin32":
        print("[Autoloader] fallback: downloading pywin32 wheel...")
        import urllib.request
        wheel_url = "https://github.com/mhammond/pywin32/releases/latest/download/pywin32-306-cp311-cp311-win_amd64.whl"
        tmp = os.path.join(tempfile.gettempdir(), "pywin32.whl")
        urllib.request.urlretrieve(wheel_url, tmp)
        result = run(f'"{sys.executable}" -m pip install "{tmp}"')

    # 4. Post-install (pywin32 registration)
    if needs_post:
        print("[Autoloader] running post-install...")
        run(f'"{sys.executable}" -m pywin32_postinstall -install')

    # 5. Verify
    if importlib.util.find_spec(check_name) is None:
        print(f"[Autoloader] FAILED: {name}")
        return False

    print(f"[Autoloader] Installed: {name}")
    return True


def autoload_all():
    for name, spec in REQUIRED_MODULES.items():
        if not install_module(name, spec):
            print(f"[Autoloader] Fatal: cannot continue without {name}")
            time.sleep(5)
            sys.exit(1)


# ============================================================
#  RUN AUTOLOADER BEFORE ANY IMPORTS
# ============================================================

autoload_all()

# Safe to import pywin32 now
import win32api, win32con, win32gui
