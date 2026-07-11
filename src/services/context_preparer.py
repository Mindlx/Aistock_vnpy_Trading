from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from src.services.signal_loader import SignalLoader

logger = logging.getLogger(__name__)

_ML_DB = "systems/MindLynx-Aistock/data/stock_analysis.db"


class ContextPreparer:
    """准备多源上下文（行情/技术/基本面/新闻/情绪），供 LLM 注入使用。"""

    def __init__(self, signal_loader: Optional[SignalLoader] = None):
        self._sl = signal_loader or SignalLoader()

    def prepare_all(self, stock_code: str) -> Dict[str, str]:
        ly_signals_md = self._sl.load_ly_signal(stock_code)
        ml_factor_md = self._sl.load_ml_factor(stock_code)
        market_md = self._prepare_market_context(stock_code)
        tech_md = self._prepare_technical_context(stock_code)
        news_md = self._prepare_news_context(stock_code)
        fund_md = self._prepare_fundamentals_context(stock_code)
        ml_md = self._prepare_ml_analysis_context(stock_code)

        return {
            "ly_signals_context": ly_signals_md,
            "ml_factor_context": ml_factor_md,
            "market_context": market_md + "\n\n" + tech_md,
            "fundamentals_context": fund_md + "\n\n" + ml_md,
            "sentiment_context": ml_md,
            "news_context": news_md,
        }

    def build_injection_payload(self, context: Dict[str, str]) -> Tuple[str, str]:
        ly_md = context.get("ly_signals_context", "")
        ml_factor_md = context.get("ml_factor_context", "")
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
            f"{context.get('market_context', '')}\n\n"
            "--- 基本面 ---\n"
            f"{context.get('fundamentals_context', '')}\n"
        )
        aiml = (
            f"[预加载数据] 以下数据从外部缓存获取，可直接用于分析。\n\n"
            f"=== LY 量化信号 ===\n{ly_md}\n\n"
            f"=== ML 因子信号 ===\n{ml_factor_md}\n\n"
            f"=== 行情与技术指标 ===\n{context.get('market_context', '')}\n\n"
            f"=== 基本面 ===\n{context.get('fundamentals_context', '')}\n\n"
            f"=== 情绪 ===\n{context['sentiment_context']}\n\n"
            f"=== 新闻 ===\n{context['news_context']}\n\n"
            f"(数据来源: LY UnifiedCache + ML stock_analysis.db)"
        )
        return sys_inject, aiml

    def _prepare_market_context(self, stock_code: str) -> str:
        try:
            from src.unified_cache import get_cache

            cache = get_cache()
            df = cache.get_daily_ohlcv(stock_code, days=60)
            if df is None or len(df) < 10:
                return "**行情数据:** 缓存数据不可用"

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
            return "**OHLCV (最近5天):**\n" + "\n".join(lines)
        except Exception as e:
            logger.debug(f"[preload] UnifiedCache 读取失败({stock_code}): {e}")
            return "**行情数据:** 缓存数据不可用"

    def _prepare_technical_context(self, stock_code: str) -> str:
        try:
            from src.unified_cache import get_cache

            cache = get_cache()
            df = cache.get_daily_ohlcv(stock_code, days=60)
            if df is None or len(df) < 20:
                return "**技术指标:** 缓存数据不可用"

            closes = df["close"].values if "close" in df.columns else None
            if closes is None or len(closes) < 20:
                return "**技术指标:** 数据不足"

            s = __import__("pandas", fromlist=["Series"]).Series(closes)
            ma5 = np.mean(closes[-5:])
            ma10 = np.mean(closes[-10:])
            ma20 = np.mean(closes[-20:])

            delta = np.diff(closes)
            gain = np.where(delta > 0, delta, 0)
            loss = np.where(delta < 0, -delta, 0)
            ag = np.mean(gain[-14:]) if len(gain) >= 14 else 0
            al = np.mean(loss[-14:]) if len(loss) >= 14 else 1e-6
            rsi = 100 - (100 / (1 + ag / max(al, 1e-10)))

            ema12 = s.ewm(span=12).mean().values
            ema26 = s.ewm(span=26).mean().values
            macd = ema12 - ema26
            macd_hist = macd[-1] - np.mean(macd[-9:]) if len(macd) >= 9 else macd[-1]

            bb_mid = ma20
            bb_std = np.std(closes[-20:])
            bb_up = bb_mid + 2 * bb_std
            bb_down = bb_mid - 2 * bb_std
            bb_pos = (closes[-1] - bb_down) / (bb_up - bb_down) if bb_up > bb_down else 0.5

            highs = df["high"].values if "high" in df.columns else closes
            lows = df["low"].values if "low" in df.columns else closes
            tr = np.maximum(
                highs[1:] - lows[1:],
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            )
            atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)

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
            return "**技术指标:** " + " | ".join(parts)
        except Exception as e:
            logger.debug(f"[preload] 技术指标计算失败({stock_code}): {e}")
            return "**技术指标:** 计算失败"

    def _prepare_ml_analysis_context(self, stock_code: str) -> str:
        if not Path(_ML_DB).exists():
            return "**ML分析:** 数据不可用"

        try:
            conn = sqlite3.connect(_ML_DB)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT operation_advice, sentiment_score, trend_prediction, "
                "analysis_summary, created_at FROM analysis_history "
                "WHERE code=? ORDER BY created_at DESC LIMIT 1",
                (stock_code,),
            ).fetchone()
            conn.close()

            if not row:
                return "**ML分析:** 无记录"

            ts = str(row["created_at"])[:19] if row["created_at"] else ""
            summary = (row["analysis_summary"] or "")[:300]
            return (
                f"**ML分析 ({ts}):** "
                f"建议={row['operation_advice']} | "
                f"评分={row['sentiment_score']} | "
                f"趋势={row['trend_prediction']}\n"
                f"{summary}"
            )
        except Exception as e:
            logger.debug(f"[preload] ML DB 查询失败({stock_code}): {e}")
            return "**ML分析:** 查询失败"

    def _prepare_news_context(self, stock_code: str) -> str:
        if not Path(_ML_DB).exists():
            return "**新闻公告:** 无近期新闻"

        try:
            conn = sqlite3.connect(_ML_DB)
            conn.row_factory = sqlite3.Row
            news_rows = conn.execute(
                "SELECT title, source, importance, created_at FROM news_intel "
                "WHERE code=? AND dimension IN ('daily_intel','weekend_intel') "
                "ORDER BY id DESC LIMIT 3",
                (stock_code,),
            ).fetchall()
            conn.close()

            if not news_rows:
                return "**新闻公告:** 无近期新闻"

            news_lines = ["**近期新闻:**"]
            for nr in news_rows:
                t = (nr["title"] or "")[:60]
                s = nr["source"] or ""
                imp = nr["importance"] or 0
                news_lines.append(f"  - [{s}](重要{imp}) {t}")
            return "\n".join(news_lines)
        except Exception as e:
            logger.debug(f"[preload] 新闻查询失败({stock_code}): {e}")
            return "**新闻公告:** 查询失败"

    def _prepare_fundamentals_context(self, stock_code: str) -> str:
        if not Path(_ML_DB).exists():
            return "**基本面:** 数据不可用"

        try:
            conn = sqlite3.connect(_ML_DB)
            conn.row_factory = sqlite3.Row
            fs = conn.execute(
                "SELECT payload FROM fundamental_snapshot "
                "WHERE code=? ORDER BY id DESC LIMIT 1",
                (stock_code,),
            ).fetchone()
            conn.close()

            if not fs:
                return "**基本面:** 无快照"

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
            return "**基本面:** " + " | ".join(fund_parts) if fund_parts else "**基本面:** 无数据"
        except Exception as e:
            logger.debug(f"[preload] 基本面查询失败({stock_code}): {e}")
            return "**基本面:** 查询失败"
