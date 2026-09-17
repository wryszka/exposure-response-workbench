# Demo run-sheet — Exposure & Event Response (Bricksurance SE)

> Skeleton — filled beat-by-beat as the build lands (P1+). Every beat is **GO · DO · SAY · IF-ASKED**. SAY lines ≤20 words, **bold** the numbers, **no platform words** (they live in the Q&A). See the standard's BUILD_AND_REVIEW.md §4.

**Audience framing:** present in the exposure manager's process order — an event is developing, so what's my book's exposure and who do I tell? Platform words live in `DEMO_QA.md`.
**Runtime:** ~TBD min demo + Q&A.
**If compressed, cut in this order:** TBD. **NEVER cut** the live-footprint → threatened-properties beat.

## Links — dev (stable)
- **App:** TBD (P5)
- **Schema (Catalog Explorer):** `lr_dev_aws_us_catalog.exposure_response`
- **Key assets:** `3_property` · `2_event_footprint` · `fn_exposure_in_footprint` (P1) · Genie (P5) · alert job (P4)

## Pre-flight (before the room)
- **Reset:** in-app button / CLI (regenerates seeded data, rolls event dates to recent past).
- **Verify** embedded map + Genie render on your authenticated session.

---

**0 · The problem (1m)**
- **SAY:** "A wildfire is burning in southern France. Which of our properties are threatened, what's the exposure, and who needs to know — **right now**?"
- **IF ASKED:** how is this done today? → manual: hand-pull policies, eyeball fire outlines. Q&A #1.

**1 · The live footprint on the book (P1)** — the load-bearing beat
- **SAY:** "The fire footprint over our book: **N properties** within **200 m**, **€X m** sum insured at risk."

**2 · Gross → net of treaty (P3)**

**3 · Who needs to know — the alert (P4)**
- **SAY:** "One event, **three countries**. One alert to the right people — not three teams missing it."

**N · Finale — the governed loop (1m)**
- **SAY:** "Live event, real book, one governed answer — gross, net, and dispatched — in minutes, not a scramble."
