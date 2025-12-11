# User-Specific API Key Authentication System

**Status**: PLANNING PHASE
**Risk Level**: LOW
Implementation Status: Not Implemented (design only; canonical implementation plan lives in SHU-121 task documentation).

Known Issues (design):
- Existing static API key system remains in place until this plan is implemented.
- Rate limiting, logging, and migration details may need adjustment during implementation.

Security Vulnerabilities (current state):
- Static API key flow lacks per-user attribution and fine-grained scopes.
- No dedicated security testing yet for API key flows; this must be added when SHU-121 is implemented.



## Executive Summary

This plan addresses the current authentication fragmentation by implementing a unified user-specific API key system that inherits user permissions. This will eliminate the confusion between the two existing `get_current_user` methods and provide proper external API access while maintaining security.

## Current Problem Analysis

### **Authentication Fragmentation**
Currently, Shu has two separate authentication systems:

1. **Static API Key System** (`src/shu/api/dependencies.py`)
   - Returns: `Optional[str]` (simple user ID)
   - Used by: a small set of legacy endpoints (groups, knowledge_base_sources, gmail, permissions, sync, query).
   - Note: the legacy `/api/v1/source-types` endpoint and underlying `source_types` registry table have been removed; this design doc predates that change.
   - Limitations: No user context, no granular permissions, no audit trail

2. **JWT User Authentication** (`src/shu/auth/rbac.py`)
   - Returns: `User` object with full permissions
   - Used by: 50+ endpoints (chat, auth, user_preferences, model_configuration, llm, etc.)
   - Features: Full user context, RBAC, audit trail

### **Security and Usability Issues**
- **No user context**: Static API keys can't associate actions with specific users
- **No granular permissions**: All API key access has the same permissions
- **No audit trail**: Can't track which user's API key was used
- **No rate limiting per user**: All API key usage shares the same rate limits
- **Security risk**: If compromised, affects entire system

## Proposed Solution

### **User-Specific API Key System**
Each user can create multiple API keys that inherit their permissions and have configurable restrictions.

### **Key Features**
- **User Context**: API keys tied to specific users with their permissions
- **Granular Control**: Each API key can have different rate limits and endpoint restrictions
- **Audit Trail**: Track which user's API key was used for each request
- **Security**: API keys can be revoked individually, have expiration dates
- **Flexibility**: Support for endpoint-specific permissions and rate limiting
- **Scalability**: Easy to add new permission types and restrictions

## Impact Analysis

### **Database Changes (MINIMAL IMPACT)**
```sql
-- New table (no breaking changes)
CREATE TABLE api_keys (...);

-- Add columns to existing table (backward compatible)
ALTER TABLE users ADD COLUMN rate_limit_rpm INTEGER DEFAULT 60;
ALTER TABLE users ADD COLUMN rate_limit_tpm INTEGER DEFAULT 60000;
```

**Impact**: MINIMAL - No breaking changes to existing data

### **Code Changes (LOW IMPACT)**
- **New Files**: 4 files (unified auth, API key models, services, context)
- **Modified Files**: 7 files (6 endpoint files + dependencies)
- **Removed Files**: 0 files (maintain backward compatibility)

**Impact**: LOW - Mostly new files, minimal changes to existing code

### **Testing Impact (MODERATE IMPACT)**
- **New Test Suites**: 3 test files needed
- **Existing Tests**: All JWT tests remain unchanged
- **Coverage**: Need to add API key CRUD and permission tests

**Impact**: MODERATE - Need to add new test suites

## Implementation Plan

### **Phase 1: Foundation (Days 1-2)**

#### **1.1 Database Schema**
- [ ] Create API key table migration
- [ ] Add rate limiting columns to users table
- [ ] Create database indexes for performance
- [ ] Test database migration rollback

#### **1.2 Core Models**
- [ ] Implement `APIKey` model with user relationships
- [ ] Add API key relationship to `User` model
- [ ] Implement key generation and hashing utilities
- [ ] Add permission checking methods

#### **1.3 Service Layer**
- [ ] Create `APIKeyService` for CRUD operations
- [ ] Implement key validation and usage tracking
- [ ] Add rate limiting calculation methods
- [ ] Implement endpoint permission checking

### **Phase 2: Authentication System (Days 3-4)**

#### **2.1 Unified Authentication**
- [ ] Create `UnifiedAuthentication` class
- [ ] Implement `AuthenticationContext` data class
- [ ] Add API key vs JWT detection logic
- [ ] Implement backward compatibility layer

#### **2.2 Permission System**
- [ ] Create permission inheritance logic
- [ ] Implement endpoint-specific restrictions
- [ ] Add rate limiting per authentication type
- [ ] Create audit logging system

#### **2.3 Middleware Integration**
- [ ] Update authentication middleware
- [ ] Add API key usage tracking
- [ ] Implement rate limiting middleware
- [ ] Add security headers

### **Phase 3: API Endpoints (Days 5-6)**

#### **3.1 API Key Management**
- [ ] Create API key CRUD endpoints
- [ ] Implement key creation with permissions
- [ ] Add key revocation and expiration
- [ ] Create key usage statistics endpoints

#### **3.2 Endpoint Migration**
- [ ] Update 6 endpoint files to use unified auth
- [ ] Test each migrated endpoint
- [ ] Add proper error handling
- [ ] Update API documentation

