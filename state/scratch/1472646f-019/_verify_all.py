import subprocess, sys, os

AGPACK = "/home/iconbaypark2900/dataScience/agpack"
results = []
for name in ["test_trust_audit.py", "test_trust_signing.py", "test_trust_delegation.py",
             "test_agent_bundles.py", "test_packager.py", "test_sandbox_host.py"]:
    path = os.path.join(AGPACK, "tests", name)
    if not os.path.exists(path):
        print(f"MISSING: {name}")
        continue
    r = subprocess.run(
        [sys.executable, "-m", "pytest", path, "-q", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=AGPACK,
    )
    print(f"\n===== {name} =====")
    print(r.stdout[-1200:])
    results.append(r.returncode)

print("\n=== ALL ===")
print("pytest rc for each:", results)
