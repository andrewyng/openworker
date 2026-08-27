---
name: automation-design
description: Assess repeatable work and recommend the smallest safe workflow, team, or schedule with explicit approval gates.
source: worker
---

# Automation design

Map the trigger, inputs, output, decision points, failure modes, cost, and stop condition
before recommending automation. Choose the smallest useful mechanism: direct work, a
read-only specialist, a durable worker, or a schedule. State why automation is justified
and which decisions must remain human-approved.

Treat connections, credentials, schedules, and external writes as dependencies until
verified. Do not claim an integration exists or create an automation without the required
approval. Record material decisions and evidence, not periodic status noise.
