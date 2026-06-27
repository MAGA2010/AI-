# -*- coding: utf-8 -*-
"""CLI entry point."""

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from alphasift.audit import audit_project
from alphasift.config import Config
from alphasift.evaluate import evaluate_saved_run, evaluate_saved_runs
from alphasift.industry import fetch_akshare_board_map, save_industry_map
from alphasift.pipeline import screen
from alphasift.store import (
    evaluation_result_to_jsonl,
    list_saved_runs,
    save_evaluation_result,
    save_screen_result,
    screen_result_to_jsonl,
)
from alphasift.strategy import list_strategies


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="alphasift", description="自动选股 Skill")
    parser.add_argument(
        "--env-file",
        action="append",
        default=None,
        help="加载额外 .env 文件，可重复；用于复用 daily_stock_analysis/daily_ai_assistant 配置",
    )
    sub = parser.add_subparsers(dest="command")

    # screen
    sp = sub.add_parser("screen", help="执行选股")
    sp.add_argument("strategy", help="策略名称")
    sp.add_argument("--market", default="cn")
    sp.add_argument("--max-output", type=int, default=None)
    sp.add_argument("--no-llm", action="store_true", help="不使用 LLM 排序")
    sp.add_argument(
        "--context",
        default=None,
        help="传给 LLM 的市场/新闻/主题上下文，不参与硬筛，只用于候选相对排序",
    )
    sp.add_argument(
        "--context-file",
        action="append",
        default=None,
        help="追加传给 LLM 的上下文文本文件，可重复",
    )
    sp.add_argument(
        "--candidate-context-file",
        action="append",
        default=None,
        help="追加候选级上下文 CSV/JSON/JSONL，需包含 code，可含 news/announcement/fund_flow/text 等列",
    )
    sp.add_argument(
        "--collect-candidate-context",
        action="store_true",
        help="对送入 LLM 的 Top K 候选抓取新闻/公告/资金流线索，默认关闭",
    )
    sp.add_argument(
        "--candidate-context-max-candidates",
        type=int,
        default=None,
        help="最多对前 N 个 LLM 候选抓取外部线索",
    )
    sp.add_argument(
        "--candidate-context-provider",
        action="append",
        default=None,
        help="候选级抓取来源：news、announcement、fund_flow；可重复或逗号分隔",
    )
    sp.add_argument(
        "--industry-map-file",
        action="append",
        default=None,
        help="追加 code->industry/concepts 映射 CSV/JSON/JSONL，可重复",
    )
    sp.add_argument(
        "--industry-provider",
        default=None,
        help="可选行业/概念映射 provider，例如 akshare；默认读取 INDUSTRY_PROVIDER",
    )
    sp.add_argument(
        "--post-analyzer",
        action="append",
        default=None,
        help="追加 L3 后置分析器：scorecard、dsa、external_http；可重复或逗号分隔",
    )
    sp.add_argument(
        "--no-post-analysis",
        action="store_true",
        help="关闭默认 L3 后置评分器和其他后置分析器",
    )
    sp.add_argument(
        "--post-analysis-max-picks",
        type=int,
        default=None,
        help="最多对前 N 只候选运行 L3 后置分析器",
    )
    sp.add_argument("--deep-analysis", action="store_true", help="兼容参数：等同启用 --post-analyzer dsa")
    sp.add_argument(
        "--deep-analysis-max-picks",
        type=int,
        default=None,
        help="最多对前 N 只候选调用 DSA（默认使用环境变量或 3）",
    )
    sp.add_argument(
        "--daily-enrich",
        dest="daily_enrich",
        action="store_true",
        default=None,
        help="对 L1 后的 Top N 候选补充日 K 特征",
    )
    sp.add_argument(
        "--no-daily-enrich",
        dest="daily_enrich",
        action="store_false",
        help="即使环境变量开启也不做可选日 K 增强；策略必需的日 K 过滤仍会执行",
    )
    sp.add_argument(
        "--daily-enrich-max-candidates",
        type=int,
        default=None,
        help="日 K 增强最多处理的候选数",
    )
    sp.add_argument("--save-run", action="store_true", help="保存本次运行到 ALPHASIFT_DATA_DIR/runs")
    sp.add_argument("--output", default=None, help="额外写出结果到指定路径")
    sp.add_argument("--jsonl", action="store_true", help="以 JSONL 输出")
    sp.add_argument("--explain", action="store_true", help="输出紧凑可读摘要")

    # strategies
    sub.add_parser("strategies", help="列出可用策略")

    # evaluate
    ep = sub.add_parser("evaluate", help="用最新快照评估已保存的选股结果")
    ep.add_argument("run", help="run_id 或保存的 run JSON 文件路径")
    ep.add_argument("--save", action="store_true", help="保存评估结果到 ALPHASIFT_DATA_DIR/evaluations")
    ep.add_argument("--output", default=None, help="额外写出评估结果到指定路径")
    ep.add_argument("--jsonl", action="store_true", help="以 JSONL 输出")
    ep.add_argument("--explain", action="store_true", help="输出紧凑可读摘要")
    ep.add_argument("--cost-bps", type=float, default=None, help="评估收益扣除的往返成本，单位 bps")
    ep.add_argument("--follow-through-pct", type=float, default=None, help="突破延续判定的最低收益百分比")
    ep.add_argument("--failed-breakout-pct", type=float, default=None, help="突破失败判定的最高收益百分比")
    ep.add_argument("--with-price-path", action="store_true", help="额外抓取日 K 路径，计算最大回撤和最大浮盈")
    ep.add_argument("--price-path-lookback-days", type=int, default=None, help="价格路径日 K 回看天数")

    # evaluate-batch
    ebp = sub.add_parser("evaluate-batch", help="批量评估最近保存的选股结果并按策略聚合")
    ebp.add_argument("--limit", type=int, default=20, help="最多评估最近 N 个 run")
    ebp.add_argument("--strategy", default=None, help="只评估指定策略")
    ebp.add_argument("--output", default=None, help="额外写出批量评估 JSON 到指定路径")
    ebp.add_argument("--json", action="store_true", help="以 JSON 输出")
    ebp.add_argument("--explain", action="store_true", help="输出紧凑可读摘要")
    ebp.add_argument("--cost-bps", type=float, default=None, help="评估收益扣除的往返成本，单位 bps")
    ebp.add_argument("--follow-through-pct", type=float, default=None, help="突破延续判定的最低收益百分比")
    ebp.add_argument("--failed-breakout-pct", type=float, default=None, help="突破失败判定的最高收益百分比")
    ebp.add_argument("--with-price-path", action="store_true", help="额外抓取日 K 路径，计算最大回撤和最大浮盈")
    ebp.add_argument("--price-path-lookback-days", type=int, default=None, help="价格路径日 K 回看天数")

    # runs
    rp = sub.add_parser("runs", help="列出已保存的运行")
    rp.add_argument("--limit", type=int, default=20)

    # industry-cache
    icp = sub.add_parser("industry-cache", help="刷新行业/概念映射缓存文件")
    icp.add_argument("--provider", default="akshare", choices=["akshare"], help="行业/概念 provider")
    icp.add_argument("--max-boards", type=int, default=80, help="最多抓取行业和概念板块数")
    icp.add_argument("--output", default="data/industry_map.csv", help="输出 CSV/JSON 路径")
    icp.add_argument("--explain", action="store_true", help="输出紧凑摘要")

    # audit
    ap = sub.add_parser("audit", help="评估项目能力、策略配置覆盖和已知短板")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")

    # quickstart
    qp = sub.add_parser(
        "quickstart",
        help="一键演示：列出策略 -> 跑一个无 LLM 的 dual_low -> 输出排名摘要",
    )
    qp.add_argument("--strategy", default="dual_low", help="演示用策略，默认 dual_low")
    qp.add_argument("--max-output", type=int, default=5, help="演示输出候选数")

    # diagnose
    sub.add_parser("diagnose", help="诊断数据获取问题，检查依赖包和网络连接")

    # research
    rp2 = sub.add_parser("research", help="用网络搜索研究单只股票")
    rp2.add_argument("code", help="股票代码，如 000001")
    rp2.add_argument("--name", default="", help="股票名称")
    rp2.add_argument("--custom-query", action="append", default=None, help="自定义搜索查询，可重复")

    # smart-screen
    ssp = sub.add_parser("smart-screen", help="AI 智能选股：网络搜索 + 策略筛选 + LLM 分析")
    ssp.add_argument("--strategy", default="balanced_alpha", help="基础策略名称")
    ssp.add_argument("--requirements", "-r", default="", help="个人选股要求（自然语言描述）")
    ssp.add_argument("--max-candidates", type=int, default=20, help="最大候选数")
    ssp.add_argument("--max-research", type=int, default=10, help="最多网络研究几只股票")
    ssp.add_argument("--no-news", action="store_true", help="不搜索新闻")
    ssp.add_argument("--no-financials", action="store_true", help="不搜索财务数据")
    ssp.add_argument("--custom-query", action="append", default=None, help="自定义搜索查询")
    ssp.add_argument("--explain", action="store_true", help="输出可读摘要")

    args = parser.parse_args()
    _apply_env_file_args(args.env_file)

    if args.command == "screen":
        config = Config.from_env()
        if args.no_post_analysis and (args.post_analyzer or args.deep_analysis):
            parser.error("--no-post-analysis cannot be combined with --post-analyzer or --deep-analysis")
        post_analyzers = []
        if not args.no_post_analysis:
            post_analyzers = list(config.post_analyzers)
            if args.post_analyzer:
                post_analyzers.extend(args.post_analyzer)
        result = screen(
            args.strategy,
            market=args.market,
            max_output=args.max_output,
            use_llm=not args.no_llm,
            llm_context=args.context,
            llm_context_files=args.context_file,
            candidate_context_files=args.candidate_context_file,
            collect_llm_candidate_context=args.collect_candidate_context or None,
            candidate_context_max_candidates=args.candidate_context_max_candidates,
            candidate_context_providers=_split_csv_args(args.candidate_context_provider),
            industry_map_files=args.industry_map_file,
            industry_provider=args.industry_provider,
            post_analyzers=post_analyzers,
            post_analysis_max_picks=args.post_analysis_max_picks,
            daily_enrich=args.daily_enrich,
            daily_enrich_max_candidates=args.daily_enrich_max_candidates,
            deep_analysis=args.deep_analysis,
            deep_analysis_max_picks=args.deep_analysis_max_picks,
            config=config,
        )
        if args.save_run:
            save_screen_result(result, data_dir=config.data_dir)
        if args.output:
            save_screen_result(result, data_dir=config.data_dir, path=args.output, jsonl=args.jsonl)
        if args.explain:
            print(_format_screen_explain(result))
        elif args.jsonl:
            print("\n".join(screen_result_to_jsonl(result)))
        else:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))

    elif args.command == "strategies":
        for s in list_strategies():
            tags = ",".join(s.tags)
            suffix = f" tags={tags}" if tags else ""
            print(
                f"  {s.name:<25} {s.display_name:<10} "
                f"v{s.version:<5} [{s.category}] {s.description}{suffix}"
            )

    elif args.command == "evaluate":
        config = Config.from_env()
        result = evaluate_saved_run(
            args.run,
            config=config,
            cost_bps=args.cost_bps,
            follow_through_pct=args.follow_through_pct,
            failed_breakout_pct=args.failed_breakout_pct,
            with_price_path=args.with_price_path or None,
            price_path_lookback_days=args.price_path_lookback_days,
        )
        if args.save:
            save_evaluation_result(result, data_dir=config.data_dir)
        if args.output:
            save_evaluation_result(result, data_dir=config.data_dir, path=args.output, jsonl=args.jsonl)
        if args.explain:
            print(_format_evaluation_explain(result))
        elif args.jsonl:
            print("\n".join(evaluation_result_to_jsonl(result)))
        else:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))

    elif args.command == "evaluate-batch":
        config = Config.from_env()
        result = evaluate_saved_runs(
            config=config,
            limit=args.limit,
            strategy=args.strategy,
            cost_bps=args.cost_bps,
            follow_through_pct=args.follow_through_pct,
            failed_breakout_pct=args.failed_breakout_pct,
            with_price_path=args.with_price_path or None,
            price_path_lookback_days=args.price_path_lookback_days,
        )
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if args.explain:
            print(_format_evaluation_batch_explain(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == "runs":
        config = Config.from_env()
        for item in list_saved_runs(data_dir=config.data_dir, limit=args.limit):
            print(
                f"{item['run_id']:<14} {item['strategy']:<20} "
                f"{item['created_at']:<26} picks={item['picks']} {item['path']}"
            )

    elif args.command == "industry-cache":
        mapping, notes = fetch_akshare_board_map(max_boards=args.max_boards)
        output_path = save_industry_map(mapping, args.output)
        generated_at = datetime.now().isoformat()
        history_path = _append_industry_cache_history(
            output_path,
            mapping=mapping,
            generated_at=generated_at,
        )
        metadata_path = _write_industry_cache_metadata(
            output_path,
            provider=args.provider,
            max_boards=args.max_boards,
            rows=len(mapping),
            notes=notes,
            generated_at=generated_at,
            history_path=history_path,
        )
        if args.explain:
            print(
                f"industry_cache={output_path} metadata={metadata_path} "
                f"history={history_path} rows={len(mapping)} "
                f"notes={' | '.join(notes)}"
            )
        else:
            print(json.dumps({
                "path": str(output_path),
                "metadata_path": str(metadata_path),
                "history_path": str(history_path),
                "rows": len(mapping),
                "notes": notes,
            }, ensure_ascii=False, indent=2))

    elif args.command == "audit":
        config = Config.from_env()
        result = audit_project(config.strategies_dir)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(_format_audit_explain(result))

    elif args.command == "quickstart":
        _run_quickstart(strategy=args.strategy, max_output=args.max_output)

    elif args.command == "diagnose":
        _run_diagnose()

    elif args.command == "research":
        _run_research(args.code, name=args.name, custom_queries=args.custom_query)

    elif args.command == "smart-screen":
        _run_smart_screen(args)

    else:
        parser.print_help()
        sys.exit(1)


# ---------------------------------------------------------------------------
# New commands: diagnose, research, smart-screen
# ---------------------------------------------------------------------------


def _run_diagnose() -> None:
    """诊断数据获取问题。"""
    from alphasift.data_manager import DataSourceManager

    print("=" * 50)
    print("AlphaSift 数据源诊断")
    print("=" * 50)

    manager = DataSourceManager()
    report = manager.diagnose()
    print(report)


def _run_research(code: str, *, name: str = "", custom_queries: list[str] | None = None) -> None:
    """用网络搜索研究单只股票。"""
    from alphasift.web_research import WebResearcher

    print("=" * 50)
    print(f"网络研究: {code} {name}")
    print("=" * 50)

    researcher = WebResearcher()
    research = researcher.research_stock(
        code, name,
        include_basic=True,
        include_news=True,
        include_financials=True,
        include_analyst=True,
        custom_queries=custom_queries,
    )

    context = research.to_context_text(max_chars=3000)
    if context:
        print(context)
    else:
        print("未找到相关信息。")
        print("提示: 检查网络连接，或设置 BING_SEARCH_API_KEY / SERPER_API_KEY 获取更好的搜索结果。")


def _run_smart_screen(args) -> None:
    """AI 智能选股：网络搜索 + 策略筛选 + LLM 分析。"""
    from alphasift.smart_researcher import SmartResearcher, SmartScreenRequest

    print("=" * 50)
    print("AlphaSift AI 智能选股")
    print("=" * 50)

    if args.requirements:
        print(f"个人要求: {args.requirements}")
    print(f"基础策略: {args.strategy}")
    print()

    request = SmartScreenRequest(
        strategy_name=args.strategy,
        personal_requirements=args.requirements,
        max_candidates=args.max_candidates,
        max_research_stocks=args.max_research,
        include_news=not args.no_news,
        include_financials=not args.no_financials,
        custom_queries=args.custom_query or [],
    )

    researcher = SmartResearcher()
    result = researcher.smart_screen(request)

    if result.error:
        print(f"错误: {result.error}")
        print("运行 'alphasift diagnose' 检查数据源状态")
        sys.exit(1)

    # 输出结果
    print(f"行情快照: {result.snapshot_count} 只股票")
    print(f"策略筛选: {result.filtered_count} 只候选")
    print(f"网络研究: {result.researched_count} 只")
    print()

    if result.market_sentiment:
        print("市场情绪:")
        print(result.market_sentiment)
        print()

    if result.picks:
        print("选股结果:")
        print(f"{'排名':<5}{'代码':<10}{'名称':<14}{'价格':<10}{'涨跌%':<10}{'PE':<10}{'PB':<8}{'成交额(万)':<14}")
        print("-" * 80)
        for pick in result.picks:
            price = f"{pick['price']:.2f}" if pick['price'] else "-"
            change = f"{pick['change_pct']:.2f}" if pick['change_pct'] else "-"
            pe = f"{pick['pe_ratio']:.1f}" if pick['pe_ratio'] else "-"
            pb = f"{pick['pb_ratio']:.2f}" if pick['pb_ratio'] else "-"
            amount = f"{pick['amount']/10000:.0f}" if pick['amount'] else "-"
            print(
                f"{pick['rank']:<5}{pick['code']:<10}{pick['name'][:12]:<14}"
                f"{price:<10}{change:<10}{pe:<10}{pb:<8}{amount:<14}"
            )
        print()

    if result.research_context and args.explain:
        print("研究摘要:")
        print(result.research_context[:2000])
    elif not result.picks:
        print("未找到符合条件的股票。")
        print("建议: 调整策略或放宽筛选条件。")


# ---------------------------------------------------------------------------
# Existing helper functions
# ---------------------------------------------------------------------------


def _run_quickstart(*, strategy: str = "dual_low", max_output: int = 5) -> None:
    """One-shot showcase: list strategies, screen without LLM, print top picks.

    Mirrors the AlphaEvo `showcase` UX: no API key required, prints a
    deterministic-looking summary that fits a single screen.
    """
    print("=" * 60)
    print("AlphaSift Quickstart  -  无 API key 演示")
    print("=" * 60)
    print()

    config = Config.from_env()
    strategies = list_strategies(config.strategies_dir)
    print(f"[1/3] 可用策略 ({len(strategies)}):")
    for s in strategies:
        marker = "->" if s.name == strategy else " "
        print(f"   {marker} {s.name:<20s} {s.display_name}")
    print()

    print(f"[2/3] 执行 `{strategy}` 选股 (--no-llm, --no-post-analysis, top {max_output}) ...")
    try:
        result = screen(
            strategy,
            market="cn",
            max_output=max_output,
            use_llm=False,
            post_analyzers=[],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"   失败: {exc}")
        print("   提示: 检查网络，或设置 SNAPSHOT_SOURCE_PRIORITY / TUSHARE_TOKEN")
        sys.exit(2)

    print(
        f"   全市场 {result.snapshot_count} 只 -> 硬筛后 {result.after_filter_count} 只 "
        f"-> 输出 {len(result.picks)} 只 (源: {result.snapshot_source})"
    )
    print()

    print("[3/3] 候选排名:")
    print(f"   {'rank':<5}{'code':<10}{'name':<14}{'score':<8}{'price':<8}{'pe':<8}{'pb':<6}")
    for pick in result.picks:
        pe = f"{pick.pe_ratio:.1f}" if pick.pe_ratio is not None else "-"
        pb = f"{pick.pb_ratio:.2f}" if pick.pb_ratio is not None else "-"
        print(
            f"   {pick.rank:<5}{pick.code:<10}{pick.name[:12]:<14}"
            f"{pick.final_score:<8.1f}{pick.price:<8.2f}{pe:<8}{pb:<6}"
        )
    print()
    print("下一步:")
    print("   alphasift screen <strategy> --explain     # 查看入选理由和因子分")
    print("   alphasift screen <strategy> --save-run    # 保存运行")
    print("   alphasift evaluate <run_id> --explain     # T+N 评估")
    print("   alphasift strategies                      # 完整策略列表")


def _format_screen_explain(result) -> str:
    lines = [
        f"run_id={result.run_id} strategy={result.strategy} market={result.market}",
        (
            f"snapshot={result.snapshot_count} after_filter={result.after_filter_count} "
            f"source={result.snapshot_source or '-'} llm_ranked={result.llm_ranked}"
        ),
    ]
    if result.post_analyzers:
        lines.append(f"post_analyzers={','.join(result.post_analyzers)}")
    if result.llm_market_view:
        lines.append(f"llm_market_view={result.llm_market_view}")
    if result.llm_selection_logic:
        lines.append(f"llm_selection_logic={result.llm_selection_logic}")
    if result.llm_portfolio_risk:
        lines.append(f"llm_portfolio_risk={result.llm_portfolio_risk}")
    if result.portfolio_concentration_notes:
        lines.append("portfolio_concentration=" + " | ".join(result.portfolio_concentration_notes))
    if result.saved_path:
        lines.append(f"saved_path={result.saved_path}")
    if result.degradation:
        lines.append("degradation=" + " | ".join(result.degradation))
    lines.append("rank code name final screen risk sector penalty reason")
    for pick in result.picks:
        reason = (
            pick.llm_thesis
            or pick.ranking_reason
            or pick.post_analysis_summaries.get("scorecard", "")
        )
        lines.append(
            f"{pick.rank:<4} {pick.code:<8} {pick.name:<10} "
            f"{pick.final_score:>6.1f} {pick.screen_score:>6.1f} "
            f"{pick.risk_level or '-':<6} {pick.llm_sector or '-':<8} "
            f"{pick.portfolio_penalty:>4.1f} {reason[:48]}"
        )
    return "\n".join(lines)


def _format_evaluation_explain(result) -> str:
    lines = [
        f"run_id={result.run_id} strategy={result.strategy} elapsed_days={result.elapsed_days}",
        (
            f"avg_return={result.average_return_pct} "
            f"median_return={result.median_return_pct} win_rate={result.win_rate}"
        ),
    ]
    if result.saved_path:
        lines.append(f"saved_path={result.saved_path}")
    if result.degradation:
        lines.append("degradation=" + " | ".join(result.degradation))
    lines.append("rank code name entry current return_pct status shape max_dd max_runup")
    for pick in result.picks:
        current = "-" if pick.current_price is None else f"{pick.current_price:.2f}"
        ret = "-" if pick.return_pct is None else f"{pick.return_pct:.2f}%"
        lines.append(
            f"{pick.rank:<4} {pick.code:<8} {pick.name:<10} "
            f"{pick.entry_price:<8.2f} {current:<8} {ret:<9} {pick.status:<10} "
            f"{pick.shape_status or '-':<24} "
            f"{_fmt_pct(pick.max_drawdown_pct):<8} {_fmt_pct(pick.max_runup_pct)}"
        )
    return "\n".join(lines)


def _format_evaluation_batch_explain(result: dict) -> str:
    summary = result.get("summary", {})
    lines = [
        (
            f"evaluated_at={result.get('evaluated_at')} "
            f"source={result.get('snapshot_source') or '-'} "
            f"runs={summary.get('run_count')} picks={summary.get('pick_count')} "
            f"cost_bps={result.get('cost_bps')} "
            f"follow_through={result.get('follow_through_pct')} "
            f"failed_breakout={result.get('failed_breakout_pct')} "
            f"price_path={result.get('with_price_path')}"
        ),
        (
            f"avg_return={summary.get('average_return_pct')} "
            f"median_return={summary.get('median_return_pct')} "
            f"win_rate={summary.get('win_rate')} "
            f"missing={summary.get('missing_count')} "
            f"path_picks={summary.get('path_pick_count')} "
            f"avg_max_dd={summary.get('average_max_drawdown_pct')} "
            f"avg_max_runup={summary.get('average_max_runup_pct')}"
        ),
    ]
    if result.get("source_errors"):
        lines.append("source_errors=" + " | ".join(result["source_errors"]))
    if result.get("by_strategy"):
        lines.append("strategy run_count pick_count avg_return median_return win_rate missing")
        for strategy, item in sorted(result["by_strategy"].items()):
            lines.append(
                f"{strategy:<20} {item.get('run_count'):<9} {item.get('pick_count'):<10} "
                f"{item.get('average_return_pct')!s:<10} {item.get('median_return_pct')!s:<13} "
                f"{item.get('win_rate')!s:<8} {item.get('missing_count')}"
            )
    dimensions = result.get("dimensions", {})
    for title, key in (
        ("top_sectors", "by_sector"),
        ("top_themes", "by_theme"),
        ("top_risk_flags", "by_risk_flag"),
        ("shape_status", "by_shape_status"),
        ("shape_tags", "by_shape_tag"),
        ("path_status", "by_path_status"),
        ("holding_periods", "by_holding_period"),
    ):
        items = _top_dimension_items(dimensions.get(key, {}))
        if items:
            lines.append(f"{title}=" + " | ".join(items))
    return "\n".join(lines)


def _top_dimension_items(items: dict, *, limit: int = 5) -> list[str]:
    ranked = sorted(
        items.items(),
        key=lambda item: (
            item[1].get("pick_count") or 0,
            item[1].get("average_return_pct") or -999999,
        ),
        reverse=True,
    )
    return [
        (
            f"{label}:n={stats.get('pick_count')},"
            f"avg={stats.get('average_return_pct')},win={stats.get('win_rate')}"
        )
        for label, stats in ranked[:limit]
    ]


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{float(value):.2f}%"


def _format_audit_explain(result: dict) -> str:
    profile = result.get("profile_coverage", {})
    lines = [
        f"project={result.get('project')} positioning={result.get('positioning')}",
        f"strategies={result.get('strategy_count')} categories={result.get('categories')}",
        "profile_coverage="
        + ", ".join(
            f"{name}:{item.get('configured')}/{result.get('strategy_count')}"
            for name, item in profile.items()
        ),
    ]
    lines.append("strengths:")
    for item in result.get("strengths", []):
        lines.append(f"- [{item.get('area')}] {item.get('message')}")

    findings = result.get("strategy_findings", [])
    if findings:
        lines.append("strategy_findings:")
        for item in findings:
            lines.append(
                f"- [{item.get('severity')}] {item.get('strategy')} "
                f"{item.get('area')}: {item.get('message')} "
                f"next={item.get('recommendation')}"
            )

    lines.append("project_gaps:")
    for item in result.get("project_gaps", []):
        lines.append(
            f"- [{item.get('severity')}] {item.get('area')}: "
            f"{item.get('message')} next={item.get('recommendation')}"
        )

    lines.append("next_priorities:")
    for item in result.get("next_priorities", []):
        lines.append(f"- {item}")
    return "\n".join(lines)


def _write_industry_cache_metadata(
    output_path: Path,
    *,
    provider: str,
    max_boards: int,
    rows: int,
    notes: list[str],
    generated_at: str | None = None,
    history_path: Path | None = None,
) -> Path:
    metadata_path = output_path.with_suffix(output_path.suffix + ".meta.json")
    metadata = {
        "generated_at": generated_at or datetime.now().isoformat(),
        "provider": provider,
        "max_boards": max_boards,
        "rows": rows,
        "history_path": str(history_path) if history_path is not None else "",
        "notes": notes,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata_path


def _append_industry_cache_history(
    output_path: Path,
    *,
    mapping: dict[str, dict[str, object]],
    generated_at: str,
) -> Path:
    history_path = output_path.with_suffix(output_path.suffix + ".history.jsonl")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    records = _industry_cache_history_records(mapping, generated_at=generated_at)
    if records:
        with history_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    else:
        history_path.touch()
    return history_path


def _industry_cache_history_records(
    mapping: dict[str, dict[str, object]],
    *,
    generated_at: str,
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for code, item in mapping.items():
        summaries = _split_board_heat_summary(item.get("board_heat_summary", ""))
        heat_score = _safe_float(item.get("board_heat_score"))
        for summary in summaries:
            board = summary.split(":", 1)[0].strip()
            if not board:
                continue
            record = grouped.setdefault(summary, {
                "generated_at": generated_at,
                "board": board,
                "summary": summary,
                "code_count": 0,
                "max_board_heat_score": None,
                "sample_codes": [],
            })
            record["code_count"] = int(record["code_count"]) + 1
            current_heat = _safe_float(record.get("max_board_heat_score"))
            if heat_score is not None and (current_heat is None or heat_score > current_heat):
                record["max_board_heat_score"] = heat_score
            sample_codes = record["sample_codes"]
            if isinstance(sample_codes, list) and len(sample_codes) < 20:
                sample_codes.append(code)
    return sorted(grouped.values(), key=lambda item: str(item.get("summary", "")))


def _split_board_heat_summary(value: object) -> list[str]:
    summaries = []
    for item in str(value or "").split("|"):
        summary = item.strip()
        if summary and summary.lower() not in {"nan", "none", "<na>"}:
            summaries.append(summary)
    return summaries


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _apply_env_file_args(env_files: list[str] | None) -> None:
    if not env_files:
        return
    existing = os.environ.get("ALPHASIFT_ENV_FILES", "")
    items = [item for item in existing.split(os.pathsep) if item]
    items.extend(env_files)
    os.environ["ALPHASIFT_ENV_FILES"] = os.pathsep.join(items)


def _split_csv_args(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    result = []
    for value in values:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return result


if __name__ == "__main__":
    main()
