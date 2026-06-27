# -*- coding: utf-8 -*-
"""Automated market-wide news fetcher for LLM context enrichment.

Fetches macro/policy/market-level news from akshare sources and compresses
into text for injection into the LLM ranking context as a ``【市场资讯】``
section.

Providers:
  headlines  — EastMoney 7x24 global finance news (ak.stock_info_global_em)
  policy     — Financial calendar / policy events (ak.stock_info_cjzc_em)
  activity   — Market activity metrics (ak.stock_market_activity_legu)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _cell(row, candidates: list[str]) -> str:
    """Extract a string value from a DataFrame row, trying multiple column names."""
    for col in candidates:
        val = row.get(col)
        if val is not None and str(val).strip() and str(val).strip().lower() != "nan":
            return str(val).strip()
    return ""


def fetch_market_headlines(*, limit: int = 15) -> str:
    """Fetch EastMoney 7x24 global finance headlines.

    Returns compressed text of top headlines with timestamps.
    """
    try:
        import akshare as ak
        df = ak.stock_info_global_em()
        if df is None or df.empty:
            return ""
        items = []
        for _, row in df.head(limit).iterrows():
            title = _cell(row, ["标题", "title", "新闻标题"])
            time_str = _cell(row, ["发布时间", "publish_time", "时间"])
            if title:
                ts = time_str[:16] if len(time_str) >= 16 else time_str
                items.append(f"{ts} {title}" if ts else title)
        return " | ".join(items)
    except Exception as exc:
        logger.warning("market headlines fetch failed: %s", exc)
        return ""


def fetch_policy_news(*, limit: int = 10) -> str:
    """Fetch EastMoney financial calendar / policy event summaries.

    Returns compressed text of recent policy and market events.
    """
    try:
        import akshare as ak
        df = ak.stock_info_cjzc_em()
        if df is None or df.empty:
            return ""
        items = []
        for _, row in df.head(limit).iterrows():
            title = _cell(row, ["标题", "title"])
            summary = _cell(row, ["摘要", "summary"])
            # Prefer summary (richer) but truncate
            text = summary if summary else title
            if text:
                items.append(text[:200])
        return " | ".join(items)
    except Exception as exc:
        logger.warning("policy news fetch failed: %s", exc)
        return ""


def fetch_market_activity() -> str:
    """Fetch market activity metrics (limit-up/down counts, activity ratio).

    Returns a one-line summary like: "涨停42家 跌停5家 活跃度14.5%".
    """
    try:
        import akshare as ak
        df = ak.stock_market_activity_legu()
        if df is None or df.empty:
            return ""
        metrics = {}
        for _, row in df.iterrows():
            item = _cell(row, ["item", "项目", "指标"])
            value = _cell(row, ["value", "数值"])
            if item and value:
                metrics[item] = value
        # Build a concise summary from the key metrics
        parts = []
        for key, value in metrics.items():
            if key == "上涨":
                parts.append(f"上涨{value}家")
            elif key == "下跌":
                parts.append(f"下跌{value}家")
            elif key == "涨停" and "真实" not in key and "st" not in key.lower():
                parts.append(f"涨停{value}家")
            elif key == "跌停" and "真实" not in key and "st" not in key.lower():
                parts.append(f"跌停{value}家")
            elif "活跃" in key:
                parts.append(f"活跃度{value}")
        return " ".join(parts) if parts else ""
    except Exception as exc:
        logger.warning("market activity fetch failed: %s", exc)
        return ""


def collect_market_news(
    *,
    providers: list[str] | None = None,
    max_chars: int = 800,
    cache_dir: str | Path | None = None,
    cache_ttl_hours: int = 4,
    headlines_limit: int = 15,
    policy_limit: int = 10,
) -> str:
    """Collect market-wide news from multiple sources and return compressed text.

    This is the main entry point. Returns a string suitable for injection into
    the LLM context as a ``【市场资讯】`` section.  Caches results to avoid
    re-fetching on every run within the TTL window.
    """
    providers = providers if providers is not None else ["headlines", "policy", "activity"]

    # Check cache
    if cache_dir:
        cached = _read_cache(cache_dir, cache_ttl_hours)
        if cached is not None:
            return cached[:max_chars]

    sections: list[str] = []

    if "headlines" in providers:
        text = fetch_market_headlines(limit=headlines_limit)
        if text:
            sections.append(f"【财经快讯】{text}")

    if "policy" in providers:
        text = fetch_policy_news(limit=policy_limit)
        if text:
            sections.append(f"【政策动向】{text}")

    if "activity" in providers:
        text = fetch_market_activity()
        if text:
            sections.append(f"【市场活跃度】{text}")

    combined = "\n".join(sections).strip()
    if not combined:
        return ""
    if len(combined) > max_chars:
        combined = combined[: max_chars - 20].rstrip() + "\n...[truncated]"

    # Write cache
    if cache_dir:
        _write_cache(cache_dir, combined)

    return combined


# ═══════════════════════════════════════════════════════════════
#  Cache helpers
# ═══════════════════════════════════════════════════════════════

def _cache_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / "market_news.json"


def _read_cache(cache_dir: str | Path, ttl_hours: int) -> str | None:
    path = _cache_path(cache_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data["timestamp"])
        if (datetime.now() - cached_at).total_seconds() > ttl_hours * 3600:
            return None
        return data.get("text", "")
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def _write_cache(cache_dir: str | Path, text: str) -> None:
    path = _cache_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"timestamp": datetime.now().isoformat(), "text": text}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
