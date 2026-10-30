# 本地部署一致性检查（2026-10-27）

## 数据源统一结果

- 唯一在线主库：Docker Desktop 的 sub2api-postgres / sub2api_postgres_data。
- Ubuntu WSL 旧 sub2api、postgres、redis 容器及三个对应命名卷已移除；旧库完整备份经 pg_restore -l 校验可读，保存在 .local-backups/recovered-wsl-20261027.dump。
- 旧 sub2api-quick-tunnel.service 已停用并取消开机启动。
- 当前 .env 已设置 SIMPLE_MODE_AUTO_CREATE_DEFAULT_GROUPS=false，防止重启反复补建默认分组；当前业务分组保留，不按旧 ID 覆盖。
- 配置冲突以当前部署为准：保留 jiying、当前注册/验证开关、包含 RunningHub 的额度结构和本机地址；旧临时公网地址、旧 admin_api_key、旧合规确认记录不导入。邮箱密码和 SMTP 已恢复。
- 合并前当前库额外备份 before-unify-20261027.dump。备份文件不是在线数据源。

## 当前基线

- Windows Docker Desktop；根目录 docker-compose.yml + compose.override.yaml 为本地启动入口。
- 主站使用本地构建 sub2api-local:runninghub；数据库使用 sub2api_postgres_data。
- 数据库、.env、源码、CC Switch 配置属于不同层，不会自动双向同步。
- 旧数据库已在 Ubuntu WSL 的独立 Docker 引擎中找回，与 Docker Desktop 是不同数据源。
- 已备份旧库 recovered-wsl-20261027.dump 和合并前当前库 before-old-account-merge-20261027.dump。
- 已恢复旧管理员邮箱和密码哈希、SMTP 配置；核对新旧密码哈希相等。没有输出密码或密钥。
- 保留当前站点名、账号、分组、API Key；旧库没有上游账号和 API Key。

## 已处理

- 启动脚本原先 up -d 可能复用旧镜像；现强制先构建并检查失败，再替换主站。
- 原脚本无条件启动 Mock 网关；现默认停用，真实适配网关必须显式启用。
- 已停止当前 Mock 容器；测试 Mock 源码保留在独立适配项目，不作为真实业务上游运行。
- 启动脚本显式指定本地 Compose 文件，健康检查读取实际端口，兼容 Docker CLI 不在 PATH 的 Windows 环境。
- 部署文档同步更新。
- 修改前数据库备份：.local-backups/pre-consistency-audit-20261027.dump。备份目录已排除 Git 与镜像上下文。

## 已核对并保留

- 当前 1 个 OpenAI OAuth 账号、6 个分组及用户 API Key；不删除用户业务配置。
- 账号定时测试计划 0、渠道监控 0、用户订阅记录 0。
- 8 条现存运维告警规则保留；未发现与本地启动流程直接冲突，未测试外部邮件送达。
- 未发现匹配项目名/Cloudflare/Web2API 的 Windows 计划任务或活动隧道进程（仅按名称及动作筛选）。
- RunningHub 原生路由和监控迁移继续保留；没有凭据，不声称已完成真实上游验证。
- 历史迁移文件、其他部署模板、Mock 测试不在当前自动启动链路，不进行破坏性删除。

## 未解决及范围限制

- CC Switch 是外部应用，已存供应商不会随后台自动更新；其“获取列表”提示根因仍需定位。
- SMTP 仅恢复配置，未向任何收件人发送测试邮件；其他有差异的认证默认值、注册规则保留当前配置，尚未完成逐项合并判断。旧 admin_api_key 未恢复，避免重新启用旧管理密钥。
- CC Switch 导入已移除固定 gpt-5.5 参数，不再替账号虚构默认模型；客户端需选择已验证模型。外部客户端自身默认值仍不受本站控制。
- 当前 /v1/models 仍可能来源于本地白名单，不能作为上游权限保证；已实测可用模型不得仅因目录缺失删除。
- RUN_MODE=standard（2026-10-30 已更正，历史 simple 模式说明失效）。
- 本次不是全仓逐行或全平台回归审计，不能保证所有遗留代码绝无冲突。未执行上游生成压测、外部通知或账号刷新流程测试。

