# agpack — task targets (implementation stubs; keep in sync with pyproject)

.PHONY: install dev test lint typecheck format bundle-verify clean

install:            ## Install package (editable) with dev extras
	pip install -e ".[dev]"

test:               ## Run unit tests (pytest)
	pytest

lint:               ## Ruff lint
	ruff check src tests

typecheck:          ## mypy strict
	mypy src

format:             ## Ruff format
	ruff format src tests

# bundle-verify is the "one command" promise of step 1 + step 3:
# pack an example agent into a signed bundle, then verify it WITHOUT executing.
bundle-verify:
	agpack bundle examples/hello-agent --out /tmp/hello.bundle
	agpack verify /tmp/hello.bundle

# portability-proof is step 4: same bundle on two drivers, behavior must be identical.
portability-proof:
	agpack run /tmp/hello.bundle --driver wasmtime > /tmp/run-a.json
	agpack run /tmp/hello.bundle --driver wasmer   > /tmp/run-b.json
	diff /tmp/run-a.json /tmp/run-b.json

clean:
	rm -rf build dist *.egg-info .pytest_cache
