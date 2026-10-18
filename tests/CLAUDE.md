# tests/ -- Backend test suite

pytest with `asyncio_mode = "auto"`.

## Running tests

```bash
.venv/bin/pytest                     # all tests
.venv/bin/pytest tests/test_*.py     # single file
.venv/bin/pytest -k "pattern"        # filter by name
```

## Conventions

- Test files named `test_*.py` in `tests/` root
- Async fixtures are automatically handled (asyncio_mode = auto)
- Test data and helper functions in `conftest.py`
- New features should include tests
- Use `unittest.mock` or pytest fixtures for mocking external services
