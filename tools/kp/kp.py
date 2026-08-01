#!/usr/bin/env python3
"""kp - reorganize harvested knowledge packs from source-mirrored layout into
an ExpertPack composite organized by retrieval intent.

Subcommands
-----------
  index   Scan the harvested tree, parse frontmatter, attach a deterministic
          origin id and module classification. Read-only. -> inventory.jsonl
  plan    Turn an inventory into a reviewable migration mapping. Read-only.
          -> plan.csv
  apply   Execute a (possibly hand-edited) plan. Dry-run by default; writes a
          reversible manifest when run with --execute.
  revert  Undo an applied migration using its manifest.
  stamp   Write the ExpertPack frontmatter contract onto migrated files,
          joining each back to its plan row through the apply manifests.
  triage  Collapse re-harvests of the same source document and report the
          quality signals that need a human call.
  validate Check the pack is fit to index. Exits non-zero when it is not.
  queue   Emit a re-harvest script for source material that was never ingested.

Nothing mutates the tree unless `apply --execute` is passed.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("kp requires PyYAML (uv add pyyaml)")

HERE = Path(__file__).resolve().parent
DEFAULT_TAXONOMY = HERE / "taxonomy.yaml"

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
SLUG_STRIP = re.compile(r"[^a-z0-9]+")

# harvester.py:extract_text can read all of these, but they are NOT all worth
# harvesting. Sweeping code extensions across a project tree is what produced
# the original 841-file blowup: one .md summary per .py file, most of them
# vendored library internals. Documents are the default; code is opt-in via
# --include-code and should be pointed at a subsystem, not a whole degree.
DOC_EXT = {".pdf", ".ipynb"}
CODE_EXT = {".py", ".txt", ".md", ".csv", ".json"}
HARVESTABLE = DOC_EXT | CODE_EXT
# Directories that are never source material.
SOURCE_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    "site-packages", "dist-packages", ".ipynb_checkpoints", "build", "dist",
    ".idea", ".vscode", "lib", "lib64", "include", "Scripts",
}
MAX_SOURCE_BYTES = 8 * 1024 * 1024

SCHEMA_VERSION = 1
# Directory name -> the `type` a file living there carries. Post-migration the
# directory is authoritative: it is what `plan` already committed to, and it is
# what a retriever sees. Frontmatter that disagrees is stale, not a second opinion.
DIR_TYPE = {
    "concepts": "concept",
    "workflows": "workflow",
    "troubleshooting": "troubleshooting",
    "faq": "faq",
    "reference": "reference",
}
# The harvester occasionally emits the fence *language* without the fence, so
# the frontmatter opens on line 2 and every parser misses it. The content below
# is intact - this is a one-line repair, not a reharvest.
FENCE_JUNK = {"expert_pack", "yaml", "markdown", "md", "```yaml", "```"}
# ...and occasionally returns a refusal instead of a document. Those are not
# knowledge and must never be stamped as though they were.
REFUSAL_RE = re.compile(
    r"(i'm |i am )?unable to process|i cannot process|does not contain esoteric"
    r"|no esoteric knowledge|i'm sorry|as an ai language model",
    re.IGNORECASE,
)
MIN_STAMPABLE_BYTES = 600


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def load_taxonomy(path):
    with open(path, "r", encoding="utf-8") as fh:
        tax = yaml.safe_load(fh)
    # Longest match patterns first so "1_1_Practical_ML" wins over "Practical".
    for mod in tax["modules"]:
        mod["_match_lc"] = sorted(
            (m.lower() for m in mod["match"]), key=len, reverse=True
        )
    return tax


def slugify(text, maxlen=72):
    s = SLUG_STRIP.sub("-", (text or "").lower()).strip("-")
    if len(s) > maxlen:
        s = s[:maxlen].rsplit("-", 1)[0]
    return s or "untitled"


def parse_frontmatter(text):
    """Return (frontmatter_dict, body). Tolerates missing/!valid frontmatter."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, text[m.end():]


def sha256_body(body):
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def origin_id(rel_path):
    """Stable 12-hex identity for a file at its *pre-migration* location.

    This is what makes the move reversible and re-runnable: the id is derived
    from the origin path, so re-running `index` after a partial migration still
    joins rows to the same logical file via the manifest.
    """
    return hashlib.sha1(str(rel_path).encode("utf-8")).hexdigest()[:12]


def segments(rel_path):
    p = Path(rel_path)
    return list(p.parts[:-1]) + [p.stem]


def match_module(tax, rel_path):
    segs_lc = [s.lower() for s in segments(rel_path)]
    joined = "/".join(segs_lc)
    best = None
    best_len = -1
    for mod in tax["modules"]:
        for pat in mod["_match_lc"]:
            if pat in joined and len(pat) > best_len:
                best, best_len = mod, len(pat)
    return best


def has_segment(rel_path, names):
    segs_lc = {s.lower() for s in Path(rel_path).parts}
    return any(n.lower() in segs_lc for n in names)


