package admin

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/Wei-Shaw/sub2api/internal/pkg/response"
	"github.com/gin-gonic/gin"
)

// Only an operator-configured internal service may perform Docker updates.
// The browser cannot choose a host, repository, image or command.
func managedUpdatesEnabled() bool {
	return strings.TrimSpace(os.Getenv("MANAGED_UPDATER_URL")) != ""
}

func managedUpdateRequest(ctx context.Context, method, path string, payload any) (map[string]any, error) {
	base, err := url.Parse(strings.TrimSpace(os.Getenv("MANAGED_UPDATER_URL")))
	if err != nil || base == nil || base.Host == "" || base.User != nil ||
		(base.Scheme != "http" && base.Scheme != "https") {
		return nil, fmt.Errorf("managed updater URL is invalid")
	}
	tokenFile := strings.TrimSpace(os.Getenv("MANAGED_UPDATER_TOKEN_FILE"))
	token, err := os.ReadFile(tokenFile)
	if err != nil || len(strings.TrimSpace(string(token))) < 24 {
		return nil, fmt.Errorf("managed updater credential is unavailable")
	}
	var body io.Reader
	if payload != nil {
		data, marshalErr := json.Marshal(payload)
		if marshalErr != nil {
			return nil, marshalErr
		}
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(base.String(), "/")+path, body)
	if err != nil {
		return nil, fmt.Errorf("cannot create managed updater request")
	}
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(token)))
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{
		Timeout:       40 * time.Second,
		Transport:     &http.Transport{Proxy: nil},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	defer client.CloseIdleConnections()
	res, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("managed update service is unreachable; current service was not replaced")
	}
	defer func() { _ = res.Body.Close() }()
	var data map[string]any
	if err := json.NewDecoder(io.LimitReader(res.Body, 64<<10)).Decode(&data); err != nil {
		return nil, fmt.Errorf("managed updater returned invalid data")
	}
	if res.StatusCode >= 300 {
		if message, ok := data["error"].(string); ok {
			return nil, fmt.Errorf("managed updater: %s", message)
		}
		return nil, fmt.Errorf("managed updater returned HTTP %d", res.StatusCode)
	}
	return data, nil
}

// GetUpdateStatus is under the same admin-only router as the update endpoint.
func (h *SystemHandler) GetUpdateStatus(c *gin.Context) {
	if !managedUpdatesEnabled() {
		response.Success(c, gin.H{"state": "disabled", "stage": "disabled"})
		return
	}
	data, err := managedUpdateRequest(c.Request.Context(), http.MethodGet, "/status", nil)
	if err != nil {
		response.Error(c, http.StatusBadGateway, err.Error())
		return
	}
	response.Success(c, data)
}
