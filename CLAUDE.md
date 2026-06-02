# Drift — Asian Session Scalper

Bot de forex autónomo. Estrategia: Asian Session Scalper en M15, mean reversion durante la ventana 21:00-02:00 hora servidor MT5 (GMT+3) sobre un rango definido en las primeras 2 horas. (El nombre engaña: en Bogotá es una franja de tarde, 13:00-18:00, no madrugada.) Opera en MetaTrader 5 con ICMarkets sobre 5 pares (AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP). Gestión de riesgo: 1% por trade, 10% max drawdown, sin trailing stop. Notificaciones y control vía Telegram.

## Key Documents

| Documento | Propósito |
|---|---|
| `ROADMAP.md` | Spec completo: visión, estrategia, fases, non-goals |
| `PROGRESS.md` | Checklist de implementación — leer primero en cada sesión |
| `docs/DECISIONS.md` | Decisiones de diseño con rationale — no re-litigar |
| `docs/ARCHITECTURE.md` | Diagramas, data flow, schema SQL, file structure |
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

```
main.py (M15 loop dentro de ventana 21:00-02:00 hora servidor MT5)
  → mt5_client.py    (datos de mercado)
  → indicators.py    (RSI, ATR, ADX)
  → strategy.py      (¿comprar/vender/nada?)
  → risk.py          (position sizing, límites)
  → executor.py      (abrir/cerrar trades)
  → trailing.py      (no activo — use_trailing_stop: false)
  → db.py            (logging completo)
  → telegram_bot.py  (notificaciones + comandos)
```

## Design Priorities

1. **Supervivencia** — riesgo 1% por trade, max 4 simultáneos, 10% drawdown = pausa
2. **Simplicidad** — 3 indicadores (RSI, ATR, ADX) sobre M15 + H4, nada más
3. **Transparencia** — logging completo de TODAS las señales (aceptadas y rechazadas)
4. **Autonomía** — 24/5 sin intervención, Telegram para monitoreo
5. **Cabeza fría** — cambios solo con datos y análisis, nunca por emoción

## Conventions

- Tres capas horarias: el cálculo de la ventana de sesión usa hora de servidor MT5 (GMT+3, sin DST), el almacenamiento en DB es UTC real (ISO 8601), y la presentación al usuario es UTC-5 (Bogotá)
- Cada módulo es un archivo independiente en `drift/`
- Config en `config.yaml`, nunca hardcodeado
- Credenciales no se versionan — usar `config.example.yaml` como template
- Magic number `234000` identifica órdenes de Drift en MT5
- Todas las fechas en DB se guardan en UTC (ISO 8601)

## Implementation Order

Follow `ROADMAP.md` Phase 1, steps 1-16. Check `PROGRESS.md` to know where to pick up.

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
| 8 | 22-26 | Go live: VPS, live account, monitoring |

### Parallelism

Steps that can run concurrently via subagents:
- Steps 5 + 11: indicators module and database module are independent
- Steps 9 + 10: executor and trailing stop are independent modules
- Steps 12 + 15: Telegram commands and weekly report logic are independent

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
You are implementing Drift, un bot de forex con estrategia Asian Session Scalper. Read CLAUDE.md first, then ROADMAP.md Phase 1. Check PROGRESS.md to know where to start. Begin with the next uncompleted step.
-->
