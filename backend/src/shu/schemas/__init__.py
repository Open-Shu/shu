"""
Pydantic schemas for Shu RAG Backend.

This package contains Pydantic models for request/response validation
and serialization.
"""

from .knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseResponse,
    KnowledgeBaseList,
)
from .document import (
    DocumentResponse,
    DocumentList,
    DocumentChunkResponse,
)
from .query import (
    QueryRequest,
    QueryResponse,
    QueryResult,
)
from .envelope import (
    SuccessResponse,
    ErrorResponse,
)
from .rbac import (
    UserGroupCreate,
    UserGroupUpdate,
    UserGroupResponse,
    UserGroupListResponse,
    UserGroupMembershipCreate,
    UserGroupMembershipUpdate,
    UserGroupMembershipResponse,
    UserGroupMembershipListResponse,
    KnowledgeBasePermissionCreate,
    KnowledgeBasePermissionUpdate,
    KnowledgeBasePermissionResponse,
    KnowledgeBasePermissionListResponse,
    EffectivePermissionResponse,
    BulkPermissionCreate,
    BulkPermissionResponse,
)
from .side_call import (
    SideCallConfigRequest,
    SideCallConfigResponse,
    SideCallModelResponse,
    ConversationAutomationRequest,
    ConversationSummaryPayload,
    ConversationRenamePayload,
)

__all__ = [
    "KnowledgeBaseCreate",
    "KnowledgeBaseUpdate", 
    "KnowledgeBaseResponse",
    "KnowledgeBaseList",
    "DocumentResponse",
    "DocumentList",
    "DocumentChunkResponse",
    "QueryRequest",
    "QueryResponse",
    "QueryResult",
    "SuccessResponse",
    "ErrorResponse",
    "UserGroupCreate",
    "UserGroupUpdate",
    "UserGroupResponse",
    "UserGroupListResponse",
    "UserGroupMembershipCreate",
    "UserGroupMembershipUpdate",
    "UserGroupMembershipResponse",
    "UserGroupMembershipListResponse",
    "KnowledgeBasePermissionCreate",
    "KnowledgeBasePermissionUpdate",
    "KnowledgeBasePermissionResponse",
    "KnowledgeBasePermissionListResponse",
    "EffectivePermissionResponse",
    "BulkPermissionCreate",
    "BulkPermissionResponse",
    "SideCallConfigRequest",
    "SideCallConfigResponse",
    "SideCallModelResponse",
    "ConversationAutomationRequest",
    "ConversationSummaryPayload",
    "ConversationRenamePayload",
]
