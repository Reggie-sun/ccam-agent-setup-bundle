# Claude Tools Bundle

套装包含三个本机工具：**ccam** 用于在终端选择 Claude Code 账号，**CC Switcher（Claude 切换器）** 用于在桌面切换账号的本地登录状态，**额度看板** 用于查看每个账号的剩余额度。如果你有自己的 VPS，还可以把账号集中放在服务器上，让多台电脑连接同一个中转。

## Download

**用 npx 下载**：在准备存放工具的目录打开终端或 PowerShell，运行下面两行。需要已安装 Node.js 和 npm。第一行用下载工具 `giget` 获取本仓库的源码和安装脚本，第二行进入新目录。

```sh
npx --yes giget@3.3.1 "gh:Reggie-sun/ccam-agent-setup-bundle#main" claude-tools
cd claude-tools
```

没有 Node.js 也可以[直接下载 ZIP](https://github.com/Reggie-sun/ccam-agent-setup-bundle/archive/refs/heads/main.zip)。解压后打开含有 `AGENT-GUIDE.md` 的目录。两种方式下载的是同一分支，接下来选择本机使用或 VPS 部署即可。

## Local quick start

准备好 Claude Code 和你自己的 Claude 账号。以下命令均从刚下载的工具目录运行。

**1. 安装账号管理器。** 按系统选择一条命令，安装器会下载并校验 ccam 的上游构建。

| 系统 | 安装命令 |
| --- | --- |
| Linux / macOS | `sh ./ccam/install.sh` |
| Windows PowerShell | `powershell -NoProfile -ExecutionPolicy Bypass -File .\ccam\install.ps1` |

**2. 添加账号并启动 Claude。** 安装后新开终端，依次运行下面的命令。`work` 是你起的本机别名。添加账号时，在浏览器中自行完成官方登录。

```sh
ccam version
ccam add work
ccam list
ccam run work
```

需要第二个账号时，运行 `ccam add personal`，之后用 `ccam run personal` 启动。各账号独立保存登录状态，可按组共享设置、MCP 和会话历史。更多命令见 [ccam 使用手册](ccam/README.md)。

**3. 打开额度看板。** 回到工具目录，在另一个终端运行以下命令。看板需要 Python 3.9 或更高版本。

| 系统 | 启动命令 |
| --- | --- |
| Linux / macOS | `python3 ccam-board/server.py --open-browser` |
| Windows PowerShell | `powershell -NoProfile -ExecutionPolicy Bypass -File .\ccam-board\start-windows.ps1` |

浏览器会打开 `http://127.0.0.1:47321`。用它查看各账号额度，结束时在运行看板的终端按 `Ctrl+C`。macOS 上请从终端启动账号会话。详见[本机看板说明](ccam-board/README.md)。

## CC Switcher（Claude 切换器）

上面的 `npx` 命令已经把 CC Switcher 下载到了 `claude-switcher/`。想在图形界面切换账号，可以直接从这里开始。macOS 和 Windows 版管理 Claude 桌面端的账号环境，Linux 版管理浏览器登录状态。

| 平台 | 从套装根目录开始 |
| --- | --- |
| macOS | 双击 `claude-switcher/installer/安装.command`。安装后打开切换器，点击菜单栏的盾牌图标进入账号界面。[macOS 使用说明](claude-switcher/README.md) |
| Windows（Beta） | 在管理员 PowerShell 中运行 `& .\claude-switcher\windows\install.ps1`，完成后打开桌面「Claude切换器」。安装涉及系统时区、网络设置和后台任务，请先阅读 [Windows 安装说明](claude-switcher/windows/README.md)。 |
| Linux（Beta） | 安装 GTK 3 和 PyGObject 后，运行 `/usr/bin/python3 claude-switcher/linux/desktop.py` 打开界面。桌面快捷方式和后台保护的安装步骤见 [Linux 使用说明](claude-switcher/linux/README.md)。 |

**第一次切换**：先保存当前工作，在切换器中选择目标账号槽位；首次使用该槽位时，由本人完成 Claude 登录。以后选择同一槽位即可载入它保存的登录状态。Linux 上先完全退出受管理的浏览器，切换完成后手动重新打开。切换器还提供启动前的网络环境检查，以及安装后台组件后的断线保护。

## VPS relay

有 Linux VPS 时，可以在自己的服务器上运行管理页。先按 [VPS 部署指南](VPS-RELAY.md) 配置中转和管理入口，再在网页添加账号并完成官方授权。最后按[客户端配置指南](client/DEFAULT-VPS.md)把本机 Claude 指向自己的 VPS。

| 在管理页可以做什么 | 使用方式 |
| --- | --- |
| 查额度和重置时间 | 分别查看每个账号的 5 小时和每周剩余额度。点击时间轴上的账号，查看完整邮箱和精确重置时间。 |
| 等待额度恢复 | 页面可见时每 15 秒读取后台快照。安装指南中的 cu 补丁后，后台会优先查询已到重置点的账号；受限账号上次读取满 60 秒后可再次查询，实际时间受排队和上游限流影响。 |
| 管理账号和分配请求 | 启用或停用账号、调整优先级，切换「分散使用」或「用完再切」。可导入本机 ccam 别名，方便对照。 |
| 处理登录和断流 | 配置后台登录检测与按需续期；需要本人授权时从网页重新登录。安装诊断补丁后，可用请求 ID 在 `stream_end.jsonl` 中查中转是否收到了结束帧、是否成功写向下游。 |

## Other tools and guides

- **让 Agent 帮你安装**：用你的编码 Agent 打开工具目录，告诉它「先读 AGENT-GUIDE.md，按我的系统安装本机账号管理器和额度看板，保留现有配置，完成后告诉我怎么启动。」也可以使用[完整安装提示词](START-HERE.md)。
- **许可证**：套装使用 [MIT 许可证](LICENSE)。ccam 和 CC Switcher 保留各自的[上游许可证](ccam/LICENSE)和[项目许可证](claude-switcher/LICENSE)。
