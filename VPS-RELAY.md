# VPS Relay and Account Administration

v5 保留原有 Windows/Linux CCAM、看板、检查工具、网页 OAuth broker 与许可证，新增 VPS 后台自动登录检测、按需续期及本机默认连接 VPS 的配置工具。先阅读本文件，再使用原有 START-HERE.md / WINDOWS.md 安装本机工具。

新增 `relay-admin/` 源码、模板及本文档沿用包根目录 `LICENSE` 的 MIT 许可证；原有第三方文件保留各自许可证。cu 与官方 Claude CLI 并未包含在本包内，适用其各自许可与服务条件。

## Scope

本包交付软件与部署资料，不含 Claude 账号、订阅、额度、VPS、域名、第三方费用、托管服务或卖家的中转访问权限。用户自备合法可用账号并亲自完成官方授权。CCAM 本机账号管理与 VPS 的 cu 账号池是不同数据源；本机新增账号不会自动加入 VPS。

管理网页提供账号列表、剩余额度（未知时显示未知）、启用/停用、优先级、搜索，以及网页发起新增/重新登录流程。日常添加账号无需 SSH：

1. 在管理页点击添加账号，或选择目标账号后重新登录，然后点击开始。
2. VPS 上的官方 Claude CLI 发起授权；网页提供 `https://claude.com/cai/oauth/authorize` 官方链接。
3. 用户本人在官方页面登录、授权，将回调页显示的**完整授权 code** 粘贴回管理页提交，不要截断其中符号，也不要发送给客服或 Agent。
4. 等待管理页明确返回成功，并核对账号池；凭据保存在 VPS 服务用户密钥环中，网页不接收访问/刷新令牌。

重新登录会核对目标账号身份，不允许用另一个账号替换。登录进行中阻止网页修改账号开关/优先级。每次流程有时限，取消/超时需要服务器确认；网络中断显示结果未知时仅查询状态，不自动重复提交或重新授权。安装和故障排查仍需服务器管理员权限。浏览器用于授权，不代表后续请求依赖这台电脑。

## Prerequisites

模板基于 Ubuntu 24.04、systemd、Python 3.12 与已验证的 cu 1.4.0 API：`GET /api/profiles`、`GET /api/status` 的 `csrf_token`、`PATCH /api/profiles/:id`。cu 为第三方 claude-unlimited 工具，**不随本包分发**；管理员须从其可信上游取得版本并核对许可证、安装方式与账号条款。其他版本需先运行兼容性验证；不能将任意同名 PyPI 包当作正确依赖安装。官方 Claude CLI 需另行安装，原型验证版本为 2.1.322。

管理 Web 后端只用 Python 标准库，新增 login broker 需在已有 cu 的 venv 中运行。cu 1.4.0 在 Linux 上通过 `secret-tool` 访问 Secret Service，运行需要 `dbus-run-session`、`gnome-keyring-daemon` 和 `secret-tool`（Ubuntu 的 `libsecret-tools` 包）；cu 本身的 Python 依赖由其安装流程负责。VPS 需有足够内存/磁盘容纳 CLI 和账号密钥环。没有在全新 VPS 上验证一键安装，本包不提供一键整机迁移。

## Relay Runtime Contract

先配置并验证你自己的 cu 服务，禁止把无鉴权的 4317 端口暴露公网。附带 `claude-relay-cu.service`、`start-cu.sh` 是可审阅的参考模板，假定以下布局已经创建：

- 专用系统用户 `claude-relay`，HOME `/var/lib/claude-relay`，目录仅该用户可读。
- cu 安装在 `/opt/claude-relay/venv`，入口 `/opt/claude-relay/venv/bin/python -m claude_unlimited`。
- 官方 Claude CLI `/opt/claude-relay/claude`；`/opt/claude-relay/bin/claude` 指向该文件。
- `/etc/claude-relay/keyring-pass` 是管理员自行生成的密钥环口令，root 所有、0600；必须与现有密钥环匹配，不能随意替换。
- cu 监听 `127.0.0.1:4317`，服务名 `claude-relay-cu.service`。不要在用户本机停掉旧服务，直到 VPS 实际调用验收成功。

按现有部署方式安全创建上述布局后，安装参考脚本与 service，再 `systemctl daemon-reload` 并启动。已有 cu 部署应适配模板，不能覆盖现有凭据或账号目录。`cu-admin` 通过运行中的 cu 进程定位其 D-Bus，并切换为同一服务用户；它需要 root 权限、上述固定路径和可读 `/proc`。

### Optional Administrator CLI

以下终端方法仅供管理员检查或备用操作；日常添加账号使用上文网页流程即可。不要在网页授权活动中同时运行 CLI 登录，以免并行改动账号池。

```sh
sudo install -m 0755 relay-admin/cu-admin /usr/local/sbin/cu-admin
sudo /usr/local/sbin/cu-admin status
sudo /usr/local/sbin/cu-admin add-account
# 失效账号重新授权：sudo /usr/local/sbin/cu-admin reauth
```

