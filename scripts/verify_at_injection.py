#!/usr/bin/env python3
"""AT 注入验证脚本

验证 LY 信号 + ML 因子是否成功注入 AT Agent 的上下文。

用法:
  python scripts/verify_at_injection.py                    # 测试第一只股票
  python scripts/verify_at_injection.py --code 601801      # 指定股票
  python scripts/verify_at_injection.py --all              # 测试全部10只
  python scripts/verify_at_injection.py --dry-run          # 只检查数据文件不跑AT
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("verify_inject")


def check_data_files(stock_code: str) -> dict:
    """检查注入所需的数据文件是否存在且有效。"""
    realtime = Path("data/realtime")
    results = {}

    # LY signals
    ly_path = realtime / "ly_signal.json"
    if ly_path.exists():
        try:
            data = json.loads(ly_path.read_text())
            stocks = data.get("stocks", {})
            has_stock = stock_code in stocks
            results["ly_signal"] = {
                "exists": True,
                "has_stock": has_stock,
                "stock_data": stocks.get(stock_code, {}),
            }
        except Exception as e:
            results["ly_signal"] = {"exists": True, "error": str(e)}
    else:
        results["ly_signal"] = {"exists": False}

    # prob_up_log.csv
    csv_path = realtime / "prob_up_log.csv"
    if csv_path.exists():
        try:
            import csv
            with open(csv_path) as f:
                rows = list(csv.DictReader(f))
            results["prob_up_log"] = {
                "exists": True,
                "rows": len(rows),
                "latest_date": rows[-1].get("date", "?") if rows else "N/A",
            }
        except Exception as e:
            results["prob_up_log"] = {"exists": True, "error": str(e)}
    else:
        results["prob_up_log"] = {"exists": False}

    # ML factor signal
    mf_path = realtime / "ml_signal.json"
    if mf_path.exists():
        try:
            data = json.loads(mf_path.read_text())
            stocks = data.get("stocks", {})
            results["ml_signal"] = {
                "exists": True,
                "has_stock": stock_code in stocks,
            }
        except Exception as e:
            results["ml_signal"] = {"exists": True, "error": str(e)}
    else:
        results["ml_signal"] = {"exists": False}

    return results


def check_preloaded_context(stock_code: str) -> dict:
    """调用 _get_preloaded_context 验证 LY/ML 信号是否被正确加载。"""
    sys.path.insert(0, str(Path.cwd()))
    from src.mind_agent_wrapper import MindTradingAgentWrapper

    wrapper = MindTradingAgentWrapper(debug=True)
    wrapper._ensure_imported()

    ctx = wrapper._get_preloaded_context(stock_code)
    return {
        "ly_signals_context": ctx.get("ly_signals_context", ""),
        "ml_factor_context": ctx.get("ml_factor_context", ""),
        "market_context": (ctx.get("market_context", "")[:100] + "...") if ctx.get("market_context") else "(empty)",
        "fundamentals_context": (ctx.get("fundamentals_context", "")[:100] + "...") if ctx.get("fundamentals_context") else "(empty)",
    }


def main():
    parser = argparse.ArgumentParser(description="验证 AT 信号注入")
    parser.add_argument("--code", default="601801", help="股票代码 (默认: 601801)")
    parser.add_argument("--all", action="store_true", help="测试全部10只股票")
    parser.add_argument("--dry-run", action="store_true", help="只检查数据文件，不加载 AT")
    args = parser.parse_args()

    stock_pool = ["000592", "001390", "300652", "300676", "600372",
                  "601801", "603189", "603557", "605368", "688202"]

    codes = stock_pool if args.all else [args.code]

    print(f"{'='*60}")
    print(f"AT 注入验证 - 验证步骤 1: 数据文件检查")
    print(f"{'='*60}")

    all_ok = True
    for code in codes:
        print(f"\n--- {code} ---")
        files = check_data_files(code)
        for name, status in files.items():
            if status.get("exists"):
                ok = status.get("has_stock", True)
                extra = ""
                if "stock_data" in status and status["stock_data"]:
                    extra = f" | data: {json.dumps(status['stock_data'], ensure_ascii=False)[:80]}"
                if "rows" in status:
                    extra = f" | {status['rows']} rows, latest: {status.get('latest_date','')}"
                print(f"  ✅ {name}: 存在{extra}" if ok else f"  ❌ {name}: 存在但不含本股票")
                if not ok:
                    all_ok = False
            else:
                print(f"  ⚠️  {name}: 文件不存在")
        if not status.get("exists"):
            pass  # some files may legitimately not exist yet

    if args.dry_run:
        print(f"\n{'='*60}")
        print("DRY RUN: 数据文件检查完成，未加载 AT")
        print(f"{'='*60}")
        return 0 if all_ok else 1

    print(f"\n{'='*60}")
    print(f"AT 注入验证 - 验证步骤 2: 预加载上下文检查")
    print(f"{'='*60}")

    for code in codes[:2 if not args.all else len(codes)]:  # limit to 2 for speed
        print(f"\n--- {code} ---")
        try:
            ctx = check_preloaded_context(code)
            ly = ctx.get("ly_signals_context", "")
            mf = ctx.get("ml_factor_context", "")
            if ly:
                print(f"  ✅ LY 信号: 已加载 ({len(ly)} chars)")
                print(f"     前两行: {ly.split(chr(10))[0][:80]}")
            else:
                print(f"  ⚠️  LY 信号: 空（数据文件可能过时）")
            if mf:
                print(f"  ✅ ML 因子: 已加载 ({len(mf)} chars)")
                print(f"     内容: {mf[:80]}")
            else:
                print(f"  ⚠️  ML 因子: 空")
            print(f"  ✅ 行情: {ctx.get('market_context', '')[:60]}")
            print(f"  ✅ 基本面: {ctx.get('fundamentals_context', '')[:60]}")
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            import traceback
            traceback.print_exc()
            all_ok = False

    print(f"\n{'='*60}")
    if all_ok:
        print(f"✅ 验证完成: 所有检查通过")
    else:
        print(f"⚠️ 验证完成: 存在警告（某些数据文件可能尚未生成）")
    print(f"{'='*60}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
