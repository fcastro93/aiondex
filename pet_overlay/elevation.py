"""AionDex as administrator, without Windows asking every time.

The game runs as administrator, and Windows only lets a program click on it (the build macro) or see the keys you
press in it (the minimap route moving with you) when that program is too. Once you choose it ("Reiniciar como
administrador" or "Abrir siempre como administrador"), settings["run_as_admin"] stays on, and a Windows scheduled
task (no trigger, "run with highest privileges", in your session) opens AionDex elevated: created once (Windows asks
then), afterwards any start - yours, a restart, the one after an update - goes through it with no prompt. Without the
task, Windows' usual prompt (runas) does it."""
import ctypes
import subprocess
import sys
import tempfile
from pathlib import Path

TASK = r"\AionDex\AionDex como administrador"
NO_WINDOW = 0x08000000


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def command(extra=()):
    """(program, arguments) that start this AionDex again."""
    if getattr(sys, "frozen", False):
        return sys.executable, list(extra)
    return sys.executable, [str(Path(sys.argv[0]).resolve())] + list(extra)


def _quote(arg):
    return f'"{arg}"' if " " in arg and not arg.startswith('"') else arg


def _schtasks(*args):
    try:
        done = subprocess.run(["schtasks", *args], capture_output=True, text=True, creationflags=NO_WINDOW,
                              timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def task_exists():
    return _schtasks("/query", "/tn", TASK)


def create_task():
    """Create (or update) the task; it must be done from an elevated AionDex. -> True when it is there."""
    program, args = command(["--restarted"])
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Abre AionDex como administrador sin preguntar (lo crea AionDex).</Description>
  </RegistrationInfo>
  <Triggers />
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel>
  </Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author"><Exec>
    <Command>{_xml(program)}</Command>
    <Arguments>{_xml(" ".join(_quote(a) for a in args))}</Arguments>
    <WorkingDirectory>{_xml(str(Path(program).parent))}</WorkingDirectory>
  </Exec></Actions>
</Task>
"""
    path = Path(tempfile.gettempdir()) / "aiondex_admin_task.xml"
    path.write_text(xml, encoding="utf-16")
    try:
        return _schtasks("/create", "/tn", TASK, "/xml", str(path), "/f")
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def delete_task():
    return _schtasks("/delete", "/tn", TASK, "/f")


def relaunch_elevated(extra=("--restarted",)):
    """Start an elevated AionDex: through the task (no prompt) or Windows' prompt. -> True when one was started."""
    if task_exists() and _schtasks("/run", "/tn", TASK):
        return True
    program, args = command(extra)
    params = " ".join(_quote(a) for a in args)
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", program, params, None, 1) > 32


def _xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
