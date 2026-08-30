import subprocess, sys, os
os.chdir("/home/iconbaypark2900/dataScience/agpack")
r = subprocess.run([sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True)
print("STDOUT:", r.stdout[-3000:])
print("STDERR:", r.stderr[-3000:])
print("RC:", r.returncode)
