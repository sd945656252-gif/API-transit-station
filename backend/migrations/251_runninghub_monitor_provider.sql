-- Add RunningHub to the channel monitor provider allow-list.
-- RunningHub is intentionally a monitor provider only; it does not add a
-- subscription/account platform because its API key is supplied by the
-- monitor itself.

ALTER TABLE channel_monitors
    DROP CONSTRAINT IF EXISTS channel_monitors_provider_check;

ALTER TABLE channel_monitors
    ADD CONSTRAINT channel_monitors_provider_check
    CHECK (provider IN ('openai', 'anthropic', 'gemini', 'grok', 'antigravity',
                       'kimi', 'zhipu', 'deepseek', 'minimax', 'opencode_go',
                       'runninghub'));

ALTER TABLE channel_monitor_request_templates
    DROP CONSTRAINT IF EXISTS channel_monitor_request_templates_provider_check;

ALTER TABLE channel_monitor_request_templates
    ADD CONSTRAINT channel_monitor_request_templates_provider_check
    CHECK (provider IN ('openai', 'anthropic', 'gemini', 'grok', 'antigravity',
                       'kimi', 'zhipu', 'deepseek', 'minimax', 'opencode_go',
                       'runninghub'));
