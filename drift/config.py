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
class StrategyConfig:
    # RSI thresholds for session scalper entries
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    # ADX regime filter — trade ONLY when ADX < adx_max_threshold (ranging market)
    adx_max_threshold: float = 35.0
    # Asian Session Scalper — session window (UTC hours)
    session_start_hour: int = 21
    range_definition_hours: int = 2
    session_end_hour: int = 2
    # Asian Session Scalper — range quality filter (multiples of ATR)
    range_atr_min: float = 1.0
    range_atr_max: float = 4.0
    # Asian Session Scalper — risk parameters
    sl_atr_mult: float = 2.5
    # Asian Session Scalper — indicator periods
    m15_rsi_period: int = 14
    m15_atr_period: int = 14
    h4_adx_period: int = 14


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


DEFAULT_PAIRS = ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"]


@dataclass
class DriftConfig:
    broker: BrokerConfig
    strategy: StrategyConfig
    risk: RiskConfig
    telegram: TelegramConfig
    reports: ReportsConfig
    system: SystemConfig
    pairs: list[str] = field(default_factory=lambda: list(DEFAULT_PAIRS))


def _resolve_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    return Path(__file__).parent.parent / "config.yaml"


def _parse_broker(raw: dict) -> BrokerConfig:
    for key in ("server", "login", "password"):
        if key not in raw:
            raise ValueError(f"broker.{key} is required")
    return BrokerConfig(
        server=str(raw["server"]),
        login=int(raw["login"]),
        password=str(raw["password"]),
    )


def _parse_strategy(raw: dict) -> StrategyConfig:
    defaults = StrategyConfig()
    return StrategyConfig(
        rsi_oversold=float(raw.get("rsi_oversold", defaults.rsi_oversold)),
        rsi_overbought=float(raw.get("rsi_overbought", defaults.rsi_overbought)),
        adx_max_threshold=float(raw.get("adx_max_threshold", defaults.adx_max_threshold)),
        session_start_hour=int(raw.get("session_start_hour", defaults.session_start_hour)),
        range_definition_hours=int(
            raw.get("range_definition_hours", defaults.range_definition_hours)
        ),
        session_end_hour=int(raw.get("session_end_hour", defaults.session_end_hour)),
        range_atr_min=float(raw.get("range_atr_min", defaults.range_atr_min)),
        range_atr_max=float(raw.get("range_atr_max", defaults.range_atr_max)),
        sl_atr_mult=float(raw.get("sl_atr_mult", defaults.sl_atr_mult)),
        m15_rsi_period=int(raw.get("m15_rsi_period", defaults.m15_rsi_period)),
        m15_atr_period=int(raw.get("m15_atr_period", defaults.m15_atr_period)),
        h4_adx_period=int(raw.get("h4_adx_period", defaults.h4_adx_period)),
    )


def _parse_risk(raw: dict) -> RiskConfig:
    defaults = RiskConfig()
    return RiskConfig(
        percent_per_trade=float(raw.get("percent_per_trade", defaults.percent_per_trade)),
        max_open_trades=int(raw.get("max_open_trades", defaults.max_open_trades)),
        max_same_currency_direction=int(
            raw.get("max_same_currency_direction", defaults.max_same_currency_direction)
        ),
        max_drawdown_percent=float(raw.get("max_drawdown_percent", defaults.max_drawdown_percent)),
        trailing_stop_atr_multiplier=float(
            raw.get("trailing_stop_atr_multiplier", defaults.trailing_stop_atr_multiplier)
        ),
        use_trailing_stop=bool(raw.get("use_trailing_stop", defaults.use_trailing_stop)),
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
    )


def _validate(config: DriftConfig) -> None:
    if not (0 < config.risk.percent_per_trade <= 5):
        raise ValueError(
            f"risk.percent_per_trade must be between 0 and 5, got {config.risk.percent_per_trade}"
        )
    if not (1 <= config.risk.max_open_trades <= 10):
        raise ValueError(
            f"risk.max_open_trades must be between 1 and 10, got {config.risk.max_open_trades}"
        )
    if not (1 <= config.risk.max_drawdown_percent <= 50):
        raise ValueError(
            f"risk.max_drawdown_percent must be between 1 and 50, "
            f"got {config.risk.max_drawdown_percent}"
        )
    if not config.pairs:
        raise ValueError("pairs must be a non-empty list")


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
        strategy=_parse_strategy(raw.get("strategy", {})),
        risk=_parse_risk(raw.get("risk", {})),
        telegram=_parse_telegram(raw.get("telegram", {})),
        reports=_parse_reports(raw.get("reports", {})),
        system=_parse_system(raw.get("system", {})),
        pairs=[str(p) for p in raw.get("pairs", DEFAULT_PAIRS)],
    )

    _validate(config)
    return config
