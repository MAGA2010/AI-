# -*- coding: utf-8 -*-
"""Tests for daily.py — MA250 and volume surge features."""

import pandas as pd
import pytest

from alphasift.daily import compute_daily_features, _volume_surge_3d


def _make_hist(n: int, *, base_price: float = 10.0, base_volume: float = 1000000) -> pd.DataFrame:
    """Generate synthetic daily K-line data with n days."""
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = [base_price + i * 0.05 for i in range(n)]
    volume = [base_volume] * n
    # Last day volume spike for testing
    volume[-1] = base_volume * 2.0
    volume[-2] = base_volume * 1.8
    volume[-3] = base_volume * 1.6
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "收盘": close,
        "开盘": [p - 0.1 for p in close],
        "最高": [p + 0.2 for p in close],
        "最低": [p - 0.3 for p in close],
        "成交量": volume,
    })


def test_compute_daily_features_includes_ma250():
    hist = _make_hist(300)
    features = compute_daily_features(hist)
    assert "ma250" in features
    assert features["ma250"] is not None
    assert isinstance(features["ma250"], float)


def test_compute_daily_features_ma250_none_when_short_lookback():
    hist = _make_hist(100)
    features = compute_daily_features(hist)
    assert "ma250" in features
    assert features["ma250"] is None


def test_compute_daily_features_ma250_boundary():
    """Exactly 250 days should produce a MA250 value."""
    hist = _make_hist(250)
    features = compute_daily_features(hist)
    assert features["ma250"] is not None


def test_volume_surge_3d_present():
    hist = _make_hist(300)
    features = compute_daily_features(hist)
    assert "volume_surge_3d_pct" in features
    assert features["volume_surge_3d_pct"] is not None
    # Volume was spiked on last 3 days (1.6x, 1.8x, 2.0x base) -> avg = 1.8x -> surge = 80%
    assert features["volume_surge_3d_pct"] > 50  # significant surge


def test_volume_surge_3d_no_surge():
    """Flat volume should give ~0% surge."""
    hist = _make_hist(300)
    # Normalize volume to be flat
    hist["成交量"] = 1000000
    features = compute_daily_features(hist)
    assert abs(features["volume_surge_3d_pct"]) < 5  # near zero


def test_volume_surge_3d_insufficient_data():
    """Short history should return None."""
    hist = _make_hist(10)
    features = compute_daily_features(hist)
    assert features["volume_surge_3d_pct"] is None


def test_volume_surge_3d_direct():
    """Direct test of _volume_surge_3d helper."""
    df = pd.DataFrame({"volume": [100] * 23 + [200, 200, 200]})
    result = _volume_surge_3d(df)
    assert result is not None
    # avg_3d = 200, avg_20d (indices 3-22) = 100 -> surge = 100%
    assert result == pytest.approx(100.0)


def test_all_features_present():
    hist = _make_hist(300)
    features = compute_daily_features(hist)
    expected_keys = [
        "daily_data_points", "change_60d", "ma5", "ma20", "ma60", "ma250",
        "ma_bullish", "price_above_ma20", "macd_status", "rsi_status",
        "rsi14", "signal_score", "volume_surge_3d_pct",
        "breakout_20d_pct", "range_20d_pct", "volume_ratio_20d",
        "body_pct", "pullback_to_ma20_pct", "consolidation_days_20d",
    ]
    for key in expected_keys:
        assert key in features, f"Missing key: {key}"
