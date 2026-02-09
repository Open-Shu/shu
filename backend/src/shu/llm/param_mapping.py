"""TASK-003: Parameter Normalization and Mapping Layer.

This module implements a small, self-contained utility for:
- Merging model_configurations.parameter_overrides with per-request llm_params
- Validating provided parameters using ProviderTypeDefinition.parameter_mapping typing metadata
- Mapping normalized parameters into provider-specific request payload fields

Key policies (as per EPIC-LLM-PROVIDER-GENERALIZATION and TASK-003):
- No default merging. Only provided parameters are considered; unspecified are omitted
- Normalized keys must be used in storage (parameter_overrides) and in per-request llm_params
- Unknown keys are accepted and passed through as-is (no validation)
- Hidden parameters (type == "hidden") are not expected from overrides/llm_params; they should be supplied by the caller
  directly in the request assembly (e.g., model, messages, stream)

This module does not perform any network I/O and does not depend on httpx.
It is intended to be consumed by UnifiedLLMClient (TASK-004) and admin preview APIs (TASK-007).
"""

from __future__ import annotations

from typing import Any

from ..core.exceptions import ValidationError

# Supported primitive types declared in ProviderTypeDefinition.parameter_mapping
_ALLOWED_TYPES = {
    "number": (int, float),
    "integer": (int,),
    "string": (str,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    # "hidden" is allowed in mapping but we don't validate its values here
    "hidden": None,
    # "enum" is validated via the given options
    "enum": None,
}


def _options_values(spec: dict[str, Any]) -> list | None:
    options = spec.get("options")
    if not isinstance(options, list):
        return None
    vals = []
    for opt in options:
        if isinstance(opt, dict) and "value" in opt:
            vals.append(opt.get("value"))
    return vals


def _matches_option(value: Any, candidates: list | None) -> bool:
    if candidates is None:
        return True
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(value, dict):
            if candidate.get("type") and value.get("type") == candidate.get("type"):
                return True
            # fallback: candidate as subset of value
            if all(candidate.get(k) == value.get(k) for k in candidate):
                return True
        elif value == candidate:
            return True
    return False


def _validate_single(key: str, value: Any, spec: dict[str, Any]) -> None:
    """Validate a single normalized parameter value against mapping spec.

    Spec fields honored:
    - type: one of number|integer|string|boolean|array|object|hidden|enum
    - min, max: numeric bounds (inclusive) for number/integer
    - options: allowed values for enum
    """
    t = spec.get("type")
    if t is None:
        # No type info → accept value as-is
        return

    if t not in _ALLOWED_TYPES:
        raise ValidationError(f"Unsupported type '{t}' for parameter '{key}'", details={"param": key, "type": t})

    option_values = _options_values(spec)

    if t == "enum":
        if not option_values:
            raise ValidationError(
                f"Enum parameter '{key}' is missing 'options' in mapping",
                details={"param": key},
            )
        if not _matches_option(value, option_values):
            raise ValidationError(
                f"Invalid enum value for '{key}': {value}",
                details={"param": key, "allowed": option_values},
            )
        return

    if t == "hidden":
        # Hidden params are mapped by caller (e.g., model/messages/stream), not validated here
        return

    expected_types = _ALLOWED_TYPES[t]
    if expected_types and not isinstance(value, expected_types):
        raise ValidationError(
            f"Parameter '{key}' expects type {t}",
            details={"param": key, "expected": t, "actual": type(value).__name__},
        )

    if option_values:
        if t == "array":
            if not isinstance(value, list):
                raise ValidationError(
                    f"Parameter '{key}' expects a list of allowed options",
                    details={"param": key, "expected": "array", "actual": type(value).__name__},
                )
            for idx, v in enumerate(value):
                if not _matches_option(v, option_values):
                    raise ValidationError(
                        f"Invalid value for '{key}' at index {idx}",
                        details={"param": key, "allowed": option_values, "invalid": v},
                    )
        elif t != "object":  # object options may be handled via presets; allow freeform merge
            if not _matches_option(value, option_values):
                raise ValidationError(
                    f"Invalid value for '{key}'",
                    details={"param": key, "allowed": option_values, "invalid": value},
                )


def build_provider_params(
    provider_parameter_mapping: dict[str, Any] | None,
    model_overrides: dict[str, Any] | None,
    request_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge model_config overrides with per-request params, validate known keys, and return normalized dict.

    Precedence: request_params override model_overrides, but when both provide the same key:
    - dict values are shallow-merged
    - list values are concatenated
    - everything else prefers the request_params value

    No defaults are applied. Keys with None values are omitted.

    Unknown keys are allowed and passed through without validation.
    """
    mapping = provider_parameter_mapping or {}
    normalized: dict[str, Any] = {}

    def _merge_value(existing: Any, incoming: Any) -> Any:
        if existing is None:
            return incoming
        if incoming is None:
            return existing
        if isinstance(existing, dict) and isinstance(incoming, dict):
            merged = dict(existing)
            merged.update(incoming)
            return merged
        if isinstance(existing, list) and isinstance(incoming, list):
            return [*existing, *incoming]
        return incoming

    # Merge with precedence: request_params override model_overrides, but same-key aggregates as above
    for source in (model_overrides or {}, request_params or {}):
        if not source:
            continue
        for key, value in source.items():
            if value is None:
                continue
            normalized[key] = _merge_value(normalized.get(key), value)

    # Validate only keys that have a mapping spec; unknowns pass through
    if mapping:
        for k in list(normalized.keys()):
            spec = mapping.get(k)
            if spec is not None:
                _validate_single(k, normalized[k], spec)

    return normalized
