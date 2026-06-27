# -*- coding: utf-8 -*-
"""
AlphaSift 完整测试脚本
在你的终端里运行:  python test_alphasift.py

逐步测试每个模块，告诉你哪里出了问题。
"""

import sys
import traceback

def header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)

def ok(msg):
    print(f"  [OK] {msg}")

def fail(msg, detail=""):
    print(f"  [FAIL] {msg}")
    if detail:
        print(f"         {detail}")

def warn(msg):
    print(f"  [WARN] {msg}")


# ============================================================
# Test 1: Python 环境
# ============================================================
header("Test 1: Python 环境")
print(f"  Python 版本: {sys.version}")
if sys.version_info >= (3, 9):
    ok("Python 版本满足要求 (>= 3.9)")
else:
    fail("Python 版本太低，需要 >= 3.9")


# ============================================================
# Test 2: 依赖包
# ============================================================
header("Test 2: 依赖包检查")

required = ["pandas", "requests"]
optional = ["efinance", "akshare", "litellm", "tushare"]

for pkg in required:
    try:
        mod = __import__(pkg)
        ver = getattr(mod, "__version__", "?")
        ok(f"{pkg} ({ver})")
    except ImportError:
        fail(f"{pkg} 未安装 — 运行: pip install {pkg}")

for pkg in optional:
    try:
        mod = __import__(pkg)
        ver = getattr(mod, "__version__", "?")
        ok(f"{pkg} ({ver}) — 可选")
    except ImportError:
        warn(f"{pkg} 未安装 — 运行: pip install {pkg}")


# ============================================================
# Test 3: 网络连接
# ============================================================
header("Test 3: 网络连接")

try:
    import requests
    resp = requests.get("https://www.baidu.com", timeout=10)
    if resp.status_code == 200:
        ok("baidu.com 连接正常")
    else:
        fail(f"baidu.com 返回状态码 {resp.status_code}")
except Exception as e:
    fail(f"网络连接失败: {e}")
    print("  -> 检查你的网络/VPN/代理设置")


# ============================================================
# Test 4: 东方财富 API (直接获取行情)
# ============================================================
header("Test 4: 东方财富 API (实时行情)")

try:
    import requests
    import json

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 10, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f2,f3,f12,f14",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Referer": "https://quote.eastmoney.com/",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    data = resp.json()

    if data.get("data") and data["data"].get("diff"):
        rows = data["data"]["diff"]
        ok(f"获取到 {len(rows)} 条实时行情")
        print("  示例:")
        for row in rows[:3]:
            code = row.get("f12", "?")
            name = row.get("f14", "?")
            price = row.get("f2", "?")
            change = row.get("f3", "?")
            print(f"    {code} {name}  价格={price}  涨跌={change}%")
    else:
        fail("东方财富 API 返回空数据")
        print(f"  响应: {json.dumps(data, ensure_ascii=False)[:200]}")

except Exception as e:
    fail(f"东方财富 API 调用失败: {e}")
    traceback.print_exc()


# ============================================================
# Test 5: efinance 数据源
# ============================================================
header("Test 5: efinance 数据源")

try:
    import efinance as ef
    df = ef.stock.get_realtime_quotes()
    if df is not None and not df.empty:
        ok(f"efinance 获取到 {len(df)} 条行情")
        print(f"  列名: {list(df.columns[:8])}")
    else:
        fail("efinance 返回空数据")
except ImportError:
    warn("efinance 未安装，跳过")
except Exception as e:
    fail(f"efinance 获取失败: {e}")


# ============================================================
# Test 6: akshare 数据源
# ============================================================
header("Test 6: akshare 数据源")

try:
    import akshare as ak
    df = ak.stock_zh_a_spot_em()
    if df is not None and not df.empty:
        ok(f"akshare 获取到 {len(df)} 条行情")
        print(f"  列名: {list(df.columns[:8])}")
    else:
        fail("akshare 返回空数据")
except ImportError:
    warn("akshare 未安装，跳过")
except Exception as e:
    fail(f"akshare 获取失败: {e}")


# ============================================================
# Test 7: DuckDuckGo 搜索
# ============================================================
header("Test 7: DuckDuckGo 搜索 (网络研究)")

