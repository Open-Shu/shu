"""Services package for Shu RAG Backend.

Services are imported from their individual submodules
(`from shu.services.foo_service import FooService`), not via this package
namespace. Keeping `__init__.py` minimal avoids forcing every importer of
any one service to load the full chat/LLM/billing dependency stack — which
previously closed a circular import through
`shu.billing.billing_state_cache → billing_state_persister → services →
llm.client → billing.enforcement`.
"""
