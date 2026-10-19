"""Windows desktop integration: run without a console window, start with Windows, desktop
and Start menu icons, and a hidden launcher for RuneLite with the plugin loaded.

Standard library only. The app itself never touches the game; launching RuneLite here is the
same as double-clicking run-plugin.bat, minus the black window.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

from . import config

ROOT = config.ROOT
RUN_PY = os.path.join(ROOT, "run.py")
ICON = os.path.join(ROOT, "web", "bankstanding.ico")
PLUGIN_DIR = os.path.join(ROOT, "runelite-plugin")
LOG = os.path.join(config.DATA_DIR, "bankstanding.log")
RL_LOG = os.path.join(config.DATA_DIR, "runelite.log")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Bankstanding"
SHORTCUTS = [("Bankstanding", "--open", "Open the Bankstanding dashboard"),
             ("Bankstanding RuneLite", "--runelite", "RuneLite with the Bankstanding plugin"),
             ("Restart Bankstanding", "--restart", "Restart Bankstanding (after an update)")]
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def pythonw():
    """The windowless Python next to this one (pythonw.exe), else this interpreter."""
    exe = sys.executable or "python"
    if IS_WIN:
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            return w
    return exe


def message(text, error=False):
    """A plain message box on Windows (there is no console to print to), else stderr."""
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, NAME, 0x10 if error else 0x40)
            return
        except Exception:  # noqa: BLE001
            pass
    sys.stderr.write(text + "\n")


def running(port, timeout=1.5):
    """True if a Bankstanding server answers on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "bankstanding"
    except Exception:  # noqa: BLE001
        return False


def code_stamp():
    """A fingerprint of the app's code on disk: the newest change to run.py or geco/*.py. A running
    server reports the one it started with, so an update made while it runs can be spotted."""
    newest = 0.0
    files = [RUN_PY] + [os.path.join(ROOT, "geco", f) for f in os.listdir(os.path.join(ROOT, "geco")) if f.endswith(".py")]
    for f in files:
        try:
            newest = max(newest, os.path.getmtime(f))
        except OSError:
            pass
    return int(newest)


STARTED_STAMP = code_stamp()


def server_status(port, timeout=1.5):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=timeout) as r:
            st = json.loads(r.read().decode("utf-8"))
            return st if st.get("app") == "bankstanding" else None
    except Exception:  # noqa: BLE001
        return None


def stale(port):
    """True when a Bankstanding server is running older code than what is on disk now (servers
    from before this check report no stamp at all)."""
    st = server_status(port)
    return bool(st) and (st.get("code") or {}).get("started") != code_stamp()


def _listening_pids(port):
    """Process IDs listening on 127.0.0.1:port (or any address), from netstat."""
    try:
        if IS_WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20,
                                 creationflags=NO_WINDOW).stdout
            pids = set()
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                    pids.add(int(parts[4]))
            return pids
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=20).stdout
        return {int(x) for x in out.split() if x.isdigit()}
    except Exception:  # noqa: BLE001
        return set()


def stop_server(port, wait=30):
    """Stops the Bankstanding server on this port: asks it to quit, else ends its process. Only a
    server that answers as Bankstanding is stopped. True once the port is free."""
    if not server_status(port):
        return True
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:  # noqa: BLE001 - a server older than /api/quit
        pass
    end = time.time() + 8
    while time.time() < end and server_status(port, timeout=1):
        time.sleep(0.5)
    if server_status(port, timeout=1):
        for pid in _listening_pids(port):
            if pid == os.getpid():
                continue
            try:
                if IS_WIN:
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=20,
                                   creationflags=NO_WINDOW)
                else:
                    os.kill(pid, 15)
            except Exception:  # noqa: BLE001
                pass
    end = time.time() + wait
    while time.time() < end:
        if not running(port, timeout=1):
            return True
        time.sleep(0.5)
    return False


def restart(port, open_browser=True):
    """Stops whatever Bankstanding is running and starts the code on disk in the background."""
    if not stop_server(port):
        message(f"Could not stop the Bankstanding already running on port {port}. Restart your PC, "
                "or end pythonw.exe in Task Manager, then try again.", error=True)
        return False
    time.sleep(1)
    if not ensure_running(port):
        message(f"Bankstanding did not start within two minutes.\nThe log is in {LOG}", error=True)
        return False
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}")
    return True


def _spawn(args, cwd=ROOT, log=None):
    out = open(log, "ab") if log else subprocess.DEVNULL
    kw = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": out, "stderr": subprocess.STDOUT if log else subprocess.DEVNULL}
    if IS_WIN:
        kw["creationflags"] = NO_WINDOW | DETACHED
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(args, **kw)


def ensure_running(port, wait=120):
    """Starts the server in the background if it is not already up (or restarts it when it runs
    older code than is on disk, after an update); True once it answers."""
    if running(port):
        if not stale(port) or not stop_server(port):
            return True
    _spawn([pythonw(), RUN_PY, "--background", "--port", str(port)])
    end = time.time() + wait
    while time.time() < end:
        time.sleep(1)
        if running(port):
            return True
    return False


def open_dashboard(port):
    if not ensure_running(port):
        message(f"Bankstanding did not start within two minutes.\nThe log is in {LOG}", error=True)
        return False
    webbrowser.open(f"http://127.0.0.1:{port}")
    return True


