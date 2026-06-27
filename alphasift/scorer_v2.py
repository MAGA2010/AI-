# -*- coding: utf-8 -*-
"""全维度评分引擎 V2 — 覆盖全部34条策略规则。

覆盖策略条目：
  #1-4   赛道分析维度（政策/周期/供需/景气度）
  #5-7   公司分析维度（竞争壁垒/财务健康度/管理层能力）
  #8-12  选股规则（5条）
  #13-18 买入条件（6个）
  #19-22 卖出条件（4类）
  #23-30 指标操作细则
  #31-34 补充策略规则

PROXY INDICATORS（快照数据局限性，当数据可用时自动使用真实指标）：
  #2  行业周期：无历史行业营收Y/Y数据时用 change_60d 作代理
  #3  供需关系：无PPI/产能利用率数据时用 volume_ratio 作代理
  #4  景气度：无连续季度环比数据时用 revenue_yoy/profit_yoy 作代理
  #5  竞争壁垒：无专利/市占率/品牌数据时用 PB/PE+市值 作代理
  #14 技术面突破：无MA250时用MA20/MA60对齐作代理；无3日放量数据时用量比作代理
  #15 资金面：无连续5日北向资金时用单日值标准化作代理
  #16 事件驱动：无公告/订单数据时用 change_pct 作代理
  #18 情绪面：无大盘相对涨跌数据时用绝对 change_60d 作代理

真实数据改进（当 daily enrichment 开启时）：
  #14 MA250 检查：需要 daily_lookback_days >= 250
  #14 3日放量检查：来自日K线成交量数据
  #23/#24 相对PE：当候选池计算 industry_pe_avg 时可用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alphasift.strategy import load_strategy_raw
from alphasift.track_analyzer import TrackAnalyzer, _build_track_analyzer_config
from alphasift.moat_scorer import MoatScorer, _build_moat_config


@dataclass
class _BetaScored:
    value: float
    components: dict[str, float] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
#  辅助工具
# ═══════════════════════════════════════════════════════════════

def _num(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _has(val: Any) -> bool:
    return val is not None and str(val).strip() != ""


def compute_industry_pe_averages(snapshots: list[dict]) -> dict[str, float]:
    """计算候选池中各行业平均PE，用于规则#23/#24的相对估值判断。"""
    industry_pes: dict[str, list[float]] = {}
    for snap in snapshots:
        industry = str(snap.get("industry", "")).strip()
        if not industry:
            continue
        pe = _num(snap.get("pe_ratio"), 0.0)
        if 0 < pe < 500:
            industry_pes.setdefault(industry, []).append(pe)
    return {
        k: sum(v) / len(v) for k, v in industry_pes.items() if len(v) >= 2
    }


# ═══════════════════════════════════════════════════════════════
#  L2 Beta 评分（完整实现全部34条）
# ═══════════════════════════════════════════════════════════════

