# Claude 切换器 · Linux 版 (Beta)

> ⚠️ **本版本为 Beta**。核心逻辑移植自 macOS 稳定版,但 Linux 发行版差异大,遇到问题请到 [Issues](https://github.com/YOURSJEYO/claude-switcher/issues) 反馈。

## 先说最重要的设计差异(为什么是这些取舍)

1. **Claude 官方没有 Linux 桌面版**——Linux 上的使用形态是「网页版(浏览器) + Claude Code CLI」。因此:
   - **账号切换**切换的是**整个浏览器配置目录**(对 `~/.config/google-chrome` 或 `~/.config/chromium` 做快照交换)，包括所有网站登录态、历史和设置；切换前需要手动完全退出浏览器及后台进程，不切换 Claude Code 登录
   - **保镖**只终止 `claude` CLI 进程,**不会关你的浏览器**(网页版没有独立进程可归因,关浏览器体验太糟;网页端的保护由 TUN + 置顶规则兜底)
2. **简单桌面界面 + CLI**——GTK 3 窗口提供账号状态、安全检查和 A/B 切换；仍可使用终端交互菜单 (`claude-switch menu`)
3. **常驻服务用 systemd 用户单元**——免 root、登录自启、崩溃自动拉起

## 系统要求

> **注意**：Linux 版已支持多代理客户端与地区自适应（安装时按出口国家自动锁定 国家/时区/地区格式 三件套）。自动更新（`update` 命令 + 12 小时自动检查）目前仍仅 macOS 版提供，后续版本同步。

- 任一现代发行版(Ubuntu/Debian/Fedora/Arch 等,systemd)
- 以下任一代理客户端(TUN/虚拟网卡可用):
  - [Clash Verge](https://github.com/clash-verge-rev/clash-verge-rev)(体验最佳,置顶规则可自动写入)
  - mihomo / sing-box / V2RayA / NekoBox / Hiddify(置顶规则需手动,安装器会打印清单)
  - WireGuard(wg-quick 服务,启发式判定)
  - 其他客户端安装时选 `other`/`auto`
- python3、bash、curl
- 建议安装 `dnsutils`(Debian/Ubuntu)或 `bind-utils`(Fedora)以获得 dig;没有也能跑(自动回退 nslookup/getent)

## Desktop App

在仓库根目录运行：

```bash
/usr/bin/python3 linux/install-desktop.py
```

之后双击桌面「Claude 切换器」，或在应用列表搜索同名应用。也可以直接运行：

```bash
/usr/bin/python3 linux/desktop.py
```

依赖系统 Python、PyGObject 和 GTK 3；Ubuntu/Debian 缺失时安装 `python3-gi gir1.2-gtk-3.0`。桌面入口指向当前 checkout，移动仓库后需重新运行桌面安装脚本。

桌面安装仅创建用户快捷方式和缺失的 STUN 探针链接，不运行完整安装器、不修改代理规则、时区、IPv6 或后台服务。未完整安装时明确显示使用默认地区配置；已安装时安全检查读取现有核心配置。修复功能需要已安装核心，确认后在终端运行，以便输入 sudo 密码。

显式配置用户核心、应用地区和保镖（会双源核验出口国家，仍不修改系统语言、时区、IPv6 或代理规则）：

```bash
/usr/bin/python3 linux/install-desktop.py --configure --proxy-mode mihomo
```

它按出口国家选择地区格式，在用户目录生成 locale，只为此桌面进程及其检查子进程设置地区环境；不宣称已改变浏览器或系统地区。保镖只监控并终止当前用户名为 `claude` 的 CLI 进程，不关闭浏览器；已有配置文件会先备份到 `~/.claude-switcher/setup-backups/`。WebRTC 路由仍由实际代理配置的 owner 管理，配置此入口不会代写无效的 Clash Merge 文件。

账号切换使用仓库里的 Linux 核心，A/B 快照结构不变。切换前必须确认，浏览器未退出则拒绝操作；复制/替换失败保留原资料和恢复目录，并用 `~/.claude-switcher/switch.pending` 阻止盲目重试。看到「待恢复」时先检查 `current`、`current.new`、浏览器目录及 `.new`/`.previous`、账号快照及同名后缀，不要删除可能的唯一副本。正常切换后手动重新打开浏览器。此保护覆盖进程中断，不承诺断电后的事务持久一致性。

成功切换也保留原始浏览器目录为 `~/.config/google-chrome.retained.*`（Chromium 为 `chromium.retained.*`），避免检查之间短暂重开浏览器时丢失快照复制后的写入。副本不会自动轮替或清理，会占用磁盘；请在确认登录态和资料无误后手动清理。浏览器仍须手动保持退出状态，切换过程中不要重新启动。

检查结果缺失视为未通过；无 STUN 响应显示待确认，HTTP 有响应不代表登录或账号可用。桌面界面隐藏 IP 地址，原核心的本地历史记录仍保留。界面不会自动打开 Claude 网页或 CLI。

## Core Installation

```bash
# 先开好代理(TUN + 目标国家节点),安装器会校验出口国家不能是 CN
bash install.sh
```

- 安装器自动识别代理客户端与代理组名,并按出口 IP 锁定 国家/时区/地区格式
- 时区锁定和 IPv6 关闭需要一次 sudo 密码
- IPv6 关闭会持久化到 `/etc/sysctl.d/90-claude-switcher.conf`;恢复:删掉该文件后 `sudo sysctl -p`

## 使用

```bash
claude-switch menu          # 交互菜单(推荐)
claude-switch checkdetail   # 14 项逐项检查
claude-switch fix           # 一键修复
claude-switch switch A      # 切到账号A(切换浏览器登录态)
```

## 多代理客户端说明

安装器按以下顺序自动探测,都没有时进入手选菜单(默认 `auto`):

| 客户端 | PROXY_MODE | 在线判定 | 置顶规则 |
|---|---|---|---|
| Clash Verge | `clash-verge` | verge-mihomo/mihomo 进程 | 自动写入 Merge.yaml(智能合并,不动你的已有配置) |
| mihomo | `mihomo` | mihomo 进程 | 手动添加(安装器打印清单) |
| sing-box | `sing-box` | sing-box 进程 | 手动添加 |
| V2RayA | `v2raya` | v2raya 进程 | 手动添加 |
| NekoBox | `nekobox` | nekobox/nekoray 进程 | 手动添加 |
| Hiddify | `hiddify` | hiddify 进程 | 手动添加 |
| WireGuard | `wireguard` | 活动的 `wg-quick@*` 服务 | 手动添加(行为检测不适用,见下) |
| 其他/自动 | `other` / `auto` | 虚拟网卡启发式 / 任一命中 | 手动添加 |

## Beta 已知限制

- 设备指纹体检（`fingerprint`）与 Web 检测页为 macOS/浏览器能力，Linux 版暂未内置，可用 Web 版检查 Canvas/WebGL。
- **wireguard / other 为启发式判定**:WireGuard 没有 fake-ip,DNS 与规则置顶的行为检测(解析到 198.18.x.x)不适用,以虚拟网卡与出口国家校验为准
- **非 Clash Verge 客户端的置顶规则需手动维护**:安装器会打印 DOMAIN-SUFFIX 清单,`fix` 只检测不代写
- **未做多发行版 CI 覆盖**:脚本只依赖 bash + systemd + 常见命令,但各发行版(尤其 Fedora/Arch/openSUSE 等非 Debian 系)未经真机回归,遇到问题请到 [Issues](https://github.com/YOURSJEYO/claude-switcher/issues) 反馈

## 与 macOS/Windows 版的差异

| 项目 | Linux 版 |
|---|---|
| 账号切换对象 | 浏览器配置目录(Chrome/Chromium),不是独立 App |
| 保镖保护对象 | `claude` CLI 进程(不关浏览器) |
| 常驻方式 | systemd 用户服务(免 root) |
| 界面 | GTK 3 桌面 + 终端菜单 + CLI |
| 时区/IPv6 | timedatectl / sysctl(需 sudo) |

## Multi-browser Automation

当前默认 `claude` / `claude --yolo` 使用正常开发工具，可并发运行。浏览器不再与 Claude 账号固定绑定；通过 `chrome-browsers` MCP 的 `browser_profiles` 列出已有浏览器，再用 `browser_select(profile_id)` 选择要自动化的那个。每个 CLI 独立选择目标，选择失败不会继续控制上一个浏览器。CLI 保留原 Pro 登录、插件和历史；链接按系统默认浏览器处理。

Chrome Profile manager 的全部现有及新建 Chrome 使用中文界面、美国地区格式、`en-US,en` 请求语言、`America/New_York` 时区，默认拒绝网站定位。浏览器 HTTP 代理 `127.0.0.1:17987` 与 TUN 使用既有 Mihomo 正常分流；千川页面、上传和上报域名单独直连，避免被末尾的海外代理规则误接管。不再按 Chrome 进程强制全部走美国。原有 Claude 等海外服务规则保留，启动前检测的海外出口仍须为 US。运行中的 Chrome 保留页面和资料，关闭重开后应用新的启动参数。

自动化调试接口仅监听本机回环动态端口。选择目标时校验注册 profile、Chrome PID、监听 socket 和 DevTools endpoint，拒绝过期端口造成的账号误连。旧窗口若未带美国设置或调试参数，需先保存工作并重新打开该浏览器。按用户选择，Claude 使用当前用户的完整现有权限，包括 Docker 和既有 sudo 权限，不附加隔离或 `no_new_privs`；系统认证与 sudoers 规则保持原样。

实现与本机安装入口为 `install-multi-browser.py`、`claude-host.py`、`chrome-browsers.py`、`chrome-router.mjs`、`chrome-region.py`。安装器备份现有 manager、入口和 Preferences，再接入已有 manager；美国专用监听的持久生成逻辑由 `VPN/mihomo-runtime/browser_route.py` 维护，不在 Chrome 工具里另起代理。

## Optional Browser-only Assistant

Ubuntu 本机入口：桌面「Claude浏览器专用助手」，命令 `claude-browser-only`。保留正常 Claude 开发能力：Bash、读写文件、搜索、Agent、Skill、用户/项目插件和 hooks；浏览器操作固定使用专用 Chrome 的 `claude-browser` MCP。命令在 OS 隔离内免确认执行，不授予主机 sudo/root。CLI 通过 `BROWSER` / `xdg-open` 自动打开的 HTTP/HTTPS 链接绑定同一个隔离浏览器；在其他程序中手动点击终端链接仍由该程序处理。

以下是保留的可选隔离入口，不再作为默认 `claude`：`claude-browser-only` 运行独立 CLI，多个隔离会话共用一个专用浏览器及其标签页。浏览器由 `claude-private-browser.service` 用户服务持有，关闭某个 CLI 不会关闭浏览器；关闭浏览器窗口会停止该服务，下次使用此隔离入口自动重建。此入口内的浏览器连接固定，拒绝 `--chrome` 和命令行 `--mcp-config` 覆盖；普通 CLI 参数和开发工具保留。

开发文件：`~/vscode_folder` 中的项目可读写；从其他主目录下普通项目启动时，挂载该项目并保留原工作目录。CLI 的 `HOME` 仍是已有登录的专用目录，插件配置和账号资料不会被重置。挂载项目前按 `Local State`、`SingletonLock`、`prefs.js` 元数据识别浏览器资料根并遮蔽；从浏览器资料的子目录启动也会拒绝。实际用户主目录、其他默认浏览器目录与其托管备份不整体开放。客户端强制使用固定 SDK 的 seccomp helper 拦截 AF_UNIX socket，避免项目内的主机控制 socket 穿透 network namespace；socketpair 进程通信保留，开发服务使用隔离网络中的 TCP。主机回环/私网控制接口仍隔离，开发服务可在会话内启动供专用浏览器访问。

此专用入口固定美国配置：`en-US` 界面、请求语言与拼写，美国日期/数字格式，`America/New_York` 时区，默认拒绝网站定位。启动前要求切换器目标和实际出口国家都为 `US`；修改的是专用 profile 的这些设置，其他偏好及登录数据保留。网站账号资料中的地区由各网站管理。

浏览器显示在「Claude 专用浏览器」嵌套窗口中，可人工登录和完成验证，也可由 MCP 点击、填表和截图。订阅账号在专用 CLI 内运行 `/login`，选择 Claude Pro/Max，自动打开的授权页进入这个窗口；网页登录与 CLI 授权分别完成。专用 profile 同时只能由一个入口使用，启动前关闭原有「Claude 专用浏览器」窗口，其他日常浏览器保持运行。

实现入口为 `browser-only.py`、`claude-session.py` 和 `browser-only-runtime.mjs`。浏览器服务通过 `@anthropic-ai/sandbox-runtime@0.0.81` 创建受过滤的独立网络，客户端仅加入该网络，各自建立独立的 bubblewrap 文件系统/PID namespace 并挂载开发项目。服务不挂载主机项目；MCP 将允许上传的项目文件复制到共享专用工作目录供 Chrome 读取。外层从空文件系统开始，挂载系统运行库、只读运行代码、隔离状态目录及指定专用 profile。不开主机调试端口，不整体挂载日常浏览器、session bus、Docker socket 或用户 home。Chrome 与实际 MCP 各用额外的 user/PID namespace，剥离 CLI 认证环境并隐藏 CLI home，阻断通过 `/proc`、浏览器文件导航和上传读取 CLI 凭据；SDK 允许这些嵌套 namespace，外层挂载边界始终强制生效。Chromium 在该强制 OS 隔离内使用 `--no-sandbox`，依赖外层隔离，不能将此参数用于普通浏览器。一个仅在隔离内监听的认证转发器为 Chrome 添加 SDK 代理凭据，所有请求仍经过 SDK 网络检查。隔离启动失败直接退出，无非隔离回退。

可见窗口使用 Xephyr 独立显示服务，它自身也在空根目录、独立 PID/network namespace 中，仅有系统运行库、私有临时目录以及绘制窗口所需的父显示连接。CLI/Chrome/MCP 的文件系统只开放 Xephyr 的专用 socket 和认证文件，不开放主机 X11 socket 或认证文件；Xephyr 禁止 TCP 监听、主机输入抓取和共享内存/GLX/DRI 扩展。无需向浏览器开放整个宿主桌面。`/usr/bin/xdg-open` 在会话内也绑定专用处理器，防止设置 `DISPLAY` 后转入其他桌面浏览器。

本机安装要求已有 npm Claude Code、Node 22.12+、Google Chrome、bubblewrap、socat、Xephyr、xauth、xdpyinfo、本地 X11 会话，以及 profile manager 管理的专用 `cp-*` profile。先在 `~/.local/share/claude-browser-only/runtime` 安装固定依赖 `@anthropic-ai/sandbox-runtime@0.0.81` 和 `chrome-devtools-mcp@1.12.0`，再运行 `python3 linux/browser-only.py install <专用profile-id>`。不要指定日常 profile；此入口与专用浏览器共享该 profile 的网站状态。

Ubuntu 若从桌面启动报告 `bwrap: setting up uid map: Permission denied`，需要管理员将 `browser-only.apparmor.in` 的 `__LAUNCHER__` 替换为该用户入口的绝对路径，保存为 root 所有的 `/etc/apparmor.d/claude-browser-only`，再运行 `apparmor_parser -r /etc/apparmor.d/claude-browser-only`。这是 Ubuntu 支持的逐应用 `userns` 许可；保持 `kernel.apparmor_restrict_unprivileged_userns=1`，不设置全局 namespace 放行，不给 CLI sudo/root。模板只提供 namespace 前置许可，文件/进程/网络隔离仍由上述运行时强制执行。

验证：`python3 ~/.local/share/claude-browser-only/code/browser-only.py verify` 为无界面验证，`verify-visible` 另验证真实可见窗口及显示认证。使用无真实凭据的临时测试 profile 与无害认证 canary，验证目录/进程/socket 隔离、公共 HTTPS、真实 MCP 点击截图/文件上传、凭据不可读、链接绑定和真实 CLI 启动，不提交模型推理请求。该检查不证明账号登录、网站风控结果或对内核漏洞的防护。

## 卸载

```bash
systemctl --user disable --now claude-bodyguard.service
rm -f ~/.config/systemd/user/claude-bodyguard.service
rm -rf ~/.claude-switcher ~/.local/bin/claude-switch
sudo rm -f /etc/sysctl.d/90-claude-switcher.conf
```
