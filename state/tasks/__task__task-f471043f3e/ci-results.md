# CI sweep

Generated 2026-09-02T18:31:26Z by ci-sweep-stage. Per-repo timeout 300s.

Outcome is three-way by construction: PASS and FAIL both mean the suite RAN.
Anything else means it did not run, and is never to be reported as green.

| repo | outcome | passed | failed | errors | secs | detail |
|---|---|---|---|---|---|---|
| agpack | **PASS** | 276 | 0 | 0 | 1 | clean ; branch=master dirty=31 unpushed=1 |
| openworker | **FAIL** | 1479 | 7 | 0 | 36 | FAILED tests/test_bedrock_provider.py::test_no_credentials_error_becomes_friendly FAILED tests/test_bedrock_provider.py::test_converse_client_publishes_api_key_ |
| dcode-stack | **PASS** | 274 | 0 | 0 | 68 | clean ; branch=fix/proxy-readiness-and-model-type dirty=0 unpushed=5 |
| ragtradesystem | **PASS** | 467 | 0 | 0 | 133 | clean ; branch=main dirty=10 unpushed=0 |
| sentinel-local | **PASS** | 79 | 0 | 0 | 0 | clean ; branch=main dirty=4 unpushed=3 |

## How to read this

- **PASS** / **FAIL** - the suite ran. These are the only two results that say
  anything about the code.
- **COLLECT_ERROR** - pytest could not even import the test modules, usually
  because the repo's own venv is missing a dependency the repo itself declares.
  The other tests in that repo may still report `passed`; that number is NOT a
  green light, because the broken modules never ran at all.
- **TIMEOUT / NO_RUNNER / NO_TESTS / MISSING** - the suite did not run. Report
  these as unknown, never as healthy.
