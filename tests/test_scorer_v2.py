# -*- coding: utf-8 -*-
"""Tests for scorer_v2.py — V2 全维度评分引擎。"""

import pytest
from alphasift.scorer_v2 import (
    _build_beta_scored,
    compute_industry_pe_averages,
    generate_signals,
    score_snapshot,
    suggest_position,
)
from alphasift.track_analyzer import TrackAnalyzer, TrackAnalyzerConfig
from alphasift.moat_scorer import MoatScorer, MoatScorerConfig


@pytest.fixture
def base_strategy():
    return {
        "track_analysis": {
            "policy_orientation": {
                "policy_boost": 15,
                "policy_keywords_positive": ["新能源", "AI", "半导体"],
                "policy_keywords_negative": ["煤炭", "钢铁"],
            },
            "industry_cycle": {"growth_stage_boost": 10, "decline_stage_penalty": -100},
            "supply_demand": {"shortage_boost": 8, "oversupply_penalty": -8},
            "prosperity": {"prosperity_boost": 12, "prosperity_penalty": -10},
        },
        "company_analysis": {
            "competitive_moat": {"moat_boost": 12},
            "financial_health": {
                "financial_boost": 10,
                "gross_margin_premium_pct": 10,
                "ocf_to_net_profit_min_pct": 80,
                "max_debt_to_asset_pct": 60,
                "min_current_ratio": 1.0,
            },
            "management_quality": {"management_boost": 8},
        },
        "buy_conditions": {
            "min_total_score": 50,
            "strong_buy_threshold": 70,
            "condition_1_fundamental": {"weight": 25},
            "condition_2_technical": {
                "weight": 20,
                "turnover_rate_min_pct": 3,
                "turnover_rate_max_pct": 8,
            },
            "condition_3_capital": {"weight": 20},
            "condition_4_event": {"weight": 15},
            "condition_5_valuation": {
                "weight": 12,
                "pe_below_industry_avg_pct": 30,
                "peg_max": 1.0,
                "min_dividend_yield_pct": 3.0,
            },
            "condition_6_sentiment": {
                "weight": 8,
                "turnover_rate_min_pct": 1.0,
                "turnover_rate_max_pct": 5.0,
            },
        },
        "sell_conditions": {},
        "indicator_operations": {},
        "scoring": {"sell_threshold": 30, "strong_sell_threshold": 20},
        "supplementary_rules": {
            "position_management": {
                "max_single_stock_pct": 20,
                "max_single_sector_pct": 40,
                "min_cash_pct": 10,
            }
        },
        "buy_advice": {
            "position_sizing": {"max_single_stock_pct": 20},
        },
    }


@pytest.fixture
def base_snap():
    return {
        "name": "测试股份",
        "industry": "新能源",
        "concepts": "光伏",
        "price": 25.0,
        "pe_ratio": 15.0,
        "pb_ratio": 2.0,
        "total_mv": 30_000_000_000,
        "volume_ratio": 2.0,
        "turnover_rate": 4.0,
        "change_pct": 2.5,
        "change_60d": 15.0,
        "profit_yoy_pct": 35.0,
        "revenue_yoy_pct": 25.0,
        "gross_margin_pct": 30.0,
        "ocf_to_profit_ratio": 0.9,
        "debt_to_asset_pct": 45.0,
        "current_ratio": 1.5,
        "roe_pct": 18.0,
        "north_net_buy": 5_000_000,
        "dividend_yield": 3.5,
        "macd_dif": 0.5,
        "macd_dea": 0.3,
        "macd_hist": 0.2,
        "ma5": 26.0,
        "ma10": 25.0,
        "ma20": 24.0,
        "ma250": 0.0,
    }


# ── 政策导向测试 ─────────────────────────────────────────────

def test_policy_positive_keyword(base_strategy, base_snap):
    score, comp, meta = score_snapshot(base_strategy, base_snap)
    assert comp["policy_orientation"] == 15


def test_policy_negative_keyword_vetoes(base_strategy, base_snap):
    base_snap["industry"] = "煤炭开采"
    base_snap["name"] = "煤炭股份"
    _, comp, meta = score_snapshot(base_strategy, base_snap)
    assert comp["policy_orientation"] == -100
    assert any("POLICY_RESTRICTED" in f for f in meta["risk_flags"])


