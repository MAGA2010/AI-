# -*- coding: utf-8 -*-
"""Main pipeline — orchestrates L1 → L2 → result.

集成 V2 全维度评分引擎，覆盖全部34条策略规则。
"""

import logging
import re
import uuid
from pathlib import Path

import pandas as pd

from alphasift.config import Config
from alphasift.candidate_context import collect_candidate_context
from alphasift.context import build_llm_context
from alphasift.daily import enrich_daily_features
from alphasift.filter import apply_hard_filters, requires_daily_features, without_daily_filters
from alphasift.industry import enrich_industry_concepts
from alphasift.market_news import collect_market_news
from alphasift.models import Pick, ScreenResult
from alphasift.moat_scorer import MoatScorer, _build_moat_config
from alphasift.post_analysis import normalize_post_analyzers, run_post_analyzers
from alphasift.ranker import rank_candidates_with_metadata
from alphasift.risk import apply_portfolio_overlay, apply_risk_overlay
from alphasift.scorer import compute_screen_scores, factor_score_columns
from alphasift.scorer_v2 import compute_industry_pe_averages, generate_signals, score_snapshot, suggest_position
from alphasift.snapshot import fetch_snapshot_with_fallback
from alphasift.strategy import load_all_strategies, load_strategy_raw
from alphasift.track_analyzer import TrackAnalyzer, _build_track_analyzer_config

logger = logging.getLogger(__name__)


