# 账号额度看板

Python 3.9+ 标准库网页服务，无需安装 GTK 或 pip 依赖。先确保 `ccam.exe`（Windows）或 `ccam` 在 PATH 中，并由本人完成账号登录。

Windows PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-windows.ps1
```

也可运行 `python -X utf8 server.py --open-browser`。服务仅监听 `http://127.0.0.1:47321`，终端按 Ctrl+C 停止。端口冲突时先检查已有服务，不自动终止进程。页面“打开”在 Windows 新控制台运行 `ccam run <账号>`，保持正常权限确认；不会执行账号快捷方式脚本。

状态路径遵循 ccam 的 `CCAM_HOME` 和绝对路径 `XDG_STATE_HOME`，否则使用用户目录 `.local/state/ccam`。Windows 通过进程创建时间校验 ccam 注册的会话标记，Linux 使用 `/proc`，其他平台返回未知（null）；这不是所有手动启动 Claude 进程的总数。网页额度读取、刷新、事件推送可跨平台使用；macOS 的打开按钮返回明确错误，需在终端手动启动。`app.py` 仍是 Linux GTK 入口。

验证：`python -m unittest discover -s tests -v`。测试使用临时目录和模拟命令，不登录账号，不改本机配置。