def test_policy_neutral(base_strategy, base_snap):
    base_snap["industry"] = "餐饮"
    base_snap["concepts"] = ""
    base_snap["name"] = "餐馆股份"
    _, comp, _ = score_snapshot(base_strategy, base_snap)
    assert comp["policy_orientation"] == 0.0


# ── TrackAnalyzer 委托测试 ────────────────────────────────────

def test_track_analyzer_delegation(base_strategy, base_snap):
    cfg = TrackAnalyzerConfig()
    ta = TrackAnalyzer(cfg)
    score_with, comp_with, _ = score_snapshot(base_strategy, base_snap, track_analyzer=ta)
    score_without, comp_without, _ = score_snapshot(base_strategy, base_snap, track_analyzer=None)
    # Both should produce same policy score since the logic is identical
    assert comp_with["policy_orientation"] == comp_without["policy_orientation"]


def test_moat_scorer_delegation(base_strategy, base_snap):
    cfg = MoatScorerConfig()
    ms = MoatScorer(cfg)
    # Without patent data, MoatScorer returns 0.0
    score_with, comp_with, _ = score_snapshot(base_strategy, base_snap, moat_scorer=ms)
    # Should fall back to proxy (PB/PE based)
    assert comp_with["competitive_moat"] > 0  # PB=2, PE=15 => proxy gives moat_boost


# ── 财务健康度测试 ────────────────────────────────────────────

def test_financial_health_good_metrics(base_strategy, base_snap):
    _, comp, _ = score_snapshot(base_strategy, base_snap)
    assert comp["financial_health"] >= 7  # good margins + ocf + debt + current ratio


def test_financial_health_high_debt(base_strategy, base_snap):
    base_snap["debt_to_asset_pct"] = 75.0
    _, comp, meta = score_snapshot(base_strategy, base_snap)
    assert comp["financial_health"] < 7  # penalty for high debt
    assert any("高负债" in f for f in meta["risk_flags"])


# ── 买入条件测试 ──────────────────────────────────────────────

def test_buy_fundamental_strong(base_strategy, base_snap):
    base_snap["profit_yoy_pct"] = 35.0
    base_snap["revenue_yoy_pct"] = 25.0
    _, comp, _ = score_snapshot(base_strategy, base_snap)
    assert comp["buy_fundamental"] == 25  # full weight


def test_buy_fundamental_weak(base_strategy, base_snap):
    base_snap["profit_yoy_pct"] = -5.0
    base_snap["revenue_yoy_pct"] = -3.0
    _, comp, _ = score_snapshot(base_strategy, base_snap)
    assert comp["buy_fundamental"] == 0.0


def test_buy_technical_with_ma250(base_strategy, base_snap):
    base_snap["ma250"] = 20.0  # price 25 > MA250 20
    base_snap["volume_surge_3d_pct"] = 60.0
    _, comp, _ = score_snapshot(base_strategy, base_snap)
    assert comp["buy_technical"] > 0


def test_buy_valuation_uses_industry_pe(base_strategy, base_snap):
    base_snap["pe_ratio"] = 10.0
    # Industry avg PE = 30 -> PE < 30*0.7=21 -> gets low PE bonus
    _, comp_low, _ = score_snapshot(base_strategy, base_snap, industry_pe_avg=30.0)
    # Industry avg PE = 10 -> PE < 10*0.7=7 -> no low PE bonus
    _, comp_high, _ = score_snapshot(base_strategy, base_snap, industry_pe_avg=10.0)
    assert comp_low["buy_valuation"] >= comp_high["buy_valuation"]


# ── 指标操作测试 ──────────────────────────────────────────────

def test_pe_overvalued_with_industry_avg(base_strategy, base_snap):
    base_snap["pe_ratio"] = 100.0
    # Industry avg = 30 -> threshold = 60 -> PE 100 > 60 -> penalty
    _, comp, _ = score_snapshot(base_strategy, base_snap, industry_pe_avg=30.0)
    assert "pe_overvalued_penalty" in comp
    assert comp["pe_overvalued_penalty"] == -8.0


def test_pe_undervalued_bonus(base_strategy, base_snap):
    base_snap["pe_ratio"] = 8.0
    base_snap["profit_yoy_pct"] = 50.0
    # Industry avg = 30 -> threshold = 21 -> PE 8 < 21 and PEG = 8/50 < 1
    _, comp, _ = score_snapshot(base_strategy, base_snap, industry_pe_avg=30.0)
    assert "pe_undervalued_bonus" in comp
    assert comp["pe_undervalued_bonus"] == 5.0


