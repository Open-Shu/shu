"""Query service package.

This package provides the QueryService for document queries and search operations.
For backward compatibility, QueryService is re-exported from the original module.

Future work (SHU-627): Split into separate modules:
- base.py: Shared utilities and base class
- similarity.py: Vector similarity search
- keyword.py: Keyword-based search
- hybrid.py: Combined similarity + keyword search
- multi_surface.py: Multi-surface search orchestration
"""

# Re-export from original module for backward compatibility
from ..query_service import QueryService

__all__ = ["QueryService"]
