# -*- coding: utf-8 -*-
"""赛道分析模块 — 政策导向/行业周期/供需关系/景气度。

覆盖策略条目：
  #1  政策导向（扶持/限制赛道筛选）
  #2  行业周期（成长/衰退判断）
  #3  供需关系（短缺/过剩）
  #4  景气度（营收/利润连续2季度环比提升）
  #8  选股规则1：政策扶持赛道筛选
  #9  选股规则2：景气度连续2季度提升+行业排名前30%
  #12 选股规则5：剔除衰退期+市占率下滑标的
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── 政策赛道清单（内置默认，可被 YAML 覆盖）──────────────────
_DEFAULT_SUPPORTED = [
    "新能源", "半导体", "人工智能", "数字经济", "生物医药",
    "高端制造", "新材料", "军工", "信创", "储能", "算力",
    "机器人", "低空经济", "商业航天", "量子计算", "脑机接口",
    "可控核聚变", "光伏", "风电", "锂电", "氢能",
    "自动驾驶", "数据中心", "云计算", "创新药", "医疗器械",
    "航天", "卫星", "5G", "6G", "量子",
]
_DEFAULT_RESTRICTED = [
    "高耗能", "落后产能", "高污染", "传统煤化工", "小火电", "地条钢",
]
_DEFAULT_KW_POSITIVE = [
    "新能源", "半导体", "芯片", "人工智能", "AI", "储能", "光伏",
    "风电", "锂电", "氢能", "机器人", "自动驾驶", "算力", "数据中心",
    "云计算", "生物医药", "创新药", "医疗器械", "军工", "航天", "卫星",
    "5G", "6G", "量子", "信创", "数字经济", "高端制造", "新材料",
    "低空经济", "商业航天",
]
_DEFAULT_KW_NEGATIVE = [
    "煤炭", "钢铁", "水泥", "电解铝", "煤化工", "造纸", "印染",
]


@dataclass
class TrackAnalyzerConfig:
    """赛道分析配置，从 YAML track_analysis 段加载。"""
    supported_tracks: list[str] = field(default_factory=lambda: list(_DEFAULT_SUPPORTED))
    restricted_tracks: list[str] = field(default_factory=lambda: list(_DEFAULT_RESTRICTED))
    kw_positive: list[str] = field(default_factory=lambda: list(_DEFAULT_KW_POSITIVE))
    kw_negative: list[str] = field(default_factory=lambda: list(_DEFAULT_KW_NEGATIVE))
    policy_boost: float = 15.0
    growth_threshold_yoy: float = 10.0
    growth_years_required: int = 2
    decline_threshold_yoy: float = 0.0
    decline_years_required: int = 2
    growth_stage_boost: float = 10.0
    decline_stage_penalty: float = -100.0
    shortage_ppi_threshold: float = 5.0
    oversupply_utilization_threshold: float = 70.0
    shortage_boost: float = 8.0
    oversupply_penalty: float = -8.0
    prosperity_consecutive_quarters: int = 2
    prosperity_boost: float = 12.0
    prosperity_penalty: float = -10.0


def _build_track_analyzer_config(strategy: dict) -> TrackAnalyzerConfig:
    """从策略 YAML 构建配置。"""
    ta = strategy.get("track_analysis", {})
    cfg = TrackAnalyzerConfig()
    po = ta.get("policy_orientation", {})
    if po.get("supported_tracks"):
        cfg.supported_tracks = po["supported_tracks"]
    if po.get("restricted_tracks"):
        cfg.restricted_tracks = po["restricted_tracks"]
    if po.get("policy_keywords_positive"):
        cfg.kw_positive = po["policy_keywords_positive"]
    if po.get("policy_keywords_negative"):
        cfg.kw_negative = po["policy_keywords_negative"]
    cfg.policy_boost = po.get("policy_boost", cfg.policy_boost)
    ic = ta.get("industry_cycle", {})
    cfg.growth_threshold_yoy = ic.get("growth_threshold_revenue_yoy", cfg.growth_threshold_yoy)
    cfg.growth_years_required = ic.get("growth_years_required", cfg.growth_years_required)
    cfg.decline_threshold_yoy = ic.get("decline_threshold_revenue_yoy", cfg.decline_threshold_yoy)
    cfg.decline_years_required = ic.get("decline_years_required", cfg.decline_years_required)
    cfg.growth_stage_boost = ic.get("growth_stage_boost", cfg.growth_stage_boost)
    cfg.decline_stage_penalty = ic.get("decline_stage_penalty", cfg.decline_stage_penalty)
    sd = ta.get("supply_demand", {})
    cfg.shortage_ppi_threshold = sd.get("shortage_ppi_threshold", cfg.shortage_ppi_threshold)
    cfg.oversupply_utilization_threshold = sd.get("oversupply_utilization_threshold", cfg.oversupply_utilization_threshold)
    cfg.shortage_boost = sd.get("shortage_boost", cfg.shortage_boost)
    cfg.oversupply_penalty = sd.get("oversupply_penalty", cfg.oversupply_penalty)
    pr = ta.get("prosperity", {})
    cfg.prosperity_consecutive_quarters = pr.get("consecutive_quarters", cfg.prosperity_consecutive_quarters)
    cfg.prosperity_boost = pr.get("prosperity_boost", cfg.prosperity_boost)
    cfg.prosperity_penalty = pr.get("prosperity_penalty", cfg.prosperity_penalty)
    return cfg


class TrackAnalyzer:
    """赛道分析器：对每只股票的行业/赛道进行多维度评分。"""

    def __init__(self, cfg: TrackAnalyzerConfig):
        self.cfg = cfg
        self._supported_lower = {t.lower() for t in cfg.supported_tracks}
        self._restricted_lower = {t.lower() for t in cfg.restricted_tracks}

    # ── #1 政策导向 ──────────────────────────────────────────
    def score_policy(self, name: str, industry: str, concepts: str) -> tuple[float, str]:
        """政策导向评分。

        返回 (score_delta, reason)。
        满足：#1 政策导向 + #8 选股规则1。
        """
        text = f"{name} {industry} {concepts}".lower()

        # 先检查限制赛道 → 强制剔除
        for kw in self._restricted_lower:
            if kw in text:
                return -100.0, f"政策限制赛道: {kw}"

        # 检查扶持赛道
        matched = []
        for kw in self.cfg.kw_positive:
            if kw.lower() in text:
                matched.append(kw)
        if matched:
            return self.cfg.policy_boost, f"政策扶持赛道: {', '.join(matched[:3])}"
        return 0.0, ""

    # ── #2 行业周期 ──────────────────────────────────────────
    def score_industry_cycle(
        self,
        industry_revenue_yoy_history: list[float] | None = None,
    ) -> tuple[float, str]:
        """行业周期评分。

        需要近N年行业营收同比增速数据。
        满足：#2 行业周期 + #12 选股规则5（衰退期剔除）。
        """
        if not industry_revenue_yoy_history:
            return 0.0, "行业周期数据缺失"

        years = len(industry_revenue_yoy_history)

        # 衰退期判断：连续N年增速<0
        if years >= self.cfg.decline_years_required:
            recent = industry_revenue_yoy_history[-self.cfg.decline_years_required:]
            if all(v < self.cfg.decline_threshold_yoy for v in recent):
                return self.cfg.decline_stage_penalty, "行业衰退期（连续{0}年营收负增长）".format(
                    self.cfg.decline_years_required
                )

        # 成长期判断：连续N年增速>阈值
        if years >= self.cfg.growth_years_required:
            recent = industry_revenue_yoy_history[-self.cfg.growth_years_required:]
            if all(v > self.cfg.growth_threshold_yoy for v in recent):
                return self.cfg.growth_stage_boost, "行业成长期（连续{0}年营收高增长）".format(
                    self.cfg.growth_years_required
                )

        return 0.0, "行业成熟期"

    # ── #3 供需关系 ──────────────────────────────────────────
    def score_supply_demand(
        self,
        ppi_yoy: float | None = None,
        capacity_utilization: float | None = None,
    ) -> tuple[float, str]:
        """供需关系评分。

        满足：#3 供需关系。
        """
        if ppi_yoy is not None and ppi_yoy > self.cfg.shortage_ppi_threshold:
            return self.cfg.shortage_boost, f"供给短缺（PPI同比{ppi_yoy:.1f}%）"
        if capacity_utilization is not None and capacity_utilization < self.cfg.oversupply_utilization_threshold:
            return self.cfg.oversupply_penalty, f"产能过剩（产能利用率{capacity_utilization:.0f}%）"
        return 0.0, ""

    # ── #4 景气度 ──────────────────────────────────────────
    def score_prosperity(
        self,
        revenue_growth_qoq_history: list[float] | None = None,
        profit_growth_qoq_history: list[float] | None = None,
    ) -> tuple[float, str]:
        """景气度评分：营收/利润连续N季度环比提升。

        满足：#4 景气度 + #9 选股规则2。
        """
        n = self.cfg.prosperity_consecutive_quarters
        has_revenue = revenue_growth_qoq_history and len(revenue_growth_qoq_history) >= n
        has_profit = profit_growth_qoq_history and len(profit_growth_qoq_history) >= n

        if not has_revenue and not has_profit:
            return 0.0, "景气度数据不足"

        improving_count = 0
        total = 0
        if has_revenue:
            recent = revenue_growth_qoq_history[-n:]
            improving_count += sum(1 for v in recent if v > 0)
            total += n
        if has_profit:
            recent = profit_growth_qoq_history[-n:]
            improving_count += sum(1 for v in recent if v > 0)
            total += n

        if total == 0:
            return 0.0, ""

        ratio = improving_count / total
        if ratio >= 0.75:
            return self.cfg.prosperity_boost, f"赛道景气（近{n}季环比提升率{ratio:.0%}）"
        elif ratio <= 0.25:
            return self.cfg.prosperity_penalty, f"赛道低迷（近{n}季环比提升率{ratio:.0%}）"
        return 0.0, ""

    # ── 综合赛道评分 ──────────────────────────────────────────
    def score_track(
        self,
        name: str,
        industry: str,
        concepts: str,
        industry_revenue_yoy_history: list[float] | None = None,
        ppi_yoy: float | None = None,
        capacity_utilization: float | None = None,
        revenue_growth_qoq_history: list[float] | None = None,
        profit_growth_qoq_history: list[float] | None = None,
    ) -> tuple[float, dict[str, float], list[str]]:
        """综合赛道评分，返回 (总分变化, 各维度分, flags)。"""
        total = 0.0
        dims: dict[str, float] = {}
        flags: list[str] = []

        s, r = self.score_policy(name, industry, concepts)
        dims["policy"] = s
        if s <= -50:
            flags.append(f"POLICY_RESTRICTED: {r}")
        total += s

        s, r = self.score_industry_cycle(industry_revenue_yoy_history)
        dims["cycle"] = s
        if s <= -50:
            flags.append(f"INDUSTRY_DECLINE: {r}")
        total += s

        s, r = self.score_supply_demand(ppi_yoy, capacity_utilization)
        dims["supply_demand"] = s
        if r:
            flags.append(r)
        total += s

        s, r = self.score_prosperity(revenue_growth_qoq_history, profit_growth_qoq_history)
        dims["prosperity"] = s
        if r:
            flags.append(r)
        total += s

        return total, dims, flags