Windows PowerShell 或 Linux 终端可执行（先替换占位符）：

```sh
ssh -t -p YOUR_SSH_PORT YOUR_ADMIN_USER@YOUR_VPS_HOST sudo /usr/local/sbin/cu-admin add-account
```

操作用户须拥有相应 sudo 权限。按 CLI 提示本人登录，不把密码、授权码或 Token 发给 Agent。官方 CLI 可用和帮助命令成功不等同于新增账号 OAuth 已验收；必须本人完成一次授权并确认账号出现在 VPS 列表。

## Install or Upgrade the Admin UI and Login Broker

以下是新安装示例；升级时先备份 `/opt/relay-admin`、现有 service 与认证文件，不要替换已有密码或账号目录。必须已经有专用 `claude-relay` 用户及上文 cu 服务。缺少 `relay-admin` 用户时才创建。命令在解压后的 `ccam-bundle` 目录执行。`ADMIN_ORIGIN` 保持为自己的 HTTPS 域名（不带末尾斜线）。

```sh
# 仅首次安装且用户不存在时执行：
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin relay-admin
sudo install -d -m 0755 /opt/relay-admin
sudo install -d -m 0700 /etc/relay-admin
sudo install -m 0644 relay-admin/server.py relay-admin/index.html relay-admin/login_broker.py /opt/relay-admin/
sudo install -d -m 0755 /opt/relay-admin/fonts
sudo install -m 0644 relay-admin/fonts/* /opt/relay-admin/fonts/
# 仅首次安装、认证文件不存在时执行：
sudo python3 relay-admin/create-auth.py /etc/relay-admin/admin-auth
sudo install -m 0644 relay-admin/relay-login.service /etc/systemd/system/
# 新安装复制；已有 service 则保留自己的 ADMIN_ORIGIN 与安全配置：
sudo install -m 0644 relay-admin/relay-admin.service /etc/systemd/system/
sudoedit /etc/systemd/system/relay-admin.service
sudo systemctl daemon-reload
# broker 先启动并确认成功，再启动/重启管理后端：
sudo systemctl enable --now relay-login.service
sudo systemctl is-active relay-login.service
sudo systemctl enable relay-admin.service
sudo systemctl restart relay-admin.service
```

`relay-login.service` 必须使用 `/opt/claude-relay/venv/bin/python`，以 `User=claude-relay`、`Group=claude-relay`、`SupplementaryGroups=relay-admin` 运行。不要改变 cu 用户的主组。broker 自行将 `/run/relay-login` 目录设为 relay-admin 组、0750，将其中 `login.sock` 设为该组、0660，使 Web 服务只能通过私有 UNIX socket 调用登录代理。不要把 socket 或 broker 暴露公网。

cu 的 Secret Service D-Bus socket 可能位于主机 `/tmp`，broker 必须与其共享，因此附带 service 明确使用 `PrivateTmp=false`，不能改成 true。升级后的 Web 后端依赖 broker；broker 缺失时登录和账号修改会失败，不能只复制 HTML/server。不要在正在进行授权时升级或重启。

管理员用户名 `admin`，密码由你在隐藏输入提示中自行设置。生成器只创建不存在的文件，拒绝覆盖；请将密码存入自己的密码管理器。密码修改须先把旧认证文件移到私有备份、重新生成，再重启管理 service；无需改 cu API 客户端令牌。

## HTTPS and Public Routing

管理服务仅绑定 `127.0.0.1:4318`。配置自己现有的 HTTPS 反向代理或 Cloudflare Tunnel，把**独立管理域名**指向这个地址。Tunnel 参考见 `tunnel.yml.example`，ID、凭据 JSON、DNS 记录需由你自己创建；不要使用别人的 Tunnel 或凭据。代理必须保留 Host 并正确设置 `X-Forwarded-Proto`，HTTP 应在入口跳转 HTTPS。Basic 登录只能经 HTTPS 使用。

不要把管理域名指向 cu 的 4317，也不要将 `/api/export`、密钥环或凭据接口暴露公网。中转调用域名与管理域名分别配置；调用入口的 API 认证/访问控制由你的 cu 和反向代理部署负责，本管理页面不替代它。Windows/Linux 客户端应使用自己的 HTTPS 中转地址与调用凭据，不能使用管理页密码充当 API Token。若原有本机 Claude 包装器清理环境变量，须单独配置官方 CLI 的中转启动入口。

## Verification and Rollback

```sh
python3 -m unittest discover -s relay-admin -p 'test_*.py'
sudo /usr/local/sbin/cu-admin status
systemctl is-active relay-admin relay-login claude-relay-cu
```

完成 HTTPS 后验证：未登录返回 401；管理员登录可看账号和额度；无效 Origin/CSRF 修改被拒绝；先用自己的测试账号验证开关/优先级，并恢复原值。本包自动化测试与网页发起/等待/取消流程验证不等同于真实账号授权成功；完整供应商授权仍待用户本人验收。亲自从网页完成新增账号授权，再从 Windows/Linux 进行一次有额度预算的最小真实调用；本包的单元测试不能证明你的供应商账号/网络可用。最后关闭本机中转进程进行独立性测试（先确认回滚方案），确认 VPS 仍可调用。不要自动注销桌面或重启机器。

