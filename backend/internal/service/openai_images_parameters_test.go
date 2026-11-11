package service

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
	"github.com/tidwall/gjson"
)

func TestOpenAIImagesAspectRatioOAuthGuidance(t *testing.T) {
	for _, ratio := range []string{"9:16", "16:9", "1:1"} {
		t.Run(ratio, func(t *testing.T) {
			body := []byte(fmt.Sprintf(`{"model":"gpt-image-2.5-sunburst","prompt":"a cup","aspect_ratio":%q}`, ratio))
			c, _ := newOpenAIImagesTestContext(t, body)
			upstream := &httpUpstreamRecorder{resp: openAIImagesJSONResponse()}
			svc := newOpenAIImagesTestService(upstream)
			parsed, err := svc.ParseOpenAIImagesRequest(c, body)
			require.NoError(t, err)
			_, err = svc.ForwardImages(context.Background(), c, directImagesTestAccount(), body, parsed, "")
			require.NoError(t, err)
			require.Contains(t, gjson.GetBytes(upstream.lastBody, "prompt").String(), "width:height = "+ratio)
			require.False(t, gjson.GetBytes(upstream.lastBody, "aspect_ratio").Exists(), "not an established upstream field")
			require.False(t, gjson.GetBytes(upstream.lastBody, "size").Exists(), "do not invent a pixel resolution")
			require.False(t, gjson.GetBytes(upstream.lastBody, "quality").Exists())
			responses, err := buildOpenAIImagesResponsesRequest(parsed, parsed.Model)
			require.NoError(t, err)
			require.Contains(t, gjson.GetBytes(responses, "input.0.content.0.text").String(), "width:height = "+ratio)
			require.Equal(t, body, parsed.Body, "retain original request for native API-key forwarding")
		})
	}
}

func TestOpenAIImagesAspectRatioValidation(t *testing.T) {
	for _, value := range []string{`null`, `9`, `{}`, `[]`, `""`, `"0:9"`, `"-1:9"`, `"16/9"`, `"9:16:1"`, `"1:10001"`, `"text:9"`} {
		t.Run(value, func(t *testing.T) {
			body := []byte(`{"prompt":"a cup","aspect_ratio":` + value + `}`)
			c, _ := newOpenAIImagesTestContext(t, body)
			_, err := newOpenAIImagesTestService(nil).ParseOpenAIImagesRequest(c, body)
			require.ErrorContains(t, err, "aspect_ratio")
		})
	}
	var body bytes.Buffer
	w := multipart.NewWriter(&body)
	require.NoError(t, w.WriteField("prompt", "a cup"))
	require.NoError(t, w.WriteField("aspect_ratio", " 18 : 32 "))
	require.NoError(t, w.Close())
	c, _ := newOpenAIImagesTestContext(t, body.Bytes())
	c.Request.Header.Set("Content-Type", w.FormDataContentType())
	parsed, err := newOpenAIImagesTestService(nil).ParseOpenAIImagesRequest(c, body.Bytes())
	require.NoError(t, err)
	require.Equal(t, "9:16", parsed.AspectRatio)
}

func TestOpenAIImagesAspectRatioLeavesExistingPromptUnchanged(t *testing.T) {
	body := []byte(`{"model":"gpt-image-2.5-sunburst","prompt":"  original prompt  ","size":"1024x1536","quality":"high"}`)
	c, _ := newOpenAIImagesTestContext(t, body)
	upstream := &httpUpstreamRecorder{resp: openAIImagesJSONResponse()}
	svc := newOpenAIImagesTestService(upstream)
	parsed, err := svc.ParseOpenAIImagesRequest(c, body)
	require.NoError(t, err)
	_, err = svc.ForwardImages(context.Background(), c, directImagesTestAccount(), body, parsed, "")
	require.NoError(t, err)
	require.Equal(t, "  original prompt  ", gjson.GetBytes(upstream.lastBody, "prompt").String())
}

func TestOpenAIImagesAspectRatioDoesNotRewriteAPIKeyRequest(t *testing.T) {
	body := []byte(`{"model":"gpt-image-2.5-sunburst","prompt":"a cup","aspect_ratio":"9:16"}`)
	c, _ := newOpenAIImagesTestContext(t, body)
	upstream := &httpUpstreamRecorder{resp: openAIImagesJSONResponse()}
	svc := newOpenAIImagesTestService(upstream)
	parsed, err := svc.ParseOpenAIImagesRequest(c, body)
	require.NoError(t, err)
	_, err = svc.ForwardImages(context.Background(), c, newOpenAIImagesAPIKeyAccount(), body, parsed, "")
	require.NoError(t, err)
	require.JSONEq(t, string(body), string(upstream.lastBody))
}

// These tests check gateway transport behavior, not upstream model capabilities.
func TestCodexDirectImagesPreservesExplicitGeometryAndQuality(t *testing.T) {
	for _, size := range []string{"1536x1024", "1024x1536", "3840x2160"} {
		t.Run(size, func(t *testing.T) {
			body := []byte(fmt.Sprintf(`{"model":"gpt-image-2.5-sunburst","prompt":"a cup","size":%q,"quality":"high"}`, size))
			c, _ := newOpenAIImagesTestContext(t, body)
			upstream := &httpUpstreamRecorder{resp: openAIImagesJSONResponse()}
			svc := newOpenAIImagesTestService(upstream)
			parsed, err := svc.ParseOpenAIImagesRequest(c, body)
			require.NoError(t, err)
			_, err = svc.ForwardImages(context.Background(), c, directImagesTestAccount(), body, parsed, "")
			require.NoError(t, err)
			require.Equal(t, size, gjson.GetBytes(upstream.lastBody, "size").String())
			require.Equal(t, "high", gjson.GetBytes(upstream.lastBody, "quality").String())
		})
	}
}

func TestCodexDirectImagesReportsActualOutputWhenUpstreamIgnoresParameters(t *testing.T) {
	body := []byte(`{"model":"gpt-image-2.5-sunburst","prompt":"a cup","size":"3840x2160","quality":"high"}`)
	c, recorder := newOpenAIImagesTestContext(t, body)
	encoded := encodeOpenAIImageTestPNG(t, 1536, 1024)
	response := fmt.Sprintf(`{"quality":"low","size":"3840x2160","data":[{"b64_json":%q}]}`, encoded)
	upstream := &httpUpstreamRecorder{resp: &http.Response{
		StatusCode: http.StatusOK,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(strings.NewReader(response)),
	}}
	svc := newOpenAIImagesTestService(upstream)
	parsed, err := svc.ParseOpenAIImagesRequest(c, body)
	require.NoError(t, err)
	result, err := svc.ForwardImages(context.Background(), c, directImagesTestAccount(), body, parsed, "")
	require.NoError(t, err)
	require.Equal(t, "3840x2160", gjson.GetBytes(upstream.lastBody, "size").String())
	require.Equal(t, "high", gjson.GetBytes(upstream.lastBody, "quality").String())
	require.Equal(t, "1536x1024", gjson.GetBytes(recorder.Body.Bytes(), "size").String())
	require.Equal(t, "1536x1024", gjson.GetBytes(recorder.Body.Bytes(), "data.0.size").String())
	require.Equal(t, "low", gjson.GetBytes(recorder.Body.Bytes(), "quality").String())
	require.Equal(t, []string{"1536x1024"}, result.ImageOutputSizes)
}
