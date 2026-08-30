"""Host imports — the *surface* the guest module can call.

This is the only door between the guest and the host. Concretely, it is
a mapping from **the WAT/WASM import namespace the guest declared in
its module header** to a Python callable the sandbox engine wires up
when it instantiates the module. The shape of the mapping:

    "<ns>.<name>" -> Callable[[guest-args...], guest-result]

Example (for the default ``net.fetch`` scope):

    "agpack/net.fetch" -> (url: str) -> (status: i32, body_bytes: i32, body_len: i32)

Rules the module must enforce *at instantiation time* (see sandbox
invariant #2):

1. The guest's import list (read without executing; WASM section 2)
   is a **subset** of the bundle's ``permission_policy`` scopes.
   An *unlisted* import is a *hard error* — the module does not
   instantiate, full stop. The module-level ``imports`` dict below is
   the *only* import namespace the sandbox engine registers; anything
   the guest requests that is not in that dict is not "denied at call
   time," it's "not declared to the guest in the first place."
2. The parameter types / arities must match the import contract for
   the scope (e.g. ``net.fetch`` takes exactly one ``str`` and returns
   a tuple). A guest that declares a different signature is a *guest
   bug*, not a *policy decision* — the module does not instantiate
   either. (This is a *type-safety* check, not a *security* check, but
   it's in the same place because the cost of a silent type mismatch
   is a guest that hangs waiting on a call the host can't route.)
3. Every registered import has a fuel hook and an audit hook. A host
   import without these is a **hard error at sandbox construction** —
   the sandbox cannot grant a capability it cannot meter and log.

The default scopes (v0), in the order the validator allows them:

    fs.read      (path: i32, path_len: i32, out: i32, out_len: i32) -> i32
    fs.write     (path: i32, path_len: i32, data: i32, data_len: i32) -> i32
    net.fetch    (url: i32, url_len: i32, out_status: i32, out_body: i32, out_body_len: i32) -> i32
    clock.now    (out_i64: i32) -> ()
    random       (out_i32: i32, n: i32) -> ()
    memory.get   (field: i32, field_len: i32, out: i32, out_len: i32) -> i32
    memory.set   (field: i32, field_len: i32, data: i32, data_len: i32) -> i32
    emit.text    (chunk: i32, chunk_len: i32) -> i32

Design notes:

- The **``i32`` offsets + lengths** pattern (a WASM "string pointer"
  convention, *not* a null-terminated C string) is deliberate: the
  guest hands a *pointer and a length* into its own linear memory,
  and the host copies exactly that many bytes. There are no C-string
  conventions here; a trailing NUL is a *payload* byte, not a
  terminator. (A guest that writes a NUL-terminated string and
  declares the length including the NUL gets a NUL byte at the end of
  its read — that's correct behavior for a byte-oriented API.)
- The host never *passes* a writable pointer into guest memory that it
  hasn't pre-specified with the exact byte count it will write. This
  means a guest can't "trick" the host into writing more than it
  declared (the ``out_len`` param is the *destination capacity*; the
  host writes ``min(payload_len, out_len)`` and returns the count it
  actually wrote).
- The import surface **must not expose** a "host function call"
  primitive (e.g. a "call Python" import). There is no escape hatch
  from the guest into arbitrary host Python *by design*: the host
  Python in the same process is the *sandbox engine*, not a callable.
  If a future version needs plugin-style guest-to-host dispatch beyond
  this, it's a *new* scope with its own fuel hook and audit hook, not
  a new entry point in this dict.
"""

# Intended surface:
#   Import = Callable[..., Any]
#   IMPORTS: dict[str, Import] = {
#       "agpack/fs.read":       _fs_read,
#       "agpack/fs.write":      _fs_write,
#       "agpack/net.fetch":     _net_fetch,
#       "agpack/clock.now":     _clock_now,
#       "agpack/random":        _random,
#       "agpack/memory.get":    _memory_get,
#       "agpack/memory.set":    _memory_set,
#       "agpack/emit.text":     _emit_text,
#   }
#   def build_imports(policy: CapabilityPolicy, budget: Budget, audit: AuditLedger) -> dict[str, Callable]: ...
#       # Filters IMPORTS down to the policy's scopes and wires up
#       # the fuel/audit hooks for each.

raise NotImplementedError("Scaffold stub — see module docstring.")