# Vendored third-party source that got swept into the harvest (e.g. a checkout
# of imbalanced-learn under Practical_ML/Project2/Testing). Summaries of other
# people's libraries are pure general knowledge - exactly what EK triage exists
# to strip - so they are always quarantined.
VENDORED_RE = re.compile(
    r"(^|/)(site-packages|dist-packages|node_modules)(/|$)"
    r"|(^|/)[^/]+-(master|main)(/|$)"
    r"|(^|/)(imblearn|sklearn|numpy|pandas|scipy|torch|tensorflow|keras)(/|$)",
    re.IGNORECASE,
)


def is_vendored(rel_path):
    return bool(VENDORED_RE.search(str(rel_path).replace(os.sep, "/")))


def first_paragraph(body, limit=240):
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if chunk and not chunk.startswith("#"):
            return " ".join(chunk.split())[:limit]
    return ""


def norm_title(title):
    """Normalized key for duplicate detection."""
    t = (title or "").lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\b(the|a|an|of|for|with|in|to|and|using|from)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Drop harvester's numeric disambiguation suffixes ("... _3").
    t = re.sub(r"\s+\d+$", "", t)
    return t


# --------------------------------------------------------------------------
# content-type inference
# --------------------------------------------------------------------------

# NOTE: harvester.py's SYSTEM_PROMPT *mandates* an "## Anti-Patterns / Mistakes
# to Avoid" and a "## Frequently Asked" section in every file it generates. The
# mere presence of those headings therefore carries no signal at all - only the
# share of the body they occupy does. Classify on proportion, not presence.

SECTION_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)

FAQ_SECTION = re.compile(r"frequently asked|^faq\b|questions", re.IGNORECASE)
TROUBLE_SECTION = re.compile(
    r"anti-?pattern|mistake|troubleshoot|pitfall|error|failure|debug|gotcha",
    re.IGNORECASE)
WORKFLOW_SECTION = re.compile(
    r"\bsteps?\b|procedure|workflow|pipeline|walkthrough|how to|recipe",
    re.IGNORECASE)
REFERENCE_SECTION = re.compile(
    r"\bapi\b|parameters?|reference|signature|class |function |module ",
    re.IGNORECASE)

TITLE_FAQ = re.compile(r"^(why|what|when|which|who|how come)\b", re.IGNORECASE)
TITLE_WORKFLOW = re.compile(
    r"^(how to|building|creating|implementing|deploying|setting up)\b"
    r"|workflow|pipeline|end-to-end", re.IGNORECASE)
TITLE_TROUBLE = re.compile(
    r"troubleshoot|debugging|common (mistakes|errors|pitfalls)|fixing",
    re.IGNORECASE)

ORDERED_STEP = re.compile(r"^\s*(\d+[.)]\s|step\s*\d)", re.IGNORECASE | re.MULTILINE)


def section_shares(body):
    """Return {heading: fraction_of_body_chars} for each level-2 section."""
    marks = [(m.start(), m.group(1).strip()) for m in SECTION_RE.finditer(body)]
    if not marks:
        return {}
    total = max(len(body), 1)
    shares = {}
    for i, (start, heading) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        shares[heading] = (end - start) / total
    return shares


def _share_matching(shares, pattern):
    return sum(v for k, v in shares.items() if pattern.search(k))


def infer_type(fm, body, module, code_derived):
    """Deterministic type inference. Returns (type, confidence, reason)."""
    if code_derived:
        return "reference", "high", "code-derived per-module summary"
    if module and module.get("default_type"):
        return module["default_type"], "medium", "module default_type"

    title = (fm.get("title") or "").strip()
    shares = section_shares(body)
    faq_s = _share_matching(shares, FAQ_SECTION)
    trouble_s = _share_matching(shares, TROUBLE_SECTION)
    workflow_s = _share_matching(shares, WORKFLOW_SECTION)
    ref_s = _share_matching(shares, REFERENCE_SECTION)

    # Title is the strongest available signal: it is written by the model to
    # describe the whole file, whereas sections are boilerplate-contaminated.
    if TITLE_TROUBLE.search(title):
        return "troubleshooting", "high", "title indicates troubleshooting"
    if TITLE_WORKFLOW.search(title):
        return "workflow", "high", "title indicates workflow"
    if TITLE_FAQ.search(title) and title.endswith("?"):
        return "faq", "high", "title is a question"

    # Proportion thresholds: a file is an faq/troubleshooting/workflow atom only
    # when that material dominates, not when the boilerplate section exists.
    if faq_s >= 0.45:
        return "faq", "medium", f"q&a is {faq_s:.0%} of body"
    if trouble_s >= 0.45:
        return "troubleshooting", "medium", f"problem material is {trouble_s:.0%} of body"
    if workflow_s >= 0.40 and len(ORDERED_STEP.findall(body)) >= 4:
        return "workflow", "medium", f"ordered steps are {workflow_s:.0%} of body"
    if ref_s >= 0.50:
        return "reference", "medium", f"reference material is {ref_s:.0%} of body"

    declared = (fm.get("type") or "").strip().lower()
    if declared in {"concept", "workflow", "troubleshooting", "faq", "reference"}:
        return declared, "medium", "declared in frontmatter"
    return "concept", "low", "default"


