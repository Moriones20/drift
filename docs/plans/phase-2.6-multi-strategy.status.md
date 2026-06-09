# Orchestration status — Phase 2.6 Plataforma multi-estrategia

- Plan: `ROADMAP.md` Phase 2.6 (steps 27-36) + `docs/DECISIONS.md` D050-D057 + `docs/knowledge/strategy-framework.md`
- Branch: `feat/multi-strategy-platform` (baseline `15b519e`)
- Started: 2026-06-08
- Last updated: 2026-06-08
- Plan status: complete — 11/11 chunks DONE (Steps 27-35). Step 36 (validación e2e en paper) = manual, pendiente. Suite: 282 passed, 2 skipped (slow opt-in), ruff limpio.
- Decisión C9: alcance núcleo — engine + run_lull/optimize; lull_engine.py se conserva (analyze_*/validate_oos siguen usándolo); migración completa del tooling de research = follow-up. C10 NO elimina lull_engine.py.
- C6b follow-up: fix be8f297 (_shutdown cierra por magic efectivo de cada estrategia, D052).
- Nota de implementación: expand/contract para mantener verde — C2 añade esquema nuevo + shim transitorio (config.strategy/.risk/.pairs derivados de daily_lull); C4/C5 aditivos; C6 hace el switch y elimina el shim + strategy.py viejo.

## Waves

- Wave 1 (seq — fundación): DONE
- Wave 2 (parallel, worktrees — módulos disjuntos): DONE (cherry-picked ad7ff3c, 179e12f, e87d246; verificado)
- Wave 3 (seq — motor): DONE (C6a aa6485f, C6b 7082ab0 + fix be8f297)
- Wave 4 (seq — control): IN PROGRESS (C7→C8)
- Wave 5 (seq — backtest): DONE (C9 6e36730)
- Wave 6 (seq — cleanup): DONE (C10 8973aca)
- Step 36 (validación e2e en paper): manual, fuera de orquestación

## Chunks

| # | Plan step | Archivo principal | Model | Wave | Status | Commit | Notes |
|---|---|---|---|---|---|---|---|
| C1 | 27 — contrato Strategy + registry | drift/strategies/base.py | opus | 1 | DONE | 4840e1c | verificado; 144 tests OK |
| C2 | 28 — refactor config strategies[] | drift/config.py | sonnet | 1 | DONE | eb3867f | verificado; 169 tests OK |
| C3 | 29 — port Daily Lull al contrato | drift/strategies/daily_lull.py | opus | 2 | DONE | ad7ff3c | verificado; equivalencia OK; +on_fill/on_order_rejected |
| C4 | 30 — migración DB strategy/strategy_state | drift/db.py | sonnet | 2 | DONE | 179e12f | verificado; migración idempotente |
| C5 | 31 — riesgo de dos niveles | drift/risk.py | opus | 2 | DONE | e87d246 | verificado; aditivo |
| C6a | 32a — next_wake + port timing | drift/strategies/daily_lull.py | opus | 3 | DONE | aa6485f | equivalencia 1:1 vs main.py; D058 |
| C6b | 32b — motor (engine.py + loop) | drift/engine.py, main.py | opus | 3 | DONE | 7082ab0,be8f297 | verificado; fix _shutdown magic |
| C7 | 33 — monitoreo por estrategia | main.py, drift/engine.py | opus | 4 | DONE | f46ef23 | verificado; pausa coordinada vía strategy_state |
| C8 | 34 — Telegram por estrategia | drift/telegram_bot.py, drift/report.py, main.py | sonnet | 4 | DONE | 1631ecc | /pause [x], /strategies, desglose, reporte |
| C9 | 35 — backtest unificado | backtest/engine.py | opus | 5 | DONE | 6e36730 | equivalencia bit-exact; D059; lull_engine.py conservado |
| C10 | cleanup — quitar shim+strategy.py+golden | varios | sonnet | 6 | DONE | 8973aca | strategy.py + shim eliminados; golden tests; lull_engine.py conservado |
