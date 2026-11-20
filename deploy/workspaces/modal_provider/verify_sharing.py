"""Exercise shared Modal storage using disposable CPU sandboxes and a unique Volume."""
import argparse
from contextlib import ExitStack
import uuid

import modal
from filesystem import file_operation, runner_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    env = parser.parse_args().environment
    name = "ow-ws-share-" + uuid.uuid4().hex[:16]
    app = modal.App(name)
    with ExitStack() as cleanup:
        cleanup.enter_context(app.run(environment_name=env))
        modal.Volume.objects.create(name, version=2, environment_name=env)
        volume = modal.Volume.from_name(name, environment_name=env)
        cleanup.callback(modal.Volume.objects.delete, name, environment_name=env)
        volume.hydrate()
        print("Created trial volume", volume.object_id, flush=True)
        image = runner_image()
        sandboxes = {}
        for role in ("builder", "reviewer"):
            sandbox = modal.Sandbox.create(
                "sleep", "infinity", app=app, image=image, volumes={"/data": volume},
                name=name + "-" + role, env={"WORKSPACE_ID": role},
                timeout=180, cpu=0.25, memory=256, block_network=True,
            )
            cleanup.callback(sandbox.terminate, wait=True)
            sandboxes[role] = sandbox
            print(role, sandbox.object_id, flush=True)

        def apply(role, tool, **arguments):
            return file_operation(sandboxes[role], role, {"name": tool, "arguments": arguments})

        assert sandboxes["reviewer"].object_id != sandboxes["builder"].object_id
        assert apply("reviewer", "list_files")["files"] == []
        page = {"path": "index.html"}
        apply("builder", "write_file", **page, content="<h1>Garden</h1>")
        assert apply("reviewer", "read_file", **page)["text"] == "<h1>Garden</h1>"
        apply("builder", "edit_file", **page, old_text="Garden", new_text="Autumn garden")
        assert apply("reviewer", "read_file", **page)["text"] == "<h1>Autumn garden</h1>"
        print("Verified write and edit from the original live reviewer", flush=True)
    print("Released both sandboxes and deleted the trial volume", flush=True)


if __name__ == "__main__":
    main()
