"""Windows 服务 / 注册表操作层。"""

from __future__ import annotations

import os
import threading
import time
import winreg
from contextlib import contextmanager
from typing import Any, Iterator

import pywintypes
import win32api
import win32security
import win32service
import win32serviceutil

from .constants import CONTROL_ACTIONS, START_TYPE_MAP, STATE_MAP
from .elevate import is_admin

__all__ = ["is_admin"]

# SCM 与注册表写操作互斥锁，防止多线程并发修改造成 SCM 锁或状态竞态冲突
_scm_mutation_lock = threading.Lock()

RESET_NEVER = -1
_FAILURE_SLOTS = 3

ACCOUNT_TYPES = {
    "local": "LocalSystem",
    "service": r"NT AUTHORITY\LocalService",
    "network": r"NT AUTHORITY\NetworkService",
}

_ACCOUNT_LOOKUP = {
    "": "local",
    "localsystem": "local",
    "local system": "local",
    r"nt authority\system": "local",
    "system": "local",
    "localservice": "service",
    r"nt authority\localservice": "service",
    "networkservice": "network",
    r"nt authority\networkservice": "network",
}

_ACTION_TO_CODE = {
    "none": win32service.SC_ACTION_NONE,
    "restart": win32service.SC_ACTION_RESTART,
    "reboot": win32service.SC_ACTION_REBOOT,
}
_CODE_TO_ACTION = {code: name for name, code in _ACTION_TO_CODE.items()}


