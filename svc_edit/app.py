"""应用启动。"""

from __future__ import annotations

import sys
from pathlib import Path

import webview

from .api import Api
from .elevate import elevate_or_exit


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


def main() -> None:
    # 非管理员时自动 UAC 提权后重启
    elevate_or_exit()

    index = resource_path("web", "index.html")
    if not index.exists():
        raise FileNotFoundError(f"找不到前端页面: {index}")

    webview.create_window(
        "Windows 服务管理器",
        url=index.as_uri(),
        js_api=Api(),
        width=1120,
        height=680,
        min_size=(880, 520),
        background_color="#090d12",
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
