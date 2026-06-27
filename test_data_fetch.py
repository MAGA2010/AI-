# -*- coding: utf-8 -*-
"""数据获取验证脚本 — 测试各数据源是否可用。"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """测试必要的包是否已安装。"""
    results = {}
    
    # 测试 pandas
    try:
        import pandas as pd
        results["pandas"] = f"✅ {pd.__version__}"
    except ImportError:
        results["pandas"] = "❌ 未安装"
    
    # 测试 pyyaml
    try:
        import yaml
        results["pyyaml"] = "✅ 已安装"
    except ImportError:
        results["pyyaml"] = "❌ 未安装"
    
    # 测试 efinance
    try:
        import efinance as ef
        results["efinance"] = "✅ 已安装"
    except ImportError:
        results["efinance"] = "❌ 未安装"
    
    # 测试 akshare
    try:
        import akshare as ak
        results["akshare"] = f"✅ {ak.__version__}"
    except ImportError:
        results["akshare"] = "❌ 未安装"
    
    # 测试 baostock
    try:
        import baostock as bs
        results["baostock"] = "✅ 已安装"
    except ImportError:
        results["baostock"] = "❌ 未安装"
    
    # 测试 tushare
    try:
        import tushare as ts
        results["tushare"] = f"✅ {ts.__version__}"
    except ImportError:
        results["tushare"] = "❌ 未安装"
    
    # 测试 requests
    try:
        import requests
        results["requests"] = f"✅ {requests.__version__}"
    except ImportError:
        results["requests"] = "❌ 未安装"
    
    return results


def test_efinance():
    """测试 efinance 数据获取。"""
    try:
        import efinance as ef
        # 获取实时行情
        df = ef.stock.get_quote_history("000001", klt=101, beg="20260101", end="20260603")
        if df is not None and len(df) > 0:
            return f"✅ 成功获取 {len(df)} 条数据"
        else:
            return "⚠️ 返回空数据"
    except Exception as e:
        return f"❌ 错误: {str(e)[:100]}"


def test_akshare():
    """测试 akshare 数据获取。"""
    try:
        import akshare as ak
        # 获取实时行情
        df = ak.stock_zh_a_spot_em()
        if df is not None and len(df) > 0:
            return f"✅ 成功获取 {len(df)} 条数据"
        else:
            return "⚠️ 返回空数据"
    except Exception as e:
        return f"❌ 错误: {str(e)[:100]}"


def test_baostock():
    """测试 baostock 数据获取。"""
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            return f"❌ 登录失败: {lg.error_msg}"
        
        rs = bs.query_history_k_data_plus(
            "sh.000001",
            "date,code,open,high,low,close,volume",
            start_date="2026-01-01",
            end_date="2026-06-03",
            frequency="d"
        )
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        
        bs.logout()
        
        if len(data03"
        )
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        
        bs.logout()
        
        if len(data_list) > 0:
            return f"✅ 成功获取 {len(data_list)} 条数据"
        else:
            return "⚠️ 返回空数据"
    except Exception as e:
        return f"❌ 错误: {str(e)[:100]}"


def test_alphasift_snapshot():
    """测试 alphasift 内置的数据获取。"""
    try:
        from alphasift.snapshot import fetch_snapshot_with_fallback
        df = fetch_snapshot_with_fallback(
            source_priority=["efinance", "akshare", "baostock"],
            required_columns=["code", "name", "price"]
        )
        if df is not None and len(df) > 0:
            return f"✅ 成功获取 {len(df)} 条数据，来源: {df.attrs.get('snapshot_source', 'unknown')}"
        else:
            return "⚠️ 返回空数据"
    except Exception as e:
        return f"❌ 错误: {str(e)[:100]}"


if __name__ == "__main__":
    print("=" * 60)
    print("数据获取验证")
    print("=" * 60)
    
    # 测试导入
    print("\n1. 测试包导入:")
    imports = test_imports()
    for pkg, status in imports.items():
        print(f"   {pkg}: {status}")
    
    # 测试数据获取
    print("\n2. 测试数据获取:")
    
    # 只测试已安装的包
    if imports.get("efinance", "").startswith("✅"):
        print(f"   efinance: {test_efinance()}")
    
    if imports.get("akshare", "").startswith("✅"):
        print(f"   akshare: {test_akshare()}")
    
    if imports.get("baostock", "").startswith("✅"):
        print(f"   baostock: {test_baostock()}")
    
    # 测试 alphasift 内置获取
    print("\n3. 测试 alphasift 数据获取:")
    print(f"   {test_alphasift_snapshot()}")
    
    print("\n" + "=" * 60)