def _reg_query(key, name: str, default: Any = "") -> Any:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except (FileNotFoundError, OSError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _expand(value: Any) -> str:
    if not isinstance(value, str):
        return str(value) if value is not None else ""
    try:
        return win32api.ExpandEnvironmentStrings(value)
    except Exception:
        return os.path.expandvars(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def get_state(name: str) -> str:
    try:
        status = win32serviceutil.QueryServiceStatus(name)
        return STATE_MAP.get(status[1], "未知")
    except win32service.error as exc:
        return "未安装" if exc.winerror == 1060 else "未知"
    except Exception:
        return "未知"


def _start_label(start: int) -> str:
    if start < 0:
        return "—"
    return START_TYPE_MAP.get(start, "未知")


def split_image_path(image_path: str) -> tuple[str, str]:
    """把 ImagePath 拆成可执行文件和参数。"""
    text = (image_path or "").strip()
    if not text:
        return "", ""
    if text[0] == '"':
        end = text.find('"', 1)
        if end == -1:
            return text.strip('"'), ""
        return text[1:end], text[end + 1 :].strip()
    if " " not in text and "\t" not in text:
        return text, ""
    executable, arguments = text.split(None, 1)
    return executable, arguments


def join_image_path(executable: str, arguments: str) -> str:
    """把可执行文件和参数拼回 ImagePath。路径含空格时加引号。"""
    exe = (executable or "").strip()
    if len(exe) >= 2 and exe[0] == '"' and exe[-1] == '"':
        exe = exe[1:-1]
    args = (arguments or "").strip()
    if not exe:
        return ""
    if any(ch.isspace() for ch in exe):
        quoted = '"' + exe.replace('"', "") + '"'
    else:
        quoted = exe
    return f"{quoted} {args}" if args else quoted


def classify_account(account_name: str) -> tuple[str, str]:
    """返回 (local|service|network|custom, 显示用账户名)。"""
    raw = (account_name or "").strip()
    key = raw.lower().replace("/", "\\")
    kind = _ACCOUNT_LOOKUP.get(key, "")
    if kind == "local" or not raw:
        return "local", ACCOUNT_TYPES["local"]
    if kind in ("service", "network"):
        return kind, ACCOUNT_TYPES[kind]
    return "custom", raw


def canonical_account(account_type: str, username: str = "") -> str:
    kind = (account_type or "local").strip().lower()
    if kind in ACCOUNT_TYPES:
        return ACCOUNT_TYPES[kind]
    if kind != "custom":
        raise ValueError("未知的登录账户类型")
    user = (username or "").strip()
    if not user:
        raise ValueError("自定义账户需要填写用户名")
    if "\\" not in user and "@" not in user:
        user = ".\\" + user
    return user


def parse_environment(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, str):
        lines = [raw]
    elif isinstance(raw, (list, tuple)):
        lines = [str(item) for item in raw if item]
    else:
        lines = []
    items: list[dict[str, str]] = []
    for line in lines:
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name:
            items.append({"name": name, "value": value})
    return items


def normalize_environments(items: Any) -> list[str]:
    if not items:
        return []
    if not isinstance(items, (list, tuple)):
        raise ValueError("环境变量格式无效")
    seen: set[str] = set()
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") if item.get("value") is not None else "")
        if not name:
            continue
        if "=" in name or "\n" in name or "\r" in name or "\x00" in name:
            raise ValueError(f"环境变量名无效: {name}")
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError(f"环境变量值不能包含换行: {name}")
        key = name.lower()
        if key in seen:
            raise ValueError(f"环境变量重复: {name}")
        seen.add(key)
        lines.append(f"{name}={value}")
    return lines


def _default_actions() -> list[dict[str, Any]]:
    return [{"type": "none", "delay_sec": 0} for _ in range(_FAILURE_SLOTS)]


def failure_from_raw(raw: dict | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "known": False,
            "never_reset": True,
            "reset_seconds": 86400,
            "actions": _default_actions(),
            "extra_actions": [],
        }
    reset = _as_int(raw.get("ResetPeriod"), 0)
    never = reset < 0
    parsed: list[dict[str, Any]] = []
    for item in raw.get("Actions") or ():
        type_code = _as_int(item[0], win32service.SC_ACTION_NONE)
        delay_ms = max(0, _as_int(item[1], 0))
        parsed.append(
            {
                "type": _CODE_TO_ACTION.get(type_code, "none"),
                "delay_sec": int(round(delay_ms / 1000)),
            }
        )
    visible = parsed[:_FAILURE_SLOTS]
    while len(visible) < _FAILURE_SLOTS:
        visible.append({"type": "none", "delay_sec": 0})
    return {
        "known": True,
        "never_reset": never,
        "reset_seconds": 0 if never else reset,
        "actions": visible,
        "extra_actions": parsed[_FAILURE_SLOTS:],
    }


def failure_to_scm(failure: dict[str, Any], existing: dict | None) -> dict[str, Any]:
    actions_in = failure.get("actions") or []
    if not isinstance(actions_in, (list, tuple)):
        raise ValueError("失败恢复格式无效")
    slots: list[tuple[int, int]] = []
    for item in list(actions_in)[:_FAILURE_SLOTS]:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("type") or "none").strip().lower()
        if action_type not in _ACTION_TO_CODE:
            raise ValueError(f"未知的失败操作: {action_type}")
        delay_sec = _as_int(item.get("delay_sec"), 0)
        if delay_sec < 0 or delay_sec > 86400 * 7:
            raise ValueError("失败延迟需在 0 到 604800 秒之间")
        slots.append((_ACTION_TO_CODE[action_type], delay_sec * 1000))
    while len(slots) < _FAILURE_SLOTS:
        slots.append((win32service.SC_ACTION_NONE, 0))
    for item in failure.get("extra_actions") or []:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("type") or "none").strip().lower()
        code = _ACTION_TO_CODE.get(action_type, win32service.SC_ACTION_NONE)
        delay_sec = max(0, _as_int(item.get("delay_sec"), 0))
        slots.append((code, delay_sec * 1000))

    if _as_bool(failure.get("never_reset")):
        reset = RESET_NEVER
    else:
        reset = _as_int(failure.get("reset_seconds"), 0)
        if reset < 0:
            reset = RESET_NEVER

    reboot_msg = ""
    command = ""
    if isinstance(existing, dict):
        reboot_msg = existing.get("RebootMsg") or ""
        command = existing.get("Command") or ""
    return {
        "ResetPeriod": reset,
        "RebootMsg": reboot_msg,
        "Command": command,
        "Actions": slots,
    }


