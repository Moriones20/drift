"""Tests for the risk engine.

Covers the legacy single-strategy helpers and the two-level notional risk
engine added for the multi-strategy platform (D053).
"""

from __future__ import annotations

import pytest

from drift.config import RiskGlobalConfig, StrategyRiskConfig
from drift.risk import (
    COMMISSION_PER_LOT,
    calculate_position_size,
    calculate_position_size_allocated,
    check_correlation,
    check_drawdown,
    check_max_trades,
    check_strategy_drawdown,
    check_strategy_risk,
    seed_baseline_capital,
    strategy_drawdown,
    strategy_equity,
)


def _trade(pair: str, direction: str) -> dict:
    return {"pair": pair, "direction": direction}


# ---------------------------------------------------------------------------
# calculate_position_size (legacy, regression)
# ---------------------------------------------------------------------------


def test_calculate_position_size_basic():
    # risk_amount = 10000 * 1% = 100; denom = 20*10 + 7 = 207; lot = 0.483 -> 0.48
    lot = calculate_position_size(10000, 1.0, 20, 10.0)
    assert lot == 0.48


def test_calculate_position_size_invalid_inputs_return_zero():
    assert calculate_position_size(10000, 1.0, 0, 10.0) == 0.0
    assert calculate_position_size(10000, 1.0, 20, 0.0) == 0.0


def test_calculate_position_size_below_minimum_returns_zero():
    # Tiny balance -> lot below 0.01
    assert calculate_position_size(10, 1.0, 20, 10.0) == 0.0


# ---------------------------------------------------------------------------
# check_max_trades / check_correlation / check_drawdown (legacy, regression)
# ---------------------------------------------------------------------------


def test_check_max_trades():
    assert check_max_trades([], 4)[0] is True
    assert check_max_trades([_trade("EURUSD", "buy")] * 4, 4)[0] is False


def test_check_correlation_blocks_same_direction():
    open_trades = [_trade("EURUSD", "buy"), _trade("EURGBP", "buy")]
    # Two trades already buying EUR; a third should be blocked at max_same=2
    ok, reason = check_correlation(open_trades, "EURJPY", "buy", 2)
    assert ok is False
    assert "EUR" in reason


def test_check_correlation_allows_opposite_currency_exposure():
    open_trades = [_trade("EURUSD", "buy")]
    ok, _ = check_correlation(open_trades, "GBPUSD", "sell", 2)
    assert ok is True


def test_check_drawdown():
    assert check_drawdown(900, 1000, 10.0)[0] is False  # exactly 10%
    assert check_drawdown(950, 1000, 10.0)[0] is True
    # invalid peak -> permissive
    assert check_drawdown(900, 0, 10.0)[0] is True


# ---------------------------------------------------------------------------
# calculate_position_size_allocated (D053 notional sizing)
# ---------------------------------------------------------------------------


def test_allocated_sizing_matches_reduced_balance():
    # balance 500, allocation 60% -> allocated capital 300; percent_per_trade 1%
    # -> risk on 300. Should equal sizing 1% on a 300 balance directly.
    direct = calculate_position_size(300, 1.0, 20, 10.0)
    allocated = calculate_position_size_allocated(500, 60, 1.0, 20, 10.0)
    assert allocated == direct


def test_allocated_sizing_notional_risk_amount():
    # balance 500, allocation 60, percent 1 -> risk_usd = 300 * 1% = 3.0
    # denom = 20*10 + 7 = 207; lot = 3/207 = 0.0144 -> floor to 0.01
    allocated = calculate_position_size_allocated(500, 60, 1.0, 20, 10.0)
    assert allocated == 0.01


def test_allocated_sizing_full_allocation_equals_plain():
    # allocation 100% must be identical to plain sizing on the full balance
    allocated = calculate_position_size_allocated(10000, 100, 1.0, 20, 10.0)
    plain = calculate_position_size(10000, 1.0, 20, 10.0)
    assert allocated == plain