def screen(
    strategy: str,
    *,
    market: str = "cn",
    max_output: int | None = None,
    use_llm: bool = True,
    llm_context: str | None = None,
    llm_context_files: list[str | Path] | None = None,
    candidate_context_files: list[str | Path] | None = None,
    collect_llm_candidate_context: bool | None = None,
    candidate_context_max_candidates: int | None = None,
    candidate_context_providers: list[str] | None = None,
    industry_map_files: list[str | Path] | None = None,
    industry_provider: str | None = None,
    post_analyzers: list[str] | None = None,
    post_analysis_max_picks: int | None = None,
    daily_enrich: bool | None = None,
    daily_enrich_max_candidates: int | None = None,
    deep_analysis: bool = False,
    deep_analysis_max_picks: int | None = None,
    config: Config | None = None,
) -> ScreenResult:
    """Execute stock screening with the given strategy.

    Pipeline:
      1. Load strategy YAML
      2. Fetch snapshot data
      3. L1 hard filter
      4. L1.5 V2 全维度评分（#1-34 全部条件）
      5. L2 LLM ranking (optional)
      6. Risk overlay
      7. Portfolio overlay
      8. Generate buy/sell signals
      9. Output
    """
    if config is None:
        config = Config.from_env()

    if market != "cn":
        raise ValueError("Only market='cn' is currently supported")

    run_id = uuid.uuid4().hex[:12]
    degradation: list[str] = []

    # ── 1. Load strategy ─────────────────────────────────────
    strategies = load_all_strategies(config.strategies_dir)
    if strategy not in strategies:
        available = ", ".join(strategies.keys()) or "(none)"
        raise ValueError(f"Strategy '{strategy}' not found. Available: {available}")

    strat = strategies[strategy]
    screening = strat.screening
    if market not in screening.market_scope:
        raise ValueError(
            f"Strategy '{strategy}' does not support market '{market}'. "
            f"Supported: {', '.join(screening.market_scope)}"
        )
    output_count = max_output or screening.max_output
    analyzer_names = normalize_post_analyzers(
        post_analyzers if post_analyzers is not None else config.post_analyzers
    )
    if deep_analysis and "dsa" not in analyzer_names:
        analyzer_names.append("dsa")
    analyzer_max_picks = post_analysis_max_picks or deep_analysis_max_picks
    daily_needed = requires_daily_features(screening.hard_filters)
    daily_requested = config.daily_enrich_enabled if daily_enrich is None else daily_enrich
    daily_limit = daily_enrich_max_candidates or config.daily_enrich_max_candidates
    snapshot_filters = without_daily_filters(screening.hard_filters) if daily_needed else screening.hard_filters

    # ── 2. Fetch snapshot ────────────────────────────────────
    snapshot_df = fetch_snapshot_with_fallback(
        config.snapshot_source_priority,
        required_columns=_required_snapshot_columns(snapshot_filters),
    )
    effective_industry_map_files = (
        list(industry_map_files)
        if industry_map_files is not None
        else list(config.industry_map_files)
    )
    effective_industry_provider = (
        industry_provider
        if industry_provider is not None
        else config.industry_provider
    )
    effective_industry_provider = str(effective_industry_provider or "none").strip().lower()
    if effective_industry_map_files or effective_industry_provider not in {"", "none", "off", "false"}:
        snapshot_df, industry_notes = enrich_industry_concepts(
            snapshot_df,
            map_files=effective_industry_map_files,
            provider=effective_industry_provider,
            max_boards=config.industry_provider_max_boards,
        )
        degradation.extend(f"Industry/concepts enrichment: {item}" for item in industry_notes)
    snapshot_count = len(snapshot_df)
    snapshot_source = str(snapshot_df.attrs.get("snapshot_source", ""))
    source_errors = [str(item) for item in snapshot_df.attrs.get("source_errors", [])]
    degradation.extend(f"Snapshot source fallback: {item}" for item in source_errors)

    # ── 3. L1 hard filter ────────────────────────────────────
    df = apply_hard_filters(snapshot_df, snapshot_filters)
    after_filter_count = len(df)

    if df.empty:
        return ScreenResult(
            strategy=strategy,
            market=market,
            snapshot_count=snapshot_count,
            after_filter_count=0,
            run_id=run_id,
            degradation=[*degradation, "No candidates after hard filter"],
            snapshot_source=snapshot_source,
            source_errors=source_errors,
            strategy_version=strat.version,
            strategy_category=strat.category,
            post_analyzers=analyzer_names,
            daily_enriched=False,
            risk_enabled=config.risk_enabled,
            portfolio_diversity_enabled=config.portfolio_diversity_enabled,
        )

    # Daily K-line enrichment
    daily_enriched = False
    daily_enrich_count = 0
    if daily_needed or daily_requested:
        provisional = compute_screen_scores(df, screening).sort_values("screen_score", ascending=False)
        enrich_count = min(daily_limit, len(provisional))
        daily_candidates = provisional.head(enrich_count)
        try:
            enriched = enrich_daily_features(
                daily_candidates,
                max_rows=enrich_count,
                lookback_days=config.daily_lookback_days,
                source=config.daily_source,
                fetch_retries=config.daily_fetch_retries,
            )
            daily_enriched = True
            daily_errors = [str(item) for item in enriched.attrs.get("daily_errors", [])]
            daily_enrich_count = int(enriched.attrs.get("daily_success_count", len(enriched)))
            degradation.append(
                f"Daily K-line enrichment attempted {enrich_count} candidates, "
                f"succeeded {daily_enrich_count} of {after_filter_count} snapshot-filtered candidates"
            )
            if daily_errors:
                sample = " | ".join(daily_errors[:5])
                suffix = f" | +{len(daily_errors) - 5} more" if len(daily_errors) > 5 else ""
                degradation.append(f"Daily K-line enrichment row errors: {sample}{suffix}")
            if daily_needed:
                df = apply_hard_filters(enriched, screening.hard_filters)
                after_filter_count = len(df)
            else:
                df = enriched
        except Exception as exc:
            if daily_needed:
                raise RuntimeError(
                    "Daily K-line enrichment is required by this strategy but failed: "
                    f"{exc}"
                ) from exc
            degradation.append(f"Daily K-line enrichment skipped: {exc}")

    if df.empty:
        return ScreenResult(
            strategy=strategy,
            market=market,
            strategy_version=strat.version,
            strategy_category=strat.category,
            snapshot_count=snapshot_count,
            after_filter_count=0,
            run_id=run_id,
            degradation=[*degradation, "No candidates after daily hard filter"],
            snapshot_source=snapshot_source,
            source_errors=source_errors,
            post_analyzers=analyzer_names,
            daily_enriched=daily_enriched,
            daily_enrich_count=daily_enrich_count,
            risk_enabled=config.risk_enabled,
            portfolio_diversity_enabled=config.portfolio_diversity_enabled,
        )

    # ── 4. Compute L1 screen_score ───────────────────────────
    df = compute_screen_scores(df, screening)
    df = df.sort_values("screen_score", ascending=False)

    # ── 4.5 V2 全维度评分（#1-34 全部条件）──────────────────
    # 加载策略原始 dict 用于 V2 评分
    try:
        raw_strategy = load_strategy_raw(strategy, config.strategies_dir)
    except Exception:
        raw_strategy = {}

    # 初始化赛道分析器和壁垒评分器
    track_cfg = _build_track_analyzer_config(raw_strategy)
    track_analyzer = TrackAnalyzer(track_cfg)
    moat_cfg = _build_moat_config(raw_strategy)
    moat_scorer = MoatScorer(moat_cfg)

    # 计算行业平均PE（用于规则#23/#24相对估值判断）
    snap_dicts = [row.to_dict() for _, row in df.iterrows()]
    industry_pe_avg = compute_industry_pe_averages(snap_dicts)

    # 对每条候选执行 V2 评分（委托 TrackAnalyzer/MoatScorer，传入行业PE均值）
    v2_scores = []
    for snap_dict in snap_dicts:
        ind = str(snap_dict.get("industry", "")).strip()
        pe_avg = industry_pe_avg.get(ind)
        v2_score, v2_components, v2_meta = score_snapshot(
            raw_strategy, snap_dict,
            track_analyzer=track_analyzer,
            moat_scorer=moat_scorer,
            industry_pe_avg=pe_avg,
        )
        v2_scores.append({
            "v2_score": v2_score,
            "v2_components": v2_components,
            "v2_meta": v2_meta,
        })

    # 将 V2 评分结果合并回 DataFrame
    df["v2_score"] = [s["v2_score"] for s in v2_scores]
    # 合并 V1 screen_score 和 V2 score（取加权平均）
    df["screen_score"] = df["screen_score"] * 0.3 + df["v2_score"] * 0.7
    df = df.sort_values("screen_score", ascending=False)

    # ── 5. Take Top K for LLM ranking ───────────────────────
    top_k = min(
        max(output_count * config.llm_candidate_multiplier, output_count),
        config.llm_max_candidates,
        len(df),
    )
    df_top = df.head(top_k)

    # ── 6. Build Pick list（含 V2 评分字段）──────────────────
    picks = _df_to_picks(df_top, v2_scores[:top_k], raw_strategy)

    # ── 7. L2 LLM ranking ───────────────────────────────────
    llm_ranked = False
    llm_market_view = ""
    llm_selection_logic = ""
    llm_portfolio_risk = ""
    llm_coverage: float | None = None
    llm_parse_errors: list[str] = []
    if use_llm and config.has_llm_config():
        candidate_context_rows: list[dict[str, object]] = []
        event_source_weights = _event_source_weights(screening.event_profile)
        should_collect_candidate_context = (
            config.llm_candidate_context_enabled
            if collect_llm_candidate_context is None
            else collect_llm_candidate_context
        )
        if should_collect_candidate_context:
            candidate_context_rows, candidate_context_errors = collect_candidate_context(
                df_top,
                max_rows=(
                    candidate_context_max_candidates
                    or config.llm_candidate_context_max_candidates
                ),
                providers=(
                    candidate_context_providers
                    if candidate_context_providers is not None
                    else config.llm_candidate_context_providers
                ),
                news_limit=config.llm_candidate_context_news_limit,
                announcement_limit=config.llm_candidate_context_announcement_limit,
                cache_dir=(
                    config.data_dir / "candidate_context"
                    if config.llm_candidate_context_cache_enabled
                    else None
                ),
                cache_ttl_hours=config.llm_candidate_context_cache_ttl_hours,
                source_weights=event_source_weights,
            )
            degradation.append(
                f"Candidate context collected rows={len(candidate_context_rows)}"
            )
            if candidate_context_errors:
                sample = " | ".join(candidate_context_errors[:5])
                suffix = (
                    f" | +{len(candidate_context_errors) - 5} more"
                    if len(candidate_context_errors) > 5
                    else ""
                )
                degradation.append(f"Candidate context row errors: {sample}{suffix}")
        # Collect market-wide news if enabled
        market_news_text = ""
        if config.market_news_enabled:
            try:
                market_news_text = collect_market_news(
                    providers=config.market_news_providers,
                    max_chars=config.market_news_max_chars,
                    cache_dir=(
                        config.data_dir / "market_news"
                        if config.market_news_cache_enabled
                        else None
                    ),
                    cache_ttl_hours=config.market_news_cache_ttl_hours,
                )
                if market_news_text:
                    degradation.append(
                        f"Market news collected: {len(market_news_text)} chars"
                    )
            except Exception as exc:
                degradation.append(f"Market news fetch skipped: {exc}")
        effective_context = build_llm_context(
            base_context=llm_context if llm_context is not None else config.llm_context,
            context_files=llm_context_files,
            candidate_context_files=candidate_context_files,
            candidate_context_rows=candidate_context_rows,
            market_news_text=market_news_text,
            snapshot_df=snapshot_df,
            candidate_df=df_top,
            event_profile=screening.event_profile,
            max_chars=config.llm_context_max_chars,
        )
        llm_result = rank_candidates_with_metadata(
            picks,
            screening.ranking_hints,
            config.llm_api_key,
            config.llm_model,
            config.llm_base_url,
            context=effective_context,
            rank_weight=config.llm_rank_weight,
            max_retries=config.llm_max_retries,
            min_coverage=config.llm_min_coverage,
            fallback_models=config.llm_fallback_models,
            temperature=config.llm_temperature,
            json_mode=config.llm_json_mode,
            silent=config.llm_silent,
            channels=config.llm_channels,
            config_path=str(config.llm_config_path or ""),
            timeout_sec=config.llm_timeout_sec,
        )
        picks = llm_result.picks
        llm_market_view = llm_result.market_view
        llm_selection_logic = llm_result.selection_logic
        llm_portfolio_risk = llm_result.portfolio_risk
        llm_coverage = llm_result.coverage
        llm_parse_errors = llm_result.errors
        llm_ranked = any(p.llm_score is not None for p in picks)
        if not llm_ranked:
            degradation.append("LLM ranking failed: fell back to screen_score")
            for i, p in enumerate(picks):
                p.rank = i + 1
                p.final_score = p.screen_score
    else:
        if use_llm and not config.has_llm_config():
            degradation.append("LLM ranking skipped: no LLM config")
        for i, p in enumerate(picks):
            p.rank = i + 1
            p.final_score = p.screen_score

    # ── 8. Risk overlay ─────────────────────────────────────
    if config.risk_enabled:
        picks, risk_degradation = apply_risk_overlay(
            picks,
            max_penalty=config.risk_max_penalty,
            veto_high_risk=config.risk_veto_high,
            profile=screening.risk_profile,
        )
        degradation.extend(risk_degradation)

    # ── 9. Portfolio overlay ────────────────────────────────
    portfolio_concentration_notes: list[str] = []
    if config.portfolio_diversity_enabled:
        picks, portfolio_concentration_notes = apply_portfolio_overlay(
            picks,
            max_same_sector=config.portfolio_max_same_llm_sector,
            concentration_penalty=config.portfolio_concentration_penalty,
            profile=screening.portfolio_profile,
        )

    # ── 10. Trim to max_output ──────────────────────────────
    picks = picks[:output_count]

    # ── 11. Optional L3 post-analysis ───────────────────────
    if analyzer_names:
        picks, post_degradation = run_post_analyzers(
            picks,
            analyzer_names=analyzer_names,
            run_id=run_id,
            config=config,
            max_picks=analyzer_max_picks,
            scorecard_profile=screening.scorecard_profile,
        )
        degradation.extend(post_degradation)

    return ScreenResult(
        strategy=strategy,
        market=market,
        strategy_version=strat.version,
        strategy_category=strat.category,
        snapshot_count=snapshot_count,
        after_filter_count=after_filter_count,
        picks=picks,
        run_id=run_id,
        llm_ranked=llm_ranked,
        llm_market_view=llm_market_view,
        llm_selection_logic=llm_selection_logic,
        llm_portfolio_risk=llm_portfolio_risk,
        llm_coverage=llm_coverage,
        llm_parse_errors=llm_parse_errors,
        degradation=degradation,
        snapshot_source=snapshot_source,
        source_errors=source_errors,
        deep_analysis_requested=("dsa" in analyzer_names),
        post_analyzers=analyzer_names,
        daily_enriched=daily_enriched,
        daily_enrich_count=daily_enrich_count,
        risk_enabled=config.risk_enabled,
        portfolio_diversity_enabled=config.portfolio_diversity_enabled,
        portfolio_concentration_notes=portfolio_concentration_notes,
    )