#### **3.3 Backward Compatibility**
- [ ] Maintain static API key support during transition
- [ ] Add configuration to disable static API key
- [ ] Create migration guide for external clients
- [ ] Add deprecation warnings

### **Phase 4: Testing & Documentation (Days 7-8)**

#### **4.1 Test Implementation**
- [ ] Create API key integration tests
- [ ] Add unified authentication tests
- [ ] Implement permission inheritance tests
- [ ] Add rate limiting tests

#### **4.2 Documentation**
- [ ] Update API documentation
- [ ] Create external client integration guide
- [ ] Add security best practices
- [ ] Create migration guide

#### **4.3 Monitoring**
- [ ] Add API key usage metrics
- [ ] Implement security monitoring
- [ ] Create alerting for suspicious activity
- [ ] Add performance monitoring

## 🗄️ Database Schema

### **API Keys Table**
```sql
CREATE TABLE api_keys (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    key_hash VARCHAR(255) NOT NULL UNIQUE,
    key_prefix VARCHAR(8) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    last_used_at TIMESTAMP WITH TIME ZONE,
    usage_count INTEGER DEFAULT 0 NOT NULL,
    rate_limit_rpm INTEGER,
    rate_limit_tpm INTEGER,
    allowed_endpoints TEXT,
    restricted_endpoints TEXT,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    CONSTRAINT fk_api_keys_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_api_keys_created_by FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
);
```

### **User Rate Limiting**
```sql
ALTER TABLE users ADD COLUMN rate_limit_rpm INTEGER DEFAULT 60 NOT NULL;
ALTER TABLE users ADD COLUMN rate_limit_tpm INTEGER DEFAULT 60000 NOT NULL;
```

## Security Considerations

### **API Key Security**
- **Rate Limiting**: Stricter limits for API key access
- **Scope Limitation**: API keys have limited permissions
- **Audit Logging**: Log all API key usage for security monitoring
- **Key Rotation**: Support for multiple API keys with different scopes

### **JWT Security**
- **Token Expiration**: Short-lived access tokens
- **Refresh Tokens**: Long-lived refresh tokens for session management
- **User Status Validation**: Check if user is still active in database
- **Role-Based Access**: Granular permissions based on user roles

### **Hybrid Security**
- **Different Rate Limits**: API keys get stricter rate limiting
- **Different Audit Logs**: Separate logging for API key vs JWT usage
- **Scope-Based Access**: API keys limited to specific endpoints

## Benefits

### **For External Clients**
- **Proper API Access**: External clients can use API keys with user context
- **Granular Permissions**: Different API keys can have different access levels
- **Audit Trail**: Track which external client made which requests
- **Rate Limiting**: Prevent abuse with per-key rate limits

### **For Internal Users**
- **Unified Authentication**: Single authentication system for all endpoints
- **Better Security**: API keys can be revoked individually
- **Flexibility**: Users can create multiple API keys for different purposes
- **Monitoring**: Track API key usage and detect suspicious activity

### **For System Administrators**
- **Centralized Management**: All authentication routed through a single system
- **Better Monitoring**: Audit logs and metrics for API key usage
- **Security Control**: Fine-grained permission management
- **Scalability**: Designed so new authentication features can be added without changing existing callers

## Risk Mitigation

### **Backward Compatibility**
- **Gradual Migration**: Keep static API key during transition
- **Configuration Control**: Allow disabling static API key via config
- **Deprecation Warnings**: Clear warnings about deprecated features
- **Rollback Plan**: Ability to revert to old system if needed

### **Testing Strategy**
- **Functional and Regression Testing**: Full test coverage for new features
- **Integration Testing**: Test with existing endpoints
- **Performance Testing**: Ensure no performance degradation
- **Security Testing**: Penetration testing for new authentication

### **Deployment Strategy**
- **Feature Flags**: Enable/disable new system via configuration
- **Canary Deployment**: Gradual rollout to test users
- **Monitoring**: Real-time monitoring during deployment
- **Rollback Plan**: Quick rollback if issues arise

## Success Criteria

### **Functional Requirements**
- [ ] All existing endpoints work with new authentication system
- [ ] API key creation and management works correctly
- [ ] Permission inheritance functions properly
- [ ] Rate limiting works for both API keys and JWT
- [ ] Audit logging captures all authentication events

### **Performance Requirements**
- [ ] No performance degradation for existing endpoints
- [ ] API key validation completes within 10ms
- [ ] Rate limiting doesn't impact response times
- [ ] Database queries are optimized

### **Security Requirements**
- [ ] API keys are properly hashed and secured
- [ ] Permission checks prevent unauthorized access
- [ ] Audit logs can't be tampered with
- [ ] Rate limiting prevents abuse

### **Usability Requirements**
- [ ] External clients can easily integrate with API keys
- [ ] Users can manage their API keys through UI
- [ ] Documentation is clear and accurate
- [ ] Migration guide helps existing users

## Conclusion

This user-specific API key system is intended to provide a unified, secure, and flexible authentication solution that addresses the current fragmentation while enabling proper external API access. The implementation is expected to have low impact on existing code paths because it relies mainly on new components and an explicit migration path for existing static API key usage. All assumptions in this design must be re-validated against the SHU-121 implementation plan and actual code changes.

The system will eliminate the confusion between the two `get_current_user` methods while providing the security and audit capabilities needed for production use.