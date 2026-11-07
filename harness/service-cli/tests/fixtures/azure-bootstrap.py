#!/usr/bin/python3
"""Isolated Azure CLI contract fixture. Never makes a network request."""
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
home = Path(os.environ["HOME"])
with (home / "az.log").open("a") as f:
    f.write(json.dumps(args) + "\n")
scenario = os.environ.get("OW_AZ_TEST_SCENARIO", "ready")
bundle = Path(os.environ["OW_AZ_TEST_BUNDLE"])
ident = json.loads((bundle / "azure-identities.json").read_text())
target = ident["target"]
scope = ident["resourceGroupScope"]


def value(name):
    return args[args.index(name) + 1] if name in args else None


def emit(v):
    print(v if value("-o") == "tsv" else json.dumps(v))
    sys.exit(0)


if scenario == "timeout":
    time.sleep(5)
if scenario == "invalid-json":
    print("not-json-sensitive-canary")
    sys.exit(0)
if scenario == "too-large":
    print("x" * (1024 * 1024 + 20))
    sys.exit(0)
if args[:2] == ["cloud", "show"]:
    c = {
        "name": "AzureCloud",
        "endpoints": {
            "resourceManager": "https://management.azure.com/",
            "activeDirectory": "https://login.microsoftonline.com/",
        },
    }
    if scenario == "custom-endpoint":
        c["endpoints"]["resourceManager"] = "https://example.invalid/"
    query = value("--query")
    emit(c["name"] if query == "name" else c["endpoints"]["resourceManager"] if query else c)
if value("--subscription") != target["subscription"]:
    raise RuntimeError("Wrong explicit subscription")
if args[:2] == ["account", "show"]:
    a = {
        "id": target["subscription"],
        "tenantId": target["tenant"],
        "environmentName": "AzureCloud",
        "state": "Enabled",
    }
    if scenario == "wrong-tenant":
        a["tenantId"] = "11111111-1111-1111-1111-111111111111"
    emit(a.get(value("--query")) if value("--query") else a)
if args[:2] == ["group", "show"]:
    g = {"id": scope, "properties": {"provisioningState": "Succeeded"}}
    if scenario == "wrong-group":
        g["id"] = scope + "-other"
    emit(g["id"] if value("--query") else g)
if args[:3] == ["role", "definition", "list"]:
    assert value("--scope") == scope and value("--name") == ident["roleDefinitionGuid"]
    emit(1 if scenario == "existing-role" else 0)
if args[:3] == ["role", "assignment", "list"]:
    assert value("--scope") == scope
    emit(0)
if args[:2] == ["provider", "show"]:
    provider = value("--namespace")
    emit({"namespace": provider, "registrationState": "NotRegistered" if scenario == "missing-provider" else "Registered"})
if args[0] == "rest":
    url = value("--url")
    assert url in [ident["roleUrl"], ident["assignmentUrl"]], "Unknown URL"
    is_role = url == ident["roleUrl"]
    payload = json.loads((bundle / ("azure-setup-role.json" if is_role else "azure-setup-assignment.json")).read_text())
    payload["name"] = ident["roleDefinitionGuid"] if is_role else ident["roleAssignmentGuid"]
    payload["id"] = ident["roleDefinitionId"] if is_role else scope + "/providers/Microsoft.Authorization/roleAssignments/" + ident["roleAssignmentGuid"]
    if not is_role:
        payload["properties"]["scope"] = scope
    method = value("--method")
    if method == "put":
        if scenario == "assignment-fails" and not is_role:
            print("AuthorizationFailed secret-error-canary", file=sys.stderr)
            sys.exit(1)
        emit(payload)
    assert method == "get", "Unexpected mutation"
    if scenario == "missing-role" and is_role:
        print("ResourceNotFound sensitive-error-canary", file=sys.stderr)
        sys.exit(1)
    if scenario == "broad-role" and is_role:
        payload["properties"]["permissions"][0]["actions"] = ["*"]
    if scenario == "wrong-principal" and not is_role:
        payload["properties"]["principalId"] = "22222222-2222-2222-2222-222222222222"
    if scenario == "conditional-assignment" and not is_role:
        payload["properties"]["condition"] = "unreviewed-condition"
    emit(payload)
raise RuntimeError("Non-allowlisted fake Azure command: " + json.dumps(args))
