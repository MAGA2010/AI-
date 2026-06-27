# -*- coding: utf-8 -*-
"""Market snapshot fetcher.

Fetches full-market real-time snapshots for screening.
This is separate from single-stock realtime quotes.
"""

import logging
import os
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# akshare 新浪接口的列名映射
_AKSHARE_SINA_RENAME_MAP = {
    "代码": "code",
    "名称": "name",
    "最新价": "price",
    "涨跌额": "change_amt",
    "涨跌幅": "change_pct",
    "成交量": "volume",
    "成交额": "amount",
    "昨收": "prev_close",
    "今开": "open",
    "最高": "high",
    "最低": "low",
    "买入": "bid1",
    "卖出": "ask1",
    "时间戳": "time_str",
}


def fetch_cn_snapshot(source: str = "efinance") -> pd.DataFrame:
    """Fetch A-share full-market snapshot.

    Returns a DataFrame with columns:
        code, name, price, change_pct, amount, total_mv, circ_mv,
        pe_ratio, pb_ratio, volume_ratio, turnover_rate

    Raises RuntimeError if the source is unavailable.
    """
    if source == "efinance":
        return _fetch_efinance()
    elif source == "akshare_em":
        return _fetch_akshare_em()
    elif source == "akshare_sina":
        return _fetch_akshare_sina()
    elif source == "em_datacenter":
        return _fetch_em_datacenter()
    elif source == "tushare":
        return _fetch_tushare()
    elif source == "baostock":
        return _fetch_baostock()
    elif source == "data_manager":
        return _fetch_via_data_manager()
    else:
        raise ValueError(f"Unknown snapshot source: {source}")


