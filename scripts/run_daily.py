#!/usr/bin/env python3
"""
每日融合决策执行脚本

用法:
    python scripts/run_daily.py                          # 正常执行
    python scripts/run_daily.py --dry-run                # 仅打印，不推送
    python scripts/run_daily.py --date 2026-05-29        # 回测指定日期
    python scripts/run_daily.py --mock                   # 使用模拟数据测试
    python scripts/run_daily.py --schedule               # 定时模式，每日 16:30 自动执行
    python scripts/run_daily.py --schedule --time 16:00  # 自定义时间

数据流:
    1. 从三个系统读取每日输出
    2. 归一化各系统信号
    3. 线性积分融合（含分歧检测+置信度调制）
    4. 推送结果到企业微信
    5. 保存融合结果到日志/CSV/JSON

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
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
        "--schedule", action="store_true",
        help="定时模式，每日指定时间自动执行融合分析",
    )
    parser.add_argument(
        "--time", type=str, default="16:30",
        help="定时执行时间，格式 HH:MM（默认 16:30）",
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
        "--mode", type=str, default=None,
        choices=["linear", "bayesian", "dual"],
        help="融合模式（覆盖 settings.yaml 中的 fusion_mode）",
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
            "price": round(random.uniform(5, 100), 2),
            "pct_chg": round(random.uniform(-5.5, 5.5), 2),
            "volume_ratio": round(random.uniform(0.3, 2.5), 2),
            "ma5": round(random.uniform(5, 100), 2),
            "ma10": round(random.uniform(5, 100), 2),
            "ma20": round(random.uniform(5, 100), 2),
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
    从三个子系统读取真实输出数据。

    数据来源（均位于本项目内部）:
    - lynx_vnpy:    systems/lynx_vnpy/ — 直接 import 调用 (sys.path)
    - MindLynx:     systems/MindLynx-Aistock/reports/ — 解析 Markdown 报告
    - TradingAgent: ~/.mind_tradingagent/logs/ — TA 自身输出的 JSON 日志
    """
    loader = UnifiedDataLoader(
        lynx_root="systems/lynx_vnpy",
        mindlynx_reports="systems/MindLynx-Aistock/reports/",
        tradingagent_logs="~/.mind_tradingagent/logs/",
        stock_pool_path=stock_pool_path,
    )
    stock_signals = loader.load_all(date)

    # TA 当日日志不存在时，自动使用前一日数据（标记 stale）
    _supply_ta_stale_data(stock_signals, date)

    if not stock_signals:
        print("⚠️  所有系统数据为空。各系统可能尚未运行或输出文件尚未生成。")
        print("   使用 --mock 模式测试融合引擎逻辑。")

    return stock_signals


def _supply_ta_stale_data(
    stock_signals: List[Dict[str, Any]], date: str,
) -> None:
    """补充 TA 数据：当日日志不存在时，尝试用昨日数据（标记为 stale）"""
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    for offset in range(1, 10):  # 最多回退9天（覆盖周末+长假）
        prev_date = (date_obj - timedelta(days=offset)).strftime("%Y-%m-%d")
        need_ta = [s for s in stock_signals
                   if not s.get("tradingagent_rating") or s["tradingagent_rating"] in ("", "Hold")]
        if not need_ta:
            return
        from src.data_loader import TradingAgentDataLoader
        ta_loader = TradingAgentDataLoader()
        for s in need_ta:
            code = s["code"]
            result = ta_loader.load_by_stock_and_date(code, prev_date)
            if result and result.get("rating"):
                s["tradingagent_rating"] = result["rating"]
                s["ta_debate_state"] = result.get("debate_state", {})
                s["ta_is_stale"] = True
                s["tradingagent_valid"] = True
                print(f"  ⏳ TA [{code}] 使用 {prev_date} 数据 (stale, rating={result['rating']})")
        if any(s.get("ta_is_stale") for s in need_ta):
            break


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
            "lynx_score", "lynx_valid",
            "mindlynx_score", "mindlynx_valid",
            "mindlynx_sentiment", "mindlynx_trend", "mindlynx_operation",
            "ml_trend_score", "ml_risk_alert_count",
            "tradingagent_score", "tradingagent_valid",
            "fusion_score", "signal", "signal_name", "position_advice",
            "is_degraded", "has_disagreement", "ta_is_stale",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow({
                    k: r.get(k, "") for k in fieldnames
                })
        print(f"  CSV: {csv_path}")

    # ── 写入 ly_signal.json（准实时文件交换区） ──
    write_ly_signal(results, date)


