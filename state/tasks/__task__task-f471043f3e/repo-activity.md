# Repository activity - last 7 days

Generated 2026-09-02T18:35:24Z from LOCAL git. Origin here is Forgejo (git.home.arpa:2222);
never read this from github.com or `gh`, which answer about stale mirrors.

Authorship is counted, not commits: only commits authored by iconbaypark2900 are your work.
Tracked upstream clones (llama.cpp and friends) would otherwise read as activity.

## Commits by you, last 7 days

### agpack  (1 commits)
```
08-30 17:05  60aaaf1  agpack/tools/metered.py — step 5 metered access (pay-per-tool-call)
```

### openworker  (44 commits)
```
09-02 11:02  9976940  automations: say which provider error killed the run
09-02 10:45  8a05237  scheduler: backend readiness is "is it up", not "does it serve my default id"
09-02 09:37  f8a5b7b  gui: "In picker" now hides a persona from the picker
09-02 09:37  b106967  environment: keep the cached prefix still while the repo moves
09-02 00:30  5831eff  permissions: some commands always ask, past every allowlist
09-02 00:30  002b7fe  automations: a run nobody is watching has to be able to stop itself
09-01 10:49  ecffc2a  scheduler: a socket that answers is not a model that can serve
09-01 10:49  2771ba7  browser: filling a form and sending it are different tools
08-30 15:32  281990f  state: carry four days, including the mornings that failed
08-30 15:27  641992f  desktop: one place that knows where the user's screen is
08-30 15:18  f2f77e3  automations: catch-up waits for the model, and goes one at a time
08-30 14:36  e51280a  picker: a folder dialog that cannot open says so
08-26 20:49  3ee906a  state: carry the task output and scratch the database points at
08-26 20:39  aa71a98  state: carry the install in the repo, so a clone is a working OpenWorker
08-26 17:23  b0a2add  e2e: press ⌘B after the app has mounted the listener for it
08-26 17:23  d05bbb6  todo: a plan is not stale because of the batch that wrote it
08-26 17:23  a40c6ef  engine: tell the truth about why a run stopped, and leave one marker doing it
08-26 17:23  71f0d8f  automations: give a scheduled run the MCP tools its persona declares
08-26 17:23  095a2f4  browser: check for the binary a headed launch would actually exec
08-26 17:23  e2bb378  mcp: answer a prompt from another machine in the word the caller typed
```

### dcode-stack  (13 commits)
```
09-01 13:20  c3234f7  readiness: print the refusal rate, not the label around it
09-01 13:15  c5dfe6e  Rule 2 held 54% of the time; naming the failure took it to 100%
09-01 12:33  84bafae  harness-canary samples the live gate; one draw was deciding the card
09-01 11:54  316c3ba  govern without a second model, and gates that bind at the edges
09-01 10:49  cbce958  decode_proxy: an empty model list is 503, not 200
08-27 08:24  9bc3b30  brain: report the engine that is serving, and the proxy in front of it
08-27 08:24  b7fa880  A dead CUDA engine exits 0, so on-failure never restarted the brain
08-26 20:42  e08083b  decode_proxy: a thinking-off door onto a model already being served
08-26 19:21  2629361  decode_proxy: a tool call is a reply, so do not "rescue" it
08-26 15:07  94ade62  Size the serving profile for four consumers, not two
08-26 14:52  e24cce7  The auto-classifier is the main model; granite failed 5 of 6 destructive batches
08-26 14:51  3ed7dea  decode_proxy: reasoning is "reasoning" on vLLM, "reasoning_content" on llama.cpp
08-26 14:51  f19c6a8  vLLM serves the brain on :5100; llama.cpp becomes on-demand
```

### workstation-stack  (7 commits)
```
09-02 12:23  ad77dad  research: carry the four MCP tools its jobs name, not thirty-eight
09-02 12:10  2615afc  Apply moves to research, and the build persona loses the browser
09-02 11:33  2bf7e5f  mcp-gateway: rotate the GitHub token, and stop losing the API key on rm
09-02 11:16  43be7a4  The prompts driving 17 automations had no history behind them
08-29 10:08  639fd2d  Seven LibreChat secrets existed on one disk and in no backup
08-28 22:58  2ebc751  Track the quadlets that actually define the stack
08-28 22:20  468eae4  The mirror never protected the secrets; the age key has one copy
```