def test_northbound_normalized_by_market_cap(base_strategy, base_snap):
    base_snap["north_net_buy"] = 200_000_000  # 2亿 / 300亿 = 0.67%
    _, comp, _ = score_snapshot(base_strategy, base_snap)
    assert comp.get("northbound_bonus", 0) >= 4.0  # >= 0.5% => strong signal


# ── 行业PE均值计算测试 ────────────────────────────────────────

def test_compute_industry_pe_averages():
    snaps = [
        {"industry": "新能源", "pe_ratio": 20.0},
        {"industry": "新能源", "pe_ratio": 30.0},
        {"industry": "新能源", "pe_ratio": 40.0},
        {"industry": "医药", "pe_ratio": 15.0},
        {"industry": "医药", "pe_ratio": 25.0},
    ]
    result = compute_industry_pe_averages(snaps)
    assert result["新能源"] == pytest.approx(30.0)
    assert result["医药"] == pytest.approx(20.0)


def test_compute_industry_pe_skips_single_stock_industry():
    snaps = [
        {"industry": "新能源", "pe_ratio": 20.0},
        {"industry": "新能源", "pe_ratio": 30.0},
        {"industry": "稀有行业", "pe_ratio": 50.0},  # only 1 stock
    ]
    result = compute_industry_pe_averages(snaps)
    assert "新能源" in result
    assert "稀有行业" not in result  # < 2 stocks, skipped


# ── 卖出信号测试 ──────────────────────────────────────────────

def test_sell_signal_score_threshold(base_strategy, base_snap):
    signals = generate_signals(base_strategy, base_snap, score=25)
    assert signals["buy_signal"] == "sell"  # score < 30


def test_strong_sell_signal(base_strategy, base_snap):
    signals = generate_signals(base_strategy, base_snap, score=15)
    assert signals["buy_signal"] == "strong_sell"


def test_sell_fundamental_deterioration(base_strategy, base_snap):
    base_snap["profit_yoy_pct"] = -10.0
    base_snap["gross_margin_pct"] = -8.0
    signals = generate_signals(base_strategy, base_snap, score=50)
    assert any("基本面恶化" in s for s in signals["sell_signals"])


def test_sell_price_below_ma20(base_strategy, base_snap):
    base_snap["price"] = 22.0
    base_snap["ma20"] = 24.0
    signals = generate_signals(base_strategy, base_snap, score=50)
    assert any("跌破20日均线" in s for s in signals["sell_signals"])


def test_sell_price_below_ma250(base_strategy, base_snap):
    base_snap["price"] = 22.0
    base_snap["ma250"] = 24.0
    signals = generate_signals(base_strategy, base_snap, score=50)
    assert any("跌破年线" in s for s in signals["sell_signals"])


def test_sell_industry_downturn(base_strategy, base_snap):
    base_snap["industry_change_pct"] = -3.0
    signals = generate_signals(base_strategy, base_snap, score=50)
    assert any("行业大跌" in s for s in signals["sell_signals"])


def test_sell_northbound_pct(base_strategy, base_snap):
    base_snap["north_net_buy"] = -500_000_000  # -5亿 / 300亿 = -1.67%
    base_snap["total_mv"] = 30_000_000_000
    signals = generate_signals(base_strategy, base_snap, score=50)
    assert any("北向资金" in s for s in signals["sell_signals"])


def test_stop_loss_stop_profit(base_strategy, base_snap):
    base_snap["price"] = 20.0
    signals = generate_signals(base_strategy, base_snap, score=50)
    assert signals["stop_loss_price"] == 18.0  # 20 * 0.9
    assert signals["stop_profit_price"] == 26.0  # 20 * 1.3


# ── 仓位建议测试 ──────────────────────────────────────────────

def test_position_strong_buy(base_strategy):
    assert suggest_position(base_strategy, score=75) == 20  # max_single_stock_pct


def test_position_moderate_buy(base_strategy):
    assert suggest_position(base_strategy, score=55) == pytest.approx(12.0)  # 20 * 0.6


def test_position_weak(base_strategy):
    assert suggest_position(base_strategy, score=35) == pytest.approx(6.0)  # 20 * 0.3


def test_position_none(base_strategy):
    assert suggest_position(base_strategy, score=20) == 0.0


def test_position_reads_supplementary_rules(base_strategy):
    base_strategy["supplementary_rules"]["position_management"]["max_single_stock_pct"] = 15
    assert suggest_position(base_strategy, score=75) == 15