def _service_kind_from_type(service_type: int) -> str:
    if service_type & (
        win32service.SERVICE_WIN32_OWN_PROCESS | win32service.SERVICE_WIN32_SHARE_PROCESS
    ):
        return "win32"
    if service_type & (
        win32service.SERVICE_KERNEL_DRIVER | win32service.SERVICE_FILE_SYSTEM_DRIVER
    ):
        return "driver"
    return "other"


def _service_kind(name: str) -> str:
    try:
        status = win32serviceutil.QueryServiceStatus(name)
        return _service_kind_from_type(int(status[0]))
    except Exception:
        return "other"


def _read_reg_meta(name: str) -> dict[str, Any]:
    start = -1
    display_name = ""
    description = ""
    image_path = ""
    working_directory = ""
    delayed_auto = False
    environments: list[dict[str, str]] = []
    account_name = ""
    dependencies: list[str] = []
    try:
        path = rf"SYSTEM\CurrentControlSet\Services\{name}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            start = int(_reg_query(key, "Start", -1))
            display_name = str(_reg_query(key, "DisplayName", "") or "")
            description = str(_reg_query(key, "Description", "") or "")
            image_path = str(_reg_query(key, "ImagePath", "") or "")
            working_directory = str(_reg_query(key, "AppDirectory", "") or "")
            account_name = str(_reg_query(key, "ObjectName", "") or "")
            environments = parse_environment(_reg_query(key, "Environment", []))
            delayed_auto = bool(_as_int(_reg_query(key, "DelayedAutostart", 0)))
            raw_deps = _reg_query(key, "DependOnService", [])
            if isinstance(raw_deps, (list, tuple)):
                dependencies = [str(d) for d in raw_deps if d]
            elif isinstance(raw_deps, str) and raw_deps:
                dependencies = [raw_deps]
    except Exception:
        pass
    executable, arguments = split_image_path(image_path)
    account_type, account_label = classify_account(account_name)
    start_label = _start_label(start)
    if start == 2 and delayed_auto:
        start_label = "自动（延迟）"
    return {
        "start": start,
        "delayed_auto": delayed_auto,
        "start_label": start_label,
        "display_name": display_name,
        "description": description,
        "image_path": image_path,
        "executable": executable,
        "arguments": arguments,
        "working_directory": working_directory,
        "account_type": account_type,
        "account_name": account_label if account_type != "custom" else account_name,
        "environments": environments,
        "dependencies": dependencies,
    }


def _query_failure_raw(name: str) -> dict | None:
    try:
        with _service_handle(name, win32service.SERVICE_QUERY_CONFIG) as handle:
            raw = win32service.QueryServiceConfig2(
                handle, win32service.SERVICE_CONFIG_FAILURE_ACTIONS
            )
            return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def _get_pid(name: str) -> int:
    """Return the PID of a running service, or 0."""
    try:
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            hs = win32service.OpenService(hscm, name, win32service.SERVICE_QUERY_STATUS)
            try:
                info = win32service.QueryServiceStatusEx(hs)
                return info.get("ProcessId", 0) or 0
            finally:
                win32service.CloseServiceHandle(hs)
        finally:
            win32service.CloseServiceHandle(hscm)
    except Exception:
        return 0


def get_service_info(name: str) -> dict[str, Any]:
    meta = _read_reg_meta(name)
    failure_raw = _query_failure_raw(name)
    return {
        "name": name,
        "state": get_state(name),
        "pid": _get_pid(name),
        "kind": _service_kind(name),
        "failure": failure_from_raw(failure_raw),
        **meta,
    }


def _enum_scm(service_type: int) -> list[tuple[str, str, int, int]]:
    """Return list of (name, display_name, state_code, service_type)."""
    items: list[tuple[str, str, int, int]] = []
    hscm = None
    try:
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
        statuses = win32service.EnumServicesStatus(hscm, service_type, win32service.SERVICE_STATE_ALL)
        for name, display_name, status in statuses:
            items.append((name, display_name or "", status[1], status[0]))
    except Exception:
        pass
    finally:
        if hscm is not None:
            try:
                win32service.CloseServiceHandle(hscm)
            except Exception:
                pass
    return items


