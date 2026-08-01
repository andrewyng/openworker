# kp — knowledge-pack reorganizer

Reorganizes the harvested `knowledge_packs/` tree from a **source-mirrored**
layout (course / week / project folders) into an **ExpertPack composite**
organized by *retrieval intent*.

The organizing principle, borrowed from
[brianhearn/expert-pack](https://github.com/brianhearn/expert-pack): a
directory encodes what a file is *for*, never where it came from. Provenance
moves into frontmatter tags (`domain:`, `term:`, `course:`), where it stays
queryable — a file can carry two domains, a folder forces you to pick one.

## Layout produced

```
knowledge_packs/
├── ai-foundations/         taught-module knowledge
│   ├── concepts/           flat, named <module-prefix>-<slug>.md
│   ├── workflows/
│   ├── troubleshooting/
│   ├── faq/
│   └── reference/          papers, standards, API surface
├── msc-final-project/      the final-project codebase
│   └── reference/
├── coursework-archive/     admin; retrieval_strategy: navigation
└── _quarantine/            set aside, not deleted — mirrors origin paths
```

## Usage

```bash
# 1. inventory the current tree (read-only)
python3 tools/kp/kp.py index --root knowledge_packs --out build/inventory.jsonl

# 2. build a reviewable mapping (read-only)
python3 tools/kp/kp.py plan --inventory build/inventory.jsonl --out build/plan.csv

# 3. review build/plan.csv by hand, then dry-run
python3 tools/kp/kp.py apply --root knowledge_packs --plan build/plan.csv

# 4. commit to it
python3 tools/kp/kp.py apply --root knowledge_packs --plan build/plan.csv --execute

# undo, using the manifest written by step 4
python3 tools/kp/kp.py revert --manifest build/migration-manifest.json

# 5. write the frontmatter contract onto the migrated files
python3 tools/kp/kp.py stamp --root knowledge_packs \
    --plan build/plan.csv \
    --manifest build/migration-manifest.json \
    --quarantine-refusals --execute

# 6. collapse re-harvests of the same source, report quality signals
python3 tools/kp/kp.py triage --root knowledge_packs \
    --quarantine-redundant --execute

# 7. gate: is the pack fit to index? exits non-zero if not
python3 tools/kp/kp.py validate --root knowledge_packs --strict

# 8. re-harvest source material that was never ingested
python3 tools/kp/kp.py queue --source ~/Onedrive/AI_Master-2023 \
    --inventory build/inventory.jsonl --out build/reharvest.sh
```

`plan.csv` is the review surface and is meant to be edited: change a `type`,
retarget a `dest_rel`, or flip an `action` to `quarantine`, and `apply` honours
it. Nothing mutates the tree unless `--execute` is passed.

## Actions in the plan

| action | meaning |
|---|---|
| `move` | relocate into a pack under its inferred type |
| `merge` | title-duplicate of another row; **skipped by default**, needs `--merge-duplicates` |
| `quarantine` | moved to `_quarantine/`, preserving origin path. Never deleted |
| `navigation` | administrative; kept but excluded from the RAG index |

## Design notes

**Identity is derived from the origin path.** `origin_id` is
`sha1(origin_rel)[:12]`, so a plan stays joinable to its files and every move is
reversible via the manifest. Filenames alone are useless as identity here: the
harvester names files from LLM-generated titles, so the same source harvested
twice produces two *different* filenames. `Draft` and `PythonProject` share
only 2 basenames despite covering the same codebase — which is why duplicate
detection keys on the normalized title, not the filename.

**Type inference classifies on proportion, not presence.** `harvester.py`'s
`SYSTEM_PROMPT` *mandates* an "## Anti-Patterns / Mistakes to Avoid" and a
"## Frequently Asked" section in every file it writes. Those headings are
therefore boilerplate and carry zero signal — an early presence-based version of
this classifier labelled 158 files `troubleshooting` against 79 `concept`. What
counts is the share of the body a section occupies, with the title as the
strongest single signal.

**`queue` defaults to documents.** Code extensions are opt-in behind
`--include-code`. Sweeping `.py` across a project tree is precisely what
produced the original blowup — one summary per source file, most of them
vendored library internals. Without the flag: 762 files queued. With it, across
the same tree: 7,177.

**Vendored source is always quarantined.** A checkout of `imbalanced-learn`
under `Practical_ML/Project2/Testing` contributed 284 files of summaries of
someone else's library — the definition of general knowledge, and the first
thing EK triage should strip.

## Taxonomy

`taxonomy.yaml` holds the module → prefix/domain/term/course mapping and the
quarantine, navigation, and out-of-scope segment lists. **Edit that file, not
the code**, when a module is renamed or added. Course codes recovered so far:
COMP9016 (Knowledge Representation), COMP9058 (Metaheuristics), COMP9061
(Practical ML), COMP9057 (Decision Analytics), COMP9062 (Big Data), COMP9069
(Robotics), COMP9072 (Distributed Ledger), COMP9097 (TinyML), COMP8062 (Cloud
Automation), COMP9053 (Scripting for Cybersecurity), SOFT9022 (Programming
Language Design).

## Scope

In scope: `AI_Msc_Lectures_2023_2025` (all 17 modules), `Resources/`,
`2_2_AI_Project`, `SOFT9022`, `Robotics and auonomous systems`, `Standards_ISO`.

Out of scope: `Computing`, `Electronic Engineering`, `Management & Enterprise`,
`Cork School of Music`, `GoogleDrive` — institutional module catalogues for
other programmes, ~5,550 PDFs.

## stamp

`stamp` writes the ExpertPack frontmatter contract — `id`, `schema_version`,
`retrieval_strategy`, `verified_at`, `content_hash` — onto migrated files,
sourcing `type`, tags, and `domain`/`term`/`course` from the plan row each file
joins back to. It is idempotent: `content_hash` covers the body only, so
re-stamping an unedited file rewrites it byte-for-byte.

**Identity survives renames.** `--manifest` is repeatable and order-sensitive,
each pass resolving through the ones before it. A file migrated by `apply` and
then renamed by a later pass still stamps with the `origin_id` of where it
started, so `id` stays stable across reorganizations.

**The directory wins over frontmatter.** After migration the folder is what the
plan committed to and what a retriever sees; a `pack:` or `type:` that disagrees
is stale. 235 of 269 files still declared `pack: "ai-masters"` post-migration.

**Two harvester failure modes are handled here**, both invisible until you try
to parse the corpus:

- *Fence leakage* — the fence language (`expert_pack`, `yaml`) is emitted
  without the fence, so frontmatter opens on line 2 and every parser misses it.
  The document below is intact, so `stamp` repairs the line rather than
  discarding the file. 11 files.
- *Refusals* — the model returns an apology ("I'm unable to process images
  directly") instead of a document. These are short, have no frontmatter, and
  are not knowledge; `stamp` refuses to stamp them and `--quarantine-refusals`
  moves them to `_quarantine/_harvest_refusals/`. 4 files. They are re-harvest
  candidates, which is why they are set aside rather than deleted.

Detection requires both a refusal phrase *and* a body under
`MIN_STAMPABLE_BYTES`; a long document that merely contains the words "I'm
sorry" is not a refusal.

## triage

**The same source document harvested twice is the defect this exists for.** It
is not hypothetical: 20 documents were each harvested into both a rich summary
and a thin one, and the split was systematic — the `lectures/` origin tree
produced full summaries while `organized_lectures/` produced impoverished ones
from the identical document. Left alone they were 36 near-duplicate pairs of
retrieval noise, one cluster of 18 covering a single assignment.

Grouping is on the source **basename**, not the full origin path. The same
lecture PDF is mirrored into several origin trees, so full paths differ while
the document is one and the same — the full path is exactly the key that fails
to catch this. The richest harvest wins: two passes over one document differ in
how much they retained, never in what they were about.

The thin copy is *not* a strict subset — 50–76% term coverage, 33–70 unique
terms — so it is quarantined, never deleted. Inspected, those unique terms are
prose filler (`because`, `delivering`, `dynamically`) with the occasional real
identifier.

Everything else `triage` finds is **reported, not acted on**, because it needs a
human call:

- **stubs** — bodies under `min_body_bytes`
- **near-duplicates from *different* sources** — distinct lecture handouts on
  one topic land at 0.60–0.71, which is why the threshold is 0.72. Set it lower
  to see the overlap; do not let it collapse anything on its own
- **off-topic tags** — a supply-chain project legitimately cites supply-chain
  material, so pack fit is a judgement, not a rule

Only `--quarantine-redundant --execute` moves anything, and it writes a
manifest recording what was kept in each file's place.

## validate

The readiness gate, and the `ep-validate` gap this repo actually has. Exits
non-zero when the pack is not fit to index. Errors:

- frontmatter missing or unparseable, or a contract field absent
- `content_hash` disagreeing with the body — **the body was edited after
  stamping**, which is the failure most worth catching: nothing else notices
- duplicate `id` across the pack
- `pack:`/`type:` disagreeing with the directory holding the file
- a harvester refusal that reached the pack

Warnings (failures under `--strict`): stub bodies, and any source document
harvested more than once — which names `kp triage` as the fix, so the two
commands close each other's loop.

## Not done here

Nothing further. `stamp`, `triage`, and `validate` cover the contract, the
quality sweep, and the gate that used to be deferred upstream.
