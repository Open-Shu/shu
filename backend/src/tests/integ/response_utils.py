"""
Test utilities for handling standardized API response envelopes.

These helpers are intentionally minimal and Python-only for use in tests.
They mirror the envelope format defined in docs/policies/API_RESPONSE_STANDARD.md
and src/shu/core/response.py without depending on frontend utilities.
"""
from typing import Any, Dict


def extract_data(resp_or_json: Any) -> Any:
    """
    Extract the actual payload from a SuccessResponse envelope.

    Accepts either a httpx/fastapi Response-like object (with .json()) or
    a plain dict already parsed from JSON.

    Rules:
    - If object has .json(), call it and continue.
    - If the resulting object is a dict with a "data" key, return that.
    - Otherwise, return the object as-is.
    """
    data = resp_or_json
    # Handle Response-like objects that provide .json()
    if hasattr(resp_or_json, "json") and callable(getattr(resp_or_json, "json")):
        try:
            data = resp_or_json.json()
        except Exception:
            # If .json() fails, keep original object
            data = resp_or_json

    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data

