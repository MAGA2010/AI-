# -*- coding: utf-8 -*-
"""数据获取验证脚本 — 所有输出写入文件。"""

import sys
import os
import traceback

# 写入当前工作目录确认
outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_test_result.txt")

def w(msg):
    with open(outpath, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

# 清空文件
with open(outpath, "w", encoding="utf-8") as f:
    f.write("")

w(f"Python: {sys.version}")
w(f"Executable: {sys.executable}")
w(f"CWD: {os.getcwd()}")
w("")

# 1. 测试包导入
w("=" * 60)
w("1. 包导入测试")
w("=" * 60)

pkgs = {
    "pandas": None,
    "yaml": None,
    "requests": None,
    "numpy": None,
}

optional_pkgs = {
    "efinance": None,
    "akshare": None,
    "baostock": None,
    "tushare": None,
}

for pkg in pkgs:
    try:
        mod = __import__(pkg)
        ver = getattr(mod, "__version__", "ok")
        pkgs[pkg] = ver
        w(f"  ✅ {pkg}: {ver}")
    except ImportError as e:
        w(f"  ❌ {pkg}: NOT INSTALLED ({e})")

for pkg in optional_pkgs:
    try:
        mod = __import__(pkg)
        ver = getattr(mod, "__version__", "ok")
        optional_pkgs[pkg] = ver
        w(f"  ✅ {pkg}: {ver}")
    except ImportError:
        w(f"  ⚠️  {pkg}: 未安装（可选）")

w("")

# 2. 测试 alphasift 模块导入
w("=" * 60)
w("2. alphasift 模块导入测试")
w("=" * 60)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

modules_to_test = [
    "alphasift.models",
    "alphasift.track_analyzer",
    "alphasift.moat_scorer",
    "alphasift.scorer_v2",
    "alphasift.filter",
    "alphasift.risk",
    "alphasift.strategy",
    "alphasift.snapshot",
    "alphasift.pipeline",
]

for mod_name in modules_to_test:
    try:
        __import__(mod_name)
        w(f"  ✅ {mod_name}")
    except Exception as e:
        w(f"  ❌ {mod_name}: {e}")

w("")

# 3. 测试数据获取
w("=" * 60)
w("3. 数据获取测试")
w("=" * 60)

# 3a. efinance
if optional_pkgs.get("efinance"):
    w("\n--- efinance ---")
    try:
        import efinance as ef
        df = ef.stock.get_realtime_quotes()
        if df is not None and len(df) > 0:
            w(f"  ✅ 实时行情: {len(df)} 条")
            w(f"  列名: {list(df.columns)[:10]}")
            w(f"  前3行:")
            for _, row in df.head(3).iterrows():
                code = row.get("股票代码", row.get("code", ""))
                name = row.get("股票名称", row.get("name", ""))
                price = row.get("最新价", row.get("price", ""))
                w(f"    {code} {name} 最新价={price}")
        else:
            w("  ⚠️ 返回空数据")
    except Exception as e:
        w(f"  ❌ 错误: {e}")
        w(f"  {traceback.format_exc()[:500]}")

# 3b. akshare
if optional_pkgs.get("akshare"):
    w("\n--- akshare ---")
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is not None and len(df) > 0:
            w(f"  ✅ 实时行情: {len(df)} 条")
            w(f"  列名: {list(df.columns)[:10]}")
            w(f"  前3行:")
            for _, row in df.head(3).iterrows():
                code = row.get("代码", "")
                name = row.get("名称", "")
                price = row.get("最新价", "")
                w(f"    {code} {name} 最新价={price}")
        else:
            w("  ⚠️ 返回空数据")
    except Exception as e:
        w(f"  ❌ 错误: {e}")
        w(f"  {traceback.format_exc()[:500]}")

# 3c. alphasift snapshot
w("\n--- alphasift snapshot ---")
try:
    from alphasift.snapshot import fetch_snapshot_with_fallback
    df = fetch_snapshot_with_fallback(
        source_priority=["efinance", "akshare_em", "em_datacenter"],
        required_columns=["code", "name", "price"],
    )
    if df is not None and len(df) > 0:
        w(f"  ✅ 成功获取 {len(df)} 条数据")
        w(f"  来源: {df.attrs.get('snapshot_source', 'unknown')}")
        w(f"  列名: {list(df.columns)[:15]}")
        w(f"  前3行:")
        for _, row in df.head(3).iterrows():
            code = row.get("code", "")
            name = row.get("name", "")
            price = row.get("price", "")
            w(f"    {code} {name} price={price}")
    else:
        w("  ⚠️ 返回空数据")
except Exception as e:
    w(f"  ❌ 错误: {e}")
    w(f"  {traceback.format_exc()[:800]}")

w("")
w("=" * 60)
w("验证完成")
w("=" * 60)
