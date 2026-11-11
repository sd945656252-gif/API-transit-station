# 官方上游同步

本仓库保留自己的 `main` 分支作为运行版本，官方仓库 `Wei-Shaw/sub2api` 作为上游来源。`.upstream-ref` 记录已同步的官方提交；`.github/workflows/sync-upstream.yml` 每天检查一次，也可以在 GitHub Actions 中手动运行。

官方代码增量会应用到临时分支并创建 Pull Request，不会直接改写本仓库的 `main`。同步流程保留本仓库的 GitHub Actions 工作流；官方工作流变更需要人工检查后再移植。CI 和人工检查通过后合并，保护 RunningHub、生图比例适配、Docker 配置和其他本地定制。

如果官方改动与本地定制产生冲突，工作流会停止并保留现有 `main`。解决冲突后重新运行工作流即可。首次接入时会从 `.upstream-ref` 指定的共同基线应用官方增量；同步成功后更新该标记，后续按新标记继续跟进。

合并后需要重新构建本地镜像：

```powershell
docker compose build sub2api
docker compose up -d sub2api
```

`compose.override.yaml` 继续使用本地镜像 `sub2api-local:runninghub`，因此仓库同步不会自动替换正在运行的容器。这样可以先审核代码，再决定何时部署。

