from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SignalLoader:
    """加载 LY/ML/AT 信号，支持会话级 per-stock 缓存。"""

    REALTIME_DIR = Path("data/realtime")
    _cache: Dict[str, Dict[str, Optional[str]]] = {}

    def load_ly_signal(self, stock_code: str) -> str:
        cached = self._cache.get(stock_code, {}).get("ly")
        if cached is not None:
            return cached
        result = self._do_load_ly_signal(stock_code)
        self._cache.setdefault(stock_code, {})["ly"] = result
        return result

    def load_ml_factor(self, stock_code: str) -> str:
        cached = self._cache.get(stock_code, {}).get("ml_factor")
        if cached is not None:
            return cached
        result = self._do_load_ml_factor(stock_code)
        self._cache.setdefault(stock_code, {})["ml_factor"] = result
        return result

    def load_at_signal(self, stock_code: str) -> str:
        cached = self._cache.get(stock_code, {}).get("at")
        if cached is not None:
            return cached
        result = self._do_load_at_signal(stock_code)
        self._cache.setdefault(stock_code, {})["at"] = result
        return result

    def clear_cache(self, stock_code: Optional[str] = None):
        if stock_code:
            self._cache.pop(stock_code, None)
        else:
            self._cache.clear()

    def _do_load_ly_signal(self, stock_code: str) -> str:
        rf_data: dict = {}
        rf_path = self.REALTIME_DIR / "ly_signal.json"
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

        lgb_data: dict = {}
        lgb_path = self.REALTIME_DIR / "ly_alpha_signal.json"
        if lgb_path.exists():
            try:
                raw = json.loads(lgb_path.read_text(encoding="utf-8"))
                lgb_data = raw.get("stocks", {})
            except Exception:
                pass

        csv_latest: dict = {}
        csv_path = self.REALTIME_DIR / "prob_up_log.csv"
        if csv_path.exists():
            try:
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
            except (ValueError, TypeError):
                pass

        prob_rf = csv_row.get("prob_up_rf", "") or rf.get("prob_up", "")
        prob_lgb = csv_row.get("prob_up_lgb", "") or lgb.get("prob_up", "")

        disagreement = ""
        try:
            pr = float(prob_rf) if prob_rf else 0
            pl = float(prob_lgb) if prob_lgb else 0
            if pr and pl:
                disagreement = f"{abs(pr - pl):.1f}%"
        except (ValueError, TypeError):
            pass

        strength = ""
        try:
            prob = float(ensemble) if ensemble else 0
            if prob >= 70:
                strength = "强"
            elif prob >= 55:
                strength = "中"
            else:
                strength = "弱"
        except (ValueError, TypeError):
            pass

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
            level = "高分歧" if float(disagreement.replace("%", "")) > 15 else "低分歧"
            lines.append(f"| 模型分歧 | {disagreement} | {level} |")
        return "\n".join(lines)

    def _do_load_ml_factor(self, stock_code: str) -> str:
        mf_path = self.REALTIME_DIR / "ml_signal.json"
        if not mf_path.exists():
            return ""

        try:
            raw = json.loads(mf_path.read_text(encoding="utf-8"))
            mf_stock = raw.get("stocks", {}).get(stock_code, {})
            if not mf_stock:
                return ""

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
                sorted_f = sorted(
                    factors.items(),
                    key=lambda x: abs(x[1] if isinstance(x[1], (int, float)) else 0),
                    reverse=True,
                )[:3]
                top3 = " | ".join(f"{k}={v}" for k, v in sorted_f)
                parts.append(f"前三因子: {top3}")
            return " | ".join(parts) if parts else ""
        except Exception:
            return ""

    def _do_load_at_signal(self, stock_code: str) -> str:
        return ""
