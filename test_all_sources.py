# -*- coding: utf-8 -*-
"""
多数据源探测：找出哪条路能通
"""

import sys
import time

def try_it(name, func):
    print(f"\n{'='*50}")
    print(f"测试: {name}")
    print(f"{'='*50}")
    try:
        result = func()
        if result:
            print(f"[OK] {name} 可用!")
            return True
        else:
            print(f"[FAIL] {name} 返回空数据")
            return False
    except Exception as e:
        err = str(e)[:120]
        print(f"[FAIL] {name}: {err}")
        return False

results = {}

# ============================================================
# 1. akshare 的其他接口（有很多不同的数据源）
# ============================================================
def test_akshare_sina():
    """akshare 用新浪接口获取行情"""
    import akshare as ak
    df = ak.stock_zh_a_spot()
    if df is not None and len(df) > 0:
        print(f"  获取到 {len(df)} 条数据")
        print(f"  示例: {df.head(3).to_string(index=False)}")
        return True
    return False

def test_akshare_em_hist():
    """akshare 获取单只股票历史数据"""
    import akshare as ak
    df = ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20260501", end_date="20260603", adjust="qfq")
    if df is not None and len(df) > 0:
        print(f"  获取到 {len(df)} 条历史数据")
        print(f"  最新: {df.tail(1).to_string(index=False)}")
        return True
    return False

def test_akshare_news():
    """akshare 获取财经新闻"""
    import akshare as ak
    df = ak.stock_news_em(symbol="000001")
    if df is not None and len(df) > 0:
        print(f"  获取到 {len(df)} 条新闻")
        for _, row in df.head(3).iterrows():
            print(f"    - {row.iloc[0]}")
        return True
    return False

results["akshare 新浪行情"] = try_it("akshare 新浪接口 (stock_zh_a_spot)", test_akshare_sina)
results["akshare 历史数据"] = try_it("akshare 东方财富历史 (stock_zh_a_hist)", test_akshare_em_hist)
results["akshare 新闻"] = try_it("akshare 新闻 (stock_news_em)", test_akshare_news)

# ============================================================
# 2. Tushare（需要 token，但端点不同）
# ============================================================
def test_tushare():
    import tushare as ts
    token = "YOUR_TOKEN"  # 需要用户填入
    ts.set_token(token)
    pro = ts.pro_api()
    df = pro.daily(ts_code='000001.SZ', start_date='20260601', end_date='20260603')
    if df is not None and len(df) > 0:
        print(f"  获取到 {len(df)} 条数据")
        return True
    return False

# 跳过 tushare，需要 token
print(f"\n{'='*50}")
print(f"跳过: Tushare (需要 API token)")
print(f"{'='*50}")

# ============================================================
# 3. Yahoo Finance（国际站，大概率不被封）
# ============================================================
def test_yahoo():
    import requests
    url = "https://query1.finance.yahoo.com/v8/finance/chart/000001.SZ"
    params = {"interval": "1d", "range": "5d"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    data = resp.json()
    if "chart" in data and data["chart"].get("result"):
        print(f"  Yahoo Finance 可访问!")
        return True
    return False

results["Yahoo Finance"] = try_it("Yahoo Finance (国际站)", test_yahoo)

# ============================================================
# 4. 新浪财经直接接口
# ============================================================
def test_sina_direct():
    import requests
    # 新浪实时行情接口
    url = "https://hq.sinajs.cn/list=sh000001,sz000001"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Referer": "https://finance.sina.com.cn/",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 200 and len(resp.text) > 50:
        print(f"  新浪行情接口返回数据:")
        lines = resp.text.strip().split("\n")
        for line in lines[:2]:
            print(f"    {line[:80]}...")
        return True
    return False

results["新浪直接接口"] = try_it("新浪财经直接接口 (hq.sinajs.cn)", test_sina_direct)

# ============================================================
# 5. 腾讯财经接口
# ============================================================
def test_tencent():
    import requests
    url = "https://qt.gtimg.cn/q=sz000001,sh000001"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 200 and len(resp.text) > 50:
        print(f"  腾讯行情接口返回数据:")
        lines = resp.text.strip().split("\n")
        for line in lines[:2]:
            print(f"    {line[:80]}...")
        return True
    return False

results["腾讯接口"] = try_it("腾讯财经接口 (qt.gtimg.cn)", test_tencent)

# ============================================================
# 6. DuckDuckGo 搜索（之前测试过应该能用）
# ============================================================
def test_ddg():
    import requests
    url = "https://html.duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    resp = requests.post(url, data={"q": "平安银行 股价", "b": ""}, headers=headers, timeout=15)
    if resp.status_code == 200 and "result" in resp.text.lower():
        print(f"  DuckDuckGo 搜索正常")
        return True
    return False

results["DuckDuckGo"] = try_it("DuckDuckGo 搜索", test_ddg)

# ============================================================
# 7. Google Finance（如果 Google 能访问）
# ============================================================
def test_google():
    import requests
    url = "https://www.google.com/finance/quote/000001:SHE"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 200:
        print(f"  Google Finance 可访问 (状态码: {resp.status_code})")
        return True
    return False

results["Google Finance"] = try_it("Google Finance", test_google)

# ============================================================
# 总结
# ============================================================
print(f"\n{'='*50}")
print("总结")
print(f"{'='*50}")

ok_sources = [name for name, ok in results.items() if ok]
fail_sources = [name for name, ok in results.items() if not ok]

if ok_sources:
    print(f"\n可用的数据源 ({len(ok_sources)}):")
    for name in ok_sources:
        print(f"  [OK] {name}")
else:
    print("\n所有数据源都失败了！")

if fail_sources:
    print(f"\n不可用的数据源 ({len(fail_sources)}):")
    for name in fail_sources:
        print(f"  [FAIL] {name}")

if ok_sources:
    print(f"\n建议使用: {ok_sources[0]}")
    print("我将更新代码使用可用的数据源。")
else:
    print("\n所有途径都不通，可能需要:")
    print("  1. 设置代理/VPN")
    print("  2. 使用 Tushare (需要注册获取 token)")
    print("  3. 从交易软件导出 CSV 数据")
