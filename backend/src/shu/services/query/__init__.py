"""Query service package.

This package provides mixin modules that compose the QueryService class:

- base.py: Shared utilities, preprocessing, and base class (QueryServiceBase)
- similarity.py: Vector similarity search (SimilaritySearchMixin)
- keyword.py: Keyword-based search with title weighting (KeywordSearchMixin)
- hybrid.py: Combined similarity + keyword search (HybridSearchMixin)
- multi_surface.py: Multi-surface search orchestration (MultiSurfaceSearchMixin)
- constants.py: Stop word sets

QueryService is composed from these mixins in query_service.py (one level up).
Import it via: ``from shu.services.query_service import QueryService``
"""