def launch_runelite(port):
    """Starts the server if needed, then RuneLite with the plugin (gradlew run), hidden.
    Waits for RuneLite to close so a failed start can be reported."""
    ensure_running(port, wait=60)
    gradlew = os.path.join(PLUGIN_DIR, "gradlew.bat" if IS_WIN else "gradlew")
    if not os.path.exists(gradlew):
        message(f"The RuneLite plugin folder is missing: {PLUGIN_DIR}", error=True)
        return 1
    try:
        if os.path.exists(RL_LOG) and os.path.getsize(RL_LOG) > 2_000_000:
            os.remove(RL_LOG)
    except OSError:
        pass
    with open(RL_LOG, "ab") as f:
        f.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} launching RuneLite ====\n".encode())
    start = time.time()
    try:
        proc = _spawn([gradlew, "--no-daemon", "--quiet", "run"], cwd=PLUGIN_DIR, log=RL_LOG)
    except OSError as e:
        message(f"Could not start RuneLite: {e}", error=True)
        return 1
    rc = proc.wait()
    if rc != 0:
        tail = ""
        try:
            with open(RL_LOG, "rb") as f:
                tail = f.read()[-1500:].decode("utf-8", "replace")
        except OSError:
            pass
        hint = ("Java 17 or newer is needed (Adoptium Temurin is free)."
                if "JAVA_HOME" in tail or "is not recognized" in tail else "")
        message(f"RuneLite closed with an error after {int(time.time() - start)}s. {hint}\n\n"
                f"Last lines of {RL_LOG}:\n\n{tail[-700:]}", error=True)
    return rc


# Start with Windows ---------------------------------------------------------------------
def autostart_command():
    return f'"{pythonw()}" "{RUN_PY}" --background'


def autostart_enabled():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    if not IS_WIN:
        raise OSError("Start with Windows is only available on Windows")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except OSError:
                pass


# Shortcuts (.lnk files through PowerShell's WScript.Shell; values pass as environment
# variables so no path needs quoting inside the script) ------------------------------------
_PS_MAKE = r"""
$sh = New-Object -ComObject WScript.Shell
foreach ($place in 'Desktop', 'Programs') {
  $dir = [Environment]::GetFolderPath($place)
  if (-not $dir) { continue }
  $lnk = $sh.CreateShortcut((Join-Path $dir ($env:BS_NAME + '.lnk')))
  $lnk.TargetPath = $env:BS_TARGET
  $lnk.Arguments = $env:BS_ARGS
  $lnk.WorkingDirectory = $env:BS_DIR
  $lnk.IconLocation = $env:BS_ICON
  $lnk.Description = $env:BS_DESC
  $lnk.Save()
}
"""
_PS_REMOVE = r"""
foreach ($place in 'Desktop', 'Programs') {
  $dir = [Environment]::GetFolderPath($place)
  if ($dir) { Remove-Item -LiteralPath (Join-Path $dir ($env:BS_NAME + '.lnk')) -ErrorAction SilentlyContinue }
}
"""
_PS_LIST = r"""
$found = @()
foreach ($place in 'Desktop', 'Programs') {
  $dir = [Environment]::GetFolderPath($place)
  if ($dir -and (Test-Path -LiteralPath (Join-Path $dir ($env:BS_NAME + '.lnk')))) { $found += $place }
}
$found -join ','
"""


def _ps(script, env):
    e = dict(os.environ, **env)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                       env=e, capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW if IS_WIN else 0)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or "PowerShell failed").strip()[:300])
    return r.stdout.strip()


def make_shortcuts():
    for name, arg, desc in SHORTCUTS:
        _ps(_PS_MAKE, {"BS_NAME": name, "BS_TARGET": pythonw(), "BS_ARGS": f'"{RUN_PY}" {arg}',
                       "BS_DIR": ROOT, "BS_ICON": ICON + ",0", "BS_DESC": desc})


def remove_shortcuts():
    for name, _, _ in SHORTCUTS:
        _ps(_PS_REMOVE, {"BS_NAME": name})


def shortcuts_present():
    if not IS_WIN:
        return False
    try:
        return bool(_ps(_PS_LIST, {"BS_NAME": SHORTCUTS[0][0]}))
    except Exception:  # noqa: BLE001
        return False


def status():
    return {"windows": IS_WIN, "autostart": autostart_enabled(), "shortcuts": shortcuts_present(),
            "background": bool(os.environ.get("BANKSTANDING_BACKGROUND")), "log": LOG}


def install(port):
    """One time setup: start with Windows, icons on the desktop and in the Start menu, then
    start the app hidden and open the dashboard."""
    if not IS_WIN:
        message("Install sets up Windows icons and start up; on this system run: python run.py")
        return 1
    try:
        set_autostart(True)
        make_shortcuts()
    except Exception as e:  # noqa: BLE001
        message(f"Setup did not finish: {e}", error=True)
        return 1
    ok = open_dashboard(port)
    message("Bankstanding is set up.\n\n"
            "It now starts quietly when you sign in to Windows (no black window).\n"
            "Desktop and Start menu icons:\n"
            "  Bankstanding: opens the dashboard in your browser\n"
            "  Bankstanding RuneLite: opens RuneLite with the plugin\n\n"
            "To undo, run uninstall.bat or turn it off in Settings.")
    return 0 if ok else 1


def uninstall():
    if not IS_WIN:
        return 0
    errors = []
    for fn in (lambda: set_autostart(False), remove_shortcuts):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    message("Bankstanding no longer starts with Windows and its icons are removed. Your data is kept in the data folder."
            + ("\n\n" + "\n".join(errors) if errors else ""), error=bool(errors))
    return 0
