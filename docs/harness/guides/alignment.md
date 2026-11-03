# Agent alignment

An action can be technically permitted and still be unrelated to the user's task.
OpenWorker separates **what an agent can reach** from **whether an action is
justified by the user's request**.

For example, a coding agent may be allowed to read a project file. That does not
mean a request to fix a UI bug authorizes uploading the file to an external service.

## Choose an approval mode

| Mode | What happens |
|---|---|
| Ask | Required decisions go to a human; responses and session grants are durable. |
| Auto-approve | A separate reviewer evaluates eligible actions. Uncertainty or reviewer failure pauses for a human. |
| Bypass | Human approval is skipped. Required access policy, sandbox limits and guardrails still apply. |

Configure the reviewer's model separately from the working agent. Keep task context,
tool effects and source-labelled information available to the review process.
Tool output, memory and messages from another agent are data, not new human consent.

## Keep the layers independent

- **Access policy and OpenShell** constrain resources and execution.
- **Approval/review** decides whether an action is appropriate for the task.
- **Guardrails and hooks** apply required application rules to content and actions.
- **Compliance records** retain the decisions and committed activity for inspection.

Tool categories describe effects; they are not a safety ranking. Calling a tool
“read” or “execute” cannot remove another restriction. “Allow always” is scoped to
the current session and its bindings, and does not disable mandatory checks.
Auto-skipping a question also does not count as approval.

These mechanisms help enforce intent; a model reviewer is not a guarantee of perfect
judgment. Evaluate it against representative tasks and adversarial inputs from your
application. See [session grants](../SESSION-GRANTS.md),
[application policy](../APP-POLICY-CONTRACT.md), [guardrails](../GUARDRAILS-QUICKSTART.md)
and the [safety test catalog](../GUARDRAILS-TESTING.md).
