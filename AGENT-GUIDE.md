# Agent Guide

## Mission

你是用户本机的安装 Agent。自主完成环境识别、适用工具安装、配置及验证；正常流程不要求联系店主。登录和管理员授权由买家本人完成；遇到不能解决的错误，保留现场并给出脱敏记录供买家联系售后。先读本文件，再读各工具的平台 README；不要把检测器根 AGENTS.md 的 macOS 步骤套用到 Linux/Windows。

## Inspect And Preserve

1. 识别操作系统、CPU 架构、Python、Claude Code、已有 ccam 的路径和版本。选择买家可写的安装目录，保留包内相对结构，不写死卖家的路径。
2. 校验 SHA256SUMS（包根运行 `sha256sum -c SHA256SUMS`；其他系统可由 Agent 使用 Python hashlib 逐行核验）。这证明解压文件未变，不代替上游发布签名。
3. 已有安装先核对，修改前备份本次会修改的文件；不覆盖未知配置、账号登录态或代理配置。任何账号登录都由买家完成，不向聊天或工单输出密码、Cookie、token 或 OAuth 回调。
4. 包含 ccam 源码及安装器、ccam-board 源码、claude-switcher 对应平台安装材料、许可证。缺文件时先重新下载，仍缺失才联系售后，不猜测路径。

## Install ccam

阅读 `ccam/README.md` 和本机 `ccam --help`。有可用安装则不盲目更新。没有时检查包内 `ccam/install.sh`（Linux/macOS）或 `ccam/install.ps1`（Windows）的下载与校验逻辑，再执行对应入口；它们需要联网获取上游构建，不是离线二进制安装包。

需要精确使用包内源码时，读取 go.mod 的 Go 版本要求，在 ccam 目录执行 `go build -pgo=off -o ccam ./cmd/ccam`（Windows 输出 ccam.exe），再放入买家选定的 PATH 目录。依赖下载需要联网。不要复制卖家机器上的二进制或账号目录。

```bash
ccam version
ccam --help
ccam add <买家自选账号名称>
ccam list --json
ccam usage --json
ccam pick --dry-run --json
ccam use <name>
ccam run <name>
```

`add` 引导买家本人登录；不要代填秘密。`use` 设置后续启动默认账号，不改变正在运行的会话；`run` 使用正常权限确认。额度输出只在本机查看，对外报告需脱敏。额度不可用时核对 `ccam usage --help` 和错误状态，不连续高频绕过缓存；缓存或未知状态不当作实时成功。需要重新登录时按 `ccam login --help` 引导本人操作。

## Install Dashboard

将 `ccam-board/` 放入选定的持久目录，确保其进程 PATH 能找到 ccam。在该目录执行：

```bash
python3 server.py
```

访问 `http://127.0.0.1:47321`。仅绑定本机，不能改成公网地址。端口已占用时先确认是否已有看板，不盲目杀进程；只停止本次启动的进程。

Linux GTK 桌面入口是 `/usr/bin/python3 app.py`，需要 PyGObject、GTK 4、Adwaita 1、WebKit 6。根据发行版检查现有系统包；安装系统依赖需要买家授权。优先采用网页入口，无需为了看板额外安装整个桌面运行时。

交付副本已移除默认跳过 Claude 权限确认和关闭 WebKit 沙盒的设置；不要为启动成功重新加入这些设置。WebKit 沙盒错误时先用网页入口，按系统依赖排障。`ccam-board` 不是保证存在的命令；为买家创建快捷方式时指向实际目录，并在报告中记录。

当前看板含 Linux `/proc` 活跃会话检测与 `gnome-terminal` 启动逻辑，**macOS/Windows 不能视为完整可用**。可先验证网页版额度显示；启动账号使用 `ccam run <name>`。无法验证的活跃会话信息和按钮必须明确标注不可用，不伪造成功，也不要求买家先联系卖家才能继续其余部分。不要自动迁移或重写大量平台代码。

## Install Detector

| 系统 | 阅读入口 | 安装入口及限制 |
| --- | --- | --- |
| macOS | claude-switcher/README.md、AGENTS.md | `installer/安装.command`；需要 Claude 桌面版、适用代理环境及 Xcode 命令行工具；系统改动需本人授权 |
| Linux | claude-switcher/linux/README.md | `bash linux/install.sh` 为核心安装；`python3 linux/install-desktop.py` 为 GTK 3 快捷方式安装，二者不同；依赖 systemd，Beta |
| Windows | claude-switcher/windows/README.md | `windows/install.ps1`，依 README 使用 PowerShell 管理员入口；WPF 版本为 Beta |

先检查脚本将修改的网络、时区、IPv6 与后台服务，使用买家实际环境的值并取得系统授权，不复制卖家的节点、地区值或私有配置。Linux 检测器交换的是整个浏览器 profile（可能包含其他网站登录态），不是 Claude Code 账号；日常 Claude Code 切换用 ccam，不自动触发浏览器交换。

检测入口：Linux 在检测器目录运行 `bash linux/claude-switch.sh checkdetail`；macOS 使用安装后的 `~/.local/bin/claude-switch.sh checkdetail`；Windows 按 README 用 PowerShell 执行 `windows/claude-switch.ps1 checkdetail`。检测可能产生本地日志和网络请求；探测可达不等于登录或订阅可用。不为“全部变绿”盲目运行 fix、更新或快照导入。

## Verify And Report

安装后在包外实际安装目录生成 INSTALLATION-REPORT.md，写入：系统与架构；三个工具版本和实际路径；启动命令与快捷方式；修改及备份位置；通过项目；失败项与原因；下一步。不要包含凭据。

验收逐项执行：

- ccam 版本/帮助正常；买家授权账号可见，额度成功或清楚记录未取得数据的原因。
- 由买家确认一次正常权限启动，不运行无关收费任务或消耗大量额度。
- 看板页面打开、额度与 CLI 一致；缓存时间清楚，未知状态不冒充成功。
- 检测器展示真实检查结果，失败项有原因；不能保证账号不受限制。
- 后续 Agent 能仅凭 INSTALLATION-REPORT.md 找到工具并进行额度查询和正常启动。

## Troubleshooting

先定位具体失败步骤，保留原始状态与备份。缺命令查 PATH 和依赖；登录过期让本人重新登录；端口占用识别已有服务；网络检查失败核对买家实际网络；遇到待恢复快照不得重复交换或删除。先完成可以独立验证的项目。

仍无法解决时，输出系统、工具版本、失败命令、脱敏错误与已尝试操作，由用户到 GitHub Issues 反馈。不得声称未实测的平台安装已经成功。
