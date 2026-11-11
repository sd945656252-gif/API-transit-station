# 官方版本提醒与点击部署

管理员版本面板检查 `Wei-Shaw/sub2api` 的最新正式 Release。页面可见时每分钟查询，更新器缓存五分钟；手动刷新绕过缓存。不是秒级推送，不跟踪未发布的 main 提交。查询失败显示原因，不显示“已是最新版”。

点击“立即更新并部署”会启动独立任务：获取正式版本 → 按 `.upstream-ref` 应用官方增量 → 构建定制镜像 → 正常推送本站 GitHub main → 替换应用容器 → 确认 Docker 健康状态和源码提交标识。页面显示真实阶段、结果和失败原因；重启应用不会终止更新器。不会把官方预编译二进制直接覆盖到定制应用中。

文本冲突只在冲突块优先官方，其余定制保留。RunningHub、生图比例和管理员菜单等使用 `deploy/managed-updater/contracts.json` 静态契约防止明显丢失，再要求真实构建通过。这不是所有功能的语义回归证明，也不是能够自动补修任意未来冲突的 AI。二进制、添加/删除冲突、契约丢失或构建失败会停止，补修后重试。构建通过后保留 `managed/update-*` 分支；构建前失败没有远程修复分支。不允许 force push；main 并发变更时停止并重试。

## 安装与权限

需要 Docker、现有 `sub2api` 应用和 `sub2api_sub2api-network` 网络，以及对本站仓库具备读写权限的 GitHub CLI 登录：

```powershell
powershell -ExecutionPolicy Bypass -File tools/enable-managed-updates.ps1
```

安装器将凭据写入本机 `.managed-updater/`（Git 和 Docker 构建上下文均忽略）；GitHub 凭据只给更新器，不进入浏览器和应用。不要提交该目录。Windows 下目录 ACL 限制为当前用户和 SYSTEM。`CUSTOM_REPOSITORY` 可指定自己的仓库。

更新器无宿主机端口，通过内部网络和独立 Bearer 凭据通信。应用端更新、状态和版本检查均经过现有管理员鉴权。更新器持有 Docker socket，具备宿主机 Docker 管理权限，必须作为可信运维服务维护。

首次安装还需部署包含 managed updater 支持的应用。安装生成 `.managed-updater/app.override.json`，`tools/deploy-local.ps1` 会自动使用它。手动 Compose 部署也必须带上此文件；只用原始官方 Compose 会丢失更新器连接。已启用后，更新器会直接保留运行中应用的端口、数据卷、网络、运行参数和凭据挂载。

## 故障与数据边界

独立状态卷 `sub2api_managed_updater_state` 保存任务结果和有界脱敏构建日志。可使用 `docker exec sub2api-updater cat /state/job.json`、`docker exec sub2api-updater tail -n 60 /state/build.log` 排查。不要公开完整容器 inspect、环境变量或凭据文件。

构建和推送期间旧服务继续运行；切换应用有短暂中断。失败会尝试恢复确切的旧应用容器，成功部署也保留停止的 `sub2api-previous-*` 容器。更新器重启会核对已记录的切换结果或恢复旧容器，不自动重放未完成构建/推送。GitHub 已发布但部署失败会单独显示，可重试部署。

更新器不重建 PostgreSQL/Redis、不删除数据卷。旧迁移被修改/删除，以及新增迁移中检测到的数据改写、删除等语句会暂停审核；此检查不保证所有迁移都可逆。**容器恢复不等于数据库回退**，新增迁移仍可能有不可逆影响，生产数据库应单独执行已验证的备份恢复流程。不要因应用回退而宣称数据已恢复。

GitHub 网络、权限、分支保护、镜像依赖下载、磁盘空间或构建错误会导致更新失败并显示原因。GitHub 发布顺序在部署之前，两者并非跨系统原子事务。

每日 GitHub 工作流只生成正式版本检查报告，不合并或部署，避免与管理员点击更新形成第二套权威更新来源。GitHub main 保存源码，本机 Docker 使用同一提交构建镜像；本流程不承诺自动发布 GHCR/Docker Hub 镜像。

更新器自身的代码或静态契约改变时，重新运行安装脚本更新其镜像。历史容器、分支和镜像由管理员按运维留存策略管理，不自动删除数据卷。
