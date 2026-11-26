"""Generate one isolated cloud-host Compose deployment per bot; never starts it.

Run this on an existing cloud Docker host, then explicitly run Compose. Device
approval enrolls the new runtime without putting a join credential in the file.
"""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def deployment(name: str, controller: str, image: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise ValueError("Name must be a lowercase slug, at most 40 characters")
    url = urlsplit(controller)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment or "$" in controller):
        raise ValueError("Controller must be a bare HTTPS origin, without credentials or enrollment tokens")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]*", image):
        raise ValueError("Use a Docker image name or digest")
    return {
        "name": f"openworker-bot-{name}",
        "services": {
            "bot": {
                "image": image, "init": True, "restart": "unless-stopped",
                "environment": {
                    "COWORKER_DEDICATED_BOT_RUNTIME": "1", "COWORKER_STATE_DIR": "/state",
                    "HOME": "/state/home", "BOT_RUNTIME_NAME": name,
                    "BOT_CONTROLLER": controller.rstrip("/"),
                },
                "volumes": ["state:/state"], "networks": ["private"],
                "read_only": True, "tmpfs": ["/tmp:rw,nosuid,nodev,size=1g,mode=1777"],
                "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
                "pids_limit": 256, "mem_limit": "4g", "cpus": 2, "shm_size": "512m",
                "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
            }
        },
        "volumes": {"state": {}}, "networks": {"private": {"driver": "bridge"}},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = deployment(args.name, args.controller, args.image)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = args.output / "compose.json"
    # Never overwrite another runtime's deployment or private configuration.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(config, indent=2) + "\n")
    path.chmod(0o600)
    print(f"Created {path}. Start it explicitly with Docker Compose; approve its device code in Settings → Machines.")


if __name__ == "__main__":
    main()
