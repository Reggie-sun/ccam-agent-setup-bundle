# VPS Update — v6

v6 更新管理页：未开始计时、每行额度更新时间、按优先级排序并标出轮换指针、本机 ccam 别名、「分散使用 / 用完再切」开关；另附可选的 cu 1.4.0 补丁（额度更新时间与失败请求日志）。cu 不打补丁时管理页其余功能照常，只是不显示额度更新时间。详见 [VPS-RELAY.md](VPS-RELAY.md) 的 v6 章节。

## v5

新增后台自动登录检测与按需续期、邮箱账号管理，以及本机 Claude 默认连接 VPS 的配置工具。先读 [VPS-RELAY.md](VPS-RELAY.md) 和 [client/DEFAULT-VPS.md](client/DEFAULT-VPS.md)。旧 Windows/Linux 本机工具与数据迁移说明保留如下；VPS 与本机账号状态独立。

---

# Claude Tools Bundle

开源：ccam 多账号管理、Claude 账号额度看板、claude检测器及 Agent 安装配置指引。由你自己的 Agent 按说明安装与验证。

从 START-HERE.md 开始。AGENT-GUIDE.md 是 Agent 的执行入口；每个工具的 README 与 LICENSE 随包保留。包内是源码和安装材料，不含任何账号、订阅、代理节点或个人配置，不包含预编译 ccam；可使用包内安装器获取上游构建，或按说明从源码构建。

Windows 使用说明和开发验证见 [WINDOWS.md](WINDOWS.md)。ccam 提供 Linux、macOS、Windows 入口；额度看板已增加 Windows 网页启动、会话检测及账号终端启动。检测器 Windows WPF 版仍为 Beta；真实账号和管理员安装尚需验收，不承诺检测结果能保证账号不受限制。已完成的本机验证与限制见 [实测报告](WINDOWS-TEST-REPORT.md)。

## 许可证

仓库根目录及 ccam-board、文档按 MIT 发布；`ccam/`（上游 jhyunwoo）与 `claude-switcher/` 保留各自目录内的 MIT LICENSE。