### ragtradesystem  (15 commits)
```
08-28 18:19  bd1ab79  A rejected sell no longer leaves its replacement buy to go through
08-28 18:19  5db9820  The cron defers to the daemon instead of taking over when it dies
08-28 16:46  a1e21bb  Two breakers and two always-green cards on one dashboard page
08-28 16:29  adb1444  Stopping the daemon did not stop trading — it moved trading to the ungated path
08-28 16:00  143749f  The daily-loss cap could not see an open loss, so it kept buying into one
08-28 15:52  c68265b  A JSON null defeated the default, and the swallow made it look handled
08-28 15:47  f67fc25  The one order-pricing path that still accepted a fabricated FX rate
08-28 14:00  3433dfd  Timestamp the exit quote and the fill, so the cost is attributable to execution
08-28 13:14  774f2de  Commit the regenerated params — HEAD's copy un-vetoes NVDA and TSM
08-28 13:13  a98e00d  Anchor the knowledge base to the package, not to the working directory
08-28 13:09  229aef9  Two cross-repo references that could not be followed, and a stale half-spread
08-28 13:09  495d247  Bound scheduler.log, which had reached 219MB writing every line twice
08-28 13:08  a2e081e  Scale the circuit breaker to real equity, without trading one halt bug for a worse one
08-28 13:08  6534d8a  Sandbox the suite's cwd, and re-arm the two guards that sandboxed away
08-26 18:29  475f07f  Move embeddings off retired Ollama, and refuse a model that only looks loaded
```

### sigma-trading  (43 commits)
```
09-02 12:35  b429124  Nothing is missing but a measurement — the four submitting jobs had never run
09-01 18:08  50a0dd4  backtest and paper return real numbers now, off real Alpaca bars
09-01 15:48  fbd2854  Every command runs, and run_full_scan had never completed a scan
09-01 14:20  44e3840  The system reads its own book now — two commands, and the reason none could
09-01 13:53  86db4a8  The walk-forward had no training window, and I nearly shipped a fifth copy
09-01 13:01  1d53d6b  The dashboard reported a risk budget nobody had set
09-01 12:42  121374d  Forty-five files reached the source repo through a symlink, not the tree
09-01 12:11  02e6ba8  A conservative error hid the only opportunity its dataset contained
08-30 20:05  89cbf8b  A rejected sell no longer buys the sleeve it was funding
08-30 19:41  7324f26  A cost is charged to a panel, and until today it never had to name which
08-30 19:20  6d7a396  A panel's digest pins its bytes and says nothing about what produced them
08-30 19:07  48df34e  The plan's closing advice sent a reader to redo finished work
08-30 17:39  50e87b3  assess_folds refused one fold at a time, after paying for the others
08-30 17:19  2ecc12d  The best panel this project can build still cannot see its best effect
08-30 17:03  a8cf082  Declare the share-volume floor before any roster exists to test it against
08-30 17:00  7c71db0  Measured: SPLIT scales volume, so the roster ranking was never contaminated
08-30 16:57  d0efcd2  The screen's rule named a constant that does not exist
08-30 16:54  0039bbe  004 ran: INCONCLUSIVE, and the sign it got right does not matter
08-30 16:36  f900bdf  Register 004 before running it, on a region declared rather than discovered
08-30 15:26  b1790b1  A planted alpha certified on a month the factors never saw
```

### sentinel-local  (7 commits)
```
08-29 22:14  29a2a2d  generator: honest detector/severity pools, shared-IP citations, pass-2 self-agreement
08-29 15:19  e6f0f3b  Fix markdown-fence wrapper defeating structured-output parse
08-29 13:28  b806246  Fix attack-table completeness and FN-metric denominator
08-28 18:42  4c25bbc  M6: supervisor, router, consensus, and state layers
08-28 15:46  aa6899e  A2: model-size ablation sweep (A1-A5 complete)
08-28 15:34  3a467cf  M5: eval harness, metrics, API baseline, and A1/A3/A4 ablations
08-28 13:44  e456531  Implement M1-M3: data pipeline, LLM client, single triage agent
```

