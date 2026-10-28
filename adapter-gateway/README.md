# AI Adapter Gateway

独立的 OpenAI 兼容协议适配网关。它位于现有中转站与上游 AI 平台之间：

```text
客户端 -> Sub2API / New API -> adapter-gateway -> 上游平台
```

当前包含三个 Provider：

- `mock`：本地可重复测试，不访问外部服务。
- `runninghub`：转发 RunningHub 的 OpenAI 兼容 LLM API。需要合法的 RunningHub API Key；没有凭据时不能进行真实上游验证。
- `openai-compatible`：转发一个已经运行且获得授权的 OpenAI 兼容服务，例如本机或内网中的 Web2API。网关不负责浏览器登录、Cookie 提取、验证码处理或访问限制绕过。

## 本地运行

以下独立运行示例用于开发测试，`.env.example` 默认使用 Mock，不代表已接通真实上游。
主站实际部署请使用仓库根目录 `tools/deploy-local.ps1`；真实网关需先配置凭据，再加 `-EnableAdapter`。

```powershell
Copy-Item .env.example .env
docker compose --env-file .env up --build
```

默认监听 `http://127.0.0.1:8090`。客户端鉴权使用 `Authorization: Bearer <ADAPTER_GATEWAY_API_KEY>`。

## 接口

- `GET /healthz`
- `GET /readyz`
- `GET /v1/models`
- `POST /v1/chat/completions`

`stream=false` 返回标准 OpenAI Chat Completions JSON；`stream=true` 返回 `text/event-stream`。生成类请求不自动重试，避免上游不具备幂等性时重复扣费。

## 接入现有中转站

在渠道管理中新增一个 OpenAI 兼容渠道：

- Base URL：`http://adapter-gateway:8090/v1`
- API Key：`ADAPTER_GATEWAY_API_KEY` 的值
- 模型：使用网关 `/v1/models` 返回的模型 ID

如果 Sub2API 和适配网关不在同一个 Docker 网络，把 Base URL 改成适配网关可访问的内网地址。生产环境应设置固定的长随机 `ADAPTER_GATEWAY_API_KEY`，不要使用示例值。

## RunningHub 配置

```env
ADAPTER_PROVIDER=runninghub
ADAPTER_GATEWAY_API_KEY=change-this-gateway-key
RUNNINGHUB_API_KEY=your-authorized-runninghub-key
RUNNINGHUB_BASE_URL=https://llm.runninghub.ai/v1
ADAPTER_MODEL_MAP={"public-model":"runninghub-model-id"}
```

`RUNNINGHUB_BASE_URL` 和模型 ID 必须按当前官方文档及账号实际可用模型配置。适配器不会伪造模型列表、usage 或能力；上游不支持的能力由上游错误原样映射。

## 外部 OpenAI 兼容服务配置

如果 Web2API 已经在同一台机器或同一内网中独立运行，并且该服务的使用符合上游平台授权和条款，可以把网关配置为通用转发模式：

```env
ADAPTER_PROVIDER=openai-compatible
ADAPTER_GATEWAY_API_KEY=change-this-gateway-key
UPSTREAM_BASE_URL=http://web2api:23456/v1
UPSTREAM_API_KEY=the-upstream-service-key
ADAPTER_MODEL_MAP={"public-model":"deepseek"}
```

`UPSTREAM_BASE_URL` 必须指向兼容服务的 `/v1` 根路径。该模式只转发标准模型和聊天接口，不读取或保存浏览器 Cookie、Session Token，也不实现验证码、风控或计费限制绕过。生产部署建议将 Web2API 只绑定到内网，并在 Sub2API 中配置 `http://adapter-gateway:8090/v1` 作为 OpenAI 兼容渠道。

## 调用测试

```powershell
python scripts/smoke_test.py --api-key $env:ADAPTER_GATEWAY_API_KEY --model mock-chat
```

把 `ADAPTER_PROVIDER` 切换为 `runninghub` 并填入授权凭据后，再用真实模型 ID 执行同一脚本。
