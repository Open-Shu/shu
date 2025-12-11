"""
Dot/bracket path accessor utilities.

Provides reusable helpers to get/set values in nested dict/list structures using
paths like "choices[0].message.content". Designed to be small and dependency-free.
"""
from typing import Any, List, Union, Optional, Dict


class DotPath:
    """Utility for working with dot/bracket paths on JSON-like objects."""

    @staticmethod
    def tokenize(path: str) -> List[Union[str, int]]:
        """Tokenize a dot/bracket path into keys and indices.

        Example: "choices[0].message.content" -> ["choices", 0, "message", "content"]
        """
        tokens: List[Union[str, int]] = []
        buf = ""
        i = 0
        while i < len(path):
            c = path[i]
            if c == "[":
                if buf:
                    tokens.append(buf)
                    buf = ""
                j = path.find("]", i + 1)
                if j == -1:
                    raise ValueError("Unbalanced bracket in path")
                idx_str = path[i + 1 : j].strip()
                try:
                    idx = int(idx_str)
                except ValueError as exc:
                    raise ValueError(f"Non-integer index in bracket path: {idx_str}") from exc
                tokens.append(idx)
                i = j + 1
            elif c == ".":
                if buf:
                    tokens.append(buf)
                    buf = ""
                i += 1
            else:
                buf += c
                i += 1
        if buf:
            tokens.append(buf)
        return tokens

    @staticmethod
    def get(obj: Any, path: Optional[str], default: Any = None) -> Any:
        """Retrieve a nested value using dot/bracket path.

        If the path is None or empty, returns the object itself.
        Returns `default` when any step is missing or type does not match.
        """
        if path is None or path == "":
            return obj
        try:
            tokens = DotPath.tokenize(path)
        except Exception:
            return default
        cur: Any = obj
        for t in tokens:
            if isinstance(t, int):
                if isinstance(cur, list):
                    try:
                        cur = cur[t]
                    except IndexError:
                        return default
                else:
                    return default
            else:
                if isinstance(cur, dict) and t in cur:
                    cur = cur[t]
                else:
                    return default
        return cur

    @staticmethod
    def set(obj: Dict[str, Any], path: str, value: Any) -> None:
        """Set a value into a dict using dot/bracket path; creates containers as needed.

        - Dicts are created for key steps when missing
        - Lists are extended with None when assigning/traversing indices beyond length
        """
        if not path:
            raise ValueError("Empty path")
        tokens = DotPath.tokenize(path)
        cur: Any = obj
        for idx, t in enumerate(tokens):
            is_last = idx == len(tokens) - 1
            if is_last:
                if isinstance(t, int):
                    if not isinstance(cur, list):
                        raise ValueError(f"Expected list when setting index {t}")
                    while len(cur) <= t:
                        cur.append(None)
                    cur[t] = value
                else:
                    if not isinstance(cur, dict):
                        raise ValueError(f"Expected dict when setting key '{t}'")
                    cur[t] = value
            else:
                nxt = tokens[idx + 1]
                if isinstance(t, int):
                    if not isinstance(cur, list):
                        raise ValueError(f"Expected list when traversing index {t}")
                    while len(cur) <= t:
                        cur.append([] if isinstance(nxt, int) else {})
                    cur = cur[t]
                else:
                    if not isinstance(cur, dict):
                        raise ValueError(f"Expected dict when traversing key '{t}'")
                    if t not in cur or not isinstance(cur[t], (dict, list)):
                        cur[t] = [] if isinstance(nxt, int) else {}
                    cur = cur[t]

