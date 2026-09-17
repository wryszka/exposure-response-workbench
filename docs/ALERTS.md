# Stakeholder alerts — how they work, and the one credential to send for real

The alert path answers the Hiscox requirement head-on: **the people who never open Databricks** (claims
handlers, underwriters, seniors) get told when a catastrophe event develops — and a cross-border event arrives
as **one alert**, not fragmented across country-siloed teams.

## What runs

`notebooks/30_alerts.py`, on the serverless job **`exposure_30_alerts`** (hourly schedule, **PAUSED** by default
— arm it for the demo, or hit *Run alert sweep now* in the app). Each sweep, for every active event:

1. **Snapshot** the live threatened set → `gov_exposure_snapshot` (append-only).
2. **Delta** vs the previous snapshot → `fn_event_delta` → newly / still / no-longer threatened (new fires,
   progression, extinguished).
3. **Threshold** per peril → `ref_alert_threshold` (gross-loss floor OR newly-threatened count).
4. **Digest, grouped by event** — one alert with the whole cross-border picture: threatened count + sum insured,
   gross → ceded → net, exposure by country (every country the event touches), top delegated authorities, and
   what's new since last time. A link back to the live exposure view.
5. **Dispatch + audit** → `gov_alert_dispatch` (append-only): the threshold decision, the governed numbers, the
   channel + recipients, and the exact digest sent.

Every dispatch is recorded whether or not it is delivered, and the app's **"Preview the alert that goes out"**
button renders the exact stored digest — so the preview is the real artefact, not a mock.

## Status values (on each `gov_alert_dispatch` row)

| Status | Meaning |
|---|---|
| `SENT` | Event breached its threshold **and** a channel secret is configured — really delivered. |
| `RENDERED_NOT_SENT` | Event breached, but no channel secret is set — the digest is built, audited and previewable; nothing was delivered. **This is the current state out of the box.** |
| `SUPPRESSED` | Below threshold — logged, not sent. |

## The one credential to add — then it sends for real (no code change)

The sender is pluggable and tries **SMTP first, then a Slack webhook**. Set either as secrets in the
`exposure_response` scope. Serverless egress for both is already proven (P2).

### Option A — email (SMTP)
```bash
databricks secrets create-scope exposure_response   # if it doesn't exist yet
databricks secrets put-secret exposure_response SMTP_HOST --string-value smtp.yourprovider.com
databricks secrets put-secret exposure_response SMTP_PORT --string-value 587
databricks secrets put-secret exposure_response SMTP_USER --string-value <username>
databricks secrets put-secret exposure_response SMTP_PASS --string-value <app-password>
databricks secrets put-secret exposure_response SMTP_FROM --string-value alerts@bricksurance.example
databricks secrets put-secret exposure_response SMTP_TO   --string-value your-demo-inbox@example.com
```
A Gmail account with an **app password** works well for a demo inbox.

### Option B — Slack
```bash
databricks secrets put-secret exposure_response SLACK_WEBHOOK --string-value https://hooks.slack.com/services/XXX/YYY/ZZZ
```

Re-run the sweep (`Run alert sweep now`, or `databricks bundle run exposure_30_alerts -t dev`) and breaching
events flip to **`SENT`**.

## Related credentials (feeds — see `docs/FEEDS.md`)
Independent of alerting: **NASA FIRMS MAP_KEY** and **Copernicus CDS API key** light up the live wildfire and
flood feeds. All three (FIRMS, CDS, SMTP/Slack) are free and optional — the demo runs fully without any of them.
