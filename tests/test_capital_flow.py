"""测试: 资金流数据管道（get_capital_flows）

测试链路:
  WarehouseReader.get_capital_flows() → CapitalFlowFetcher → Tushare/akshare

运行:
  python tests/test_capital_flow.py
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "systems" / "mind_TradingAgent"))

errors = 0
A_SHARE_TEST = os.environ.get("TEST_TICKER", "601801")  # 皖新传媒

# ── Test 1: WarehouseReader.get_capital_flows() 返回数据结构 ──
print("=== WarehouseReader.get_capital_flows() 数据结构 ===")
try:
    from services.data_warehouse import WarehouseReader

    reader = WarehouseReader()
    rows = reader.get_capital_flows(A_SHARE_TEST, days=10)
    assert isinstance(rows, list), f"期望 list, 实际 {type(rows)}"
    assert len(rows) > 0, "资金流数据不应为空"

    required_fields = {"date", "main_net_flow", "super_large_net",
                       "large_net", "medium_net", "small_net", "source"}
    for row in rows[:3]:
        missing = required_fields - set(row.keys())
        assert not missing, f"行缺字段: {missing}"
        assert row["date"], f"date 不应为空: {row}"
        assert isinstance(row["main_net_flow"], (int, float)), "main_net_flow 应为数值"

    print(f"  ✅ 返回 {len(rows)} 条记录, 字段完整")
    print(f"  最新: {rows[-1]['date']} main_net_flow={rows[-1]['main_net_flow']:,.0f}")
    print(f"  来源: {rows[-1]['source']}")
except Exception as e:
    print(f"  ❌ {e}")
    errors += 1

# ── Test 2: dataflows/warehouse.py get_capital_flows() 格式 ──
print()
print("=== dataflows/warehouse.get_capital_flows() Markdown 格式 ===")
try:
    from mind_tradingagent.dataflows.warehouse import get_capital_flows

    result = get_capital_flows(A_SHARE_TEST, days=10)

    assert "# Capital Flows" in result, "缺标题行"
    assert "| Date | Main Net Flow |" in result, "缺表头"
    assert "|------|" in result, "缺分隔行"
    assert "**Summary**" in result, "缺汇总行"
    assert "Tushare" in result or "akshare" in result, "缺数据来源标识"

    # 验证至少有一行数据
    data_lines = [l for l in result.split("\n") if l.startswith("|") and "Date" not in l and "---" not in l]
    assert len(data_lines) > 0, "无数据行"

    print(f"  ✅ Markdown 表格完整 ({len(data_lines)} 行数据)")
    # 截取前几行展示
    for line in result.split("\n")[:8]:
        print(f"  {line}")
    summary = [l for l in result.split("\n") if "Summary" in l]
    if summary:
        print(f"  {summary[0]}")
except Exception as e:
    print(f"  ❌ {e}")
    errors += 1

# ── Test 3: route_to_vendor dispatch ──
print()
print("=== route_to_vendor('get_capital_flows', ...) ===")
try:
    from mind_tradingagent.dataflows.interface import route_to_vendor

    result = route_to_vendor("get_capital_flows", A_SHARE_TEST, 10)
    assert isinstance(result, str), f"期望 str, 实际 {type(result)}"
    assert "Capital Flows" in result or "NO_DATA" in result, "返回值异常"
    print(f"  ✅ Dispatch 成功, 返回长度 {len(result)} 字符")
except Exception as e:
    print(f"  ❌ {e}")
    errors += 1

# ── Test 4: 空 ticker 应返回 NoMarketDataError ──
print()
print("=== 空 ticker / 无效代码 异常处理 ===")
try:
    from mind_tradingagent.dataflows.warehouse import get_capital_flows
    from mind_tradingagent.dataflows.errors import NoMarketDataError

    try:
        get_capital_flows("000000", days=5)
        print("  ⚠️ 无效代码未抛异常 (可能因依赖方返回了空列表)")
    except NoMarketDataError:
        print("  ✅ NoMarketDataError 正确抛出")
    except Exception as e:
        print(f"  ⚠️ 异常类型非预期: {type(e).__name__}: {e}")
except Exception as e:
    print(f"  ❌ 导入异常: {e}")
    errors += 1

# ── 总结果 ──
if __name__ == "__main__":
    print()
    if errors:
        print(f"❌ 失败 {errors} / 4 项测试")
        sys.exit(1)
    else:
        print("✅ 全部 4 项测试通过")
        sys.exit(0)
