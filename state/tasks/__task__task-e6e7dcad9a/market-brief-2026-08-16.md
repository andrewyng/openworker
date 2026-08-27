# Pre-Open Market Brief — Sigma System — 2026-08-16

Sources staged: status_report.json (snapshot 2026-08-16T14:33Z), decisions_20260816.csv (1,717 rows: 136 GO / 1,445 NO_GO / 136 GATE_SKIP). No earlier market-brief file exists in this workspace, so there is no prior baseline to diff against — this brief is the first of the series.

---

## 🔴 Needs a decision before the open

1. **Account C (Options/High-Vol) evaluated nothing today.** `accounts_seen` in decisions-summary.json is `["A", "B"]` — C is present in status_report.json with 32 open positions and $5,036 equity, yet produced zero decisions. Either C's evaluation path failed silently or the daemon doesn't route it. This is a system failure, not a market event — decide whether to restart/investigate that leg.
2. **49 suppressed errors: `scanner.fetch_option_chain:alpaca | HTTPError`** in `swallowed`. Option-chain data was degrading during the scan for GO decisions that are all option-based (all rows show dte=32, spread checks, IV). Some scored names may have stale or missing option data. Worth checking the scanner before acting on the GO list.

Circuit breaker: NOT tripped. Trading-day stamp matches today (2026-08-16), so the daemon is current.

---

## 1. Account state

| Account | Label | Equity | Cash | Positions | Unrealized P&L |
|---|---|---|---|---|---|
| A | Mean-Reversion | $5,014.05 | $5,014.05 | 0 | $0.00 (empty) |
| B | Momentum | $2,525.88 | $1,080.88 | 4 | **−$1,240.00** |
| C | Options/High-Vol | $5,036.34 | $22.31 | 32 | +$30.45 |

- **Portfolio value: $12,576.27 · Daily P&L: −$1,209.55** — essentially all of today's drawdown is Account B's unrealized losses (−$1,240 across 4 momentum positions).
- A is 100% cash (buying power $20,056) — idle while it has 3 GO names.
- C is nearly all-in: $22 cash against 32 positions; buying power $14,128 (margin on options).
- No FX positions; both Alpaca connections active; trade_log_count = 0 at snapshot.

## 2. Active GO names (8 of 8 covered — none skipped)

All 8 are 32-DTE options with `reason: score>=gate`. Scores shown are total / min gate.

| Ticker | Account | Cluster | Total score (gate) | IV rank | Spread % | Notable news (since last close) |
|---|---|---|---|---|---|---|
| NVDA | B | ai_chips | 131.2 (101) | 38.0 | 1.7% | Clean. Earnings-preview chatter this week (Aug 14); 13-F position trims/boosts on Aug 14; +5 momentum bonus applied. Trades ~$225, high $227.49 per one Aug 16 snapshot. **No thesis-invalidating event found.** |
| QCOM | B | ai_chips | 120.8 (101) | 44.8 | 1.6% | Just digested fiscal-Q3 revenue beat + modest EPS miss with a cautious outlook (mid-Aug reports); dividend $0.92, ex-date Sep 3 — before the 32-DTE expiry window, no conflict with the position horizon. An Aug 13 "Intel/Qualcomm" cross-headline exists but is analyst commentary, not a corporate event. **Thesis intact — watch the cautious guidance sentiment.** |
| INTC | B | ai_chips | 110.8 (101) | 63.5 | 2.7% | ⚠️ **Aug 10: Intel announced a proposed $15B common stock offering.** A share issuance of that size is a material supply/dilution event over the next several weeks and directly interacts with momentum theses. Not an earnings/guidance miss (Q2 reported Jul 23), but this is the name most at risk of thesis erosion. |
| ALB | A | lithium_mining | 111.3 (99) | 50.9 | 6.9% | Q2 earnings (call Aug 13) showed record profitability on lithium pricing strength + cost savings; lithium prices "stay strong" (RBC: demand resilience). Positive catalyst, thesis intact. Note spread 6.9% is near the 7% hard limit. |
| FCX | A | copper_mining | 108.3 (99) | 47.2 | 3.0% | Quiet. Q2 reported Jul 23 (strong copper results, $0.68 EPS). No post-close catalysts found. |
| SHOP | A | e_commerce | 106.4 (99) | 44.4 | 6.7% | Q2 earnings Aug 5; revenue guidance for Q3 raised to $3.7–3.8B vs ~$3.6B consensus — beat + raise already in the number. Spread 6.7% also near the 7% limit; lowest open interest (963) of the A-names, so liquidity is the soft spot. |
| JOBY | B | ev_aviation | 104.1 (101) | 72.3 | 2.8% | ⚠️ **Aug 11: ~$500M defense acquisition (Resonant Sciences) + up to $750M equity offering** — stock fell ~4% the day it was disclosed; a sell rating was reaffirmed Aug 12 citing dilution and execution risk; Q2 loss ($0.25) missed and heavy cash burn; Form 144 insider selling on file. Multiple thesis-stress events in one week. |
| QBTS | B | photonics_quantum | 102.1 (101) | 79.4 | 2.1% | Expanded AT&T partnership (~Aug 6–10) drove a ~7% pop — positive. But Q2 revenue/EPS missed at the June report and Northland cut to Hold on Aug 7. IV rank is the highest in the group (79.4) with the thinnest book (859 OI, $1.92 premium) — widest directional risk/IV decay profile. |

## 3. Operational health

- ❌ **Account C absent from `accounts_seen`** — present in status report (32 positions) but evaluated nothing. System-level flag, see "needs a decision."
- ⚠️ **49 swallowed `fetch_option_chain` HTTP errors** — repeated option-chain fetch failures during scanning; affects data quality of every option row above.
- ✅ `circuit_breaker_tripped: false`.
- ✅ `trading_day` = 2026-08-16 = today; decision pipeline is current (copied 2026-08-16 14:33 EDT, 1,717 decisions — full population scanned).
- ℹ️ First brief in this workspace — no prior snapshot to compare deltas against.

---

*Informational only — no trades placed, modified, or recommended. Verify the INTC offering and JOBY financing details against filings before the open if acting on those names.*
