// `tool_requested` payloads.
import type { CardState } from "../types";

export const toolRequestStates: CardState[] = [
  {
    id: "installable",
    title: "Pinned build available",
    note: "The fact strip is the product speaking (registry metadata), apart from the coworker's quoted reason.",
    payload: {
      name: "gitleaks",
      reason: "scan the git history for committed secrets",
      installable: true,
      version: "8.30.1",
      summary: "scans git history and the working tree for committed secrets",
      source: "github.com/gitleaks",
    },
  },
  {
    id: "not-installable",
    title: "No pinned build (old payload shape)",
    note: "No installable / version / summary: must render NOT installable, never a guessed Install offer (owner-hit 2026-08-14).",
    payload: { name: "somescanner", reason: "scan the Terraform for misconfigurations" },
  },
  {
    id: "long-reason",
    title: "Long reason",
    payload: {
      name: "trivy",
      reason:
        "check the three container images for known vulnerabilities before the pull request goes up, because the API image moved to a new Node base this week and the last scan predates that change",
      installable: true,
      version: "0.56.2",
      summary: "scans container images, filesystems and IaC for vulnerabilities and misconfigurations",
      source: "github.com/aquasecurity/trivy",
    },
  },
];