# --------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------

def cmd_index(args):
    tax = load_taxonomy(args.taxonomy)
    root = Path(args.root).resolve()
    if not root.is_dir():
        sys.exit(f"kp index: {root} is not a directory")

    rows = []
    skipped = 0
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            print(f"[warn] unreadable, skipped: {rel} ({exc})", file=sys.stderr)
            skipped += 1
            continue

        fm, body = parse_frontmatter(text)
        module = match_module(tax, rel)
        navigation = has_segment(rel, tax["navigation_segments"])
        vendored = is_vendored(rel)
        keep_snap = has_segment(rel, [tax["keep_snapshot"]])
        quarantine = (has_segment(rel, tax["quarantine_segments"]) and not keep_snap) \
            or vendored
        code_derived = bool(module and module["pack"] == "msc-final-project") or quarantine

        ctype, conf, reason = infer_type(fm, body, module, code_derived)

        rows.append({
            "origin_id": origin_id(rel),
            "origin_rel": str(rel),
            "title": (fm.get("title") or path.stem.replace("_", " ")).strip(),
            "norm_title": norm_title(fm.get("title") or path.stem),
            "declared_type": fm.get("type") or "",
            "declared_pack": fm.get("pack") or "",
            "tags": fm.get("tags") if isinstance(fm.get("tags"), list) else [],
            "has_frontmatter": bool(fm),
            "body_chars": len(body),
            "content_hash": sha256_body(body),
            "excerpt": first_paragraph(body),
            "module_prefix": (module or {}).get("prefix", ""),
            "module_domain": (module or {}).get("domain", ""),
            "module_term": (module or {}).get("term") or "",
            "module_course": (module or {}).get("course") or "",
            "pack": (module or {}).get("pack", "ai-foundations"),
            "navigation": navigation,
            "quarantine": quarantine,
            "vendored": vendored,
            "keep_snapshot": keep_snap,
            "code_derived": code_derived,
            "inferred_type": ctype,
            "type_confidence": conf,
            "type_reason": reason,
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    unmatched = sum(1 for r in rows if not r["module_prefix"])
    print(f"indexed {len(rows)} files -> {out}")
    print(f"  no module match : {unmatched}")
    print(f"  navigation      : {sum(1 for r in rows if r['navigation'])}")
    print(f"  quarantine      : {sum(1 for r in rows if r['quarantine'])}")
    print(f"  code-derived    : {sum(1 for r in rows if r['code_derived'])}")
    print(f"  no frontmatter  : {sum(1 for r in rows if not r['has_frontmatter'])}")
    if skipped:
        print(f"  unreadable      : {skipped}")


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------

PLAN_FIELDS = [
    "action", "origin_id", "origin_rel", "pack", "dest_rel", "type",
    "retrieval_strategy", "domain", "term", "course", "tags",
    "merge_group", "merge_role", "confidence", "reason", "title",
]


def dest_for(row, ctype, pack):
    prefix = row["module_prefix"]
    stem = slugify(row["title"])
    if prefix and not stem.startswith(prefix + "-"):
        stem = f"{prefix}-{stem}"
    directory = {
        "concept": "concepts",
        "workflow": "workflows",
        "troubleshooting": "troubleshooting",
        "faq": "faq",
        "reference": "reference",
        "decision": "decisions",
    }.get(ctype, "concepts")
    return f"{pack}/{directory}/{stem}.md"


def cmd_plan(args):
    rows = [json.loads(line) for line in Path(args.inventory).read_text(
        encoding="utf-8").splitlines() if line.strip()]

    # --- duplicate grouping (title-normalized; filenames are unreliable
    # because the harvester names files from LLM-generated titles, so the same
    # source harvested twice yields two different filenames).
    groups = defaultdict(list)
    for r in rows:
        if r["navigation"] or r["quarantine"]:
            continue
        key = (r["module_domain"], r["norm_title"])
        if r["norm_title"]:
            groups[key].append(r)

    merge_of = {}
    for key, members in groups.items():
        if len(members) < 2:
            continue
        gid = "m-" + hashlib.sha1("|".join(key).encode()).hexdigest()[:8]
        # Primary = longest body; the rest are candidates to fold in.
        members.sort(key=lambda r: r["body_chars"], reverse=True)
        for i, r in enumerate(members):
            merge_of[r["origin_id"]] = (gid, "primary" if i == 0 else "duplicate")

    plan = []
    for r in rows:
        gid, role = merge_of.get(r["origin_id"], ("", ""))

        if r["navigation"]:
            plan.append(dict(
                action="navigation", origin_id=r["origin_id"],
                origin_rel=r["origin_rel"], pack="coursework-archive",
                dest_rel=f"coursework-archive/meta/{slugify(r['title'])}.md",
                type="meta", retrieval_strategy="navigation",
                domain=r["module_domain"], term=r["module_term"],
                course=r["module_course"], tags=";".join(r["tags"]),
                merge_group="", merge_role="", confidence="high",
                reason="administrative material", title=r["title"]))
            continue

        if r["quarantine"]:
            plan.append(dict(
                action="quarantine", origin_id=r["origin_id"],
                origin_rel=r["origin_rel"], pack="",
                dest_rel=f"_quarantine/{r['origin_rel']}",
                type="", retrieval_strategy="",
                domain=r["module_domain"], term=r["module_term"],
                course=r["module_course"], tags=";".join(r["tags"]),
                merge_group="", merge_role="", confidence="high",
                reason="redundant codebase snapshot (decision 2a)",
                title=r["title"]))
            continue

        ctype = r["inferred_type"]
        pack = r["pack"]
        strategy = "reference" if ctype == "reference" else (
            "atomic" if r["body_chars"] > 6000 else "standard")

        tags = list(r["tags"])
        for extra in (f"domain:{r['module_domain']}" if r["module_domain"] else None,
                      f"term:{r['module_term']}" if r["module_term"] else None,
                      f"course:{r['module_course'].lower()}" if r["module_course"] else None):
            if extra and extra not in tags:
                tags.append(extra)

        action = "merge" if role == "duplicate" else "move"
        plan.append(dict(
            action=action, origin_id=r["origin_id"], origin_rel=r["origin_rel"],
            pack=pack, dest_rel=dest_for(r, ctype, pack), type=ctype,
            retrieval_strategy=strategy, domain=r["module_domain"],
            term=r["module_term"], course=r["module_course"],
            tags=";".join(tags), merge_group=gid, merge_role=role,
            confidence=r["type_confidence"], reason=r["type_reason"],
            title=r["title"]))

    # --- collision resolution on destination paths -------------------------
    seen = defaultdict(int)
    for p in plan:
        if p["action"] not in ("move", "navigation"):
            continue
        base = p["dest_rel"]
        seen[base] += 1
        if seen[base] > 1:
            stem, ext = base.rsplit(".", 1)
            p["dest_rel"] = f"{stem}-{seen[base]}.{ext}"
            p["reason"] = (p["reason"] + "; dest collision renamed").strip("; ")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PLAN_FIELDS)
        w.writeheader()
        for p in sorted(plan, key=lambda p: (p["action"], p["dest_rel"])):
            w.writerow(p)

    counts = defaultdict(int)
    for p in plan:
        counts[p["action"]] += 1
    print(f"planned {len(plan)} rows -> {out}")
    for action in sorted(counts):
        print(f"  {action:<12}: {counts[action]}")
    low = sum(1 for p in plan if p["confidence"] == "low" and p["action"] == "move")
    print(f"  low-confidence type calls needing review: {low}")


# --------------------------------------------------------------------------
# apply / revert
# --------------------------------------------------------------------------

def cmd_apply(args):
    root = Path(args.root).resolve()
    plan = list(csv.DictReader(Path(args.plan).open(encoding="utf-8")))
    if not plan:
        sys.exit("kp apply: empty plan")

    moves, problems = [], []
    for p in plan:
        if p["action"] == "merge" and not args.merge_duplicates:
            continue  # duplicates are left in place until reviewed
        src = root / p["origin_rel"]
        dst = root / p["dest_rel"]
        if not src.exists():
            problems.append(f"missing source: {p['origin_rel']}")
            continue
        if dst.exists():
            problems.append(f"destination exists: {p['dest_rel']}")
            continue
        moves.append((src, dst, p))

    print(f"{len(moves)} moves ready, {len(problems)} problems")
    for prob in problems[:20]:
        print(f"  [problem] {prob}")
    if len(problems) > 20:
        print(f"  ... and {len(problems) - 20} more")

    if not args.execute:
        for src, dst, p in moves[:15]:
            print(f"  {p['action']:<10} {p['origin_rel']}  ->  {p['dest_rel']}")
        if len(moves) > 15:
            print(f"  ... and {len(moves) - 15} more")
        print("\ndry run - nothing moved. re-run with --execute to apply.")
        return
    if problems and not args.force:
        sys.exit("refusing to apply with unresolved problems (use --force)")

    manifest = {"root": str(root), "moves": []}
    for src, dst, p in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        manifest["moves"].append({
            "origin_id": p["origin_id"],
            "from": p["origin_rel"],
            "to": p["dest_rel"],
        })

    mpath = Path(args.manifest)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"moved {len(manifest['moves'])} files; manifest -> {mpath}")


