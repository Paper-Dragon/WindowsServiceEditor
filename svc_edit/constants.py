from __future__ import annotations

STATE_MAP = {
    1: "已停止",
    2: "启动中",
    3: "停止中",
    4: "运行中",
    5: "继续中",
    6: "暂停中",
    7: "已暂停",
}

START_TYPE_MAP = {
    0: "引导",
    1: "系统",
    2: "自动",
    3: "手动",
    4: "禁用",
}

START_TYPE_OPTIONS = [
    {"value": 2, "label": "自动"},
    {"value": 3, "label": "手动"},
    {"value": 4, "label": "禁用"},
]

CONTROL_ACTIONS = {
    "start": ("StartService", "启动"),
    "stop": ("StopService", "停止"),
    "restart": ("RestartService", "重启"),
    "pause": ("PauseService", "暂停"),
    "continue": ("ResumeService", "继续"),
}

# SCM service type filter labels for UI
TYPE_FILTERS = {
    "all": "全部类型",
    "win32": "Win32 服务",
    "driver": "驱动程序",
}
