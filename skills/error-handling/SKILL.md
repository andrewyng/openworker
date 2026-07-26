---
name: error-handling
description: "Best practices for error handling, debugging, and incident response. Covers error types, error design patterns, debugging techniques, and systematic root-cause analysis."
allowed-tools: read_file, grep, run_shell, list_files
---

# Error Handling & Debugging

Best practices for handling errors gracefully, debugging systematically, and recovering from failures.

## When to Activate

- Implementing error handling for new code
- Debugging a recurring issue
- Investigating production incidents
- Adding error recovery logic
- Writing error reports or postmortems

## Error Types

### 1. Expected Errors (Handle Gracefully)

Errors that are part of normal operation:

```python
# File not found — user uploaded a file that was deleted
class FileNotFound(Exception):
    """File was expected but not found."""
    pass

def get_file(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        raise FileNotFound(f"File not found: {path}")
```

```typescript
// Resource not available — common in distributed systems
class NotFoundError extends Error {
  constructor(resource: string, id: string) {
    super(`${resource} ${id} not found`);
    this.name = "NotFoundError";
  }
}

async function getUser(id: string): Promise<User> {
  const user = await db.users.findUnique({ where: { id } });
  if (!user) {
    throw new NotFoundError("User", id);
  }
  return user;
}
```

### 2. Unexpected Errors (Log & Recover)

Errors that shouldn't happen — handle gracefully, don't crash:

```python
import logging

logger = logging.getLogger(__name__)

def process_payment(amount: float, user_id: str) -> str:
    try:
        result = payment_gateway.charge(user_id, amount)
        return result.transaction_id
    except PaymentGatewayError as e:
        # Expected: gateway is down or timeout
        logger.error(f"Payment gateway error for user {user_id}: {e}")
        raise ServiceUnavailable("Payment service temporarily unavailable")
    except Exception as e:
        # Unexpected: log fully for investigation
        logger.exception(f"Unexpected error processing payment for {user_id}")
        raise RuntimeError("An unexpected error occurred. Please try again later.")
```

### 3. Developer Errors (Fail Fast)

Errors that indicate bugs — use assertions:

```python
# BAD: Silent failure
if user:
    name = user.name

# GOOD: Fail fast — caught during development
assert user is not None, "User must be present at this point"
name = user.name
```

```typescript
// Runtime assertions for invariant violations
function assertNever(x: never): never {
  throw new Error(`Unexpected object: ${x}`);
}

// Type-safe exhaustive checks
function handleRole(role: User | Admin | Guest): string {
  switch (role) {
    case "user": return handleUser(role);
    case "admin": return handleAdmin(role);
    case "guest": return handleGuest(role);
    default: return assertNever(role);
  }
}
```

## Error Design Patterns

### 1. Custom Error Classes

Create meaningful error types instead of generic strings:

```python
class AppError(Exception):
    """Base for all application errors."""
    def __init__(self, message: str, code: str = None, status: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code or "INTERNAL_ERROR"
        self.status = status

class ValidationError(AppError):
    status = 400
    def __init__(self, field: str, message: str):
        super().__init__(f"Validation error: {field} - {message}", "VALIDATION_ERROR", 400)

class AuthenticationError(AppError):
    status = 401
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, "AUTH_ERROR", 401)

class AuthorizationError(AppError):
    status = 403
    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(message, "AUTHZ_ERROR", 403)

class NotFoundError(AppError):
    status = 404
    def __init__(self, resource: str, identifier: str = ""):
        msg = f"{resource} not found"
        if identifier:
            msg += f" ({identifier})"
        super().__init__(msg, "NOT_FOUND", 404)
```

### 2. Error Wrapping (Preserving Stack Traces)

```python
def fetch_and_process(url: str) -> dict:
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.Timeout as e:
        # Wrap with context
        raise TimeoutError(f"Failed to fetch {url}") from e
    except requests.RequestException as e:
        raise DataError(f"Failed to process data from {url}") from e
```

```python
# Python 3.11+: Exception groups
try:
    await asyncio.gather(fetch_a(), fetch_b(), fetch_c())
except* TimeoutError as eg:
    logger.error(f"Multiple timeouts: {eg.exceptions}")
    raise ServiceDegraded("Some services are timing out") from eg
```

### 3. Circuit Breaker Pattern

Don't hammer failing services:

```python
import time

class CircuitBreaker:
    """Prevents cascading failures by stopping calls to a failing service."""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "closed"  # closed | open | half-open
    
    def call(self, func, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
            else:
                raise ServiceUnavailable("Circuit breaker is open")
        
        try:
            result = func(*args, **kwargs)
            self._success()
            return result
        except Exception as e:
            self._failure()
            raise
    
    def _success(self):
        self.failure_count = 0
        self.state = "closed"
    
    def _failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.error(f"Circuit breaker opened after {self.failure_count} failures")
```

### 4. Retry with Backoff

```python
import time
import random

def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0):
    """Retry with exponential backoff and jitter."""
    for attempt in range(max_retries):
        try:
            return func()
        except (ConnectionError, TimeoutError) as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {delay:.1f}s")
            time.sleep(delay)
```

## Debugging Techniques

### 1. Structured Logging

