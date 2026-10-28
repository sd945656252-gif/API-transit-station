package handler

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/server/middleware"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
	"github.com/tidwall/gjson"
)

// RunningHubGatewayHandler 透传 RunningHub 的原生任务 API。
// 客户端只携带 Sub2API API Key；适配器在上游请求体中注入被选账号的 RunningHub key，
// 并用 taskId 绑定粘性会话，确保后续 status/outputs 请求回到同一个上游账号。
type RunningHubGatewayHandler struct {
	gatewayService *service.GatewayService
	client         *http.Client
}

func NewRunningHubGatewayHandler(gatewayService *service.GatewayService) *RunningHubGatewayHandler {
	return &RunningHubGatewayHandler{
		gatewayService: gatewayService,
		client:         &http.Client{Timeout: 5 * time.Minute},
	}
}

func (h *RunningHubGatewayHandler) AIAppRun(c *gin.Context) {
	h.proxy(c, "/task/openapi/ai-app/run", true)
}

func (h *RunningHubGatewayHandler) Create(c *gin.Context) { h.proxy(c, "/task/openapi/create", true) }

func (h *RunningHubGatewayHandler) Status(c *gin.Context) { h.proxy(c, "/task/openapi/status", false) }

func (h *RunningHubGatewayHandler) Outputs(c *gin.Context) {
	h.proxy(c, "/task/openapi/outputs", false)
}

func (h *RunningHubGatewayHandler) proxy(c *gin.Context, upstreamPath string, bindTask bool) {
	apiKey, ok := middleware.GetAPIKeyFromContext(c)
	if !ok || apiKey == nil || apiKey.Group == nil || apiKey.Group.Platform != service.PlatformRunningHub {
		c.JSON(http.StatusNotFound, gin.H{"error": gin.H{"type": "not_found_error", "message": "RunningHub is not enabled for this API key group"}})
		return
	}

	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": gin.H{"type": "invalid_request_error", "message": "failed to read request body"}})
		return
	}
	if !gjson.ValidBytes(body) {
		c.JSON(http.StatusBadRequest, gin.H{"error": gin.H{"type": "invalid_request_error", "message": "request body must be valid JSON"}})
		return
	}

	taskID := strings.TrimSpace(gjson.GetBytes(body, "taskId").String())
	if taskID == "" {
		taskID = strings.TrimSpace(gjson.GetBytes(body, "task_id").String())
	}
	if !bindTask && taskID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": gin.H{"type": "invalid_request_error", "message": "taskId is required"}})
		return
	}
	// 提交请求使用 workflow/app 标识作为稳定的初始会话键；任务查询使用 taskId。
	sessionHash := "runninghub-native"
	if !bindTask && taskID != "" {
		sessionHash = "runninghub-task:" + taskID
	}
	account, err := h.gatewayService.SelectAccountForModel(c.Request.Context(), apiKey.GroupID, sessionHash, "")
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": gin.H{"type": "upstream_error", "message": "no available RunningHub account"}})
		return
	}
	upstreamKey := strings.TrimSpace(account.GetCredential("api_key"))
	if upstreamKey == "" {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": gin.H{"type": "upstream_error", "message": "RunningHub account has no API key"}})
		return
	}

	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil || payload == nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": gin.H{"type": "invalid_request_error", "message": "request body must be a JSON object"}})
		return
	}
	payload["apiKey"] = upstreamKey
	body, err = json.Marshal(payload)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": gin.H{"type": "server_error", "message": "failed to prepare upstream request"}})
		return
	}

	baseURL := strings.TrimSpace(account.GetCredential("runninghub_base_url"))
	if baseURL == "" {
		baseURL = strings.TrimSpace(account.GetCredential("base_url"))
		// The account form uses the LLM-compatible base URL for OpenAI traffic;
		// native task endpoints live on the main RunningHub host.
		if strings.Contains(strings.ToLower(baseURL), "llm.runninghub.") {
			baseURL = ""
		}
	}
	if baseURL == "" {
		baseURL = service.DefaultRunningHubBaseURL
	}
	baseURL = strings.TrimRight(baseURL, "/")
	u, err := url.Parse(baseURL + upstreamPath)
	if err != nil || u.Scheme == "" || u.Host == "" {
		c.JSON(http.StatusBadGateway, gin.H{"error": gin.H{"type": "upstream_error", "message": "invalid RunningHub base URL"}})
		return
	}
	u.RawQuery = c.Request.URL.RawQuery
	upstreamReq, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, u.String(), bytes.NewReader(body))
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": gin.H{"type": "upstream_error", "message": "failed to create upstream request"}})
		return
	}
	for key, values := range c.Request.Header {
		lower := strings.ToLower(key)
		if lower == "authorization" || lower == "x-api-key" || lower == "content-length" || lower == "host" {
			continue
		}
		for _, value := range values {
			upstreamReq.Header.Add(key, value)
		}
	}
	upstreamReq.Header.Set("Authorization", "Bearer "+upstreamKey)
	upstreamReq.Header.Set("Content-Type", "application/json")

	resp, err := h.client.Do(upstreamReq)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": gin.H{"type": "upstream_error", "message": "RunningHub request failed"}})
		return
	}
	defer resp.Body.Close()
	for key, values := range resp.Header {
		if strings.EqualFold(key, "Content-Length") || strings.EqualFold(key, "Transfer-Encoding") {
			continue
		}
		for _, value := range values {
			c.Writer.Header().Add(key, value)
		}
	}
	c.Status(resp.StatusCode)
	responseBody, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		return
	}
	_, _ = c.Writer.Write(responseBody)

	if bindTask && resp.StatusCode >= 200 && resp.StatusCode < 300 {
		returnedTaskID := strings.TrimSpace(gjson.GetBytes(responseBody, "taskId").String())
		if returnedTaskID == "" {
			returnedTaskID = strings.TrimSpace(gjson.GetBytes(responseBody, "task_id").String())
		}
		if returnedTaskID != "" {
			_ = h.gatewayService.BindStickySession(c.Request.Context(), apiKey.GroupID, "runninghub-task:"+returnedTaskID, account.ID)
		}
	}
}
