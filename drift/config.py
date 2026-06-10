from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class BrokerConfig:
    server: str
    login: int
    password: str


@dataclass
class RiskConfig:
    percent_per_trade: float = 1.0
    max_open_trades: int = 4
    max_same_currency_direction: int = 2
    max_drawdown_percent: float = 10.0
    trailing_stop_atr_multiplier: float = 1.5
    use_trailing_stop: bool = False


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass
class ReportsConfig:
    timezone: str = "UTC-5"
    weekly_report_day: str = "sunday"
    weekly_report_hour: int = 20


@dataclass
class SystemConfig:
    loop_check_interval_seconds: int = 30
    mt5_reconnect_interval_seconds: int = 300
    magic_number: int = 234000
    # Order execution retry on transient broker rejections (e.g. retcode 10018
    # "market closed" during the ~00:00 server-time daily rollover). See D040.
    order_retry_attempts: int = 3
    order_retry_delay_seconds: float = 25.0
    # The candle closing at 00:00 server time lands on the broker's daily
    # rollover halt; wait this long before evaluating/executing it so the market
    # has reopened and the strategy re-checks on a fresh price. See D044.
    rollover_settle_seconds: int = 150
    # Mean-reversion TPs are tiny; a market order fills at the ask/bid, so a wide
    # spread (e.g. post-rollover) can land the fill past the TP. Abandon the
    # entry unless at least this fraction of the intended reward survives the
    # live spread. See D046.
    min_reward_fraction: float = 0.5


# ---------------------------------------------------------------------------
# Multi-strategy config (D052, D053, D054)
# ---------------------------------------------------------------------------


@dataclass
class RiskGlobalConfig:
    """Account-wide risk limits shared across all strategies (D053)."""

    max_open_trades: int = 4
    max_drawdown_percent: float = 10.0
    max_same_currency_direction: int = 2


@dataclass
class StrategyRiskConfig:
    """Per-strategy risk budget (D053).

    Each strategy has its own risk parameters.  ``max_open_trades`` must be
    <= ``risk_global.max_open_trades``; validated in ``_validate``.
    """

    percent_per_trade: float = 1.0
    max_open_trades: int = 4
    max_drawdown_percent: float = 10.0


@dataclass
class StrategyInstanceConfig:
    """Config envelope for one strategy instance (D054).

    The ``params`` dict is OPAQUE to the engine: config.py does not parse it
    into typed fields.  Each strategy class parses its own ``params`` dict
    internally (see D054 — "params is opaque to the engine").
    """

    name: str
    enabled: bool
    magic_offset: int
    allocation_pct: float
    pairs: list[str]
    risk: StrategyRiskConfig
    params: dict  # opaque per-strategy parameters — parsed by the strategy class


DEFAULT_PAIRS = ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"]


@dataclass
class DriftConfig:
    broker: BrokerConfig
    telegram: TelegramConfig
    reports: ReportsConfig
    system: SystemConfig
    strategies: dict[str, StrategyInstanceConfig]
    risk_global: RiskGlobalConfig = field(default_factory=RiskGlobalConfig)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    return Path(__file__).parent.parent / "config.yaml"


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_broker(raw: dict) -> BrokerConfig:
    for key in ("server", "login", "password"):
        if key not in raw:
            raise ValueError(f"broker.{key} is required")
    return BrokerConfig(
        server=str(raw["server"]),
        login=int(raw["login"]),
        password=str(raw["password"]),
    )


def _parse_telegram(raw: dict) -> TelegramConfig:
    for key in ("bot_token", "chat_id"):
        if key not in raw:
            raise ValueError(f"telegram.{key} is required")
    return TelegramConfig(
        bot_token=str(raw["bot_token"]),
        chat_id=str(raw["chat_id"]),
    )


def _parse_reports(raw: dict) -> ReportsConfig:
    defaults = ReportsConfig()
    return ReportsConfig(
        timezone=str(raw.get("timezone", defaults.timezone)),
        weekly_report_day=str(raw.get("weekly_report_day", defaults.weekly_report_day)),
        weekly_report_hour=int(raw.get("weekly_report_hour", defaults.weekly_report_hour)),
    )


def _parse_system(raw: dict) -> SystemConfig:
    defaults = SystemConfig()
    return SystemConfig(
        loop_check_interval_seconds=int(
            raw.get("loop_check_interval_seconds", defaults.loop_check_interval_seconds)
        ),
        mt5_reconnect_interval_seconds=int(
            raw.get("mt5_reconnect_interval_seconds", defaults.mt5_reconnect_interval_seconds)
        ),
        magic_number=int(raw.get("magic_number", defaults.magic_number)),
        order_retry_attempts=int(raw.get("order_retry_attempts", defaults.order_retry_attempts)),
        order_retry_delay_seconds=float(
            raw.get("order_retry_delay_seconds", defaults.order_retry_delay_seconds)
        ),
        rollover_settle_seconds=int(
            raw.get("rollover_settle_seconds", defaults.rollover_settle_seconds)
        ),
        min_reward_fraction=float(raw.get("min_reward_fraction", defaults.min_reward_fraction)),
    )


def _parse_risk_global(raw: dict) -> RiskGlobalConfig:
    defaults = RiskGlobalConfig()
    return RiskGlobalConfig(
        max_open_trades=int(raw.get("max_open_trades", defaults.max_open_trades)),
        max_drawdown_percent=float(raw.get("max_drawdown_percent", defaults.max_drawdown_percent)),
        max_same_currency_direction=int(
            raw.get("max_same_currency_direction", defaults.max_same_currency_direction)
        ),
    )