def cmd_revert(args):
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    root = Path(args.root or manifest["root"]).resolve()
    restored = 0
    for mv in reversed(manifest["moves"]):
        src, dst = root / mv["to"], root / mv["from"]
        if not src.exists():
            print(f"[warn] gone, cannot restore: {mv['to']}", file=sys.stderr)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        restored += 1
    print(f"restored {restored}/{len(manifest['moves'])} files")


# --------------------------------------------------------------------------
# stamp
# --------------------------------------------------------------------------
def repair_fence(text):
    """Strip a stray leading fence-language line so frontmatter parses.

    Returns (text, repaired). Also re-opens frontmatter that lost its `---`
    delimiter along with the fence.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip().lower() not in FENCE_JUNK:
        return text, False
    rest = "\n".join(lines[1:])
    if not rest.lstrip().startswith("---"):
        # the opening delimiter went with the fence; the closing one survives
        head = rest.split("\n", 30)[:30]
        if any(l.strip() == "---" for l in head) and re.match(r"^[a-zA-Z_]+:", rest.lstrip()):
            rest = "---\n" + rest.lstrip()
    return rest, True


def is_refusal(body):
    """True when the harvester returned an apology instead of a document."""
    head = body.strip()[:400]
    return bool(head) and len(body.strip()) < MIN_STAMPABLE_BYTES and bool(REFUSAL_RE.search(head))


def load_origin_map(manifest_paths):
    """current pack-relative path -> pre-migration origin_rel.

    Manifests are applied in order, so a file moved twice (migrated, then
    renamed by a later pass) still resolves back to its original identity.
    """
    origin = {}
    for mp in manifest_paths:
        data = json.loads(Path(mp).read_text(encoding="utf-8"))
        for mv in data.get("moves", []):
            origin[mv["to"]] = origin.get(mv["from"], mv["from"])
    return origin


def split_tags(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in re.split(r"[;,]", str(raw)) if t.strip()]


def build_frontmatter(rel, fm, body, row, verified_at):
    """Compose the contract block for one file. Directory wins over stale fm."""
    parts = Path(rel).parts
    pack = parts[0]
    ctype = DIR_TYPE.get(parts[1] if len(parts) > 2 else "", None) \
        or (row or {}).get("type") or fm.get("type") or "concept"

    title = fm.get("title") or (row or {}).get("title") or \
        Path(rel).stem.replace("-", " ").title()

    tags = split_tags(fm.get("tags")) + split_tags((row or {}).get("tags"))
    seen, merged = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            merged.append(t)

    src = (row or {}).get("origin_rel")
    out = {
        "id": origin_id(src) if src else origin_id(rel),
        "schema_version": SCHEMA_VERSION,
        "title": str(title),
        "type": ctype,
        "pack": pack,
        "retrieval_strategy": (row or {}).get("retrieval_strategy") or "standard",
    }
    for key in ("domain", "term", "course"):
        val = (row or {}).get(key)
        if val:
            out[key] = val
    if merged:
        out["tags"] = merged
    if src:
        out["source"] = src
    out["verified_at"] = verified_at
    out["content_hash"] = sha256_body(body)

    # keep anything the harvester added that the contract does not describe
    for k, v in fm.items():
        if k not in out and k not in ("tags", "title", "type", "pack"):
            out[k] = v
    return out


def cmd_stamp(args):
    root = Path(args.root).resolve()
    verified_at = args.verified_at or __import__("datetime").date.today().isoformat()

    rows = {}
    if args.plan:
        for r in csv.DictReader(Path(args.plan).open(encoding="utf-8")):
            rows[r["origin_rel"]] = r
    origin = load_origin_map(args.manifest or [])

    packs = args.pack or ["ai-foundations", "msc-final-project", "coursework-archive"]
    targets = sorted(p for pk in packs for p in (root / pk).rglob("*.md"))

    stamped, repaired, refusals, unjoined = [], [], [], []
    for path in targets:
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="replace")
        text, was_repaired = repair_fence(text)
        fm, body = parse_frontmatter(text)
        body = body.strip() + "\n"

        if not fm and is_refusal(body):
            refusals.append(rel)
            continue

        src = origin.get(rel)
        row = rows.get(src) if src else None
        if row is None:
            unjoined.append(rel)

        block = build_frontmatter(rel, fm, body, row, verified_at)
        rendered = "---\n" + yaml.safe_dump(
            block, sort_keys=False, allow_unicode=True, default_flow_style=None
        ) + "---\n\n" + body
        stamped.append((path, rendered))
        if was_repaired:
            repaired.append(rel)

    print(f"{len(stamped)} files to stamp "
          f"({len(repaired)} fence-repaired, {len(unjoined)} without a plan row)")
    if refusals:
        print(f"{len(refusals)} harvester refusals - NOT stamped:")
        for r in refusals:
            print(f"  [refusal] {r}")
        if args.quarantine_refusals:
            print("  -> will be moved to _quarantine/ (--quarantine-refusals)")

    if not args.execute:
        for path, rendered in stamped[:5]:
            print(f"\n--- {path.relative_to(root)}")
            print("\n".join(rendered.split("\n")[:14]))
        print(f"\ndry run - nothing written. re-run with --execute to apply.")
        return

    for path, rendered in stamped:
        path.write_text(rendered, encoding="utf-8")

    moved = []
    if args.quarantine_refusals:
        for rel in refusals:
            dst = root / "_quarantine" / "_harvest_refusals" / Path(rel).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(root / rel), str(dst))
            moved.append({"from": rel, "to": str(dst.relative_to(root))})

    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps({
            "root": str(root), "verified_at": verified_at,
            "stamped": [str(p.relative_to(root)) for p, _ in stamped],
            "fence_repaired": repaired, "unjoined": unjoined,
            "refusals": refusals, "quarantined": moved,
        }, indent=2), encoding="utf-8")
        print(f"report -> {rp}")
    print(f"stamped {len(stamped)} files"
          + (f"; quarantined {len(moved)} refusals" if moved else ""))


# --------------------------------------------------------------------------
# triage / validate
# --------------------------------------------------------------------------
CONTRACT_FIELDS = ("id", "schema_version", "title", "type", "pack",
                   "retrieval_strategy", "verified_at", "content_hash")
DEFAULT_PACKS = ("ai-foundations", "msc-final-project", "coursework-archive")
WORD_RE = re.compile(r"[a-z_][a-z0-9_]{3,}")


def iter_pack_files(root, packs):
    """Yield (path, frontmatter, body) for every live file in the given packs."""
    for pk in packs:
        d = root / pk
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            fm, body = parse_frontmatter(text)
            yield p, fm, body


def source_key(fm):
    """Group key for 'these are harvests of the same source document'.

    The basename, not the full path: the same lecture PDF is mirrored into
    several origin trees (lectures/, organized_lectures/, AI_Master-2023/), so
    full paths differ while the underlying document is one and the same. That
    is precisely the case the full path fails to catch.
    """
    src = fm.get("source") or ""
    return Path(src).name.lower() or None


def redundant_harvests(docs):
    """[(kept, [redundant...])] for source documents harvested more than once.

    The richest harvest wins. Two passes over one document differ in how much
    they retained, never in what they were about, so length is the signal -
    ties broken on path for determinism.
    """
    groups = defaultdict(list)
    for p, fm, body in docs:
        key = source_key(fm)
        if key:
            groups[key].append((len(body), str(p), p, fm))
    out = []
    for key, variants in sorted(groups.items()):
        if len(variants) < 2:
            continue
        variants.sort(key=lambda v: (-v[0], v[1]))
        out.append((key, variants[0], variants[1:]))
    return out


def near_duplicate_pairs(docs, ratio):
    """Similar bodies from *different* source documents. Reported, not acted on.

    Jaccard over token sets first - it is O(1) set math and discards almost
    every pair - then the expensive sequence ratio only on what survives.
    """
    import difflib
    prepped = []
    for p, fm, body in docs:
        flat = re.sub(r"\s+", " ", body).strip().lower()
        prepped.append((p, flat, set(WORD_RE.findall(flat)), source_key(fm)))
    pairs = []
    for i in range(len(prepped)):
        pi, fi, ti, si = prepped[i]
        for j in range(i + 1, len(prepped)):
            pj, fj, tj, sj = prepped[j]
            if si and si == sj:
                continue  # same document: redundant_harvests owns this case
            union = ti | tj
            if not union or len(ti & tj) / len(union) < ratio * 0.8:
                continue
            r = difflib.SequenceMatcher(None, fi, fj).ratio()
            if r >= ratio:
                pairs.append((round(r, 3), str(pi), str(pj)))
    pairs.sort(reverse=True)
    return pairs


def cmd_triage(args):
    tax = load_taxonomy(args.taxonomy)
    q = tax.get("quality", {}) or {}
    root = Path(args.root).resolve()
    packs = args.pack or list(DEFAULT_PACKS)
    min_bytes = args.min_body_bytes or q.get("min_body_bytes", 1200)
    ratio = args.near_duplicate_ratio or q.get("near_duplicate_ratio", 0.72)
    off_tags = {t.lower() for t in (q.get("off_topic_tags") or [])}

    docs = list(iter_pack_files(root, packs))
    print(f"{len(docs)} live files in {', '.join(packs)}\n")

    groups = redundant_harvests(docs)
    moves = []
    for key, keep, losers in groups:
        for n, _s, p, _fm in losers:
            moves.append({
                "reason": "redundant re-harvest",
                "from": str(p.relative_to(root)),
                "to": str(Path("_quarantine/_redundant_harvests") / p.name),
                "source_document": key,
                "bytes": n,
                "kept_instead": str(keep[2].relative_to(root)),
                "kept_bytes": keep[0],
            })

    stubs = [(p, len(b)) for p, _f, b in docs if len(b.strip()) < min_bytes]
    off_topic = []
    for p, fm, _b in docs:
        hits = [str(t) for t in (fm.get("tags") or [])
                if str(t).lower().split(":")[-1] in off_tags]
        if hits:
            off_topic.append((p, hits))

    print(f"redundant re-harvests : {len(moves)} "
          f"(from {len(groups)} source documents harvested more than once)")
    for m in moves:
        print(f"  {m['bytes']:>6}B {Path(m['from']).name[:52]:<52}"
              f" -> keeping {m['kept_bytes']}B {Path(m['kept_instead']).name[:34]}")

    print(f"\nstubs (<{min_bytes}B)      : {len(stubs)}")
    for p, n in stubs:
        print(f"  {n:>6}B {p.relative_to(root)}")

    print(f"\noff-topic tags        : {len(off_topic)}  (reported only)")
    for p, hits in off_topic:
        print(f"  {str(p.relative_to(root))[:66]:<66} {hits}")

    pairs = []
    if not args.no_near_duplicates:
        pairs = near_duplicate_pairs(docs, ratio)
        print(f"\nnear-duplicates >={ratio} : {len(pairs)}  (reported only - "
              f"distinct sources)")
        for r, a, b in pairs[:25]:
            print(f"  {r:.2f}  {Path(a).name[:48]}\n        {Path(b).name[:48]}")

    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps({
            "root": str(root), "packs": packs,
            "redundant": moves,
            "stubs": [{"file": str(p.relative_to(root)), "bytes": n} for p, n in stubs],
            "off_topic": [{"file": str(p.relative_to(root)), "tags": h}
                          for p, h in off_topic],
            "near_duplicates": [{"ratio": r, "a": a, "b": b} for r, a, b in pairs],
        }, indent=2), encoding="utf-8")
        print(f"\nreport -> {rp}")

    if not args.quarantine_redundant:
        print("\nnothing moved. pass --quarantine-redundant --execute to collapse "
              "redundant re-harvests.")
        return
    if not args.execute:
        print("\ndry run - nothing moved. add --execute to apply.")
        return

    manifest = {"root": str(root), "moves": []}
    for m in moves:
        src, dst = root / m["from"], root / m["to"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        manifest["moves"].append(m)
    mpath = Path(args.manifest)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nquarantined {len(manifest['moves'])} redundant harvests; "
          f"manifest -> {mpath}")


def cmd_validate(args):
    """Readiness gate. Exits non-zero when the pack is not fit to index."""
    tax = load_taxonomy(args.taxonomy)
    q = tax.get("quality", {}) or {}
    dir_types = tax.get("dir_types", {}) or {}
    root = Path(args.root).resolve()
    packs = args.pack or list(DEFAULT_PACKS)
    min_bytes = args.min_body_bytes or q.get("min_body_bytes", 1200)

    docs = list(iter_pack_files(root, packs))
    errors, warnings = [], []
    seen_ids, seen_sources = {}, defaultdict(list)

    for p, fm, body in docs:
        rel = str(p.relative_to(root))
        if not fm:
            errors.append((rel, "no parseable frontmatter"))
            continue
        for field in CONTRACT_FIELDS:
            if field not in fm:
                errors.append((rel, f"missing contract field: {field}"))
        if fm.get("schema_version") != SCHEMA_VERSION:
            errors.append((rel, f"schema_version {fm.get('schema_version')!r} "
                                f"!= {SCHEMA_VERSION}"))
        expected = sha256_body(body)
        if fm.get("content_hash") and fm["content_hash"] != expected:
            errors.append((rel, "content_hash stale - body edited since stamp; "
                                "re-run `kp stamp`"))
        fid = fm.get("id")
        if fid:
            if fid in seen_ids:
                errors.append((rel, f"duplicate id {fid} (also {seen_ids[fid]})"))
            seen_ids[fid] = rel

        parts = p.relative_to(root).parts
        if fm.get("pack") != parts[0]:
            errors.append((rel, f"pack {fm.get('pack')!r} != directory {parts[0]!r}"))
        if len(parts) > 2:
            want = dir_types.get(parts[1])
            if want and fm.get("type") != want:
                errors.append((rel, f"type {fm.get('type')!r} != {want!r} "
                                    f"for directory {parts[1]!r}"))
        if is_refusal(body):
            errors.append((rel, "harvester refusal, not a document"))
        if len(body.strip()) < min_bytes:
            warnings.append((rel, f"stub: {len(body.strip())}B < {min_bytes}B"))
        key = source_key(fm)
        if key:
            seen_sources[key].append(rel)

    for key, files in sorted(seen_sources.items()):
        if len(files) > 1:
            warnings.append((files[1], f"source {key!r} harvested {len(files)}x; "
                                       "run `kp triage --quarantine-redundant`"))

    for rel, msg in errors:
        print(f"  ERROR  {rel}: {msg}")
    for rel, msg in warnings:
        print(f"  warn   {rel}: {msg}")
    print(f"\n{len(docs)} files | {len(errors)} errors | {len(warnings)} warnings")

    if errors or (args.strict and warnings):
        sys.exit(1)
    print("pack is ready to index")


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------

def cmd_queue(args):
    tax = load_taxonomy(args.taxonomy)
    source = Path(args.source).resolve()
    # Administrative folders are navigation-tier: kept in the vault but never
    # worth spending harvest tokens on, so they are skipped here too.
    out_of_scope = {s.lower() for s in tax["out_of_scope"]}
    out_of_scope |= {s.lower() for s in tax["navigation_segments"]}

    harvested_stems = set()
    if args.inventory:
        for line in Path(args.inventory).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                harvested_stems.add(slugify(Path(r["origin_rel"]).stem))
                harvested_stems.add(slugify(r["title"]))

    queued, skipped_big, already = [], 0, 0
    for dirpath, dirnames, filenames in os.walk(source):
        rel_dir = Path(dirpath).relative_to(source)
        dirnames[:] = [
            d for d in dirnames
            if d not in SOURCE_SKIP_DIRS
            and not d.startswith(".")
            and d.lower() not in out_of_scope
            and "venv" not in d.lower()
        ]
        # Never re-harvest vendored third-party source or the redundant
        # codebase snapshots that decision 2a quarantines.
        if is_vendored(rel_dir) or has_segment(rel_dir, tax["quarantine_segments"]):
            dirnames[:] = []
            continue

        wanted = DOC_EXT | (CODE_EXT if args.include_code else set())
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext not in wanted:
                continue
            fpath = Path(dirpath) / name
            try:
                if fpath.stat().st_size > MAX_SOURCE_BYTES:
                    skipped_big += 1
                    continue
            except OSError:
                continue
            if slugify(fpath.stem) in harvested_stems:
                already += 1
                continue
            module = match_module(tax, rel_dir / name)
            prefix = (module or {}).get("prefix", "misc")
            pack = (module or {}).get("pack", "ai-foundations")
            dest = Path(args.dest) / pack / "_inbox" / f"{prefix}-{slugify(fpath.stem)}.md"
            queued.append((fpath, dest, ext))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write("#!/bin/bash\n")
        fh.write("# Generated by kp queue. Re-harvest of un-ingested source material.\n")
        fh.write("# Calls harvester.py directly so .ipynb/.py are included -\n")
        fh.write("# agent_batch_harvest.py filters to .pdf only and would skip them.\n")
        fh.write("set -u\n\n")
        for fpath, dest, ext in sorted(queued, key=lambda t: str(t[0])):
            vision = " --vision" if ext == ".pdf" and args.vision else ""
            fh.write(f'uv run scripts/harvester.py "{fpath}" \\\n')
            fh.write(f'  --output "{dest}" --auto{vision} || echo "FAILED: {fpath}"\n')
    out.chmod(0o755)

    by_ext = defaultdict(int)
    for _, _, ext in queued:
        by_ext[ext] += 1
    print(f"queued {len(queued)} source files -> {out}")
    for ext in sorted(by_ext, key=lambda e: -by_ext[e]):
        print(f"  {ext:<8}: {by_ext[ext]}")
    print(f"  skipped (already harvested): {already}")
    print(f"  skipped (>8MB)             : {skipped_big}")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(prog="kp", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="scan harvested tree -> inventory.jsonl")
    p.add_argument("--root", required=True)
    p.add_argument("--out", default="build/inventory.jsonl")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("plan", help="inventory -> reviewable plan.csv")
    p.add_argument("--inventory", default="build/inventory.jsonl")
    p.add_argument("--out", default="build/plan.csv")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="execute a plan (dry-run by default)")
    p.add_argument("--root", required=True)
    p.add_argument("--plan", default="build/plan.csv")
    p.add_argument("--manifest", default="build/migration-manifest.json")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--merge-duplicates", action="store_true",
                   help="also move rows marked action=merge")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("revert", help="undo an applied migration")
    p.add_argument("--manifest", default="build/migration-manifest.json")
    p.add_argument("--root")
    p.set_defaults(func=cmd_revert)

    p = sub.add_parser("stamp", help="write the ExpertPack frontmatter contract")
    p.add_argument("--root", required=True)
    p.add_argument("--plan", default="build/plan.csv")
    p.add_argument("--manifest", action="append",
                   help="apply manifest, repeatable and order-sensitive; later "
                        "passes resolve through earlier ones")
    p.add_argument("--pack", action="append",
                   help="pack directory to stamp (default: the three content packs)")
    p.add_argument("--verified-at", help="ISO date to record (default: today)")
    p.add_argument("--report", default="build/stamp-report.json")
    p.add_argument("--quarantine-refusals", action="store_true",
                   help="move harvester refusals out of the pack")
    p.add_argument("--execute", action="store_true")
    p.set_defaults(func=cmd_stamp)

    p = sub.add_parser("triage", help="collapse redundant harvests, report quality")
    p.add_argument("--root", required=True)
    p.add_argument("--pack", action="append")
    p.add_argument("--min-body-bytes", type=int)
    p.add_argument("--near-duplicate-ratio", type=float)
    p.add_argument("--no-near-duplicates", action="store_true",
                   help="skip the pairwise similarity sweep")
    p.add_argument("--quarantine-redundant", action="store_true",
                   help="collapse same-source re-harvests, keeping the richest")
    p.add_argument("--manifest", default="build/triage-manifest.json")
    p.add_argument("--report", default="build/triage-report.json")
    p.add_argument("--execute", action="store_true")
    p.set_defaults(func=cmd_triage)

    p = sub.add_parser("validate", help="check the pack is fit to index")
    p.add_argument("--root", required=True)
    p.add_argument("--pack", action="append")
    p.add_argument("--min-body-bytes", type=int)
    p.add_argument("--strict", action="store_true",
                   help="treat warnings as failures")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("queue", help="emit re-harvest script for missing source")
    p.add_argument("--source", required=True)
    p.add_argument("--inventory", default="build/inventory.jsonl")
    p.add_argument("--dest", default="knowledge_packs")
    p.add_argument("--out", default="build/reharvest.sh")
    p.add_argument("--vision", action="store_true")
    p.add_argument("--include-code", action="store_true",
                   help="also queue .py/.md/.txt/.csv/.json (use on a subsystem, not a tree)")
    p.set_defaults(func=cmd_queue)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
