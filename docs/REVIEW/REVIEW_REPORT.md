# Exposure & Event Response — 8-Agent Demo Review

**Date:** 2026-10-06 · **Target review:** Hiscox Europe, 16 Oct 2026 · **Repo:** wryszka/exposure-response-workbench @ a4aa06d · **App:** https://exposure-response-workbench-7474656169654171.aws.databricksapps.com
**Panel:** practitioner · decision-maker · Databricks SA · senior developer · security · current-Databricks expert · incumbent champion · UI/UX + "scared Excel analyst". Extra lens: the **clear demo track** rule (don't overwhelm).

---

## Verdict: **SHIP WITH ROADMAPPED GAPS**

The build is functionally complete, on-brand, secure, and demoable. No true blockers (no live secrets, core logic correct). Ship once the pre-room fix list below lands and the incumbent-champion objections are armed in the Q&A. The "blockers" the incumbent champion names are our already-labelled PARTIAL/ROADMAP gaps, not breakage.

**Scorecard (P0/P1):** P0 — 1 open (app default screen) → fixing. P1 — overwhelm/jargon cluster (UI/UX) + bound-params SQL + talk-track framing → fixing. All other P1 gaps labelled + roadmapped. Security: LOW RISK. SA: GREEN with pre-flight.

---

## Pre-room fix list (apply before the dry run)

| # | Fix | Source | Status |
|---|-----|--------|--------|
| 1 | **App defaults to Live event**, not the Exposure picker (opening hook) | Decision-maker P0 | folding |
| 2 | **Strip jargon from on-screen explainers** (function/table names, EPSG, "ceded/Cat XL") → plain language; detail lives in Learn | UI/UX MAJOR 1,3,6,12,15 + practitioner | folding |
| 3 | **De-clutter KPIs** — headline = threatened properties + sum insured; collapse the 6 distance-band tiles | UI/UX MAJOR 2 | folding |
| 4 | **Map legible from the back of the room** — bigger property dots, stronger footprint fill, land/water + scale | UI/UX MAJOR 4 | folding |
| 5 | **Buffer defaults to the hero view** (not always 200m) | UI/UX MAJOR 5 | folding |
| 6 | **Tone:** recolour the red "Follow this story" button (brand/amber), reword "…never open Databricks" heading, tie the buy-vignette to the fire narratively | UI/UX MAJOR 7,8,9 | folding |
| 7 | **Bound-parameter SQL** everywhere (replace manual `esc()` + f-strings); + consistent HTML `esc()` in the SPA | Senior dev MAJOR + Security | folding |
| 8 | **Talk-track reframes:** Beat 1 name the risk-of-inaction + "real live feeds"; Beat 2 add a 30-sec gross→net bridge; Beat 3 present tense ("here's the alert that goes out") | Decision-maker + practitioner | folding (doc) |
| 9 | **Setup pre-flight** in the doc: warm the warehouse, pre-cache the 5 agent starters, confirm reset, manual "Next update" fallback | SA | folding (doc) |
| 10 | **Wire the real alert send** (SMTP or Slack webhook) | Incumbent/practitioner/decision-maker/SA | **needs a credential from Laurence** |

Minor/polish (fold if time, else v1.1): Genie SQL behind a "see the SQL" toggle; perils-sidebar acronym tooltips; nav/title de-dup; loading-time hints; agent loop-limit logging; bind rate-limit note; promotion unique-constraint (TOCTOU).

### Fixes applied — 2026-10-06 (deploy 01f1c16642451bb980103a60d79cc3c0, RUNNING; verified over HTTP)
**Done:** #1 (app now lands on Live event — verified), #2 (jargon stripped from all on-screen explainers + card subtitles; technical detail kept in Learn — verified plain copy served), #3 (KPIs = 2 headline tiles, distance bands behind a disclosure), #4 (map: dots 3.4→5.5 + white halo, footprint fill 0.16→0.30), #6 (Follow button → amber, "never open Databricks" → "teams who don't log into Databricks", vignette narrative bridge added, bind/object_ref wording plain), #7 app layer (all `esc()`+f-string → bound `:params` in app.py + agent.py; `query_many` extended to carry params; verified /api/event, /api/bind/buy, ramp return identical numbers), #8 (SPA `esc()` on event-card fields), #9 doc, talk-track doc reframes. Minors done: Genie SQL behind `<details>`, perils tooltips, nav de-dup, loading hint, agent loop-limit warning.
**Deferred (labelled):** #5 buffer default left at 200 m (the headline 59/€164.3m figure; impact reduced now the app opens on Live event, not the Exposure picker); #7 notebook SQL interpolation in 30_alerts.py / 10_feeds_common.py (feed/notebook context, not URL-controlled — v1.1); bind rate-limit + promotion TOCTOU unique-constraint (demo-trusted — v1.1).
**#10 real alert send:** still needs a free SMTP/Slack credential from the user.

### Post-review enhancements — 2026-10-06
- **Front page → alert board** (commit 26e2d31) + **AI/BI Live Fire Tracker dashboard** + Track-live button (commit 988c472).
- **Dual-outcome buy panel** (this change): two preset addresses → quote → Buy → **BOUND** (Munich, clear) vs **DECLINED** (Var, active zone), governed by `fn_bind_check`, audited; verified over HTTP.

---

## Deal-breakers to ARM in Q&A (incumbent champion + practitioner)

Each has a credible, honest answer — the demo must not pretend otherwise:
- **"Alerts don't actually send."** Engine built + audited; one free credential (SMTP/Slack) turns real send on, no code change. *(Fix #10 removes this if we wire it.)*
- **"Wildfire (EFFIS) isn't live."** Frozen real Copernicus perimeter for reproducibility; MeteoAlarm + GDACS are live-proven; EFFIS WFS endpoint confirmation is in progress (parked by choice).
- **"Damage curve is illustrative, not a cat model."** True and labelled — a transparent, governed first-order curve; a real cat model would supply it (roadmap). Don't present net as a signed-off capital number.
- **"Anti-selection isn't integrated to underwriting."** Self-contained vignette by scope decision; the control logic + audit are real; cross-workbench wiring is the deferred next phase.
- **"Geocoding accuracy / scales to a 50k+ book?"** Synthetic coords in the demo; at real scale this needs a geocoding provider + a scale test — roadmap; the engine pattern is standard warehouse geospatial.

## Roadmap (post-16-Oct, labelled)
Served Agent-Framework endpoint + MLflow tracing (current-DBX P1); real cross-workbench underwriting integration; real cat model for the damage curve; historical/season-to-date UI; EFFIS live endpoint; scale test on a real-size book.

---

## Per-persona summary

- **Practitioner — "real and right."** Matches the canonical question; cross-border fix is structural; governed maths credible. Framing risks: alerts/EFFIS/anti-selection (above) + Beat 2 needs a bridge.
- **Decision-maker — "ship."** Money + story land (€183m → €25m). P0: default to Live event. Add risk-of-inaction + live-data signal to Beat 1.
- **Databricks SA — GREEN w/ pre-flight.** Warm warehouse, pre-cache starters, manual Next-update fallback for the 30s auto-advance. All tested-mitigated.
- **Senior developer — ship after SQL fix.** MAJOR: bound params vs manual escaping. Core geospatial/waterfall/governance tight; no dead code.
- **Security — LOW RISK, ship.** No secrets in code/history; least-privilege SP; synthetic data; public-API egress only. Minor: bound params (defense-in-depth).
- **Current-Databricks expert — ship as-is.** No deprecations. P1 production roadmap: served agent endpoint + MLflow tracing.
- **Incumbent champion — would keep incumbent (today).** Objections = the labelled gaps above; arm them in Q&A, don't let them ambush.
- **UI/UX + scared analyst — MAJOR overwhelm fixes (fixes #1–6).** On-brand and functional, but jargon leaks + KPI wall + faint map would overwhelm a non-technical analyst. After fixes: "OK, I can follow this — I'd use it."
