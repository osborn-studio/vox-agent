# Windows LLM providers share one runtime contract

## Status

Accepted

## Date

2026-07-19

## Context

The compatibility runtime had separate hard-coded HTTP implementations in the
voice polisher and digest enhancer. They supported only OpenAI, Qwen, and
Ollama, selected providers with substring checks, and disagreed about endpoint
regions and response handling. The Windows settings workspace could not select
LLM main/fallback routes or configure Doubao, Claude, or a private
OpenAI-compatible endpoint.

That architecture reproduced individual VoxLog clients rather than providing a
Vox Agent execution control plane. It also made provider failures difficult to
qualify consistently and encouraged credentials to be added as one-off fields.

## Decision

The Python compatibility runtime owns one allowlisted LLM provider resolver and
one bounded asynchronous client:

- OpenAI, Qwen Bailian, Doubao ARK, custom OpenAI-compatible endpoints, and
  Ollama use the chat-completions contract;
- Claude uses the Anthropic Messages contract behind the same client interface;
- new installations start with current, editable model defaults (`gpt-5-mini`,
  `qwen3.7-plus`, `doubao-seed-2-0-lite-260215`, and `claude-sonnet-5`);
- the client omits `temperature` for Claude and GPT-5 requests because current
  Claude Sonnet 5 rejects non-default sampling parameters and GPT-5 models do
  not share the legacy chat model's sampling contract;
- the polisher and digest enhancer resolve the active profile route and call the
  shared client;
- provider error messages include only provider identity and HTTP status, never
  response bodies, request bodies, or credentials;
- custom endpoints accept only bounded HTTPS URLs, or HTTP on loopback, without
  embedded credentials, query parameters, or fragments;
- main and fallback routes are distinct, allowlisted, and persisted per profile
  by the Tauri shell;
- API keys remain in the operating-system credential store. Models, routes, and
  the custom base URL are the only provider metadata stored in desktop settings.

Provider configuration is not live evidence. The qualification matrix reports
configured cloud adapters as `configured_unverified`; explicit credentialed
Windows smoke requests remain the separate WIN-4D gate.

## Consequences

- Adding another OpenAI-compatible provider no longer requires another polisher
  or digest HTTP implementation.
- Qwen region selection follows the configured CN or US credential instead of
  always using the CN endpoint.
- Windows users can audit and change LLM main/fallback routing without editing
  environment files.
- Custom provider configuration is deliberately less permissive than a raw URL
  field. URLs containing credentials or query secrets are rejected.
- Anthropic remains a distinct wire protocol even though it shares routing,
  validation, error handling, and settings with the compatible providers.
- Live calls are never made during startup or static qualification refresh.

## References

- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat/create)
- [Alibaba Cloud Model Studio base URLs](https://www.alibabacloud.com/help/en/model-studio/base-url)
- [Volcano Engine ARK ChatCompletions API](https://api.volcengine.com/api-docs/view?action=ChatCompletions&serviceCode=ark&version=2024-01-01)
- [Claude Sonnet 5 migration notes](https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
