"""Entry point of the tool runner: `serve` (the daemon) and `attach` (the relay).

Runs as `python -m coworker.sandbox.runner ...` from a checkout, or as
`python runner.pyz ...` from the packed single file that is mounted into a sandbox.
"""

from __future__ import annotations

import argparse
import os
import sys

from .daemon import RUNNER_VERSION, Daemon
from .relay import run as run_relay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openworker tool-runner", description="OpenWorker tool runner")
    parser.add_argument("--version", action="version", version=RUNNER_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the daemon (the sandbox's main process)")
    serve.add_argument("--socket", required=True, help="path of the Unix socket file to listen on (Windows: a \\\\.\\pipe\\ name)")
    serve.add_argument("--cwd", default=None, help="folder new shells start in (default: current folder)")
    serve.add_argument("--exit-with-parent", action="store_true", help="stop when the starting process is gone (local use)")
    serve.add_argument("--dir", default=None, help="this runner's own folder, removed at shutdown (default: the socket's folder)")
    serve.add_argument("--allow-sid", action="append", default=[], help="Windows: an account that may connect to the pipe (repeatable)")
    serve.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="set a variable for the shells (repeatable; for a provider that cannot pass an environment)")
    serve.add_argument("--env-file", default=None, help="a JSON object of variables for the shells (a command line that must stay short: Windows' logon call allows 1024 characters)")
    attach = sub.add_parser("attach", help="connect this process's stdin/stdout to the daemon")
    attach.add_argument("--socket", required=True)
    attach.add_argument("--silence-seconds", type=float, default=None, help="leave after this much client silence (0 = never)")
    connect = sub.add_parser("connect", help="a tunnel through the allow-list proxy on stdin/stdout (ssh ProxyCommand)")
    connect.add_argument("proxy_host")
    connect.add_argument("proxy_port", type=int)
    connect.add_argument("host")
    connect.add_argument("port", type=int)
    args = parser.parse_args(argv)
    if args.command == "connect":
        from .connect import run as run_connect

        return run_connect(args.proxy_host, args.proxy_port, args.host, args.port)
    if args.command == "serve":
        if args.env_file:
            import json

            with open(args.env_file, encoding="utf-8") as fh:
                for name, value in json.load(fh).items():
                    os.environ[str(name)] = str(value)
        for item in args.env:
            name, sep, value = item.partition("=")
            if sep and name:
                os.environ[name] = value
        # A provider may put a folder of its own first on PATH (the ssh wrapper that points
        # at a copied credential, section 11b) without knowing the sandbox's own PATH.
        prepend = os.environ.pop("OPENWORKER_PATH_PREPEND", "")
        if prepend:
            os.environ["PATH"] = prepend + os.pathsep + os.environ.get("PATH", "")
        Daemon(args.socket, args.cwd, args.exit_with_parent, runtime_dir=args.dir, allow_sids=args.allow_sid).serve_forever()
        return 0
    if args.silence_seconds is None:
        return run_relay(args.socket)
    return run_relay(args.socket, args.silence_seconds)


if __name__ == "__main__":
    sys.exit(main())