def _type_code_to_kind(service_type: int) -> str:
    return _service_kind_from_type(service_type)


def list_services(
    keyword: str = "",
    state_filter: str = "all",
    type_filter: str = "all",
) -> list[dict[str, Any]]:
    """
    Fast list via SCM EnumServicesStatus (state included).
    Start type is omitted here for speed; detail view loads it.
    """
    keyword = (keyword or "").strip().lower()
    state_filter = (state_filter or "all").lower()
    type_filter = (type_filter or "all").lower()

    type_mask = 0
    if type_filter in ("all", "win32"):
        type_mask |= win32service.SERVICE_WIN32
    if type_filter in ("all", "driver"):
        type_mask |= win32service.SERVICE_DRIVER
    if type_mask == 0:
        type_mask = win32service.SERVICE_WIN32 | win32service.SERVICE_DRIVER

    raw = _enum_scm(type_mask)
    result: list[dict[str, Any]] = []

    for name, display_name, state_code, svc_type in raw:
        kind = _type_code_to_kind(svc_type)
        if type_filter == "win32" and kind != "win32":
            continue
        if type_filter == "driver" and kind != "driver":
            continue

        state = STATE_MAP.get(state_code, "未知")
        if state_filter == "running" and state != "运行中":
            continue
        if state_filter == "stopped" and state != "已停止":
            continue
        if state_filter == "other" and state in ("运行中", "已停止"):
            continue

        if keyword:
            hay = f"{name} {display_name}".lower()
            if keyword not in hay:
                continue

        result.append(
            {
                "name": name,
                "display_name": display_name,
                "state": state,
                "kind": kind,
                "start": -1,
                "start_label": "—",
            }
        )

    result.sort(key=lambda s: s["name"].lower())
    return result


def _wait_for_status(
    name: str,
    target_states: tuple[int, ...],
    max_wait_sec: float = 6.0,
    poll_interval: float = 0.25,
) -> int:
    """轮询等待服务达到目标状态之一，超时则返回当前实际状态。"""
    deadline = time.time() + max_wait_sec
    last_status = 0
    while time.time() < deadline:
        try:
            status = win32serviceutil.QueryServiceStatus(name)
            last_status = status[1]
            if last_status in target_states:
                return last_status
        except Exception:
            break
        time.sleep(poll_interval)
    return last_status


def control_service(name: str, action: str) -> dict[str, Any]:
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"未知操作: {action}")
    fn_name, label = CONTROL_ACTIONS[action]

    target_states_map = {
        "start": (win32service.SERVICE_RUNNING,),
        "stop": (win32service.SERVICE_STOPPED,),
        "restart": (win32service.SERVICE_RUNNING,),
        "pause": (win32service.SERVICE_PAUSED,),
        "continue": (win32service.SERVICE_RUNNING,),
    }

    with _scm_mutation_lock:
        getattr(win32serviceutil, fn_name)(name)
        expected = target_states_map.get(action, ())
        if expected:
            _wait_for_status(name, expected, max_wait_sec=5.0, poll_interval=0.2)
        info = get_service_info(name)
        return {"info": info, "label": label}


@contextmanager
def _service_handle(name: str, access: int) -> Iterator[Any]:
    hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    handle = None
    try:
        handle = win32service.OpenService(hscm, name, access)
        yield handle
    finally:
        if handle is not None:
            win32service.CloseServiceHandle(handle)
        win32service.CloseServiceHandle(hscm)


def _write_image_path(key, image_path: str) -> None:
    existing_type = winreg.REG_SZ
    try:
        _value, existing_type = winreg.QueryValueEx(key, "ImagePath")
    except FileNotFoundError:
        pass
    if "%" in image_path:
        value_type = winreg.REG_EXPAND_SZ
    elif existing_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        value_type = existing_type
    else:
        value_type = winreg.REG_SZ
    winreg.SetValueEx(key, "ImagePath", 0, value_type, image_path)


