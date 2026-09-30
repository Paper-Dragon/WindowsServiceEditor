"""服务状态监控与告警。"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

import win32service
import win32serviceutil

from . import notify
from .constants import STATE_MAP
from .paths import monitor_config_path, monitor_events_path

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "interval_sec": 5,
    "system_toast": True,
    "watched": [],
}

MAX_EVENTS = 200


def _normalize_watched(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return result
    for raw in items:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        result.append(
            {
                "name": name,
                "auto_restart": bool(raw.get("auto_restart", False)),
                "alert_on_stop": bool(raw.get("alert_on_stop", True)),
                "alert_on_start": bool(raw.get("alert_on_start", False)),
            }
        )
    return result


def normalize_config(raw: Any) -> dict[str, Any]:
    data = deepcopy(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        data["enabled"] = bool(raw.get("enabled", False))
        try:
            interval = int(raw.get("interval_sec", 5) or 5)
        except (TypeError, ValueError):
            interval = 5
        data["interval_sec"] = max(2, min(interval, 300))
        data["system_toast"] = bool(raw.get("system_toast", True))
        data["watched"] = _normalize_watched(raw.get("watched"))
    return data


def load_config() -> dict[str, Any]:
    path = monitor_config_path()
    if not path.exists():
        return normalize_config(None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return normalize_config(None)
    return normalize_config(raw)


def save_config(payload: Any) -> dict[str, Any]:
    config = normalize_config(payload)
    path = monitor_config_path()
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def load_events(limit: int = MAX_EVENTS) -> list[dict[str, Any]]:
    path = monitor_events_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    items = [item for item in raw if isinstance(item, dict)]
    limit = max(1, min(int(limit or MAX_EVENTS), MAX_EVENTS))
    return list(reversed(items[-limit:]))


def _persist_events(events: deque[dict[str, Any]]) -> None:
    path = monitor_events_path()
    try:
        path.write_text(
            json.dumps(list(events), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def query_state_code(name: str) -> int | None:
    try:
        status = win32serviceutil.QueryServiceStatus(name)
        return int(status[1])
    except Exception:
        return None


def state_label(code: int | None) -> str:
    if code is None:
        return "未知"
    return STATE_MAP.get(code, f"状态{code}")


@dataclass
class MonitorEngine:
    """后台轮询引擎（GUI 或托盘代理共用）。"""

    on_event: Callable[[dict[str, Any]], None] | None = None
    push_ui: bool = True
    reload_config: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _config: dict[str, Any] = field(default_factory=load_config, repr=False)
    _last_states: dict[str, int | None] = field(default_factory=dict, repr=False)
    _events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_EVENTS), repr=False
    )
    _config_mtime: float = field(default=0.0, repr=False)
    _paused: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        for event in reversed(load_events(MAX_EVENTS)):
            self._events.appendleft(event)
        try:
            self._config_mtime = monitor_config_path().stat().st_mtime
        except OSError:
            self._config_mtime = 0.0

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = bool(paused)

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="svc-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._config)

    def update_config(self, payload: Any) -> dict[str, Any]:
        config = save_config(payload)
        with self._lock:
            self._config = config
            known = {item["name"] for item in config["watched"]}
            self._last_states = {k: v for k, v in self._last_states.items() if k in known}
            try:
                self._config_mtime = monitor_config_path().stat().st_mtime
            except OSError:
                pass
        return deepcopy(config)

    def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        # 磁盘为跨进程真源（GUI / 托盘代理均可写入）
        return load_events(limit)

    def _maybe_reload_config(self) -> None:
        if not self.reload_config:
            return
        path = monitor_config_path()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime <= self._config_mtime:
            return
        config = load_config()
        with self._lock:
            self._config = config
            self._config_mtime = mtime
            known = {item["name"] for item in config["watched"]}
            self._last_states = {k: v for k, v in self._last_states.items() if k in known}
        self._snapshot_baseline()

    def _emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)
            system_toast = bool(self._config.get("system_toast", True))
            _persist_events(self._events)
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass
        if self.push_ui:
            notify.push_ui_alert(
                event.get("title", "服务告警"),
                event.get("message", ""),
                event.get("level", "warn"),
            )
        if system_toast:
            notify.show_toast(event.get("title", "服务告警"), event.get("message", ""))

    def _loop(self) -> None:
        self._snapshot_baseline()
        while not self._stop.is_set():
            self._maybe_reload_config()
            with self._lock:
                enabled = bool(self._config.get("enabled")) and not self._paused
                interval = int(self._config.get("interval_sec") or 5)
                watched = list(self._config.get("watched") or [])
            if enabled and watched:
                for item in watched:
                    if self._stop.is_set():
                        break
                    self._check_one(item)
            self._stop.wait(interval)

    def _snapshot_baseline(self) -> None:
        with self._lock:
            watched = list(self._config.get("watched") or [])
        for item in watched:
            name = item["name"]
            self._last_states[name] = query_state_code(name)

    def _check_one(self, item: dict[str, Any]) -> None:
        name = item["name"]
        current = query_state_code(name)
        previous = self._last_states.get(name, current)
        self._last_states[name] = current

        if previous is None and current is None:
            return
        if previous == current:
            return

        prev_label = state_label(previous)
        cur_label = state_label(current)

        if current == win32service.SERVICE_STOPPED and item.get("alert_on_stop", True):
            event = {
                "ts": time.time(),
                "name": name,
                "kind": "stopped",
                "level": "error",
                "title": f"服务已停止 · {name}",
                "message": f"{name} 状态：{prev_label} → {cur_label}",
                "previous": prev_label,
                "current": cur_label,
            }
            self._emit(event)
            if item.get("auto_restart"):
                self._try_restart(name)
        elif current == win32service.SERVICE_RUNNING and item.get("alert_on_start", False):
            event = {
                "ts": time.time(),
                "name": name,
                "kind": "started",
                "level": "success",
                "title": f"服务已启动 · {name}",
                "message": f"{name} 状态：{prev_label} → {cur_label}",
                "previous": prev_label,
                "current": cur_label,
            }
            self._emit(event)
        elif current is None:
            event = {
                "ts": time.time(),
                "name": name,
                "kind": "missing",
                "level": "warn",
                "title": f"服务不可用 · {name}",
                "message": f"无法查询 {name} 的状态（可能已删除）",
                "previous": prev_label,
                "current": cur_label,
            }
            self._emit(event)

    def _try_restart(self, name: str) -> None:
        try:
            win32serviceutil.StartService(name)
            event = {
                "ts": time.time(),
                "name": name,
                "kind": "auto_restart",
                "level": "info",
                "title": f"已尝试自动拉起 · {name}",
                "message": f"监控检测到停止，已对 {name} 发出启动命令",
                "previous": "已停止",
                "current": "启动中",
            }
            self._emit(event)
        except Exception as exc:
            event = {
                "ts": time.time(),
                "name": name,
                "kind": "auto_restart_failed",
                "level": "error",
                "title": f"自动拉起失败 · {name}",
                "message": str(exc),
                "previous": "已停止",
                "current": "已停止",
            }
            self._emit(event)
