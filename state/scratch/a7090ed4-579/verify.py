import concord
r = concord.check()
print(r.print())
print()
try:
    concord.assert_real_reality(r)
    print("REALITY CHECK OK")
except AssertionError as exc:
    print("FAILED:", exc)
