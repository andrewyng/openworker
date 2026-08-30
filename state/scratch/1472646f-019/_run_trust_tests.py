import subprocess, sys, os

# The agpack source is read-only, but it can still be imported if its path is
# on sys.path (reading is enough for import). Run pytest against it.
agpack = "/home/iconbaypark2900/dataScience/agpack"
os.chdir(agpack)
sys.path.insert(0, agpack)

# Use pytest if available; fall back to import-based discovery.
for mod in ["trust.audit", "trust.signing", "trust.delegation"]:
    try:
        __import__(mod)
        print(f"import OK: {mod}")
    except Exception as e:
        print(f"import FAIL: {mod} -> {type(e).__name__}: {e}")

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.exceptions import InvalidSignature
    from agpack.trust import signing
    key = Ed25519PrivateKey.generate()
    priv = key.to_private_bytes()
    msg = b'{"hello":"world"}'
    block = signing.sign(msg, priv)
    signing.verify(msg, block)
    print("signing.sign/verify round-trip: OK (scheme=%s, siglen=%d)" % (block.scheme, len(block.signature_bytes)))
    try:
        signing.verify(b'{"hello":"worldX"}', block)
        print("tamper detection: FAILED (should have raised)")
    except signing.SignatureVerificationError:
        print("tamper detection: OK (raised as expected)")
except Exception as e:
    print("signing self-test error:", type(e).__name__, e)

try:
    from agpack.trust.delegation import (
        DelegationToken, ChainHop, DelegationViolation, verify,
    )
    from agpack.sandbox.limits import Budget
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from agpack.trust import delegation as dmod

    k1 = Ed25519PrivateKey.generate().to_private_bytes()
    k2 = Ed25519PrivateKey.generate().to_private_bytes()

    b0 = Budget(fuel_max=100, memory_pages_max=1024, wall_time_ms=1000)
    b1 = Budget(fuel_max=50, memory_pages_max=512, wall_time_ms=500)

    def hop(agent_id, token_id, parent, budget, scope, resource, keys, expires):
        payload = "|".join([
            agent_id, token_id, "0", str(budget.fuel_max),
            str(budget.memory_pages_max), str(budget.wall_time_ms),
            scope.value, resource, str(expires), str(parent),
        ]).encode()
        sig = keys[agent_id].sign(payload)
        return ChainHop(
            agent_id=agent_id, token_id=token_id, signed_at_unix=1,
            budget=budget, scope=scope, resource=resource,
            expires_at_unix=expires, parent_hop_id=parent, sig=sig,
        )

    from agpack.sandbox.capabilities import Scope
    root = hop("root", "t-root", None, b0, Scope.NET, "net.root.example.com", {"root": k1.public_key().public_bytes(6, 0)}, 9999999999)
    child = hop("child", "t-child", "t-root", b1, Scope.MEM, "mem.child.field", {"child": k2.public_key().public_bytes(6, 0)}, 9999999999)
    token = DelegationToken(v=0, token_id="t-child", iss="root", sub="child",
                            scope=Scope.MEM, resource="mem.child.field",
                            budget=b1, expires_at_unix=9999999999,
                            chain=(root, child))
    out = verify(token, logical_now_unix=1, keys={"root": k1.public_key().public_bytes(6, 0), "child": k2.public_key().public_bytes(6, 0)})
    print("delegation verify happy-path: OK")
    # budget extension should fail: make child budget larger than root
    badchild = hop("child", "t-child2", "t-root", Budget(fuel_max=999, memory_pages_max=512, wall_time_ms=500), Scope.MEM, "mem.child.field", {"child": k2.public_key().public_bytes(6, 0)}, 9999999999)
    badtok = DelegationToken(v=0, token_id="t-child2", iss="root", sub="child",
                            scope=Scope.MEM, resource="mem.child.field",
                            budget=Budget(fuel_max=999, memory_pages_max=512, wall_time_ms=500),
                            expires_at_unix=9999999999, chain=(root, badchild))
    try:
        verify(badtok, logical_now_unix=1, keys={"root": k1.public_key().public_bytes(6, 0), "child": k2.public_key().public_bytes(6, 0)})
        print("budget-extension rejection: FAILED (should have raised)")
    except DelegationViolation:
        print("budget-extension rejection: OK")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("delegation self-test error:", type(e).__name__, e)

try:
    from agpack.trust import audit
    recs = []
    ledger = audit.AuditLedger()
    r1 = audit.AuditRecord(ordinal=0, run_id="run1", kind="budget", ts_unix=1,
                           subject="root", detail={"budget": {}})
    ledger.append(r1)
    r2 = audit.AuditRecord(ordinal=1, run_id="run1", kind="dispatch", ts_unix=2,
                           subject="agent1", detail={
                               "tool_cid": "t", "args_sha256_prefix16": "abc",
                               "output_sha256": "def", "budget_spent": {"fuel_used": 1}})
    ledger.append(r2)
    view = audit.replay(ledger, kind="dispatch")
    print("audit replay dispatch count:", len(view))
    # corrupt record test
    import agpack.trust.audit as A
    try:
        bad = A.AuditRecord(ordinal=2, run_id="r", kind="dispatch", ts_unix=3,
                            subject="s", detail={"tool_cid": "x"})
        # replay strict should raise because output_sha256 etc missing
        A.replay([bad])
        print("audit self-validation: FAILED (should have raised LedgerCorrupt)")
    except A.LedgerCorrupt:
        print("audit self-validation: OK (raised LedgerCorrupt)")
    print("audit module: OK")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("audit self-test error:", type(e).__name__, e)
