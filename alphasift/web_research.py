# -*- coding: utf-8 -*-
"""
Web Research Module — AI 网络搜索能力
使用新浪/腾讯财经接口获取股票数据和新闻。
（DuckDuckGo/Google 在你的网络环境下被封，已移除）
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str = ""
    published: str = ""


@dataclass
class ResearchReport:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    summary: str = ""
    search_engine: str = ""
    success: bool = True
    error: str = ""


@dataclass
class StockResearch:
    code: str
    name: str
    basic_info: ResearchReport | None = None
    news: ResearchReport | None = None
    financials: ResearchReport | None = None
    analyst: ResearchReport | None = None
    custom_queries: list[ResearchReport] = field(default_factory=list)

    def to_context_text(self, max_chars: int = 2000) -> str:
        sections = []
        for label, report in [
            ("基本面", self.basic_info),
            ("新闻动态", self.news),
            ("财务数据", self.financials),
            ("分析师观点", self.analyst),
        ]:
            if report and report.results:
                items = []
                for r in report.results[:3]:
                    item = f"- {r.title}: {r.snippet[:120]}"
                    if r.published:
                        item += f" ({r.published})"
                    items.append(item)
                if items:
                    sections.append(f"[{label}]\n" + "\n".join(items))

        for i, report in enumerate(self.custom_queries):
            if report and report.results:
                items = [f"- {r.title}: {r.snippet[:120]}" for r in report.results[:3]]
                if items:
                    sections.append(f"[自定义搜索{i+1}: {report.query}]\n" + "\n".join(items))

        text = "\n\n".join(sections)
        return text[:max_chars] if len(text) > max_chars else text


# ---------------------------------------------------------------------------
# 可用的搜索引擎（已验证在你的网络环境下能用）
# ---------------------------------------------------------------------------

class EastmoneyNewsSearch:
    """东方财富新闻搜索 — 已验证可用。"""

    SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"

    def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        try:
            params = {
                "cb": "jQuery_callback",
                "param": json.dumps({
                    "uid": "",
                    "keyword": query,
                    "type": ["cmsArticleWebOld"],
                    "client": "web",
                    "clientType": "web",
                    "clientVersion": "curr",
                    "param": {
                        "cmsArticleWebOld": {
                            "searchScope": "default",
                            "sort": "default",
                            "pageIndex": 1,
                            "pageSize": num_results,
                            "preTag": "",
                            "postTag": "",
                        }
                    },
                }),
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                "Referer": "https://so.eastmoney.com/",
            }
            resp = requests.get(self.SEARCH_URL, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            text = resp.text
            json_match = re.search(r"jQuery_callback\((.*)\)", text, re.DOTALL)
            if not json_match:
                return []
            data = json.loads(json_match.group(1))
            results = []
            for item in (
                data.get("result", {})
                .get("cmsArticleWebOld", {})
                .get("list", [])[:num_results]
            ):
                title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
                content = re.sub(r"<[^>]+>", "", item.get("content", "")).strip()
                results.append(SearchResult(
                    title=title,
                    url=item.get("url", ""),
                    snippet=content[:200],
                    source="eastmoney",
                    published=item.get("date", ""),
                ))
            return results
        except Exception as e:
            logger.warning("Eastmoney news search failed for '%s': %s", query, e)
            return []


class AkshareNewsSearch:
    """akshare 新闻搜索 — 已验证可用。"""

    def search(self, code: str, num_results: int = 5) -> list[SearchResult]:
        try:
            import akshare as ak
            df = ak.stock_news_em(symbol=code)
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(num_results).iterrows():
                # akshare 新闻 DataFrame 的列名可能变化，尝试多种可能
                title = ""
                content = ""
                url = ""
                published = ""
                for col in df.columns:
                    val = str(row[col])
                    if "标题" in col or "title" in col.lower():
                        title = val
                    elif "内容" in col or "content" in col.lower() or "摘要" in col:
                        content = val
                    elif "链接" in col or "url" in col.lower():
                        url = val
                    elif "时间" in col or "date" in col.lower() or "发布" in col:
                        published = val

                if not title:
                    # 如果没有明确的标题列，用第一列
                    title = str(row.iloc[0]) if len(row) > 0 else ""
                if not content:
                    content = str(row.iloc[1]) if len(row) > 1 else ""

                if title:
                    results.append(SearchResult(
                        title=title[:100],
                        url=url,
                        snippet=content[:200],
                        source="akshare_news",
                        published=published,
                    ))
            return results
        except Exception as e:
            logger.warning("akshare news search failed for '%s': %s", code, e)
            return []


class AkshareAnnouncementSearch:
    """akshare 公告搜索 — 已验证可用。"""

    def search(self, code: str, num_results: int = 5) -> list[SearchResult]:
        try:
            import akshare as ak
            df = ak.stock_notice_report(symbol=code)
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.head(num_results).iterrows():
                title = ""
                url = ""
                published = ""
                for col in df.columns:
                    val = str(row[col])
                    if "标题" in col or "title" in col.lower():
                        title = val
                    elif "链接" in col or "url" in col.lower():
                        url = val
                    elif "时间" in col or "date" in col.lower() or "发布" in col:
                        published = val
                if not title and len(row) > 0:
                    title = str(row.iloc[0])
                if title:
                    results.append(SearchResult(
                        title=title[:100],
                        url=url,
                        snippet="",
                        source="akshare_announcement",
                        published=published,
                    ))
            return results
        except Exception as e:
            logger.warning("akshare announcement search failed for '%s': %s", code, e)
            return []


class SinaQuoteSearch:
    """新浪实时行情搜索 — 直接调用 hq.sinajs.cn，已验证可用。"""

    def search(self, code: str, name: str = "") -> list[SearchResult]:
        try:
            # 确定市场前缀
            if code.startswith("6"):
                symbol = f"sh{code}"
            elif code.startswith(("0", "3")):
                symbol = f"sz{code}"
            elif code.startswith(("4", "8")):
                symbol = f"bj{code}"
            else:
                symbol = f"sz{code}"

            url = f"https://hq.sinajs.cn/list={symbol}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                "Referer": "https://finance.sina.com.cn/",
            }
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            match = re.match(r'var hq_str_\w+="(.*)";', resp.text.strip())
            if not match:
                return []

            fields = match.group(1).split(",")
            if len(fields) < 32:
                return []

            stock_name = fields[0]
            open_price = fields[1]
            prev_close = fields[2]
            price = fields[3]
            high = fields[4]
            low = fields[5]
            volume = fields[8]
            amount = fields[9]
            date_str = fields[30]
            time_str = fields[31]

            snippet = (
                f"日期:{date_str} 时间:{time_str} "
                f"现价:{price} 涨跌:{float(price)-float(prev_close):.2f} "
                f"开盘:{open_price} 最高:{high} 最低:{low} "
                f"成交量:{volume} 成交额:{amount}"
            )

            return [SearchResult(
                title=f"{stock_name}({code}) 实时行情",
                url=f"https://finance.sina.com.cn/realstock/company/{symbol}/nc.shtml",
                snippet=snippet,
                source="sina_quote",
                published=f"{date_str} {time_str}",
            )]
        except Exception as e:
            logger.warning("Sina quote search failed for '%s': %s", code, e)
            return []


class TencentQuoteSearch:
    """腾讯实时行情搜索 — 直接调用 qt.gtimg.cn，已验证可用。"""

    def search(self, code: str, name: str = "") -> list[SearchResult]:
        try:
            if code.startswith("6"):
                symbol = f"sh{code}"
            elif code.startswith(("0", "3")):
                symbol = f"sz{code}"
            elif code.startswith(("4", "8")):
                symbol = f"bj{code}"
            else:
                symbol = f"sz{code}"

            url = f"https://qt.gtimg.cn/q={symbol}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            match = re.match(r'v_\w+="(.*)";', resp.text.strip())
            if not match:
                return []

            fields = match.group(1).split("~")
            if len(fields) < 45:
                return []

            stock_name = fields[1]
            price = fields[3]
            prev_close = fields[4]
            open_price = fields[5]
            volume = fields[6]
            high = fields[33]
            low = fields[34]
            amount = fields[37]
            change_pct = fields[32]

            snippet = (
                f"现价:{price} 涨跌:{change_pct}% "
                f"开盘:{open_price} 最高:{high} 最低:{low} "
                f"成交量:{volume} 成交额:{amount}"
            )

            return [SearchResult(
                title=f"{stock_name}({code}) 实时行情",
                url=f"https://stockapp.finance.qq.com/mstats/#mod=list&id={symbol}&module=SS&type=&metric=",
                snippet=snippet,
                source="tencent_quote",
            )]
        except Exception as e:
            logger.warning("Tencent quote search failed for '%s': %s", code, e)
            return []


# ---------------------------------------------------------------------------
# Web Researcher — 统一搜索入口
# ---------------------------------------------------------------------------

class WebResearcher:
    """
    统一的网络研究接口。
    使用已验证可用的数据源：东方财富新闻、akshare新闻/公告、新浪/腾讯行情。
    """

    def __init__(self, *, rate_limit_sec: float = 1.0):
        self.news_engine = EastmoneyNewsSearch()
        self.akshare_news = AkshareNewsSearch()
        self.akshare_announcement = AkshareAnnouncementSearch()
        self.sina_quote = SinaQuoteSearch()
        self.tencent_quote = TencentQuoteSearch()
        self.rate_limit_sec = rate_limit_sec
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)
        self._last_request_time = time.time()

    def search_news(self, query: str, num_results: int = 5) -> ResearchReport:
        """搜索新闻。"""
        self._rate_limit()
        results = self.news_engine.search(query, num_results)
        if results:
            return ResearchReport(
                query=query, results=results,
                search_engine="EastmoneyNews", success=True,
            )
        return ResearchReport(query=query, success=False, error="No results")

    def search(self, query: str, num_results: int = 5) -> ResearchReport:
        """通用搜索（使用东方财富新闻）。"""
        return self.search_news(query, num_results)

    def research_stock(
        self,
        code: str,
        name: str = "",
        *,
        include_basic: bool = True,
        include_news: bool = True,
        include_financials: bool = True,
        include_analyst: bool = True,
        custom_queries: list[str] | None = None,
    ) -> StockResearch:
        """对一只股票进行全面研究。"""
        label = f"{code} {name}".strip()
        research = StockResearch(code=code, name=name)

        # 实时行情（用新浪，最可靠）
        if include_basic:
            research.basic_info = ResearchReport(
                query=f"{label} 行情",
                results=self.sina_quote.search(code, name),
                search_engine="SinaQuote",
                success=True,
            )

        # 新闻
        if include_news:
            news_results = self.akshare_news.search(code, 5)
            if not news_results:
                news_results = self.news_engine.search(f"{name} 最新消息", 5)
            research.news = ResearchReport(
                query=f"{label} 新闻",
                results=news_results,
                search_engine="AkshareNews",
                success=bool(news_results),
            )

        # 财务数据
        if include_financials:
            research.financials = self.search_news(
                f"{label} 财报 营收 净利润", 5
            )

        # 分析师观点
        if include_analyst:
            research.analyst = self.search_news(
                f"{label} 研报 评级 目标价", 5
            )

        # 自定义查询
        if custom_queries:
            for query_template in custom_queries:
                query = query_template.replace("{code}", code).replace("{name}", name)
                research.custom_queries.append(self.search_news(query, 5))

        return research

    def research_market_sentiment(self, *, market: str = "A股", topics: list[str] | None = None) -> ResearchReport:
        """研究市场整体情绪。"""
        query_parts = [f"{market} 今日行情"]
        if topics:
            query_parts.extend(topics)
        return self.search_news(" ".join(query_parts), 5)


def batch_research_stocks(
    stocks: list[dict[str, str]],
    researcher: WebResearcher | None = None,
    *,
    max_stocks: int = 10,
    include_basic: bool = True,
    include_news: bool = True,
    include_financials: bool = False,
    include_analyst: bool = False,
    custom_queries: list[str] | None = None,
) -> dict[str, StockResearch]:
    """批量研究多只股票。"""
    if researcher is None:
        researcher = WebResearcher()

    results = {}
    for i, stock in enumerate(stocks[:max_stocks]):
        code = stock.get("code", "")
        name = stock.get("name", "")
        if not code:
            continue
        logger.info("研究股票 %d/%d: %s %s", i + 1, min(len(stocks), max_stocks), code, name)
        results[code] = researcher.research_stock(
            code, name,
            include_basic=include_basic,
            include_news=include_news,
            include_financials=include_financials,
            include_analyst=include_analyst,
            custom_queries=custom_queries,
        )
    return results


def format_research_for_llm(researches: dict[str, StockResearch], max_chars_per_stock: int = 1500) -> str:
    """将批量研究结果格式化为 LLM 上下文文本。"""
    sections = []
    for code, research in researches.items():
        text = research.to_context_text(max_chars_per_stock)
        if text:
            sections.append(f"## {code} {research.name}\n{text}")
    combined = "\n\n".join(sections)
    return combined[:8000] if len(combined) > 8000 else combined


def quick_stock_research(code: str, name: str = "") -> StockResearch:
    """快速股票研究。"""
    researcher = WebResearcher()
    return researcher.research_stock(code, name)