def _parse_strategy_risk(raw: dict) -> StrategyRiskConfig:
    defaults = StrategyRiskConfig()
    return StrategyRiskConfig(
        percent_per_trade=float(raw.get("percent_per_trade", defaults.percent_per_trade)),
        max_open_trades=int(raw.get("max_open_trades", defaults.max_open_trades)),
        max_drawdown_percent=float(raw.get("max_drawdown_percent", defaults.max_drawdown_percent)),
    )


def _parse_strategy_instance(name: str, raw: dict) -> StrategyInstanceConfig:
    """Parse one entry under ``strategies:`` in the YAML."""
    if not isinstance(raw, dict):
        raise ValueError(f"strategies.{name} must be a YAML mapping")
    return StrategyInstanceConfig(
        name=name,
        enabled=bool(raw.get("enabled", True)),
        magic_offset=int(raw.get("magic_offset", 0)),
        allocation_pct=float(raw.get("allocation_pct", 100.0)),
        pairs=[str(p) for p in raw.get("pairs", DEFAULT_PAIRS)],
        risk=_parse_strategy_risk(raw.get("risk", {})),
        params=dict(raw.get("params", {})),
    )


def _parse_strategies(raw: dict) -> dict[str, StrategyInstanceConfig]:
    """Parse the ``strategies:`` block.  Returns dict keyed by strategy name."""
    if not isinstance(raw, dict):
        raise ValueError("strategies must be a YAML mapping")
    return {name: _parse_strategy_instance(name, entry) for name, entry in raw.items()}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(config: DriftConfig) -> None:
    """Structural validation of the loaded config.

    NOTE: strategy name -> class resolution (e.g. verifying "daily_lull" is in
    STRATEGY_REGISTRY) happens in the engine at startup (Step 32), NOT here.
    At this point the registry is empty because strategy classes have not been
    imported yet.  See D054.
    """
    # At least one strategy must be enabled.
    enabled_strategies = {name: s for name, s in config.strategies.items() if s.enabled}
    if not enabled_strategies:
        raise ValueError("At least one strategy must be enabled in strategies:")

    # Each enabled strategy must have a non-empty pairs list.
    for name, s in enabled_strategies.items():
        if not s.pairs:
            raise ValueError(f"strategies.{name}.pairs must be a non-empty list")

    # Sum of allocation_pct for enabled strategies must be <= 100 (D053).
    total_allocation = sum(s.allocation_pct for s in enabled_strategies.values())
    if total_allocation > 100.0 + 1e-9:
        raise ValueError(
            f"Sum of allocation_pct for enabled strategies is {total_allocation:.2f}%, "
            f"which exceeds 100%. Reduce allocations to prevent accidental leverage (D053)."
        )

    # Effective magic numbers (base + offset) must be unique across ALL strategies
    # (not just enabled ones — prevents silent conflicts when re-enabling; D052).
    base_magic = config.system.magic_number
    seen_magics: dict[int, str] = {}
    for name, s in config.strategies.items():
        effective = base_magic + s.magic_offset
        if effective in seen_magics:
            raise ValueError(
                f"Duplicate effective magic number {effective} "
                f"(system.magic_number={base_magic} + magic_offset={s.magic_offset}): "
                f"strategies '{seen_magics[effective]}' and '{name}' share the same magic. "
                f"Each strategy must have a unique magic_offset (D052)."
            )
        seen_magics[effective] = name

    # Per-strategy max_open_trades must not exceed the global cap.
    global_max = config.risk_global.max_open_trades
    for name, s in config.strategies.items():
        if s.risk.max_open_trades > global_max:
            raise ValueError(
                f"strategies.{name}.risk.max_open_trades ({s.risk.max_open_trades}) "
                f"exceeds risk_global.max_open_trades ({global_max}). "
                f"Per-strategy limit must be <= global limit (D053)."
            )

    # Per-strategy risk range validations (reuse existing bounds).
    for name, s in config.strategies.items():
        if not (0 < s.risk.percent_per_trade <= 5):
            raise ValueError(
                f"strategies.{name}.risk.percent_per_trade must be between 0 and 5, "
                f"got {s.risk.percent_per_trade}"
            )
        if not (1 <= s.risk.max_open_trades <= 10):
            raise ValueError(
                f"strategies.{name}.risk.max_open_trades must be between 1 and 10, "
                f"got {s.risk.max_open_trades}"
            )
        if not (1 <= s.risk.max_drawdown_percent <= 50):
            raise ValueError(
                f"strategies.{name}.risk.max_drawdown_percent must be between 1 and 50, "
                f"got {s.risk.max_drawdown_percent}"
            )

    # Global risk range validations.
    if not (1 <= config.risk_global.max_open_trades <= 10):
        raise ValueError(
            f"risk_global.max_open_trades must be between 1 and 10, "
            f"got {config.risk_global.max_open_trades}"
        )
    if not (1 <= config.risk_global.max_drawdown_percent <= 50):
        raise ValueError(
            f"risk_global.max_drawdown_percent must be between 1 and 50, "
            f"got {config.risk_global.max_drawdown_percent}"
        )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_config(path: str | Path | None = None) -> DriftConfig:
    config_path = _resolve_path(path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            "Copy config.example.yaml to config.yaml and fill in your credentials."
        )

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("config.yaml must contain a YAML mapping at the top level")

    config = DriftConfig(
        broker=_parse_broker(raw.get("broker", {})),
        telegram=_parse_telegram(raw.get("telegram", {})),
        reports=_parse_reports(raw.get("reports", {})),
        system=_parse_system(raw.get("system", {})),
        risk_global=_parse_risk_global(raw.get("risk_global", {})),
        strategies=_parse_strategies(raw.get("strategies", {})),
    )

    _validate(config)
    return config
