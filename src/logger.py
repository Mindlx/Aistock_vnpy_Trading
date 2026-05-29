"""
融合系统日志与历史记录模块

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


class FusionLogger:
    """融合系统日志记录器"""

    def __init__(self, log_dir: str = "config/logs", retention_days: int = 90):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days

        # 配置标准 logging
        self.logger = logging.getLogger("fusion")
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)
            # 文件 handler
            fh = logging.FileHandler(
                self.log_dir / f"fusion_{datetime.now().strftime('%Y%m%d')}.log",
                encoding="utf-8",
            )
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s"
            ))
            self.logger.addHandler(fh)
            # 控制台 handler
            ch = logging.StreamHandler()
            ch.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s"
            ))
            self.logger.addHandler(ch)

    def info(self, msg: str):
        self.logger.info(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)

    def error(self, msg: str):
        self.logger.error(msg)

    def record_decision(
        self,
        stock_code: str,
        stock_name: str,
        lynx_score: float,
        lynx_valid: bool,
        mindlynx_score: float,
        tradingagent_score: float,
        fusion_score: float,
        final_signal: str,
        position_advice: str,
        fusion_output_dir: Optional[str] = None,
    ):
        """记录单次融合决策到 CSV"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. 写日志
        self.logger.info(
            f"[{stock_code}] {stock_name} | "
            f"lynx={lynx_score:.3f}({'有效' if lynx_valid else '无效'}) "
            f"mindlynx={mindlynx_score:.3f} "
            f"tradingagent={tradingagent_score:.3f} | "
            f"融合={fusion_score:.3f} → {final_signal} ({position_advice})"
        )

        # 2. 写 CSV 历史记录
        csv_path = self.log_dir / "fusion_history.csv"
        is_new = not csv_path.exists()

        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow([
                    "timestamp", "stock_code", "stock_name",
                    "lynx_score", "lynx_valid",
                    "mindlynx_score", "tradingagent_score",
                    "fusion_score", "final_signal", "position_advice",
                ])
            writer.writerow([
                timestamp, stock_code, stock_name,
                f"{lynx_score:.3f}", lynx_valid,
                f"{mindlynx_score:.3f}", f"{tradingagent_score:.3f}",
                f"{fusion_score:.3f}", final_signal, position_advice,
            ])

        # 3. 清理过期日志
        self._cleanup_old_logs()

    def record_daily_summary(
        self,
        date: str,
        results: List[Dict[str, Any]],
        fusion_output_dir: Optional[str] = None,
    ):
        """记录每日融合结果摘要（JSON）"""
        if fusion_output_dir is None:
            fusion_output_dir = str(self.log_dir)

        output_dir = Path(fusion_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "date": date,
            "total_stocks": len(results),
            "results": results,
            "generated_at": datetime.now().isoformat(),
        }

        # 保存 JSON
        json_path = output_dir / f"fusion_{date}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        self.logger.info(f"每日摘要已保存: {json_path} ({len(results)} 只股票)")

    def _cleanup_old_logs(self):
        """删除过期日志文件"""
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        for f in self.log_dir.glob("fusion_*.log"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
                    self.logger.debug(f"清除过期日志: {f}")
            except OSError:
                pass
