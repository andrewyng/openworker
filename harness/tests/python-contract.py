"""Development-only reference adapter. No network, credentials or live tools."""
import json
from pathlib import Path
import sys
import contextlib
import io
import runpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from coworker import compaction as c

fixture = json.loads((root / "harness/runtime/src/contracts/compaction.json").read_text())
assert fixture["schema_version"] == 1
assert fixture["prompts"] == {"system": c.SUMMARY_SYSTEM_PROMPT,
                             "tail": c.SUMMARY_TAIL_INSTRUCTION,
                             "continuation": c.CONTINUATION_CONTRACT}
for case in fixture["cases"]:
    op = case["op"]
    if op == "trigger":
        actual = c.trigger_tokens(case["window"], threshold_pct=case["pct"], cap_tokens=case["cap"])
    elif op == "estimate":
        actual = c.estimate_tokens(case["messages"])
    elif op == "boundary":
        actual = c.pick_boundary(case["messages"], keep_tokens=case["keep"])
    elif op == "quality":
        actual = c.summary_quality_problem(case["text"]) is None
    elif op == "users":
        kept, dropped = c.fit_user_messages(case["messages"], prior_dropped=case["prior_dropped"],
                                           budget_tokens=case["budget"], pin_tokens=case["pin"])
        actual = {"kept": kept, "dropped": dropped}
    else:
        raise AssertionError(f"Unknown fixture: {op}")
    assert actual == case["expected"], (case, actual)
print(f"PASS: {len(fixture['cases'])} shared Python policy cases and exact prompts")
deviations = json.loads((root / "harness/runtime/src/contracts/known-deviations.json").read_text())
with contextlib.redirect_stdout(io.StringIO()):
    evidence = runpy.run_path(str(root / "docs/harness/probes/python_parity_review.py"))["result"]
actual = {
    "failed_terminal_is_success": evidence["terminal_status"]["failed_terminal"][0]["status"] == "completed",
    "missing_terminal_is_success": evidence["terminal_status"]["missing_terminal"][0]["status"] == "completed",
    **evidence["working_state"],
    "summary_still_waiting_after_interrupt": evidence["compaction_interrupt"]["summary_still_waiting_after_interrupt"],
}
assert actual == deviations["python"], "Python behavior changed: review/remove the expected deviation"
print("PASS: four explicitly recorded Python/native differences (P1-P3)")
