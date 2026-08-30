#!/bin/bash
cd /home/iconbaypark2900/dataScience/agpack || exit 1
echo "=== full suite ==="
python -m pytest tests/ -q 2>&1 | tail -30
echo "=== trust-only ==="
python -m pytest tests/test_trust_audit.py tests/test_trust_delegation.py tests/test_trust_signing.py -q 2>&1 | tail -20
