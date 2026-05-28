from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from drift.config import StrategyConfig
from drift.indicators import compute_all
from drift.mlp_filter import RegimeFilter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signal:
    pair: str
    timestamp: datetime
    h4_candle_time: datetime
    bb_upper: float
    bb_middle: float
    bb_lower: float
    rsi: float
    adx: float
    atr_value: float
    action: str
    reason: str
    regime_probability: float = 0.5  # MLP confidence: probability of ranging market


def check_d1_regime(
    adx: pd.Series,
    adx_max_threshold: float,
) -> tuple[bool, str]:
    """Fallback ADX-based regime check (used when no MLP model is loaded).

    Return (regime_ok, reject_reason).
    Mean reversion trades only in ranging markets: ADX < adx_max_threshold.
    """
    adx_val = float(adx.iloc[-1])
    if adx_val >= adx_max_threshold:
        return False, "trending_market"
    return True, ""


def check_h4_entry(
    close: pd.Series,
    bb_upper: pd.Series,
    bb_lower: pd.Series,
    rsi: pd.Series,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 70.0,
) -> str:
    """Bollinger Band + RSI mean reversion entry.

    BUY:  close below lower BB AND RSI < rsi_oversold  (price too low → expect bounce)
    SELL: close above upper BB AND RSI > rsi_overbought (price too high → expect fade)
    """
    if len(close) < 1 or len(bb_upper) < 1 or len(bb_lower) < 1 or len(rsi) < 1:
        return "none"

    curr_close = float(close.iloc[-1])
    curr_upper = float(bb_upper.iloc[-1])
    curr_lower = float(bb_lower.iloc[-1])
    curr_rsi = float(rsi.iloc[-1])

    if curr_close < curr_lower and curr_rsi < rsi_oversold:
        return "buy"

    if curr_close > curr_upper and curr_rsi > rsi_overbought:
        return "sell"

    return "none"


def _build_mlp_feature_df(indicators: dict, df_h4: pd.DataFrame) -> pd.DataFrame:
    """Assemble a single-row DataFrame with the columns RegimeFilter.compute_features expects."""
    return pd.DataFrame(
        {
            "adx": indicators["adx"].values,
            "bb_upper": indicators["bb_upper"].values,
            "bb_middle": indicators["bb_middle"].values,
            "bb_lower": indicators["bb_lower"].values,
            "atr": indicators["atr"].values,
        },
        index=df_h4.index[: len(indicators["adx"])],
    )


def analyze_pair(
    pair: str,
    df_d1: pd.DataFrame,
    df_h4: pd.DataFrame,
    config: StrategyConfig,
    regime_filter: RegimeFilter | None = None,
) -> Signal:
    indicators = compute_all(df_d1, df_h4, config)

    bb_upper_val = float(indicators["bb_upper"].iloc[-1])
    bb_middle_val = float(indicators["bb_middle"].iloc[-1])
    bb_lower_val = float(indicators["bb_lower"].iloc[-1])
    rsi_val = float(indicators["rsi"].iloc[-1])
    adx_val = float(indicators["adx"].iloc[-1])
    atr_val = float(indicators["atr"].iloc[-1])
    h4_candle_time = df_h4.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)

    # --- Regime filter ---
    regime_ok: bool
    regime_reason: str
    regime_probability: float = 0.5

    if regime_filter is not None and regime_filter._loaded:
        feat_df = _build_mlp_feature_df(indicators, df_h4)
        features = regime_filter.compute_features(feat_df)
        last_features = features[-1]
        if np.isnan(last_features).any():
            # Not enough history for ATR SMA — fall back to ADX filter
            regime_ok, regime_reason = check_d1_regime(indicators["adx"], config.adx_max_threshold)
        else:
            regime_ok = regime_filter.predict(last_features)
            regime_probability = regime_filter.predict_proba(last_features)
            regime_reason = "" if regime_ok else "mlp_trending_market"
    else:
        regime_ok, regime_reason = check_d1_regime(indicators["adx"], config.adx_max_threshold)

    if not regime_ok:
        logger.info(
            "%s | action=none reason=%s adx=%.1f regime_prob=%.2f",
            pair,
            regime_reason,
            adx_val,
            regime_probability,
        )
        return Signal(
            pair=pair,
            timestamp=datetime.now(timezone.utc),
            h4_candle_time=h4_candle_time,
            bb_upper=bb_upper_val,
            bb_middle=bb_middle_val,
            bb_lower=bb_lower_val,
            rsi=rsi_val,
            adx=adx_val,
            atr_value=atr_val,
            action="none",
            reason=regime_reason,
            regime_probability=regime_probability,
        )

    entry = check_h4_entry(
        df_h4["close"],
        indicators["bb_upper"],
        indicators["bb_lower"],
        indicators["rsi"],
        config.rsi_oversold,
        config.rsi_overbought,
    )

    if entry == "none":
        reason = "no entry signal"
    else:
        reason = "ranging_market + BB extreme + RSI confirmed"

    logger.info(
        "%s | action=%s reason=%s adx=%.1f rsi=%.1f"
        " bb_upper=%.5f bb_lower=%.5f close=%.5f regime_prob=%.2f",
        pair,
        entry,
        reason,
        adx_val,
        rsi_val,
        bb_upper_val,
        bb_lower_val,
        float(df_h4["close"].iloc[-1]),
        regime_probability,
    )

    return Signal(
        pair=pair,
        timestamp=datetime.now(timezone.utc),
        h4_candle_time=h4_candle_time,
        bb_upper=bb_upper_val,
        bb_middle=bb_middle_val,
        bb_lower=bb_lower_val,
        rsi=rsi_val,
        adx=adx_val,
        atr_value=atr_val,
        action=entry,
        reason=reason,
        regime_probability=regime_probability,
    )
