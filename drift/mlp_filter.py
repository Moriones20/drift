"""MLP regime filter — predicts ranging vs trending market conditions."""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler


class RegimeFilter:
    """MLP-based regime filter. Output: True = ranging (trade), False = trending (don't trade)."""

    def __init__(self, model_path: str = "data/regime_model.pkl"):
        self.model_path = Path(model_path)
        self.model: MLPClassifier | None = None
        self.scaler: StandardScaler | None = None
        self._loaded = False

    def compute_features(self, df_h4: pd.DataFrame) -> np.ndarray:
        """Compute 3 features from H4 data.

        1. ADX (14) — trend strength
        2. BB Width — (upper - lower) / middle — band spread normalized
        3. ATR ratio — ATR(14) / SMA(ATR(14), 50) — current vs average volatility

        df_h4 must have columns: adx, bb_upper, bb_middle, bb_lower, atr
        Returns array of shape (n_samples, 3).
        """
        adx = df_h4["adx"].values
        bb_width = (df_h4["bb_upper"] - df_h4["bb_lower"]) / df_h4["bb_middle"]
        bb_width = bb_width.values
        atr = df_h4["atr"].values
        atr_sma = df_h4["atr"].rolling(50).mean().values
        atr_ratio = np.where(atr_sma > 0, atr / atr_sma, 1.0)

        return np.column_stack([adx, bb_width, atr_ratio])

    @staticmethod
    def label_regime(
        close: np.ndarray,
        lookforward: int = 20,
        threshold: float = 0.4,
    ) -> np.ndarray:
        """Label each bar as ranging (0) or trending (1) using Efficiency Ratio.

        ER = |net move over N bars| / sum(|individual bar moves|)
        ER < threshold = ranging = good for mean reversion  (label 0)
        ER >= threshold = trending = bad for mean reversion (label 1)

        Uses future data — only for training labels, NOT for live prediction.
        """
        n = len(close)
        labels = np.full(n, np.nan)
        for i in range(n - lookforward):
            net = abs(close[i + lookforward] - close[i])
            gross = sum(abs(close[i + j + 1] - close[i + j]) for j in range(lookforward))
            er = net / gross if gross > 0 else 0.0
            labels[i] = 0 if er < threshold else 1
        return labels

    def train(self, features: np.ndarray, labels: np.ndarray) -> float:
        """Train the MLP on features and labels. Drops NaN rows.

        Returns training accuracy on the full dataset.
        """
        mask = ~(np.isnan(features).any(axis=1) | np.isnan(labels))
        X = features[mask]
        y = labels[mask]

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = MLPClassifier(
            hidden_layer_sizes=(8, 4),
            activation="relu",
            solver="lbfgs",
            alpha=0.01,
            max_iter=1000,
            random_state=42,
        )
        self.model.fit(X_scaled, y)
        self._loaded = True

        return float(self.model.score(X_scaled, y))

    def predict(self, features: np.ndarray) -> bool:
        """Predict if current market is ranging (True = trade) or trending (False = skip).

        features: array of shape (1, 3) or (3,) — single bar's features.
        Falls back to True (allow trade) when no model is loaded.
        """
        if not self._loaded:
            return True

        f = features.reshape(1, -1)
        assert self.scaler is not None
        f_scaled = self.scaler.transform(f)
        assert self.model is not None
        prediction = self.model.predict(f_scaled)[0]
        return bool(prediction == 0)  # 0 = ranging → trade

    def predict_proba(self, features: np.ndarray) -> float:
        """Return probability of ranging (class 0). Returns 0.5 if model not loaded."""
        if not self._loaded:
            return 0.5

        f = features.reshape(1, -1)
        assert self.scaler is not None
        f_scaled = self.scaler.transform(f)
        assert self.model is not None
        proba = self.model.predict_proba(f_scaled)[0]
        return float(proba[0])  # probability of class 0 (ranging)

    def save(self) -> None:
        """Save model and scaler to disk."""
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump({"model": self.model, "scaler": self.scaler}, f)

    def load(self) -> bool:
        """Load model from disk. Returns True if successful."""
        if not self.model_path.exists():
            return False
        with open(self.model_path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.scaler = data["scaler"]
        self._loaded = True
        return True
