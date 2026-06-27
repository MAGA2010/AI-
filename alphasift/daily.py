# -*- coding: utf-8 -*-
"""Lightweight daily K-line enrichment for narrowed candidate pools."""

from __future__ import annotations

from datetime import datetime, timedelta
import time

import pandas as pd

_DAILY_FEATURE_DEFAULTS = {
    "daily_data_points": pd.NA,
    "change_60d": pd.NA,
    "ma5": pd.NA,
    "ma20": pd.NA,
    "ma60": pd.NA,
    "ma250": pd.NA,
    "ma_bullish": pd.NA,
    "price_above_ma20": pd.NA,
    "macd_status": "",
    "rsi_status": "",
    "rsi14": pd.NA,
    "signal_score": pd.NA,
    "prev_high_20d": pd.NA,
    "range_20d_pct": pd.NA,
    "breakout_20d_pct": pd.NA,
    "volume_ratio_20d": pd.NA,
    "volume_surge_3d_pct": pd.NA,
    "body_pct": pd.NA,
    "pullback_to_ma20_pct": pd.NA,
    "consolidation_days_20d": pd.NA,
}


def enrich_daily_features(
    df: pd.DataFrame,
    *,
    max_rows: int = 100,
    lookback_days: int = 120,
    source: str = "akshare",
    fetch_retries: int = 2,
) -> pd.DataFrame:
    """Attach daily technical features to the first ``max_rows`` candidates.

    This intentionally runs after broad snapshot filtering; it is not a full
    market historical-data pass.
    """
    if df.empty or max_rows <= 0:
        return df.copy()

    result = df.copy()
    daily_errors: list[str] = []
    success_count = 0
    selected_index = list(result.index[:max_rows])
    for idx in selected_index:
        raw_code = str(result.at[idx, "code"] if "code" in result.columns else "").strip()
        if not raw_code:
            continue
        code = raw_code.zfill(6)
        try:
            hist = fetch_daily_history(
                code,
                lookback_days=lookback_days,
                source=source,
                retries=fetch_retries,
            )
            features = compute_daily_features(hist)
            success_count += 1
        except Exception as exc:
            daily_errors.append(f"{code}: {exc}")
            features = dict(_DAILY_FEATURE_DEFAULTS)
        for key, value in features.items():
            result.at[idx, key] = value

    result.attrs["daily_errors"] = daily_errors
    result.attrs["daily_success_count"] = success_count
    return result


def fetch_daily_history(
    code: str,
    *,
    lookback_days: int = 120,
    source: str = "akshare",
    retries: int = 2,
) -> pd.DataFrame:
    """Fetch daily history for one A-share code.

    ``source`` accepts ``akshare``, ``baostock`` or ``auto``. ``auto`` tries
    akshare first and silently falls back to baostock when akshare fails. This
    matches the multi-source resilience pattern recommended for A-share data
    pipelines (DSA-style: free primary + free backup).
    """
    src = (source or "akshare").lower()
    if src == "auto":
        sources: tuple[str, ...] = ("akshare", "baostock")
    elif src in ("akshare", "baostock"):
        sources = (src,)
    else:
        raise ValueError(f"Unsupported daily source: {source}")

    attempts = max(int(retries), 0) + 1
    errors: list[str] = []
    for current in sources:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                if current == "akshare":
                    return _fetch_daily_akshare(code, lookback_days=lookback_days)
                return _fetch_daily_baostock(code, lookback_days=lookback_days)
            except Exception as exc:  # noqa: BLE001 - aggregated below
                last_error = exc
                if attempt >= attempts - 1:
                    break
                time.sleep(min(0.5 * (attempt + 1), 2.0))
        errors.append(f"{current} after {attempts} attempts: {last_error}")

    raise RuntimeError(
        f"daily history fetch failed for {code}: {'; '.join(errors)}"
    )


