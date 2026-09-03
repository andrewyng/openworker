---
name: maintenance-log
description: Equipment registry and service history — track appliances and machinery over their life
---
Keep a living record of the equipment across all nine domains: what exists,
what's been fixed, and what service is coming due.

1. Keep the registry as equipment.csv in the workspace root — one row per device:
   domain (household / garden / outdoor / agriculture / laboratory / medical /
   clinic / dental / fire-safety — from the domain-profiles classification),
   device, brand, model, serial, location, purchase_date, manual_url, and the
   diagnostic interface when one exists (MHS device id, OBD/CAN adapter, BLE
   service) so the next session reconnects instead of rediscovering. All nine
   domains, household to fire safety; ask before adding or removing equipment.
2. Service history per device: date, symptom or service, what was done, parts
   used with numbers, cost, and the domain's usage counter — hours for
   agriculture, sterilization cycles for clinic and dental, seasons for garden,
   run-time for outdoor. Every repair out of symptom-diagnosis lands here.
3. Maintenance intervals from the manual when public: filters, belts, oil and
   greasing schedules for machinery; descaling, coil cleaning, gasket checks for
   appliances. Where the manual isn't public, use industry-typical intervals and
   label them as such.
4. On each check-in, brief what's due or overdue, what's approaching, and any
   device whose repair history is starting to argue for replacement — with the
   cost math shown as estimates.
5. Scheduled runs stay tight: due-brief, the registry delta, and one
   recommendation. Writes stay inside the registry and history files.
