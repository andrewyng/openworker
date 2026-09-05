---
name: aws-posture
description: Read-only AWS posture check — public exposure, IAM blast radius, hygiene
---
Check the live AWS account's security posture using strictly read-only CLI calls, then
fix root causes in the IaC.

HARD RULE: read-only means read-only — describe/list/get/simulate calls only. No
create/put/update/delete/attach, no `terraform apply`, ever. If a fix is needed, it goes
into Terraform for the team to apply.

1. Confirm access and scope: `aws sts get-caller-identity` (mask the account id to its
   last 4 digits in anything you write). Ask which regions matter; default to the ones
   the Terraform state uses.
2. Sweep the high-signal surfaces, most exposed first:
   - Public entry points: S3 buckets (`get-public-access-block`, bucket policies),
     security groups open to 0.0.0.0/0 on sensitive ports, public RDS/ES endpoints,
     ALB listeners without TLS.
   - IAM blast radius: users with attached admin policies, wildcard `Action`/`Resource`
     in customer-managed policies, stale access keys (`iam get-credential-report`),
     roles with overly broad trust policies.
   - Hygiene: CloudTrail on and multi-region, default EBS/S3 encryption, root-account
     MFA (from the credential report).
3. Cross-reference each finding against the repo's Terraform: is the risky config
   defined in code (fix it there), drifted from code (flag the drift), or unmanaged
   (propose importing it)?
4. Deliver: an exposure-ranked posture report (finding · resource · evidence command ·
   where it's defined · action), the IaC fix branch for what's code-managed, and a
   short list of items needing a human decision. Every claim carries the exact
   read-only command that evidences it, so the team can re-run and verify.
