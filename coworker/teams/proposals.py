"""Structured proposal intent. Declarations never grant execution authority."""
from __future__ import annotations

import re
from copy import deepcopy


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or list(properties), "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 600}
KEY = {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,47}$"}


def array(item, minimum=0, maximum=100):
    return {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum}


GROUP = obj({"id": KEY, "title": TEXT, "summary": TEXT})
EXTERNAL_ACTIONS = obj({
    "status": {"type": "string", "enum": ["none", "planned", "undetermined"]},
    "actions": array(TEXT, maximum=20),
    "exclusions": array(TEXT, maximum=20),
    "explanation": TEXT,
})
WORK_PROPOSAL_SCHEMA = obj({
    "title": TEXT, "summary": TEXT,
    "targets": {**array(TEXT, 1, 20), "description": "Products, repositories, accounts, or environments in scope. Not implementation filenames; put those in individual task descriptions."},
    "external_actions": EXTERNAL_ACTIONS,
    "activities": array(GROUP, 1, 12),
    "workstreams": array(GROUP, 1, 24),
    "items": array(obj({
        "key": KEY, "title": TEXT, "criteria": TEXT,
        "description": {"type": "string", "maxLength": 8000},
        "case": {"type": "string", "maxLength": 200},
        "activity": KEY, "workstream": KEY,
        "depends_on": array(KEY), "verifies": array(KEY),
    }, ["key", "title", "criteria", "activity", "workstream", "depends_on", "verifies"]), 1),
    "final_acceptance": obj({"item_key": KEY, "owner": {"type": "string", "enum": ["lead", "assigned_worker"]}}),
})
TEAM_PROPOSAL_SCHEMA = obj({
    "title": TEXT, "summary": TEXT, "groups": array(GROUP, 1, 24),
    "members": array(obj({
        "persona": TEXT, "name": TEXT, "model": TEXT, "reason": TEXT,
        "group": KEY, "item_ids": array({"type": "integer", "minimum": 1}),
        "connectors": array(TEXT, maximum=30),
        "connector_reasons": {"type": "object", "additionalProperties": TEXT},
        "approval_guidance": {"type": "string", "maxLength": 2400},
    }, ["persona", "name", "reason", "group", "item_ids"]), 1),
    "enable_chat": {"type": "boolean"},
})


def _check(value, schema, path):
    kind = schema["type"]
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if not isinstance(value, types[kind]) or (kind == "integer" and isinstance(value, bool)):
        raise ValueError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key} is required; supply explicit proposal information")
        for key, child in value.items():
            rule = props.get(key, schema.get("additionalProperties", False))
            if not rule:
                raise ValueError(f"{path}.{key} is not a supported proposal field")
            _check(child, rule, f"{path}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError(f"{path} has an invalid number of entries")
        for i, child in enumerate(value):
            _check(child, schema["items"], f"{path}[{i}]")
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value.strip()) <= schema.get("maxLength", 10000):
            raise ValueError(f"{path} must be concise and nonempty")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"{path} must be a short lowercase identifier")
    elif kind == "integer" and value < schema.get("minimum", 0):
        raise ValueError(f"{path} must be positive")


