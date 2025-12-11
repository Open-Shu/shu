# LLM Provider Contract (Policies & Known Issues)

Implementation Status: Partial

## Overview
This document defines current policies for LLM provider integration in Shu. Providers are code-backed adapters (see `shu/services/providers/adapters/*`) selected by a minimal ProviderTypeDefinition row (`key`, `display_name`, `provider_adapter_name`). Adapters own request construction, streaming parsing, tool-calling shape, and model discovery.

## Policy: Parameter Source (Overrides-only)
- Chat requests accept no ad-hoc per-request parameters from the API/UI.
- All LLM parameters come from ModelConfiguration.parameter_overrides (admin-only).
- Effective params are the stored overrides after pruning null/empty values.

## Validation Rules
- Each adapter exposes `get_parameter_mapping`; that mapping is used for typing metadata for known keys.
- Only typed keys in parameter_mapping are validated (type, enum, optional min/max if present).
- Unknown keys are allowed and passed through without validation by design.
- Null/None values are omitted and not sent to providers.

## Mapping Rules
- Normalized keys from parameter_overrides are mapped directly into the provider payload.
- No default values are applied; unspecified keys are omitted.
- Messages/model/stream flags are treated as "hidden" transport fields and are not part of overrides validation.

## Endpoint Options
- Adapters expose `get_endpoint_settings()` to drive UI defaults. Current contract:
  - `chat.path`: base chat endpoint for the provider.
  - `models.path`: model discovery endpoint.
  - `models.options.get_model_information_path`: JMESPath to extract model ids/display names.
- Provider-level overrides are stored in `provider.config` as `get_api_base_url`, `get_chat_endpoint`, `get_models_endpoint`, and `get_model_information_path` (plus any adapter-specific options). These are passed directly to the adapter’s override hooks; dot/bracket message paths and streaming output paths are no longer user-configurable.

## Streaming Hints
- Streaming/event parsing is adapter-owned. There is no configurable streaming hint; adapters must emit normalized ProviderEventResult objects (content deltas, tool calls, reasoning deltas, final/error).

## Admin & RBAC
- Provider Types: exposed read-only via GET (/llm/provider-types, /llm/provider-types/{key}); contain no secrets.
- Admins manage operational settings at the provider level:
  - api_endpoint is initialized from the adapter’s `get_api_base_url`; admins may override via provider config (`get_api_base_url`) or edit `get_chat_endpoint`/`get_models_endpoint`/`get_model_information_path`.
  - endpoints overrides are expressed as flat fields in the provider payload (no legacy `endpoints_override` object).
  - Provider capabilities (streaming/tools/vision) are surfaced from adapters and can be toggled per-provider; toggles are advisory and consumed by adapters/services.
- Provider CRUD and model CRUD are admin-only; audited via existing API auth.

## Logging & Redaction
- Do not log override values. Log only key names.
- Redact obvious secrets (api_key, token, authorization) anywhere they may appear.

## Known Issues / Discrepancies
- One internal backend path (conversation summary) still supplies per-request llm_params to the client. UI does not expose per-chat params; policy remains overrides-only. This path will be removed/updated in a future task.
- Provider Types are not editable via API/UI; edits are per-provider (api_endpoint and adapter override fields). If provider-type edits are desired later, they must be admin-only and audited.
- AWS Bedrock is not yet supported in adapters; tasks will add explicit routing and SigV4 signing.

## Frontend Notes (current behavior)
- UI loads Provider Types read-only to scaffold api_endpoint, endpoints, and capabilities.
- Admins edit providers (PUT/POST /llm/providers) and set override fields (api_endpoint, chat/models paths, model information path, capabilities); Advanced JSON is allowed for parameter_overrides in Model Configurations, with typed controls rendered from adapter parameter_mapping.

## References
- See docs/contracts/PLUGIN_CONTRACT.md for general plugin host/validation behavior.
- See src/shu/llm/param_mapping.py for merge/validate/mapping semantics.
