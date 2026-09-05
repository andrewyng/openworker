"""One-off corpus coverage stats for the permission-mode analysis (not shipped)."""
import json
import collections

for name in ("benign", "dangerous", "injection"):
    tags = collections.Counter()
    tools = collections.Counter()
    correct = collections.Counter()
    holdout = 0
    n = 0
    with open(f"tests/corpora/{name}.jsonl", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            n += 1
            correct[d["correct"]] += 1
            tools[d["action"]["tool"]] += 1
            holdout += bool(d.get("holdout"))
            for t in d.get("tags", []):
                tags[t] += 1
    print(f"== {name}: {n} rows, holdout={holdout}, correct={dict(correct)}")
    print("  tools:", dict(tools))
    print("  tags:", dict(tags.most_common()))