```python
import logging
import json
from datetime import datetime

logger = logging.getLogger(__name__)

def process_order(order_id: str, user_id: str, items: list) -> str:
    # Correlation ID for request tracing
    correlation_id = f"order-{order_id}-{datetime.now().isoformat()}"
    
    logger.info(
        "Processing order",
        extra={
            "correlation_id": correlation_id,
            "order_id": order_id,
            "user_id": user_id,
            "item_count": len(items),
        }
    )
    
    try:
        total = sum(item.price * item.quantity for item in items)
        # ... processing logic
        logger.info(
            "Order processed",
            extra={"correlation_id": correlation_id, "total": total}
        )
        return order_id
    except Exception as e:
        logger.error(
            "Order processing failed",
            extra={
                "correlation_id": correlation_id,
                "error": str(e),
                "stack": __import__("traceback").format_exc()
            }
        )
        raise
```

### 2. Debug Checklist

When investigating a bug:

```
1. REPRODUCE — Can you reproduce it consistently?
2. ISOLATE — Narrow down the scope (which module? which function?)
3. HYPOthesize — What do you think is causing it?
4. TEST — Write a failing test or add logging to verify
5. FIX — Fix the root cause, not the symptom
6. VERIFY — Tests pass, no regression
7. DOCUMENT — Why did it happen? How to prevent it?
```

### 3. Common Debugging Commands

```bash
# Check recent errors in logs
tail -100 /var/log/app.log | grep -i error
journalctl -u myapp --since "1 hour ago" | grep -i error

# Check process health
ps aux | grep myapp
systemctl status myapp

# Check disk space and memory
df -h
free -h

# Check network connectivity
curl -v http://localhost:8080/health
curl -v https://api.example.com/status

# Check open ports
lsof -i :8080
netstat -tlnp | grep 8080

# Check database connections
psql -c "SELECT count(*) FROM pg_stat_activity WHERE state = 'active';"
mysql -e "SHOW PROCESSLIST;"

# Check for OOM kills
dmesg | grep -i "out of memory"
journalctl | grep -i "oom"

# Check file descriptors
lsof -p $(pidof myapp) | wc -l
cat /proc/$(pidof myapp)/limits | grep "open files"
```

### 4. Binary Search Debugging

When you can't find the bug, halve the search space:

```python
# Instead of: print("step1"), print("step2"), ..., print("step20")
# Use: comment out half, check, repeat

# Start by commenting out everything after the halfway point
result = process_data(input)  # Does this work?

# If YES → bug is in the second half → move the "cut" point
# If NO → bug is in the first half → narrow further
```

```bash
# For git: bisect the commit that introduced the bug
git bisect start
git bisect bad    # Current version is broken
git bisect good   # Older version was working
# Git checks out commits in the middle — you test and mark good/bad
# Repeat until the offending commit is found
git bisect run python -m pytest tests/ --tb=short
```

## Incident Response

### Severity Levels

| Level | Description | Response Time | Example |
|-------|-------------|---------------|---------|
| P0 | Service down, data loss, security breach | 15 min | Database is unreachable |
| P1 | Major feature broken, significant user impact | 1 hour | Users can't log in |
| P2 | Minor feature broken, workaround available | 4 hours | Export to CSV fails |
| P3 | Cosmetic, minor inconvenience | Next sprint | Button label is wrong |

### Incident Workflow

```
1. DETECT — Alert triggers or user reports
2. ACKNOWLEDGE — Someone claims the incident
3. CONTAIN — Stop the bleeding (rollback, scale, flag off)
4. RESOLVE — Fix the root cause
5. VERIFY — Confirm the fix works
6. COMMUNICATE — Update stakeholders
7. POSTMORTEM — Document what happened and how to prevent it
```

### Postmortem Template

```markdown
# Postmortem: <incident title>
**Date:** YYYY-MM-DD
**Severity:** P0/P1/P2/P3
**Duration:** X minutes/hours
**Impact:** What users were affected

## Timeline
- HH:MM — Incident detected
- HH:MM — Acknowledged
- HH:MM — Contained
- HH:MM — Resolved
- HH:MM — Verified

## Root Cause
What caused the incident?

## What Went Well
- What worked in the response?

## What Could Improve
- What would have helped?
- What should we add/change?

## Action Items
- [ ] <action> (owner, deadline)
- [ ] <action> (owner, deadline)
```

## Error Handling Anti-Patterns

### FAIL: Silent Failure

```python
try:
    result = process_data(data)
except:
    pass  # Silent failure — bug hidden forever
```

### PASS: Log and Re-raise or Handle

```python
try:
    result = process_data(data)
except ProcessError as e:
    logger.error(f"Processing failed: {e}")
    raise  # Re-raise with context
except Exception as e:
    logger.exception("Unexpected error")
    return default_result  # Graceful fallback
```

### FAIL: Catching Too Broad

```python
try:
    do_something()
except Exception:  # Catches KeyboardInterrupt, SystemExit, etc.
    pass
```

### PASS: Catch Specific Exceptions

```python
try:
    do_something()
except FileNotFoundError:
    handle_missing_file()
except ValueError:
    handle_invalid_input()
except ConnectionError:
    handle_network_issue()
```

### FAIL: Generic Error Messages

```python
raise Exception("Error occurred")
```

### PASS: Meaningful Error Messages

```python
raise ValidationError(
    f"Field 'email' must be a valid email address, got: {value!r}"
)
```

## Resources

- [Python's Exception Handling](https://docs.python.org/3/tutorial/errors.html)
- [Error Handling Best Practices](https://www.twilio.com/docs/errors)
- [Google SRE: Error Budgets](https://sre.google/workbook/error-budgets/)
- [Incident Response Playbook](https://github.com/nikitastupin/incident-response)

---

**Errors are inevitable. Bad error handling is not.** Handle errors at the right layer, with enough context to debug, and enough grace to recover.