def test_allocated_sizing_zero_allocation_returns_zero():
    assert calculate_position_size_allocated(10000, 0, 1.0, 20, 10.0) == 0.0


def test_allocated_sizing_negative_allocation_returns_zero():
    assert calculate_position_size_allocated(10000, -5, 1.0, 20, 10.0) == 0.0


def test_allocated_sizing_effective_risk_is_compounded():
    # Equivalent effective risk percent = allocation/100 * percent_per_trade
    # 50% allocation, 2% per trade == 1% of full balance.
    allocated = calculate_position_size_allocated(10000, 50, 2.0, 20, 10.0)
    equivalent = calculate_position_size(10000, 1.0, 20, 10.0)
    assert allocated == equivalent


# ---------------------------------------------------------------------------
# check_strategy_risk (D053 two-level gate)
# ---------------------------------------------------------------------------


def test_strategy_risk_blocks_on_per_strategy_cap():
    sr = StrategyRiskConfig(max_open_trades=2)
    rg = RiskGlobalConfig(max_open_trades=10, max_same_currency_direction=10)
    strat_trades = [_trade("EURUSD", "buy"), _trade("AUDNZD", "buy")]
    account_trades = list(strat_trades)
    ok, reason = check_strategy_risk(strat_trades, account_trades, "GBPJPY", "buy", sr, rg)
    assert ok is False
    assert reason.startswith("strategy")


def test_strategy_risk_blocks_on_global_cap_even_if_strategy_ok():
    sr = StrategyRiskConfig(max_open_trades=10)
    rg = RiskGlobalConfig(max_open_trades=3, max_same_currency_direction=10)
    strat_trades = [_trade("EURUSD", "buy")]
    # Account is full from OTHER strategies' positions
    account_trades = [
        _trade("EURUSD", "buy"),
        _trade("USDCAD", "sell"),
        _trade("AUDNZD", "buy"),
    ]
    ok, reason = check_strategy_risk(strat_trades, account_trades, "GBPCHF", "buy", sr, rg)
    assert ok is False
    assert reason.startswith("account")


def test_strategy_risk_blocks_on_global_correlation():
    sr = StrategyRiskConfig(max_open_trades=10)
    rg = RiskGlobalConfig(max_open_trades=10, max_same_currency_direction=2)
    # Two account positions already buying EUR (from any strategy)
    account_trades = [_trade("EURUSD", "buy"), _trade("EURGBP", "buy")]
    strat_trades = []  # this strategy has none
    ok, reason = check_strategy_risk(strat_trades, account_trades, "EURJPY", "buy", sr, rg)
    assert ok is False
    assert "correlation" in reason


def test_strategy_risk_correlation_is_account_wide_not_per_strategy():
    # Even with zero strategy positions, account-wide correlation still blocks.
    sr = StrategyRiskConfig(max_open_trades=10)
    rg = RiskGlobalConfig(max_open_trades=10, max_same_currency_direction=1)
    account_trades = [_trade("EURUSD", "buy")]
    ok, _ = check_strategy_risk([], account_trades, "EURGBP", "buy", sr, rg)
    assert ok is False


def test_strategy_risk_all_clear():
    sr = StrategyRiskConfig(max_open_trades=4)
    rg = RiskGlobalConfig(max_open_trades=8, max_same_currency_direction=2)
    strat_trades = [_trade("EURUSD", "buy")]
    account_trades = [_trade("EURUSD", "buy"), _trade("USDJPY", "sell")]
    ok, reason = check_strategy_risk(strat_trades, account_trades, "AUDNZD", "buy", sr, rg)
    assert ok is True
    assert reason == ""


# ---------------------------------------------------------------------------
# strategy_equity / strategy_drawdown / check_strategy_drawdown (D053)
# ---------------------------------------------------------------------------


def test_strategy_equity_sums_components():
    assert strategy_equity(300.0, 50.0, -20.0) == 330.0


