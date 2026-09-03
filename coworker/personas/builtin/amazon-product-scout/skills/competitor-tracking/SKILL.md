---
name: competitor-tracking
description: Standing watch on competitor ASINs — snapshots, deltas, scheduled briefs
---
Keep a standing watch on a set of competitors: snapshot what is publicly visible,
diff it on every run, and brief what changed. Read-only and modest in volume — this
is a watch, not a crawler.

1. Set up the watchlist as competitors.csv in the workspace root — one row per
   tracked product: asin, title, brand, category, why_tracked, added_on. Ask before
   adding or pruning entries; the list is the user's strategy, you maintain it.
2. Each run, snapshot every product into
   competitor-tracking/snapshots/YYYY-MM-DD.json: price, coupon, rating,
   review_count, buy_box_seller, variation_count, and any visible listing changes
   (title keywords, imagery, A+ modules). Date every field; record "not visible"
   rather than guessing when a page throttles or hides something.
3. Diff against the previous snapshot and brief the deltas:
   - Price moves of 5% or more, or any coupon change — who moved first?
   - Review velocity: reviews gained since the last run; a sharp shift is a demand
     or campaign signal worth naming.
   - Listing changes: what changed and which keyword or positioning it targets.
   - New entrants appearing near the top of the niche's search results.
   Unchanged products stay one line. Changes get evidence and a likely-reason read,
   clearly marked as your inference.
4. Escalate thesis-changing events prominently: a price war starting, a top player's
   review velocity doubling, a competitor repositioning onto your keywords, a full
   listing refresh.
5. Scheduled runs keep the brief tight: headline changes, the delta table, and what
   to do about it. Writes stay inside the tracking files — anything beyond that
   gets asked first.