### metered-web-broker  (15 commits)
```
08-30 15:12  b38aff4  deploy.yml: full GHCR publish + SSH deploy gate; add talking-points docs
08-28 20:59  122ebb8  deploy.yml: minimal echo dispatch test
08-28 20:53  1ee5a34  deploy.yml: add concurrency guard
08-28 20:44  a78b451  Deploy workflow comment tweak
08-28 20:37  c0e4216  Fix deploy: add docker/setup-buildx-action + fix outputs ref + deploy up -d
08-28 20:29  c9e409b  Bisect: add docker/build-push-action
08-28 20:28  9790210  Bisect: add docker/login-action
08-28 20:27  a520fa9  Bisect: add permissions block to minimal
08-28 20:26  b911f64  Bisect: build-and-publish only (no deploy job)
08-28 20:24  49c6c8d  Bisect: minimal deploy.yml (strip permissions + external actions)
08-28 20:23  6d91dd4  Bisect: remove needs: block from deploy.yml
08-28 20:20  e9b024b  Trivial test workflow to isolate deploy.yml dispatch failure
08-28 20:13  aa70da7  Fix deploy.yml dispatch failure + --no-deps bug
08-28 20:07  bca256e  Fix npm ci: regenerate lock to register @mwb/conformance workspace
08-28 19:57  4056904  Ship broker: rails, gateway, conformance, VPS deployment
```

### vqe_molecular_mcp  (3 commits)
```
09-01 09:29  cdccb3a  Implement step 7: offline VQE benchmarks tool
09-01 09:27  8016d64  Implement steps 5-6: PDB/molecule input, properties and chart payloads
08-31 21:15  66388e3  Implement VQE step 4 (run_vqe) and add VQE/hamiltonian tests
```

### surplus_property_intelligence  (5 commits)
```
08-27 14:08  75a72f2  feat: GSA source, price estimate, and frontend polish (B-era work)
08-27 13:42  4b81b42  feat(enrichment): B-11 runtime wiring for live sources
08-27 13:02  70cd2d5  feat(enrichment): B-9 flood-zone overlap source (FEMA NFHL)
08-27 10:29  b1d386a  feat(enrichment): B-8 cadastral geometry renewal source
08-27 01:01  3f17cbc  Tier B: fix has_building_permit threading (B-7) and add enrichment pipeline (B-11)
```

### polars-idiom  (2 commits)
```
08-27 02:15  660af84  Re-measure all four rows: the fine-tune beats the base, and the data says so
08-27 00:18  ded2033  Disclose the scorer in the table, not just the corpus
```

## Needs attention

| repo | last commit by you | days ago | unpushed | dirty | note |
|---|---|---|---|---|---|
| agpack | 2026-08-30 | 2 | 1 | 31 | 1 commit(s) only on this disk; |
| openworker | 2026-09-02 | 0 | 8 | 0 | 8 commit(s) only on this disk; |
| dcode-stack | 2026-09-01 | 1 | 5 | 0 | 5 commit(s) only on this disk; |
| workstation-stack | 2026-09-02 | 0 | 4 | 5 | 4 commit(s) only on this disk; |
| ragtradesystem | 2026-08-28 | 4 | 0 | 10 | - |
| sigma-trading | 2026-09-02 | 0 | 0 | 0 | - |
| sentinel-local | 2026-08-29 | 3 | 3 | 4 | 3 commit(s) only on this disk; |
| metered-web-broker | 2026-08-30 | 2 | 0 | 8 | - |
| vqe_molecular_mcp | 2026-09-01 | 1 | 3 | 102 | 3 commit(s) only on this disk; |
| surplus_property_intelligence | 2026-08-27 | 6 | 0 | 2 | - |
| polars-idiom | 2026-08-27 | 6 | 0 | 4 | - |

Unpushed commits exist ONLY on this disk. Report them; do not push them --
the ~/.githooks pre-push policy gates that deliberately and it is the user's call.
