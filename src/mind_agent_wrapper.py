"""
mind_TradingAgent 批量分析封装器（零侵入版）

不修改 mind_TradingAgent 的任何代码。
通过 Python import 调用 TradingAgentsGraph.propagate() 逐只分析股票。

数据获取：使用 yfinance（mind_TradingAgent 原生数据供应商）
- 上海 A 股: 600xxx.SS
- 深圳 A 股: 000xxx.SZ / 300xxx.SZ

若 yfinance 数据不可用，自动降级到 akshare 获取 A 股行情作为替代。

使用方式:
    wrapper = MindTradingAgentWrapper()
    results = wrapper.run_batch(["601801", "001390"], "2026-05-29")
    # 返回: [{"code": "601801", "rating": "Buy", ...}, ...]

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MindTradingAgentWrapper:
    """
    mind_TradingAgent 批量分析封装器。

    主要功能:
    1. 驱动 mind_TradingAgent 对多只股票进行逐个分析
    2. 自动处理 A 股代码到 yfinance ticker 的转换
    3. 解析 PortfolioDecision 提取信号
    4. yfinance 不可用时 fallback 到 akshare

    使用前确保:
      - mind_TradingAgent 项目已 git clone
      - .env 已配置 API Key
      - 依赖已安装（yfinance, akshare）
    """

    def __init__(
        self,
        tradingagent_root: str = "systems/mind_TradingAgent",
        config_override: Optional[Dict[str, Any]] = None,
        debug: bool = False,
    ):
        """
        参数:
            tradingagent_root: mind_TradingAgent 项目根路径
            config_override: 覆盖 TradingAgent 默认配置的字典
            debug: 是否启用 TradingAgent 的 debug 模式（打印 LLM tokens）
        """
        self.ta_root = Path(tradingagent_root).expanduser().resolve()
        self.debug = debug
        self._config_override = config_override or {}
        self._imported = False
        self._ta = None

        # 确保 TradingAgent 项目在 sys.path 中
        if str(self.ta_root) not in sys.path:
            sys.path.insert(0, str(self.ta_root))

    def _ensure_imported(self):
        """确保 mind_TradingAgent 模块已加载（只导入一次）"""
        if self._imported:
            return True

        try:
            # 加载 .env 文件，让 DEFAULT_CONFIG 正确读取环境变量覆盖
            from dotenv import load_dotenv
            env_path = self.ta_root / ".env"
            if env_path.exists():
                load_dotenv(env_path, override=False)
                logger.info(f"已加载 env: {env_path}")
        except ImportError:
            pass

        try:
            from mind_tradingagent.graph.trading_graph import TradingAgentsGraph
            from mind_tradingagent.default_config import DEFAULT_CONFIG

            # 合并配置
            config = DEFAULT_CONFIG.copy()
            config.update(self._config_override)

            # 创建 TradingAgentsGraph 实例
            self._ta = TradingAgentsGraph(debug=self.debug, config=config)
            self._imported = True
            logger.info("mind_TradingAgent 已加载 (TradingAgentsGraph)")
            return True

        except ImportError as e:
            logger.error(f"导入 mind_TradingAgent 失败: {e}")
            logger.error(f"  确保项目已 clone: {self.ta_root}")
            logger.error("  并已安装依赖: pip install -r requirements.txt")
            return False

    def analyze_single(
        self, stock_code: str, trade_date: str, stock_name: str = ""
    ) -> Dict[str, Any]:
        """
        分析单只股票。

        参数:
            stock_code: A 股 6 位代码，如 "601801"
            trade_date: 交易日，如 "2026-05-29"
            stock_name: 股票中文名（可选）

        返回:
            {"code": str, "name": str, "rating": str, "signal": float,
             "raw_decision": str, "success": bool, ...}
        """
        from src.mind_stock_config import get_yfinance_ticker, get_stock_name, A_SHARE_MARKET_MAP

        if not self._ensure_imported():
            return self._empty_result(stock_code, stock_name, "TradingAgent 未加载")

        # 转换 A 股代码到 yfinance ticker
        try:
            yf_ticker = get_yfinance_ticker(stock_code)
            resolved_name = stock_name or get_stock_name(stock_code)
        except KeyError:
            from src.mind_stock_config import is_shanghai
            suffix = ".SS" if is_shanghai(stock_code) else ".SZ"
            yf_ticker = f"{stock_code}{suffix}"
            resolved_name = stock_name or stock_code

        # A 股数据前置验证：akshare → efinance → yfinance 降级
        from src.ashare_data import AshareDataProvider
        provider = AshareDataProvider()
        data_check = provider.verify_stock(stock_code)
        if not data_check.get("available"):
            logger.warning(f"A股数据 [{stock_code}]: 所有数据源不可用，跳过分析")
            return self._empty_result(stock_code, resolved_name, "A股数据源全部不可用")
        logger.info(f"A股数据 [{stock_code}]: 可用源={[k for k,v in data_check.get('sources',{}).items() if v]}")

        logger.info(f"TradingAgent: 分析 {resolved_name}({stock_code}) → {yf_ticker} @ {trade_date}")

        # ── 快速降级：主数据源不可用时直接走技术评分，跳过 LLM ──
        if data_check.get("fast_degrade", False):
            logger.info(f"TradingAgent [{stock_code}]: 主数据源不可用，启用快速降级")
            fb = self._fallback_akshare(stock_code, trade_date, resolved_name, error="快速降级")
            if fb.get("_fallback_data"):
                return {
                    "code": stock_code,
                    "name": resolved_name,
                    "yf_ticker": yf_ticker,
                    "rating": fb["rating"],
                    "final_decision": f"技术信号: {fb['rating']} (快速降级)",
                    "trade_date": trade_date,
                    "success": True,
                    "error": None,
                    "is_degraded": True,
                }

        try:
            # 调用 TradingAgent（可能耗时数分钟，含 LLM 推理）
            final_state, signal = self._ta.propagate(yf_ticker, trade_date)

            # 提取最终决策
            rating = signal if isinstance(signal, str) else "Hold"
            final_decision = final_state.get("final_trade_decision", "")

            result = {
                "code": stock_code,
                "name": resolved_name,
                "yf_ticker": yf_ticker,
                "rating": rating,
                "final_decision": final_decision,
                "trade_date": trade_date,
                "success": True,
                "error": None,
            }
            logger.info(f"TradingAgent [{stock_code}]: {rating}")
            return result

        except Exception as e:
            logger.error(f"TradingAgent [{stock_code}] 分析失败: {e}")
            # 尝试降级到数据分析（不含 LLM 推理）
            return self._fallback_akshare(stock_code, trade_date, resolved_name, error=str(e))

    def _fallback_akshare(
        self, stock_code: str, trade_date: str, stock_name: str, error: str = ""
    ) -> Dict[str, Any]:
        """
        当 TradingAgent 分析失败时，使用 akshare 获取基础行情数据。
        仅做简单的技术信号判断，不含 LLM 推理。
        """
        result = self._empty_result(stock_code, stock_name, error)

        try:
            import yfinance as yf

            # 用 is_shanghai 判断后缀
            from src.mind_stock_config import is_shanghai
            suffix = ".SS" if is_shanghai(stock_code) else ".SZ"
            yf_ticker = f"{stock_code}{suffix}"
            ticker = yf.Ticker(yf_ticker)
            hist = ticker.history(period="3mo")
            if hist is not None and not hist.empty:
                latest = hist.iloc[-1]
                # 计算涨跌幅（基于前一天的收盘价）
                if len(hist) >= 2:
                    prev_close = hist.iloc[-2]["Close"]
                    change_pct = ((float(latest["Close"]) - float(prev_close)) / float(prev_close)) * 100
                else:
                    change_pct = 0.0
                result["_fallback_data"] = {
                    "close": float(latest["Close"]),
                    "change_pct": change_pct,
                    "volume": float(latest.get("Volume", 0)),
                    "high": float(latest["High"]),
                    "low": float(latest["Low"]),
                }
                # 技术指标综合评分
                result["rating"] = self._technical_rating(hist)
                logger.info(f"yfinance [{stock_code}]: 技术信号={result['rating']}")
            else:
                logger.warning(f"yfinance [{stock_code}]: 无数据")

        except ImportError:
            logger.warning("yfinance 未安装，无法获取 A 股行情")
        except Exception as e:
            logger.warning(f"yfinance [{stock_code}]: 获取失败 {e}")

        return result

    def _technical_rating(self, hist) -> str:
        """Compute Buy/Overweight/Hold/Underweight/Sell from yfinance OHLCV.
        Uses MA alignment, RSI, MACD, volume confirmation — no LLM needed."""
        import numpy as np
        import pandas as pd

        closes = hist["Close"].values
        highs = hist["High"].values
        lows = hist["Low"].values
        volumes = hist["Volume"].values
        n = len(closes)
        if n < 20:
            return "Hold"

        latest_close = float(closes[-1])
        prev_close = float(closes[-2]) if n >= 2 else latest_close

        # MA
        ma5 = np.mean(closes[-5:]) if n >= 5 else latest_close
        ma10 = np.mean(closes[-10:]) if n >= 10 else latest_close
        ma20 = np.mean(closes[-20:]) if n >= 20 else latest_close

        # RSI(14)
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-14:]) if n >= 15 else 0
        avg_loss = np.mean(losses[-14:]) if n >= 15 else 1e-6
        rs = avg_gain / max(avg_loss, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # MACD
        ema12 = pd.Series(closes).ewm(span=12).mean().values
        ema26 = pd.Series(closes).ewm(span=26).mean().values
        macd = ema12 - ema26
        macd_hist = macd[-1] - np.mean(macd[-9:]) if n >= 9 else macd[-1]

        # Volume ratio (current vs MA5 volume)
        vol_ma5 = np.mean(volumes[-5:]) if n >= 5 else volumes[-1]
        vol_ratio = volumes[-1] / max(vol_ma5, 1)

        # Score logic
        score = 0

        # MA alignment: price vs MA
        if latest_close > ma10:
            score += 1
        elif latest_close < ma10:
            score -= 1

        # MA cross
        if ma5 > ma10:
            score += 1  # short-term uptrend
        elif ma5 < ma10:
            score -= 1

        if ma10 > ma20:
            score += 1  # medium-term uptrend
        elif ma10 < ma20:
            score -= 1

        # RSI momentum
        if rsi > 60:
            score += 1
        elif rsi < 40:
            score -= 1

        # MACD momentum
        if macd_hist > 0:
            score += 1
        elif macd_hist < 0:
            score -= 1

        # Volume confirmation
        if vol_ratio > 1.2 and latest_close > prev_close:
            score += 1  # bullish volume
        elif vol_ratio > 1.2 and latest_close < prev_close:
            score -= 1  # bearish volume

        # Map to 5-level TA rating
        if score >= 4:
            return "Buy"
        elif score >= 2:
            return "Overweight"
        elif score <= -4:
            return "Sell"
        elif score <= -2:
            return "Underweight"
        else:
            return "Hold"

    def run_batch(
        self, stock_codes: List[str], trade_date: str,
        concurrency: int = 1, delay_between: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        批量分析多只股票。

        注意: mind_TradingAgent 使用 LLM，每只股票需 1-5 分钟。
        当前仅支持串行执行（concurrency=1）。

        参数:
            stock_codes: A 股代码列表
            trade_date: 交易日
            concurrency: 并发数（保留，暂未实现）
            delay_between: 间隔秒数

        返回:
            分析结果列表
        """
        if not self._ensure_imported():
            logger.error("TradingAgent 未加载，无法批量分析")
            return []

        results = []
        total = len(stock_codes)

        logger.info(f"TradingAgent: 开始批量分析 {total} 只股票 (date={trade_date})")

        for i, code in enumerate(stock_codes, 1):
            logger.info(f"[{i}/{total}] 分析 {code}...")
            result = self.analyze_single(code, trade_date)
            results.append(result)

            # 间隔保护（避免 API 限流）
            if i < total and delay_between > 0:
                logger.debug(f"等待 {delay_between}s...")
                time.sleep(delay_between)

        # 汇总
        success_count = sum(1 for r in results if r.get("success"))
        fallback_count = sum(1 for r in results if r.get("rating") == "Hold" and r.get("_fallback_data"))
        logger.info(
            f"TradingAgent: 完成 {len(results)}/{total} 只, "
            f"成功 {success_count}, 降级 {fallback_count}"
        )

        return results

    def run_batch_and_save(
        self, stock_codes: List[str], trade_date: str,
        output_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        批量分析并保存结果到 JSON。

        返回的分析结果可直接被融合系统的 data_loader 读取。
        """
        results = self.run_batch(stock_codes, trade_date)

        if output_path is None:
            output_path = f"data/tradingagent/ta_signals_{trade_date}.json"

        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "date": trade_date,
                "generated_at": datetime.now().isoformat(),
                "total": len(results),
                "results": results,
            }, f, ensure_ascii=False, indent=2)

        logger.info(f"TradingAgent 结果已保存: {output_path}")
        return results

    @staticmethod
    def _empty_result(code: str, name: str, error: str = "") -> Dict[str, Any]:
        """生成空结果（分析失败时使用）"""
        return {
            "code": code,
            "name": name,
            "yf_ticker": "",
            "rating": "Hold",
            "final_decision": "",
            "trade_date": "",
            "success": False,
            "error": error,
        }

    @staticmethod
    def extract_rating_from_results(
        results: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """
        从批量结果中提取融合系统需要的评级信息。

        返回:
            {stock_code: rating_str}
            rating 为 Buy/Overweight/Hold/Underweight/Sell 之一
        """
        ratings = {}
        for r in results:
            code = r.get("code", "")
            rating = r.get("rating", "Hold")
            ratings[code] = rating
        return ratings


# ══════════════════════════════════════════
# 快速验证工具
# ══════════════════════════════════════════

def verify_data_source(stock_code: str = "601801") -> Dict[str, Any]:
    """
    验证 A 股数据源是否正常工作。
    尝试依次使用 yfinance 和 akshare 获取数据。
    """
    from src.mind_stock_config import get_yfinance_ticker

    yf_ticker = get_yfinance_ticker(stock_code)
    result = {"stock_code": stock_code, "yf_ticker": yf_ticker, "yfinance": None, "akshare": None}

    # 测试 yfinance
    try:
        import yfinance as yf
        ticker = yf.Ticker(yf_ticker)
        hist = ticker.history(period="5d")
        if hist is not None and not hist.empty:
            result["yfinance"] = {
                "available": True,
                "rows": len(hist),
                "latest_close": float(hist["Close"].iloc[-1]),
                "columns": list(hist.columns),
            }
        else:
            result["yfinance"] = {"available": False, "reason": "empty data"}
    except Exception as e:
        result["yfinance"] = {"available": False, "reason": str(e)}

    # 测试 akshare
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date="20260520", adjust="qfq")
        if df is not None and not df.empty:
            result["akshare"] = {
                "available": True,
                "rows": len(df),
                "latest_close": float(df["收盘"].iloc[-1]),
                "columns": list(df.columns),
            }
        else:
            result["akshare"] = {"available": False, "reason": "empty data"}
    except Exception as e:
        result["akshare"] = {"available": False, "reason": str(e)}

    return result


if __name__ == "__main__":
    # 快速验证
    import pprint
    logging.basicConfig(level=logging.INFO)

    print("=" * 50)
    print("A 股数据源验证")
    print("=" * 50)

    for code in ["601801", "001390", "600372"]:
        print(f"\n📡 {code} ({get_yfinance_ticker(code)})...")
        result = verify_data_source(code)
        print(f"  yfinance: {result['yfinance']}")
        print(f"  akshare:  {result['akshare']}")
