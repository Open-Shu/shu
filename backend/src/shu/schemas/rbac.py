"""RBAC (Role-Based Access Control) Schemas.

This module contains Pydantic schemas for RBAC operations including
user groups, memberships, and access control management.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from ..models.rbac import GroupRole


# User Group Schemas
class UserGroupBase(BaseModel):
    """Base schema for user groups."""

    name: str = Field(..., min_length=1, max_length=255, description="Group name")
    description: str | None = Field(None, max_length=1000, description="Group description")
    is_active: bool = Field(True, description="Whether the group is active")


class UserGroupCreate(UserGroupBase):
    """Schema for creating a new user group."""

    pass


class UserGroupUpdate(BaseModel):
    """Schema for updating a user group."""

    name: str | None = Field(None, min_length=1, max_length=255, description="Group name")
    description: str | None = Field(None, max_length=1000, description="Group description")
    is_active: bool | None = Field(None, description="Whether the group is active")


class UserGroupResponse(UserGroupBase):
    """Schema for user group responses."""

    id: str = Field(..., description="Group ID")
    created_by: str = Field(..., description="ID of user who created the group")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    member_count: int | None = Field(None, description="Number of members in the group")

    class Config:
        """Configure Pydantic to work with ORM objects."""

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

    role: GroupRole | None = Field(None, description="Role within the group")
    is_active: bool | None = Field(None, description="Whether the membership is active")


class UserGroupMembershipResponse(UserGroupMembershipBase):
    """Schema for group membership responses."""

    id: str = Field(..., description="Membership ID")
    user_id: str = Field(..., description="User ID")
    group_id: str = Field(..., description="Group ID")
    is_active: bool = Field(..., description="Whether the membership is active")
    granted_by: str = Field(..., description="ID of user who granted membership")
    granted_at: datetime = Field(..., description="When membership was granted")

    # Optional user details for convenience
    user_email: str | None = Field(None, description="User email")
    user_name: str | None = Field(None, description="User display name")
    group_name: str | None = Field(None, description="Group name")

    class Config:
        """Configure Pydantic to work with ORM objects."""

        from_attributes = True


class UserGroupListResponse(BaseModel):
    """Schema for paginated user group list responses."""

    groups: list[UserGroupResponse] = Field(..., description="List of user groups")
    total_count: int = Field(..., description="Total number of groups")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")


class UserGroupMembershipListResponse(BaseModel):
    """Schema for group membership list responses."""

    memberships: list[UserGroupMembershipResponse] = Field(..., description="List of memberships")
    total_count: int = Field(..., description="Total number of memberships")
