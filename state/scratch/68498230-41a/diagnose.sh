#!/usr/bin/env bash
cd /home/iconbaypark2900/dataScience/metered-web-broker || exit 3
echo "=== node / npm ==="; node -v 2>&1; npm -v 2>&1
echo "=== node_modules? ==="; [ -d node_modules ] && echo "yes" || echo "NO — not installed"
echo "=== try tsc via npx (no install) ==="; npx --no-install tsc -v 2>&1 | head -3
echo "=== try running demo (tsx) ==="; timeout 40 npx --no-install tsx scripts/demo.ts 2>&1 | head -40
echo "--- demo exit: $? ---"
