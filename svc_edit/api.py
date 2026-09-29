"""pywebview JS API 桥接。"""

from __future__ import annotations

import webview
import win32service

from . import services as svc
from .constants import START_TYPE_OPTIONS, TYPE_FILTERS


def _ok(data=None, message: str = "") -> dict:
    return {"ok": True, "data": data, "message": message}


def _err(message: str, code: str | None = None) -> dict:
    return {"ok": False, "message": message, "code": code}


class Api:
    """前端通过 window.pywebview.api 调用。"""

    def get_meta(self) -> dict:
        return _ok(
            {
                "is_admin": svc.is_admin(),
                "version": "0.4.0",
                "start_options": START_TYPE_OPTIONS,
                "type_filters": [
                    {"value": k, "label": v} for k, v in TYPE_FILTERS.items()
                ],
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
