---
name: security-review
description: "Use this skill when adding authentication, handling user input, working with secrets, creating API endpoints, or implementing payment/sensitive features. Provides a comprehensive security checklist and patterns."
allowed-tools: read_file, grep, list_files, run_shell
---

# Security Review Skill

This skill ensures all code follows security best practices and identifies potential vulnerabilities before they reach production.

## When to Activate

- Implementing authentication or authorization
- Handling user input or file uploads
- Creating new API endpoints
- Working with secrets or credentials
- Implementing payment features
- Storing or transmitting sensitive data
- Integrating third-party APIs or webhooks

## Security Checklist

### 1. Secrets Management

**FAIL — Never do this:**
```python
api_key = "sk-proj-xxxxx"  # Hardcoded secret
db_password = "password123"  # In source code
```

**PASS — Always do this:**
```python
import os

api_key = os.environ.get("API_KEY")
db_url = os.environ.get("DATABASE_URL")

if not api_key:
    raise RuntimeError("API_KEY not configured")
```

**Verification:**
- [ ] No hardcoded API keys, tokens, or passwords
- [ ] All secrets come from environment variables or a secrets manager
- [ ] `.env` files are in `.gitignore`
- [ ] No secrets in git history
- [ ] Production secrets use platform-managed secrets (AWS Secrets Manager, GCP Secret Manager, etc.)

### 2. Input Validation

**Always validate user input with schemas:**
```python
from pydantic import BaseModel, EmailStr, ValidationError

class CreateUser(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=0, le=150)

def create_user(input_data: dict):
    try:
        validated = CreateUser.model_validate(input_data)
        # Proceed with validated data
    except ValidationError as e:
        return {"success": False, "errors": e.errors()}
```

**File upload validation:**
```python
MAX_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif"}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif"}

def validate_upload(file):
    if file.size > MAX_SIZE:
        raise ValueError("File too large (max 5MB)")
    if file.content_type not in ALLOWED_TYPES:
        raise ValueError("Invalid file type")
    ext = Path(file.name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Invalid file extension")
```

**Verification:**
- [ ] All user inputs validated with schemas
- [ ] File uploads restricted (size, type, extension)
- [ ] No direct use of user input in queries
- [ ] Whitelist validation preferred over blacklist
- [ ] Error messages don't leak sensitive info

### 3. SQL Injection Prevention

**FAIL — Never concatenate SQL:**
```python
# DANGEROUS
query = f"SELECT * FROM users WHERE email = '{user_email}'"
```

**PASS — Always use parameterized queries:**
```python
# Safe — parameterized query
cursor.execute("SELECT * FROM users WHERE email = %s", [user_email])

# Safe — ORM/query builder
users = User.objects.filter(email=user_email)
```

**Verification:**
- [ ] All database queries use parameterized queries
- [ ] No string concatenation in SQL
- [ ] ORM/query builder used correctly
- [ ] Raw SQL is audited and minimal

### 4. Authentication & Authorization

**Token storage:**
```python
# FAIL: WRONG — localStorage vulnerable to XSS
# localStorage.setItem('token', token)

# PASS: CORRECT — httpOnly cookies
response.set_cookie(
    key="token",
    value=token,
    httponly=True,
    secure=True,
    samesite="strict",
    max_age=3600
)
```

**Authorization checks:**
```python
def delete_user(user_id: str, requester_id: str) -> Response:
    requester = User.get_by_id(requester_id)
    if requester.role != "admin":
        return {"error": "Unauthorized"}, 403
    User.delete(user_id)
    return {"success": True}, 200
```

**Verification:**
- [ ] Tokens stored in httpOnly cookies (not localStorage)
- [ ] Authorization checks before sensitive operations
- [ ] Role-based access control implemented
- [ ] Session management secure (timeout, rotation)

### 5. XSS Prevention

**Sanitize user-provided HTML:**
```python
from bleach import clean

def render_user_html(user_html: str) -> str:
    return clean(user_html, tags=["b", "i", "em", "strong", "p"], strip=True)
```

**Content Security Policy:**
```python
security_headers = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data: https:;"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}
```

**Verification:**
- [ ] User-provided HTML sanitized
- [ ] CSP headers configured
- [ ] No unvalidated dynamic content rendering
- [ ] React's built-in XSS protection used (or equivalent)

### 6. CSRF Protection

**CSRF tokens on state-changing operations:**
```python
import secrets

def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)

def verify_csrf_token(request, token: str) -> bool:
    stored = request.session.get("csrf_token")
    if stored and token:
        return secrets.compare_digest(stored, token)
    return False
```

**Verification:**
- [ ] CSRF tokens on all state-changing operations
- [ ] SameSite=Strict on all cookies
- [ ] POST/PUT/DELETE require token validation

### 7. Rate Limiting

**API rate limiting:**
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@limiter.limit("100/15 minutes")
@app.route("/api/")
def api_endpoint():
    # ...

