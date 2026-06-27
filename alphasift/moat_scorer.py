# -*- coding: utf-8 -*-
"""竞争壁垒评分模块 — 专利/市占率/品牌护城河。

覆盖策略条目：
  #5  竞争壁垒（核心技术/专利/品牌护城河）
  #10 选股规则3：核心壁垒（专利≥10/市占率≥15%/品牌前5）
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MoatScorerConfig:
    min_core_patents: int = 10
    min_market_share_pct: float = 15.0
    brand_rank_top_n: int = 5
    weight_patents: float = 30.0
    weight_market_share: float = 40.0
    weight_brand_rank: float = 30.0
    moat_threshold: float = 60.0
    moat_boost: float = 12.0


def _build_moat_config(strategy: dict) -> MoatScorerConfig:
    """从策略 YAML 构建配置。"""
    ca = strategy.get("company_analysis", {})
    cm = ca.get("competitive_moat", {})
    cfg = MoatScorerConfig()
    cfg.min_core_patents = cm.get("min_core_patents", cfg.min_core_patents)
    cfg.min_market_share_pct = cm.get("min_market_share_pct", cfg.min_market_share_pct)
    cfg.brand_rank_top_n = cm.get("brand_rank_top_n", cfg.brand_rank_top_n)
    w = cm.get("weights", {})
    cfg.weight_patents = w.get("patents", cfg.weight_patents)
    cfg.weight_market_share = w.get("market_share", cfg.weight_market_share)
    cfg.weight_brand_rank = w.get("brand_rank", cfg.weight_brand_rank)
    cfg.moat_threshold = cm.get("moat_score_threshold", cfg.moat_threshold)
    cfg.moat_boost = cm.get("moat_boost", cfg.moat_boost)
    return cfg


class MoatScorer:
    """竞争壁垒评分器。"""

    def __init__(self, cfg: MoatScorerConfig):
        self.cfg = cfg

    def score_patents(self, core_patents: int | None) -> float:
        """专利评分（0-100）。

        满足：#5 竞争壁垒 + #10 选股规则3（专利≥10项）。
        """
        if core_patents is None:
            return 0.0
        if core_patents >= self.cfg.min_core_patents:
            return 100.0
        return (core_patents / self.cfg.min_core_patents) * 100.0

    def score_market_share(self, market_share_pct: float | None) -> float:
        """市占率评分（0-100）。

        满足：#5 竞争壁垒 + #10 选股规则3（市占率≥15%）。
        """
        if market_share_pct is None:
            return 0.0
        if market_share_pct >= self.cfg.min_market_share_pct:
            return 100.0
        return (market_share_pct / self.cfg.min_market_share_pct) * 100.0

    def score_brand_rank(self, brand_rank: int | None) -> float:
        """品牌排名评分（0-100）。

        满足：#5 竞争壁垒 + #10 选股规则3（品牌前5）。
        """
        if brand_rank is None:
            return 0.0
        if brand_rank <= self.cfg.brand_rank_top_n:
            return 100.0
        # 排名越低分越低，最多到第20名得0分
        if brand_rank >= 20:
            return 0.0
        return max(0.0, 100.0 * (1.0 - (brand_rank - self.cfg.brand_rank_top_n) / 15.0))

    def score_moat(
        self,
        core_patents: int | None = None,
        market_share_pct: float | None = None,
        brand_rank: int | None = None,
    ) -> tuple[float, str]:
        """综合壁垒评分，返回 (score_delta, reason)。

        评分逻辑：
        - 三项加权平均得综合分
        - 满足任意一项即有加分（any条件）
        - 综合分≥阈值 → full boost
        """
        s_pat = self.score_patents(core_patents)
        s_ms = self.score_market_share(market_share_pct)
        s_br = self.score_brand_rank(brand_rank)

        w_pat = self.cfg.weight_patents
        w_ms = self.cfg.weight_market_share
        w_br = self.cfg.weight_brand_rank

        total_weight = w_pat + w_ms + w_br
        if total_weight == 0:
            return 0.0, ""

        composite = (s_pat * w_pat + s_ms * w_ms + s_br * w_br) / total_weight

        reasons = []
        if s_pat >= 100:
            reasons.append(f"核心专利≥{self.cfg.min_core_patents}项")
        if s_ms >= 100:
            reasons.append(f"市占率≥{self.cfg.min_market_share_pct}%")
        if s_br >= 100:
            reasons.append(f"品牌排名前{self.cfg.brand_rank_top_n}")

        if composite >= self.cfg.moat_threshold:
            return self.cfg.moat_boost, f"竞争壁垒强: {', '.join(reasons) if reasons else '综合评分{:.0f}'.format(composite)}"

        # 部分加分（按比例）
        partial = self.cfg.moat_boost * (composite / self.cfg.moat_threshold) * 0.5
        if reasons:
            return partial, f"部分壁垒: {', '.join(reasons)}"
        return 0.0, ""
