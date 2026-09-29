"""启动时检测并请求管理员权限（UAC）。"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _quote(arg: str) -> str:
    if not arg:
        return '""'
    if any(ch in arg for ch in ' \t"'):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def _launch_params() -> tuple[str, str, str]:
    """Return (executable, parameters, working_directory)."""
    cwd = str(Path.cwd())
    if getattr(sys, "frozen", False):
        # Packaged exe: relaunch itself with original argv (skip argv[0])
        exe = sys.executable
        params = " ".join(_quote(a) for a in sys.argv[1:])
        return exe, params, str(Path(sys.executable).parent)

    # Dev: python.exe + script path + args
    exe = sys.executable
    script = str(Path(sys.argv[0]).resolve())
    rest = " ".join(_quote(a) for a in sys.argv[1:])
    params = _quote(script) + (f" {rest}" if rest else "")
    return exe, params, cwd


def elevate_or_exit() -> None:
    """
    若当前非管理员：弹出 UAC，启动提权进程后退出当前进程。
    用户取消 UAC 时提示并退出。
    已是管理员则直接返回。
    """
    if is_admin():
        return

    exe, params, cwd = _launch_params()
    # SEE_MASK not needed; >32 means success
    rc = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        exe,
        params,
        cwd,
        1,  # SW_SHOWNORMAL
    )
    if rc <= 32:
        ctypes.windll.user32.MessageBoxW(
            None,
            "需要管理员权限才能管理 Windows 服务。\n请在 UAC 提示中选择「是」。",
            "Windows 服务管理器",
            0x10,  # MB_ICONERROR
        )
        raise SystemExit(1)
    raise SystemExit(0)
