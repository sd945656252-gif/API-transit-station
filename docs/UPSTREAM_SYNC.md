# 统一发布与各实例独立更新

所有部署检查同一个 `CUSTOM_REPOSITORY` 的已发布本站 Release。GitHub 工作流每五分钟检查 `Wei-Shaw/sub2api` 的正式 Release，本站 main 推送和手动运行也会触发。GitHub 调度、排队和构建均需要时间，不是秒级推送；不跟踪官方尚未发布的 main 提交。运行实例不需要公网回调地址。

发布顺序：GitHub 获取正式版本 → 按 `.upstream-ref` 合并官方增量 → 检查定制契约 → 在 GitHub 构建包含前端检查及 Go 编译的镜像 → 发布完整源码、Docker 镜像归档及 `managed-release.json`。只有构建和上传成功才将草稿 Release 正式发布。不使用官方预编译二进制覆盖本站定制功能。镜像归档保存在现有私有 GitHub Release，不将仓库或镜像公开，也不需要额外的 Docker Hub/GHCR 凭据。

管理员页面可见时每分钟检查，更新器缓存一分钟，手动刷新绕过缓存。页面区分 GitHub 准备中、准备失败、已就绪，以及本机下载、校验、加载和部署状态。本站构建按完整源码提交号识别，因此官方版本号相同的本站修复也能提醒更新。点击后只下载同一份已构建镜像，校验 SHA-256、大小、源提交标签、平台、镜像配置摘要和文件层，再使用本机不可变镜像 ID 替换当前应用并确认健康。Docker 经典存储和 containerd 对镜像 ID 的表示不同，因此清单的 `image_id` 表示归档配置摘要，不直接当作本机查找键。不在实例上合并、编译或推送 GitHub。

电脑 A 和电脑 B 各自安装更新器并配置相同仓库，就会各自发现新版本。A 点击更新只影响 A；B 需要单独确认。两台各自保留本机数据库、配置、凭据和更新状态。仅下载过旧源码而未安装更新模块的部署不会自动收到提醒。当前发布 `linux/amd64` 镜像，适用于常见 Intel/AMD 电脑上的 Linux Docker；ARM 服务器会明确拒绝不匹配镜像，需要先扩展构建平台。

文本冲突只在冲突块优先官方，其余定制保留。RunningHub、生图比例和管理员菜单等由 `deploy/managed-updater/contracts.json` 保护，再要求真实构建通过。这不是所有功能的语义回归证明，也不能自动补修任意未来冲突。二进制、添加/删除冲突、契约丢失或构建失败会停止。合并成功后保留 `managed/candidate-*` 分支，构建前的无法应用冲突保留在工作流诊断中。main 并发变更会拒绝发布并等待重试，禁止 force push。

## 安装与权限

每台电脑需要 Docker、现有 `sub2api` 应用和 `sub2api_sub2api-network` 网络，以及能读取本站私有仓库的 GitHub CLI 登录（或预先放入本机凭据文件）。实例只需要 Contents 和 Actions 读取权限，不需要仓库写入权限。GitHub 工作流使用内置 `GITHUB_TOKEN` 完成中央发布，不使用各台电脑的凭据：

```powershell
powershell -ExecutionPolicy Bypass -File tools/enable-managed-updates.ps1
```

安装器将凭据写入本机 `.managed-updater/`（Git 和 Docker 构建上下文均忽略）；GitHub 凭据只给更新器，不进入浏览器和应用。不要提交该目录。Windows 下目录 ACL 限制为当前用户和 SYSTEM。脚本 `-Repository` 指定同一发布仓库，`-AppContainer` 和 `-AppNetwork` 指定当前安装的容器与网络，不绑定任何固定电脑 IP。两台电脑分别执行安装，不要复制正在运行的数据库卷或复用远程 Docker socket。

更新器无宿主机端口，通过内部网络和独立 Bearer 凭据通信。应用端更新、状态和版本检查均经过现有管理员鉴权。更新器持有 Docker socket，具备宿主机 Docker 管理权限，必须作为可信运维服务维护。

首次安装还需部署包含 managed updater 支持的应用。安装生成 `.managed-updater/app.override.json`，`tools/deploy-local.ps1` 会自动使用它。手动 Compose 部署也必须带上此文件；只用原始官方 Compose 会丢失更新器连接。已启用后，更新器会直接保留运行中应用的端口、数据卷、网络、运行参数和凭据挂载。

## 故障与数据边界

独立状态卷 `sub2api_managed_updater_state` 保存任务结果和有界脱敏构建日志。可使用 `docker exec sub2api-updater cat /state/job.json`、`docker exec sub2api-updater tail -n 60 /state/build.log` 排查。不要公开完整容器 inspect、环境变量或凭据文件。

GitHub 构建及实例下载期间旧服务继续运行；切换应用有短暂中断。更新器先持久化原自动重启策略，再将回退容器策略设为 `no`，新主容器沿用原策略，避免 Docker 重启时旧容器争抢端口。失败恢复确切的旧应用容器时也恢复原策略；成功部署保留停止的 `sub2api-previous-*` 容器。更新器重启会核对已记录的切换结果或恢复旧容器，不自动重放未完成更新。GitHub 已发布但某一实例部署失败会单独显示，不影响其他实例。仓库读取失败、镜像校验失败和未上传完整的草稿不会伪装成成功。

更新器不重建 PostgreSQL/Redis、不删除数据卷。旧迁移被修改/删除，以及新增迁移中检测到的数据改写、删除等语句会暂停审核；此检查不保证所有迁移都可逆。**容器恢复不等于数据库回退**，新增迁移仍可能有不可逆影响，生产数据库应单独执行已验证的备份恢复流程。不要因应用回退而宣称数据已恢复。

GitHub 网络、权限、Actions 配额、分支保护、构建依赖、磁盘空间或构建错误会导致准备或部署失败并显示状态。GitHub 发布和各实例部署不是跨系统原子事务。

唯一源码同步和发布入口为 `.github/workflows/sync-upstream.yml` 与 `tools/prepare-managed-release.py`。更新器已移除旧的本机 Git 合并、镜像构建及远程推送实现，避免两台实例同时改写 main。版本清单只绑定源码和镜像，不含账号、API Key、数据库或本机配置。

更新器自身的代码或静态契约改变时，重新运行安装脚本更新其镜像。历史容器、分支和镜像由管理员按运维留存策略管理，不自动删除数据卷。
