"""应用数据目录。"""

from __future__ import annotations

import os
from pathlib import Path


def app_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    path = Path(root) / "WindowsServiceEditor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def program_data_dir() -> Path:
    root = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    path = Path(root) / "WindowsServiceEditor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def monitor_config_path() -> Path:
    return app_data_dir() / "monitor.json"


def monitor_events_path() -> Path:
    return app_data_dir() / "monitor_events.json"


def tray_pid_path() -> Path:
    return app_data_dir() / "tray.pid"


def wrapper_log_path(service_name: str) -> Path:
    logs = program_data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in service_name)
    return logs / f"{safe}.log"
