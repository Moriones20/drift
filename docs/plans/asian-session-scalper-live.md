# Plan: Migrate Live Bot to Asian Session Scalper

**Status**: Draft — pending validation from `backtest/optimize_asian.py` results
**Owner**: To be assigned to a Sonnet agent via the `orchestrate` skill
**Decision**: Replace existing MLP mean reversion strategy (H4) with Asian Session Scalper (M15). Do not coexist.

## Goal

Replace the live bot's H4 BB+RSI+MLP mean reversion strategy with the Asian Session Scalper (M15, 21:00-02:00 GMT window). The backtest engine at `backtest/asian_engine.py` is the source of truth for strategy logic — port it to live code while keeping the existing risk, execution, database, and Telegram infrastructure.

## Why now

- Asian Scalper produced EURCHF PF 8.17, AUDNZD PF 4.29 on 2 years real MT5 M15 data (vs MLP mean reversion's best PF 1.74).
- Existing infrastructure (`risk.py`, `executor.py`, `db.py`, `telegram_bot.py`) is strategy-agnostic and reusable.
- Mean reversion + MLP code can be removed cleanly — no need to keep two strategies running.

## Reference files (read these first)

- `backtest/asian_engine.py` — strategy class `AsianSessionStrategy`. The `next()` method is the spec.
- `backtest/optimize_asian.py` — final parameter values to use (will be filled in after optimization completes).
- `drift/strategy.py` — current strategy module to replace.
- `main.py` — main loop scheduler to modify.
- `drift/config.py` — config dataclasses to extend.
- `docs/ARCHITECTURE.md` — system diagram (will need updating).
- `docs/DECISIONS.md` — append a new decision (D029) explaining the strategy switch.

## Implementation steps

### Step 1 — Update config schema (small, sequential)

**Files**: `drift/config.py`, `config.example.yaml`, `docs/user/configuration.md`

Add to `StrategyConfig`:
```python
session_start_hour: int = 21          # GMT hour to start observing range
range_definition_hours: int = 2        # 21:00-23:00 = define range
session_end_hour: int = 2             # GMT hour to close all trades
range_atr_min: float = 1.0            # minimum range width as multiple of ATR
range_atr_max: float = 3.0            # maximum range width
sl_atr_mult: float = 1.5              # SL distance (will replace existing 2.0)
m15_rsi_period: int = 14
m15_atr_period: int = 14
h4_adx_period: int = 14
```

Remove from `StrategyConfig` (no longer used): `bb_period`, `bb_std_dev`, `mlp_*`, `take_profit_mode`, `max_trade_duration_hours`.

Update `config.example.yaml` with the new fields. Default pairs becomes:
```yaml
pairs: [EURCHF, EURGBP, AUDNZD, USDJPY, GBPJPY, EURJPY]
```

Update `docs/user/configuration.md` to match.

### Step 2 — Port strategy logic to drift/strategy.py (medium, sequential)

**Files**: `drift/strategy.py`, `drift/indicators.py`

Replace contents of `drift/strategy.py` with an Asian Session Scalper implementation. Logic mirrors `AsianSessionStrategy.next()` in `backtest/asian_engine.py` lines 160-258.

Key data classes:
```python
@dataclass
class SessionState:
    session_date: int | None = None      # ordinal of session-start day
    high: float = float("-inf")
    low: float = float("inf")
    locked: bool = False
    range_high: float = float("nan")
    range_low: float = float("nan")
    traded: bool = False

@dataclass
class Signal:
    action: Literal["buy", "sell", "none"]
    pair: str
    entry_price: float
    sl: float
    tp: float
    range_high: float
    range_low: float
    rsi: float
    atr: float
    h4_adx: float
    rejection_reason: str | None = None
```

Public functions:
- `evaluate_pair(symbol, m15_df, h4_df, session_state, config) -> Signal`
- `update_session_state(state, current_bar_time, current_bar_high, current_bar_low, atr) -> SessionState`
- `should_close_on_time(current_bar_time, config) -> bool` (true at session_end_hour)
- `check_tp_hit(position, current_close, range_midpoint) -> bool`

Indicators (`drift/indicators.py`): make sure RSI and ATR use Wilder's smoothing (already correct from backtest fixes). Add an `adx()` function if not present (copy from `backtest/_indicators.py`).

### Step 3 — Refactor main loop scheduler (medium, depends on Step 1+2)

**Files**: `main.py`

Current behavior: wait for H4 close, evaluate, sleep.

New behavior:
1. On startup, check if we're in the trading window (21:00-02:00 GMT). If not, sleep until next 21:00.
2. Inside the window: every M15 close (21:00, 21:15, 21:30, ...), fetch latest M15 + H4 bars per pair, call `strategy.evaluate_pair()`, act on signal.
3. Maintain `dict[str, SessionState]` per pair, reset at every 21:00 boundary.
4. At 02:00 GMT, close any open trades and sleep until next 21:00.

Keep the existing monitoring daemon (drawdown check, MT5 health check) running every 30s.

Time zone handling: all internal logic in UTC. Telegram notifications continue using UTC-5 for display (existing pattern).

### Step 4 — Update mt5_client.py for M15 data (small, can parallelize with Step 3)

**Files**: `drift/mt5_client.py`

Add M15 timeframe support if not present:
```python
TIMEFRAMES = {
    "M15": mt5.TIMEFRAME_M15,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}
```

`get_candles()` should accept timeframe parameter. Ensure it returns enough bars for indicator warmup (M15 needs ~100 bars for ATR/RSI to stabilize; H4 needs ~30 for ADX).

### Step 5 — Update database signals table (small, sequential after Step 2)

**Files**: `drift/db.py`

Migrate `signals` table columns to match new fields:
- Add: `range_high`, `range_low`, `range_atr_ratio`, `m15_rsi`, `m15_atr`, `h4_adx`, `session_traded_count`
- Remove: `bb_upper`, `bb_middle`, `bb_lower`, `regime_probability` (if present)
- Keep: `id`, `pair`, `analyzed_at`, `decision`, `reason`, `trade_id`

Use the existing migration pattern (rename old table to `signals_v3`, create new). Update `log_signal()` to write the new fields.

### Step 6 — Adapt risk and executor (minimal, depends on Step 2)

**Files**: `drift/risk.py`, `drift/executor.py`

These are mostly strategy-agnostic but verify:
- `risk.position_size()` works with M15 ATR-based SL (it already takes `sl_pips` so just pass `1.5 * atr_pips`).
- Correlation check counts positions correctly across the new pair set including JPY crosses.
- `executor.open_trade()` accepts the new TP (range midpoint price, not a ratio).
- Friday close logic still works — if we're trading at 22:00 UTC Friday, we should still close before 20:00 UTC.

**Trailing stops**: keep `use_trailing_stop: false` for this strategy.

### Step 7 — Update tests (medium, can parallelize with implementation)

**Files**: `tests/test_strategy.py`, `tests/test_e2e.py`

Replace tests that reference the old BB+RSI+MLP strategy with tests for the new Asian Scalper:
- Session state lifecycle (reset at 21:00, lock at 23:00, reset trade flag)
- Time-window gating (no trades at 12:00 UTC, trades at 23:00 UTC)
- Range validation (1x-3x ATR filter)
- Time stop at 02:00 closes open position
- TP at range midpoint
- ADX H4 filter blocks trades when trending

End-to-end test: simulate one full session with mock MT5 data, verify a single trade executes.

### Step 8 — Update docs (small, sequential after implementation)

**Files**: `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/knowledge/asian-session-scalper.md` (new), `docs/user/getting-started.md`, `docs/user/configuration.md`, `CLAUDE.md`, `ROADMAP.md`

- Add D029 to DECISIONS.md explaining the switch from MLP mean reversion → Asian Session Scalper with the backtest evidence.
- Update ARCHITECTURE.md system diagram and data-flow section for M15 + session windowing.
- Create `docs/knowledge/asian-session-scalper.md` documenting the strategy rules, pairs, time windows, and known failure modes.
- Update CLAUDE.md's "Drift" tagline and "Strategy" section.
- Update ROADMAP.md non-goals if needed.

### Step 9 — Cleanup (small, last)

Remove dead code from the MLP era:
- `drift/mlp_filter.py`
- `backtest/train_mlp.py`
- `data/regime_model.pkl`
- `backtest/engine.py` and `backtest/run_all.py` (old mean reversion — keep only if we want historical comparison; mark deprecated otherwise)

Decision needed: keep old backtest engine for comparison reference, or delete? **Recommended**: keep but rename to `engine_meanrev_legacy.py` so anyone reading the repo understands its status.

## Parallelism plan (for orchestrate skill)

Sequential dependencies:
- Step 1 (config) → Step 2 (strategy) → Step 3 (main loop) and Step 5 (db) and Step 6 (risk/exec)
- Step 7 (tests) can run in parallel with Steps 3, 5, 6 once Step 2 is done

Two waves:
1. **Wave 1**: Step 1 + Step 4 (parallel — config and mt5_client are independent)
2. **Wave 2**: Step 2 (strategy module) — depends on wave 1
3. **Wave 3**: Steps 3 + 5 + 6 + 7 (parallel — main loop, db, risk, tests)
4. **Wave 4**: Step 8 (docs) + Step 9 (cleanup)

Each wave should commit before the next starts.

## Final parameters (TO BE FILLED after optimization)

Once `backtest/optimize_asian.py` completes, fill in here:

```yaml
# Universal parameters (from optimize_asian.py)
sl_atr_mult: TBD
adx_max_threshold: TBD
rsi_oversold: TBD
rsi_overbought: TBD
range_atr_min: TBD
range_atr_max: TBD

# Pairs that passed KEEP criteria (PF>1.5, WR>50%, MaxDD>-5%, Trades>=20)
pairs: TBD
```

If per-pair optimization shows large variance, also document per-pair overrides.

## Pre-live checklist

Before deploying to live (Phase 3 of ROADMAP):
- [ ] All 9 implementation steps complete and committed
- [ ] All tests passing (`pytest`)
- [ ] `ruff check .` and `ruff format .` clean
- [ ] Manual end-to-end test on demo account for at least 3 trading sessions
- [ ] Telegram notifications verified for: trade open, trade close, session start/end, drawdown alert
- [ ] Weekly report adapted to new metrics (no BB references)
- [ ] Friday close logic verified at week boundary

## Risks and mitigations

1. **Higher query rate to MT5**: 20 evaluations per night × 6 pairs = 120 candle fetches per session. Mitigation: fetch all 6 pairs in one batch per M15 tick, not sequentially.

2. **Session range mis-detection at DST boundaries**: GMT does not observe DST, but pairs trade against currencies that do. Mitigation: explicitly compare to UTC hours, never local.

3. **Synthetic vs real M15 data**: backtest used real MT5 M15 data. Live will use real. Should match. Watch for first-week divergences.

4. **JPY pair pip value differences**: JPY pairs have pip value at 2nd decimal (not 4th). Verify `risk.position_size()` handles this. Reference `docs/knowledge/mt5-python-api.md` for `symbol_info.point`.

5. **Holiday/illiquid sessions**: Japanese holidays (Golden Week, New Year) make spreads explode. Mitigation later (Phase 3+) — for now, fixed SL protects.
