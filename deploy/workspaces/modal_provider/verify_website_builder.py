"""Check the physical Modal objects at the SDK website-builder test's barriers."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

import modal


def until(check, description):
    end = time.monotonic() + 120
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.25)
    raise TimeoutError(description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--barrier", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    uri = (args.state_dir / "workspaces.sqlite").resolve().as_uri() + "?mode=ro"

    def snapshot():
        with closing(sqlite3.connect(uri, uri=True)) as db:
            compute = dict(db.execute("SELECT key, uid FROM compute WHERE uid IS NOT NULL"))
            data = dict(db.execute("SELECT key, uid FROM keys JOIN data ON data.id=keys.data"))
        return compute, data

    builder, reviewer, bob = ("website-builder/" + k for k in ("garden/builder", "garden/reviewer", "studio/builder"))
    receipts = {}
    for step in ("built", "shared", "separate", "bob", "app-stopped", "edited"):
        until((args.barrier / step).exists, step)
        if step == "app-stopped":
            until(lambda: not snapshot()[0], "idle sandboxes to terminate")
            for sandbox_id in initial_sandboxes:
                try:
                    assert modal.Sandbox.from_id(sandbox_id).poll() is not None
                except modal.exception.NotFoundError:
                    pass
        compute, data = snapshot()
        for key, uid in compute.items():
            sb = modal.Sandbox.from_id(uid)
            assert sb.poll() is None and sb.get_tags()["volume"] == data[key]
        if step == "built":
            first = compute[builder]
        elif step == "shared":
            assert compute == {builder: first}
        elif step == "separate":
            assert compute[builder] == first != compute[reviewer]
            assert data[builder] == data[reviewer]
        elif step == "bob":
            assert data[bob] != data[builder]
            saved = data
            initial_sandboxes = set(compute.values()) | {first, receipts["separate"]["sandboxes"][reviewer]}
        elif step == "app-stopped":
            assert not compute and data == saved
        elif step == "edited":
            assert data == saved and set(compute.values()).isdisjoint(initial_sandboxes)
        receipts[step] = {"sandboxes": compute, "volumes": data}
        args.evidence.write_text(json.dumps(receipts, indent=2) + "\n")
        (args.barrier / (step + ".ok")).touch()
        print(step, flush=True)


if __name__ == "__main__":
    main()
