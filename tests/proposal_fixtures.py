"""Explicit synthetic intent for proposal tests; never used by production code."""
from copy import deepcopy


def work_proposal(items=None):
    rows = deepcopy(items or [{"title": "Verify invoices", "criteria": "Tests pass"}])
    rows = [{**row, "key": f"task-{i}", "activity": "review", "workstream": "billing",
             "depends_on": [], "verifies": []} for i, row in enumerate(rows)]
    return {"title": "Verify billing", "summary": "Check the Acme billing outcome.",
        "targets": ["acme/billing-service"],
        "external_actions": {"status": "none", "actions": [], "exclusions": [], "explanation": "Local test fixture only."},
        "activities": [{"id": "review", "title": "Review", "summary": "Independent checks"}],
        "workstreams": [{"id": "billing", "title": "Billing", "summary": "Customer invoices"}],
        "items": rows, "final_acceptance": {"item_key": rows[-1]["key"], "owner": "lead"}}


def team_proposal(members=None):
    return {"title": "Billing team", "summary": "Independently verify customer invoices.",
        "groups": [{"id": "review", "title": "Reviewers", "summary": "Check the outcome"}],
        "enable_chat": False,
        "members": [{"reason": "Verify the outcome", "group": "review", "item_ids": [], **m}
                    for m in deepcopy(members or [{"persona": "test-worker", "name": "sam"}])]}
