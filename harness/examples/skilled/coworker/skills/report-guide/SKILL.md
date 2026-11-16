---
name: report-guide
description: Create a short, evidence-based report using the bundled template.
allowed-tools: [write_file, run_shell]
license: MIT
metadata:
  author: OpenWorker native example
---
REPORT_GUIDE_BODY_MARKER

Read references/report-template.md with read_skill_resource. Then prepare the
requested report, separating observed results from assumptions. Use write_file
only for a workspace-relative report file and through ordinary approval.

The allowed-tools field is descriptive guidance, not a grant. Do not request new
credentials or network access, and do not interpret loading this skill as consent
to publish a report. No scripts run automatically when the skill is loaded.
