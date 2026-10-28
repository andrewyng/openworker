# Agent Sandbox bootstrap source

agent-sandbox-v1.0.6.yaml is the unmodified core installation manifest from
Kubernetes SIGs Agent Sandbox v1.0.6, distributed under Apache License 2.0.
See LICENSE.agent-sandbox for its license.

Source: https://github.com/kubernetes-sigs/agent-sandbox/releases/tag/v1.0.6
SHA-256: b995db5839f2bdca3a24b8bbefdec405df662d6dd223eec8f5dc74098c974231.

The offline OWS exporter substitutes the digest of the ARM64 controller image used
in local qualification, and appends encrypted, ownership-tagged EBS storage and
policies for trusted platform/service components. It does not broaden the namespace
or network policies of untrusted OpenShell tool sandboxes. Fresh EKS bootstrap and
strict CNI enforcement still require live cloud qualification.
