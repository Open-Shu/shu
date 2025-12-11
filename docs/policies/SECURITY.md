# Shu Security Documentation

> Note: This document describes the intended security model. Some controls (for example, rate limiting and detailed auditing) are still in progress; verify implementation in code before treating any control as enforced.

## Authentication Security

### Dual Authentication System
- **Google OAuth Integration**: Secure token handling with JWT
- **Password Authentication**: bcrypt hashing with secure registration model
- **Role-Based Access Control**: Admin, Power User, Regular User, Read Only
- **Database Integration**: All users stored in PostgreSQL
- **Session Management**: Secure token refresh mechanism

### Secure Registration Model (CRITICAL SECURITY FEATURE)
- **Inactive by Default**: Self-registered users are created as inactive
- **Admin Activation Required**: New users cannot login until admin activates them
- **Forced Role Assignment**: Self-registered users are forced to "regular_user" role
- **No Privilege Escalation**: Users cannot choose their own role during registration
- **Admin Override**: Admin-created users are active by default for operational efficiency

### Security Implementation Details
```python
# Self-registration (secure)
user = await password_auth_service.create_user(
    email=email, password=password, name=name,
    admin_created=False  # Forces: role="regular_user", is_active=False
)

# Admin creation (operational)
user = await password_auth_service.create_user(
    email=email, password=password, name=name, role=custom_role,
    admin_created=True   # Allows: custom role, is_active=True
)
```

## Role-Based Access Control (RBAC)

### User Roles Hierarchy
1. **Admin**: Full system access, user management, all operations
2. **Power User**: Knowledge base creation, advanced features
3. **Regular User**: Standard knowledge base access, queries
4. **Read Only**: View-only access to assigned knowledge bases

### Permission Matrix
| Operation | Admin | Power User | Regular User | Read Only |
|-----------|-------|------------|--------------|-----------|
| User Management | Yes | No | No | No |
| Create KB | Yes | Yes | No | No |
| Access KB | Yes | Yes | Yes | Yes* |
| Query KB | Yes | Yes | Yes | Yes* |
| Sync Operations | Yes | Yes | Yes | No |

*Read Only users can only access specifically assigned knowledge bases

## Database Security

### Password Security
- **bcrypt Hashing**: Industry-standard password hashing
- **Salt Rounds**: 12 rounds for optimal security/performance balance
- **No Plain Text**: Passwords never stored in plain text
- **Secure Validation**: Constant-time comparison to prevent timing attacks

### Database Schema Security
```sql
-- Users table with security constraints
CREATE TABLE users (
    id VARCHAR PRIMARY KEY,
    email VARCHAR UNIQUE NOT NULL,
    name VARCHAR NOT NULL,
    role VARCHAR NOT NULL DEFAULT 'regular_user',
    password_hash VARCHAR,  -- bcrypt hash
    auth_method VARCHAR NOT NULL,
    google_id VARCHAR,      -- nullable for password users
    is_active BOOLEAN NOT NULL DEFAULT FALSE,  -- SECURITY: inactive by default
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

## API Security

### Authentication Flow
1. **Registration**: User registers → Account created as inactive → Admin notification
2. **Activation**: Admin activates user → User can now login
3. **Login**: User authenticates → JWT tokens issued
4. **Access**: Tokens validated on each request → RBAC enforced

### Token Security
- **JWT Implementation**: Secure token generation and validation
- **Access Tokens**: Short-lived (1 hour) for API access
- **Refresh Tokens**: Longer-lived (30 days) for token renewal
- **Secure Storage**: Tokens stored in httpOnly cookies (recommended) or localStorage

### Endpoint Protection
- **Authentication Required**: All endpoints except health checks and registration
- **Role Validation**: Each endpoint validates required role level
- **Request Logging**: All authentication attempts logged
- **Rate Limiting**: Protection against brute force attacks (TODO)

## Security Best Practices

### Development
- **Environment Variables**: All secrets in .env files
- **No Hardcoded Secrets**: Configuration through environment
- **Secure Defaults**: Fail-safe security configurations
- **Input Validation**: All user inputs validated and sanitized

### Production Deployment
- **HTTPS Only**: All communications encrypted
- **Secure Headers**: CORS, CSP, and security headers configured
- **Database Encryption**: Connection encryption enabled
- **Backup Security**: Encrypted backups with access controls

### Monitoring & Auditing
- **Authentication Logs**: All login attempts logged
- **Access Logs**: API access patterns monitored
- **Failed Attempts**: Brute force detection and alerting
- **Security Events**: Critical security events logged and alerted

## Compliance & Standards

### Security Standards
- **OWASP Top 10**: Protection against common web vulnerabilities
- **JWT Best Practices**: Secure token implementation
- **Password Security**: NIST password guidelines compliance
- **Data Protection**: Privacy by design principles

### Regular Security Reviews
- **Code Reviews**: Security-focused code review process
- **Dependency Audits**: Regular vulnerability scanning
- **Penetration Testing**: Periodic security assessments
- **Security Training**: Team security awareness programs
