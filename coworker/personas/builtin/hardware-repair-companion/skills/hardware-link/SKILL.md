---
name: hardware-link
description: Live device diagnostics over MHS or diagnostic ports — read fault codes and telemetry before touching anything
---
Connect to the equipment itself when it has a digital interface, and let real
telemetry drive the diagnosis instead of guesswork. Built around the Model
Hardware Standard (MHS) pattern — standardized device drivers, plain-language
safety labels, read/write discipline.

1. Establish what the machine exposes, in this order:
   - An MHS-registered device discoverable on the network. Read its reference
     file and natural-language labels FIRST — weight, speeds, temperatures, and
     interlock behavior live there, and they bound everything you do next.
   - A diagnostic port reached through an adapter with a documented interface —
     OBD-II or CAN bus on machinery, service or BLE ports on appliances,
     community hardware MCP servers where they fit.
   - Nothing digital: say so plainly and fall back to the manual's fault-code
     table plus owner-described symptoms. No interface is a fact, not a failure.
2. READ before anything else: active and stored fault codes, hour meters and
   cycle counts, sensor readings (temperatures, pressures, RPM, voltages), and
   the error history. Timestamp every reading and name the device it came
   from — live data is evidence, exactly like a manual page, and it goes into
   the diagnosis with its source.
3. Feed the readings into symptom-diagnosis: a real fault code outranks a
   hypothesis. Owner-described symptoms get reconciled against what the machine
   itself reports, and mismatches get named — they often ARE the finding.
4. WRITE only with explicit approval — resets, calibration values, actuation
   tests. Before each write, state what it commands, what the machine will
   physically do, and what could go wrong. Never write to defeat an interlock,
   guard, or limit, regardless of interface. MHS is still a research preview:
   most household and farm equipment has no driver yet — treat a missing
   registration as the normal case and degrade to the port or manual path.
5. Record the connection in the equipment registry: interface type, adapter,
   driver or MHS device id, and the telemetry snapshot alongside the service
   history entry — so the next session reconnects instead of rediscovering.
