package domain

import (
	"testing"

	"github.com/stretchr/testify/require"
)

// Explicit contract expectations cover the current platform registry and ordering.
var (
	expectedDisplayOrder = []string{
		PlatformAnthropic, PlatformOpenAI, PlatformGemini, PlatformAntigravity, PlatformGrok,
		PlatformKimi, PlatformZhipu, PlatformDeepseek, PlatformMiniMax, PlatformOpenCodeGo, PlatformTypeSafe,
		PlatformCommandCode,
		PlatformCline, PlatformRunningHub,
	}
	expectedCompositePrecedence = []string{
		PlatformAnthropic, PlatformGemini, PlatformOpenAI, PlatformAntigravity, PlatformGrok,
		PlatformKimi, PlatformZhipu, PlatformDeepseek, PlatformMiniMax, PlatformOpenCodeGo, PlatformTypeSafe,
		PlatformCommandCode,
		PlatformCline, PlatformRunningHub,
	}
	expectedOpenAIGateway = []string{
		PlatformOpenAI, PlatformGrok, PlatformKimi, PlatformZhipu, PlatformDeepseek, PlatformMiniMax, PlatformOpenCodeGo,
		PlatformCommandCode,
		PlatformCline, PlatformRunningHub,
	}
	expectedCNProviders       = []string{PlatformKimi, PlatformZhipu, PlatformDeepseek, PlatformMiniMax}
	expectedLiteLLMByPlatform = map[string]string{
		PlatformAnthropic: "anthropic", PlatformOpenAI: "openai", PlatformGemini: "gemini",
		PlatformAntigravity: "anthropic", PlatformGrok: "xai", PlatformKimi: "moonshot",
		PlatformZhipu: "zhipu", PlatformDeepseek: "deepseek", PlatformMiniMax: "minimax",
		PlatformOpenCodeGo: "opencode-go", PlatformTypeSafe: "typesafe", PlatformCommandCode: "", PlatformCline: "", PlatformRunningHub: "",
	}
)

func TestPlatformListMatchesContract(t *testing.T) {
	require.Equal(t, expectedDisplayOrder, ConcretePlatformIDs())
	require.Equal(t, expectedCompositePrecedence, CompositePrecedencePlatformIDs())
	require.Equal(t, expectedOpenAIGateway, PlatformIDsWhere(func(spec PlatformSpec) bool {
		return spec.Gateway == PlatformGatewayOpenAI
	}))
	require.Equal(t, expectedCNProviders, PlatformIDsWhere(func(spec PlatformSpec) bool { return spec.CNProvider }))
	for platform, provider := range expectedLiteLLMByPlatform {
		spec, ok := LookupPlatform(platform)
		require.True(t, ok, platform)
		require.Equal(t, provider, spec.LiteLLMProvider, platform)
	}
}

func TestPlatformListPredicates(t *testing.T) {
	for _, platform := range expectedDisplayOrder {
		require.True(t, IsConcretePlatform(platform), platform)
		require.True(t, IsGroupPlatform(platform), platform)
		spec, ok := LookupPlatform(platform)
		require.True(t, ok, platform)
		require.NotEmpty(t, spec.DisplayName, platform)
		require.NotEmpty(t, spec.Gateway, platform)
	}
	require.False(t, IsConcretePlatform(PlatformComposite))
	require.True(t, IsGroupPlatform(PlatformComposite))
	for _, invalid := range []string{"", "moonshot", "Kimi", "openai ", "glm", "bogus"} {
		require.False(t, IsConcretePlatform(invalid), invalid)
		require.False(t, IsGroupPlatform(invalid), invalid)
		require.False(t, UsesOpenAIGateway(invalid), invalid)
	}
}

func TestPlatformsReturnsCopy(t *testing.T) {
	platforms := Platforms()
	platforms[0].ID = "mutated"
	require.Equal(t, PlatformAnthropic, Platforms()[0].ID)
}
