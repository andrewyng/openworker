"""Remote homes — headless workers that dial OUT and join a controller.

One codebase, two composable roles (remote-home-design.md):
  - WORKER: the entire existing server (state, engines, ASGI app) with NO
    listener — `joiner.py` dials out and the held WebSocket carries a small
    RPC framing into the app in-process.
  - ACCEPTOR: a thin module (enrollment, machines registry, socket holder,
    proxy prefix) mounted into the sidecar app — and nothing else, so the
    future cloud service is "import acceptor, add auth".
"""
