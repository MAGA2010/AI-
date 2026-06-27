# -*- coding: utf-8 -*-
"""Risk overlay scoring module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RiskConfig:
    """Configuration for risk scoring."""
    max_pe: float = 100.0
    max_pb: float = 20.0
    max_change_pct: float = 9.5
    max_turnover: float = 15.0
    min_volume_ratio: float = 0.3
    max_volume_ratio: float = 10.0
    max_debt_ratio: float = 100.0
    min_market_cap: float = 1_000_000_000
    penalty_multiplier: float = 1.0
    exclude_threshold: float = 80.0


@dataclass
class RiskResult:
    """Risk scoring result."""
    score: float = 0.0
    level: str = "low"
    flags: list[str] = field(default_factory=list)
    penalty: float = 0.0
    excluded: bool = False


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


class RiskScorer:
    """Computes stock risk scores and generates risk flags."""

    def __init__(self, config: RiskConfig):
        self.config = config

    def score(self, snap: dict, strategy_risk: dict[str, Any] | None = None) -> RiskResult:
        """Score a stock snapshot and return risk assessment.

        Covers:
          #19 基本面卖出信号
          #20 技术面卖出信号
          #21 资金面卖出信号
          #22 市场环境卖出信号
          #29 止损
          #30 止盈
        """
        flags: list[str] = []
        score = 0.0

        # Valuation risk
        valuation_risk, valuation_flags = self._score_valuation(snap, strategy_risk)
        score += valuation_risk
        flags.extend(valuation_flags)

        # Volatility risk
        volatility_risk, volatility_flags = self._score_volatility(snap)
        score += volatility_risk
        flags.extend(volatility_flags)

        # Liquidity risk
        liquidity_risk, liquidity_flags = self._score_liquidity(snap)
        score += liquidity_risk
        flags.extend(liquidity_flags)

        # Fundamental risk (#19)
        fundamental_risk, fundamental_flags = self._score_fundamentals(snap)
        score += fundamental_risk
        flags.extend(fundamental_flags)

        # Market risk
        market_risk, market_flags = self._score_market(snap)
        score += market_risk
        flags.extend(market_flags)

        # Capital flow risk (#21 资金面卖出)
        capital_risk, capital_flags = self._score_capital_flow(snap)
        score += capital_risk
        flags.extend(capital_flags)

        # Market environment risk (#22 市场环境卖出)
        env_risk, env_flags = self._score_market_environment(snap)
        score += env_risk
        flags.extend(env_flags)

        # Normalize score to 0-100
        score = max(0.0, min(100.0, score))

        # Determine risk level
        risk_level = self._determine_risk_level(score)

        # Calculate penalty
        penalty = self._calculate_penalty(score, risk_level)

        # Check if should be excluded
        excluded = self._should_exclude(score, flags)

        return RiskResult(
            score=score,
            level=risk_level,
            flags=flags,
            penalty=penalty,
            excluded=excluded
        )

    def _score_valuation(self, snap: dict, strategy_risk: dict[str, Any] | None = None) -> tuple[float, list[str]]:
        """Score valuation risk."""
        flags = []
        score = 0.0

        pe = _safe_float(snap.get("pe_ratio"))
        pb = _safe_float(snap.get("pb_ratio"))

        if pe > 0 and pe > self.config.max_pe:
            score += 30.0
            flags.append(f"High PE: {pe:.1f}")
        elif pe < 0:
            score += 20.0
            flags.append("Negative PE")

        if pb > 0 and pb > self.config.max_pb:
            score += 20.0
            flags.append(f"High PB: {pb:.1f}")

        return score, flags

    def _score_volatility(self, snap: dict) -> tuple[float, list[str]]:
        """Score volatility risk."""
        flags = []
        score = 0.0

        change_pct = _safe_float(snap.get("change_pct"))
        if abs(change_pct) > self.config.max_change_pct:
            score += 25.0
            flags.append(f"High volatility: {change_pct:.1f}%")

        return score, flags

    def _score_liquidity(self, snap: dict) -> tuple[float, list[str]]:
        """Score liquidity risk."""
        flags = []
        score = 0.0

        turnover = _safe_float(snap.get("turnover_rate"))
        volume_ratio = _safe_float(snap.get("volume_ratio"))

        if turnover > self.config.max_turnover:
            score += 20.0
            flags.append(f"High turnover: {turnover:.1f}%")

        if volume_ratio < self.config.min_volume_ratio:
            score += 15.0
            flags.append(f"Low volume ratio: {volume_ratio:.2f}")
        elif volume_ratio > self.config.max_volume_ratio:
            score += 10.0
            flags.append(f"High volume ratio: {volume_ratio:.2f}")

        return score, flags

    def _score_fundamentals(self, snap: dict) -> tuple[float, list[str]]:
        """Score fundamental risk.

        Covers: #19 基本面卖出信号（净利润增速<0且毛利率环比下滑≥5%）。
        """
        flags = []
        score = 0.0

        profit_yoy = _safe_float(snap.get("profit_yoy_pct"))
        gross_margin = _safe_float(snap.get("gross_margin_pct"))
        debt_ratio = _safe_float(snap.get("debt_to_asset_pct"))
        market_cap = _safe_float(snap.get("total_mv"))

        # #19 基本面卖出：净利润负增长
        if profit_yoy < 0:
            score += 25.0
            flags.append(f"利润负增长: {profit_yoy:.1f}%")
        elif profit_yoy < 10:
            score += 10.0
            flags.append(f"利润低增长: {profit_yoy:.1f}%")

        # #19 基本面卖出：毛利率下滑
        if gross_margin < 0:
            score += 20.0
            flags.append(f"毛利率为负: {gross_margin:.1f}%")

        # 高负债风险
        if debt_ratio > self.config.max_debt_ratio:
            score += 30.0
            flags.append(f"High debt ratio: {debt_ratio:.1f}%")
        elif debt_ratio > 80:
            score += 15.0
            flags.append(f"高负债: {debt_ratio:.1f}%")

        # 小市值风险
        if market_cap < self.config.min_market_cap:
            score += 15.0
            flags.append(f"小市值: {market_cap/1e8:.0f}亿")

        return score, flags

    def _score_market(self, snap: dict) -> tuple[float, list[str]]:
        """Score market risk."""
        flags = []
        score = 0.0

        change_pct = _safe_float(snap.get("change_pct"))

        if change_pct > 5.0:
            score += 10.0
            flags.append(f"短期大涨: {change_pct:.1f}%")
        elif change_pct < -5.0:
            score += 15.0
            flags.append(f"短期大跌: {change_pct:.1f}%")

        return score, flags

    # ── V2 新增：资金面卖出信号 (#21) ─────────────────────────

    def _score_capital_flow(self, snap: dict) -> tuple[float, list[str]]:
        """Score capital flow risk.

        Covers: #21 资金面卖出信号（北向资金单日净卖出≥1%流通市值）。
        """
        flags = []
        score = 0.0

        # 北向资金净卖出
        north_net = _safe_float(snap.get("north_net_buy"))
        market_cap = _safe_float(snap.get("total_mv"))
        if north_net < 0 and market_cap > 0:
            sell_pct = abs(north_net) / market_cap * 100
            if sell_pct >= 1.0:
                score += 30.0
                flags.append(f"北向资金大幅净卖出: {sell_pct:.2f}%流通市值")
            elif sell_pct >= 0.5:
                score += 15.0
                flags.append(f"北向资金净卖出: {sell_pct:.2f}%流通市值")

        return score, flags

    # ── V2 新增：市场环境卖出信号 (#22) ─────────────────────

    def _score_market_environment(self, snap: dict) -> tuple[float, list[str]]:
        """Score market environment risk.

        Covers: #22 市场环境卖出信号（大盘/行业跌幅）。
        """
        flags = []
        score = 0.0

        # 个股所属行业跌幅
        industry_change = _safe_float(snap.get("industry_change_pct"))
        if industry_change < -2.0:
            score += 15.0
            flags.append(f"行业大跌: {industry_change:.1f}%")

        # 个股跌幅（作为市场环境的代理）
        change_pct = _safe_float(snap.get("change_pct"))
        if change_pct < -3.0:
            score += 10.0
            flags.append(f"个股大跌: {change_pct:.1f}%")

        return score, flags

    # ── 风险等级判定 ────────────────────────────────────────

    def _determine_risk_level(self, score: float) -> str:
        """Determine risk level based on score."""
        if score >= 70:
            return "critical"
        elif score >= 50:
            return "high"
        elif score >= 30:
            return "medium"
        else:
            return "low"

    def _calculate_penalty(self, score: float, risk_level: str) -> float:
        """Calculate score penalty based on risk."""
        if risk_level == "critical":
            return score * 0.8 * self.config.penalty_multiplier
        elif risk_level == "high":
            return score * 0.5 * self.config.penalty_multiplier
        elif risk_level == "medium":
            return score * 0.2 * self.config.penalty_multiplier
        return 0.0

    def _should_exclude(self, score: float, flags: list[str]) -> bool:
        """Check if stock should be excluded."""
        if score >= self.config.exclude_threshold:
            return True
        critical_keywords = ["High debt", "小市值", "Negative PE", "北向资金大幅净卖出"]
        for flag in flags:
            for keyword in critical_keywords:
                if keyword in flag:
                    return True
        return False


# ═══════════════════════════════════════════════════════════════
#  Pipeline 集成函数
# ═══════════════════════════════════════════════════════════════

def apply_risk_overlay(
    picks: list,
    *,
    max_penalty: float = 15.0,
    veto_high_risk: bool = True,
    profile: dict | None = None,
) -> tuple[list, list[str]]:
    """Apply risk overlay to picks list.

    Returns (modified_picks, degradation_notes).
    """
    from alphasift.models import Pick

    config = RiskConfig()
    if profile:
        for k, v in profile.items():
            if hasattr(config, k):
                setattr(config, k, v)

    scorer = RiskScorer(config)
    degradation: list[str] = []
    result: list = []

    for pick in picks:
        snap = {
            "pe_ratio": pick.pe_ratio,
            "pb_ratio": pick.pb_ratio,
            "change_pct": pick.change_pct,
            "turnover_rate": pick.turnover_rate,
            "volume_ratio": pick.volume_ratio,
            "total_mv": pick.total_mv,
            "profit_yoy_pct": getattr(pick, "profit_yoy_pct", None),
            "gross_margin_pct": getattr(pick, "gross_margin_pct", None),
            "debt_to_asset_pct": getattr(pick, "debt_to_asset_pct", None),
            "north_net_buy": getattr(pick, "north_net_buy", None),
            "industry_change_pct": pick.industry_change_pct,
        }
        risk_result = scorer.score(snap)

        pick.risk_score = risk_result.score
        pick.risk_level = risk_result.level
        pick.risk_penalty = min(risk_result.penalty, max_penalty)
        pick.risk_flags = risk_result.flags
        pick.excluded_by_risk = risk_result.excluded

        if risk_result.excluded and veto_high_risk:
            degradation.append(f"{pick.code} {pick.name}: excluded by risk ({risk_result.level})")
            continue

        result.append(pick)

    return result, degradation


def apply_portfolio_overlay(
    picks: list,
    *,
    max_same_sector: int = 3,
    concentration_penalty: float = 5.0,
    profile: dict | None = None,
) -> tuple[list, list[str]]:
    """Apply portfolio diversity overlay to picks.

    Returns (modified_picks, concentration_notes).
    """
    buckets_key = "llm_sector"
    max_same = max_same_sector
    penalty = concentration_penalty

    if profile:
        max_same = profile.get("max_same_bucket", max_same)
        penalty = profile.get("concentration_penalty", penalty)
        buckets = profile.get("buckets")
        if isinstance(buckets, list) and len(buckets) > 0:
            buckets_key = buckets[0]
        elif isinstance(buckets, str) and buckets:
            buckets_key = buckets
        # else keep the default "llm_sector"

    notes: list[str] = []
    sector_counts: dict[str, int] = {}

    for pick in picks:
        bucket = getattr(pick, buckets_key, "") or pick.industry or "unknown"
        count = sector_counts.get(bucket, 0) + 1
        sector_counts[bucket] = count

        if count > max_same:
            pick.portfolio_penalty = penalty * (count - max_same)
            pick.portfolio_flags.append(f"concentration: {bucket}")
            notes.append(f"{pick.code}: concentration in {bucket} ({count})")

    return picks, notes