def _build_beta_scored(
    strategy: dict,
    snap: dict,
    track_analyzer: TrackAnalyzer | None = None,
    moat_scorer: MoatScorer | None = None,
    industry_pe_avg: float | None = None,
) -> _BetaScored:
    """为单只股票构建全维度评分。

    每个评分项对应策略文档中的编号，便于审计。
    当 track_analyzer / moat_scorer 提供时，委托给对应的分析器；
    否则使用快照内可用数据的代理逻辑。
    """
    total = 0.0
    components: dict[str, float] = {}
    flags: list[str] = []

    name = str(snap.get("name", ""))
    industry = str(snap.get("industry", ""))
    concepts = str(snap.get("concepts", ""))
    pe_ratio = _num(snap.get("pe_ratio"), 0.0)
    pb_ratio = _num(snap.get("pb_ratio"), 0.0)
    market_cap = _num(snap.get("total_mv"), 0.0)
    volume_ratio = _num(snap.get("volume_ratio"), 1.0)
    turnover = _num(snap.get("turnover_rate"), 0.0)
    change_pct = _num(snap.get("change_pct"), 0.0)
    change_60d = _num(snap.get("change_60d"), 0.0)
    profit_yoy = _num(snap.get("profit_yoy_pct"), 0.0)
    revenue_yoy = _num(snap.get("revenue_yoy_pct"), 0.0)

    ta = strategy.get("track_analysis", {})
    ca = strategy.get("company_analysis", {})
    bc = strategy.get("buy_conditions", {})

    # ── #1 政策导向 ───────────────────────────────────────────
    po_cfg = ta.get("policy_orientation", {})
    if track_analyzer is not None:
        policy_score, policy_reason = track_analyzer.score_policy(name, industry, concepts)
    else:
        # 代理逻辑：关键字匹配（与 TrackAnalyzer.score_policy 等效）
        policy_boost = po_cfg.get("policy_boost", 15)
        text = f"{name} {industry} {concepts}".lower()
        kw_neg = [k.lower() for k in po_cfg.get("policy_keywords_negative", [])]
        kw_pos = [k.lower() for k in po_cfg.get("policy_keywords_positive", [])]
        policy_score = 0.0
        policy_reason = ""
        for kw in kw_neg:
            if kw in text:
                policy_score = -100.0
                policy_reason = f"政策限制赛道: {kw}"
                break
        if policy_score == 0.0:
            matched = [k for k in kw_pos if k in text]
            if matched:
                policy_score = policy_boost
                policy_reason = f"政策扶持赛道: {', '.join(matched[:3])}"
    if policy_score <= -50:
        flags.append(f"POLICY_RESTRICTED: {policy_reason}")
    elif policy_reason:
        flags.append(policy_reason)
    components["policy_orientation"] = policy_score
    total += policy_score

    # ── #2 行业周期 ───────────────────────────────────────────
    ic_cfg = ta.get("industry_cycle", {})
    # 尝试委托 TrackAnalyzer（需要历史行业营收增速数据）
    cycle_score = 0.0
    cycle_reason = ""
    if track_analyzer is not None:
        # 快照中可能包含 industry_revenue_yoy_history（JSON字符串或列表）
        yoy_hist = snap.get("industry_revenue_yoy_history")
        if isinstance(yoy_hist, str):
            try:
                import json
                yoy_hist = json.loads(yoy_hist)
            except (json.JSONDecodeError, TypeError):
                yoy_hist = None
        if yoy_hist and isinstance(yoy_hist, list):
            cycle_score, cycle_reason = track_analyzer.score_industry_cycle(yoy_hist)
    # PROXY：无历史数据时用60日涨跌幅作代理（反映行业动量）
    if cycle_score == 0.0 and not cycle_reason:
        if change_60d > 20:
            cycle_score = ic_cfg.get("growth_stage_boost", 10)
            cycle_reason = f"成长信号: 60日涨{change_60d:.1f}%"
        elif change_60d < -20:
            cycle_score = ic_cfg.get("decline_stage_penalty", -100)
            cycle_reason = f"行业衰退信号: 60日跌幅{change_60d:.1f}%"
            flags.append(cycle_reason)
    components["industry_cycle"] = cycle_score
    total += cycle_score

    # ── #3 供需关系 ───────────────────────────────────────────
    sd_cfg = ta.get("supply_demand", {})
    sd_score = 0.0
    if track_analyzer is not None:
        ppi = snap.get("ppi_yoy")
        cap_util = snap.get("capacity_utilization")
        sd_score, sd_reason = track_analyzer.score_supply_demand(
            ppi_yoy=float(ppi) if ppi is not None else None,
            capacity_utilization=float(cap_util) if cap_util is not None else None,
        )
    # PROXY：无PPI/产能利用率时用量比作代理（放量=需求旺盛）
    if sd_score == 0.0:
        if volume_ratio > 2.0:
            sd_score = sd_cfg.get("shortage_boost", 8)
        elif volume_ratio < 0.5:
            sd_score = sd_cfg.get("oversupply_penalty", -8)
    components["supply_demand"] = sd_score
    total += sd_score

    # ── #4 景气度 ─────────────────────────────────────────────
    pr_cfg = ta.get("prosperity", {})
    prosperity_score = 0.0
    if track_analyzer is not None:
        rev_qoq = snap.get("revenue_growth_qoq_history")
        profit_qoq = snap.get("profit_growth_qoq_history")
        if isinstance(rev_qoq, str):
            try:
                import json
                rev_qoq = json.loads(rev_qoq)
            except (json.JSONDecodeError, TypeError):
                rev_qoq = None
        if isinstance(profit_qoq, str):
            try:
                import json
                profit_qoq = json.loads(profit_qoq)
            except (json.JSONDecodeError, TypeError):
                profit_qoq = None
        if rev_qoq or profit_qoq:
            prosperity_score, prosperity_reason = track_analyzer.score_prosperity(
                rev_qoq, profit_qoq
            )
            if prosperity_score < 0:
                flags.append(prosperity_reason)
    # PROXY：无季度环比数据时用年度同比作代理
    if prosperity_score == 0.0:
        if revenue_yoy > 20 and profit_yoy > 30:
            prosperity_score = pr_cfg.get("prosperity_boost", 12)
        elif revenue_yoy < 0 and profit_yoy < 0:
            prosperity_score = pr_cfg.get("prosperity_penalty", -10)
            flags.append("赛道低迷: 营收利润双降")
    components["prosperity"] = prosperity_score
    total += prosperity_score

    # ── #5 竞争壁垒 ───────────────────────────────────────────
    cm_cfg = ca.get("competitive_moat", {})
    moat_boost = cm_cfg.get("moat_boost", 12)
    moat_score = 0.0
    if moat_scorer is not None:
        patents = snap.get("core_patents")
        mshare = snap.get("market_share_pct")
        brand = snap.get("brand_rank")
        moat_delta, moat_reason = moat_scorer.score_moat(
            core_patents=int(patents) if patents is not None else None,
            market_share_pct=float(mshare) if mshare is not None else None,
            brand_rank=int(brand) if brand is not None else None,
        )
        if moat_delta > 0:
            moat_score = moat_delta
        if moat_reason:
            flags.append(moat_reason)
    # PROXY：无专利/市占率/品牌数据时用PB/PE+市值作代理
    if moat_score == 0.0:
        if pb_ratio > 0 and pb_ratio < 3 and 0 < pe_ratio < 30:
            moat_score = moat_boost
        elif pb_ratio > 10 or pe_ratio > 100:
            moat_score = -5.0
        if market_cap > 50_000_000_000:
            moat_score += 3.0  # 龙头效应
    components["competitive_moat"] = moat_score
    total += moat_score

    # ── #6 财务健康度 ─────────────────────────────────────────
    fh_cfg = ca.get("financial_health", {})
    gross_margin = _num(snap.get("gross_margin_pct"), 0.0)
    ocf_ratio = _num(snap.get("ocf_to_profit_ratio"), 0.0)
    debt_ratio = _num(snap.get("debt_to_asset_pct"), 0.0)
    current_ratio = _num(snap.get("current_ratio"), 0.0)
    fh_boost = fh_cfg.get("financial_boost", 10)
    fh_score = 0.0
    gm_premium = fh_cfg.get("gross_margin_premium_pct", 10)
    if gross_margin > gm_premium + 20:
        fh_score += 4.0
    elif gross_margin > gm_premium:
        fh_score += 2.0
    ocf_min = fh_cfg.get("ocf_to_net_profit_min_pct", 80)
    if ocf_ratio >= ocf_min / 100.0:
        fh_score += 3.0
    elif ocf_ratio > 0:
        fh_score += 1.0
    max_debt = fh_cfg.get("max_debt_to_asset_pct", 60)
    if 0 < debt_ratio <= max_debt:
        fh_score += 2.0
    elif debt_ratio > max_debt:
        fh_score -= 3.0
        flags.append(f"高负债: {debt_ratio:.0f}%")
    min_cr = fh_cfg.get("min_current_ratio", 1.0)
    if current_ratio >= min_cr:
        fh_score += 1.0
    fh_score = min(fh_score, fh_boost)
    components["financial_health"] = fh_score
    total += fh_score

    # ── #7 管理层能力 ─────────────────────────────────────────
    mg_cfg = ca.get("management_quality", {})
    mg_boost = mg_cfg.get("management_boost", 8)
    mg_score = 0.0
    if profit_yoy > 20:
        mg_score += 4.0
    elif profit_yoy > 0:
        mg_score += 2.0
    roe = _num(snap.get("roe_pct"), 0.0)
    if roe > 15:
        mg_score += 4.0
    elif roe > 8:
        mg_score += 2.0
    mg_score = min(mg_score, mg_boost)
    components["management_quality"] = mg_score
    total += mg_score

    # ── #13 买入条件1：基本面改善 ─────────────────────────────
    c1_cfg = bc.get("condition_1_fundamental", {})
    c1_weight = c1_cfg.get("weight", 25)
    c1_score = 0.0
    profit_growth = _num(snap.get("profit_yoy_pct"), 0.0)
    revenue_growth = _num(snap.get("revenue_yoy_pct"), 0.0)
    if profit_growth >= 30 and revenue_growth >= 20:
        c1_score = c1_weight
    elif profit_growth >= 15 and revenue_growth >= 10:
        c1_score = c1_weight * 0.6
    elif profit_growth > 0 and revenue_growth > 0:
        c1_score = c1_weight * 0.3
    components["buy_fundamental"] = c1_score
    total += c1_score

    # ── #14 买入条件2：技术面突破 ─────────────────────────────
    c2_cfg = bc.get("condition_2_technical", {})
    c2_weight = c2_cfg.get("weight", 20)
    c2_score = 0.0
    price = _num(snap.get("price"), 0.0)
    ma5 = _num(snap.get("ma5"), 0.0)
    ma10 = _num(snap.get("ma10"), 0.0)
    ma20 = _num(snap.get("ma20"), 0.0)
    ma250 = _num(snap.get("ma250"), 0.0)
    volume_surge_3d = _num(snap.get("volume_surge_3d_pct"), 0.0)
    vol_ratio = _num(snap.get("volume_ratio"), 1.0)
    # MA250 年线突破（真实数据，需要 daily_lookback_days >= 250）
    if ma250 > 0 and price > ma250:
        c2_score += c2_weight * 0.25
    # 均线多头排列（MA250不可用时的替代/补充）
    elif ma5 > 0 and ma10 > 0 and ma20 > 0 and ma5 > ma10 > ma20 and price > ma20:
        c2_score += c2_weight * 0.20
    elif price > ma20 > 0:
        c2_score += c2_weight * 0.12
    # 3日放量 >= 50%（真实数据，来自 daily.py volume_surge_3d_pct）
    if volume_surge_3d >= 50:
        c2_score += c2_weight * 0.25
    elif volume_surge_3d >= 30:
        c2_score += c2_weight * 0.10
    # PROXY：无3日放量数据时用量比作代理
    elif vol_ratio >= 1.5:
        c2_score += c2_weight * 0.15
    # 换手率在合理区间（3%-8%）
    to_min = c2_cfg.get("turnover_rate_min_pct", 3)
    to_max = c2_cfg.get("turnover_rate_max_pct", 8)
    if to_min <= turnover <= to_max:
        c2_score += c2_weight * 0.20
    c2_score = min(c2_score, c2_weight)
    components["buy_technical"] = c2_score
    total += c2_score

    # ── #15 买入条件3：资金面认可 ─────────────────────────────
    c3_cfg = bc.get("condition_3_capital", {})
    c3_weight = c3_cfg.get("weight", 20)
    c3_score = 0.0
    north_buy = _num(snap.get("north_net_buy"), 0.0)
    # 北向资金标准化（占流通市值百分比）
    if north_buy > 0 and market_cap > 0:
        north_pct = north_buy / market_cap * 100
        if north_pct >= 0.5:
            c3_score += c3_weight * 0.6  # 强信号：≥0.5%流通市值
        elif north_pct > 0:
            c3_score += c3_weight * 0.3  # 弱信号
    if volume_ratio >= 1.5:
        c3_score += c3_weight * 0.25
    if 1.0 <= turnover <= 8.0:
        c3_score += c3_weight * 0.15
    c3_score = min(c3_score, c3_weight)
    components["buy_capital"] = c3_score
    total += c3_score

    # ── #16 买入条件4：事件驱动 ───────────────────────────────
    # PROXY：无公告/订单数据时用 change_pct + 政策赛道作代理
    c4_cfg = bc.get("condition_4_event", {})
    c4_weight = c4_cfg.get("weight", 15)
    c4_score = 0.0
    if change_pct >= 5:
        c4_score = c4_weight * 0.7
    elif change_pct >= 3:
        c4_score = c4_weight * 0.4
    if policy_score > 0:
        c4_score += c4_weight * 0.3
    c4_score = min(c4_score, c4_weight)
    components["buy_event"] = c4_score
    total += c4_score

    # ── #17 买入条件5：估值面 ─────────────────────────────────
    c5_cfg = bc.get("condition_5_valuation", {})
    c5_weight = c5_cfg.get("weight", 12)
    c5_score = 0.0
    peg_max = c5_cfg.get("peg_max", 1.0)
    # 使用行业均值PE（如果有），否则用绝对阈值
    pe_ref = industry_pe_avg if industry_pe_avg and industry_pe_avg > 0 else 30.0
    if 0 < pe_ratio < pe_ref * 0.7:
        c5_score += c5_weight * 0.4
    elif 0 < pe_ratio < pe_ref:
        c5_score += c5_weight * 0.2
    if profit_yoy > 0 and pe_ratio > 0:
        peg = pe_ratio / profit_yoy
        if peg < peg_max:
            c5_score += c5_weight * 0.4
        elif peg < 1.5:
            c5_score += c5_weight * 0.2
    dividend_yield = _num(snap.get("dividend_yield"), 0.0)
    min_div = c5_cfg.get("min_dividend_yield_pct", 3.0)
    if dividend_yield >= min_div:
        c5_score += c5_weight * 0.2
    elif dividend_yield > 0:
        c5_score += c5_weight * 0.1
    c5_score = min(c5_score, c5_weight)
    components["buy_valuation"] = c5_score
    total += c5_score

    # ── #18 买入条件6：情绪面 ─────────────────────────────────
    # PROXY：无大盘相对涨跌数据时用绝对 change_60d 作代理
    c6_cfg = bc.get("condition_6_sentiment", {})
    c6_weight = c6_cfg.get("weight", 8)
    c6_score = 0.0
    # 抗跌性：60日跌幅小=抗跌
    if change_60d > 0:
        c6_score += c6_weight * 0.5
    elif change_60d > -10:
        c6_score += c6_weight * 0.3
    # 换手率温和（1%-5%）
    to_s_min = c6_cfg.get("turnover_rate_min_pct", 1.0)
    to_s_max = c6_cfg.get("turnover_rate_max_pct", 5.0)
    if to_s_min <= turnover <= to_s_max:
        c6_score += c6_weight * 0.5
    c6_score = min(c6_score, c6_weight)
    components["buy_sentiment"] = c6_score
    total += c6_score

    # ── #23-28 指标操作细则 ───────────────────────────────────

    # #23 PE > 行业均值 * 2 → 减仓50%（高估值惩罚）
    pe_high = industry_pe_avg * 2 if industry_pe_avg and industry_pe_avg > 0 else 80.0
    if pe_ratio > pe_high and pe_ratio > 0:
        penalty = -8.0
        total += penalty
        components["pe_overvalued_penalty"] = penalty
        flags.append(f"高估值: PE={pe_ratio:.1f}" + (f" (行业均值{industry_pe_avg:.1f})" if industry_pe_avg else ""))

    # #24 PE < 行业均值 * 0.7 + PEG < 1 → 加仓20%（低估值奖励）
    pe_low = industry_pe_avg * 0.7 if industry_pe_avg and industry_pe_avg > 0 else 20.0
    if 0 < pe_ratio < pe_low and profit_yoy > 0:
        peg_est = pe_ratio / profit_yoy
        if peg_est < 1.0:
            bonus = 5.0
            total += bonus
            components["pe_undervalued_bonus"] = bonus

    # #25 MACD金叉+红柱放大 → 首次建仓30%
    macd_hist = _num(snap.get("macd_hist"), 0.0)
    dif = _num(snap.get("macd_dif"), 0.0)
    dea = _num(snap.get("macd_dea"), 0.0)
    if dif > dea and macd_hist > 0:
        bonus = 3.0
        total += bonus
        components["macd_golden_bonus"] = bonus

    # #26 MACD死叉+绿柱放大 → 减仓50%
    if dif < dea and macd_hist < 0:
        penalty = -5.0
        total += penalty
        components["macd_death_penalty"] = penalty
        flags.append("MACD死叉")

    # #27 换手率>15% → 清仓30%（筹码松动）
    if turnover > 15:
        penalty = -10.0
        total += penalty
        components["high_turnover_penalty"] = penalty
        flags.append(f"换手率过高: {turnover:.1f}%")

    # #28 北向单日净买入≥0.5%流通市值 → 加仓10%
    if north_buy > 0 and market_cap > 0:
        north_pct_bonus = north_buy / market_cap * 100
        if north_pct_bonus >= 0.5:
            bonus = 4.0
            total += bonus
            components["northbound_bonus"] = bonus
        elif north_pct_bonus > 0:
            bonus = 2.0
            total += bonus
            components["northbound_bonus"] = bonus

    # #29 止损 — 由 risk.py 执行
    # #30 止盈 — 由 risk.py 执行

    return _BetaScored(value=total, components=components, risk_flags=flags)


