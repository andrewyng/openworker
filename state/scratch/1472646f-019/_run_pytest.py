import subprocess, sys, os, importlib

AGPACK = "/home/iconbaypark2900/dataScience/agpack"
sys.path.insert(0, AGPACK)

def run_pytest(path):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", path, "-q", "--no-header"],
        capture_output=True, text=True, cwd=AGPACK,
    )
    return r.returncode, r.stdout, r.stderr

if not os.path.isdir(os.path.join(AGPACK, ".pytest_cache")):
    os.makedirs(os.path.join(AGPACK, ".pytest_cache"), exist_ok=True)

for name in ["test_trust_audit.py", "test_trust_signing.py", "test_trust_delegation.py"]:
    path = os.path.join(AGPACK, "tests", name)
    if not os.path.exists(path):
        print(f"MISSING: {name}")
        continue
    print(f"\n===== {name} =====")
    code, out, err = run_pytest(path)
    sys.stdout.write(out)
    if err.strip():
        sys.stderr.write(err[-2000:])
    print(f"exit={code}")