def _write_environment(key, lines: list[str]) -> None:
    if not lines:
        try:
            winreg.DeleteValue(key, "Environment")
        except FileNotFoundError:
            pass
        return
    winreg.SetValueEx(key, "Environment", 0, winreg.REG_MULTI_SZ, lines)


def _write_working_directory(key, directory: str) -> None:
    if not directory:
        try:
            winreg.DeleteValue(key, "AppDirectory")
        except FileNotFoundError:
            pass
        return
    winreg.SetValueEx(key, "AppDirectory", 0, winreg.REG_SZ, directory)


def grant_service_logon_right(account: str) -> None:
    policy = win32security.LsaOpenPolicy(
        None,
        win32security.POLICY_LOOKUP_NAMES | win32security.POLICY_CREATE_ACCOUNT,
    )
    try:
        sid, _domain, _acc_type = win32security.LookupAccountName(None, account)
        win32security.LsaAddAccountRights(policy, sid, ["SeServiceLogonRight"])
    finally:
        win32security.LsaClose(policy)


def _apply_account(name: str, account_type: str, username: str, password: str, grant: bool) -> None:
    kind = (account_type or "local").strip().lower()
    canonical = canonical_account(kind, username)
    current_name = ""
    try:
        path = rf"SYSTEM\CurrentControlSet\Services\{name}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            current_name = str(_reg_query(key, "ObjectName", "") or "")
    except OSError:
        current_name = ""
    current_type, current_label = classify_account(current_name)
    same_account = current_type == kind and (
        current_type != "custom" or current_label.lower() == canonical.lower()
    )
    if kind == "custom":
        if password:
            secret: str | None = password
        elif same_account:
            secret = None
        else:
            raise ValueError("更改登录账户时需要填写密码")
    else:
        secret = ""
    if same_account and not password:
        if grant and kind == "custom":
            grant_service_logon_right(canonical)
        return
    if grant and kind == "custom":
        grant_service_logon_right(canonical)
    access = win32service.SERVICE_CHANGE_CONFIG | win32service.SERVICE_QUERY_CONFIG
    with _service_handle(name, access) as handle:
        win32service.ChangeServiceConfig(
            handle,
            win32service.SERVICE_NO_CHANGE,
            win32service.SERVICE_NO_CHANGE,
            win32service.SERVICE_NO_CHANGE,
            None,
            None,
            False,
            None,
            canonical,
            secret,
            None,
        )


def _reset_equivalent(left: int, right: int) -> bool:
    def norm(value: int) -> int:
        return -1 if value < 0 else value

    return norm(int(left)) == norm(int(right))


def _actions_equivalent(left: Any, right: Any) -> bool:
    def slots(items: Any, *, from_scm: bool) -> tuple[tuple[int, int], ...]:
        parsed: list[tuple[int, int]] = []
        for item in items or ():
            delay = _as_int(item[1], 0)
            if from_scm:
                delay = int(round(delay / 1000)) * 1000
            parsed.append((_as_int(item[0], 0), delay))
        while len(parsed) < _FAILURE_SLOTS:
            parsed.append((win32service.SC_ACTION_NONE, 0))
        return tuple(parsed)

    return slots(left, from_scm=True) == slots(right, from_scm=False)


def _apply_failure(name: str, failure: dict[str, Any]) -> None:
    access = win32service.SERVICE_CHANGE_CONFIG | win32service.SERVICE_QUERY_CONFIG
    with _service_handle(name, access) as handle:
        existing = None
        try:
            existing = win32service.QueryServiceConfig2(
                handle, win32service.SERVICE_CONFIG_FAILURE_ACTIONS
            )
        except win32service.error:
            existing = None
        current = existing if isinstance(existing, dict) else {}
        payload = failure_to_scm(failure, current)
        if _reset_equivalent(_as_int(current.get("ResetPeriod"), 0), payload["ResetPeriod"]) and _actions_equivalent(
            current.get("Actions"), payload["Actions"]
        ):
            return
        win32service.ChangeServiceConfig2(
            handle,
            win32service.SERVICE_CONFIG_FAILURE_ACTIONS,
            payload,
        )


