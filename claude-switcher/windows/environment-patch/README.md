# Claude Environment Patch

`claude检测器` Windows 预览版，版本 `0.3.0-preview`。用于远程环境配置、检测与恢复服务。

## Support

- Windows 10/11，自带 Windows PowerShell 5.1 和 WPF。
- 可选 Chrome 设置使用 Chrome 91+ 支持的策略；实际支持情况须在客户的 `chrome://policy` 核对。
- 如需跟随 VPS 时区：客户已有 Windows OpenSSH、SSH 密钥或 agent 登录和已验证的主机密钥；VPS 为支持 `timedatectl show -p Timezone --value` 的 Linux/systemd 系统。该读取不需要 `sudo`。
- 客户已有 Clash Verge Rev / Mihomo、可用订阅与代理组；补丁不提供代理线路或账号。
- 首版只支持空白或默认的全局扩展脚本，以及本工具此前安装的未修改脚本。发现客户自定义脚本或不认识的 DNS policy 时，需要服务人员人工合并。
- 当前已验证 JavaScript 配置转换和 Linux Mihomo 配置解析；Windows GUI、权限、安装和恢复必须经过 Windows 真机验收后，才能作为正式交付版本。

## Scope

1. 检测配置目录、补丁状态和 Windows 系统时区。
2. 生成并安装 DNS/分流扩展：默认通过客户选定的代理组查询 Cloudflare/Google DNS；已列出的国外域名所有记录类型都使用海外 DNS；`geosite:cn` 和千川上传域名使用国内 DNS。千川上传域名的 DIRECT 规则前置，随后前置明确海外域名到客户代理组的规则。
3. 按客户选择保持时区，或读取其明确指定 VPS 的实际系统时区并同步 Windows；不固定纽约/洛杉矶，不从 IP 城市或当前时差猜测。
4. 保存原扩展脚本字节与原时区，提供恢复入口；客户后续修改过脚本时拒绝直接覆盖。
5. 可选 Chrome 设置：中文界面、网页优先英语、限制 WebRTC 非代理 UDP、默认拒绝网站定位，并备份原注册表值以便恢复。
6. 单独可选“Chrome DNS 跟随 TUN”：只有控制接口确认 TUN 已开启且启用 53 端口 DNS 劫持后，才允许禁用 Chrome 自身 DoH。不满足前提时不应用该设置。

除上述域名规则前置和精确重复项去重外，保留其余路由；不新增 catch-all。订阅节点、浏览器资料和登录态不在此补丁修改范围。历史预览 ZIP 不包含本次新增的运维端复刻功能；以当前源码和客户验收记录为准。

## Chrome Settings

Chrome 选项默认不勾选，由远程服务人员和客户确认后启用。写入范围为当前 Windows 用户的 `HKCU\Software\Policies\Google\Chrome`，会影响该用户的全部 Chrome 资料；浏览器可能显示“由贵组织管理”。

| 设置 | 策略 |
|---|---|
| 界面中文 | `ApplicationLocaleValue=zh-CN` |
| 网页优先英语 | `ForcedLanguages\1=en-US`、`ForcedLanguages\2=en`；保留其他编号语言，不承诺请求头严格只有两项 |
| WebRTC 非代理 UDP 限制 | `WebRtcIPHandling=disable_non_proxied_udp` |
| 默认拒绝网站定位 | `DefaultGeolocationSetting=2` |
| DNS 跟随已验证的 TUN，可单独选择 | `DnsOverHttpsMode=off` |

Chrome 跟随 Windows 系统时区；选择 VPS 同步时，由 VPS 读数决定 Windows 时区。Windows Chrome 没有单独的 `SystemTimezone` 策略。设置中文界面或时区后，正常关闭重开 Chrome，再刷新检测页。补丁不会关闭 Chrome，也不清理登录、密码、历史、书签或扩展。

## VPS Timezone

输入本次客户明确指定的 `user@host` 或已配置的 SSH 别名及端口，点击“读取 VPS 时区”。读取只显示结果，不改变 VPS 或客户电脑的设置；选择“跟随 VPS”并安装后，才修改客户 Windows 时区。

固定远端命令为 `timedatectl show -p Timezone --value`。工具使用现有密钥或 SSH agent，不接收、保存密码或私钥，不自动接受未知主机密钥，也不自动选择操作人员自己电脑里的 VPS。首次 SSH 连接和主机身份确认应由服务人员按客户授权完成。

