# Orchestration status — Phase 2.8 London ORB

- Plan: PROGRESS.md Phase 2.8 (Steps 43-50) + ROADMAP.md Phase 2.8
- Design: docs/DECISIONS.md D067-D069, docs/knowledge/london-orb.md
- Branch: feat/london-orb (design docs committed c89d719)
- Started: 2026-06-19
- Last updated: 2026-06-20
- Plan status: implementation COMPLETE (code+backtest+config committed). Backtest verdict: NO EDGE (PF 0.82). Deployed to paper as system+fidelity validation (D070). Step 50 = user runs ~1 week of demo, then decides archive vs H1/H4 trend filter. Step 47 superseded by live demo.
- Commits (feat/london-orb): c89d719 spec docs · aba10ff strategy · dc01bf0 boundary-parity fix · e84eec2 engine broker-TP · 472f8c2 runner · ad9424e geometry re-anchor · e73f834 D070 docs · 3382307 D071 dual-strategy. config.yaml edited (gitignored).
- 2026-06-20 UPDATE (D071): user opted to run BOTH daily_lull + london_orb in paper ~2 weeks, independent (global max_open 4->8, alloc 50/50). config.yaml validated (both enabled, sum 100, caps ok). Review ~2026-07-06. Lull observed (not fixed — R:R<spread structural); ORB system+fidelity. Awaiting user bot restart to load config.

## Waves

- Wave 1 — ORB-A (Steps 43-45 + 44b): chunk 1. DONE (verified)
- Wave 2 — ORB-B (Steps 46-47): split — chunk 2a (generalize engine, opus), chunk 2b (runner+calibration, sonnet), chunk 3 (spread revalidation). 2a IN PROGRESS. Data for all 4 pairs downloaded (GBPUSD/EURUSD M15 fetched from MT5 demo, local/gitignored).
- Wave 3 — ORB-C (Steps 48-49): chunk 4 (config pivot), chunk 5 (user docs). PENDING
- Step 50 (e2e paper validation): MANUAL (needs live MT5). PENDING

## Chunks

> Design pivot (user decision 2026-06-19): backtest showed realized payoff ~0.55 (entry fills past breakout level, TP anchored at range extreme). Re-anchor geometry to curr_close (SL/TP measured from the breakout price, unit = range_width) to restore R:R = tp_mult:1. Experiment chunk 1g below; D070 + docs after the result.


| # | Plan step | Repo | Model | Wave | Status | Commit | Notes |
|---|---|---|---|---|---|---|---|
| 1 | 43-45 + 44b — london_orb module + tests + heartbeat | drift | sonnet x2 | 1 | DONE | aba10ff + dc01bf0 | verified. Failed verify #1 (window keyed on boundary not candle hour → live/backtest divergence); fixed in dc01bf0 with regression test. Engine 18:00 heartbeat already generic (no change). 23 ORB tests + full suite 354 pass. |
| 2a | 46 — generalize backtest engine (broker TP + active-hours + types) | drift | opus | 2 | DONE | e84eec2 | verified: ruff clean, 361 fast + 11 slow Lull equivalence tests pass (byte-identical). Agent died pre-commit; orchestrator committed verified work. |
| 2b | 46 — run_orb runner + 4-pair backtest + calibration recommendation | drift | sonnet | 2 | DONE | 472f8c2 | verified correct (server-time + entry-hours confirmed). FINDING: baseline portfolio -0.6% PF 0.88; no pair OOS PF>=1.2; realized payoff ~0.55 (entry fills past breakout level). Win rate ~61%. |
| 1g | geometry re-anchor experiment (curr_close-based SL/TP) + tests + re-backtest | drift | sonnet | 2 | DONE | ad9424e | verified. Payoff 0.55→0.77-0.85 (geometry honest now) BUT win rate 61%→50% (old WR was an artifact of far-stop+tiny-target). Portfolio PF 0.82, no pair OOS PF>=1.2. EDGE NOT RESTORED — signal is weak. Go/no-go pending. |
| 3 | 47 — execution-cost revalidation (inflated London spread) | drift | sonnet | 2 | BLOCKED | — | moot until base geometry has an edge — adding spread to PF 0.88 only worsens it. Awaiting 1g result. |
| 4 | 48 — config pivot (lull off, orb on) | drift | sonnet | 3 | PENDING | — | real config.yaml + validation |
| 5 | 49 — user docs | drift | sonnet | 3 | PENDING | — | configuration/commands/getting-started |
