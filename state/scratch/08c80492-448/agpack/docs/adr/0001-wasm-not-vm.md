# ADR-0001: WASM, not microVMs, for the v0 tool sandbox

**Status:** accepted (scaffold stage; revisit if step 4's portability
proof hits a wall)

## Context
The tool sandbox must run *untrusted* tool code with hard capability
boundaries at the "most dangerous security gap" the decision memo's
sources cite. Two candidate isolation technologies: WASM (wasmtime /
wasmer) vs. microVMs (Firecracker, gVisor).

## Decision
WASM for v0. The guest's only door to the host is the declared
import surface, and WASM imports are declared **at instantiation
time** — a guest cannot ask for a capability it isn't declared.
That matches the "deny by default, capability by exception" policy
model natively; in a container the door is a *syscall surface that
exists and is then denied*, which is inverted.

WASM also gives: fuel metering (budget = security property), bounded
linear memory (no host `mmap`), no arbitrary fs/net unless explicitly
imported, and a first-class fit with edge/serverless runtimes (the
"Vercel-able" criterion). microVMs earn their cost when the guest is
an arbitrary OS binary — our guests are tools, not operating
systems.

## Consequences
- + Instantiation-time capability check (sandbox invariant #1/#2).
- + Deterministic fuel meter doubles as the billing hook (step 5).
- - Guest code must be compiled to WASM — toolchain friction is on
  tool authors, not on the platform; acceptable for v0 (spec §12,
  open question 1: possibly ship a tool build step later).
- - WASM-to-WASM interop (guest calling guest) is not possible.
  Not a v0 requirement; a v1+ spec question.
- - Engine heterogeneity (wasmtime vs wasmer fuel accounting) must be
  normalized — that's exactly what the profile layer
  (`portability/profiles.py`) exists for.
