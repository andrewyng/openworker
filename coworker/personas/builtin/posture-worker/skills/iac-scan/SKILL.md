---
name: iac-scan
description: Scan Terraform/IaC with trivy config and fix what matters in code
---
Scan the repo's infrastructure-as-code and turn findings into minimal, safe Terraform
changes.

1. Pick the scanner (in this order — do NOT skip the scan if none is present):
   - `trivy config . --format json -o /tmp/iac.json` (also covers Dockerfiles/k8s)
   - `checkov -d . -o json > /tmp/iac.json` if the repo already uses it
   - Neither installed: ask for trivy with `request_tool("trivy", …)`. If the user
     declines, review the Terraform by hand against the exposure checklist in step 2
     and say in your report that the scan was manual.
   Do not suggest tfsec — it is deprecated; `trivy config` is its successor.
2. Triage by real exposure, reading the surrounding Terraform for each finding:
   - Internet-reachable (0.0.0.0/0 ingress, public buckets/ALBs) first.
   - Then identity blast radius (wildcard IAM, broad assume-role trust).
   - Then encryption/logging hygiene.
   Mark deliberate-looking configuration (a public website bucket, a bastion SG) as
   "intentional?" and ask rather than auto-fix.
3. Fix in the module where the resource is DEFINED (follow module sources), matching
   the repo's Terraform style — variables, locals, and tags the way the codebase
   already does them.
4. Validate every change: `terraform fmt` on touched files, then `terraform init
   -backend=false && terraform validate` when possible. Include `terraform plan`
   output in the PR when the user can run it — NEVER run `terraform apply`.
5. Deliver: exposure-ranked findings table (resource · issue · verdict · action), the
   fix branch/PR, and any "intentional?" items awaiting a human decision. Offer a
   pinned scanner config (e.g. `.trivyignore` with justifications) only for findings
   the team explicitly accepts.
