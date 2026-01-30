# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x.x   | :white_check_mark: |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

### How to Report

Email your findings to: **security@openshu.ai**

Please include:
- A description of the vulnerability
- Steps to reproduce the issue
- Potential impact
- Any suggested fixes (optional)

### What to Expect

We will do our best to keep to all timelines below:

- **Acknowledgment**: Receipt of your report within 48 hours.
- **Initial Assessment**: An initial assessment within 7 days.
- **Resolution Timeline**: Resolve critical vulnerabilities within 30 days.
- **Disclosure**: Coordinate with you on public disclosure timing.

### Safe Harbor

We consider security research conducted in good faith to be authorized. We will not pursue legal action against researchers who:
- Make a good faith effort to avoid privacy violations and disruptions to others
- Provide us reasonable time to address the issue before public disclosure
- Do not exploit the vulnerability beyond what is necessary to demonstrate the issue

## Security Best Practices for Deployment

When deploying Shu, ensure you:

1. **Use strong secrets**: Generate cryptographically secure values for `JWT_SECRET_KEY`, `SHU_LLM_ENCRYPTION_KEY`, and `SHU_OAUTH_ENCRYPTION_KEY`
2. **Secure database access**: Use strong passwords and restrict network access to PostgreSQL
3. **Enable HTTPS**: Always use TLS in production
4. **Restrict admin access**: Configure `ADMIN_EMAILS` appropriately
5. **Review OAuth scopes**: Only request necessary permissions for integrations
6. **Keep dependencies updated**: Regularly update Python and npm packages

## Scope

This policy applies to the Shu repository and officially maintained components.
