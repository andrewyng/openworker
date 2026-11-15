"""Agent observability: recording every step of every turn (Phase 1).

The design is `ocw-context/docs/observability/phase-1/technical-design.md`; the contract
shared with the Rust harness SDK is `recording-format.md` 1.0 and its golden test.

- `recorder.Recorder`: the one sink for recording events, folding them into turns and steps.
- `store.RecordingStore`: `recordings.db`, next to `coworker.db`.
- `ids.new_ulid`: ids for messages (`mid`), turns and steps.
"""

from .ids import new_ulid
from .recorder import Recorder
from .store import RecordingStore

__all__ = ["Recorder", "RecordingStore", "new_ulid"]
