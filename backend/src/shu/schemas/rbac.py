"""
RBAC (Role-Based Access Control) Schemas

This module contains Pydantic schemas for RBAC operations including
user groups, permissions, and access control management.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, validator, model_validator
from enum import Enum

from ..models.rbac import PermissionLevel, GroupRole


# User Group Schemas
class UserGroupBase(BaseModel):
    """Base schema for user groups."""
    name: str = Field(..., min_length=1, max_length=255, description="Group name")
    description: Optional[str] = Field(None, max_length=1000, description="Group description")
    is_active: bool = Field(True, description="Whether the group is active")


class UserGroupCreate(UserGroupBase):
    """Schema for creating a new user group."""
    pass


class UserGroupUpdate(BaseModel):
    """Schema for updating a user group."""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Group name")
    description: Optional[str] = Field(None, max_length=1000, description="Group description")
    is_active: Optional[bool] = Field(None, description="Whether the group is active")


class UserGroupResponse(UserGroupBase):
    """Schema for user group responses."""
    id: str = Field(..., description="Group ID")
    created_by: str = Field(..., description="ID of user who created the group")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    member_count: Optional[int] = Field(None, description="Number of members in the group")

    class Config:
        from_attributes = True


# User Group Membership Schemas
class UserGroupMembershipBase(BaseModel):
    """Base schema for group membership."""
    role: GroupRole = Field(GroupRole.MEMBER, description="Role within the group")


class UserGroupMembershipCreate(UserGroupMembershipBase):
    """Schema for adding a user to a group."""
    user_id: str = Field(..., description="ID of user to add to group")


class UserGroupMembershipUpdate(BaseModel):
    """Schema for updating group membership."""
    role: Optional[GroupRole] = Field(None, description="Role within the group")
    is_active: Optional[bool] = Field(None, description="Whether the membership is active")


class UserGroupMembershipResponse(UserGroupMembershipBase):
    """Schema for group membership responses."""
    id: str = Field(..., description="Membership ID")
    user_id: str = Field(..., description="User ID")
    group_id: str = Field(..., description="Group ID")
    is_active: bool = Field(..., description="Whether the membership is active")
    granted_by: str = Field(..., description="ID of user who granted membership")
    granted_at: datetime = Field(..., description="When membership was granted")
    
    # Optional user details for convenience
    user_email: Optional[str] = Field(None, description="User email")
    user_name: Optional[str] = Field(None, description="User display name")
    group_name: Optional[str] = Field(None, description="Group name")

    class Config:
        from_attributes = True


# Knowledge Base Permission Schemas
class KnowledgeBasePermissionBase(BaseModel):
    """Base schema for KB permissions."""
    permission_level: PermissionLevel = Field(..., description="Permission level")
    expires_at: Optional[datetime] = Field(None, description="Optional expiration time")


class KnowledgeBasePermissionCreate(KnowledgeBasePermissionBase):
    """Schema for creating a KB permission."""
    user_id: Optional[str] = Field(None, description="User ID (for user permissions)")
    group_id: Optional[str] = Field(None, description="Group ID (for group permissions)")

    @model_validator(mode='after')
    def validate_target(self):
        """Ensure either user_id OR group_id is provided, but not both."""
        user_id = self.user_id
        group_id = self.group_id
        
        if not user_id and not group_id:
            raise ValueError('Either user_id or group_id must be provided')
        if user_id and group_id:
            raise ValueError('Cannot specify both user_id and group_id')
        return self


class KnowledgeBasePermissionUpdate(BaseModel):
    """Schema for updating a KB permission."""
    permission_level: Optional[PermissionLevel] = Field(None, description="Permission level")
    is_active: Optional[bool] = Field(None, description="Whether the permission is active")
    expires_at: Optional[datetime] = Field(None, description="Optional expiration time")


class KnowledgeBasePermissionResponse(KnowledgeBasePermissionBase):
    """Schema for KB permission responses."""
    id: str = Field(..., description="Permission ID")
    knowledge_base_id: str = Field(..., description="Knowledge base ID")
    user_id: Optional[str] = Field(None, description="User ID (for user permissions)")
    group_id: Optional[str] = Field(None, description="Group ID (for group permissions)")
    is_active: bool = Field(..., description="Whether the permission is active")
    granted_by: str = Field(..., description="ID of user who granted permission")
    granted_at: datetime = Field(..., description="When permission was granted")
    
    # Optional details for convenience
    user_email: Optional[str] = Field(None, description="User email (for user permissions)")
    group_name: Optional[str] = Field(None, description="Group name (for group permissions)")
    kb_name: Optional[str] = Field(None, description="Knowledge base name")
    granter_name: Optional[str] = Field(None, description="Name of user who granted permission")

    class Config:
        from_attributes = True


# Effective Permission Schemas
class EffectivePermissionResponse(BaseModel):
    """Schema for effective permission responses."""
    user_id: str = Field(..., description="User ID")
    knowledge_base_id: str = Field(..., description="Knowledge base ID")
    effective_level: PermissionLevel = Field(..., description="Highest effective permission level")
    source: str = Field(..., description="Source of permission (owner/direct/group)")
    source_id: Optional[str] = Field(None, description="ID of permission source")
    expires_at: Optional[datetime] = Field(None, description="Earliest expiration time")


# List Response Schemas
class UserGroupListResponse(BaseModel):
    """Schema for paginated user group list responses."""
    groups: List[UserGroupResponse] = Field(..., description="List of user groups")
    total_count: int = Field(..., description="Total number of groups")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")


class UserGroupMembershipListResponse(BaseModel):
    """Schema for group membership list responses."""
    memberships: List[UserGroupMembershipResponse] = Field(..., description="List of memberships")
    total_count: int = Field(..., description="Total number of memberships")


class KnowledgeBasePermissionListResponse(BaseModel):
    """Schema for KB permission list responses."""
    permissions: List[KnowledgeBasePermissionResponse] = Field(..., description="List of permissions")
    total_count: int = Field(..., description="Total number of permissions")


# Bulk Operation Schemas
class BulkPermissionCreate(BaseModel):
    """Schema for bulk permission creation."""
    permissions: List[KnowledgeBasePermissionCreate] = Field(..., description="List of permissions to create")


class BulkPermissionResponse(BaseModel):
    """Schema for bulk operation responses."""
    created: List[KnowledgeBasePermissionResponse] = Field(..., description="Successfully created permissions")
    failed: List[dict] = Field(..., description="Failed permission creations with error details")
    total_requested: int = Field(..., description="Total number of permissions requested")
    total_created: int = Field(..., description="Total number of permissions created")
    total_failed: int = Field(..., description="Total number of failed creations")
