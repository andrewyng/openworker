# Coding workspaces, source transfer and GitHub access

Use this guide to choose where workers edit code, how they receive the initial
source, and how their changes return. These are three separate choices. Creating
a child does not implicitly clone GitHub, grant credentials, commit, push or merge.

All agent loops in this implementation run in the trusted harness, outside the
OpenShell sandboxes. ToolRunner runs inside each sandbox. In diagrams below,
"builder tools" means that agent's execution processes, not its model loop.

Related: [SDK coding guide](CODING-AGENTS.md),
[design decisions](CODING-AGENT-PRESETS.md),
[handoff implementation](CODING-HANDOFF-IMPLEMENTATION.md),
[milestones and journal](CODING-AGENT-PRESETS-PLAN.md),
[test coverage](CODING-AGENT-PRESETS-TESTING.md).

## Choose a scenario

| Scenario | How source arrives | How changes return | GitHub access inside the child | Current qualification |
| --- | --- | --- | --- | --- |
| One coding agent | Application supplies an existing workspace | Local files/commits | Only if separately configured | C2 verified on Linux/OpenShell |
| Team sharing one sandbox | Separate linked worktrees of an existing repository | Lead inspects local commits | Not needed for sharing work | C1–C3 verified |
| Team with independent sandboxes | Harness transfers a pinned Git bundle; ToolRunner creates each checkout | Submitted bundle → verifier → lead | Not needed for source transfer or artifact handoff | Initial transfer/admission verified; full submission workflow is C4 in progress |
| Trusted GitHub checkout/publication | Trusted broker fetches source or publishes a submitted artifact | Scoped branch publication with an exact commit | Not needed in the child | C5 upcoming |
| Git CLI inside a sandbox | Git uses HTTPS through OpenShell with an attached credential provider | Explicitly permitted fetch/push | Required for these direct GitHub requests | OpenShell mechanism exists; our adapter integration/qualification is C6 upcoming |

The recommended team default is one shared sandbox with separate builder
worktrees. Choose independent sandboxes when workers need separate execution
boundaries. Artifact handoff is the default transport between those sandboxes;
GitHub publishing is an opt-in addition.

## 1. Shared sandbox, separate worktrees

Useful when builders work on the same project and may share the sandbox's overall
access. Each builder has its own cwd, branch and ordinary file-tool scope.

```text
Trusted harness: lead loop + builder loops + verifier loop
                      |
                      | governed tool calls
                      v
+-------------------- ONE OpenShell sandbox ---------------------+
|                                                               |
|  source/                 workers/ui/          workers/api/     |
|  Lead integration        Builder UI tools     Builder API tools|
|  existing main worktree  own branch/cwd        own branch/cwd   |
|       |                       |                    |           |
|       +------- shared Git objects and common metadata ---------+
|                                                               |
|  Verifier tools inspect an explicit submitted commit.           |
+---------------------------------------------------------------+
                      |
                      v
          Lead explicitly reviews and integrates commits
```

No upload or GitHub push is needed for the lead to inspect a builder's commit.
Linked worktrees depend on shared Git metadata, which is deliberately accessible
inside this shared sandbox. A child's cwd/file-tool scope is useful protection
against accidental edits; shell commands remain bounded by the sandbox as a whole.
Worktrees do not provide separate kernel isolation. Coordinate ports as well as
file edits.

Application choices: existing repository, pinned base SHA, per-builder worktree
defaults, builder/verifier types, concurrency, budgets and normal approval mode.
Spawning never creates a commit or integrates changes on its own.

## 2. Independent sandbox, source delivered without GitHub

Useful when each worker should have its own files, Git metadata and sandbox.
The source must already exist in an authorized execution environment; see
scenario 3 for how it gets there initially.

```text
Existing source in lead execution environment, pinned at commit A
                         |
              Source ToolRunner exports a Git bundle
                         |
                         v
               Trusted harness -> Artifact store
                         |
              private ToolRunner connection: bundle chunks
                         |
                         v
           +--------- CHILD OpenShell sandbox --------+
           | ToolRunner                               |
           |   1. Receive bytes in private staging    |
           |   2. Verify digest and Git objects       |
           |   3. Initialize an independent repo      |
           |   4. Import bundle; check out commit A   |
           |                                          |
           | checkouts/<team>/<instance>/             |
           |   .git/       own Git metadata           |
           |   files...   exact committed snapshot    |
           +------------------------------------------+
                         |
              Return original checkout/Git receipt
                         |
                         v
           Harness persists acknowledgement atomically
                         |
                         v
           Child model loop may now start using that cwd
```