# ═══════════════════════════════════════════════════════════════
#  DataFrame → Pick 转换（含 V2 字段）
# ═══════════════════════════════════════════════════════════════

def _df_to_picks(
    df: pd.DataFrame,
    v2_scores: list[dict] | None = None,
    raw_strategy: dict | None = None,
) -> list[Pick]:
    """Convert DataFrame rows to Pick objects, including V2 scoring fields."""
    picks = []
    factor_cols = factor_score_columns()
    for i, (_, row) in enumerate(df.iterrows()):
        factor_scores = {
            factor: _safe_float(row.get(col)) or 0.0
            for factor, col in factor_cols.items()
            if col in df.columns
        }

        # V2 评分数据
        v2 = v2_scores[i] if v2_scores and i < len(v2_scores) else None
        v2_score = v2["v2_score"] if v2 else 0.0
        v2_components = v2["v2_components"] if v2 else {}
        v2_meta = v2["v2_meta"] if v2 else {}

        # 生成买卖信号
        snap_dict = row.to_dict()
        signals = generate_signals(raw_strategy or {}, snap_dict, v2_score) if raw_strategy else {}

        # 仓位建议
        position_pct = suggest_position(raw_strategy or {}, v2_score) if raw_strategy else 0.0

        picks.append(Pick(
            rank=i + 1,
            code=_normalize_code(row.get("code", row.get("代码", ""))),
            name=str(row.get("name", row.get("名称", row.get("股票名称", "")))),
            screen_score=float(row.get("screen_score", 0)),
            final_score=float(row.get("screen_score", 0)),
            price=float(row.get("price", row.get("最新价", 0)) or 0),
            change_pct=float(row.get("change_pct", row.get("涨跌幅", 0)) or 0),
            amount=float(row.get("amount", row.get("成交额", 0)) or 0),
            total_mv=_safe_float(row.get("total_mv", row.get("总市值"))),
            turnover_rate=_safe_float(row.get("turnover_rate", row.get("换手率"))),
            volume_ratio=_safe_float(row.get("volume_ratio", row.get("量比"))),
            pe_ratio=_safe_float(row.get("pe_ratio", row.get("市盈率"))),
            pb_ratio=_safe_float(row.get("pb_ratio", row.get("市净率"))),
            industry=_safe_text(row.get("industry", row.get("行业", row.get("所属行业", "")))),
            concepts=_safe_text(row.get("concepts", row.get("概念", row.get("概念题材", "")))),
            industry_rank=_safe_int(row.get("industry_rank")),
            industry_change_pct=_safe_float(row.get("industry_change_pct")),
            industry_heat_score=_safe_float(row.get("industry_heat_score")),
            concept_heat_score=_safe_float(row.get("concept_heat_score")),
            board_heat_score=_safe_float(row.get("board_heat_score")),
            board_heat_latest_score=_safe_float(row.get("board_heat_latest_score")),
            board_heat_trend_score=_safe_float(row.get("board_heat_trend_score")),
            board_heat_persistence_score=_safe_float(row.get("board_heat_persistence_score")),
            board_heat_cooling_score=_safe_float(row.get("board_heat_cooling_score")),
            board_heat_observations=_safe_int(row.get("board_heat_observations")),
            board_heat_state=_safe_text(row.get("board_heat_state")),
            board_heat_summary=_safe_text(row.get("board_heat_summary")),
            change_60d=_safe_float(row.get("change_60d")),
            signal_score=_safe_float(row.get("signal_score")),
            ma_bullish=_safe_bool(row.get("ma_bullish")),
            price_above_ma20=_safe_bool(row.get("price_above_ma20")),
            macd_status=str(row.get("macd_status", "") or ""),
            rsi_status=str(row.get("rsi_status", "") or ""),
            breakout_20d_pct=_safe_float(row.get("breakout_20d_pct")),
            range_20d_pct=_safe_float(row.get("range_20d_pct")),
            volume_ratio_20d=_safe_float(row.get("volume_ratio_20d")),
            body_pct=_safe_float(row.get("body_pct")),
            pullback_to_ma20_pct=_safe_float(row.get("pullback_to_ma20_pct")),
            consolidation_days_20d=_safe_int(row.get("consolidation_days_20d")),
            factor_scores=factor_scores,
            # ── V2 新增字段 ──
            track_policy_score=v2_components.get("policy_orientation"),
            track_cycle_score=v2_components.get("industry_cycle"),
            track_supply_demand_score=v2_components.get("supply_demand"),
            track_prosperity_score=v2_components.get("prosperity"),
            moat_score=v2_components.get("competitive_moat"),
            financial_health_score=v2_components.get("financial_health"),
            management_score=v2_components.get("management_quality"),
            buy_condition_fundamental=v2_components.get("buy_fundamental"),
            buy_condition_technical=v2_components.get("buy_technical"),
            buy_condition_capital=v2_components.get("buy_capital"),
            buy_condition_event=v2_components.get("buy_event"),
            buy_condition_valuation=v2_components.get("buy_valuation"),
            buy_condition_sentiment=v2_components.get("buy_sentiment"),
            buy_signal=signals.get("buy_signal", "neutral"),
            sell_signals=signals.get("sell_signals", []),
            suggested_position_pct=position_pct,
            stop_loss_price=signals.get("stop_loss_price"),
            stop_profit_price=signals.get("stop_profit_price"),
        ))
    return picks