def _payload_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("配置格式无效")
    return payload


def _registry_config(name: str, payload: dict[str, Any], *, require_executable: bool) -> None:
    start = payload.get("start")
    display_name = str(payload.get("display_name") or "")
    description = str(payload.get("description") or "")
    executable = str(payload.get("executable") or "")
    arguments = str(payload.get("arguments") or "")
    working_directory = str(payload.get("working_directory") or "").strip().strip('"')
    environments = normalize_environments(payload.get("environments"))
    image_path = join_image_path(executable, arguments)

    if require_executable and not image_path:
        raise ValueError("请填写可执行文件")
    if start is not None and _as_int(start, -1) not in (0, 1, 2, 3, 4):
        raise ValueError("启动类型无效")

    path = rf"SYSTEM\CurrentControlSet\Services\{name}"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        if start is not None and _as_int(start, -1) in (0, 1, 2, 3, 4):
            winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, _as_int(start))
        if display_name.strip():
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name.strip())
        if description.strip():
            winreg.SetValueEx(key, "Description", 0, winreg.REG_SZ, description.strip())
        if image_path:
            _write_image_path(key, image_path)
        _write_working_directory(key, working_directory)
        _write_environment(key, environments)
        if "delayed_auto" in payload:
            da = 1 if _as_bool(payload["delayed_auto"]) else 0
            winreg.SetValueEx(key, "DelayedAutostart", 0, winreg.REG_DWORD, da)
        if "dependencies" in payload:
            deps = payload["dependencies"]
            if isinstance(deps, str):
                deps = [d.strip() for d in deps.split(",") if d.strip()]
            if isinstance(deps, list):
                winreg.SetValueEx(key, "DependOnService", 0, winreg.REG_MULTI_SZ, deps)


def _apply_scm_options(name: str, payload: dict[str, Any], *, kind: str) -> None:
    if kind == "driver":
        return
    if _as_bool(payload.get("apply_account", True)):
        _apply_account(
            name,
            str(payload.get("account_type") or "local"),
            str(payload.get("username") or ""),
            str(payload.get("password") or ""),
            _as_bool(payload.get("grant_logon")),
        )
    if _as_bool(payload.get("apply_failure", True)):
        failure = payload.get("failure")
        if not isinstance(failure, dict):
            raise ValueError("失败恢复格式无效")
        _apply_failure(name, failure)


def _validate_config(data: dict[str, Any], *, require_executable: bool, check_account: bool, check_failure: bool) -> None:
    normalize_environments(data.get("environments"))
    image_path = join_image_path(str(data.get("executable") or ""), str(data.get("arguments") or ""))
    if require_executable and not image_path:
        raise ValueError("请填写可执行文件")
    start = data.get("start")
    if start is not None and _as_int(start, -1) not in (0, 1, 2, 3, 4):
        raise ValueError("启动类型无效")
    if check_account:
        canonical_account(str(data.get("account_type") or "local"), str(data.get("username") or ""))
    if check_failure:
        failure = data.get("failure")
        if not isinstance(failure, dict):
            raise ValueError("失败恢复格式无效")
        failure_to_scm(failure, None)


