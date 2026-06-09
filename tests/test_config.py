"""Tests for the multi-strategy config schema (Step 28 / D054).

Covers:
- Parsing of the new ``strategies:`` block and ``risk_global:`` block.
- Validation: magic uniqueness, allocation_pct sum, per-strategy max_open_trades cap.
- Compat shim: config.strategy, config.risk, config.pairs derive correctly from daily_lull.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from drift.config import (
    BrokerConfig,
    DriftConfig,
    ReportsConfig,
    RiskConfig,
    RiskGlobalConfig,
    StrategyConfig,
    StrategyInstanceConfig,
    StrategyRiskConfig,
    SystemConfig,
    TelegramConfig,
    load_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BROKER_RAW = {"server": "demo", "login": 1, "password": "x"}
_TELEGRAM_RAW = {"bot_token": "fake:TOKEN", "chat_id": "123"}

_DEFAULT_PARAMS = {
    "rsi_oversold": 35.0,
    "rsi_overbought": 65.0,
    "adx_max_threshold": 35.0,
    "session_start_hour": 21,
    "range_definition_hours": 2,
    "session_end_hour": 2,
    "range_atr_min": 1.0,
    "range_atr_max": 4.0,
    "sl_atr_mult": 2.5,
    "m15_rsi_period": 14,
    "m15_atr_period": 14,
    "h4_adx_period": 14,
}


def _make_yaml_config(*, extra_strategy: dict | None = None) -> dict:
    """Return a raw YAML-like dict representing a valid config."""
    cfg: dict = {
        "broker": _BROKER_RAW,
        "telegram": _TELEGRAM_RAW,
        "reports": {},
        "system": {},
        "risk_global": {
            "max_open_trades": 4,
            "max_drawdown_percent": 10.0,
            "max_same_currency_direction": 2,
        },
        "strategies": {
            "daily_lull": {
                "enabled": True,
                "magic_offset": 0,
                "allocation_pct": 100,
                "pairs": ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"],
                "risk": {
                    "percent_per_trade": 1.0,
                    "max_open_trades": 4,
                    "max_drawdown_percent": 10.0,
                },
                "params": dict(_DEFAULT_PARAMS),
            }
        },
    }
    if extra_strategy:
        cfg["strategies"].update(extra_strategy)
    return cfg


def _write_and_load(cfg_dict: dict) -> DriftConfig:
    """Write cfg_dict to a temp YAML file and load it with load_config."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.dump(cfg_dict, f)
        tmp_path = f.name
    try:
        return load_config(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _make_direct_config(**overrides) -> DriftConfig:
    """Build a DriftConfig directly (bypassing YAML parsing) for shim tests."""
    defaults = dict(
        broker=BrokerConfig(server="demo", login=1, password="x"),
        telegram=TelegramConfig(bot_token="fake:TOKEN", chat_id="123"),
        reports=ReportsConfig(),
        system=SystemConfig(),
        risk_global=RiskGlobalConfig(),
        strategies={
            "daily_lull": StrategyInstanceConfig(
                name="daily_lull",
                enabled=True,
                magic_offset=0,
                allocation_pct=100.0,
                pairs=["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"],
                risk=StrategyRiskConfig(
                    percent_per_trade=1.0,
                    max_open_trades=4,
                    max_drawdown_percent=10.0,
                ),
                params=dict(_DEFAULT_PARAMS),
            )
        },
    )
    defaults.update(overrides)
    return DriftConfig(**defaults)


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


class TestStrategiesParsing(unittest.TestCase):
    def test_strategies_block_parsed(self) -> None:
        config = _write_and_load(_make_yaml_config())
        self.assertIn("daily_lull", config.strategies)

    def test_strategy_instance_fields(self) -> None:
        config = _write_and_load(_make_yaml_config())
        lull = config.strategies["daily_lull"]

        self.assertEqual(lull.name, "daily_lull")
        self.assertTrue(lull.enabled)
        self.assertEqual(lull.magic_offset, 0)
        self.assertAlmostEqual(lull.allocation_pct, 100.0)
        self.assertEqual(lull.pairs, ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"])

    def test_strategy_risk_fields(self) -> None:
        config = _write_and_load(_make_yaml_config())
        sr = config.strategies["daily_lull"].risk

        self.assertAlmostEqual(sr.percent_per_trade, 1.0)
        self.assertEqual(sr.max_open_trades, 4)
        self.assertAlmostEqual(sr.max_drawdown_percent, 10.0)

    def test_params_are_preserved_as_raw_dict(self) -> None:
        config = _write_and_load(_make_yaml_config())
        params = config.strategies["daily_lull"].params

        self.assertIsInstance(params, dict)
        self.assertAlmostEqual(params["rsi_oversold"], 35.0)
        self.assertAlmostEqual(params["sl_atr_mult"], 2.5)
        self.assertEqual(params["m15_rsi_period"], 14)

    def test_risk_global_parsed(self) -> None:
        config = _write_and_load(_make_yaml_config())
        rg = config.risk_global

        self.assertEqual(rg.max_open_trades, 4)
        self.assertAlmostEqual(rg.max_drawdown_percent, 10.0)
        self.assertEqual(rg.max_same_currency_direction, 2)

    def test_disabled_strategy_still_parsed(self) -> None:
        cfg = _make_yaml_config()
        cfg["strategies"]["daily_lull"]["enabled"] = False
        # Add a second enabled strategy so validation passes.
        cfg["strategies"]["other"] = {
            "enabled": True,
            "magic_offset": 1,
            "allocation_pct": 50,
            "pairs": ["EURUSD"],
            "risk": {"percent_per_trade": 1.0, "max_open_trades": 4, "max_drawdown_percent": 10.0},
            "params": {},
        }
        cfg["strategies"]["daily_lull"]["allocation_pct"] = 50
        config = _write_and_load(cfg)
        self.assertFalse(config.strategies["daily_lull"].enabled)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidationMagicUniqueness(unittest.TestCase):
    def test_duplicate_magic_offset_raises(self) -> None:
        extra = {
            "other": {
                "enabled": True,
                "magic_offset": 0,  # same as daily_lull → duplicate effective magic
                "allocation_pct": 0,
                "pairs": ["EURUSD"],
                "risk": {
                    "percent_per_trade": 1.0,
                    "max_open_trades": 4,
                    "max_drawdown_percent": 10.0,
                },
                "params": {},
            }
        }
        cfg = _make_yaml_config(extra_strategy=extra)
        cfg["strategies"]["daily_lull"]["allocation_pct"] = 100
        with self.assertRaises(ValueError) as ctx:
            _write_and_load(cfg)
        self.assertIn("Duplicate effective magic number", str(ctx.exception))

    def test_unique_magic_offsets_passes(self) -> None:
        extra = {
            "other": {
                "enabled": True,
                "magic_offset": 1,  # different offset
                "allocation_pct": 0,
                "pairs": ["EURUSD"],
                "risk": {
                    "percent_per_trade": 1.0,
                    "max_open_trades": 4,
                    "max_drawdown_percent": 10.0,
                },
                "params": {},
            }
        }
        cfg = _make_yaml_config(extra_strategy=extra)
        cfg["strategies"]["daily_lull"]["allocation_pct"] = 100
        # Should not raise.
        config = _write_and_load(cfg)
        self.assertIn("other", config.strategies)


class TestValidationAllocationPct(unittest.TestCase):
    def test_allocation_sum_exactly_100_passes(self) -> None:
        config = _write_and_load(_make_yaml_config())
        total = sum(s.allocation_pct for s in config.strategies.values() if s.enabled)
        self.assertAlmostEqual(total, 100.0)

    def test_allocation_sum_less_than_100_passes(self) -> None:
        cfg = _make_yaml_config()
        cfg["strategies"]["daily_lull"]["allocation_pct"] = 60
        config = _write_and_load(cfg)
        lull = config.strategies["daily_lull"]
        self.assertAlmostEqual(lull.allocation_pct, 60.0)

    def test_allocation_sum_over_100_raises(self) -> None:
        extra = {
            "other": {
                "enabled": True,
                "magic_offset": 1,
                "allocation_pct": 50,  # 100 + 50 = 150% > 100
                "pairs": ["EURUSD"],
                "risk": {
                    "percent_per_trade": 1.0,
                    "max_open_trades": 4,
                    "max_drawdown_percent": 10.0,
                },
                "params": {},
            }
        }
        cfg = _make_yaml_config(extra_strategy=extra)
        cfg["strategies"]["daily_lull"]["allocation_pct"] = 100
        with self.assertRaises(ValueError) as ctx:
            _write_and_load(cfg)
        self.assertIn("allocation_pct", str(ctx.exception))
        self.assertIn("100", str(ctx.exception))

    def test_disabled_strategy_excluded_from_allocation_sum(self) -> None:
        """A disabled strategy with allocation_pct=100 does not count toward the sum."""
        extra = {
            "other": {
                "enabled": False,  # disabled
                "magic_offset": 1,
                "allocation_pct": 100,
                "pairs": ["EURUSD"],
                "risk": {
                    "percent_per_trade": 1.0,
                    "max_open_trades": 4,
                    "max_drawdown_percent": 10.0,
                },
                "params": {},
            }
        }
        cfg = _make_yaml_config(extra_strategy=extra)
        cfg["strategies"]["daily_lull"]["allocation_pct"] = 100
        # Only daily_lull is enabled; sum = 100 — should pass.
        config = _write_and_load(cfg)
        self.assertFalse(config.strategies["other"].enabled)


class TestValidationMaxOpenTrades(unittest.TestCase):
    def test_strategy_max_open_trades_exceeds_global_raises(self) -> None:
        cfg = _make_yaml_config()
        cfg["strategies"]["daily_lull"]["risk"]["max_open_trades"] = 10
        cfg["risk_global"]["max_open_trades"] = 4
        with self.assertRaises(ValueError) as ctx:
            _write_and_load(cfg)
        self.assertIn("max_open_trades", str(ctx.exception))
        self.assertIn("risk_global", str(ctx.exception))

    def test_strategy_max_open_trades_equal_to_global_passes(self) -> None:
        cfg = _make_yaml_config()
        cfg["strategies"]["daily_lull"]["risk"]["max_open_trades"] = 4
        cfg["risk_global"]["max_open_trades"] = 4
        config = _write_and_load(cfg)
        self.assertEqual(config.strategies["daily_lull"].risk.max_open_trades, 4)


class TestValidationAtLeastOneEnabled(unittest.TestCase):
    def test_no_enabled_strategy_raises(self) -> None:
        cfg = _make_yaml_config()
        cfg["strategies"]["daily_lull"]["enabled"] = False
        with self.assertRaises(ValueError) as ctx:
            _write_and_load(cfg)
        self.assertIn("At least one strategy", str(ctx.exception))


class TestValidationRiskRanges(unittest.TestCase):
    def test_percent_per_trade_zero_raises(self) -> None:
        cfg = _make_yaml_config()
        cfg["strategies"]["daily_lull"]["risk"]["percent_per_trade"] = 0.0
        with self.assertRaises(ValueError):
            _write_and_load(cfg)

    def test_percent_per_trade_above_5_raises(self) -> None:
        cfg = _make_yaml_config()
        cfg["strategies"]["daily_lull"]["risk"]["percent_per_trade"] = 6.0
        with self.assertRaises(ValueError):
            _write_and_load(cfg)

    def test_drawdown_above_50_raises(self) -> None:
        cfg = _make_yaml_config()
        cfg["strategies"]["daily_lull"]["risk"]["max_drawdown_percent"] = 51.0
        with self.assertRaises(ValueError):
            _write_and_load(cfg)


# ---------------------------------------------------------------------------
# Shim tests — config.strategy, config.risk, config.pairs
# ---------------------------------------------------------------------------


class TestCompatShim(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _make_direct_config()

    def test_shim_strategy_is_strategy_config(self) -> None:
        self.assertIsInstance(self.config.strategy, StrategyConfig)

    def test_shim_strategy_derives_params(self) -> None:
        sc = self.config.strategy
        self.assertAlmostEqual(sc.rsi_oversold, 35.0)
        self.assertAlmostEqual(sc.rsi_overbought, 65.0)
        self.assertAlmostEqual(sc.adx_max_threshold, 35.0)
        self.assertEqual(sc.session_start_hour, 21)
        self.assertEqual(sc.range_definition_hours, 2)
        self.assertEqual(sc.session_end_hour, 2)
        self.assertAlmostEqual(sc.range_atr_min, 1.0)
        self.assertAlmostEqual(sc.range_atr_max, 4.0)
        self.assertAlmostEqual(sc.sl_atr_mult, 2.5)
        self.assertEqual(sc.m15_rsi_period, 14)
        self.assertEqual(sc.m15_atr_period, 14)
        self.assertEqual(sc.h4_adx_period, 14)

    def test_shim_risk_is_risk_config(self) -> None:
        self.assertIsInstance(self.config.risk, RiskConfig)

    def test_shim_risk_derives_from_strategy_and_global(self) -> None:
        rc = self.config.risk
        self.assertAlmostEqual(rc.percent_per_trade, 1.0)
        self.assertEqual(rc.max_open_trades, 4)
        self.assertAlmostEqual(rc.max_drawdown_percent, 10.0)
        # max_same_currency_direction comes from risk_global (D053)
        self.assertEqual(rc.max_same_currency_direction, 2)

    def test_shim_pairs_from_strategy(self) -> None:
        self.assertEqual(
            self.config.pairs,
            ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"],
        )

    def test_shim_strategy_custom_params(self) -> None:
        """Custom params in the strategies dict flow through the shim correctly."""
        config = _make_direct_config(
            strategies={
                "daily_lull": StrategyInstanceConfig(
                    name="daily_lull",
                    enabled=True,
                    magic_offset=0,
                    allocation_pct=100.0,
                    pairs=["EURUSD"],
                    risk=StrategyRiskConfig(percent_per_trade=2.0),
                    params=dict(_DEFAULT_PARAMS, rsi_oversold=30.0),
                )
            }
        )
        self.assertAlmostEqual(config.strategy.rsi_oversold, 30.0)
        self.assertAlmostEqual(config.risk.percent_per_trade, 2.0)
        self.assertEqual(config.pairs, ["EURUSD"])

    def test_shim_no_daily_lull_returns_defaults(self) -> None:
        """If daily_lull is absent (edge case), shim falls back to defaults."""
        config = DriftConfig(
            broker=BrokerConfig(server="demo", login=1, password="x"),
            telegram=TelegramConfig(bot_token="fake:TOKEN", chat_id="123"),
            reports=ReportsConfig(),
            system=SystemConfig(),
            risk_global=RiskGlobalConfig(),
            strategies={
                "other": StrategyInstanceConfig(
                    name="other",
                    enabled=True,
                    magic_offset=1,
                    allocation_pct=100.0,
                    pairs=["EURUSD"],
                    risk=StrategyRiskConfig(),
                    params={},
                )
            },
        )
        self.assertIsInstance(config.strategy, StrategyConfig)
        self.assertIsInstance(config.risk, RiskConfig)
        self.assertIsInstance(config.pairs, list)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
