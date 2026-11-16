# VPS Update — v6

v6 更新管理页：未开始计时、每行额度更新时间、按优先级排序并标出轮换指针、本机 ccam 别名、「分散使用 / 用完再切」开关；另附可选的 cu 1.4.0 补丁（额度更新时间与失败请求日志）。cu 不打补丁时管理页其余功能照常，只是不显示额度更新时间。详见 [VPS-RELAY.md](VPS-RELAY.md) 的 v6 章节。

## v5

新增后台自动登录检测与按需续期、邮箱账号管理，以及本机 Claude 默认连接 VPS 的配置工具。先读 [VPS-RELAY.md](VPS-RELAY.md) 和 [client/DEFAULT-VPS.md](client/DEFAULT-VPS.md)。旧 Windows/Linux 本机工具与数据迁移说明保留如下；VPS 与本机账号状态独立。

---

# Start Here

克隆或下载本仓库。把整个目录交给你电脑上的 AI Agent，发送下面这段话。

> 请先阅读本目录的 AGENT-GUIDE.md，识别我的系统和现有安装，按适用的平台说明为我安装配置 ccam、账号额度看板和 claude检测器。保留已有配置，常规检查、安装和验证由你完成。需要我本人登录或授权系统权限时告诉我；不要索取或外传我的密码、Cookie、token。完成后生成 INSTALLATION-REPORT.md，写明实际路径、启动命令、验证结果和未完成项目。遇到无法解决的问题，再整理脱敏错误供我提交 GitHub Issue。

自备 Claude 账号、相应订阅和可用网络环境。账号登录及系统权限确认仍需本人操作；“Agent 自动安装”不代表跳过这些授权。Windows 额度看板使用网页入口，步骤见 WINDOWS.md；macOS 的会话检测和启动按钮仍有限制。优先完成可验证的部分，具体差异见 AGENT-GUIDE.md。

遇到问题请在 GitHub Issues 提交，附脱敏错误。不要发送登录凭据或完整账号清单。