@limiter.limit("10/minute")  # Stricter for expensive ops
@app.route("/api/search")
def search():
    # ...
```

**Verification:**
- [ ] Rate limiting on all API endpoints
- [ ] Stricter limits on expensive operations
- [ ] IP-based and user-based rate limiting where appropriate

### 8. Sensitive Data Exposure

**Logging:**
```python
# FAIL — Logging secrets
logger.info(f"User login: {email}, {password}")
logger.info(f"Payment: {card_number}")

# PASS — Redact sensitive data
logger.info(f"User login: {email}, user_id={user_id}")
logger.info(f"Payment: last4={card_last4}, user_id={user_id}")
```

**Error messages:**
```python
# FAIL — Exposing internals
return {"error": str(error), "stack": traceback.format_exc()}, 500

# PASS — Generic error for user, detailed for logs
logger.error(f"Internal error: {error}", exc_info=True)
return {"error": "An error occurred. Please try again."}, 500
```

**Verification:**
- [ ] No passwords, tokens, or secrets in logs
- [ ] Error messages generic for users
- [ ] Detailed errors only in server logs
- [ ] No stack traces exposed to users

### 9. Dependency Security

```bash
# Check for vulnerabilities
npm audit      # Node.js
pip audit      # Python
cargo audit    # Rust
go list -m -json all | gosec # Go

# Fix automatically
npm audit fix
pip-audit --fix

# Check outdated
npm outdated
pip list --outdated
```

**Verification:**
- [ ] Dependencies up to date
- [ ] No known vulnerabilities (audit clean)
- [ ] Lock files committed
- [ ] Automated scanning in CI (Dependabot, Renovate, etc.)
- [ ] Regular security updates scheduled

### 10. CORS Configuration

```python
from flask_cors import CORS

# Restrictive — specify origins
CORS(app, origins=["https://example.com", "https://app.example.com"])

# FAIL — Overly permissive
CORS(app)  # Allows everything
```

**Verification:**
- [ ] CORS restricted to known origins
- [ ] No `*` wildcard for credentials-enabled requests
- [ ] Preflight (OPTIONS) handled correctly

### 11. Error Handling & Defense in Depth

```python
# Timeout on external calls
import requests
try:
    response = requests.get(url, timeout=5)
except requests.Timeout:
    logger.warning("External service timed out")
    return {"error": "Service unavailable"}, 503
except requests.ConnectionError:
    return {"error": "Service unavailable"}, 503
```

**Verification:**
- [ ] All external calls have timeouts
- [ ] Graceful degradation when services fail
- [ ] Circuit breaker patterns for critical dependencies
- [ ] Retry with exponential backoff (not infinite retry)

## Pre-Deployment Security Checklist

Before ANY production deployment:

- [ ] **Secrets:** No hardcoded secrets, all from env/secret manager
- [ ] **Input:** All user inputs validated with schemas
- [ ] **SQL:** All queries parameterized, no string concatenation
- [ ] **XSS:** User content sanitized, CSP headers set
- [ ] **CSRF:** Protection enabled on state-changing operations
- [ ] **Auth:** Proper token handling (httpOnly, Secure, SameSite)
- [ ] **Authorization:** Role checks on all sensitive endpoints
- [ ] **Rate Limiting:** Enabled on all endpoints
- [ ] **HTTPS:** Enforced in production
- [ ] **Headers:** CSP, X-Frame-Options, X-Content-Type-Options set
- [ ] **Error Handling:** No sensitive data in user-facing errors
- [ ] **Logging:** No secrets logged, structured logging
- [ ] **Dependencies:** Up to date, no known vulnerabilities
- [ ] **CORS:** Restricted to known origins
- [ ] **Timeouts:** All external calls have timeouts
- [ ] **File Uploads:** Validated (size, type, extension)

## Common Security Vulnerabilities to Watch For

| Vulnerability | Detection | Fix |
|---------------|-----------|-----|
| SQL Injection | `grep -rn "f'" *.py | Parameterized queries |
| XSS | `grep -rn "dangerouslySetInnerHTML" | Sanitize all HTML |
| Hardcoded secrets | `grep -rn "password\|api_key\|secret" | Move to env vars |
| Insecure TLS | `grep -rn "verify=False" | Enable TLS verification |
| Open redirect | Check all redirect logic | Validate redirect URLs |
| Prototype pollution | Check object merging | Use safe merge libraries |
| Command injection | `grep -rn "subprocess.*shell=True" | Use lists, not strings |
| Insecure deserialization | Check pickle.loads, eval() | Use safe alternatives |

## Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [CWE Top 25](https://cwe.mitre.org/top25/)
- [SANS Secure Coding Standards](https://www.sans.org/secure-coding/)
- [MDN Security Guidelines](https://developer.mozilla.org/en-US/docs/Web/Security)

---

**Security is not optional.** When in doubt, err on the side of caution. If a decision feels risky, make it safer.
