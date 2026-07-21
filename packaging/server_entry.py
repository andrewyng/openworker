"""PyInstaller entry point for the bundled desktop sidecar server.

Thin wrapper so PyInstaller has a concrete script to analyze (the console_script
`coworker-server` is generated metadata, not a file). Runs the same `main()`.
"""

from coworker.server.run import main

if __name__ == "__main__":
    main()