# ═══════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════

def _required_snapshot_columns(filters) -> list[str]:
    columns: list[str] = []
    if filters.exclude_st:
        columns.append("name")
    if filters.amount_min is not None:
        columns.append("amount")
    if filters.price_min is not None or filters.price_max is not None:
        columns.append("price")
    if filters.market_cap_min is not None or filters.market_cap_max is not None:
        columns.append("total_mv")
    if filters.pe_ttm_min is not None or filters.pe_ttm_max is not None:
        columns.append("pe_ratio")
    if filters.pb_min is not None or filters.pb_max is not None:
        columns.append("pb_ratio")
    if filters.volume_ratio_min is not None:
        columns.append("volume_ratio")
    if filters.turnover_rate_min is not None:
        columns.append("turnover_rate")
    if filters.change_pct_min is not None or filters.change_pct_max is not None:
        columns.append("change_pct")
    return list(dict.fromkeys(columns))


def _safe_float(v) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    numeric = _safe_float(v)
    if numeric is None:
        return None
    return int(numeric)


def _safe_bool(v) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _event_source_weights(event_profile: dict[str, object]) -> dict[str, float] | None:
    value = (event_profile or {}).get("source_weights")
    if not isinstance(value, dict):
        return None
    result: dict[str, float] = {}
    for key, raw in value.items():
        try:
            result[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return result or None


def _safe_text(v) -> str:
    if v is None:
        return ""
    text = str(v).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text[:120]


def _normalize_code(value: object) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        return text.zfill(6)[-6:]
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match:
        return match.group(1)
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else ""
