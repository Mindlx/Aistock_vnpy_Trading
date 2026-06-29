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

import numpy as np

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
        except ImportError as _e:
            logger.debug("[mind_agent] dotenv 未安装，跳过: %s", _e)

        try:
            from mind_tradingagent.graph.trading_graph import TradingAgentsGraph
            from mind_tradingagent.default_config import DEFAULT_CONFIG

            # 合并配置
            config = DEFAULT_CONFIG.copy()
            config.update(self._config_override)

            # 创建 TradingAgentsGraph 实例
            # ── A 股精简: 关闭 Sentiment Analyst（雪球/股吧情绪已由 News + 注入覆盖）──
            self._ta = TradingAgentsGraph(
                debug=self.debug, config=config,
                selected_analysts=["market", "social", "news", "fundamentals", "policy", "capital_flow"],
            )
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

        # ── 数据仓库预热 (零侵入: 不可用时自动跳过) ──
        try:
            from services.data_warehouse import WarehouseReader
            _wr = WarehouseReader()
            if not _wr.is_fresh(stock_code, "daily_ohlcv"):
                _wr.get_daily(stock_code, days=120)  # 触发缓存填充
        except ImportError:
            logger.debug("[mind_agent] WarehouseReader 不可用")
        except Exception as _e:
            logger.debug("[mind_agent] 数据仓库预热失败: %s", _e)

        # A 股数据前置验证：akshare → efinance → yfinance 降级
        from src.ashare_data import AshareDataProvider
        provider = AshareDataProvider()
        data_check = provider.verify_stock(stock_code)
        if not data_check.get("available"):
            logger.warning(f"A股数据 [{stock_code}]: 所有数据源不可用，跳过分析")
            return self._empty_result(stock_code, resolved_name, "A股数据源全部不可用")
        logger.info(f"A股数据 [{stock_code}]: 可用源={[k for k,v in data_check.get('sources',{}).items() if v]}")

        logger.info(f"TradingAgent: 分析 {resolved_name}({stock_code}) → {yf_ticker} @ {trade_date}")

        # ── 数据注入 + LLM 辩论路径 ──
        # 当主数据源不可用时，从 LY/ML 缓存注入预加载数据，
        # 让 AT 的 LLM 辩论仍然可以运行（不跳过 LLM）。
        # 如果注入后 AT 仍然失败，走快速降级。
        should_inject = True  # 始终注入，确保 AT Agent 获得 LY/ML 信号

        if should_inject:
            logger.info(f"TradingAgent [{stock_code}]: 尝试数据注入 (LY信号 + ML因子 + 缓存数据)")

        try:
            if should_inject:
                preloaded = self._get_preloaded_context(stock_code)
                ly_md = preloaded.get("ly_signals_context", "")
                ml_factor_md = preloaded.get("ml_factor_context", "")
                orig_create = self._ta.propagator.create_initial_state

                def _injected_create(company_name, trade_date, asset_type="stock", past_context=""):
                    state = orig_create(company_name, trade_date, asset_type, past_context)
                    from langchain_core.messages import AIMessage, SystemMessage
                    # Option A+: 注入到 system prompt 头部（最高权威层级）
                    sys_inject = (
                        "\n\n[系统注入] 以下数据来自本平台量化模型(LY)和AI分析(ML)系统，"
                        "权威性高于原始行情数据。请首先参考以下预加载数据进行判断:\n\n"
                    )
                    if ly_md:
                        sys_inject += f"--- LY 量化信号 ---\n{ly_md}\n\n"
                    if ml_factor_md:
                        sys_inject += f"--- ML 因子信号 ---\n{ml_factor_md}\n\n"
                    sys_inject += (
                        "--- 行情与技术指标 ---\n"
                        f"{preloaded.get('market_context', '')}\n\n"
                        "--- 基本面 ---\n"
                        f"{preloaded.get('fundamentals_context', '')}\n"
                    )
                    state["messages"].insert(0, SystemMessage(content=sys_inject))
                    # 同时注入 AIMessage（补充详细分析上下文）
                    aiml = (
                        f"[预加载数据] 以下数据从外部缓存获取，可直接用于分析。\n\n"
                        f"=== LY 量化信号 ===\n{ly_md}\n\n"
                        f"=== ML 因子信号 ===\n{ml_factor_md}\n\n"
                        f"=== 行情与技术指标 ===\n{preloaded['market_context']}\n\n"
                        f"=== 基本面 ===\n{preloaded['fundamentals_context']}\n\n"
                        f"=== 情绪 ===\n{preloaded['sentiment_context']}\n\n"
                        f"=== 新闻 ===\n{preloaded['news_context']}\n\n"
                        f"(数据来源: LY UnifiedCache + ML stock_analysis.db)"
                    )
                    state["messages"].append(AIMessage(content=aiml))
                    return state

                self._ta.propagator.create_initial_state = _injected_create
                try:
                    final_state, signal = self._ta.propagate(stock_code, trade_date)
                finally:
                    self._ta.propagator.create_initial_state = orig_create
            else:
                final_state, signal = self._ta.propagate(stock_code, trade_date)

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
                "_injected": True,
            }
            logger.info(f"TradingAgent [{stock_code}]: {rating}" +
                        (" (数据注入)" if should_inject else ""))
            return result

        except Exception as e:
            logger.error(f"TradingAgent [{stock_code}] 分析失败: {e}" +
                         (" (已尝试数据注入)" if should_inject else ""))
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

    def _load_ly_signals_for_at(self, stock_code: str) -> str:
        """读取 LY 双模型信号并格式化为 Markdown 文本。

        读取 data/realtime/ly_signal.json + ly_alpha_signal.json + prob_up_log.csv，
        对齐 pipeline.py:_load_ly_signals() 的逻辑。
        返回格式化 Markdown 字符串，文件缺失或过时时返回空字符串。
        """
        import json
        from datetime import datetime, timedelta
        from pathlib import Path

        realtime_dir = Path("data/realtime")
        result: dict = {}

        # Source 1: ly_signal.json (RF)
        rf_data = {}
        rf_path = realtime_dir / "ly_signal.json"
        if rf_path.exists():
            try:
                raw = json.loads(rf_path.read_text(encoding="utf-8"))
                updated = raw.get("updated_at", "")
                try:
                    updated_dt = datetime.strptime(updated[:10], "%Y-%m-%d")
                    if (datetime.now() - updated_dt) <= timedelta(hours=36):
                        rf_data = raw.get("stocks", {})
                except (ValueError, TypeError):
                    rf_data = raw.get("stocks", {})
            except Exception:
                pass

        # Source 2: ly_alpha_signal.json (LGB)
        lgb_data = {}
        lgb_path = realtime_dir / "ly_alpha_signal.json"
        if lgb_path.exists():
            try:
                raw = json.loads(lgb_path.read_text(encoding="utf-8"))
                lgb_data = raw.get("stocks", {})
            except Exception:
                pass

        # Source 3: prob_up_log.csv (ensemble)
        csv_latest = {}
        csv_path = realtime_dir / "prob_up_log.csv"
        if csv_path.exists():
            try:
                import csv
                with open(csv_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                if rows:
                    latest_date = rows[-1].get("date", "")
                    for row in rows:
                        if row.get("date") == latest_date:
                            code = row.get("stock_code", "").strip()
                            if code:
                                csv_latest[code] = {
                                    "prob_up_rf": row.get("prob_up_rf", ""),
                                    "prob_up_lgb": row.get("prob_up_lgb", ""),
                                    "prob_up_ensemble": row.get("prob_up_ensemble", ""),
                                    "l7_score_rf": row.get("l7_score_rf", ""),
                                    "l7_score_lgb": row.get("l7_score_lgb", ""),
                                }
            except Exception:
                pass

        # Merge
        rf = rf_data.get(stock_code, {})
        lgb = lgb_data.get(stock_code, {})
        csv_row = csv_latest.get(stock_code, {})
        if not rf and not lgb and not csv_row:
            return ""

        ensemble = csv_row.get("prob_up_ensemble", "")
        if not ensemble:
            prf = csv_row.get("prob_up_rf") or rf.get("prob_up")
            plgb = csv_row.get("prob_up_lgb") or lgb.get("prob_up")
            try:
                if prf != "" and plgb != "":
                    ensemble = f"{(float(prf) + float(plgb)) / 2:.1f}"
            except (ValueError, TypeError) as _e:
                logger.debug("[mind_agent] 概率计算异常: %s", _e)

        prob_rf = csv_row.get("prob_up_rf", "") or rf.get("prob_up", "")
        prob_lgb = csv_row.get("prob_up_lgb", "") or lgb.get("prob_up", "")

        # Model disagreement
        disagreement = ""
        try:
            pr = float(prob_rf) if prob_rf else 0
            pl = float(prob_lgb) if prob_lgb else 0
            if pr and pl:
                disagreement = f"{abs(pr - pl):.1f}%"
        except (ValueError, TypeError) as _e:
            logger.debug("[mind_agent] 分歧度计算异常: %s", _e)

        # Strength
        strength = ""
        try:
            prob = float(ensemble) if ensemble else 0
            if prob >= 70: strength = "强"
            elif prob >= 55: strength = "中"
            else: strength = "弱"
        except (ValueError, TypeError) as _e:
            logger.debug("[mind_agent] 强度计算异常: %s", _e)

        # Format
        lines = [
            f"| 综合上涨概率 | {ensemble}% | RF+LGB 双模型集成 |",
            f"| RF 上涨概率 | {prob_rf}% | RandomForest（15+ 技术指标） |",
            f"| LGB 上涨概率 | {prob_lgb}% | Alpha158 LightGBM（158 因子） |",
        ]
        l7_rf = csv_row.get("l7_score_rf", "") or rf.get("score", "")
        if l7_rf:
            lines.append(f"| L7 得分(RF) | {l7_rf} | 范围[-3,+3] 正值偏多 |")
        l7_lgb = csv_row.get("l7_score_lgb", "") or lgb.get("score", "")
        if l7_lgb:
            lines.append(f"| L7 得分(LGB) | {l7_lgb} | 范围[-3,+3] 正值偏多 |")
        if strength:
            lines.append(f"| 综合置信度 | {strength} | 强(≥70%) 中(55-70%) 弱(<55%) |")
        if disagreement:
            level = "高分歧" if float(disagreement.replace("%","")) > 15 else "低分歧"
            lines.append(f"| 模型分歧 | {disagreement} | {level} |")
        return "\n".join(lines)

    def _get_preloaded_context(self, stock_code: str) -> Dict[str, str]:
        """从 LY(UnifiedCache) + ML(stock_analysis.db) 提取预加载数据。

        返回格式化为 markdown 的上下文文本，供 AT 分析师 LLM 直接使用。
        包含: LY量化信号/ML因子/OHLCV/技术指标/基本面/新闻情报。
        """
        ly_signals_md = ""
        ml_factor_md = ""
        market_md = "**行情数据:** 缓存数据不可用"
        tech_md = "**技术指标:** 缓存数据不可用"
        ml_md = "**ML分析:** 数据不可用"
        news_md = "**新闻公告:** 无近期新闻"
        fund_md = "**基本面:** 数据不可用"

        # 0. LY 双模型信号
        ly_signals_md = self._load_ly_signals_for_at(stock_code)

        # 0b. ML 因子信号 (data/realtime/ml_signal.json)
        try:
            mf_path = Path("data/realtime/ml_signal.json")
            if mf_path.exists():
                import json
                raw = json.loads(mf_path.read_text(encoding="utf-8"))
                mf_stock = raw.get("stocks", {}).get(stock_code, {})
                if mf_stock:
                    parts = []
                    cs = mf_stock.get("composite_score")
                    if cs is not None:
                        parts.append(f"综合评分={cs}")
                    l7 = mf_stock.get("l7_score")
                    if l7 is not None:
                        parts.append(f"L7={l7}")
                    cl = mf_stock.get("composite_label")
                    if cl:
                        parts.append(f"标签={cl}")
                    factors = mf_stock.get("factors", {})
                    if factors:
                        sorted_f = sorted(factors.items(), key=lambda x: abs(x[1] if isinstance(x[1], (int,float)) else 0), reverse=True)[:3]
                        top3 = " | ".join(f"{k}={v}" for k,v in sorted_f)
                        parts.append(f"前三因子: {top3}")
                    if parts:
                        ml_factor_md = " | ".join(parts)
        except Exception:
            pass

        _ML_DB = "systems/MindLynx-Aistock/data/stock_analysis.db"
        _has_ml_db = Path(_ML_DB).exists()

        # 1. 从 UnifiedCache 取 OHLCV + 全量技术指标
        try:
            from src.unified_cache import get_cache
            cache = get_cache()
            df = cache.get_daily_ohlcv(stock_code, days=60)
            if df is not None and len(df) >= 10:
                recent = df.tail(5)
                lines = ["日期|开盘|最高|最低|收盘|成交量(股)"]
                lines.append("---|---|---|---|---|---")
                for _, r in recent.iterrows():
                    try:
                        date = str(r.get("date", r.name))[:10]
                        o = f"{float(r['open']):.2f}" if "open" in r else "-"
                        h = f"{float(r['high']):.2f}" if "high" in r else "-"
                        l_ = f"{float(r['low']):.2f}" if "low" in r else "-"
                        c = f"{float(r['close']):.2f}" if "close" in r else "-"
                        v = f"{int(r['volume']):,}" if "volume" in r else "-"
                        lines.append(f"{date}|{o}|{h}|{l_}|{c}|{v}")
                    except Exception:
                        continue
                market_md = "**OHLCV (最近5天):**\n" + "\n".join(lines)

                closes = df["close"].values if "close" in df.columns else None
                if closes is not None and len(closes) >= 20:
                    import pandas as pd
                    s = pd.Series(closes)
                    ma5 = np.mean(closes[-5:])
                    ma10 = np.mean(closes[-10:])
                    ma20 = np.mean(closes[-20:])
                    # RSI(14)
                    delta = np.diff(closes)
                    gain = np.where(delta > 0, delta, 0)
                    loss = np.where(delta < 0, -delta, 0)
                    ag = np.mean(gain[-14:]) if len(gain) >= 14 else 0
                    al = np.mean(loss[-14:]) if len(loss) >= 14 else 1e-6
                    rsi = 100 - (100 / (1 + ag / max(al, 1e-10)))
                    # MACD
                    ema12 = s.ewm(span=12).mean().values
                    ema26 = s.ewm(span=26).mean().values
                    macd = ema12 - ema26
                    macd_hist = macd[-1] - np.mean(macd[-9:]) if len(macd) >= 9 else macd[-1]
                    # Bollinger Bands (20,2)
                    bb_mid = ma20
                    bb_std = np.std(closes[-20:])
                    bb_up = bb_mid + 2 * bb_std
                    bb_down = bb_mid - 2 * bb_std
                    bb_pos = (closes[-1] - bb_down) / (bb_up - bb_down) if bb_up > bb_down else 0.5
                    # ATR(14)
                    highs = df["high"].values if "high" in df.columns else closes
                    lows = df["low"].values if "low" in df.columns else closes
                    tr = np.maximum(highs[1:] - lows[1:],
                                    np.abs(highs[1:] - closes[:-1]),
                                    np.abs(lows[1:] - closes[:-1]))
                    atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
                    # Volume ratio
                    vols = df["volume"].values if "volume" in df.columns else None
                    vol_ratio = (vols[-1] / np.mean(vols[-5:])) if vols is not None and len(vols) >= 5 else None

                    parts = [
                        f"MA5={ma5:.2f}", f"MA10={ma10:.2f}", f"MA20={ma20:.2f}",
                        f"RSI(14)={rsi:.1f}",
                        f"MACD柱={macd_hist:.4f}",
                        f"布林带={bb_mid:.2f}(±{bb_std:.2f})", f"BB位置={bb_pos:.0%}",
                        f"ATR(14)={atr:.4f}",
                    ]
                    if vol_ratio is not None:
                        parts.append(f"量比={vol_ratio:.2f}")
                    tech_md = "**技术指标:** " + " | ".join(parts)
        except Exception as e:
            logger.debug(f"[preload] UnifiedCache 读取失败({stock_code}): {e}")

        # 2. 从 ML 数据库取最新分析和基本面
        if _has_ml_db:
            try:
                import sqlite3
                conn = sqlite3.connect(_ML_DB)
                conn.row_factory = sqlite3.Row

                # 2a. 最新分析记录
                row = conn.execute(
                    "SELECT operation_advice, sentiment_score, trend_prediction, "
                    "analysis_summary, created_at FROM analysis_history "
                    "WHERE code=? ORDER BY created_at DESC LIMIT 1",
                    (stock_code,),
                ).fetchone()
                if row:
                    ts = str(row["created_at"])[:19] if row["created_at"] else ""
                    summary = (row["analysis_summary"] or "")[:300]
                    ml_md = (
                        f"**ML分析 ({ts}):** "
                        f"建议={row['operation_advice']} | "
                        f"评分={row['sentiment_score']} | "
                        f"趋势={row['trend_prediction']}\n"
                        f"{summary}"
                    )

                # 2b. 基本面快照 (PE/PB/板块)
                fs = conn.execute(
                    "SELECT payload FROM fundamental_snapshot "
                    "WHERE code=? ORDER BY id DESC LIMIT 1",
                    (stock_code,),
                ).fetchone()
                if fs:
                    import json
                    try:
                        p = json.loads(fs["payload"])
                        boards = p.get("belong_boards", [])
                        board_str = ", ".join(b["name"] for b in boards[:5]) if boards else ""
                        val = p.get("valuation", {})
                        val_data = val.get("data", {}) if isinstance(val, dict) else {}
                        pe = val_data.get("pe_ratio")
                        pb = val_data.get("pb_ratio")
                        mv = val_data.get("total_mv")
                        fund_parts = []
                        if board_str:
                            fund_parts.append(f"板块={board_str}")
                        if pe is not None:
                            fund_parts.append(f"PE={pe}")
                        if pb is not None:
                            fund_parts.append(f"PB={pb}")
                        if mv is not None:
                            fund_parts.append(f"市值={mv/1e8:.0f}亿" if mv > 1e8 else "")
                        if fund_parts:
                            fund_md = "**基本面:** " + " | ".join(fund_parts)
                    except Exception:
                        pass

                # 2c. 新闻公告 (最近3条)
                news_rows = conn.execute(
                    "SELECT title, source, importance, created_at FROM news_intel "
                    "WHERE code=? AND dimension IN ('daily_intel','weekend_intel') "
                    "ORDER BY id DESC LIMIT 3",
                    (stock_code,),
                ).fetchall()
                if news_rows:
                    news_lines = ["**近期新闻:**"]
                    for nr in news_rows:
                        t = (nr["title"] or "")[:60]
                        s = nr["source"] or ""
                        imp = nr["importance"] or 0
                        news_lines.append(f"  - [{s}](重要{imp}) {t}")
                    news_md = "\n".join(news_lines)

                conn.close()
            except Exception as e:
                logger.debug(f"[preload] ML DB 查询失败({stock_code}): {e}")

        return {
            "ly_signals_context": ly_signals_md,
            "ml_factor_context": ml_factor_md,
            "market_context": market_md + "\n\n" + tech_md,
            "fundamentals_context": fund_md + "\n\n" + ml_md,
            "sentiment_context": ml_md,
            "news_context": news_md,
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
