"""应用启动。"""

from __future__ import annotations

import sys
from pathlib import Path

import webview

from .api import Api
from .elevate import elevate_or_exit
from .monitor import MonitorEngine


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


def _run_service_mode() -> None:
    from .wrapper import run_service_host

    if len(sys.argv) < 3:
        raise SystemExit("用法: --service <ServiceName>")
    run_service_host(sys.argv[2])


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--service":
        _run_service_mode()
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "--tray":
        from .tray_agent import run_tray_agent

        run_tray_agent()
        return

    # 非管理员时自动 UAC 提权后重启
    elevate_or_exit()

    index = resource_path("web", "index.html")
    if not index.exists():
        raise FileNotFoundError(f"找不到前端页面: {index}")

    from .agent_ctl import agent_status

    api = Api()
    status = agent_status()
    # 托盘代理已在跑时，GUI 不再本地轮询，避免重复告警；仍挂载引擎便于读配置/事件
    engine = MonitorEngine(push_ui=True, reload_config=True)
    api.attach_monitor(engine)

    webview.create_window(
        "Windows 服务管理器",
        url=index.as_uri(),
        js_api=api,
        width=1120,
        height=680,
        min_size=(880, 520),
        background_color="#090d12",
    )

    def _after_start() -> None:
        if not status.get("running"):
            engine.start()

    webview.start(func=_after_start, debug=False)
    engine.stop()


if __name__ == "__main__":
    main()