## 后续清理（未新增备份）

- 检查 32 个 group_id / fallback_group_id 等标量引用字段，确认 9—13 号自动补建分组均无引用；对其软删除，保留 6 个当前业务分组。历史审计及数组/JSON 引用不做无依据改写。
- 删除已停止的 Mock 网关容器；根部署的环境值和默认值切换为 openai-compatible，未配上游凭据时不启用。
- 本地启动脚本新增真实 Provider 必要配置检查，防止误启动 Mock 或未配置上游；健康检查失败现在返回失败。
- .env.example 默认关闭自动补建分组；当前运行值也已关闭。
- 模型按钮改为“追加内置模型目录（未验证）”及“追加上游返回的模型”，与实际追加逻辑一致。
- 当前 settings 未发现旧 trycloudflare 或 api.jiying.com 地址；Windows 项目相关计划任务筛选无结果。
- 现有 Redis 订阅是缓存失效通知，属于当前核心流程，不删除；scheduler_outbox 中 account_last_used 属于当前账号使用事件，保留。
- 8 条当前告警规则仍在用，不因创建时间较旧而删除；未执行外部通知测试。
- 验证：本地前后端镜像构建成功；语言键检查 3 项、CC Switch 导出测试 17 项、适配器测试 5 项通过。PowerShell 语法检查无错误。
- 已部署新版；/health 和 /v1/models 返回 200，模型列表 10 项；RunningHub 原生任务路由未带密钥返回 401；重启后活动分组仍为 6 个，运行容器只有主站/PostgreSQL/Redis。
- 本次未新增备份，未向外部收件人发送消息，未删除既有恢复备份；未对未配置平台宣称完成真实调用验证。

## 2026-10-28 复核与清理结果

- 当前实际运行链路仍只有 `sub2api`、PostgreSQL 和 Redis；`adapter-gateway` 仍为显式 profile，未进入默认启动链路；未发现匹配 Sub2API、隧道或 RunningHub 的 Windows 计划任务。
- 数据库核对结果：用户 1、上游账号 1、API Key 1、告警规则 8、定时测试计划 0、渠道监控 0。已软删除的历史分组（ID 1、4、9—13）在 `group_id`、`fallback_group_id` 等业务引用字段中均为 0 条引用，因此保留不会与当前分组路由冲突。
- `scheduler_outbox` 仍有 1 条当前账号使用事件；`auth_cache_invalidation_outbox` 为 0。两者属于现有运行链路，未删除。
- Redis 原先使用空密码。已在本地 `.env` 写入随机密码并重建 Redis/主站容器；未认证请求返回 `NOAUTH`，主站 `/health` 仍为 200，数据库业务计数保持不变。
- `tools/deploy-local.ps1` 增加非回环绑定校验：绑定到公网或局域网地址时，PostgreSQL、Redis、JWT 和 TOTP 密钥不得为空或使用占位值；回环本地部署行为不变。
- 旧迁移文件、兼容字段、历史审计表、Mock 适配器、部署模板和文档未发现进入当前默认调用链的证据，继续保留。删除它们会破坏升级、回滚或测试路径，因此本次不清理。
- 复核后的结论是：已清理生成缓存和失效测试断言；未发现可以安全删除的生产代码或当前业务数据。公网生产仍需单独配置 HTTPS、反向代理、限流和真实上游凭据，不能由本地复核替代。

## 2026-10-30 baseline correction

Historical counts above are dated snapshots, not current deployment instructions. The current runtime uses standard mode and additionally runs local object storage. Current database counts: 1 user, 1 upstream account, 3 API keys, 298 applied migrations.