ToolRunner prepares the checkout **before the child agent starts**. It does not
run a model or choose its own source. The trusted controller chooses the original
artifact and destination from the application's configuration and reservation.

This is local Git import/checkout, not `git clone https://github.com/...`. The
bundle contains the required Git objects. The child needs neither a GitHub token
nor GitHub network access for this operation. Artifact-store access stays with
the trusted controller; it sends bounded chunks over the existing private channel.
No artifact URL or storage key is given to the agent or ToolRunner.

Current transfer uses 32 KiB chunks, a 64 MiB bundle limit, and separate limits
for expanded checkout bytes/files. It preserves committed binary changes,
renames and deletions. It does not automatically export untracked/ignored files,
download submodules or Git LFS payloads, or run project setup scripts.

Configuration pins application repository identity, source directory, exact Git
commit, allowed child RuntimeProfile and artifact-store binding. The default
independent destination is `checkouts/<team>/<instance>`. The child cannot replace
the source with a download URL, choose a storage credential or widen its profile.

Status: the trusted source/import controller and child admission are physically
qualified on Linux/OpenShell, including two separate child sandboxes and commits
after admission. Public isolated preset helpers and the complete submitted-code
workflow remain gated while C4 is completed.

## 3. Where does the original source come from?

Offline child transfer does not mean the original repository appears by itself.
There are three source-acquisition choices:

```text
A. Application stages source                         Current contract
   Existing checkout / application-controlled import
                 |
                 +----> Authorized source environment at pinned commit A

B. Trusted GitHub broker                              C5 upcoming
   GitHub <---- scoped credential ---- Trusted broker
                                         |
                                         +----> Pinned source bundle

C. Direct Git HTTPS in source sandbox                 C6 upcoming
   GitHub <---- OpenShell proxy <---- Git + placeholder
                 real token                  |
                                             +----> Local repo at commit A

Each choice can feed the same bundle-based child import from scenario 2.
```

Our current contract starts with an already available source checkout. Application
staging must use an authorized preparation path; it does not mean mounting an
arbitrary host directory into an OpenShell sandbox. Trusted GitHub fetching and
sandbox Git fetching require their own adapter and permission configuration.

Read-only GitHub checkout permission is separate from publication permission. An
app can obtain private source while denying GitHub access to every child.

## 4. Returning work: artifact → verifier → lead

Useful when workers must return code without GitHub write access. This is the
remaining C4 workflow; the initial source import above is already verified.

```text
Builder sandbox                Trusted harness / artifact catalog
  source at A
  edit, test, commit B
  export exact B  ------------> Submitted handoff H
                               repository + base A + commit B
                               bundle digest + producer + reports
                                          |
                         +----------------+----------------+
                         v                                 v
              Verifier sandbox                   Lead execution environment
              independent checkout B             receive B explicitly
              inspect/test exact B               inspect report bound to B
              report evidence for B              check integration base
                         |                                 |
                         +--------> durable report --------+
                                                           v
                                                explicit integration / conflict
```

A verifier must name the submitted handoff; it must not silently receive the
original base A instead. An agent-authored report is evidence to inspect, not
human authorization or proof that integration succeeded. If the lead's base has
changed, it handles conflicts and verifies the resulting integrated revision.
The application can keep GitHub publication disabled throughout this workflow.

## 5. Harness-managed GitHub access

Preferred opt-in mode when code should reach GitHub while credentials stay outside
worker sandboxes. This is C5 upcoming, not a currently exposed publishing API.

```text
Child sandbox                Trusted harness / publishing broker          GitHub
  commit B
  export artifact ----------> validate artifact + exact repository
                              check permitted branch prefix / operation
                              apply approval policy
                              inspect expected remote tip
                              publish with scoped credential ------------>
                <------------ durable branch + exact commit result
```

The model requests publication through an authorized tool using a handoff and
registered connection reference. It does not supply a host path, token or arbitrary
remote URL. Checkout/read and publication/write grants are separate. Publishing
does not implicitly permit force-push, merge, PR creation or repository admin.

Only the trusted broker needs network access to GitHub for these operations. The
child can remain without GitHub egress. A publication error cannot switch the
workflow into sandbox-CLI mode or inject a token as a fallback.

## 6. Direct Git CLI through OpenShell placeholders