def _fetch_daily_akshare(code: str, *, lookback_days: int) -> pd.DataFrame:
    import akshare as ak

    start_date = (datetime.now() - timedelta(days=max(lookback_days * 2, 90))).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(
        symbol=str(code).zfill(6),
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if df is None or df.empty:
        raise RuntimeError(f"akshare daily history empty for {code}")
    return df.tail(max(lookback_days, 30)).copy()


def _fetch_daily_baostock(code: str, *, lookback_days: int) -> pd.DataFrame:
    """Fetch daily history via Baostock as a free fallback source.

    Baostock uses ``sh.600519`` / ``sz.000001`` style codes and exposes
    forward-adjusted prices via ``adjustflag='2'``.
    """
    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError("baostock not installed; pip install baostock") from exc

    bs_code = _to_baostock_code(code)
    start_date = (datetime.now() - timedelta(days=max(lookback_days * 2, 90))).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    bs.login()
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2",
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock error {rs.error_code}: {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()

    if not rows:
        raise RuntimeError(f"baostock daily history empty for {code}")

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    return df.tail(max(lookback_days, 30)).copy()


def _to_baostock_code(code: str) -> str:
    raw = str(code).strip().zfill(6)
    if raw.startswith(("6", "9", "5")):
        return f"sh.{raw}"
    return f"sz.{raw}"


def compute_daily_features(hist: pd.DataFrame) -> dict[str, object]:
    """Compute compact trend/reversal features from a daily K-line DataFrame."""
    df = _normalize_daily_history(hist)
    if df.empty:
        raise RuntimeError("daily history is empty after normalization")

    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    if close.empty:
        raise RuntimeError("daily history has no valid close price")

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma250 = close.rolling(250).mean() if len(close) >= 250 else pd.Series(dtype=float)
    last_close = float(close.iloc[-1])
    last_ma5 = _last_float(ma5)
    last_ma20 = _last_float(ma20)
    last_ma60 = _last_float(ma60)
    last_ma250 = _last_float(ma250) if len(close) >= 250 else None
    shape = _compute_shape_features(df, last_close=last_close, last_ma20=last_ma20)

    lookback_idx = max(0, len(close) - 61)
    base_close = float(close.iloc[lookback_idx])
    change_60d = (last_close / base_close - 1.0) * 100 if base_close > 0 else None

    macd_status = _compute_macd_status(close)
    rsi_value = _compute_rsi(close)
    rsi_status = _classify_rsi(rsi_value)
    ma_bullish = _is_true(last_ma5 is not None and last_ma20 is not None and last_ma60 is not None
                          and last_ma5 >= last_ma20 >= last_ma60)
    price_above_ma20 = _is_true(last_ma20 is not None and last_close >= last_ma20)
    signal_score = _compute_signal_score(
        change_60d=change_60d,
        ma_bullish=ma_bullish,
        price_above_ma20=price_above_ma20,
        macd_status=macd_status,
        rsi_status=rsi_status,
    )

    return {
        "daily_data_points": int(len(close)),
        "change_60d": None if change_60d is None else round(float(change_60d), 4),
        "ma5": last_ma5,
        "ma20": last_ma20,
        "ma60": last_ma60,
        "ma250": last_ma250,
        "ma_bullish": ma_bullish,
        "price_above_ma20": price_above_ma20,
        "macd_status": macd_status,
        "rsi_status": rsi_status,
        "rsi14": None if rsi_value is None else round(float(rsi_value), 4),
        "signal_score": round(float(signal_score), 4),
        **shape,
    }


def _normalize_daily_history(hist: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "日期": "date",
        "收盘": "close",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = hist.rename(columns=rename_map).copy()
    if "date" in df.columns:
        df = df.sort_values("date")
    if "close" not in df.columns:
        raise RuntimeError("daily history has no close column")
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).copy()
    for col in ("open", "high", "low"):
        if col not in df.columns:
            df[col] = df["close"]
        else:
            df[col] = df[col].fillna(df["close"])
    return df


def _compute_shape_features(
    df: pd.DataFrame,
    *,
    last_close: float,
    last_ma20: float | None,
) -> dict[str, object]:
    previous = df.iloc[:-1].tail(20)
    recent = df.tail(20)
    last = df.iloc[-1]

    prev_high_20d = _series_max(previous["high"]) if "high" in previous.columns else None
    range_20d_pct = _range_pct(recent)
    breakout_20d_pct = (
        (last_close / prev_high_20d - 1.0) * 100
        if prev_high_20d is not None and prev_high_20d > 0
        else None
    )
    volume_ratio_20d = _volume_ratio_20d(df)
    body_pct = _body_pct(last)
    pullback_to_ma20_pct = (
        (last_close / last_ma20 - 1.0) * 100
        if last_ma20 is not None and last_ma20 > 0
        else None
    )

    return {
        "prev_high_20d": _round_or_none(prev_high_20d),
        "range_20d_pct": _round_or_none(range_20d_pct),
        "breakout_20d_pct": _round_or_none(breakout_20d_pct),
        "volume_ratio_20d": _round_or_none(volume_ratio_20d),
        "volume_surge_3d_pct": _round_or_none(_volume_surge_3d(df)),
        "body_pct": _round_or_none(body_pct),
        "pullback_to_ma20_pct": _round_or_none(pullback_to_ma20_pct),
        "consolidation_days_20d": _consolidation_days(previous),
    }


def _series_max(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.max())


def _range_pct(df: pd.DataFrame) -> float | None:
    if "high" not in df.columns or "low" not in df.columns:
        return None
    high = pd.to_numeric(df["high"], errors="coerce").dropna()
    low = pd.to_numeric(df["low"], errors="coerce").dropna()
    if high.empty or low.empty:
        return None
    low_min = float(low.min())
    if low_min <= 0:
        return None
    return (float(high.max()) / low_min - 1.0) * 100


def _volume_ratio_20d(df: pd.DataFrame) -> float | None:
    if "volume" not in df.columns:
        return None
    volume = pd.to_numeric(df["volume"], errors="coerce")
    if len(volume) < 2 or pd.isna(volume.iloc[-1]):
        return None
    previous = volume.iloc[:-1].tail(20).dropna()
    if previous.empty:
        return None
    base = float(previous.mean())
    if base <= 0:
        return None
    return float(volume.iloc[-1]) / base


def _volume_surge_3d(df: pd.DataFrame) -> float | None:
    """3-day average volume surge vs prior 20-day average, as percentage.

    Returns (avg_3d / avg_20d - 1) * 100, or None if insufficient data.
    """
    if "volume" not in df.columns or len(df) < 24:
        return None
    volume = pd.to_numeric(df["volume"], errors="coerce").dropna()
    if len(volume) < 24:
        return None
    recent_3 = volume.tail(3)
    prior_20 = volume.iloc[-23:-3]
    base = float(prior_20.mean())
    if base <= 0:
        return None
    return (float(recent_3.mean()) / base - 1.0) * 100


def _consolidation_days(previous: pd.DataFrame, *, max_range_pct: float = 12.0) -> int | None:
    if previous.empty or "high" not in previous.columns or "low" not in previous.columns:
        return None
    for days in range(min(len(previous), 20), 1, -1):
        window = previous.tail(days)
        range_pct = _range_pct(window)
        if range_pct is not None and range_pct <= max_range_pct:
            return int(days)
    return 0


def _body_pct(row: pd.Series) -> float | None:
    open_price = row.get("open")
    close_price = row.get("close")
    if pd.isna(open_price) or pd.isna(close_price) or float(open_price) <= 0:
        return None
    return (float(close_price) / float(open_price) - 1.0) * 100


def _round_or_none(value: float | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def _compute_macd_status(close: pd.Series) -> str:
    if len(close) < 35:
        return "neutral"
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    diff = ema12 - ema26
    dea = diff.ewm(span=9, adjust=False).mean()
    last_diff = float(diff.iloc[-1])
    last_dea = float(dea.iloc[-1])
    if last_diff > last_dea and last_diff > 0:
        return "bullish"
    if last_diff < last_dea and last_diff < 0:
        return "bearish"
    return "neutral"


def _compute_rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) <= period:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    value = rsi.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _classify_rsi(value: float | None) -> str:
    if value is None:
        return "neutral"
    if value <= 35:
        return "oversold"
    if value >= 70:
        return "overbought"
    return "neutral"


def _compute_signal_score(
    *,
    change_60d: float | None,
    ma_bullish: bool,
    price_above_ma20: bool,
    macd_status: str,
    rsi_status: str,
) -> float:
    score = 50.0
    if ma_bullish:
        score += 14
    if price_above_ma20:
        score += 10
    if macd_status == "bullish":
        score += 12
    elif macd_status == "bearish":
        score -= 12
    if change_60d is not None:
        if 0 <= change_60d <= 35:
            score += min(change_60d * 0.35, 12)
        elif change_60d > 60:
            score -= min((change_60d - 60) * 0.20, 12)
        elif change_60d < -25:
            score -= min(abs(change_60d + 25) * 0.25, 10)
    if rsi_status == "oversold":
        score += 4
    elif rsi_status == "overbought":
        score -= 6
    return max(0.0, min(score, 100.0))


def _last_float(series: pd.Series) -> float | None:
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return round(float(value), 4)


def _is_true(value: bool) -> bool:
    return bool(value)
