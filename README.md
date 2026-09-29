# Windows 服务管理器

基于 **pywebview + 现代 Web UI** 的 Windows 服务管理工具。

## 功能

- 快速浏览 / 搜索服务（SCM 枚举，毫秒级加载）
- 按状态（运行中 / 已停止 / 其他）与类型（Win32 / 驱动）筛选
- 查看详情：状态、启动类型、显示名称、描述、路径、登录账户、工作目录、失败恢复、环境变量
- 控制服务：启动、停止、重启、暂停、继续
- 编辑配置并保存：启动类型、显示名、描述、可执行文件与参数、工作目录、登录账户、失败恢复、环境变量
- 创建 / 删除服务（需确认）。新建时可分开填写可执行文件、参数和工作目录，并按文件名自动填服务名
- 复制可执行路径、浅色 / 深色主题

> 启动时会自动请求 **管理员权限**（UAC）。打包版 exe 也已嵌入 `requireAdministrator` 清单。

## 运行

```bash
uv sync
uv run python main.py
```

或：

```bash
python main.py
```

## 项目结构

```
main.py                 # 入口
svc_edit/
  app.py                # 窗口启动
  api.py                # pywebview JS API
  services.py           # 服务 / 注册表逻辑
  constants.py
web/                    # 前端
```

## 打包

```bash
pyinstaller app.spec
```

产物：`dist/svc-edit.exe`

## 快捷键

| 快捷键 | 作用 |
|--------|------|
| / 或 Ctrl+F | 快速聚焦搜索过滤框 |
| F5 | 刷新服务列表 |
| ↑ / ↓ | 在列表中切换选定服务 |
| Enter | 确认选定并切换至概览标签页 |

## 技术栈

- Python + pywin32
- pywebview
- HTML / CSS / JS
