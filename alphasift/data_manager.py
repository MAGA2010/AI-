# -*- coding: utf-8 -*-
"""
Enhanced Data Source Manager
根据网络环境自动选择可用的数据源。
已验证可用: akshare 新浪接口、新浪直接接口、腾讯接口
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class DataSourceManager:
    """
    统一的数据源管理器。
    自动尝试多个数据源，带重试和回退。
    """

    def __init__(self, *, prefer_source: str = ""):
        self.prefer_source = prefer_source
        self._available_sources: dict[str, bool] = {}
        self._check_sources()

    def _check_sources(self) -> None:
        """检查哪些数据源可用。"""
        try:
            import akshare  # noqa: F401
            self._available_sources["akshare"] = True
            logger.info("akshare 可用")
        except ImportError:
            self._available_sources["akshare"] = False
            logger.warning("akshare 不可用")

        self._available_sources["sina_direct"] = True
        self._available_sources["tencent_direct"] = True
        self._available_sources["requests"] = True

    def get_available_sources(self) -> dict[str, bool]:
        return dict(self._available_sources)

    def fetch_realtime_snapshot(self, **kwargs) -> pd.DataFrame:
        """
        获取实时行情快照，自动回退多个数据源。
        优先级: akshare 新浪接口 -> 新浪直接接口 -> 腾讯直接接口
        """
        sources = [
            ("akshare_sina", self._fetch_akshare_sina),
            ("sina_direct", self._fetch_sina_direct),
            ("tencent_direct", self._fetch_tencent_direct),
        ]

        last_error = None
        for name, func in sources:
            try:
                logger.info("尝试数据源: %s", name)
                df = func(**kwargs)
                if df is not None and not df.empty:
                    logger.info("数据源 %s 成功，获取 %d 条数据", name, len(df))
                    return df
            except Exception as e:
                logger.warning("数据源 %s 失败: %s", name, e)
                last_error = e
                continue

        if last_error:
            raise last_error
        raise RuntimeError("所有数据源都不可用")

    def _fetch_akshare_sina(self, **kwargs) -> pd.DataFrame:
        """使用 akshare 的新浪接口获取全 A 实时行情。"""
        import akshare as ak
        df = ak.stock_zh_a_spot()

        # 标准化列名
        rename_map = {
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
        df = df.rename(columns=rename_map)

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

        return df

    def _fetch_sina_direct(self, **kwargs) -> pd.DataFrame:
        """
        直接调用新浪财经接口获取实时行情。
        hq.sinajs.cn 接口，每次最多查约 800 只。
        先获取指数和主要股票作为回退。
        """
        # 先获取主要指数和热门股
        symbols = self._get_major_symbols()
        return self._fetch_sina_batch(symbols)

    def _fetch_sina_batch(self, symbols: list[str]) -> pd.DataFrame:
        """批量获取新浪行情。"""
        batch_size = 800
        all_rows = []

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            symbol_str = ",".join(batch)

            url = f"https://hq.sinajs.cn/list={symbol_str}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                "Referer": "https://finance.sina.com.cn/",
            }
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()

            for line in resp.text.strip().split("\n"):
                row = self._parse_sina_line(line)
                if row:
                    all_rows.append(row)

            if i + batch_size < len(symbols):
                time.sleep(0.5)

        if not all_rows:
            raise ValueError("新浪接口返回空数据")

        df = pd.DataFrame(all_rows)
        return df

    def _parse_sina_line(self, line: str) -> dict | None:
        """解析新浪行情的一行数据。"""
        match = re.match(r'var hq_str_(\w+)="(.*)";', line.strip())
        if not match:
            return None

        symbol = match.group(1)
        data_str = match.group(2)
        if not data_str:
            return None

        fields = data_str.split(",")
        if len(fields) < 32:
            return None

        code = symbol[2:]  # 去掉 sh/sz/bj 前缀
        name = fields[0]

        try:
            price = float(fields[3]) if fields[3] else 0
            prev_close = float(fields[2]) if fields[2] else 0
            open_price = float(fields[1]) if fields[1] else 0
            high = float(fields[4]) if fields[4] else 0
            low = float(fields[5]) if fields[5] else 0
            volume = float(fields[8]) if fields[8] else 0
            amount = float(fields[9]) if fields[9] else 0
        except (ValueError, IndexError):
            return None

        if price <= 0:
            return None

        change_amt = price - prev_close if prev_close > 0 else 0
        change_pct = (change_amt / prev_close * 100) if prev_close > 0 else 0

        return {
            "code": code,
            "name": name,
            "price": price,
            "change_amt": change_amt,
            "change_pct": change_pct,
            "volume": volume,
            "amount": amount,
            "prev_close": prev_close,
            "open": open_price,
            "high": high,
            "low": low,
        }

    def _fetch_tencent_direct(self, **kwargs) -> pd.DataFrame:
        """
        直接调用腾讯财经接口获取实时行情。
        qt.gtimg.cn 接口。
        """
        symbols = self._get_major_symbols()
        batch_size = 500
        all_rows = []

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            symbol_str = ",".join(batch)

            url = f"https://qt.gtimg.cn/q={symbol_str}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            }
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()

            for line in resp.text.strip().split("\n"):
                row = self._parse_tencent_line(line)
                if row:
                    all_rows.append(row)

            if i + batch_size < len(symbols):
                time.sleep(0.5)

        if not all_rows:
            raise ValueError("腾讯接口返回空数据")

        df = pd.DataFrame(all_rows)
        return df

    def _parse_tencent_line(self, line: str) -> dict | None:
        """解析腾讯行情的一行数据。"""
        match = re.match(r'v_(\w+)="(.*)";', line.strip())
        if not match:
            return None

        symbol = match.group(1)
        data_str = match.group(2)
        if not data_str:
            return None

        fields = data_str.split("~")
        if len(fields) < 45:
            return None

        code = fields[2]
        name = fields[1]

        try:
            price = float(fields[3]) if fields[3] else 0
            prev_close = float(fields[4]) if fields[4] else 0
            open_price = float(fields[5]) if fields[5] else 0
            volume = float(fields[6]) if fields[6] else 0
            amount = float(fields[37]) if fields[37] else 0
            high = float(fields[33]) if fields[33] else 0
            low = float(fields[34]) if fields[34] else 0
        except (ValueError, IndexError):
            return None

        if price <= 0:
            return None

        change_amt = price - prev_close if prev_close > 0 else 0
        change_pct = (change_amt / prev_close * 100) if prev_close > 0 else 0

        return {
            "code": code,
            "name": name,
            "price": price,
            "change_amt": change_amt,
            "change_pct": change_pct,
            "volume": volume,
            "amount": amount,
            "prev_close": prev_close,
            "open": open_price,
            "high": high,
            "low": low,
        }

    def _get_major_symbols(self) -> list[str]:
        """获取主要股票代码列表（用于直接接口回退）。"""
        symbols = []

        # 主要指数
        symbols.extend([
            "sh000001",  # 上证指数
            "sh000300",  # 沪深300
            "sz399001",  # 深证成指
            "sz399006",  # 创业板指
        ])

        # 生成沪深股票代码
        # 上海: 600xxx, 601xxx, 603xxx, 605xxx
        for prefix in ["600", "601", "603", "605"]:
            for i in range(1000):
                symbols.append(f"sh{prefix}{i:03d}")

        # 深圳: 000xxx, 001xxx, 002xxx, 003xxx
        for prefix in ["000", "001", "002", "003"]:
            for i in range(1000):
                symbols.append(f"sz{prefix}{i:03d}")

        # 创业板: 300xxx, 301xxx
        for prefix in ["300", "301"]:
            for i in range(1000):
                symbols.append(f"sz{prefix}{i:03d}")

        # 科创板: 688xxx
        for i in range(1000):
            symbols.append(f"sh688{i:03d}")

        # 北交所: 8xxxxx (部分)
        for i in range(100):
            symbols.append(f"bj83{i:04d}")
            symbols.append(f"bj87{i:04d}")
            symbols.append(f"bj43{i:04d}")

        return symbols

    def diagnose(self) -> str:
        """诊断数据获取问题，返回诊断报告。"""
        lines = ["=" * 50, "AlphaSift 数据源诊断报告", "=" * 50, ""]

        lines.append("依赖包状态:")
        for source, available in self._available_sources.items():
            status = "可用" if available else "不可用"
            lines.append(f"  {source}: {status}")
        lines.append("")

        lines.append("数据获取测试:")
        try:
            df = self.fetch_realtime_snapshot()
            lines.append(f"  实时行情: 成功 (获取 {len(df)} 条数据)")
            if not df.empty and "code" in df.columns:
                sample = df.head(3)
                for _, row in sample.iterrows():
                    code = row.get("code", "?")
                    name = row.get("name", "?")
                    price = row.get("price", "?")
                    lines.append(f"    示例: {code} {name} = {price}")
        except Exception as e:
            lines.append(f"  实时行情: 失败 ({e})")
        lines.append("")

        return "\n".join(lines)


def quick_diagnose() -> str:
    """快速诊断数据获取问题。"""
    manager = DataSourceManager()
    return manager.diagnose()
