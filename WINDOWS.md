# Windows 使用与分支交付

支持 Windows 10/11、Windows PowerShell 5.1、Python 3.9+；源码编译需要 Go 1.26.0+。源码、测试和安装入口均在本仓库内。

## 使用

1. 阅读 `ccam/README.md`。按需使用 `ccam/install.ps1` 安装上游二进制，或在 `ccam` 目录执行 `go build -pgo=off -o ccam.exe ./cmd/ccam`，将输出目录加入 PATH。
2. 新开 PowerShell，执行 `ccam version`、`ccam --help`，本人通过 `ccam add <账号名>` 登录。
3. 在包根执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\ccam-board\start-windows.ps1`。浏览器访问 `http://127.0.0.1:47321`，Ctrl+C 停止。看板只监听本机；“打开”使用独立控制台和正常权限确认。
4. 检测器为可选独立工具。先读 `claude-switcher/windows/README.md`，再由本人在管理员 Windows PowerShell 执行 `& .\claude-switcher\windows\install.ps1`。安装会修改时区、IPv6 绑定并注册保镖任务，开发验证不执行这一步。

`ccam-board/app.py` 是 Linux GTK 入口；Windows 使用 `server.py`。状态目录支持 `CCAM_HOME` 和 `XDG_STATE_HOME`。会话数只统计 ccam 注册且进程创建时间吻合的会话，不是任意 Claude 进程数。

## 开发验证

本机实际启动、浏览器和 WPF 检查结果见 [WINDOWS-TEST-REPORT.md](WINDOWS-TEST-REPORT.md)，包含实测发现的连接中断和端口复用修复。

在包根运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-windows.ps1
Set-Location .\ccam
go test ./...
go vet ./...
go build -pgo=off -o ccam.exe ./cmd/ccam
```

验证脚本不安装工具、不登录账号、不修改系统配置。测试以临时目录、模拟子进程和 Windows 进程 API 验证路径、编码、会话标记和启动错误。管理员安装、WPF 交互、真实账号额度和 Claude 启动仍需实际环境验收；尚未测量整个项目 80% 覆盖率。

## 推送分支

开发时从仓库当前主分支建立独立分支，按以下步骤检查并提交改动：

```powershell
git status --short
git diff
git switch -c my-windows-changes
# 审核后仅暂存本次改动，再提交
git add -- .gitattributes .gitignore .github WINDOWS.md WINDOWS-TEST-REPORT.md verify-windows.ps1 README.md START-HERE.md AGENT-GUIDE.md SHA256SUMS ccam/install.ps1 ccam/tests ccam-board claude-switcher/windows/install.ps1 claude-switcher/windows/README.md claude-switcher/windows/tests
git commit -m "Add Windows dashboard support and harden Windows installers"
git push -u origin my-windows-changes
```

不要提交账号配置、token、安装报告或本机构建产物。Windows CI 会运行脚本检查、回归测试和 Go 构建；完整实测的覆盖范围以 WINDOWS-TEST-REPORT.md 为准。
