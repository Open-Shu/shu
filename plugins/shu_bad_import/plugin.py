from __future__ import annotations

# Intentional invalid import to trigger static scan
from shu.core import config  # noqa: F401


class BadImportPlugin:
    name = "bad_import"
    version = "0"

    def get_schema(self):
        return None

    async def execute(self, params, context, host):
        # This should never run because static scan should block loading this plugin
        return {"status": "should_not_run"}

