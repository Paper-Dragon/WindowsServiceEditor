"""托盘监控代理的启停、状态与开机自启。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .elevate import _quote
from .paths import tray_pid_path

AUTOSTART_TASK = "WindowsServiceEditorMonitor"


def launch_command() -> tuple[str, list[str], str]:
    """返回 (executable, argv_without_exe, cwd)。"""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        cwd = str(Path(exe).parent)
        return exe, ["--tray"], cwd
    exe = sys.executable
    candidates = []
    if sys.argv and sys.argv[0] not in ("-c", "-m"):
        candidates.append(Path(sys.argv[0]).resolve())
    candidates.append(Path(__file__).resolve().parent.parent / "main.py")
    script = next((str(p) for p in candidates if p.exists() and p.suffix.lower() in (".py", ".pyw")), None)
    if not script:
        raise RuntimeError("找不到 main.py 入口，无法启动托盘代理")
    cwd = str(Path(script).parent)
    return exe, [script, "--tray"], cwd


def launch_command_line() -> str:
    exe, args, _ = launch_command()
    return " ".join([_quote(exe), *(_quote(a) for a in args)])


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) == 0:
                return False
            return int(exit_code.value) == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


def read_tray_pid() -> int | None:
    path = tray_pid_path()
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return None
    if not _pid_alive(pid):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return pid


def write_tray_pid(pid: int) -> None:
    tray_pid_path().write_text(str(pid), encoding="utf-8")


def clear_tray_pid(expected: int | None = None) -> None:
    path = tray_pid_path()
    if not path.exists():
        return
    try:
        current = int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        current = -1
    if expected is None or current == expected:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def agent_status() -> dict[str, Any]:
    pid = read_tray_pid()
    return {
        "running": pid is not None,
        "pid": pid,
        "autostart": is_autostart_enabled(),
        "command": launch_command_line(),
    }


def start_tray_agent() -> dict[str, Any]:
    existing = read_tray_pid()
    if existing is not None:
        return agent_status()

    exe, args, cwd = launch_command()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    proc = subprocess.Popen(
        [exe, *args],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    # 等待子进程写入 pid 文件
    for _ in range(40):
        time.sleep(0.05)
        pid = read_tray_pid()
        if pid is not None:
            break
    else:
        # 回退记录 Popen pid（托盘进程可能稍晚写入）
        if proc.poll() is None:
            write_tray_pid(proc.pid)
    return agent_status()


def stop_tray_agent() -> dict[str, Any]:
    pid = read_tray_pid()
    if pid is None:
        return agent_status()
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            try:
                ctypes.windll.kernel32.TerminateProcess(handle, 0)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    clear_tray_pid(pid)
    time.sleep(0.15)
    return agent_status()


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return subprocess.run(
        ["schtasks.exe", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=creationflags,
    )


def is_autostart_enabled() -> bool:
    completed = _run_schtasks(["/Query", "/TN", AUTOSTART_TASK])
    return completed.returncode == 0


def set_autostart(enabled: bool) -> dict[str, Any]:
    if enabled:
        tr = launch_command_line()
        # 登录时以最高权限运行，避免每次 UAC
        completed = _run_schtasks(
            [
                "/Create",
                "/TN",
                AUTOSTART_TASK,
                "/TR",
                tr,
                "/SC",
                "ONLOGON",
                "/RL",
                "HIGHEST",
                "/F",
            ]
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or "创建开机启动任务失败")
    else:
        completed = _run_schtasks(["/Delete", "/TN", AUTOSTART_TASK, "/F"])
        if completed.returncode != 0 and "ERROR: The system cannot find" not in (
            completed.stderr or ""
        ):
            # 中文系统可能是「找不到」——只要任务已不存在即可
            if is_autostart_enabled():
                detail = (completed.stderr or completed.stdout or "").strip()
                raise RuntimeError(detail or "删除开机启动任务失败")
    return agent_status()
