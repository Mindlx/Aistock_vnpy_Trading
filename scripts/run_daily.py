#!/usr/bin/env python3
"""
每日融合决策执行脚本

用法:
    python scripts/run_daily.py                          # 正常执行
    python scripts/run_daily.py --dry-run                # 仅打印，不推送
    python scripts/run_daily.py --date 2026-05-29        # 回测指定日期
    python scripts/run_daily.py --mock                   # 使用模拟数据测试

数据流:
    1. 从三个系统读取每日输出
    2. 归一化各系统信号
    3. 线性积分融合（含分歧检测+置信度调制）
    4. 推送结果到企业微信
    5. 保存融合结果到日志/CSV/JSON

推荐 crontab: 30 16 * * 1-5 cd /path/to/fusion && python scripts/run_daily.py

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.data_loader import UnifiedDataLoader
from src.fusion_engine import FusionEngine
from src.logger import FusionLogger
from src.wecom_notifier import WeComNotifier


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="三系统融合决策 - 每日执行脚本",
        epilog="示例: python scripts/run_daily.py --mock",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅打印结果，不推送企业微信",
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="指定日期 (YYYY-MM-DD)，默认使用当前日期",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="使用模拟数据（不读取真实系统输出）",
    )
    parser.add_argument(
        "--run-ta", action="store_true",
        help="运行 mind_TradingAgent 分析（耗时较长，每只股票需 1-5 分钟含 LLM 推理）",
    )
    parser.add_argument(
        "--config", type=str, default="config/settings.yaml",
        help="配置文件路径",
    )
    parser.add_argument(
        "--stock-pool", type=str, default="config/stock_pool.csv",
        help="股票池 CSV 路径",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="输出目录（覆盖配置文件中的 fusion_output 路径）",
    )
    return parser.parse_args()


def load_stock_pool(stock_pool_path: str) -> List[Dict[str, str]]:
    """从 CSV 加载股票池"""
    stocks = []
    with open(stock_pool_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        # 跳过注释行
        for row in reader:
            if row.get("code", "").startswith("#"):
                continue
            stocks.append({
                "code": row.get("code", "").strip(),
                "name": row.get("name", "").strip(),
                "market": row.get("market", "").strip(),
            })
    return stocks


def generate_mock_data(stock_pool: List[Dict[str, str]], date: str) -> List[Dict[str, Any]]:
    """
    生成模拟数据进行测试。
    """
    import random
    random.seed(hash(date) % (2 ** 31))

    mock_data = []
    for stock in stock_pool:
        code = stock["code"]
        # lynx_vnpy 模拟
        lynx_prob = random.uniform(20, 85)
        if lynx_prob >= 65:
            lynx_signal = "🟢 买入"
        elif lynx_prob >= 55:
            lynx_signal = "🟢 关注"
        elif lynx_prob >= 45:
            lynx_signal = "⚪ 观望"
        elif lynx_prob >= 35:
            lynx_signal = "🟡 谨慎"
        else:
            lynx_signal = "🔴 回避"

        # MindLynx 模拟
        mindlynx_score = random.randint(20, 80)
        if mindlynx_score >= 60:
            mindlynx_advice = "持有"
        elif mindlynx_score >= 40:
            mindlynx_advice = "观望"
        else:
            mindlynx_advice = "卖出"

        # TradingAgent 模拟
        ta_ratings = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
        ta_weights = [0.15, 0.15, 0.4, 0.15, 0.15]
        ta_rating = random.choices(ta_ratings, weights=ta_weights, k=1)[0]

        mock_data.append({
            "code": code,
            "name": stock["name"],
            "lynx_signal": lynx_signal,
            "lynx_prob_up": round(lynx_prob, 1),
            "mindlynx_advice": mindlynx_advice,
            "mindlynx_score": mindlynx_score,
            "tradingagent_rating": ta_rating,
        })

    return mock_data


def load_real_data(
    stock_pool: List[Dict[str, str]],
    date: str,
    config: Dict[str, Any],
    stock_pool_path: str = "config/stock_pool.csv",
) -> List[Dict[str, Any]]:
    """
    从三个系统读取真实输出数据（零侵入版）。

    使用 UnifiedDataLoader 统一加载，不修改任何原有系统代码。
    - lynx_vnpy:    通过 Python import 直接调用 (sys.path)
    - MindLynx:     读取 reports/ 下的 Markdown 报告（不修改）
    - TradingAgent: 读取 ~/.mind_tradingagent/logs/ 下的 JSON 日志（不修改）

    任何一个系统不可用时，融合引擎会自动处理缺失数据。
    """
    loader = UnifiedDataLoader(
        lynx_root="systems/lynx_vnpy",
        mindlynx_reports="systems/MindLynx-Aistock/reports/",
        tradingagent_logs="~/.mind_tradingagent/logs/",
        stock_pool_path=stock_pool_path,
    )
    stock_signals = loader.load_all(date)

    if not stock_signals:
        print("⚠️  所有系统数据为空。各系统可能尚未运行或输出文件尚未生成。")
        print("   使用 --mock 模式测试融合引擎逻辑。")

    return stock_signals


def save_fusion_output(
    results: List[Dict[str, Any]],
    date: str,
    output_dir: str,
    config: Dict[str, Any],
):
    """保存融合结果到 JSON 和 CSV"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    fusion_config = config.get("fusion_output", {})

    # JSON 输出
    if fusion_config.get("save_daily_json", True):
        json_data = {
            "date": date,
            "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "total_stocks": len(results),
            "results": results,
        }
        json_path = output_path / f"fusion_{date}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"  JSON: {json_path}")

    # CSV 输出（简化版本）
    if fusion_config.get("save_daily_csv", True):
        csv_path = output_path / f"fusion_{date}.csv"
        fieldnames = [
            "stock_code", "stock_name", "valid",
            "lynx_score", "mindlynx_score", "tradingagent_score",
            "fusion_score", "signal", "signal_name", "position_advice",
            "is_degraded", "has_disagreement",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow({
                    k: r.get(k, "") for k in fieldnames
                })
        print(f"  CSV: {csv_path}")


def main():
    args = parse_args()

    # 加载配置
    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 加载股票池
    stock_pool = load_stock_pool(args.stock_pool)
    print(f"📋 股票池: {len(stock_pool)} 只")
    print(f"   {', '.join(s['code'] for s in stock_pool)}")

    # 确定日期
    tz_cn = timezone(timedelta(hours=8))
    today = args.date or datetime.now(tz_cn).strftime("%Y-%m-%d")
    print(f"📅 日期: {today}")

    # ── 可选: 运行 mind_TradingAgent 批量分析 ──
    if args.run_ta:
        print("\n🧠 运行 mind_TradingAgent 分析（LLM 推理，耗时较长）...")
        try:
            from src.mind_agent_wrapper import MindTradingAgentWrapper
            from src.mind_stock_config import DEFAULT_STOCK_CODES

            ta_wrapper = MindTradingAgentWrapper(debug=False)
            ta_results = ta_wrapper.run_batch_and_save(
                DEFAULT_STOCK_CODES, today,
                output_path="data/tradingagent/ta_signals_" + today.replace("-", "") + ".json",
            )
            success = sum(1 for r in ta_results if r.get("success"))
            print(f"  ✅ TradingAgent: {success}/{len(ta_results)} 只分析完成")
        except ImportError as e:
            print(f"  ⚠️  TradingAgent 导入失败: {e}")
            print(f"     确保已 clone mind_TradingAgent 并安装依赖")
        except Exception as e:
            print(f"  ⚠️  TradingAgent 执行异常: {e}")

    # 加载数据
    if args.mock:
        print("\n🔧 使用模拟数据模式")
        stock_signals = generate_mock_data(stock_pool, today)
    else:
        print("\n📡 加载真实系统数据（零侵入，不修改原系统）...")
        stock_signals = load_real_data(stock_pool, today, config, args.stock_pool)
        # 如果所有数据都是空的且没有模拟，提示用户
        all_data_empty = all(
            s.get("lynx_signal") in ("观望", "") and s.get("tradingagent_rating") == "Hold"
            for s in stock_signals
        )
        if all_data_empty:
            print("⚠ 所有系统数据为空。使用 --mock 参数运行模拟模式测试。")
            print("   或等待各系统输出版本就绪后再次运行。")

    print(f"\n🔄 开始融合分析 ({len(stock_signals)} 只股票)...")

    # 执行融合
    engine = FusionEngine(args.config)
    results = engine.fuse_stock_pool(stock_signals)

    # 输出结果
    print(f"\n{'='*55}")
    print(f"📊 融合决策结果")
    print(f"{'='*55}")

    summary = engine.get_portfolio_summary(results)
    print(f"\n有效: {summary['total_valid']}/{summary['total_results']}")
    print(f"降级: {summary['degraded_count']}")
    print(f"分歧: {summary['disagreement_count']}")
    print(f"分布: {summary['distribution']}")

    print(f"\n--- 个股结果 ---")
    for r in results:
        if not r.get("valid"):
            print(f"  ❌ {r['stock_code']}: {r.get('message', '无效')}")
            continue
        extra = ""
        if r.get("is_degraded"):
            extra += " ⚠降级"
        if r.get("has_disagreement"):
            extra += f" ⚡分歧(p={r.get('uncertainty_penalty', 0):.2f})"
        print(
            f"  {r['signal_name']} {r.get('stock_name', '')}({r['stock_code']}) "
            f"融合={r['fusion_score']:.2f} "
            f"lynx={r['lynx_score']:.2f} mind={r['mindlynx_score']:.2f} ta={r['tradingagent_score']:.2f}"
            f"{extra}"
        )

    # 保存结果
    output_dir = args.output or config.get("data_paths", {}).get(
        "fusion_output", "data/fusion_output"
    )
    save_fusion_output(results, today, output_dir, config)

    # 企业微信推送
    if not args.dry_run and config.get("wecom", {}).get("enabled", False):
        wecom_webhook = config["wecom"].get("webhook_url", "")
        if wecom_webhook and wecom_webhook != "YOUR_KEY_HERE":
            notifier = WeComNotifier(wecom_webhook, enabled=True)
            notifier.push_daily_decision(results, today)
        else:
            print("\n⚠️  企业微信 webhook 未配置，请先更新 config/settings.yaml")
    else:
        print("\nℹ️  企业微信推送跳过 (dry-run 或未配置)")

    print(f"\n✅ 融合完成")


if __name__ == "__main__":
    main()