Useful when the application deliberately permits Git/`gh` to contact GitHub from
inside a sandbox. For the OpenShell adapter, prefer its endpoint-bound credential
placeholders instead of handing the real token to ToolRunner. This mechanism is
documented by OpenShell; its wiring and qualification in our harness are C6 work.

```text
Trusted setup / credential owner
  real scoped GitHub token
              |
              v
       OpenShell credential provider
              |
              | opaque placeholder attached to selected sandbox
              v
+------------- OpenShell sandbox ----------------+
| Git / gh, launched by authorized tool calls    |
| HTTPS request containing placeholder          |
+-----------------------------------------------+
              |
              v
       OpenShell proxy
         1. Check allowed binary + destination/request
         2. Check credential's endpoint binding
         3. Replace placeholder with real credential
              |
              v
           GitHub
```

The real token stays in OpenShell's trusted credential path. ToolRunner/Git uses
the supplied opaque reference. This requires inspected HTTPS; do not assume that
the same mechanism works for Git over SSH or an opaque TLS tunnel. Git's client
authentication must also be configured to use the placeholder; attaching a
provider is not a claim that our ToolRunner adapter already supplies that setup.
[OpenShell credential injection](https://docs.nvidia.com/openshell/latest/how-it-works/providers/overview#how-credential-injection-works)

Two checks are independent: allowing GitHub traffic does not by itself grant use
of a token, and supplying a credential reference does not bypass network policy.
OpenShell's example GitHub profile permits clone/fetch while denying push by
default; direct publication requires a separately reviewed grant. Host-level access
alone is not repository isolation: use appropriately scoped credentials and
supported request restrictions for the intended repository/operations.
[Example GitHub profile](https://github.com/NVIDIA/OpenShell/blob/main/providers/github.yaml)

A placeholder hides the secret value; it still enables an authorized capability
inside that sandbox. Do not describe it as private to one agent in a shared
sandbox. Scope attachment, permitted binaries/requests, expiry, refresh and
revocation explicitly. Check the pinned OpenShell release's behavior and qualify
those lifecycle operations before enabling this adapter. No fallback to a raw token.

## Which configuration owns what?

| Choice | Owner / configuration | Meaning |
| --- | --- | --- |
| Agent type and instance | Team and spawn configuration | `builder` is a reusable type; `builder-ui` is one instance. |
| Shared vs independent placement | Allowed RuntimeProfiles, delegation, spawn sandbox selection | Where tools run, within the application's ceiling. |
| RuntimeProfile, e.g. `small@1` | OpenWorker service configuration | Provider, resources and supported execution policy; not a GitHub credential. |
| Child cwd/file-tool scope | Worktree/checkout workspace selection and receipt | Where relative tools operate; separate from the kernel sandbox boundary. |
| Initial source | Application's repository identity, directory and pinned commit | What snapshot a builder receives; not an arbitrary GitHub URL. |
| Verifier source | Explicit submitted handoff identity | What exact revision is verified. |
| Artifact storage | Trusted service configuration | Where bundles live; credentials never enter child sandboxes. |
| Publication mode | Explicit application policy | Disabled, trusted harness tool, or direct sandbox CLI. No silent fallback. |
| OpenShell provider profile | OpenShell's imported profile/policy | Credential aliases and allowed endpoints/binaries; distinct from RuntimeProfile. |
| OpenShell credential provider instance | Trusted credential owner + explicit sandbox attachment | Actual stored credential and lifecycle; sandbox receives placeholders. |

These are capability/configuration choices, not a new copy-and-paste SDK API.
Use the [SDK guide](CODING-AGENTS.md) for runnable syntax and the
[journal](CODING-AGENT-PRESETS-PLAN.md) for what is currently shipped/qualified.

## Recovery: the same rule in every scenario

```text
Persist original intent -> dispatch operation -> reply lost
                                                |
                                                v
                                  mark / report outcome unknown
                                                |
                                                v
                                  inspect ORIGINAL resource/status
                                     /                     \
                              prove outcome          still unknown
                                  |                       |
                         acknowledge original     retain work + tell lead

Never interpret a lost connection as permission to repeat checkout, integration
or push. Repeating immutable chunks is allowed only for the same offset/bytes.
```

Preparation runs before the child's first turn. Importing source does not authorize
executing project code; build/test commands still use normal policy and approvals.
No scenario automatically commits, merges or pushes because a child was spawned.
