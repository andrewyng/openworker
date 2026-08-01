#!/usr/bin/env python3
"""Smoke tests for kp. Run: python3 tools/kp/test_kp.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KP = HERE / "kp.py"

sys.path.insert(0, str(HERE))
import kp  # noqa: E402

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


BOILERPLATE = """
Retrieval-augmented generation grounds a model's output in retrieved documents.

## Code Examples / Practical Implementation
```python
retriever.search(q)
```

## Anti-Patterns / Mistakes to Avoid
- Do not chunk across section boundaries.

## Frequently Asked
### Why use RAG?
Because.
"""

FAQ_HEAVY = """
Short intro.

## Frequently Asked
### Why does my sim explode?
Because the timestep is too large and the solver diverges. Reduce it.
### Why is the render noisy?
Too few samples. Raise them, or denoise.
### Why is my mesh black?
Flipped normals. Recalculate them outside.
### Why is the sim slow?
Too high a resolution for the domain.
"""


def test_type_inference():
    print("type inference")
    # The mandated boilerplate sections must NOT flip a concept file.
    t, conf, _ = kp.infer_type({"title": "Retrieval Augmented Generation"},
                               BOILERPLATE, None, False)
    check("boilerplate anti-pattern/faq sections stay concept", t == "concept",
          f"got {t}")

    t, _, _ = kp.infer_type({"title": "Blender FAQ"}, FAQ_HEAVY, None, False)
    check("q&a-dominated body -> faq", t == "faq", f"got {t}")

    t, conf, _ = kp.infer_type({"title": "How to Fine-Tune a Transformer"},
                               BOILERPLATE, None, False)
    check("title-driven workflow", t == "workflow" and conf == "high", f"got {t}")

    t, _, _ = kp.infer_type({"title": "anything"}, BOILERPLATE, None, True)
    check("code-derived -> reference", t == "reference", f"got {t}")


def test_helpers():
    print("helpers")
    check("vendored: site-packages",
          kp.is_vendored("a/site-packages/x.md"))
    check("vendored: -master checkout",
          kp.is_vendored("Project2/Testing/imbalanced-learn-master/imblearn/x.md"))
    check("not vendored: ordinary lecture path",
          not kp.is_vendored("1_2_NLP/embeddings/word2vec.md"))
    check("origin_id stable",
          kp.origin_id("a/b.md") == kp.origin_id("a/b.md"))
    check("origin_id distinct",
          kp.origin_id("a/b.md") != kp.origin_id("a/c.md"))
    check("norm_title drops harvester numeric suffix",
          kp.norm_title("Logistic Regression 3") == kp.norm_title("Logistic Regression"))
    check("slugify", kp.slugify("Why is my Render *Noisy*?") == "why-is-my-render-noisy")


def test_roundtrip():
    """index -> plan -> apply --execute -> revert restores the tree exactly."""
    print("round trip")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        root = td / "kp_root"
        (root / "1_2_NLP" / "embeddings").mkdir(parents=True)
        (root / "1_2_NLP" / "embeddings" / "word2vec.md").write_text(
            '---\ntitle: "Word2Vec Embeddings"\ntype: "concept"\n'
            'tags: ["domain:nlp"]\npack: "ai-masters"\n---\n' + BOILERPLATE,
            encoding="utf-8")
        proj = root / "2_2_AI_Msc_Final_Project" / "Draft"
        proj.mkdir(parents=True)
        (proj / "helper.md").write_text(
            '---\ntitle: "Helper Utilities"\ntype: "concept"\n'
            'tags: []\npack: "ai-masters"\n---\nbody\n', encoding="utf-8")

        before = {p.relative_to(root): p.read_text(encoding="utf-8")
                  for p in root.rglob("*.md")}

        inv, plan, man = td / "inv.jsonl", td / "plan.csv", td / "man.json"
        for args in (
            ["index", "--root", str(root), "--out", str(inv)],
            ["plan", "--inventory", str(inv), "--out", str(plan)],
            ["apply", "--root", str(root), "--plan", str(plan),
             "--manifest", str(man), "--execute"],
        ):
            r = subprocess.run([sys.executable, str(KP)] + args,
                               capture_output=True, text=True)
            if r.returncode != 0:
                check(f"{args[0]} succeeded", False, r.stderr[-400:])
                return

        rows = [json.loads(l) for l in inv.read_text().splitlines() if l.strip()]
        check("indexed both files", len(rows) == 2, f"got {len(rows)}")
        check("Draft file quarantined",
              any(r["quarantine"] for r in rows if "helper" in r["origin_rel"]))
        check("concept moved out of source-mirrored path",
              (root / "ai-foundations" / "concepts" / "nlp-word2vec-embeddings.md").exists(),
              sorted(str(p.relative_to(root)) for p in root.rglob("*.md")))
        check("quarantine preserves origin path",
              (root / "_quarantine" / "2_2_AI_Msc_Final_Project" / "Draft" / "helper.md").exists())

        r = subprocess.run([sys.executable, str(KP), "revert",
                            "--manifest", str(man), "--root", str(root)],
                           capture_output=True, text=True)
        check("revert succeeded", r.returncode == 0, r.stderr[-300:])
        after = {p.relative_to(root): p.read_text(encoding="utf-8")
                 for p in root.rglob("*.md")}
        check("revert restores tree byte-for-byte", before == after,
              f"{sorted(map(str, before))} != {sorted(map(str, after))}")


def test_stamp_helpers():
    print("stamp helpers")
    t, rep = kp.repair_fence('expert_pack\n---\ntitle: "X"\n---\n\nbody\n')
    check("strips stray fence-language line", rep and t.startswith("---"))
    fm, _ = kp.parse_frontmatter(t)
    check("repaired frontmatter parses", fm.get("title") == "X", fm)

    t2, rep2 = kp.repair_fence('yaml\ntitle: "Y"\ntype: "concept"\n---\n\nbody\n')
    check("re-opens frontmatter that lost its delimiter", rep2)
    fm2, _ = kp.parse_frontmatter(t2)
    check("delimiter-less frontmatter parses", fm2.get("title") == "Y", fm2)

    t3, rep3 = kp.repair_fence('---\ntitle: "Z"\n---\n\nbody\n')
    check("leaves well-formed files alone", not rep3 and t3.startswith("---"))

    check("refusal detected",
          kp.is_refusal("I'm unable to process images directly. Provide text."))
    check("long technical body is not a refusal",
          not kp.is_refusal("Gradient descent minimises a loss. " * 40))
    check("refusal wording inside a real document is not a refusal",
          not kp.is_refusal("I'm sorry is a common test string. " * 40))

    # later manifests resolve through earlier ones
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "a.json", Path(td) / "b.json"
        a.write_text(json.dumps({"moves": [{"from": "src/x.md", "to": "pack/x.md"}]}))
        b.write_text(json.dumps({"moves": [{"from": "pack/x.md", "to": "pack/x-2.md"}]}))
        omap = kp.load_origin_map([a, b])
        check("two-hop move resolves to original origin",
              omap["pack/x-2.md"] == "src/x.md", omap)


def test_stamp_roundtrip():
    print("stamp roundtrip")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "knowledge_packs"
        d = root / "ai-foundations" / "concepts"
        d.mkdir(parents=True)
        (d / "nlp-topic.md").write_text(
            'expert_pack\n---\ntitle: "Topic"\ntype: "concept"\n'
            'tags: ["domain:nlp"]\npack: "ai-masters"\n---\n\nReal content here.\n',
            encoding="utf-8")
        (d / "nlp-refused.md").write_text(
            "I'm unable to process images directly.\n", encoding="utf-8")

        plan = Path(td) / "plan.csv"
        plan.write_text(
            "action,origin_id,origin_rel,pack,dest_rel,type,retrieval_strategy,"
            "domain,term,course,tags,merge_group,merge_role,confidence,reason,title\n"
            "move,abc,src/topic.md,ai-foundations,ai-foundations/concepts/nlp-topic.md,"
            "concept,atomic,nlp,1-2,COMP9999,domain:nlp;level:basic,,,high,x,Topic\n",
            encoding="utf-8")
        man = Path(td) / "m.json"
        man.write_text(json.dumps({"moves": [
            {"from": "src/topic.md", "to": "ai-foundations/concepts/nlp-topic.md"}]}))
        report = Path(td) / "report.json"

        r = subprocess.run([sys.executable, str(KP), "stamp", "--root", str(root),
                            "--plan", str(plan), "--manifest", str(man),
                            "--report", str(report), "--verified-at", "2026-01-01",
                            "--quarantine-refusals", "--execute"],
                           capture_output=True, text=True)
        check("stamp succeeded", r.returncode == 0, r.stderr[-400:])

        fm, body = kp.parse_frontmatter((d / "nlp-topic.md").read_text(encoding="utf-8"))
        for field in ("id", "schema_version", "retrieval_strategy",
                      "verified_at", "content_hash"):
            check(f"contract field {field} written", field in fm, fm)
        check("id derives from the origin path, not the destination",
              fm["id"] == kp.origin_id("src/topic.md"), fm.get("id"))
        check("stale pack corrected to the owning directory",
              fm["pack"] == "ai-foundations", fm.get("pack"))
        check("plan provenance merged in",
              fm.get("course") == "COMP9999" and fm.get("retrieval_strategy") == "atomic", fm)
        check("harvester tags preserved alongside plan tags",
              "domain:nlp" in fm["tags"] and "level:basic" in fm["tags"], fm.get("tags"))
        check("content_hash matches the body it ships with",
              fm["content_hash"] == kp.sha256_body(body), fm.get("content_hash"))
        check("refusal quarantined out of the pack",
              not (d / "nlp-refused.md").exists()
              and (root / "_quarantine" / "_harvest_refusals" / "nlp-refused.md").exists())

        first = (d / "nlp-topic.md").read_text(encoding="utf-8")
        subprocess.run([sys.executable, str(KP), "stamp", "--root", str(root),
                        "--plan", str(plan), "--manifest", str(man),
                        "--report", str(report), "--verified-at", "2026-01-01",
                        "--execute"], capture_output=True, text=True)
        check("stamping twice is idempotent",
              first == (d / "nlp-topic.md").read_text(encoding="utf-8"))


def _pack_file(d, name, title, source, body, extra=""):
    (d / name).write_text(
        f'---\nid: {kp.origin_id(source)}\nschema_version: 1\ntitle: "{title}"\n'
        f'type: concept\npack: ai-foundations\nretrieval_strategy: standard\n'
        f'source: {source}\nverified_at: "2026-01-01"\n'
        f'content_hash: {kp.sha256_body(body)}\n{extra}---\n\n{body}',
        encoding="utf-8")


def test_triage():
    print("triage")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "knowledge_packs"
        d = root / "ai-foundations" / "concepts"
        d.mkdir(parents=True)
        rich = "Linear programming with OR-Tools. " * 60
        thin = "Linear programming basics. " * 12
        other = "Convolutional neural networks pool and stride. " * 60
        # same source document, mirrored into two different origin trees
        _pack_file(d, "da-lp-rich.md", "LP Rich", "lectures/A2_TASK1.md", rich)
        _pack_file(d, "da-lp-thin.md", "LP Thin", "organized_lectures/A2_TASK1.md", thin)
        _pack_file(d, "dl-cnn.md", "CNN", "lectures/cnn.md", other)

        docs = list(kp.iter_pack_files(root, ["ai-foundations"]))
        check("iter_pack_files finds the pack", len(docs) == 3, len(docs))

        groups = kp.redundant_harvests(docs)
        check("same source document across origin trees groups together",
              len(groups) == 1, groups)
        key, keep, losers = groups[0]
        check("groups on basename, not full origin path", key == "a2_task1.md", key)
        check("richest harvest is kept", keep[2].name == "da-lp-rich.md", keep[2].name)
        check("thin harvest is the redundant one",
              len(losers) == 1 and losers[0][2].name == "da-lp-thin.md")

        r = subprocess.run([sys.executable, str(KP), "triage", "--root", str(root),
                            "--pack", "ai-foundations", "--quarantine-redundant",
                            "--manifest", str(Path(td) / "m.json"),
                            "--report", str(Path(td) / "r.json"), "--execute"],
                           capture_output=True, text=True)
        check("triage succeeded", r.returncode == 0, r.stderr[-400:])
        check("redundant harvest quarantined",
              not (d / "da-lp-thin.md").exists()
              and (root / "_quarantine" / "_redundant_harvests" / "da-lp-thin.md").exists())
        check("richest harvest survives", (d / "da-lp-rich.md").exists())
        check("unrelated file untouched", (d / "dl-cnn.md").exists())
        man = json.loads((Path(td) / "m.json").read_text())
        check("manifest records what was kept instead",
              man["moves"][0]["kept_instead"].endswith("da-lp-rich.md"), man["moves"][0])

        # a second run has nothing left to collapse
        r2 = subprocess.run([sys.executable, str(KP), "triage", "--root", str(root),
                             "--pack", "ai-foundations", "--quarantine-redundant",
                             "--manifest", str(Path(td) / "m2.json"),
                             "--report", str(Path(td) / "r2.json"), "--execute"],
                            capture_output=True, text=True)
        check("triage is idempotent",
              json.loads((Path(td) / "m2.json").read_text())["moves"] == [], r2.stdout[-200:])

        # dry run must not move anything
        _pack_file(d, "da-lp-thin2.md", "LP Thin", "lectures/x/A2_TASK1.md", thin)
        subprocess.run([sys.executable, str(KP), "triage", "--root", str(root),
                        "--pack", "ai-foundations", "--quarantine-redundant",
                        "--report", str(Path(td) / "r3.json")],
                       capture_output=True, text=True)
        check("triage without --execute moves nothing", (d / "da-lp-thin2.md").exists())


def test_validate():
    print("validate")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "knowledge_packs"
        d = root / "ai-foundations" / "concepts"
        d.mkdir(parents=True)
        body = "Constraint programming with CP-SAT. " * 60
        _pack_file(d, "da-ok.md", "OK", "lectures/ok.md", body)

        def run(*extra):
            return subprocess.run([sys.executable, str(KP), "validate",
                                   "--root", str(root), "--pack", "ai-foundations",
                                   *extra], capture_output=True, text=True)

        r = run()
        check("clean pack validates", r.returncode == 0, r.stdout[-300:])
        check("reports readiness", "ready to index" in r.stdout)

        # a body edited after stamping must be caught by the hash
        p = d / "da-ok.md"
        p.write_text(p.read_text(encoding="utf-8") + "\nsmuggled edit\n", encoding="utf-8")
        r = run()
        check("stale content_hash fails validation", r.returncode == 1)
        check("stale hash names the fix", "kp stamp" in r.stdout, r.stdout[-300:])

        _pack_file(d, "da-ok.md", "OK", "lectures/ok.md", body)
        _pack_file(d, "da-dup-id.md", "Dup", "lectures/ok.md", body)
        r = run()
        check("duplicate id fails validation",
              r.returncode == 1 and "duplicate id" in r.stdout, r.stdout[-300:])
        check("re-harvest of one source warns and names the fix",
              "kp triage" in r.stdout, r.stdout[-300:])

        (d / "da-dup-id.md").unlink()
        _pack_file(d, "da-wrong-pack.md", "Wrong", "lectures/w.md", body,
                   extra="")
        txt = (d / "da-wrong-pack.md").read_text(encoding="utf-8")
        (d / "da-wrong-pack.md").write_text(
            txt.replace("pack: ai-foundations", "pack: ai-masters"), encoding="utf-8")
        r = run()
        check("frontmatter disagreeing with its directory fails",
              r.returncode == 1 and "!= directory" in r.stdout, r.stdout[-300:])

        (d / "da-wrong-pack.md").unlink()
        stub = "Too short. "
        _pack_file(d, "da-stub.md", "Stub", "lectures/s.md", stub)
        r = run()
        check("stub warns but does not fail by default", r.returncode == 0, r.stdout[-200:])
        r = run("--strict")
        check("--strict promotes the stub warning to failure", r.returncode == 1)


if __name__ == "__main__":
    test_helpers()
    test_type_inference()
    test_roundtrip()
    test_stamp_helpers()
    test_stamp_roundtrip()
    test_triage()
    test_validate()
    print()
    if failures:
        print(f"{len(failures)} failing: {', '.join(failures)}")
        sys.exit(1)
    print("all checks passed")