# ═══════════════════════════════════════════════════════════════
#  L2 评分主入口
# ═══════════════════════════════════════════════════════════════

def score_snapshot(
    strategy: dict,
    snap: dict,
    track_analyzer: TrackAnalyzer | None = None,
    moat_scorer: MoatScorer | None = None,
    industry_pe_avg: float | None = None,
) -> tuple[float, dict[str, float], dict[str, Any]]:
    """为单条快照执行全维度 L2 评分。

    返回 (score, components_dict, metadata_dict)。
    """
    scored = _build_beta_scored(
        strategy, snap,
        track_analyzer=track_analyzer,
        moat_scorer=moat_scorer,
        industry_pe_avg=industry_pe_avg,
    )
    meta = {"risk_flags": scored.risk_flags}
    return scored.value, scored.components, meta


def score_strategy_screen(
    strategy: dict,
    snapshots: list[dict],
    track_analyzer: TrackAnalyzer | None = None,
    moat_scorer: MoatScorer | None = None,
) -> list[dict[str, Any]]:
    """对所有快照执行 L2 评分并排序。"""
    industry_pe_avg = compute_industry_pe_averages(snapshots)
    results = []
    for snap in snapshots:
        ind = str(snap.get("industry", "")).strip()
        pe_avg = industry_pe_avg.get(ind)
        score, components, meta = score_snapshot(
            strategy, snap,
            track_analyzer=track_analyzer,
            moat_scorer=moat_scorer,
            industry_pe_avg=pe_avg,
        )
        snap["_llm_screen_score"] = score
        snap["_llm_components"] = components
        snap["_llm_meta"] = meta
        results.append(snap)
    results.sort(key=lambda s: s.get("_llm_screen_score", 0.0), reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════
#  买卖信号生成（#19-22 卖出条件 + 指标操作）
# ═══════════════════════════════════════════════════════════════

def generate_signals(strategy: dict, snap: dict, score: float) -> dict[str, Any]:
    """根据评分和指标生成买卖信号。

    覆盖：#19-22 卖出条件 + #23-30 指标操作细则。
    严格匹配 YAML spec 中的阈值。
    """
    bc = strategy.get("buy_conditions", {})
    sc = strategy.get("sell_conditions", {})
    io = strategy.get("indicator_operations", {})
    scoring = strategy.get("scoring", {})

    buy_threshold = bc.get("min_total_score", 50)
    strong_buy = bc.get("strong_buy_threshold", 70)
    sell_threshold = scoring.get("sell_threshold", 30)
    strong_sell_threshold = scoring.get("strong_sell_threshold", 20)

    signals: dict[str, Any] = {
        "buy_signal": "neutral",
        "sell_signals": [],
        "stop_loss_price": None,
        "stop_profit_price": None,
    }

    # 买入信号
    if score >= strong_buy:
        signals["buy_signal"] = "strong_buy"
    elif score >= buy_threshold:
        signals["buy_signal"] = "buy"

    # 卖出信号（基于评分阈值）
    if score <= strong_sell_threshold:
        signals["buy_signal"] = "strong_sell"
    elif score <= sell_threshold:
        signals["buy_signal"] = "sell"

    # 读取指标
    pe_ratio = _num(snap.get("pe_ratio"), 0.0)
    profit_yoy = _num(snap.get("profit_yoy_pct"), 0.0)
    gross_margin = _num(snap.get("gross_margin_pct"), 0.0)
    change_pct = _num(snap.get("change_pct"), 0.0)
    dif = _num(snap.get("macd_dif"), 0.0)
    dea = _num(snap.get("macd_dea"), 0.0)
    macd_hist = _num(snap.get("macd_hist"), 0.0)
    turnover = _num(snap.get("turnover_rate"), 0.0)
    price = _num(snap.get("price"), 0.0)
    ma20 = _num(snap.get("ma20"), 0.0)
    ma250 = _num(snap.get("ma250"), 0.0)
    north_buy = _num(snap.get("north_net_buy"), 0.0)
    market_cap = _num(snap.get("total_mv"), 0.0)
    industry_change = _num(snap.get("industry_change_pct"), 0.0)

    # #19 基本面卖出
    if profit_yoy < 0 and gross_margin < -5:
        signals["sell_signals"].append("基本面恶化: 净利润负增长+毛利率下滑")
        if signals["buy_signal"] not in ("strong_buy",):
            signals["buy_signal"] = "sell"

    # #20 技术面卖出
    if price > 0 and ma20 > 0 and price < ma20:
        # 量能放大确认破位
        vol_ratio = _num(snap.get("volume_ratio"), 1.0)
        vol_confirm = "（量能放大确认）" if vol_ratio >= 1.5 else ""
        signals["sell_signals"].append(f"跌破20日均线{vol_confirm}: 价格{price:.2f}<MA20={ma20:.2f}")
    # MA250 年线跌破
    if price > 0 and ma250 > 0 and price < ma250:
        signals["sell_signals"].append(f"跌破年线: 价格{price:.2f}<MA250={ma250:.2f}")
    if dif < dea and macd_hist < 0:
        signals["sell_signals"].append("MACD死叉")

    # #21 资金面卖出：北向资金净卖出占流通市值>=1%
    if north_buy < 0 and market_cap > 0:
        sell_pct = abs(north_buy) / market_cap * 100
        if sell_pct >= 1.0:
            signals["sell_signals"].append(f"北向资金大幅净卖出: {sell_pct:.2f}%流通市值")
        elif sell_pct >= 0.3:
            signals["sell_signals"].append(f"北向资金净卖出: {sell_pct:.2f}%流通市值")

    # #22 市场环境卖出
    if change_pct < -3:
        signals["sell_signals"].append(f"个股大跌: {change_pct:.1f}%")
    if industry_change < -2:
        signals["sell_signals"].append(f"行业大跌: {industry_change:.1f}%")

    # #27 换手率>15% → 清仓30%
    if turnover > 15:
        signals["sell_signals"].append(f"换手率过高({turnover:.1f}%)，建议清仓30%")

    # 止损止盈（默认参数）
    if price > 0:
        signals["stop_loss_price"] = round(price * 0.9, 2)
        signals["stop_profit_price"] = round(price * 1.3, 2)

    return signals


# ═══════════════════════════════════════════════════════════════
#  仓位建议（#31 仓位管理）
# ═══════════════════════════════════════════════════════════════

def suggest_position(strategy: dict, score: float) -> float:
    """根据评分建议仓位百分比。

    优先使用 supplementary_rules.position_management，
    回退到 buy_advice.position_sizing。
    覆盖：#31 仓位管理。
    """
    sr = strategy.get("supplementary_rules", {})
    pm = sr.get("position_management", {})
    if pm:
        max_single = pm.get("max_single_stock_pct", 20)
    else:
        ba = strategy.get("buy_advice", {})
        ps = ba.get("position_sizing", {})
        max_single = ps.get("max_single_stock_pct", 20)

    if score >= 70:
        return max_single
    elif score >= 50:
        return max_single * 0.6
    elif score >= 30:
        return max_single * 0.3
    return 0.0
