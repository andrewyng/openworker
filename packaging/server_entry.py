"""PyInstaller entry point for the bundled desktop sidecar server.

Thin wrapper so PyInstaller has a concrete script to analyze (the console_script
`openworker-server` is generated metadata, not a file). Runs the same `main()`.
"""

import sys

if __name__ == "__main__":
    # Runner mode first, before the server's imports: inside a sandbox the frozen binary
    # is the tool runner (coworker/sandbox/launch.py), and it must not load the server.
    from coworker.sandbox.launch import maybe_run_runner

    maybe_run_runner(sys.argv[1:])
    from coworker.server.run import main

    main()