try:
    import requests
    import re

    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    }
    resp = requests.post(url, data={"q": "A股 今日行情", "b": ""}, headers=headers, timeout=15)

    if resp.status_code == 200:
        # 简单检查是否有结果
        if "result__a" in resp.text or "result__snippet" in resp.text:
            ok("DuckDuckGo 搜索正常工作")
        else:
            warn("DuckDuckGo 返回了页面但可能没有结果 (可能被限制)")
    else:
        fail(f"DuckDuckGo 返回状态码 {resp.status_code}")

except Exception as e:
    fail(f"DuckDuckGo 搜索失败: {e}")


# ============================================================
# Test 8: 东方财富新闻搜索
# ============================================================
header("Test 8: 东方财富新闻搜索")

try:
    import requests
    import json
    import re

    url = "https://search-api-web.eastmoney.com/search/jsonp"
    params = {
        "cb": "jQuery_callback",
        "param": json.dumps({
            "uid": "",
            "keyword": "平安银行",
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 3,
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

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    match = re.search(r"jQuery_callback\((.*)\)", resp.text, re.DOTALL)
    if match:
        data = json.loads(match.group(1))
        articles = data.get("result", {}).get("cmsArticleWebOld", {}).get("list", [])
        if articles:
            ok(f"东方财富新闻搜索正常，找到 {len(articles)} 条新闻")
            for art in articles[:2]:
                title = re.sub(r"<[^>]+>", "", art.get("title", ""))
                print(f"    - {title}")
        else:
            warn("东方财富新闻搜索返回空结果")
    else:
        fail("东方财富新闻搜索返回格式异常")

except Exception as e:
    fail(f"东方财富新闻搜索失败: {e}")


# ============================================================
# Test 9: alphasift 模块导入
# ============================================================
header("Test 9: alphasift 模块导入")

try:
    # 需要先 pip install -e . 安装 alphasift
    from alphasift.snapshot import take_snapshot
    ok("alphasift.snapshot 导入成功")
except ImportError as e:
    fail(f"alphasift 模块导入失败: {e}")
    print("  -> 请先运行: cd alphasift && pip install -e .")

try:
    from alphasift.web_research import WebResearcher
    ok("alphasift.web_research 导入成功 (新模块)")
except ImportError as e:
    fail(f"web_research 模块导入失败: {e}")

try:
    from alphasift.data_manager import DataSourceManager
    ok("alphasift.data_manager 导入成功 (新模块)")
except ImportError as e:
    fail(f"data_manager 模块导入失败: {e}")

try:
    from alphasift.smart_researcher import SmartResearcher
    ok("alphasift.smart_researcher 导入成功 (新模块)")
except ImportError as e:
    fail(f"smart_researcher 模块导入失败: {e}")


# ============================================================
# Test 10: 完整流程测试 (不调用 LLM)
# ============================================================
header("Test 10: 完整流程测试")

try:
    from alphasift.pipeline import screen
    print("  正在运行 dual_low 策略 (--no-llm, max 5)...")
    result = screen("dual_low", market="cn", max_output=5, use_llm=False, post_analyzers=[])
    ok(f"选股成功！全市场 {result.snapshot_count} 只 -> 筛选后 {result.after_filter_count} 只 -> 输出 {len(result.picks)} 只")
    print(f"  数据源: {result.snapshot_source}")
    if result.picks:
        print("  Top 候选:")
        for pick in result.picks:
            print(f"    {pick.rank}. {pick.code} {pick.name}  分数={pick.final_score:.1f}")
except ImportError as e:
    fail(f"alphasift 未安装: {e}")
    print("  -> 运行: cd alphasift && pip install -e .")
except Exception as e:
    fail(f"选股流程失败: {e}")
    traceback.print_exc()


# ============================================================
# 总结
# ============================================================
header("测试完成")
print("""
下一步操作:

1. 如果依赖包缺失:
   pip install -e alphasift/
   pip install efinance akshare requests pandas

2. 运行诊断:
   python -m alphasift.cli diagnose

3. 测试原始选股:
   python -m alphasift.cli screen dual_low --no-llm --explain

4. 测试网络研究:
   python -m alphasift.cli research 000001 --name 平安银行

5. 测试智能选股:
   python -m alphasift.cli smart-screen --strategy dual_low -r "PE低于20的银行股" --explain
""")