安装时会重新读取 VPS，避免继续使用旧预览。目标或端口变更会使预览失效。读取失败、未知时区或 Windows 不支持时，停止本次安装；备份记录包含本次读取来源、时间与实际应用的时区。

**VPS 系统时区与出口 IP 所在城市时区可能不同。** 例如美国 VPS 若实际设置为 `Etc/UTC`，该模式就同步到 Windows `UTC`，不会擅自改成洛杉矶。IP 检测站按地理位置给出的提示不属于此系统时区模式的数据来源。

IANA 到 Windows 的转换使用随包附带的离线 Unicode CLDR 映射及官方别名表，来源、固定版本和校验值见 `TIMEZONE-DATA.md`，许可证见 `UNICODE-LICENSE.txt`。更换 VPS 或修改其时区后，应重新读取并同步；本工具不安装后台时区监控。

注册表回读只证明策略值已经写入；在 `chrome://policy` 检查来源、状态和冲突，并验证实际网页语言、定位、WebRTC 和 DNS 行为。若上级机器策略冲突，工具会阻止覆盖。恢复前若发现客户再次修改了应用值，也会拒绝覆盖。

## Operator Workflow

远程收费服务使用 [operator/REMOTE-SERVICE.md](../../operator/REMOTE-SERVICE.md) 中的运维端，不向客户发送完整 ZIP。自租 VPS + Windows 的同款结构生成入口见 [Reference Environment Replica](../../operator/REPLICA-DEPLOYMENT.md)。下述为本地 GUI 的内部验收流程。

1. 将整个解压目录保留在一起，双击 `Start.cmd`。
2. 核对客户的 Clash Verge Rev 配置目录，输入该客户配置中真实存在的代理组名；名称不一定是 `PROXY`。
3. 点击检测，确认目标客户、当前配置和拟修改项。需要同步时区时，填写该客户当前出口 VPS 的 SSH 目标及端口，读取时区后选择跟随。客户有上传、下载或远程工作时，不重启其代理或浏览器。
4. 安装补丁。出现“已写入/待生效”只表示文件写入完成。
5. 在 Clash Verge Rev 中确认全局扩展脚本已启用，并重新应用当前订阅。程序不自动重启代理客户端。
6. 运行检测站的全新 DNS 检测，并核对 Mihomo 实时 DNS 日志：用不同的新域名查询 `A`、`HTTPS`、`TXT`；国外查询应只出现海外上游，国内查询应出现国内上游。HTTP 有响应、fake-IP 地址或第三方评分均不能代替这项验证。
7. 在客户浏览器中确认时区和原有工作流。旧浏览器进程可能需要正常关闭重开。

## Rollback

从 GUI 选择本工具创建的备份并恢复。所选备份包含 Chrome 设置时，同时恢复其原值；原本不存在的值仅删除本次创建的对应值，不删除整个 Chrome 策略键。恢复后仍需在 Clash Verge Rev 重新应用订阅，并在 Chrome 核对恢复结果。备份保存在客户本机 `%LOCALAPPDATA%\claude-environment-patch\backups`，远程交付完成后按客户约定保留。

如果脚本已被客户或其他软件修改，先人工核对差异；不要通过删除备份或重复安装跳过冲突提示。

## Delivery Checklist

- 记录系统版本、Clash Verge Rev / Mihomo 版本和补丁包 SHA256。
- 客户原脚本、原时区有可读备份；不在交付记录中附带订阅、secret 或浏览器登录资料。
- 检测、新安装、重复安装、用户改动冲突、恢复和时区权限失败场景均在 Windows 真机测试。
- 如遇自定义 DNS 或自定义扩展脚本，明确记录人工合并结果和验证证据。
- 收费说明使用“环境配置、检测与维护服务”；不得把检测报告写成官方账号评分或可用性保证。

## Sources

扩展入口遵循 [Clash Verge Rev 官方脚本文档](https://www.clashverge.dev/guide/script.html)。DNS 行为参考 [Mihomo DNS 文档](https://wiki.metacubex.one/config/dns/)。Chrome 设置依据 [Chromium 官方策略定义](https://chromium.googlesource.com/chromium/src/+/main/components/policy/resources/templates/policy_definitions/)，策略生效验证参考 [Google 官方说明](https://support.google.com/chrome/a/answer/9024365?hl=en)。分发包保留上游项目的 `LICENSE`。
