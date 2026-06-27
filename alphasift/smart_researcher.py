# -*- coding: utf-8 -*-
"""
Smart Researcher — AI 驱动的智能选股研究
结合网络搜索 + 策略筛选 + LLM 分析，实现"AI 上网查数据选股"。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from alphasift.web_research import (
    WebResearcher,
    StockResearch,
    batch_research_stocks,
    format_research_for_llm,
)
from alphasift.data_manager import DataSourceManager

logger = logging.getLogger(__name__)


@dataclass
class SmartScreenRequest:
    """智能选股请求。"""
    strategy_name: str = ""
    personal_requirements: str = ""
    max_candidates: int = 20
    max_research_stocks: int = 10
    include_news: bool = True
    include_financials: bool = True
    include_analyst: bool = False
    custom_queries: list[str] = field(default_factory=list)
    market_topics: list[str] = field(default_factory=list)


@dataclass
class SmartScreenResult:
    """智能选股结果。"""
    strategy: str
    personal_requirements: str
    snapshot_count: int = 0
    filtered_count: int = 0
    researched_count: int = 0
    market_sentiment: str = ""
    picks: list[dict[str, Any]] = field(default_factory=list)
    research_context: str = ""
    llm_analysis: str = ""
    error: str = ""


class SmartResearcher:
    """
    AI 驱动的智能选股研究器。
    整合：数据获取 → 策略筛选 → 网络研究 → LLM 分析 → 输出结果
    """

    def __init__(
        self,
        *,
        researcher: WebResearcher | None = None,
        data_manager: DataSourceManager | None = None,
    ):
        self.researcher = researcher or WebResearcher()
        self.data_manager = data_manager or DataSourceManager()

    def smart_screen(self, request: SmartScreenRequest) -> SmartScreenResult:
        """
        执行智能选股流程。

        步骤:
        1. 获取实时行情快照
        2. 应用策略硬筛选
        3. 网络研究候选股票
        4. 研究市场情绪
        5. 综合 LLM 分析
        6. 输出最终结果
        """
        result = SmartScreenResult(
            strategy=request.strategy_name,
            personal_requirements=request.personal_requirements,
        )

        try:
            # 步骤 1: 获取行情快照
            logger.info("步骤 1/5: 获取实时行情快照…")
            snapshot_df = self.data_manager.fetch_realtime_snapshot()
            result.snapshot_count = len(snapshot_df)
            logger.info("获取到 %d 条行情数据", result.snapshot_count)

            # 步骤 2: 应用策略筛选
            logger.info("步骤 2/5: 应用策略筛选…")
            candidates_df = self._apply_strategy_filter(
                snapshot_df, request.strategy_name
            )
            result.filtered_count = len(candidates_df)
            logger.info("策略筛选后剩余 %d 只股票", result.filtered_count)

            # 步骤 3: 网络研究候选股票
            logger.info("步骤 3/5: 网络研究候选股票…")
            if not candidates_df.empty:
                stocks = self._df_to_stock_list(candidates_df)
                researches = batch_research_stocks(
                    stocks,
                    self.researcher,
                    max_stocks=request.max_research_stocks,
                    include_news=request.include_news,
                    include_financials=request.include_financials,
                    include_analyst=request.include_analyst,
                    custom_queries=request.custom_queries,
                )
                result.researched_count = len(researches)
                result.research_context = format_research_for_llm(researches)
                logger.info("完成 %d 只股票的网络研究", result.researched_count)

            # 步骤 4: 研究市场情绪
            if request.market_topics:
                logger.info("步骤 4/5: 研究市场情绪…")
                sentiment = self.researcher.research_market_sentiment(
                    topics=request.market_topics
                )
                if sentiment.success:
                    result.market_sentiment = self._format_sentiment(sentiment)

            # 步骤 5: 综合分析
            logger.info("步骤 5/5: 综合分析…")
            result.picks = self._format_picks(candidates_df)

        except Exception as e:
            logger.error("智能选股失败: %s", e)
            result.error = str(e)

        return result

    def research_single_stock(
        self,
        code: str,
        name: str = "",
        *,
        custom_queries: list[str] | None = None,
    ) -> StockResearch:
        """研究单只股票。"""
        return self.researcher.research_stock(
            code, name,
            include_basic=True,
            include_news=True,
            include_financials=True,
            include_analyst=True,
            custom_queries=custom_queries,
        )

    def _apply_strategy_filter(
        self, df: pd.DataFrame, strategy_name: str
    ) -> pd.DataFrame:
        """应用策略筛选。"""
        from alphasift.strategy import load_all_strategies
        from alphasift.filter import apply_hard_filters
        from alphasift.config import Config

        try:
            config = Config.from_env()
            strategies = load_all_strategies(config.strategies_dir)
            if strategy_name in strategies:
                strategy = strategies[strategy_name]
                filters = strategy.screening.hard_filters
                return apply_hard_filters(df, filters)
            else:
                logger.warning("策略 '%s' 不存在，使用默认筛选", strategy_name)
                return self._default_filter(df)
        except Exception as e:
            logger.warning("策略 '%s' 加载失败，使用默认筛选: %s", strategy_name, e)
            return self._default_filter(df)

    def _default_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """默认筛选：排除 ST，要求有价格和成交额。"""
        result = df.copy()
        if "name" in result.columns:
            result = result[~result["name"].str.contains(r"ST|退", na=False)]
        if "price" in result.columns:
            result = result[pd.to_numeric(result["price"], errors="coerce") > 0]
        if "amount" in result.columns:
            result = result[pd.to_numeric(result["amount"], errors="coerce") > 10000000]
        return result

    def _df_to_stock_list(self, df: pd.DataFrame) -> list[dict[str, str]]:
        """将 DataFrame 转为股票列表。"""
        stocks = []
        for _, row in df.iterrows():
            code = str(row.get("code", ""))
            name = str(row.get("name", ""))
            if code:
                stocks.append({"code": code, "name": name})
        return stocks

    def _format_sentiment(self, report) -> str:
        """格式化市场情绪。"""
        items = [f"- {r.title}: {r.snippet[:100]}" for r in report.results[:5]]
        return "\n".join(items) if items else ""

    def _format_picks(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        """格式化选股结果。"""
        picks = []
        for i, (_, row) in enumerate(df.iterrows()):
            pick = {
                "rank": i + 1,
                "code": str(row.get("code", "")),
                "name": str(row.get("name", "")),
                "price": float(row.get("price", 0) or 0),
                "change_pct": float(row.get("change_pct", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
                "pe_ratio": float(row.get("pe_ratio", 0) or 0),
                "pb_ratio": float(row.get("pb_ratio", 0) or 0),
                "turnover_rate": float(row.get("turnover_rate", 0) or 0),
                "volume_ratio": float(row.get("volume_ratio", 0) or 0),
                "total_mv": float(row.get("total_mv", 0) or 0),
            }
            picks.append(pick)
        return picks


# ---------------------------------------------------------------------------
# 个人策略解析器
# ---------------------------------------------------------------------------

def parse_personal_requirements(text: str) -> dict[str, Any]:
    """
    解析用户的自然语言选股要求，转化为结构化条件。
    
    示例输入:
        "我想要PE低于20、市值大于100亿的银行股，最近有利好新闻"
    
    示例输出:
        {
            "pe_ttm_max": 20,
            "market_cap_min": 10000000000,
            "industry": ["银行"],
            "news_keywords": ["利好"],
        }
    """
    import re
    
    result: dict[str, Any] = {}
    
    # PE 条件
    pe_match = re.search(r"PE[<≤低于不高于]*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if pe_match:
        result["pe_ttm_max"] = float(pe_match.group(1))
    
    pe_min_match = re.search(r"PE[>≥高于不低于]*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if pe_min_match:
        result["pe_ttm_min"] = float(pe_min_match.group(1))
    
    # PB 条件
    pb_match = re.search(r"PB[<≤低于不高于]*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if pb_match:
        result["pb_max"] = float(pb_match.group(1))
    
    # 市值条件
    mv_match = re.search(r"市值[>≥大于]*(\d+(?:\.\d+)?)\s*亿", text)
    if mv_match:
        result["market_cap_min"] = float(mv_match.group(1)) * 1e8
    
    mv_max_match = re.search(r"市值[<≤小于]*(\d+(?:\.\d+)?)\s*亿", text)
    if mv_max_match:
        result["market_cap_max"] = float(mv_max_match.group(1)) * 1e8
    
    # 成交额条件
    amt_match = re.search(r"成交额[>≥大于]*(\d+(?:\.\d+)?)\s*[亿万]", text)
    if amt_match:
        val = float(amt_match.group(1))
        if "亿" in text[amt_match.start():amt_match.end() + 5]:
            result["amount_min"] = val * 1e8
        else:
            result["amount_min"] = val
    
    # 行业关键词
    industry_keywords = [
        "银行", "券商", "保险", "地产", "医药", "科技", "消费", "白酒",
        "新能源", "半导体", "芯片", "光伏", "锂电", "军工", "汽车",
        "电力", "燃气", "水务", "公用事业", "食品", "家电", "零售",
        "钢铁", "煤炭", "有色", "化工", "机械", "建筑", "交通",
    ]
    found_industries = [kw for kw in industry_keywords if kw in text]
    if found_industries:
        result["industry"] = found_industries
    
    # 排除条件
    if "排除ST" in text or "不要ST" in text:
        result["exclude_st"] = True
    
    # 涨跌幅条件
    change_match = re.search(r"涨[幅跌度][<≤低于]*(\-?\d+(?:\.\d+)?)\s*%", text)
    if change_match:
        result["change_pct_max"] = float(change_match.group(1))
    
    # 换手率条件
    turnover_match = re.search(r"换手率[>≥高于]*(\d+(?:\.\d+)?)\s*%", text)
    if turnover_match:
        result["turnover_rate_min"] = float(turnover_match.group(1))
    
    # 新闻关键词
    news_keywords = []
    if "利好" in text:
        news_keywords.append("利好")
    if "突破" in text:
        news_keywords.append("突破")
    if "增长" in text or "增长" in text:
        news_keywords.append("增长")
    if news_keywords:
        result["news_keywords"] = news_keywords
    
    return result


def build_custom_strategy_from_requirements(
    requirements: str,
    base_strategy: str = "balanced_alpha",
) -> dict[str, Any]:
    """
    从个人要求构建自定义策略配置。
    """
    parsed = parse_personal_requirements(requirements)
    
    # 加载基础策略
    from alphasift.strategy import load_all_strategies
    from alphasift.config import Config
    try:
        config = Config.from_env()
        strategies = load_all_strategies(config.strategies_dir)
        if base_strategy in strategies:
            strategy = strategies[base_strategy]
            # 从策略对象构建配置
            config = {
                "name": f"personal_{base_strategy}",
                "display_name": f"个人定制 ({base_strategy})",
                "description": requirements[:200],
                "version": "1.0",
                "category": "personal",
                "tags": ["personal", "custom"],
                "screening": {
                    "enabled": True,
                    "market_scope": strategy.screening.market_scope,
                    "hard_filters": {},
                    "tech_weight": strategy.screening.tech_weight,
                    "factor_weights": strategy.screening.factor_weights,
                    "ranking_hints": f"基于个人要求: {requirements[:300]}",
                    "max_output": strategy.screening.max_output,
                },
            }
        else:
            raise ValueError(f"策略 '{base_strategy}' 不存在")
    except Exception:
        config = {
            "name": "personal_custom",
            "display_name": "个人定制",
            "description": requirements[:200],
            "version": "1.0",
            "category": "personal",
            "tags": ["personal", "custom"],
            "screening": {
                "enabled": True,
                "market_scope": ["cn"],
                "hard_filters": {},
                "tech_weight": 0.30,
                "factor_weights": {
                    "value": 0.30,
                    "stability": 0.20,
                    "liquidity": 0.15,
                    "momentum": 0.15,
                    "size": 0.10,
                    "activity": 0.10,
                },
                "ranking_hints": f"基于个人要求: {requirements[:300]}",
                "max_output": 10,
            },
        }
    
    # 应用个人条件到 hard_filters
    filters = config["screening"]["hard_filters"]
    if "pe_ttm_max" in parsed:
        filters["pe_ttm_max"] = parsed["pe_ttm_max"]
    if "pe_ttm_min" in parsed:
        filters["pe_ttm_min"] = parsed["pe_ttm_min"]
    if "pb_max" in parsed:
        filters["pb_max"] = parsed["pb_max"]
    if "market_cap_min" in parsed:
        filters["market_cap_min"] = parsed["market_cap_min"]
    if "market_cap_max" in parsed:
        filters["market_cap_max"] = parsed["market_cap_max"]
    if "amount_min" in parsed:
        filters["amount_min"] = parsed["amount_min"]
    if "change_pct_max" in parsed:
        filters["change_pct_max"] = parsed["change_pct_max"]
    if "turnover_rate_min" in parsed:
        filters["turnover_rate_min"] = parsed["turnover_rate_min"]
    if parsed.get("exclude_st", True):
        filters["exclude_st"] = True
    
    return config
