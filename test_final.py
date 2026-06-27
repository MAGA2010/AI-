# -*- coding: utf-8 -*-
"""
最终测试：使用已验证可用的数据源
"""

import sys
import traceback

def header(text):
    print("\n" + "=" * 50)
    print(f"  {text}")
    print("=" * 50)

def ok(msg):
    print(f"  [OK] {msg}")

def fail(msg):
    print(f"  [FAIL] {msg}")


# ============================================================
header("Test 1: akshare 新浪行情 (核心数据源)")
# ============================================================
try:
    import akshare as ak
    df = ak.stock_zh_a_spot()
    ok(f"获取到 {len(df)} 条实时行情")
    print(f"  列名: {list(df.columns[:6])}")
    sample = df.head(3)
    for _, row in sample.iterrows():
        print(f"  {row['代码']} {row['名称']} = {row['最新价']}")
except Exception as e:
    fail(f"{e}")


# ============================================================
header("Test 2: akshare 新闻")
# ============================================================
try:
    import akshare as ak
    df = ak.stock_news_em(symbol="000001")
    ok(f"获取到 {len(df)} 条新闻")
    print(f"  列名: {list(df.columns)}")
except Exception as e:
    fail(f"{e}")


# ============================================================
header("Test 3: alphasift 完整流程")
# ============================================================
try:
    from alphasift.pipeline import screen
    print("  运行 dual_low 策略 (--no-llm)...")
    result = screen("dual_low", market="cn", max_output=5, use_llm=False, post_analyzers=[])
    ok(f"选股成功！ {result.snapshot_count} 只 -> {result.after_filter_count} 只 -> {len(result.picks)} 只")
    print(f"  数据源: {result.snapshot_source}")
    for pick in result.picks:
        print(f"    {pick.rank}. {pick.code} {pick.name}  分数={pick.final_score:.1f}")
except Exception as e:
    fail(f"{e}")
    traceback.print_exc()


# ============================================================
header("Test 4: 网络研究 (WebResearcher)")
# ============================================================
try:
    from alphasift.web_research import WebResearcher
    researcher = WebResearcher()
    research = researcher.research_stock("000001", "平安银行")
    context = research.to_context_text(max_chars=1000)
    if context:
        ok("网络研究成功")
        print(context[:500])
    else:
        print("  [WARN] 未找到研究结果")
except Exception as e:
    fail(f"{e}")
    traceback.print_exc()


# ============================================================
header("Test 5: 智能选股 (SmartResearcher)")
# ============================================================
try:
    from alphasift.smart_researcher import SmartResearcher, SmartScreenRequest
    researcher = SmartResearcher()
    request = SmartScreenRequest(
        strategy_name="dual_low",
        max_candidates=5,
        max_research_stocks=3,
    )
    result = researcher.smart_screen(request)
    if result.error:
        fail(f"错误: {result.error}")
    else:
        ok(f"智能选股成功！ {result.snapshot_count} 只 -> {result.filtered_count} 只 -> 研究 {result.researched_count} 只")
        for pick in result.picks[:5]:
            print(f"    {pick['rank']}. {pick['code']} {pick['name']}")
except Exception as e:
    fail(f"{e}")
    traceback.print_exc()


# ============================================================
header("完成")
# ============================================================
print("""
如果所有测试都通过，你可以使用以下命令:

1. 诊断:   python -m alphasift.cli diagnose
2. 选股:   python -m alphasift.cli screen dual_low --no-llm --explain
3. 研究:   python -m alphasift.cli research 000001 --name 平安银行
4. 智能:   python -m alphasift.cli smart-screen --strategy dual_low -r "PE低于20的银行股" --explain
""")
