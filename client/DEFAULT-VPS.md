# Default VPS Client

本工具只配置你自己的中转，不包含卖家的 Token、域名或访问权限。先保证自己的 VPS 中转已完成 HTTPS 和 API 认证，并验证不会公开管理/导出端点。管理页登录密码不是中转 API Token。

## Private Configuration

在本机自行创建私有 JSON，限制权限为 0600；不要提交 Git、发送给 Agent 或粘贴到公开终端记录。三项值对应 Claude 的标准环境变量，headers 没有额外认证时可留空：

```json
{
  "ANTHROPIC_BASE_URL": "https://relay.example.com",
  "ANTHROPIC_AUTH_TOKEN": "REPLACE_WITH_YOUR_PRIVATE_RELAY_TOKEN",
  "ANTHROPIC_CUSTOM_HEADERS": ""
}
```

如你自己的入口启用 Cloudflare Access Service Auth，将自己生成的两项认证头按每行一项放入 `ANTHROPIC_CUSTOM_HEADERS`；不要借用第三方凭据。配置文件本身使用 JSON `\n` 表示换行。

## Linux

先确认官方 Claude 可执行文件的位置（不是旧 ccam/relay 包装器），再执行：

```sh
python3 client/configure_vps_default.py --config-file /PRIVATE/PATH/client.json --claude-bin /ABSOLUTE/PATH/TO/OFFICIAL/claude
```

工具先备份所有即将修改的文件至 HOME 下 0700 的 `.claude-vps-backup-<timestamp>`，合并默认 `.claude/settings.json` 和已有 `.local/share/ccam/profiles/*/settings.json` 的 env，保留其它设置与账号数据，凭据文件设为 0600。它会清理这些 env 中的旧 `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`，然后写入你提供的中转配置。

提供 `--claude-bin` 时还生成 `~/.local/bin/claude-vps`、默认 `~/.local/bin/claude` 启动器，在 `.bashrc` 末尾加入可重复更新的 managed block，让新 Bash 终端的 `claude` 使用中转。旧 shell 内容保留在原位置及备份中。启动器清除传入的旧 `ANTHROPIC_*` 与 `CLAUDE_CODE_OAUTH_TOKEN` 后读取私有中转配置。它不新增跳过权限检查参数，不杀会话。目标是符号链接时拒绝覆盖，须先审阅其用途。

省略 `--claude-bin` 仅更新 settings，不改变命令入口；旧包装器、显式 `--settings`、项目环境、其它 shell 或 VS Code 扩展可能覆盖它，必须按实际启动方式验证。Zsh/Fish 用户自行配置对应 shell，使 `claude` 入口指向已生成启动器。不要把已有会话视为即时迁移；新开终端并重启 Claude 后生效。

## Windows

可用 Windows Python 运行 settings-only 模式（不要指定 Linux 的 `--claude-bin`）：

```powershell
python client/configure_vps_default.py --config-file C:\Private\client.json
```

Windows 的文件访问控制需由用户自行限制到本人；Unix 0600 不等同于 NTFS ACL。检查实际使用的 Claude config directory，若使用自定义 `CLAUDE_CONFIG_DIR` 或旧启动脚本，应在对应 `settings.json` 中合并同样 env 或配置自己的用户级环境变量。关闭并重新打开 PowerShell/Windows Terminal/VS Code，重新启动 Claude。不要把真实凭据放到共享脚本或命令行参数。

## Verification and Rollback

先用自己入口的只读模型列表或 token 计数接口验证认证，再按预算进行一次最小真实调用；不要以配置存在或 `claude --version` 成功代替真实连通性验证。VPS 账号状态独立于本机旧 ccam 状态，不能用本机的 expired 标签强行覆盖 VPS。

回滚前结束当前任务，核对备份 `manifest.json` 的文件清单。对 `existed: true` 的条目，把备份中的对应相对路径复制回 HOME 并保留权限；对 `existed: false` 的条目，仅在确认文件仍为本次生成内容后移走至另一个私有备份。不要恢复整个 HOME，不覆盖回滚后产生的新配置。恢复 `.bashrc` 后新开终端；正在运行的进程不会自动改变环境。

测试（仅临时目录与假凭据，不访问供应商）：

```sh
python3 -m unittest discover -s client -p 'test_*.py'
```