def fetch_snapshot_with_fallback(
    sources: list[str],
    *,
    required_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Try sources in order, return first source matching required schema."""
    errors = []
    for source in sources:
        try:
            df = fetch_cn_snapshot(source)
            if not df.empty:
                missing = _missing_required_columns(df, required_columns or [])
                if missing:
                    errors.append(
                        f"{source}: missing required columns {','.join(missing)}"
                    )
                    continue
                df.attrs["source_errors"] = list(errors)
                logger.info("Snapshot fetched from %s: %d rows", source, len(df))
                return df
            errors.append(f"{source}: returned empty data")
        except Exception as e:
            errors.append(f"{source}: {e}")
            logger.warning("Snapshot source %s failed: %s", source, e)
    raise RuntimeError(f"All snapshot sources failed: {'; '.join(errors)}")


def _missing_required_columns(df: pd.DataFrame, required_columns: list[str]) -> list[str]:
    missing: list[str] = []
    for col in required_columns:
        if col not in df.columns:
            missing.append(col)
            continue
        if df[col].dropna().empty:
            missing.append(col)
    return missing


def _fetch_efinance() -> pd.DataFrame:
    """Fetch via efinance."""
    import efinance as ef

    df = ef.stock.get_realtime_quotes()
    if df is None or df.empty:
        raise RuntimeError("efinance returned empty data")
    return _normalize(df, source="efinance")


def _fetch_akshare_em() -> pd.DataFrame:
    """Fetch via akshare (eastmoney)."""
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        raise RuntimeError("akshare returned empty data")
    return _normalize(df, source="akshare_em")


def _fetch_akshare_sina() -> pd.DataFrame:
    """Fetch via akshare (sina) — 这个接口在你的网络环境下可用。"""
    import akshare as ak
    import time

    # 添加重试逻辑，因为 akshare 偶尔会返回 HTML 而不是数据
    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        try:
            df = ak.stock_zh_a_spot()
            if df is not None and not df.empty:
                df = df.rename(columns=_AKSHARE_SINA_RENAME_MAP)

                # 数值类型转换
                numeric_cols = [
                    "price", "change_amt", "change_pct", "volume", "amount",
                    "prev_close", "open", "high", "low", "bid1", "ask1",
                ]
                for col in numeric_cols:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                # 过滤无效数据
                df = df[df["price"].notna() & (df["price"] > 0)]
                df = df[df["code"].notna()]

                df.attrs["source"] = "akshare_sina"
                return df
            else:
                raise RuntimeError("akshare sina returned empty data")
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(2)  # 等待 2 秒后重试
                continue

    raise RuntimeError(f"akshare sina failed after {max_retries} attempts: {last_error}")


def _fetch_via_data_manager() -> pd.DataFrame:
    """Fetch via DataSourceManager (uses multiple fallbacks)."""
    from alphasift.data_manager import DataSourceManager
    manager = DataSourceManager()
    return manager.fetch_realtime_snapshot()


def _fetch_em_datacenter() -> pd.DataFrame:
    """Fetch via eastmoney datacenter xuangu API.

    This works even on weekends (returns last trading day data).
    """
    import requests

    url = "https://data.eastmoney.com/dataapi/xuangu/list"
    all_items = []
    page = 1
    page_size = 500

    while True:
        params = {
            "st": "SECURITY_CODE",
            "sr": "1",
            "ps": str(page_size),
            "p": str(page),
            "sty": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NEW_PRICE,"
                   "CHANGE_RATE,VOLUME_RATIO,DEAL_AMOUNT,TURNOVERRATE,"
                   "PE9,PBNEWMRQ,TOTAL_MARKET_CAP,CIRCULATION_MARKET_CAP",
            "filter": '(MARKET+in+("上交所主板","深交所主板","深交所创业板","上交所科创板","北交所"))',
            "source": "SELECT_SECURITIES",
            "client": "WEB",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://data.eastmoney.com/xuangu/",
        }

        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            raise RuntimeError(f"em_datacenter API error: {data.get('message', data)}")

        result = data.get("result", {})
        items = result.get("data") or []
        all_items.extend(items)

        total_pages = result.get("pageCount") or result.get("pages") or 1
        if page >= total_pages:
            break
        page += 1

    if not all_items:
        raise RuntimeError("em_datacenter returned empty data")

    df = pd.DataFrame(all_items)
    df = df.rename(columns={
        "SECURITY_CODE": "code",
        "SECURITY_NAME_ABBR": "name",
        "NEW_PRICE": "price",
        "CHANGE_RATE": "change_pct",
        "VOLUME_RATIO": "volume_ratio",
        "DEAL_AMOUNT": "amount",
        "TURNOVERRATE": "turnover_rate",
        "PE9": "pe_ratio",
        "PBNEWMRQ": "pb_ratio",
        "TOTAL_MARKET_CAP": "total_mv",
        "CIRCULATION_MARKET_CAP": "circ_mv",
    })

    for col in [
        "price", "change_pct", "volume_ratio", "amount", "turnover_rate",
        "pe_ratio", "pb_ratio", "total_mv", "circ_mv",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return _normalize(df, source="em_datacenter")


def _fetch_tushare() -> pd.DataFrame:
    """Fetch via tushare."""
    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN environment variable not set")
    ts.set_token(token)
    pro = ts.pro_api()
    df = pro.daily_basic(
        trade_date=date.today().strftime("%Y%m%d"),
        ts_code="",
        fields="ts_code,close,pe_ttm,pb,turnover_rate_f,volume_ratio,total_mv,circ_mv",
    )
    if df is None or df.empty:
        prev_day = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        df = pro.daily_basic(
            trade_date=prev_day,
            ts_code="",
            fields="ts_code,close,pe_ttm,pb,turnover_rate_f,volume_ratio,total_mv,circ_mv",
        )
    if df is None or df.empty:
        raise RuntimeError("tushare returned empty data")
    df = df.rename(columns={"ts_code": "code", "close": "price", "pe_ttm": "pe_ratio", "pb": "pb_ratio", "turnover_rate_f": "turnover_rate"})
    return _normalize(df, source="tushare")


def _fetch_baostock() -> pd.DataFrame:
    """Fetch via baostock — 使用新浪服务器，不依赖东方财富。

    流程：
    1. 登录 → 直接用昨天日期
    2. query_hs300_stocks → 拿到300只成分股
    3. 逐只查最新日线（单线程，baostock非线程安全）
    """
    import baostock as bs
    from datetime import datetime, timedelta

    lg = bs.login()
    if lg.error_code != '0':
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")

    try:
        # ── 1. 直接用昨天日期 ──────────────────────────────
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # ── 2. 获取沪深300成分股（300只，瞬间完成）──────────
        rs = bs.query_hs300_stocks()
        hs300_codes = []
        while (rs.error_code == '0') and rs.next():
            row = rs.get_row_data()
            hs300_codes.append(row[1])  # sh.600000 格式

        if not hs300_codes:
            raise RuntimeError("baostock: 未获取到沪深300成分股")

        # ── 3. 获取名称映射 ─────────────────────────────────
        rs2 = bs.query_stock_basic()
        code_name_map = {}
        while (rs2.error_code == '0') and rs2.next():
            row = rs2.get_row_data()
            code_name_map[row[0]] = row[1]  # code -> name

        # ── 4. 逐只查最新日线（单线程，baostock非线程安全）──
        fields = "date,code,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ"
        total = len(hs300_codes)
        print(f"  [baostock] 开始获取 {total} 只股票数据...")

        all_data = []
        for i, code in enumerate(hs300_codes, 1):
            try:
                rs = bs.query_history_k_data_plus(
                    code, fields,
                    start_date=yesterday, end_date=yesterday,
                    frequency="d", adjustflag="3",
                )
                while (rs.error_code == '0') and rs.next():
                    all_data.append(rs.get_row_data())
            except Exception:
                pass

            if i % 50 == 0 or i == total:
                print(f"  [baostock] {i}/{total} 已完成，获取 {len(all_data)} 条有效数据")

        if not all_data:
            raise RuntimeError("baostock: 未获取到任何股票数据")

        print(f"  [baostock] 获取完成，共 {len(all_data)} 只有效股票")

        # ── 5. 组装 DataFrame ───────────────────────────────
        df = pd.DataFrame(all_data, columns=[
            "date", "code", "open", "high", "low", "close",
            "volume", "amount", "turnover_rate", "change_pct",
            "pe_ratio", "pb_ratio"
        ])

        numeric_cols = ["open", "high", "low", "close", "volume", "amount",
                        "turnover_rate", "change_pct", "pe_ratio", "pb_ratio"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # code: sh.600000 -> 600000
        df["code"] = df["code"].str.split(".").str[-1]

        # name 映射
        short_map = {k.split(".")[-1]: v for k, v in code_name_map.items()}
        df["name"] = df["code"].map(short_map)

        # price = close
        df["price"] = df["close"]

        # 过滤无效
        df = df[df["price"].notna() & (df["price"] > 0)]
        df = df[df["code"].notna() & (df["code"] != "")]
        df = df[df["code"].str.match(r"^[036]\d{5}$")]

        return _normalize(df, source="baostock")

    finally:
        bs.logout()


def _normalize(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if "code" not in df.columns:
        col = "ts_code" if "ts_code" in df.columns else df.columns[0]
        df = df.rename(columns={col: "code"})
    df["code"] = df["code"].astype(str).str.split(".").str[0]
    df.attrs["source"] = source
    return df


def take_snapshot(
    market: str = "cn",
    source_priority: list[str] | None = None,
    *,
    required_columns: list[str] | None = None,
    config: object | None = None,
) -> pd.DataFrame:
    if market.lower() not in {"cn", "a", "a_share", "china"}:
        raise ValueError(f"Unsupported market for snapshot: {market!r}")
    if source_priority is None:
        if config is None:
            from alphasift.config import Config
            config = Config.from_env()
        source_priority = list(config.snapshot_source_priority)
    return fetch_snapshot_with_fallback(
        source_priority,
        required_columns=required_columns,
    )
