"""托盘常驻监控代理（无 GUI）。"""

from __future__ import annotations

import os
import sys
from typing import Any

import win32api
import win32con
import win32gui

from .agent_ctl import clear_tray_pid, read_tray_pid, write_tray_pid
from .elevate import elevate_or_exit
from .monitor import MonitorEngine
from .notify import show_toast

WM_TRAY = win32con.WM_USER + 42
ID_OPEN = 1001
ID_TOGGLE = 1002
ID_EXIT = 1003


def _load_icon() -> int:
    # 优先 exe 内嵌图标；失败则用系统应用图标
    try:
        if getattr(sys, "frozen", False):
            large, small = win32gui.ExtractIconEx(sys.executable, 0)
            if small:
                if large:
                    for h in large:
                        try:
                            win32gui.DestroyIcon(h)
                        except Exception:
                            pass
                return int(small[0])
            if large:
                return int(large[0])
    except Exception:
        pass
    return int(win32gui.LoadIcon(0, win32con.IDI_APPLICATION))


class TrayAgent:
    def __init__(self) -> None:
        self.hwnd = 0
        self.hicon = _load_icon()
        self._nid: tuple[Any, ...] | None = None
        self._paused = False
        self.engine = MonitorEngine(push_ui=False, reload_config=True, on_event=self._on_event)
        self._class_name = "WindowsServiceEditorTray"

    def _on_event(self, event: dict[str, Any]) -> None:
        title = str(event.get("title") or "服务告警")
        message = str(event.get("message") or "")
        self._balloon(title, message)
        # show_toast 已由 engine 按配置调用；托盘再补一次气泡更醒目
        if not event.get("_balloon_only"):
            pass

    def _balloon(self, title: str, message: str) -> None:
        if not self._nid:
            return
        try:
            flags = win32gui.NIF_INFO | win32gui.NIF_MESSAGE | win32gui.NIF_ICON | win32gui.NIF_TIP
            nid = (
                self.hwnd,
                0,
                flags,
                WM_TRAY,
                self.hicon,
                "服务监控",
                message[:250],
                200,
                title[:60],
            )
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
            self._nid = nid
        except Exception:
            show_toast(title, message)

    def _add_icon(self) -> None:
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        self._nid = (self.hwnd, 0, flags, WM_TRAY, self.hicon, "Windows 服务监控")
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._nid)

    def _remove_icon(self) -> None:
        if not self._nid:
            return
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self._nid)
        except Exception:
            pass
        self._nid = None

    def _open_gui(self) -> None:
        try:
            from .agent_ctl import launch_command

            exe, args, cwd = launch_command()
            # 去掉 --tray，启动 GUI
            gui_args = [a for a in args if a != "--tray"]
            import subprocess

            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            subprocess.Popen(
                [exe, *gui_args] if gui_args else [exe],
                cwd=cwd,
                creationflags=creationflags,
                close_fds=True,
            )
        except Exception:
            pass

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.engine.set_paused(self._paused)
        if self._paused:
            self._balloon("监控已暂停", "托盘菜单可恢复监控")
        else:
            self._balloon("监控已恢复", "正在按配置轮询服务状态")

    def _popup_menu(self) -> None:
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_OPEN, "打开主界面")
        win32gui.AppendMenu(
            menu,
            win32con.MF_STRING,
            ID_TOGGLE,
            "恢复监控" if self._paused else "暂停监控",
        )
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_EXIT, "退出监控代理")
        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, pos[0], pos[1], 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam == win32con.WM_RBUTTONUP:
                self._popup_menu()
            elif lparam == win32con.WM_LBUTTONDBLCLK:
                self._open_gui()
            return 0
        if msg == win32con.WM_COMMAND:
            cmd = win32api.LOWORD(wparam)
            if cmd == ID_OPEN:
                self._open_gui()
            elif cmd == ID_TOGGLE:
                self._toggle_pause()
            elif cmd == ID_EXIT:
                win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            self._remove_icon()
            self.engine.stop()
            clear_tray_pid(os.getpid())
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def run(self) -> None:
        existing = read_tray_pid()
        if existing is not None and existing != os.getpid():
            raise SystemExit(0)

        write_tray_pid(os.getpid())
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = self._class_name
        wc.lpfnWndProc = self._wnd_proc
        wc.hInstance = win32api.GetModuleHandle(None)
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass
        self.hwnd = win32gui.CreateWindow(
            self._class_name,
            "WindowsServiceEditorTray",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            wc.hInstance,
            None,
        )
        self._add_icon()
        # 默认打开监控开关，否则托盘常驻无意义
        cfg = self.engine.get_config()
        if cfg.get("watched") and not cfg.get("enabled"):
            cfg = {**cfg, "enabled": True}
            self.engine.update_config(cfg)
        self.engine.start()
        win32gui.PumpMessages()


def run_tray_agent() -> None:
    elevate_or_exit()
    TrayAgent().run()
