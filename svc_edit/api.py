"""pywebview JS API 桥接。"""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import webbrowser

import webview
import win32service

from . import agent_ctl
from . import monitor as mon
from . import services as svc
from . import updater
from . import wrapper as wrap
from .constants import START_TYPE_OPTIONS, TYPE_FILTERS

APP_VERSION = "0.9.0"


def _ok(data=None, message: str = "") -> dict:
    return {"ok": True, "data": data, "message": message}


def _err(message: str, code: str | None = None) -> dict:
    return {"ok": False, "message": message, "code": code}


class Api:
    """前端通过 window.pywebview.api 调用。"""

    def __init__(self) -> None:
        self._monitor: mon.MonitorEngine | None = None
        self._update_info: dict | None = None

    def attach_monitor(self, engine: mon.MonitorEngine) -> None:
        self._monitor = engine

    def get_meta(self) -> dict:
        return _ok(
            {
                "is_admin": svc.is_admin(),
                "version": APP_VERSION,
                "start_options": START_TYPE_OPTIONS,
                "type_filters": [
                    {"value": k, "label": v} for k, v in TYPE_FILTERS.items()
                ],
                "features": {
                    "monitor": True,
                    "wrapper": True,
                    "tray_agent": True,
                    "auto_update": True,
                },
                "releases_url": updater.RELEASES_URL,
                "frozen": updater.is_frozen_build(),
            }
        )

    def list_services(
        self,
        keyword: str = "",
        state_filter: str = "all",
        type_filter: str = "all",
    ) -> dict:
        try:
            items = svc.list_services(keyword, state_filter, type_filter)
            return _ok({"services": items, "total": len(items)})
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def get_service(self, name: str) -> dict:
        if not name:
            return _err("请选择一个服务")
        try:
            return _ok(svc.get_service_info(name))
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def control_service(self, name: str, action: str) -> dict:
        if not name:
            return _err("请先选择一个服务")
        try:
            result = svc.control_service(name, action)
            return _ok(result["info"], f"服务 {name} 已{result['label']}")
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def save_config(self, name: str, payload: dict | None = None) -> dict:
        if not name:
            return _err("请先选择一个服务")
        if not isinstance(payload, dict):
            return _err("配置格式无效")
        try:
            info = svc.save_config(name, payload)
            return _ok(info, f"服务 {name} 配置已保存")
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def add_service(self, payload: dict | None = None) -> dict:
        if not isinstance(payload, dict):
            return _err("配置格式无效")
        try:
            svc.add_service(payload)
            service_name = str(payload.get("name") or "").strip()
            return _ok(None, f"服务 {service_name} 创建成功")
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def add_wrapped_service(self, payload: dict | None = None) -> dict:
        if not isinstance(payload, dict):
            return _err("配置格式无效")
        try:
            info = wrap.add_wrapped_service(payload)
            return _ok(info, f"已将程序注册为服务 {info.get('name')}")
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def delete_service(self, name: str) -> dict:
        if not name:
            return _err("请先选择一个服务")
        try:
            message = svc.delete_service(name)
            return _ok(None, message)
        except win32service.error as exc:
            msg, code = svc.map_winerror(exc)
            if code == "pending_delete":
                return _ok(None, msg)
            return _err(msg, code)
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def set_priority(self, name: str, priority: str) -> dict:
        if not name:
            return _err("请先选择一个服务")
        try:
            pid = svc._get_pid(name)
            result = svc.set_process_priority(pid, priority)
            return _ok(result, f"进程优先级已设置为 {result.get('priority_label', priority)}")
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def get_event_logs(self, name: str, max_records: int = 200) -> dict:
        if not name:
            return _err("请先选择一个服务")
        try:
            logs = svc.query_event_logs(name, max_records=max_records)
            return _ok({"logs": logs, "total": len(logs)})
        except Exception as exc:
            msg, code = svc.map_winerror(exc)
            return _err(msg, code)

    def get_monitor_config(self) -> dict:
        if self._monitor is None:
            return _ok(mon.load_config())
        return _ok(self._monitor.get_config())

    def save_monitor_config(self, payload: dict | None = None) -> dict:
        if not isinstance(payload, dict):
            return _err("监控配置格式无效")
        try:
            if self._monitor is None:
                config = mon.save_config(payload)
            else:
                config = self._monitor.update_config(payload)
            return _ok(config, "监控配置已保存")
        except Exception as exc:
            return _err(str(exc))

    def get_monitor_events(self, limit: int = 50) -> dict:
        if self._monitor is None:
            return _ok({"events": mon.load_events(limit)})
        return _ok({"events": self._monitor.get_events(limit)})

    def get_tray_agent_status(self) -> dict:
        try:
            return _ok(agent_ctl.agent_status())
        except Exception as exc:
            return _err(str(exc))

    def start_tray_agent(self) -> dict:
        try:
            if self._monitor is not None:
                self._monitor.stop()
            status = agent_ctl.start_tray_agent()
            if not status.get("running"):
                return _err("托盘监控代理启动失败")
            return _ok(status, "托盘监控代理已启动（关闭主窗口后仍继续监控）")
        except Exception as exc:
            return _err(str(exc))

    def stop_tray_agent(self) -> dict:
        try:
            status = agent_ctl.stop_tray_agent()
            if self._monitor is not None:
                self._monitor.start()
            return _ok(status, "托盘监控代理已停止，改由主窗口内监控")
        except Exception as exc:
            return _err(str(exc))

    def set_monitor_autostart(self, enabled: bool = False) -> dict:
        try:
            status = agent_ctl.set_autostart(bool(enabled))
            msg = "已设置开机启动监控代理" if enabled else "已取消开机启动"
            return _ok(status, msg)
        except Exception as exc:
            return _err(str(exc))

    def check_update(self) -> dict:
        try:
            info = updater.check_for_update(APP_VERSION)
            self._update_info = info
            return _ok(info, str(info.get("message") or "检查完成"))
        except urllib.error.HTTPError as exc:
            return _err(f"检查更新失败: GitHub 返回 HTTP {exc.code}")
        except urllib.error.URLError as exc:
            return _err(f"检查更新失败: 无法连接 GitHub（{exc.reason}）")
        except Exception as exc:
            return _err(f"检查更新失败: {exc}")

    def apply_update(self) -> dict:
        try:
            info = self._update_info
            if not info or not info.get("update_available"):
                info = updater.check_for_update(APP_VERSION)
                self._update_info = info
            if not info.get("update_available"):
                return _err("当前没有可用更新")
            session = updater.get_session()
            session.start_apply(info, restart=True)

            def _watch_exit() -> None:
                for _ in range(600):
                    snap = session.snapshot()
                    if snap["state"] == "restarting":
                        time.sleep(0.6)
                        os._exit(0)
                    if snap["state"] in ("error", "done"):
                        return
                    time.sleep(0.2)

            threading.Thread(target=_watch_exit, name="svc-update-exit", daemon=True).start()
            return _ok(session.snapshot(), "已开始下载更新")
        except Exception as exc:
            return _err(str(exc))

    def get_update_progress(self) -> dict:
        return _ok(updater.get_session().snapshot())

    def open_releases(self) -> dict:
        try:
            webbrowser.open(updater.RELEASES_URL)
            return _ok(None, "已打开发布页面")
        except Exception as exc:
            return _err(str(exc))

    def pick_file(self) -> dict:
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return _err("窗口未就绪")
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("Executable (*.exe)", "All files (*.*)"),
        )
        if not result:
            return _ok({"path": ""})
        path = result[0] if isinstance(result, (list, tuple)) else result
        return _ok({"path": path})

    def pick_folder(self) -> dict:
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return _err("窗口未就绪")
        result = window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return _ok({"path": ""})
        path = result[0] if isinstance(result, (list, tuple)) else result
        return _ok({"path": path})