def _unique(rows, key, label):
    ids = [r[key] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{label} must have unique {key} values")
    return set(ids)


def validate_work_proposal(args):
    _check(args, WORK_PROPOSAL_SCHEMA, "proposal")
    acts = _unique(args["activities"], "id", "activities")
    streams = _unique(args["workstreams"], "id", "workstreams")
    keys = _unique(args["items"], "key", "items")
    external = args["external_actions"]
    if bool(external["actions"]) != (external["status"] == "planned"):
        raise ValueError("external_actions.actions must list actions only when status is planned; use explanation for uncertainty")
    if args["final_acceptance"]["item_key"] not in keys:
        raise ValueError("final_acceptance.item_key must reference a proposed task")
    for item in args["items"]:
        if item["activity"] not in acts or item["workstream"] not in streams:
            raise ValueError(f"{item['key']}: unknown activity or workstream")
        for field in ("depends_on", "verifies"):
            refs = item[field]
            if len(refs) != len(set(refs)) or any(r not in keys or r == item["key"] for r in refs):
                raise ValueError(f"{item['key']}.{field}: use unique, existing, other task keys")
    graph = {i["key"]: i["depends_on"] for i in args["items"]}
    visited, active = set(), set()
    def visit(key):
        if key in active:
            raise ValueError("depends_on contains a cycle; grouping is not sequencing")
        if key not in visited:
            active.add(key)
            for ref in graph[key]:
                visit(ref)
            active.remove(key)
            visited.add(key)
    for key in graph:
        visit(key)
    if acts != {i["activity"] for i in args["items"]} or streams != {i["workstream"] for i in args["items"]}:
        raise ValueError("every activity and workstream must contain at least one task")
    return deepcopy(args)


def validate_team_proposal(args):
    _check(args, TEAM_PROPOSAL_SCHEMA, "proposal")
    groups = _unique(args["groups"], "id", "groups")
    _unique(args["members"], "name", "members")
    names = [m["name"].strip().lower() for m in args["members"]]
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,23}", name)
            or name in {"lead", "user", "board"} for name in names):
        raise ValueError("worker names must be unique mention-safe callnames, max 24 characters; lead/user/board are reserved")
    for member in args["members"]:
        if member["group"] not in groups:
            raise ValueError("each member must reference a declared group")
        if len(member["item_ids"]) != len(set(member["item_ids"])):
            raise ValueError("item_ids must be unique per worker")
    if groups != {m["group"] for m in args["members"]}:
        raise ValueError("every staffing group must contain a worker")
    return deepcopy(args)


PROPOSAL_GUIDANCE = """Proposal cards are structured decisions, not essays. Use propose_work_items with a short
customer outcome (title, summary), explicit targets, domain-appropriate activities and
workstreams (id/title/summary), and tasks keyed within this proposal. Categories do not
imply sequencing: depends_on names actual prerequisite keys; verifies names tasks being
independently checked. Criteria are 1–3 concise, testable statements, without a label.
Targets name the product/repository/account/environment in scope, not implementation
filenames. Put file ownership and implementation paths inside individual task descriptions.
Name a real final_acceptance item and its owner (lead or assigned_worker).
Declare external_actions explicitly: status none, planned, or undetermined; actions lists
only concrete planned external actions, exclusions lists only explicit exclusions, and
explanation states the basis or uncertainty. Never infer local-only from missing connectors.
Active scans contact external systems. Clarify uncertain security targets/authorization
before scanning. These declarations describe intent, never grant execution permission.
Surface changes to external-action intent to the user before acting beyond the approved plan.
For staffing, propose_team supplies title, summary, groups and each member's group, reason,
and item_ids (approved board IDs, not proposal keys). These are planned responsibilities,
not assignments. Use an empty item_ids list for standing workers with no current task.
Call team_options first. Suggest only connected, policy-allowed connectors in members[].
connectors; connector_reasons gives one short reason per suggestion. Outside the worker's
usual set, quote the user's request. Suggestions only pre-tick the card; the human decides.
Group labels fit the domain: engineering Build/Verify, security Assess/Remediate/Validate,
marketing Research/Create/Review. Name staffing groups for their actual responsibilities,
not their implementation persona: a swe-worker doing security belongs in Assessment &
remediation, not automatically Build. Do not invent isolation, machine placement, or access.
For each member, optionally provide approval_guidance (at most 2400 characters): concrete
routine actions and explicit restrictions, grounded in the user's scope. It is editable
under Advanced in the staffing card and saved only with the human's approval. It guides
Auto-approve, never grants connectors/tools, overrides restrictions, or certifies claims
such as a disposable database. Later steering does not modify the approved text.
"""
