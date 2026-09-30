"""系统通知（Windows Toast / 回退到气泡）。"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def _ps_escape(text: str) -> str:
    return text.replace("'", "''").replace("\r", " ").replace("\n", " ")


def show_toast(title: str, message: str, *, app_id: str = "WindowsServiceEditor") -> bool:
    """尝试弹出系统 Toast；失败时返回 False，不抛异常。"""
    title = _ps_escape((title or "通知")[:120])
    message = _ps_escape((message or "")[:240])
    app_id = _ps_escape(app_id)
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{title}</text>
      <text>{message}</text>
    </binding>
  </visual>
</toast>
"@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=creationflags,
        )
        return completed.returncode == 0
    except Exception:
        return False


def push_ui_alert(title: str, message: str, level: str = "warn") -> None:
    """向已打开的 pywebview 窗口推送告警。"""
    try:
        import json

        import webview
    except Exception:
        return

    payload: dict[str, Any] = {
        "title": title,
        "message": message,
        "level": level,
    }
    js = (
        "window.__svcMonitorAlert && window.__svcMonitorAlert("
        f"{json.dumps(payload, ensure_ascii=False)}"
        ")"
    )
    for window in getattr(webview, "windows", []) or []:
        try:
            window.evaluate_js(js)
        except Exception:
            pass
