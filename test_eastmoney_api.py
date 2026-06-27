# -*- coding: utf-8 -*-
"""
快速测试：直接调用东方财富 API 获取实时行情
绕过 efinance/akshare，验证核心数据能不能拿到。
"""

import sys
print("=" * 50)
print("直接测试东方财富 API")
print("=" * 50)

# Step 1: 测试 requests
try:
    import requests
    print("[OK] requests 已安装")
except ImportError:
    print("[FAIL] requests 未安装，请运行: pip install requests")
    sys.exit(1)

# Step 2: 测试网络
try:
    resp = requests.get("https://www.baidu.com", timeout=10)
    print(f"[OK] 网络连接正常 (状态码: {resp.status_code})")
except Exception as e:
    print(f"[FAIL] 网络不通: {e}")
    sys.exit(1)

# Step 3: 直接调用东方财富 API
print("\n正在调用东方财富 API 获取实时行情...")
try:
    import json

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": 20,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "*/*",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=30)
    print(f"[INFO] HTTP 状态码: {resp.status_code}")
    print(f"[INFO] 响应长度: {len(resp.text)} 字符")

    data = resp.json()

    if data.get("data") and data["data"].get("diff"):
        rows = data["data"]["diff"]
        print(f"\n[OK] 获取到 {len(rows)} 条实时行情数据！")
        print()
        print(f"{'代码':<10}{'名称':<14}{'价格':<10}{'涨跌%':<10}{'成交额(万)':<14}")
        print("-" * 58)
        for row in rows[:15]:
            code = row.get("f12", "?")
            name = row.get("f14", "?")
            price = row.get("f2", "-")
            change = row.get("f3", "-")
            amount = row.get("f6", 0)
            if amount and amount != "-":
                try:
                    amount = f"{float(amount)/10000:.0f}"
                except:
                    amount = "-"
            else:
                amount = "-"
            print(f"{code:<10}{name:<14}{price:<10}{change:<10}{amount:<14}")

        print()
        print("=" * 50)
        print("[OK] 数据获取完全正常！")
        print("=" * 50)
        print()
        print("这意味着你的系统可以工作，只需要用新的数据管理器")
        print("替代原来的 efinance/akshare 调用。")
        print()
        print("下一步运行:")
        print("  python -m alphasift.cli diagnose")
        print("  python -m alphasift.cli screen dual_low --no-llm --explain")

    else:
        print("[FAIL] API 返回了空数据")
        print(f"响应内容: {json.dumps(data, ensure_ascii=False)[:500]}")

except requests.exceptions.ConnectionError as e:
    print(f"[FAIL] 连接被拒绝: {e}")
    print()
    print("可能原因:")
    print("  1. 东方财富 API 端点被你的网络环境屏蔽")
    print("  2. 需要设置代理")
    print("  3. 尝试使用 VPN")
except requests.exceptions.Timeout:
    print("[FAIL] 请求超时 (30秒)")
    print("网络太慢或服务器无响应")
except Exception as e:
    print(f"[FAIL] 未知错误: {e}")
    import traceback
    traceback.print_exc()
