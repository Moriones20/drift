# Drift — Plataforma de estrategias de forex (MT5/ICMarkets)

Bot de forex autónomo, **plataforma multi-estrategia** sobre una cuenta MT5 con ICMarkets. El motor es genérico ("reloj por suscripción", D051): no conoce ventanas, solo cierres de vela; cada estrategia implementa el contrato `Strategy.on_bar`. Riesgo aislado por estrategia (presupuesto notional + drawdown propio), magic base+offset, brake global. Gestión de riesgo base: 1% por trade, 10% max drawdown, sin trailing stop. Notificaciones y control vía Telegram.

**Estrategias:**
- **`london_orb` (#2, ACTIVA) — Phase 2.8, en implementación.** London Opening-Range Breakout: momentum direccional en la apertura de Londres (rango 10:00-11:00 server → ruptura por cierre M15, R:R 1:1, flat 18:00) sobre GBPJPY/GBPUSD/EURJPY/EURUSD. Diseñada para invertir cada modo de falla del Lull. Diseño: D067-D069, `docs/knowledge/london-orb.md`. **Empezar en `PROGRESS.md` Phase 2.8 (Steps 43-50).**
- **`daily_lull` (#1, PAUSADO/disabled — D069).** Daily Lull Scalper M15, mean reversion en la franja ilíquida 21:00-02:00 server sobre 5 pares. Falló en vivo (~2 semanas demo, PF 0.01): TP=midpoint < spread de la franja ⇒ sin edge. Config preservada para A/B. Lecciones: `docs/knowledge/strategy-framework.md` §9, memoria `daily-lull-live-lessons`, D043/D046/D066. Reglas: `docs/knowledge/daily-lull-scalper.md`.

> **Framework multi-estrategia (D050, Phases 2.6/2.7 — completas).** El refactor de bot mono-estrategia a plataforma ya está hecho y validado e2e en paper (2026-06-10). Contrato `Strategy.on_bar`, motor reloj-por-suscripción, riesgo de dos niveles, backtest unificado y Telegram por estrategia: D050–D066, `docs/knowledge/strategy-framework.md`. **Añadir una estrategia nueva = seguir el checklist §7 de ese doc.**

## Key Documents

| Documento | Propósito |
|---|---|
| `ROADMAP.md` | Spec completo: visión, estrategia, fases, non-goals |
| `PROGRESS.md` | Checklist de implementación — leer primero en cada sesión |
| `docs/DECISIONS.md` | Decisiones de diseño con rationale — no re-litigar |
| `docs/ARCHITECTURE.md` | Diagramas, data flow, schema SQL, file structure |
| `docs/knowledge/strategy-framework.md` | Contrato `Strategy.on_bar`, motor reloj-por-suscripción, cómo añadir una estrategia (§7 checklist, §9 lecciones del Lull en vivo) |
| `docs/knowledge/london-orb.md` | **Estrategia activa #2 (`london_orb`):** reglas, params, tiempos, diferencias con el Lull (Phase 2.8) |
| `docs/knowledge/daily-lull-scalper.md` | Estrategia #1 (`daily_lull`, pausada): reglas y params — contraste histórico |
| `docs/knowledge/mt5-python-api.md` | Referencia de la API de MT5 con Python |
| `docs/knowledge/telegram-bot-setup.md` | Setup del bot de Telegram y formato de mensajes |
| `docs/knowledge/trend-following-indicators.md` | Fórmulas e implementación de EMA, MACD, ATR (**estrategia histórica — ver encabezado del archivo**) |
| `docs/user/runbook.md` | Comandos Git Bash para encender, apagar, monitorear y diagnosticar el bot |
| `docs/user/` | Documentación para el usuario (getting-started, configuration, commands) |

## Tech Stack

- **Python 3.10+** — lenguaje principal
- **MetaTrader 5** — broker connection via `MetaTrader5` PyPI package (Windows only)
- **pandas + pandas-ta** — data manipulation e indicadores técnicos
- **python-telegram-bot** — bot de Telegram (v20+, async)
- **SQLite** — base de datos local
- **Backtesting.py** — framework de backtesting
- **PyYAML** — configuración

## Core Architecture

Estructura **actual** (mono-estrategia). La Phase 2.6 generaliza `main.py` a un motor reloj-por-suscripción y mueve la lógica de `strategy.py` a `drift/strategies/daily_lull.py` bajo el contrato `Strategy.on_bar` (ver D050–D057 y `docs/ARCHITECTURE.md`):

```
main.py (M15 loop dentro de ventana 21:00-02:00 hora servidor MT5)
  → mt5_client.py    (datos de mercado)
  → indicators.py    (RSI, ATR, ADX)
  → strategy.py      (¿comprar/vender/nada?)   # → strategies/daily_lull.py en Phase 2.6
  → risk.py          (position sizing, límites)  # → dos niveles (global + estrategia) en Phase 2.6
  → executor.py      (abrir/cerrar trades)
  → trailing.py      (no activo — use_trailing_stop: false)
  → db.py            (logging completo)          # → columna strategy + strategy_state en Phase 2.6
  → telegram_bot.py  (notificaciones + comandos) # → por estrategia en Phase 2.6
```

## Design Priorities

1. **Supervivencia** — riesgo 1% por trade, max 4 simultáneos, 10% drawdown = pausa
2. **Simplicidad** — pocos indicadores por estrategia (el Lull usa RSI/ATR/ADX; `london_orb` solo ATR), nada de más
3. **El reward debe dominar al spread** — toda estrategia: R:R ≥ 1:1 que sobreviva al spread+slippage reales del par/hora, no al fill sin costo del backtest (lección L1 del Lull; ver `strategy-framework.md` §9)
4. **Transparencia** — logging completo de TODAS las señales (aceptadas y rechazadas)
5. **Autonomía** — 24/5 sin intervención, Telegram para monitoreo
6. **Cabeza fría** — cambios solo con datos y análisis, nunca por emoción

## Conventions

- Tres capas horarias: el cálculo de la ventana de sesión usa hora de servidor MT5 (GMT+2 invierno/+3 verano, anclado al cierre NY; ver D041), el almacenamiento en DB es UTC real (ISO 8601), y la presentación al usuario es UTC-5 (Bogotá)
- Cada módulo es un archivo independiente en `drift/`
- Config en `config.yaml`, nunca hardcodeado
- Credenciales no se versionan — usar `config.example.yaml` como template
- Magic number: `234000` es la **base**; cada estrategia suma su `magic_offset` (magic efectivo = base + offset, D052). El Daily Lull usa offset 0 → magic 234000. Único por estrategia, validado.
- Todas las fechas en DB se guardan en UTC (ISO 8601)

## Implementation Order

Check `PROGRESS.md` first to know where to pick up. **Estado actual:** Phases 1, 2, 2.5, 2.6, 2.7 completas; **próximo trabajo = Phase 2.8 (London ORB, Steps 43-50)**. Phase 3 (go-live) viene después. Para añadir/implementar una estrategia, seguir el checklist §7 de `docs/knowledge/strategy-framework.md`.

Commit after each completed step. After each step, update `PROGRESS.md` — check off the step and note anything relevant.

If something is ambiguous, make a decision, document it in `docs/DECISIONS.md`, and keep moving.

### Session batches

| Session | Steps | Scope |
|---|---|---|
| 1 | 1-4 | Foundation: scaffolding, config, MT5 connection, data fetching |
| 2 | 5-7 | Strategy + validation: indicators, quick backtest, strategy logic |
| 3 | 8-10 | Risk + execution: risk management, trades, trailing stops |
| 4 | 11-13 | Database + Telegram: SQLite, bot commands, main loop |
| 5 | 14-16 | Safety + testing: security systems, weekly report, end-to-end demo test |
| 6 | 17-19 | Backtesting setup and historical data |
| 7 | 20-21 | Backtest analysis and parameter optimization |
| MS-A | 27-28 | Multi-estrategia (Phase 2.6): contrato `Strategy` + refactor de config |
| MS-B | 29-31 | Multi-estrategia: port del Lull, migración DB, riesgo de dos niveles |
| MS-C | 32 | Multi-estrategia: motor genérico (reescritura del loop) |
| MS-D | 33-34 | Multi-estrategia: monitoreo por estrategia + Telegram por estrategia |
| MS-E | 35-36 | Multi-estrategia: backtest unificado + validación e2e en paper |
| ORB-A | 43-45 | London ORB (Phase 2.8): `LondonOrbParams` + `on_bar` + heartbeat + unit tests |
| ORB-B | 46-47 | London ORB: backtest unificado + calibración de params + revalidación de costos de ejecución |
| ORB-C | 48-50 | London ORB: config (pivote D069) + docs de usuario + validación e2e en paper |
| 8 | 22-26 | Go live: VPS, live account, monitoring (DESPUÉS de Phase 2.8) |

### Parallelism

Steps that can run concurrently via subagents:
- Steps 5 + 11: indicators module and database module are independent
- Steps 9 + 10: executor and trailing stop are independent modules
- Steps 12 + 15: Telegram commands and weekly report logic are independent
- Steps 29 + 30 + 31 (Phase 2.6): port del Lull, migración DB y riesgo de dos niveles son módulos independientes una vez existen el contrato (27) y la config (28). Step 35 (backtest) puede arrancar en paralelo en cuanto exista el port (29).
- Steps 46 + 45 (Phase 2.8): el backtest/calibración (46) puede correr en paralelo con los unit tests (45) en cuanto exista `on_bar` (44). Steps 48 (config) y 49 (docs de usuario) son independientes una vez calibrados los params (47).

## Context Management

Claude's effectiveness degrades as context grows. The main process is an
orchestrator — it delegates, tracks progress, and synthesizes.

The main process should NOT:
- Read large files directly — delegate to subagents
- Write large modules directly — delegate to subagents
- Debug complex issues inline — spawn a subagent to investigate
- Re-read files it already wrote unless debugging

The main process SHOULD:
- Track which steps are complete
- Review subagent results briefly
- Make cross-step decisions (refactoring, shared patterns)
- Manage commits

Subagent delegation pattern: For each step, spawn a subagent with only
the context it needs:
- The relevant docs/knowledge/ guide (don't load all guides into main context)
- The types/interfaces it depends on
- A clear description of what to build and where

Commits are context reset points. After each step, commit. If context
gets heavy mid-session, start a new session — pick up from the last commit.

## Code Quality

- Before writing a new utility, search the codebase for existing implementations. Reuse over rewrite.
- After completing every 3-4 steps, pause and review: are there patterns that should be extracted? Refactor into shared modules only when you see 3+ concrete usages, never preemptively.
- Run `ruff check .` and `ruff format .` after every step. Fix issues immediately.
- Keep functions short and single-purpose. If a function does two things, split it.
- No dead code, no commented-out code, no TODO comments without a corresponding decision in docs/DECISIONS.md.
- When a refactor touches existing tests, update them in the same commit.
- After major integration milestones (steps 13, 15) and the final step, run /simplify to review changed code for reuse, quality, and efficiency. Fix any issues found before continuing.

## Documentation Rules

When implementing or modifying a user-facing feature, you MUST update the corresponding docs in `docs/user/`:
- New feature → add or update the relevant doc page
- Changed behavior → update the doc to match
- New Telegram command → add to `docs/user/commands.md`
- Config changes → update `docs/user/configuration.md`
- Setup changes → update `docs/user/getting-started.md`

Docs are part of the definition of done. A feature without updated docs is not complete.

<!-- Implementation prompt:
You are implementing Drift, un bot de forex con estrategia Daily Lull Scalper. Read CLAUDE.md first, then ROADMAP.md Phase 1. Check PROGRESS.md to know where to start. Begin with the next uncompleted step.
-->
