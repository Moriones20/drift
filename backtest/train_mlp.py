"""Train the MLP regime filter on historical data.

Flow:
  1. Load (or generate) H4 data for all pairs from backtest/data/.
  2. Compute ADX, BB Width and ATR-ratio features.
  3. Label each bar using the Efficiency Ratio (lookforward=20, threshold=0.4).
  4. Walk-forward cross-validation with TimeSeriesSplit(5 folds).
  5. Train the final model on all data and save to data/regime_model.pkl.

Usage:
    python -m backtest.train_mlp
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest._indicators import adx as _adx
from backtest._indicators import atr as _atr
from backtest._indicators import bollinger_bands as _bb
from backtest.download_data import PAIRS, generate_synthetic_data, load_data, save_data
from drift.mlp_filter import RegimeFilter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = Path(__file__).parent / "data"


def _ensure_data(pairs: list[str], data_dir: Path) -> None:
    """Generate synthetic data for any pair whose CSV files are missing."""
    for symbol in pairs:
        d1_path = data_dir / f"{symbol}_D1.csv"
        h4_path = data_dir / f"{symbol}_H4.csv"
        if not d1_path.exists() or not h4_path.exists():
            logger.info("Data missing for %s — generating synthetic data", symbol)
            df_d1, df_h4 = generate_synthetic_data(symbol)
            save_data(symbol, df_d1, df_h4, data_dir)


def prepare_features(df_h4: pd.DataFrame) -> pd.DataFrame:
    """Compute MLP features from H4 OHLCV data."""
    bb_upper, bb_middle, bb_lower = _bb(df_h4["close"], 20, 2.0)
    adx_values = _adx(df_h4["high"], df_h4["low"], df_h4["close"], 14)
    atr_values = _atr(df_h4["high"], df_h4["low"], df_h4["close"], 14)

    return pd.DataFrame(
        {
            "adx": adx_values,
            "bb_upper": bb_upper,
            "bb_middle": bb_middle,
            "bb_lower": bb_lower,
            "atr": atr_values,
            "close": df_h4["close"].values,
        },
        index=df_h4.index,
    )


def main() -> None:
    _ensure_data(PAIRS, DATA_DIR)

    rf = RegimeFilter(model_path=str(PROJECT_ROOT / "data" / "regime_model.pkl"))

    all_features: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []

    for pair in PAIRS:
        print(f"\nProcessing {pair}...")
        df_d1, df_h4 = load_data(pair, DATA_DIR)

        df = prepare_features(df_h4)
        features = rf.compute_features(df)
        labels = rf.label_regime(df["close"].values, lookforward=20, threshold=0.4)

        mask = ~(np.isnan(features).any(axis=1) | np.isnan(labels))
        all_features.append(features[mask])
        all_labels.append(labels[mask])

        ranging_pct = (labels[mask] == 0).sum() / len(labels[mask]) * 100
        print(f"  {pair}: {len(labels[mask])} bars, {ranging_pct:.1f}% ranging")

    X = np.vstack(all_features)
    y = np.concatenate(all_labels)

    print(f"\nTotal training data: {len(X)} bars")
    ranging_n = (y == 0).sum()
    trending_n = (y == 1).sum()
    print(
        f"Class balance: {ranging_n} ranging ({ranging_n / len(y) * 100:.1f}%), "
        f"{trending_n} trending ({trending_n / len(y) * 100:.1f}%)"
    )

    # Walk-forward cross-validation
    tscv = TimeSeriesSplit(n_splits=5)
    scores: list[float] = []

    print("\nWalk-forward cross-validation:")
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        rf_fold = RegimeFilter()
        acc = rf_fold.train(X[train_idx], y[train_idx])
        assert rf_fold.scaler is not None
        val_acc = float(
            rf_fold.model.score(rf_fold.scaler.transform(X[val_idx]), y[val_idx])  # type: ignore[union-attr]
        )
        scores.append(val_acc)
        print(f"  Fold {fold + 1}: train={acc:.3f}, val={val_acc:.3f}")

    print(f"\nMean CV accuracy: {np.mean(scores):.3f} (+/- {np.std(scores):.3f})")

    # Train final model on all data
    final_acc = rf.train(X, y)
    print(f"Final model accuracy (full data): {final_acc:.3f}")

    # Full classification report
    assert rf.scaler is not None
    assert rf.model is not None
    X_scaled = rf.scaler.transform(X)
    y_pred = rf.model.predict(X_scaled)

    print("\nClassification Report:")
    print(classification_report(y, y_pred, target_names=["Ranging", "Trending"]))
    print("Confusion Matrix:")
    print(confusion_matrix(y, y_pred))

    # Save model
    rf.save()
    print(f"\nModel saved to {rf.model_path}")


if __name__ == "__main__":
    main()