### Unknown Outcome

持久化结果不确定时，broker 会保留 `/var/lib/claude-relay/.claude-unlimited/web-login-uncertain.json`，阻止新登录或修改。管理员应在私有终端核对 journal 指向的本次 owned 登录目录、cu 账号池保存结果以及凭据清理状态。不要打印或外传凭据，不要直接删除 journal、删除账号目录或反复重启作为修复；先确认是否已经写入账号、是否留存明文，制定与实际状态匹配的恢复操作，避免重复导入或破坏账号。

### Rollback

没有活动或未知授权流程时，可以停止新管理入口并恢复安装前备份的 Web 源码/service，然后停止新增 broker：`sudo systemctl disable --now relay-login.service`。恢复旧版后启动管理 Web。若完全关闭管理入口，执行 `sudo systemctl disable --now relay-admin.service relay-login.service`，并移除自己新增的管理域名路由。存在未确认授权结果时先按上节核对，不能用回滚掩盖未知状态。保留配置和私有认证文件备份；不要停用 `claude-relay-cu` 或删除密钥环。旧版下载包与本机原配置应保留，直到新路径完成实际验收。


## Automatic Login Monitoring

v5 的后台检测在 cu 自己的 daemon 定时循环运行，关闭网页、关闭本机电脑后仍会继续。每 30 秒最多检查一个启用的 OAuth 账号，正常每个账号约 5 分钟检查一次；账号超过 10 个、供应商限流或网络错误时周期会延长。账号新增后自动发现；已停用账号不检测。

检测仅调用固定的官方只读 profile 接口，不发生成消息、不消耗生成额度。token 接近过期或 401 时复用 Gateway 的唯一刷新机制，不另建 refresh-token 交换器。只有 200 且 UUID 与当前账号一致才记为有效；401 续期仍未恢复记为登录过期，403 显示权限异常，429/网络失败分别退避，不误报为过期。不可续期、被撤销的授权仍需本人重新登录，无法自动绕过官方授权。

管理页用邮箱显示账号，展示检查时间、最后成功时间及下次检查计划；超过 10 分钟的成功结果显示待更新。列表“后台可调度”、最近请求记录或没有错误记录均不作为登录有效的证明。限流的全局退避期间，各账号的计划时间可能延后。管理页密码与供应商账号登录是两种独立认证。

安装器只支持 cu 1.4.0 的已审查 daemon 文件 SHA256；即使版本号相同但源码不同也会拒绝，不允许强行覆盖。它仅插入监测调用并将现有循环间隔调整为 30 秒，保留原 refresh owner；不包含整个 cu 源码，也不自动重启服务。

```sh
# 必须使用你现有 cu 的解释器；路径按真实部署调整：
sudo /opt/claude-relay/venv/bin/python relay-admin/install_auth_monitor.py
# 模拟测试，不使用真实账号网络：
/opt/claude-relay/venv/bin/python -m unittest discover -s relay-admin -p 'test_*.py'
```

确认没有活动请求、正在进行或结果未知的登录流程后，由管理员在维护窗口重启 `claude-relay-cu.service`。若原服务依赖导致 broker 停止，确认并启动 `relay-login.service`；按你的代理配置确认管理入口服务正常。不要在请求处理中无条件重启。检查 `~claude-relay/.claude-unlimited/auth_monitor_state.json` 的时间戳自动推进及管理页状态；它仅含账号 ID、状态和时间戳，权限为 0600。真实供应商验证结果不能由单元测试替代。

升级 cu 后应重新核对接口和安装器兼容性，不能假设补丁会保留。回滚时在无活动请求/登录的维护窗口恢复安装器打印的备份目录中的 `daemon.py`，若备份包含旧 `auth_monitor.py` 也恢复，然后重启 cu 与相关管理服务；原模块不存在时可保留新模块文件但旧 daemon 不再调用它。保留状态与所有原始备份，不删除账号或密钥环。

## Default Client Routing

参见 `client/DEFAULT-VPS.md`。客户端改成 VPS 后，VPS 负责账号选择、续期和调用转发。本地旧看板仅反映本地 ccam 会话，不能代表 VPS 账号池；可关闭看板，保留账号数据备用。配置工具不会杀死现有 Claude 会话，也不会自动停用任何本地服务。已经启动的进程需要结束当前任务并重启后才保证使用新连接。

## Font Licenses

管理页使用 Barlow 与 Barlow Condensed，随包附带各自 SIL Open Font License 1.1，见 `relay-admin/fonts/OFL-Barlow.txt` 与 `OFL-BarlowCondensed.txt`。字体来自 Google Fonts 的 `ofl/barlow` 与 `ofl/barlowcondensed` 目录；它们不沿用本项目 MIT 许可。