def write_ly_signal(results: List[Dict[str, Any]], date: str):
    """将 ly 信号写入准实时文件交换区"""
    ly_data = {"stocks": {}, "updated_at": date, "source": "ly_daily_fusion"}
    for r in results:
        if r.get("valid"):
            code = r["stock_code"]
            ly_data["stocks"][code] = {
                "score": r.get("lynx_score", 0),
                "signal": r.get("signal_name", ""),
            }
    # 保护：无有效ly数据时不覆盖已有文件，防止TA定时器(13:00)在lynx-signal(15:15)之前清空数据
    if not ly_data["stocks"]:
        print("  ⚠️ 无有效ly数据，保留现有ly_signal.json")
        return
    rt_dir = Path("data/realtime")
    rt_dir.mkdir(parents=True, exist_ok=True)
    tmp = rt_dir / "ly_signal.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ly_data, f, ensure_ascii=False)
    os.rename(tmp, rt_dir / "ly_signal.json")
    print(f"  📡 ly_signal.json 已写入 (准实时文件交换区)")


def write_at_signal(ta_results: List[Dict[str, Any]], date: str):
    """将 TA 评级写入准实时文件交换区"""
    at_data = {"stocks": {}, "updated_at": date, "source": "ta_run"}
    for r in ta_results:
        if r.get("success") and r.get("rating"):
            code = r.get("code", "")
            if code:
                from src.normalizer import SignalNormalizer
                score = SignalNormalizer.normalize_tradingagent(r["rating"])
                at_data["stocks"][code] = {
                    "rating": r["rating"],
                    "score": score,
                }
    rt_dir = Path("data/realtime")
    rt_dir.mkdir(parents=True, exist_ok=True)
    tmp = rt_dir / "at_signal.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(at_data, f, ensure_ascii=False)
    os.rename(tmp, rt_dir / "at_signal.json")
    print(f"  📡 at_signal.json 已写入 (准实时文件交换区)")


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
            # 写入准实时信号文件
            write_at_signal(ta_results, today)
            # TA 已跑完，后续融合直接用结果，不需要回填 stale 数据
            # 注: 融合的 load_real_data 会从 TA 日志文件读取 propagate() 输出，
            # 快速降级路径不写日志，需在 data loading 后注入结果
            _ta_results_cache = ta_results
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
        # 如果 --run-ta 使用了快速降级，注入 TA 结果到 stock_signals
        if args.run_ta and '_ta_results_cache' in dir() and _ta_results_cache:
            for s in stock_signals:
                for r in _ta_results_cache:
                    if s.get("code") == r.get("code") and r.get("rating"):
                        s["tradingagent_rating"] = r["rating"]
                        s["ta_is_stale"] = False
                        s["tradingagent_valid"] = True
                        s["ta_debate_state"] = {}
        # 如果所有数据都是空的且没有模拟，提示用户
        all_data_empty = all(
            s.get("lynx_signal") in ("观望", "") and s.get("tradingagent_rating") == "Hold"
            for s in stock_signals
        )
        if all_data_empty:
            print("⚠ 所有系统数据为空。使用 --mock 参数运行模拟模式测试。")
            print("   或等待各系统输出版本就绪后再次运行。")

    print(f"\n🔄 开始融合分析 ({len(stock_signals)} 只股票)...")

    # 判断 TA 数据是否过期（15:30 融合时 TA 尚未运行）
    ta_is_stale = True
    ta_today_path = Path(f"data/tradingagent/ta_signals_{today.replace('-', '')}.json")
    if ta_today_path.exists():
        ta_is_stale = False
    elif args.run_ta:
        ta_is_stale = False  # 正在运行 TA，即将有新鲜数据

    if ta_is_stale:
        print("  ⏳ TradingAgent 数据为昨日结果（TA 定时器 16:00 运行）")

    # 执行融合（支持模式覆盖）
    engine = FusionEngine(args.config)
    if args.mode:
        engine.fusion_mode = args.mode
        print(f"  🔄 融合模式: {args.mode} (覆盖配置)")
    results = engine.fuse_stock_pool(stock_signals, ta_is_stale=ta_is_stale)

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

    # 回测: 记录预测到回测数据库 (融合完成后自动执行)
    if not args.dry_run:
        try:
            import subprocess
            bt_result = subprocess.run(
                [sys.executable, "scripts/backtest.py", "update", "--date", today],
                capture_output=True, text=True, timeout=30,
            )
            if bt_result.returncode == 0:
                # 只打印非空行 (避免刷屏)
                lines = [l for l in bt_result.stdout.split("\n") if l.strip()]
                for line in lines:
                    print(f"  [回测] {line}")
            else:
                print(f"  [回测] ❌ 失败: {bt_result.stderr[:200]}")
        except Exception as e:
            print(f"  [回测] ⚠️ 异常: {e}")

    # 企业微信推送
    if not args.dry_run and config.get("wecom", {}).get("enabled", False):
        # 优先读取环境变量（项目根 .env），兼容旧版 yaml 配置
        wecom_webhook = os.getenv("WECOM_WEBHOOK_URL") or config["wecom"].get("webhook_url", "")
        if wecom_webhook and wecom_webhook != "YOUR_KEY_HERE":
            notifier = WeComNotifier(wecom_webhook, enabled=True)

            # 收集可选功能附加推送（零侵入，失败不影响主流程）
            extra_sections = []
            fc = config.get("features", {})
            try:
                from src.feature_bridge import run_dragon_tiger

                if fc.get("dragon_tiger", {}).get("enabled"):
                    codes = [s["code"] for s in stock_pool]
                    dt = run_dragon_tiger(codes, fc["dragon_tiger"].get("top_n", 10))
                    if dt:
                        extra_sections.append(f"🐉 龙虎榜 ({today})\n{dt}")

                if fc.get("xueqiu", {}).get("enabled"):
                    extra_sections.append("💰 **东方财富自选股评级报告已生成** 📎 完整报告见附件PDF")
            except Exception:
                pass

            notifier.push_daily_decision(results, today, extra_sections=extra_sections or None)

            # ── 东方财富评级 PDF 报告（独立推送，直接在 MindLynx venv 中执行）──
            if fc.get("xueqiu", {}).get("enabled") and not args.dry_run:
                _root = Path(__file__).resolve().parent.parent
                _venv = _root / "systems/MindLynx-Aistock/.venv/bin/python"
                _script = _root / "systems/MindLynx-Aistock/scripts/generate_rating_report.py"
                try:
                    subprocess.run([str(_venv), str(_script)], timeout=300)
                except Exception as e:
                    print(f"  ⚠️ 东方财富评级 PDF 推送异常: {e}")
        else:
            print("\n⚠️  企业微信 webhook 未配置，请先更新 config/settings.yaml")
    else:
        print("\nℹ️  企业微信推送跳过 (dry-run 或未配置)")

    print(f'\n✅ 融合完成')


def _schedule_loop(schedule_time: str):
    """定时调度模式：交易日每天 schedule_time 执行一次"""
    try:
        import schedule
    except ImportError:
        print('请安装 schedule 库: pip install schedule')
        return 1

    schedule.every().monday.at(schedule_time).do(main)
    schedule.every().tuesday.at(schedule_time).do(main)
    schedule.every().wednesday.at(schedule_time).do(main)
    schedule.every().thursday.at(schedule_time).do(main)
    schedule.every().friday.at(schedule_time).do(main)

    next_run = schedule.next_run()
    print(f'  执行时间: 交易日 {schedule_time}')
    print(f"  下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    print('  按 Ctrl+C 退出')

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print('\n⏹ 定时模式已退出')
    return 0


if __name__ == '__main__':
    args = parse_args()
    if args.schedule:
        exit(_schedule_loop(args.time))
    main()
