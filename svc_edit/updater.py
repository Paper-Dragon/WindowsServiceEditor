"""基于 GitHub Releases 的自动升级。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

GITHUB_REPO = "Paper-Dragon/WindowsServiceEditor"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
PORTABLE_ASSET = "svc-edit.exe"
SETUP_ASSET = "svc-edit-setup.exe"
USER_AGENT = "WindowsServiceEditor-Updater"


def parse_version(text: str) -> tuple[int, ...]:
    raw = (text or "").strip()
    if raw.lower().startswith("v"):
        raw = raw[1:]
    # 去掉预发布后缀：1.2.3-beta → 1.2.3
    raw = raw.split("-", 1)[0].split("+", 1)[0]
    parts = re.findall(r"\d+", raw)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def version_gt(left: str, right: str) -> bool:
    a = list(parse_version(left))
    b = list(parse_version(right))
    n = max(len(a), len(b))
    a.extend([0] * (n - len(a)))
    b.extend([0] * (n - len(b)))
    return tuple(a) > tuple(b)


def _http_get_json(url: str, timeout: float = 20.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GitHub API 返回格式无效")
    return data


def _pick_asset(assets: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for asset in assets:
        if str(asset.get("name") or "").lower() == name.lower():
            return asset
    return None


def current_executable() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(sys.argv[0]).resolve()


def is_frozen_build() -> bool:
    return bool(getattr(sys, "frozen", False))


def check_for_update(current_version: str) -> dict[str, Any]:
    """查询最新 Release，返回结构化结果。"""
    release = _http_get_json(API_LATEST)
    tag = str(release.get("tag_name") or "").strip()
    latest = tag[1:] if tag.lower().startswith("v") else tag
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    portable = _pick_asset(assets, PORTABLE_ASSET)
    setup = _pick_asset(assets, SETUP_ASSET)
    preferred = portable or setup
    update_available = bool(latest and version_gt(latest, current_version))
    ahead = bool(latest and version_gt(current_version, latest))
    same = bool(latest) and not update_available and not ahead

    if update_available:
        status = "update_available"
        message = f"发现新版本 {latest}（当前 {current_version}）"
    elif ahead:
        status = "ahead"
        message = (
            f"当前版本 {current_version} 新于已发布版本 {latest}，"
            "无需更新（发布页尚未打出更新 tag）"
        )
    elif same:
        status = "up_to_date"
        message = f"已是最新版本 {current_version}"
    else:
        status = "unknown"
        message = "未能解析远程版本号"

    return {
        "status": status,
        "message": message,
        "update_available": update_available,
        "current_version": current_version,
        "latest_version": latest,
        "tag_name": tag,
        "name": str(release.get("name") or tag),
        "body": str(release.get("body") or "") if update_available else "",
        "html_url": str(release.get("html_url") or RELEASES_URL),
        "published_at": str(release.get("published_at") or ""),
        "asset_name": str(preferred.get("name") if preferred else ""),
        "asset_url": str(preferred.get("browser_download_url") if preferred else ""),
        "asset_size": int(preferred.get("size") if preferred else 0) or 0,
        "portable": bool(portable),
        "can_apply": bool(update_available and preferred and is_frozen_build()),
        "frozen": is_frozen_build(),
    }


ProgressCb = Callable[[int, int], None]


def download_file(url: str, dest: Path, on_progress: ProgressCb | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    tmp.replace(dest)
    if on_progress:
        on_progress(total or done, total or done)
    return dest


def _write_updater_script(target: Path, new_file: Path, restart: bool) -> Path:
    """生成替换脚本：等待进程退出后覆盖 exe 并可选重启。"""
    script = Path(tempfile.gettempdir()) / f"svc-edit-update-{os.getpid()}.cmd"
    target_s = str(target)
    new_s = str(new_file)
    pid = os.getpid()
    lines = [
        "@echo off",
        "setlocal",
        f"set PID={pid}",
        f'set TARGET={target_s}',
        f'set NEWFILE={new_s}',
        "echo Waiting for application to exit...",
        ":wait",
        "tasklist /FI \"PID eq %PID%\" | find \"%PID%\" >nul",
        "if not errorlevel 1 (",
        "  timeout /t 1 /nobreak >nul",
        "  goto wait",
        ")",
        "timeout /t 1 /nobreak >nul",
        "echo Replacing executable...",
        "del /f /q \"%TARGET%.old\" >nul 2>&1",
        "if exist \"%TARGET%\" move /y \"%TARGET%\" \"%TARGET%.old\" >nul",
        "move /y \"%NEWFILE%\" \"%TARGET%\"",
        "if errorlevel 1 (",
        "  echo Update failed. Restoring...",
        "  if exist \"%TARGET%.old\" move /y \"%TARGET%.old\" \"%TARGET%\" >nul",
        "  pause",
        "  exit /b 1",
        ")",
        "del /f /q \"%TARGET%.old\" >nul 2>&1",
    ]
    if restart:
        lines += [
            "echo Starting updated application...",
            "start \"\" \"%TARGET%\"",
        ]
    lines += [
        "del /f /q \"%~f0\" >nul 2>&1",
        "endlocal",
    ]
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return script


class UpdateSession:
    """带进度的下载会话（供 API 轮询）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "idle"
        self.message = ""
        self.downloaded = 0
        self.total = 0
        self.error = ""
        self.result: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            pct = 0
            if self.total > 0:
                pct = min(100, int(self.downloaded * 100 / self.total))
            return {
                "state": self.state,
                "message": self.message,
                "downloaded": self.downloaded,
                "total": self.total,
                "percent": pct,
                "error": self.error,
                "result": self.result,
            }

    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def start_apply(self, info: dict[str, Any], *, restart: bool = True) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("已有升级任务在进行")
        if not info.get("asset_url"):
            raise ValueError("没有可下载的更新包")
        if not is_frozen_build():
            raise RuntimeError("开发运行模式不支持一键覆盖，请前往 Releases 手动下载")

        def worker() -> None:
            try:
                self._set(state="downloading", message="正在下载更新…", error="", result=None)
                asset_name = str(info.get("asset_name") or PORTABLE_ASSET)
                dest = Path(tempfile.gettempdir()) / f"svc-edit-download-{os.getpid()}-{asset_name}"

                def on_progress(done: int, total: int) -> None:
                    self._set(downloaded=done, total=total)

                download_file(str(info["asset_url"]), dest, on_progress=on_progress)

                if asset_name.lower().endswith("setup.exe") or "setup" in asset_name.lower():
                    self._set(state="launching", message="正在启动安装程序…")
                    subprocess.Popen(
                        [str(dest)],
                        cwd=str(dest.parent),
                        close_fds=True,
                    )
                    self._set(
                        state="done",
                        message="已启动安装程序，请按向导完成升级后重新打开应用",
                        result={"mode": "setup", "path": str(dest)},
                    )
                    return

                target = current_executable()
                self._set(state="applying", message="准备替换程序文件…")
                script = _write_updater_script(target, dest, restart=restart)
                subprocess.Popen(
                    ["cmd.exe", "/c", str(script)],
                    cwd=str(tempfile.gettempdir()),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0),
                    close_fds=True,
                )
                self._set(
                    state="restarting",
                    message="更新已就绪，即将退出并完成替换…",
                    result={"mode": "portable", "script": str(script), "target": str(target)},
                )
            except Exception as exc:
                self._set(state="error", message="升级失败", error=str(exc))

        self._thread = threading.Thread(target=worker, name="svc-updater", daemon=True)
        self._thread.start()


_session = UpdateSession()


def get_session() -> UpdateSession:
    return _session
