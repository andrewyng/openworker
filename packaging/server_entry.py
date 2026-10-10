"""PyInstaller entry point for the bundled desktop sidecar server.

Thin wrapper so PyInstaller has a concrete script to analyze (the console_script
`openworker-server` is generated metadata, not a file). Runs the same `main()`.
"""

import os
import sys

if __name__ == "__main__":
    # Runner mode first, before the server's imports: inside a sandbox the frozen binary
    # is the tool runner (coworker/sandbox/launch.py), and it must not load the server.
    # The cryptography library looks for OpenSSL's legacy-algorithms module and warns on
    # every start when it is not there ("OpenSSL 3's legacy provider failed to load"); the
    # Intel Mac build, with OpenSSL linked in statically, has no such module. OpenWorker
    # uses no legacy algorithm, so tell it not to look.
    os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")
    from coworker.sandbox.launch import maybe_run_runner

    maybe_run_runner(sys.argv[1:])
    from coworker.server.run import main

    main()
