"""任意程序变服务：宿主进程 + 注册。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import winreg
from pathlib import Path
from typing import Any

import pywintypes
import servicemanager
import win32event
import win32service
import win32serviceutil

from .paths import wrapper_log_path

WRAPPER_MARKER = "svc-edit"
_PARAM_KEY = "Parameters"


def _quote(arg: str) -> str:
    if not arg:
        return '""'
    if any(ch in arg for ch in ' \t"'):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def host_executable() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return sys.executable


def host_script() -> str | None:
    if getattr(sys, "frozen", False):
        return None
    # main.py 或 -m 入口：优先 sys.argv[0]
    candidate = Path(sys.argv[0]).resolve()
    if candidate.exists():
        return str(candidate)
    return str(Path(__file__).resolve().parent.parent / "main.py")


def build_host_image_path(service_name: str) -> str:
    """构造 SCM ImagePath，使服务启动时进入宿主模式。"""
    name = service_name.strip()
    if getattr(sys, "frozen", False):
        return f"{_quote(host_executable())} --service {_quote(name)}"
    script = host_script()
    if not script:
        raise RuntimeError("无法定位入口脚本")
    return f"{_quote(host_executable())} {_quote(script)} --service {_quote(name)}"


def _open_params(service_name: str, access: int):
    path = rf"SYSTEM\CurrentControlSet\Services\{service_name}\{_PARAM_KEY}"
    return winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, access)


def write_wrapper_params(service_name: str, data: dict[str, Any]) -> None:
    application = str(data.get("application") or "").strip().strip('"')
    if not application:
        raise ValueError("目标程序路径不能为空")
    abs_app = os.path.abspath(os.path.expandvars(application))
    if not os.path.exists(abs_app):
        raise FileNotFoundError(f"目标程序不存在: {abs_app}")

    arguments = str(data.get("arguments") or "")
    directory = str(data.get("working_directory") or "").strip().strip('"')
    if directory:
        directory = os.path.abspath(os.path.expandvars(directory))
        if not os.path.isdir(directory):
            raise FileNotFoundError(f"工作目录不存在: {directory}")
    else:
        directory = str(Path(abs_app).parent)

    restart_delay_ms = int(data.get("restart_delay_ms", 3000) or 0)
    if restart_delay_ms < 0:
        restart_delay_ms = 0
    throttle = int(data.get("throttle_seconds", 60) or 60)
    if throttle < 5:
        throttle = 5
    max_restarts = int(data.get("max_restarts", 5) or 5)
    if max_restarts < 0:
        max_restarts = 0
    on_exit = str(data.get("on_exit") or "restart").strip().lower()
    if on_exit not in ("restart", "ignore", "exit"):
        on_exit = "restart"

    with _open_params(service_name, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "WrapperHost", 0, winreg.REG_SZ, WRAPPER_MARKER)
        winreg.SetValueEx(key, "Application", 0, winreg.REG_SZ, abs_app)
        winreg.SetValueEx(key, "AppParameters", 0, winreg.REG_SZ, arguments)
        winreg.SetValueEx(key, "AppDirectory", 0, winreg.REG_SZ, directory)
        winreg.SetValueEx(key, "AppRestartDelay", 0, winreg.REG_DWORD, restart_delay_ms)
        winreg.SetValueEx(key, "AppThrottle", 0, winreg.REG_DWORD, throttle)
        winreg.SetValueEx(key, "AppMaxRestarts", 0, winreg.REG_DWORD, max_restarts)
        winreg.SetValueEx(key, "AppExitDefault", 0, winreg.REG_SZ, on_exit)


def _reg_get(key, name: str, default: Any = "") -> Any:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except FileNotFoundError:
        return default
    except OSError:
        return default


def read_wrapper_params(service_name: str) -> dict[str, Any] | None:
    path = rf"SYSTEM\CurrentControlSet\Services\{service_name}\{_PARAM_KEY}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            marker = str(_reg_get(key, "WrapperHost", ""))
            if marker != WRAPPER_MARKER:
                return None
            return {
                "is_wrapped": True,
                "application": str(_reg_get(key, "Application", "")),
                "arguments": str(_reg_get(key, "AppParameters", "")),
                "working_directory": str(_reg_get(key, "AppDirectory", "")),
                "restart_delay_ms": int(_reg_get(key, "AppRestartDelay", 3000) or 0),
                "throttle_seconds": int(_reg_get(key, "AppThrottle", 60) or 60),
                "max_restarts": int(_reg_get(key, "AppMaxRestarts", 5) or 5),
                "on_exit": str(_reg_get(key, "AppExitDefault", "restart") or "restart"),
            }
    except FileNotFoundError:
        return None
    except OSError:
        return None


def is_wrapped_service(service_name: str) -> bool:
    return read_wrapper_params(service_name) is not None


def add_wrapped_service(payload: dict[str, Any]) -> dict[str, Any]:
    """注册「程序变服务」：ImagePath 指向本宿主，目标程序写入 Parameters。"""
    data = dict(payload or {})
    name = str(data.get("name") or "").strip()
    display_name = str(data.get("display_name") or "").strip() or name
    description = str(data.get("description") or "").strip()
    if not name:
        raise ValueError("服务名不能为空")
    if not description:
        description = f"由 Windows 服务管理器托管运行: {data.get('application') or ''}"

    start = int(data.get("start", 2) or 2)
    if start not in (2, 3, 4):
        start = 2
    start_map = {
        2: win32service.SERVICE_AUTO_START,
        3: win32service.SERVICE_DEMAND_START,
        4: win32service.SERVICE_DISABLED,
    }

    image_path = build_host_image_path(name)
    # 先写 Parameters 校验目标程序存在
    # CreateService 后再写，避免服务名冲突时留下脏键；先校验文件
    application = str(data.get("application") or data.get("executable") or "").strip()
    data["application"] = application
    abs_app = os.path.abspath(os.path.expandvars(application.strip('"')))
    if not os.path.exists(abs_app):
        raise FileNotFoundError(f"目标程序不存在: {abs_app}")

    hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CREATE_SERVICE)
    handle = None
    try:
        handle = win32service.CreateService(
            hscm,
            name,
            display_name,
            win32service.SERVICE_ALL_ACCESS,
            win32service.SERVICE_WIN32_OWN_PROCESS,
            start_map[start],
            win32service.SERVICE_ERROR_NORMAL,
            image_path,
            None,
            False,
            None,
            "LocalSystem",
            "",
        )
        win32service.ChangeServiceConfig2(
            handle,
            win32service.SERVICE_CONFIG_DESCRIPTION,
            description,
        )
    finally:
        if handle is not None:
            win32service.CloseServiceHandle(handle)
        win32service.CloseServiceHandle(hscm)

    write_wrapper_params(name, data)

    delayed_auto = bool(data.get("delayed_auto", False))
    if delayed_auto and start == 2:
        reg_path = rf"SYSTEM\CurrentControlSet\Services\{name}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_SET_VALUE) as rk:
            winreg.SetValueEx(rk, "DelayedAutostart", 0, winreg.REG_DWORD, 1)

    return {
        "name": name,
        "display_name": display_name,
        "image_path": image_path,
        "application": abs_app,
        "is_wrapped": True,
    }


def _setup_logger(service_name: str) -> logging.Logger:
    logger = logging.getLogger(f"svc_wrapper.{service_name}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_file = wrapper_log_path(service_name)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


class WrapperService(win32serviceutil.ServiceFramework):
    """动态服务名的进程宿主。"""

    _svc_name_ = "SvcEditWrapper"
    _svc_display_name_ = "SvcEdit Wrapper"
    _svc_description_ = "Windows 服务管理器程序宿主"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._proc: subprocess.Popen[Any] | None = None
        self._stopping = False
        self._logger = _setup_logger(self._svc_name_)

    def SvcStop(self):
        self._stopping = True
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self._terminate_child()

    def SvcShutdown(self):
        self.SvcStop()

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        try:
            self._run_loop()
        except Exception as exc:
            self._logger.exception("宿主异常退出: %s", exc)
            servicemanager.LogErrorMsg(f"{self._svc_name_} failed: {exc}")
        finally:
            self._terminate_child()

    def _terminate_child(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception as exc:
            self._logger.warning("终止子进程失败: %s", exc)
        finally:
            self._proc = None

    def _start_child(self, params: dict[str, Any]) -> subprocess.Popen[Any]:
        application = params["application"]
        arguments = params.get("arguments") or ""
        directory = params.get("working_directory") or str(Path(application).parent)
        cmdline = f"{_quote(application)} {arguments}".strip()
        self._logger.info("启动: %s (cwd=%s)", cmdline, directory)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(
            cmdline,
            cwd=directory,
            shell=False,
            creationflags=creationflags,
        )

    def _run_loop(self) -> None:
        params = read_wrapper_params(self._svc_name_)
        if not params:
            raise RuntimeError(f"未找到包装参数: {self._svc_name_}")

        on_exit = str(params.get("on_exit") or "restart").lower()
        restart_delay = max(0, int(params.get("restart_delay_ms") or 0)) / 1000.0
        throttle = max(5, int(params.get("throttle_seconds") or 60))
        max_restarts = max(0, int(params.get("max_restarts") or 5))
        restart_times: list[float] = []

        while not self._stopping:
            if win32event.WaitForSingleObject(self.stop_event, 0) == win32event.WAIT_OBJECT_0:
                break
            try:
                self._proc = self._start_child(params)
            except Exception as exc:
                self._logger.error("启动子进程失败: %s", exc)
                break

            while not self._stopping:
                rc = win32event.WaitForSingleObject(self.stop_event, 500)
                if rc == win32event.WAIT_OBJECT_0:
                    break
                if self._proc.poll() is not None:
                    break

            if self._stopping:
                break

            code = self._proc.returncode if self._proc else -1
            self._logger.warning("子进程退出，代码=%s", code)
            self._proc = None

            if on_exit == "ignore":
                # 保持服务运行，等待停止信号
                while not self._stopping:
                    if win32event.WaitForSingleObject(self.stop_event, 1000) == win32event.WAIT_OBJECT_0:
                        break
                break

            if on_exit == "exit":
                break

            # restart
            now = time.time()
            restart_times = [t for t in restart_times if now - t < throttle]
            if max_restarts and len(restart_times) >= max_restarts:
                self._logger.error(
                    "在 %s 秒内重启超过 %s 次，停止重启",
                    throttle,
                    max_restarts,
                )
                break
            restart_times.append(now)
            if restart_delay > 0:
                win32event.WaitForSingleObject(self.stop_event, int(restart_delay * 1000))


def run_service_host(service_name: str) -> None:
    """由 SCM 调用：以 Windows 服务方式托管目标程序。"""
    name = (service_name or "").strip()
    if not name:
        raise SystemExit("缺少服务名")

    WrapperService._svc_name_ = name
    WrapperService._svc_display_name_ = name

    try:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(WrapperService)
        servicemanager.StartServiceCtrlDispatcher()
    except pywintypes.error as exc:
        # 非 SCM 上下文时给出明确错误，便于手工调试
        if exc.winerror == 1063:
            raise SystemExit(
                f"请通过服务控制管理器启动，或检查 ImagePath 是否包含 --service {name}"
            ) from exc
        raise
