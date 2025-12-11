# Pytest unit tests

- Location: `backend/src/tests/unit`
- Run: `python -m pytest backend/src/tests/unit`
- Purpose: fast, isolated unit coverage (e.g., provider adapters, pure functions).
- Path setup: `conftest.py` adds `backend/src` to `sys.path` so you can `import shu...`.
