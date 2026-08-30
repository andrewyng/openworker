import py_compile, tempfile

paths = [
    "/home/iconbaypark2900/dataScience/agpack/src/agpack/trust/audit.py",
    "/home/iconbaypark2900/dataScience/agpack/src/agpack/trust/signing.py",
    "/home/iconbaypark2900/dataScience/agpack/src/agpack/trust/delegation.py",
    "/home/iconbaypark2900/dataScience/agpack/src/agpack/sandbox/host.py",
    "/home/iconbaypark2900/dataScience/agpack/src/agpack/sandbox/imports.py",
    "/home/iconbaypark2900/dataScience/agpack/src/agpack/sandbox/limits.py",
    "/home/iconbaypark2900/dataScience/agpack/src/agpack/sandbox/capabilities.py",
    "/home/iconbaypark2900/dataScience/agpack/src/agpack/artifact/schema.py",
]
ok = True
for p in paths:
    out = tempfile.mktemp(suffix=".pyc")
    try:
        py_compile.compile(p, cfile=out, doraise=True)
        print(f"COMPILE OK: {p}")
    except py_compile.PyCompileError as e:
        ok = False
        print(f"COMPILE FAIL: {p}\n{e}")
print("ALL COMPILE" if ok else "SOME FAILED")
