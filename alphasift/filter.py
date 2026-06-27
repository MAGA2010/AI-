# -*- coding: utf-8 -*-
"""Stock filtering logic."""

import logging
from dataclasses import replace
from typing import Any

import pandas as pd

from alphasift.models import HardFilterConfig

logger = logging.getLogger(__name__)


class SnapshotFieldMissingError(Exception):
    """Raised when a required snapshot field is missing."""


_DAILY_FILTER_DEFAULTS = {
    "change_60d_min": None,
    "change_60d_max": None,
    "require_ma_bullish": False,
    "require_price_above_ma20": False,
    "signal_score_min": None,
    "macd_status_whitelist": None,
    "rsi_status_whitelist": None,
    "breakout_20d_pct_min": None,
    "breakout_20d_pct_max": None,
    "range_20d_pct_max": None,
    "volume_ratio_20d_min": None,
    "volume_ratio_20d_max": None,
    "body_pct_min": None,
    "body_pct_max": None,
    "pullback_to_ma20_pct_min": None,
    "pullback_to_ma20_pct_max": None,
    "consolidation_days_20d_min": None,
    "consolidation_days_20d_max": None,
}


# ═══════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════

def to_num(val: Any) -> float | None:
    """Convert a value to float, returning None if not possible."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════
#  DataFrame 过滤（原有逻辑）
# ═══════════════════════════════════════════════════════════════

def requires_daily_features(filters: HardFilterConfig) -> bool:
    """Check if any daily K-line filters are configured."""
    return any([
        filters.change_60d_min is not None,
        filters.change_60d_max is not None,
        filters.require_ma_bullish,
        filters.require_price_above_ma20,
        filters.signal_score_min is not None,
        bool(filters.macd_status_whitelist),
        bool(filters.rsi_status_whitelist),
        filters.breakout_20d_pct_min is not None,
        filters.breakout_20d_pct_max is not None,
        filters.range_20d_pct_max is not None,
        filters.volume_ratio_20d_min is not None,
        filters.volume_ratio_20d_max is not None,
        filters.body_pct_min is not None,
        filters.body_pct_max is not None,
        filters.pullback_to_ma20_pct_min is not None,
        filters.pullback_to_ma20_pct_max is not None,
        filters.consolidation_days_20d_min is not None,
        filters.consolidation_days_20d_max is not None,
    ])


def without_daily_filters(filters: HardFilterConfig) -> HardFilterConfig:
    """Return a copy with daily K-line filters disabled."""
    return replace(filters, **_DAILY_FILTER_DEFAULTS)


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _filter_min(df: pd.DataFrame, col_names: list[str], value: float | None) -> None:
    if value is None:
        return
    if df.empty:
        return
    col = _find_col(df, col_names)
    if not col:
        raise SnapshotFieldMissingError(
            f"Missing required snapshot column for min filter {col_names}: "
            f"configured value={value}"
        )
    series = pd.to_numeric(df[col], errors="coerce")
    df.drop(df[(series < value) | series.isna()].index, inplace=True)


def _filter_max(df: pd.DataFrame, col_names: list[str], value: float | None) -> None:
    if value is None:
        return
    if df.empty:
        return
    col = _find_col(df, col_names)
    if not col:
        raise SnapshotFieldMissingError(
            f"Missing required snapshot column for max filter {col_names}: "
            f"configured value={value}"
        )
    series = pd.to_numeric(df[col], errors="coerce")
    df.drop(df[(series > value) | series.isna()].index, inplace=True)


def _filter_bool_true(df: pd.DataFrame, col_name: str, enabled: bool) -> None:
    if not enabled:
        return
    if df.empty:
        return
    if col_name not in df.columns:
        raise SnapshotFieldMissingError(
            f"Missing required daily feature column for bool filter: {col_name}"
        )
    df.drop(df[df[col_name] != True].index, inplace=True)  # noqa: E712


def _filter_in(df: pd.DataFrame, col_name: str, allowed: list[str] | None) -> None:
    if not allowed:
        return
    if df.empty:
        return
    if col_name not in df.columns:
        raise SnapshotFieldMissingError(
            f"Missing required daily feature column for whitelist filter: {col_name}"
        )
    allowed_set = {str(item) for item in allowed}
    df.drop(df[~df[col_name].astype(str).isin(allowed_set)].index, inplace=True)


def apply_filters(df: pd.DataFrame, filters: HardFilterConfig) -> pd.DataFrame:
    """Apply all hard filters to a snapshot DataFrame."""
    if df.empty:
        return df

    result = df.copy()

    # Price filters
    _filter_min(result, ["price"], filters.price_min)
    _filter_max(result, ["price"], filters.price_max)

    # Amount filters
    _filter_min(result, ["amount"], filters.amount_min)
    _filter_max(result, ["amount"], filters.amount_max)

    # Market cap filters
    _filter_min(result, ["total_mv"], filters.market_cap_min)
    _filter_max(result, ["total_mv"], filters.market_cap_max)

    # PE filters
    _filter_min(result, ["pe_ratio", "pe_ttm"], filters.pe_ttm_min)
    _filter_max(result, ["pe_ratio", "pe_ttm"], filters.pe_ttm_max)

    # PB filters
    _filter_min(result, ["pb_ratio"], filters.pb_min)
    _filter_max(result, ["pb_ratio"], filters.pb_max)

    # Volume ratio filters
    _filter_min(result, ["volume_ratio"], filters.volume_ratio_min)

    # Turnover rate filters
    _filter_min(result, ["turnover_rate"], filters.turnover_rate_min)
    _filter_max(result, ["turnover_rate"], filters.turnover_rate_max)

    # Change percentage filters
    _filter_min(result, ["change_pct"], filters.change_pct_min)
    _filter_max(result, ["change_pct"], filters.change_pct_max)

    # 60-day change filters (daily features)
    _filter_min(result, ["change_60d"], filters.change_60d_min)
    _filter_max(result, ["change_60d"], filters.change_60d_max)

    # Boolean filters (daily features)
    _filter_bool_true(result, "ma_bullish", filters.require_ma_bullish)
    _filter_bool_true(result, "price_above_ma20", filters.require_price_above_ma20)

    # Signal score filter (daily features)
    _filter_min(result, ["signal_score"], filters.signal_score_min)

    # Whitelist filters (daily features)
    _filter_in(result, "macd_status", filters.macd_status_whitelist)
    _filter_in(result, "rsi_status", filters.rsi_status_whitelist)

    # Breakout percentage filters (daily features)
    _filter_min(result, ["breakout_20d_pct"], filters.breakout_20d_pct_min)
    _filter_max(result, ["breakout_20d_pct"], filters.breakout_20d_pct_max)

    # Range percentage filter (daily features)
    _filter_max(result, ["range_20d_pct"], filters.range_20d_pct_max)

    # Volume ratio 20-day filters (daily features)
    _filter_min(result, ["volume_ratio_20d"], filters.volume_ratio_20d_min)
    _filter_max(result, ["volume_ratio_20d"], filters.volume_ratio_20d_max)

    # Body percentage filters (daily features)
    _filter_min(result, ["body_pct"], filters.body_pct_min)
    _filter_max(result, ["body_pct"], filters.body_pct_max)

    # Pullback to MA20 percentage filters (daily features)
    _filter_min(result, ["pullback_to_ma20_pct"], filters.pullback_to_ma20_pct_min)
    _filter_max(result, ["pullback_to_ma20_pct"], filters.pullback_to_ma20_pct_max)

    # Consolidation days filters (daily features)
    _filter_min(result, ["consolidation_days_20d"], filters.consolidation_days_20d_min)
    _filter_max(result, ["consolidation_days_20d"], filters.consolidation_days_20d_max)

    # === V2 新增：北交所排除 ===
    if filters.exclude_bse and "code" in result.columns:
        result = result[~result["code"].astype(str).str.startswith("8")]

    # === V2 新增：次新股排除 ===
    if filters.exclude_sub_new:
        date_col = _find_col(result, ["list_date", "ipo_date"])
        if date_col:
            try:
                dates = pd.to_datetime(result[date_col], errors="coerce")
                cutoff = pd.Timestamp.now() - pd.Timedelta(days=filters.sub_new_min_days)
                result = result[dates.isna() | (dates <= cutoff)]
            except Exception:
                pass  # 日期列无法解析，不做过滤

    # === V2 新增：连续亏损股排除 ===
    if filters.exclude_consecutive_loss_years is not None:
        loss_col = _find_col(result, ["consecutive_loss_years"])
        if loss_col:
            loss_years = pd.to_numeric(result[loss_col], errors="coerce")
            result = result[loss_years.isna() | (loss_years < filters.exclude_consecutive_loss_years)]

    return result


# ═══════════════════════════════════════════════════════════════
#  V2 新增：单条快照硬性过滤（#34 风险规避 + 策略补充规则）
# ═══════════════════════════════════════════════════════════════

def filter_single_snapshot(snapshot: dict, config: HardFilterConfig) -> bool:
    """Check if a single snapshot dict passes all hard filters.

    Returns True if the snapshot should be kept, False if it should be filtered out.
    Covers: #34 风险规避 (ST, delisting risk, consecutive losses, liquidity).
    """
    name = str(snapshot.get("name", ""))

    # #34 ST 股剔除
    if config.exclude_st and ("ST" in name or "st" in name or "*ST" in name):
        return False

    # 价格范围
    price = to_num(snapshot.get("price"))
    if price is not None:
        if config.price_min is not None and price < config.price_min:
            return False
        if config.price_max is not None and price > config.price_max:
            return False

    # 成交量范围
    amount = to_num(snapshot.get("amount"))
    if amount is not None:
        if config.amount_min is not None and amount < config.amount_min:
            return False
        if config.amount_max is not None and amount > config.amount_max:
            return False

    # 市值范围
    market_cap = to_num(snapshot.get("total_mv"))
    if market_cap is not None:
        if config.market_cap_min is not None and market_cap < config.market_cap_min:
            return False
        if config.market_cap_max is not None and market_cap > config.market_cap_max:
            return False

    # 市盈率范围
    pe = to_num(snapshot.get("pe_ratio"))
    if pe is not None:
        if config.pe_ttm_min is not None and pe < config.pe_ttm_min:
            return False
        if config.pe_ttm_max is not None and pe > config.pe_ttm_max:
            return False

    # 市净率范围
    pb = to_num(snapshot.get("pb_ratio"))
    if pb is not None:
        if config.pb_min is not None and pb < config.pb_min:
            return False
        if config.pb_max is not None and pb > config.pb_max:
            return False

    # 涨跌幅范围
    change_pct = to_num(snapshot.get("change_pct"))
    if change_pct is not None:
        if config.change_pct_min is not None and change_pct < config.change_pct_min:
            return False
        if config.change_pct_max is not None and change_pct > config.change_pct_max:
            return False

    # 换手率范围
    turnover = to_num(snapshot.get("turnover_rate"))
    if turnover is not None:
        if config.turnover_rate_min is not None and turnover < config.turnover_rate_min:
            return False
        if config.turnover_rate_max is not None and turnover > config.turnover_rate_max:
            return False

    # 量比范围
    volume_ratio = to_num(snapshot.get("volume_ratio"))
    if volume_ratio is not None:
        if config.volume_ratio_min is not None and volume_ratio < config.volume_ratio_min:
            return False

    # === V2 新增硬性过滤（#34 风险规避 + 策略补充规则）===

    # 北交所排除（#34 流动性不足）
    if config.exclude_bse:
        code = str(snapshot.get("code", ""))
        if code.startswith("8"):
            return False

    # 日均成交额下限（#34 日均成交额<5000万剔除）
    if config.min_daily_amount is not None:
        if amount is not None and amount < config.min_daily_amount:
            return False

    # 涨停股排除（接近涨停的股票追高风险大）
    if change_pct is not None and change_pct >= 9.5:
        return False

    # 次新股排除（#34 补充规则：上市不足N日的新股风险大）
    if config.exclude_sub_new:
        list_date = snapshot.get("list_date") or snapshot.get("ipo_date")
        if list_date is not None:
            try:
                from datetime import datetime, timedelta
                if isinstance(list_date, str) and len(list_date) >= 8:
                    dt = datetime.strptime(list_date[:10], "%Y-%m-%d")
                    if (datetime.now() - dt).days < config.sub_new_min_days:
                        return False
            except (ValueError, TypeError):
                pass  # 日期格式无法解析，不做过滤

    # 连续亏损股排除（#34 剔除连续N年亏损股）
    if config.exclude_consecutive_loss_years is not None:
        loss_years = to_num(snapshot.get("consecutive_loss_years"))
        if loss_years is not None and loss_years >= config.exclude_consecutive_loss_years:
            return False

    return True


# 兼容别名：pipeline.py 导入 apply_hard_filters
apply_hard_filters = apply_filters
