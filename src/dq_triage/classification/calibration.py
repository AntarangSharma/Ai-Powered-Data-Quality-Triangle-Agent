"""Isotonic probability calibration for confidence_calibrated."""

from __future__ import annotations

from pathlib import Path

import joblib
from sklearn.isotonic import IsotonicRegression

CALIB_PATH = Path(__file__).parent / "calib.joblib"


class IsotonicCalibrator:
    """Wrapper around scikit-learn's IsotonicRegression to map raw rule scores to calibrated probabilities."""

    def __init__(self) -> None:
        self._model: IsotonicRegression | None = None
        self._loaded = False

    def _ensure_model(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if CALIB_PATH.exists():
            try:
                self._model = joblib.load(CALIB_PATH)
            except Exception:
                self._model = None
        else:
            self._model = None

    def fit(self, pairs: list[tuple[float, bool]]) -> None:
        """Fit isotonic regression on a list of (raw_score, is_correct) pairs and persist it."""
        if not pairs:
            return
        x = [p[0] for p in pairs]
        y = [1.0 if p[1] else 0.0 for p in pairs]

        # IsotonicRegression requires x elements in [0, 1] bounds, out_of_bounds="clip"
        model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        model.fit(x, y)
        self._model = model
        joblib.dump(model, CALIB_PATH)

    def calibrate(self, score: float) -> float:
        """Map raw score to calibrated probability. Falls back to raw score if no model exists."""
        self._ensure_model()
        if self._model is None:
            return score
        # Predict expects an array-like
        preds = self._model.predict([score])
        return float(preds[0])


_calibrator = IsotonicCalibrator()


def calibrate(score: float) -> float:
    """Convenience function to calibrate a score using the persisted calibrator."""
    return _calibrator.calibrate(score)


def fit_and_persist(pairs: list[tuple[float, bool]]) -> None:
    """Convenience function to fit and save the calibrator."""
    _calibrator.fit(pairs)