def test_strategy_equity_baseline_only():
    assert strategy_equity(300.0, 0.0, 0.0) == 300.0


def test_strategy_drawdown_basic():
    # peak 1000, equity 900 -> 10%
    assert strategy_drawdown(900.0, 1000.0) == pytest.approx(0.10)


def test_strategy_drawdown_no_drawdown_at_or_above_peak():
    assert strategy_drawdown(1000.0, 1000.0) == 0.0
    # New high: negative drawdown is fine (caller treats <=0 as no brake)
    assert strategy_drawdown(1100.0, 1000.0) < 0


def test_strategy_drawdown_peak_zero_returns_zero():
    assert strategy_drawdown(0.0, 0.0) == 0.0
    assert strategy_drawdown(-50.0, 0.0) == 0.0


def test_check_strategy_drawdown_trips_at_limit():
    # baseline 300, realized -30, floating 0 -> equity 270; peak 300 -> 10%
    equity = strategy_equity(300.0, -30.0, 0.0)
    ok, reason = check_strategy_drawdown(equity, 300.0, 10.0)
    assert ok is False
    assert "strategy drawdown" in reason


def test_check_strategy_drawdown_within_limit():
    equity = strategy_equity(300.0, -15.0, 0.0)  # 5% down
    ok, _ = check_strategy_drawdown(equity, 300.0, 10.0)
    assert ok is True


def test_check_strategy_drawdown_recovery_clears_brake():
    # Strategy dipped then recovered: equity back above the limit threshold.
    peak = 300.0
    dipped = strategy_equity(300.0, -40.0, 0.0)  # 260, >13% down
    assert check_strategy_drawdown(dipped, peak, 10.0)[0] is False
    recovered = strategy_equity(300.0, -10.0, 5.0)  # 295, ~1.7% down
    assert check_strategy_drawdown(recovered, peak, 10.0)[0] is True


def test_check_strategy_drawdown_peak_zero_is_permissive():
    ok, reason = check_strategy_drawdown(0.0, 0.0, 10.0)
    assert ok is True
    assert reason == ""


def test_check_strategy_drawdown_floating_loss_trips_brake():
    # Realized flat, but a large floating loss alone trips the brake.
    equity = strategy_equity(300.0, 0.0, -45.0)  # 255 -> 15% down
    ok, _ = check_strategy_drawdown(equity, 300.0, 10.0)
    assert ok is False


# ---------------------------------------------------------------------------
# seed_baseline_capital — the double-count fix (D062)
# ---------------------------------------------------------------------------


def test_seed_baseline_removes_double_count_of_realized():
    # Balance already includes +200 of past realized P&L; allocation 100%.
    # The naive baseline (balance * alloc) would carry that +200, and
    # strategy_equity would add it AGAIN.  Seeding subtracts realized so the
    # equity at seed time equals exactly the allocated slice (no double count).
    balance, alloc, realized = 1200.0, 100.0, 200.0
    baseline = seed_baseline_capital(balance, alloc, realized)
    assert baseline == 1000.0  # 1200 - 200

    equity_at_seed = strategy_equity(baseline, realized, 0.0)
    assert equity_at_seed == 1200.0  # the slice, NOT slice + realized (1400)


def test_seed_baseline_forward_realized_counted_once():
    # Seed with 200 of past realized, then 50 more is realized forward.
    baseline = seed_baseline_capital(1200.0, 100.0, 200.0)  # 1000
    equity_forward = strategy_equity(baseline, 250.0, 0.0)  # realized now 200+50
    # Equity advanced by exactly the +50 forward realized, not by 2x anything.
    assert equity_forward == 1250.0


def test_seed_baseline_respects_allocation_fraction():
    # 50% allocation, no prior realized: baseline is half the balance.
    assert seed_baseline_capital(1000.0, 50.0, 0.0) == 500.0


def test_commission_constant_is_used_in_sizing():
    # Guard against accidental change of the commission constant affecting math.
    assert COMMISSION_PER_LOT == 7.0
