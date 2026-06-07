#!/usr/bin/env python3
"""
ly 独立回测 — 使用 vnpy BacktestingEngine

用法:
    python systems/lynx_vnpy/vnpy_bridge/ly_backtest.py                          # 默认
    python systems/lynx_vnpy/vnpy_bridge/ly_backtest.py --capital 500000         # 50万本金
    python systems/lynx_vnpy/vnpy_bridge/ly_backtest.py --benchmark 000300       # 基准对比
    python systems/lynx_vnpy/vnpy_bridge/ly_backtest.py --chart                  # 显示图表

依赖: 先运行 data_converter.py 生成 Parquet 数据
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent  # vnpy_bridge → lynx_vnpy → systems → .
_VNPY_ROOT = str(_SCRIPT_DIR.parent / "lynx_vnpy")  # systems/lynx_vnpy/lynx_vnpy
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, _VNPY_ROOT)

from lynx_vnpy.alpha.lab import AlphaLab
from lynx_vnpy.alpha.strategy.backtesting import BacktestingEngine
from lynx_vnpy.alpha.strategy.strategies.equity_demo_strategy import EquityDemoStrategy
from lynx_vnpy.trader.constant import Interval

LAB_PATH = str(_PROJECT_ROOT / "data/vnpy_lab")


def main():
    parser = argparse.ArgumentParser(description="ly独立回测")
    parser.add_argument("--capital", type=int, default=1000000, help="起始资金(默认100万)")
    parser.add_argument("--benchmark", type=str, default=None, help="基准代码(如000300)")
    parser.add_argument("--chart", action="store_true", help="显示Plotly图表")
    parser.add_argument("--days", type=int, default=120, help="回测天数(默认120)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    lab = AlphaLab(LAB_PATH)
    daily_dir = Path(LAB_PATH) / "daily"
    vt_symbols = sorted(f.stem for f in daily_dir.iterdir() if f.suffix == ".parquet")
    if not vt_symbols:
        print("❌ 无数据，先运行: python vnpy_bridge/data_converter.py")
        return

    # 计算日期范围
    sample = list(daily_dir.glob("*.parquet"))[0]
    import polars as pl
    pdf = pl.read_parquet(str(sample))
    dates = pdf["datetime"].to_list()
    end = datetime.fromisoformat(str(dates[-1]))
    start = datetime.fromisoformat(str(dates[0]))
    print(f"📊 ly独立回测")
    print(f"   股票: {len(vt_symbols)} 只")
    print(f"   区间: {start.date()} ~ {end.date()}")
    print(f"   本金: ¥{args.capital:,}")
    print(f"   基准: {args.benchmark or '无'}")

    # 注册合约配置（BacktestingEngine需要）
    for vt in vt_symbols:
        lab.add_contract_setting(vt, long_rate=0.0003, short_rate=0.001, size=100, pricetick=0.01)

    # 初始化回测引擎
    engine = BacktestingEngine(lab)
    engine.set_parameters(
        vt_symbols=vt_symbols,
        interval=Interval.DAILY,
        start=start,
        end=end,
        capital=args.capital,
        risk_free=0.0,
        annual_days=240,
    )

    # 生成简单信号（每日预测值，后期可替换为LGB完整信号）
    print(f"\n🔄 生成回测信号...")
    signal_rows = []
    try:
        import sys as _sys
        _sys.path.insert(0, str(_PROJECT_ROOT / "systems/lynx_vnpy"))
        import polars as pl

        # 简单的移动平均信号: close > ma20 → 看多, 否则看空
        for vt in vt_symbols:
            pdf = pl.read_parquet(str(daily_dir / f"{vt}.parquet"))
            closes = pdf["close"].to_list()
            for i in range(20, len(closes)):
                ma20 = sum(closes[i-20:i]) / 20
                signal = 1.0 if closes[i] > ma20 else -1.0
                signal_rows.append({
                    "datetime": pdf["datetime"][i],
                    "vt_symbol": vt,
                    "signal": signal,
                })
        signal_df = pl.DataFrame(signal_rows) if signal_rows else pl.DataFrame()
        print(f"   信号生成完成: {len(signal_df)} 条")
    except Exception as e:
        print(f"   ⚠️ 信号生成失败: {e}")
        signal_df = pl.DataFrame()

    # 添加策略
    engine.add_strategy(EquityDemoStrategy, {}, signal_df)
    engine.load_data()
    engine.run_backtesting()

    # 输出统计
    try:
        # 初始化 daily_df（BacktestingEngine有时未正确设置）
        if not hasattr(engine, 'daily_df'):
            import polars as pl
            engine.daily_df = pl.DataFrame()
        stats = engine.calculate_statistics()
        print(f"\n📈 绩效指标")
        print(f"   夏普比率: {stats.get('sharpe_ratio', 0):.2f}")
        print(f"   年化收益: {stats.get('annual_return', 0):.2f}%")
        print(f"   最大回撤: {stats.get('max_drawdown', 0):.2f}%")
        print(f"   总交易: {stats.get('total_trades', 0)}")
        print(f"   胜率: {stats.get('win_rate', 0):.1f}%")
    except Exception as e:
        print(f"\n⚠️ 统计指标计算异常: {e}")
        print(f"   初始资金: ¥{args.capital:,}")
        print(f"   总交易: {getattr(engine, 'trade_count', 0)}")
        # 手动计算基础指标
        trades = getattr(engine, 'trades', {}) or {}
        wins = sum(1 for t in trades.values() if getattr(t, 'direction', None) and t.direction.name == 'LONG')
        if trades:
            print(f"   总成交: {len(trades)}")
        # 尝试获取现金余额估算收益
        cash = getattr(engine, 'cash', args.capital)
        print(f"   剩余现金: ¥{cash:,.0f}")

    # 基准对比
    if args.benchmark:
        print(f"\n📉 基准对比 ({args.benchmark})")
        try:
            engine.show_performance(args.benchmark)
            print(f"   Alpha/Beta/信息比 图表已生成")
        except Exception as e:
            print(f"   ⚠️ 基准对比失败: {e}")

    # 图表
    if args.chart:
        try:
            engine.show_chart()
        except Exception as e:
            print(f"   ⚠️ 图表显示失败: {e}")

    print(f"\n✅ 回测完成")


if __name__ == "__main__":
    main()