def save_config(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _scm_mutation_lock:
        data = _payload_dict(payload)
        kind = _service_kind(name)
        check_scm = kind != "driver"
        _validate_config(
            data,
            require_executable=False,
            check_account=check_scm and _as_bool(data.get("apply_account", True)),
            check_failure=check_scm and _as_bool(data.get("apply_failure", True)),
        )
        _registry_config(name, data, require_executable=False)
        _apply_scm_options(name, data, kind=kind)
        return get_service_info(name)


def add_service(payload: dict[str, Any]) -> None:
    with _scm_mutation_lock:
        data = _payload_dict(payload)
        name = str(data.get("name") or "").strip()
        display_name = str(data.get("display_name") or "").strip()
        description = str(data.get("description") or "").strip()
        executable = str(data.get("executable") or "").strip()
        arguments = str(data.get("arguments") or "")
        start = _as_int(data.get("start"), 2)
        if start not in (2, 3, 4):
            start = 2
        image_path = join_image_path(executable, arguments)
        if not name or not display_name or not description or not image_path:
            raise ValueError("服务名、显示名称、描述和可执行文件均为必填")
        _validate_config(data, require_executable=True, check_account=True, check_failure=True)

        exe = executable.strip().strip('"')
        abs_exe = os.path.abspath(_expand(exe))
        if not os.path.exists(abs_exe):
            raise FileNotFoundError(f"可执行文件不存在: {abs_exe}")

        account = canonical_account(str(data.get("account_type") or "local"), str(data.get("username") or ""))
        account_type = str(data.get("account_type") or "local").strip().lower()
        password = str(data.get("password") or "")
        if account_type == "custom" and not password:
            raise ValueError("自定义账户需要填写密码")
        if _as_bool(data.get("grant_logon")) and account_type == "custom":
            grant_service_logon_right(account)

        start_map = {
            2: win32service.SERVICE_AUTO_START,
            3: win32service.SERVICE_DEMAND_START,
            4: win32service.SERVICE_DISABLED,
        }
        secret = password if account_type == "custom" else ""

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
                account,
                secret,
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

        delayed_auto = _as_bool(data.get("delayed_auto", False))
        deps_raw = data.get("dependencies", [])
        if delayed_auto or deps_raw:
            reg_path = rf"SYSTEM\CurrentControlSet\Services\{name}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_SET_VALUE) as rk:
                if delayed_auto:
                    winreg.SetValueEx(rk, "DelayedAutostart", 0, winreg.REG_DWORD, 1)
                if deps_raw:
                    if isinstance(deps_raw, str):
                        deps_raw = [d.strip() for d in deps_raw.split(",") if d.strip()]
                    if isinstance(deps_raw, list) and deps_raw:
                        winreg.SetValueEx(rk, "DependOnService", 0, winreg.REG_MULTI_SZ, deps_raw)

        data = {**data, "apply_account": False, "apply_failure": True}
        _registry_config(name, data, require_executable=False)
        _apply_scm_options(name, data, kind="win32")


def delete_service(name: str) -> str:
    """Delete service. Returns human message."""
    with _scm_mutation_lock:
        try:
            status = win32serviceutil.QueryServiceStatus(name)
            if status[1] == win32service.SERVICE_RUNNING:
                win32serviceutil.StopService(name)
                _wait_for_status(name, (win32service.SERVICE_STOPPED,), max_wait_sec=8.0, poll_interval=0.25)
        except win32service.error:
            pass

        win32serviceutil.RemoveService(name)
        try:
            winreg.DeleteKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SYSTEM\CurrentControlSet\Services\{name}",
            )
        except FileNotFoundError:
            pass
        return f"服务 {name} 已删除"


def map_winerror(exc: BaseException) -> tuple[str, str | None]:
    """Map common Windows errors to (message, code)."""
    if isinstance(exc, PermissionError):
        return "权限不足，请以管理员身份运行", "permission"
    if isinstance(exc, pywintypes.error):
        code = exc.winerror
        if code == 5:
            return "权限不足，请以管理员身份运行", "permission"
        if code == 87:
            return "参数无效", None
        if code == 1056:
            return "服务已在运行", None
        if code == 1057:
            return "登录账户无效", None
        if code == 1060:
            return "服务未安装", None
        if code == 1062:
            return "服务尚未启动", None
        if code == 1069:
            return "服务账户登录失败，请检查用户名、密码和“作为服务登录”权限", None
        if code == 1072:
            return "已标记删除，重启后完全移除", "pending_delete"
        if code == 1073:
            return "服务已存在", None
        if code == 1314:
            return "缺少修改账户权限所需的特权", "permission"
        if code == 1332:
            return "找不到该账户", None
        return exc.strerror or str(exc), None
    if isinstance(exc, FileNotFoundError):
        return str(exc), None
    if isinstance(exc, ValueError):
        return str(exc), None
    return str(exc), None
