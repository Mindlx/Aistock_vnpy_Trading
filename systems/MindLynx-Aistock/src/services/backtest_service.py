"""Backtest orchestration service."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select

from src.config import get_config
from src.core.backtest_engine import OVERALL_SENTINEL_CODE, BacktestEngine, EvaluationConfig
from src.repositories.backtest_repo import BacktestRepository
from src.repositories.stock_repo import StockRepository
from src.storage import BacktestResult, BacktestSummary, DatabaseManager

logger = logging.getLogger(__name__)


class BacktestService:
    """Service layer to run and query backtests."""

    MAX_DYNAMIC_SUMMARY_ROWS = 2000

    def __init__(self, db_manager: DatabaseManager | None = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.repo = BacktestRepository(self.db)
        self.stock_repo = StockRepository(self.db)

    def run_backtest(
        self,
        *,
        code: str | None = None,
        force: bool = False,
        eval_window_days: int | None = None,
        min_age_days: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        config = get_config()

        if eval_window_days is None:
            eval_window_days = getattr(config, "backtest_eval_window_days", 10)
        if min_age_days is None:
            min_age_days = getattr(config, "backtest_min_age_days", 14)

        engine_version = getattr(config, "backtest_engine_version", "v1")
        neutral_band_pct = float(getattr(config, "backtest_neutral_band_pct", 2.0))

        eval_config = EvaluationConfig(
            eval_window_days=int(eval_window_days),
            neutral_band_pct=neutral_band_pct,
            engine_version=str(engine_version),
        )

        candidates = self.repo.get_candidates(
            code=code,
            min_age_days=int(min_age_days),
            limit=int(limit),
            eval_window_days=int(eval_window_days),
            engine_version=str(engine_version),
            force=force,
        )

        processed = 0
        completed = 0
        insufficient = 0
        errors = 0
        touched_codes: set[str] = set()

        results_to_save: list[BacktestResult] = []

        for analysis in candidates:
            processed += 1
            touched_codes.add(analysis.code)

            try:
                analysis_date = self._resolve_analysis_date(analysis)
                if analysis_date is None:
                    errors += 1
                    results_to_save.append(
                        BacktestResult(
                            analysis_history_id=analysis.id,
                            code=analysis.code,
                            eval_window_days=int(eval_window_days),
                            engine_version=str(engine_version),
                            eval_status="error",
                            evaluated_at=datetime.now(),
                            operation_advice=analysis.operation_advice,
                            skill_id=None,
                        )
                    )
                    continue
                start_daily = self.stock_repo.get_start_daily(code=analysis.code, analysis_date=analysis_date)

                if start_daily is None or start_daily.close is None:
                    self._try_fill_daily_data(
                        code=analysis.code, analysis_date=analysis_date, eval_window_days=eval_window_days
                    )
                    start_daily = self.stock_repo.get_start_daily(code=analysis.code, analysis_date=analysis_date)

                if start_daily is None or start_daily.close is None:
                    insufficient += 1
                    results_to_save.append(
                        BacktestResult(
                            analysis_history_id=analysis.id,
                            code=analysis.code,
                            analysis_date=analysis_date,
                            eval_window_days=int(eval_window_days),
                            engine_version=str(engine_version),
                            eval_status="insufficient_data",
                            evaluated_at=datetime.now(),
                            operation_advice=analysis.operation_advice,
                            skill_id=None,
                        )
                    )
                    continue

                forward_bars = self.stock_repo.get_forward_bars(
                    code=analysis.code,
                    analysis_date=start_daily.date,
                    eval_window_days=int(eval_window_days),
                )

                if len(forward_bars) < int(eval_window_days):
                    self._try_fill_daily_data(
                        code=analysis.code, analysis_date=start_daily.date, eval_window_days=eval_window_days
                    )
                    forward_bars = self.stock_repo.get_forward_bars(
                        code=analysis.code,
                        analysis_date=start_daily.date,
                        eval_window_days=int(eval_window_days),
                    )

                evaluation = BacktestEngine.evaluate_single(
                    operation_advice=analysis.operation_advice,
                    sentiment_score=getattr(analysis, "sentiment_score", None),
                    analysis_date=start_daily.date,
                    start_price=float(start_daily.close),
                    forward_bars=forward_bars,
                    stop_loss=analysis.stop_loss,
                    take_profit=analysis.take_profit,
                    config=eval_config,
                )

                status = evaluation.get("eval_status")
                if status == "insufficient_data":
                    insufficient += 1
                elif status == "completed":
                    completed += 1
                else:
                    errors += 1

                results_to_save.append(
                    BacktestResult(
                        analysis_history_id=analysis.id,
                        code=analysis.code,
                        analysis_date=evaluation.get("analysis_date"),
                        eval_window_days=int(evaluation.get("eval_window_days") or eval_window_days),
                        engine_version=str(evaluation.get("engine_version") or engine_version),
                        eval_status=str(evaluation.get("eval_status") or "error"),
                        evaluated_at=datetime.now(),
                        operation_advice=evaluation.get("operation_advice"),
                        sentiment_score=evaluation.get("sentiment_score"),
                        sentiment_direction_correct=evaluation.get("sentiment_direction_correct"),
                        position_recommendation=evaluation.get("position_recommendation"),
                        skill_id=None,
                        start_price=evaluation.get("start_price"),
                        end_close=evaluation.get("end_close"),
                        max_high=evaluation.get("max_high"),
                        min_low=evaluation.get("min_low"),
                        stock_return_pct=evaluation.get("stock_return_pct"),
                        direction_expected=evaluation.get("direction_expected"),
                        direction_correct=evaluation.get("direction_correct"),
                        outcome=evaluation.get("outcome"),
                        stop_loss=evaluation.get("stop_loss"),
                        take_profit=evaluation.get("take_profit"),
                        hit_stop_loss=evaluation.get("hit_stop_loss"),
                        hit_take_profit=evaluation.get("hit_take_profit"),
                        first_hit=evaluation.get("first_hit"),
                        first_hit_date=evaluation.get("first_hit_date"),
                        first_hit_trading_days=evaluation.get("first_hit_trading_days"),
                        simulated_entry_price=evaluation.get("simulated_entry_price"),
                        simulated_exit_price=evaluation.get("simulated_exit_price"),
                        simulated_exit_reason=evaluation.get("simulated_exit_reason"),
                        simulated_return_pct=evaluation.get("simulated_return_pct"),
                    )
                )

            except Exception as exc:
                errors += 1
                logger.error(f"回测失败: {analysis.code}#{analysis.id}: {exc}")
                results_to_save.append(
                    BacktestResult(
                        analysis_history_id=analysis.id,
                        code=analysis.code,
                        analysis_date=self._resolve_analysis_date(analysis),
                        eval_window_days=int(eval_window_days),
                        engine_version=str(engine_version),
                        eval_status="error",
                        evaluated_at=datetime.now(),
                        operation_advice=analysis.operation_advice,
                        skill_id=None,
                    )
                )

        saved = 0
        if results_to_save:
            saved = self.repo.save_results_batch(results_to_save, replace_existing=force)

        if saved:
            self._recompute_summaries(
                touched_codes=sorted(touched_codes),
                eval_window_days=int(eval_window_days),
                engine_version=str(engine_version),
            )

            # Auto-generate overall backtest report
            try:
                from src.core.backtest_report import BacktestReportGenerator

                gen = BacktestReportGenerator()
                overall_summary_data = self.get_summary(
                    scope="overall",
                    code=None,
                    eval_window_days=int(eval_window_days or 10),
                )
                if overall_summary_data:
                    gen.generate(
                        overall_summary_data,
                        strategy_name="Auto Backtest",
                        stock_code=code if code and code != OVERALL_SENTINEL_CODE else None,
                        initial_capital=100000.0,
                    )
            except Exception as rpt_exc:
                logger.warning("自动生成回测报告失败（已忽略）: %s", rpt_exc)

        return {
            "processed": processed,
            "saved": saved,
            "completed": completed,
            "insufficient": insufficient,
            "errors": errors,
        }

    def get_recent_evaluations(
        self,
        *,
        code: str | None,
        eval_window_days: int | None = None,
        limit: int = 50,
        page: int = 1,
        analysis_date_from: date | None = None,
        analysis_date_to: date | None = None,
    ) -> dict[str, Any]:
        config = get_config()
        engine_version = str(getattr(config, "backtest_engine_version", "v1"))

        # When date filters are active and no explicit window is requested,
        # infer the smallest available window to stay aligned with summary metrics.
        if eval_window_days is None and (analysis_date_from is not None or analysis_date_to is not None):
            windows = self.repo.get_distinct_eval_windows(
                code=code,
                engine_version=engine_version,
                analysis_date_from=analysis_date_from,
                analysis_date_to=analysis_date_to,
            )
            if windows:
                eval_window_days = windows[0]

        offset = max(page - 1, 0) * limit
        rows, total = self.repo.get_results_paginated(
            code=code,
            eval_window_days=eval_window_days,
            engine_version=engine_version,
            analysis_date_from=analysis_date_from,
            analysis_date_to=analysis_date_to,
            days=None,
            offset=offset,
            limit=limit,
        )
        items = [
            self._result_to_dict(result, stock_name, trend_prediction)
            for result, stock_name, trend_prediction, _ in rows
        ]
        return {"total": total, "page": page, "limit": limit, "items": items}

    def get_summary(
        self,
        *,
        scope: str,
        code: str | None,
        eval_window_days: int | None = None,
        analysis_date_from: date | None = None,
        analysis_date_to: date | None = None,
    ) -> dict[str, Any] | None:
        config = get_config()
        engine_version = str(getattr(config, "backtest_engine_version", "v1"))
        lookup_code = OVERALL_SENTINEL_CODE if scope == "overall" else code

        if analysis_date_from is not None or analysis_date_to is not None:
            ew = int(eval_window_days) if eval_window_days is not None else None
            count = self.repo.count_results(
                code=code,
                eval_window_days=ew,
                engine_version=engine_version,
                analysis_date_from=analysis_date_from,
                analysis_date_to=analysis_date_to,
            )
            if count > self.MAX_DYNAMIC_SUMMARY_ROWS:
                raise ValueError(
                    "Date-filtered summary matches too many rows; narrow the analysis date range or stock code."
                )
            rows = self.repo.list_results(
                code=code,
                eval_window_days=ew,
                engine_version=engine_version,
                analysis_date_from=analysis_date_from,
                analysis_date_to=analysis_date_to,
            )
            return self._build_dynamic_summary(
                rows=rows,
                scope=scope,
                code=lookup_code,
                eval_window_days=int(eval_window_days) if eval_window_days is not None else None,
                engine_version=engine_version,
                max_rows=self.MAX_DYNAMIC_SUMMARY_ROWS,
            )

        summary = self.repo.get_summary(
            scope=scope,
            code=lookup_code,
            eval_window_days=eval_window_days,
            engine_version=engine_version,
        )
        if summary is None:
            return None
        return self._summary_to_dict(summary)

    def get_global_summary(self, *, eval_window_days: int | None = None) -> dict[str, Any] | None:
        """Return overall backtest metrics normalized for Agent memory consumers."""
        return self._normalize_learning_summary(
            self.get_summary(scope="overall", code=None, eval_window_days=eval_window_days)
        )

    def get_stock_summary(self, code: str, *, eval_window_days: int | None = None) -> dict[str, Any] | None:
        """Return per-stock backtest metrics normalized for Agent memory consumers."""
        return self._normalize_learning_summary(
            self.get_summary(scope="stock", code=code, eval_window_days=eval_window_days)
        )

    def get_skill_summary(self, skill_id: str, *, eval_window_days: int | None = None) -> dict[str, Any] | None:
        """Return skill-scoped backtest metrics for Agent memory consumers.

        Queries BacktestSummary with scope='skill' and the given skill_id.
        Falls back to None when no skill-tagged data exists yet, so downstream
        callers use neutral weighting until data accumulates.
        """
        config = get_config()
        engine_version = str(getattr(config, "backtest_engine_version", "v1"))
        ew = eval_window_days

        summary = self.repo.get_summary(
            scope="skill",
            code=OVERALL_SENTINEL_CODE,
            skill_id=skill_id,
            eval_window_days=ew,
            engine_version=engine_version,
        )
        if summary is None:
            return None
        return self._normalize_learning_summary(self._summary_to_dict(summary))

    def get_strategy_summary(self, strategy_id: str, *, eval_window_days: int | None = None) -> dict[str, Any] | None:
        """Compatibility wrapper for legacy strategy-based callers."""
        summary = self.get_skill_summary(strategy_id, eval_window_days=eval_window_days)
        if summary is None:
            return None
        normalized = dict(summary)
        normalized["strategy_id"] = strategy_id
        return normalized

    def _resolve_analysis_date(self, analysis) -> date | None:
        parsed = self.repo.parse_analysis_date_from_snapshot(analysis.context_snapshot)
        if parsed:
            return parsed
        if getattr(analysis, "created_at", None):
            return analysis.created_at.date()
        logger.warning(f"无法确定分析日期，跳过记录: {analysis.code}#{getattr(analysis, 'id', '?')}")
        return None

    def _try_fill_daily_data(self, *, code: str, analysis_date: date, eval_window_days: int) -> None:
        try:
            from data_provider.base import DataFetcherManager

            # fetch a window that covers start + forward bars
            end_date = analysis_date + timedelta(days=max(eval_window_days * 2, 30))
            manager = DataFetcherManager()
            df, source = manager.get_daily_data(
                stock_code=code,
                start_date=analysis_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                days=eval_window_days * 2,
            )
            if df is None or df.empty:
                return
            self.db.save_daily_data(df, code=code, data_source=source)
        except Exception as exc:
            logger.warning(f"补全日线数据失败({code}): {exc}")

    def _recompute_summaries(self, *, touched_codes: list[str], eval_window_days: int, engine_version: str) -> None:
        with self.db.get_session() as session:
            # overall
            overall_rows = (
                session.execute(
                    select(BacktestResult).where(
                        and_(
                            BacktestResult.eval_window_days == eval_window_days,
                            BacktestResult.engine_version == engine_version,
                        )
                    )
                )
                .scalars()
                .all()
            )
            overall_data = BacktestEngine.compute_summary(
                results=overall_rows,
                scope="overall",
                code=OVERALL_SENTINEL_CODE,
                eval_window_days=eval_window_days,
                engine_version=engine_version,
            )
            overall_summary = self._build_summary_model(overall_data)
            self.repo.upsert_summary(overall_summary)

            for code in touched_codes:
                rows = (
                    session.execute(
                        select(BacktestResult).where(
                            and_(
                                BacktestResult.code == code,
                                BacktestResult.eval_window_days == eval_window_days,
                                BacktestResult.engine_version == engine_version,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                data = BacktestEngine.compute_summary(
                    results=rows,
                    scope="stock",
                    code=code,
                    eval_window_days=eval_window_days,
                    engine_version=engine_version,
                )
                summary = self._build_summary_model(data)
                self.repo.upsert_summary(summary)

            # skill-level summaries
            skill_ids = set()
            for row in overall_rows:
                if getattr(row, "skill_id", None):
                    skill_ids.add(row.skill_id)
            for skill_id in skill_ids:
                skill_rows = [r for r in overall_rows if getattr(r, "skill_id", None) == skill_id]
                if not skill_rows:
                    continue
                skill_data = BacktestEngine.compute_summary(
                    results=skill_rows,
                    scope="skill",
                    code=OVERALL_SENTINEL_CODE,
                    eval_window_days=eval_window_days,
                    engine_version=engine_version,
                )
                skill_data["skill_id"] = skill_id
                skill_summary = self._build_summary_model(skill_data)
                self.repo.upsert_summary(skill_summary)

    @staticmethod
    def _build_summary_model(summary_data: dict[str, Any]) -> BacktestSummary:
        return BacktestSummary(
            scope=summary_data.get("scope"),
            code=summary_data.get("code"),
            skill_id=summary_data.get("skill_id"),
            eval_window_days=summary_data.get("eval_window_days"),
            engine_version=summary_data.get("engine_version"),
            computed_at=datetime.now(),
            total_evaluations=summary_data.get("total_evaluations") or 0,
            completed_count=summary_data.get("completed_count") or 0,
            insufficient_count=summary_data.get("insufficient_count") or 0,
            long_count=summary_data.get("long_count") or 0,
            cash_count=summary_data.get("cash_count") or 0,
            win_count=summary_data.get("win_count") or 0,
            loss_count=summary_data.get("loss_count") or 0,
            neutral_count=summary_data.get("neutral_count") or 0,
            direction_accuracy_pct=summary_data.get("direction_accuracy_pct"),
            sentiment_direction_accuracy_pct=summary_data.get("sentiment_direction_accuracy_pct"),
            win_rate_pct=summary_data.get("win_rate_pct"),
            neutral_rate_pct=summary_data.get("neutral_rate_pct"),
            avg_stock_return_pct=summary_data.get("avg_stock_return_pct"),
            avg_simulated_return_pct=summary_data.get("avg_simulated_return_pct"),
            stop_loss_trigger_rate=summary_data.get("stop_loss_trigger_rate"),
            take_profit_trigger_rate=summary_data.get("take_profit_trigger_rate"),
            ambiguous_rate=summary_data.get("ambiguous_rate"),
            avg_days_to_first_hit=summary_data.get("avg_days_to_first_hit"),
            advice_breakdown_json=json.dumps(summary_data.get("advice_breakdown") or {}, ensure_ascii=False),
            diagnostics_json=json.dumps(summary_data.get("diagnostics") or {}, ensure_ascii=False),
        )

    @staticmethod
    def _result_to_dict(
        row: BacktestResult,
        stock_name: str | None = None,
        trend_prediction: str | None = None,
    ) -> dict[str, Any]:
        return {
            "analysis_history_id": row.analysis_history_id,
            "code": row.code,
            "stock_name": stock_name,
            "analysis_date": row.analysis_date.isoformat() if row.analysis_date else None,
            "eval_window_days": row.eval_window_days,
            "engine_version": row.engine_version,
            "eval_status": row.eval_status,
            "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
            "operation_advice": row.operation_advice,
            "trend_prediction": trend_prediction,
            "position_recommendation": row.position_recommendation,
            "start_price": row.start_price,
            "end_close": row.end_close,
            "max_high": row.max_high,
            "min_low": row.min_low,
            "stock_return_pct": row.stock_return_pct,
            "actual_return_pct": row.stock_return_pct,
            "actual_movement": BacktestService._actual_movement_from_return(row.stock_return_pct),
            "direction_expected": row.direction_expected,
            "direction_correct": row.direction_correct,
            "outcome": row.outcome,
            "stop_loss": row.stop_loss,
            "take_profit": row.take_profit,
            "hit_stop_loss": row.hit_stop_loss,
            "hit_take_profit": row.hit_take_profit,
            "first_hit": row.first_hit,
            "first_hit_date": row.first_hit_date.isoformat() if row.first_hit_date else None,
            "first_hit_trading_days": row.first_hit_trading_days,
            "simulated_entry_price": row.simulated_entry_price,
            "simulated_exit_price": row.simulated_exit_price,
            "simulated_exit_reason": row.simulated_exit_reason,
            "simulated_return_pct": row.simulated_return_pct,
        }

    @staticmethod
    def _summary_to_dict(row: BacktestSummary) -> dict[str, Any]:
        return {
            "scope": row.scope,
            "code": None if row.code == OVERALL_SENTINEL_CODE else row.code,
            "eval_window_days": row.eval_window_days,
            "engine_version": row.engine_version,
            "computed_at": row.computed_at.isoformat() if row.computed_at else None,
            "total_evaluations": row.total_evaluations,
            "completed_count": row.completed_count,
            "insufficient_count": row.insufficient_count,
            "long_count": row.long_count,
            "cash_count": row.cash_count,
            "win_count": row.win_count,
            "loss_count": row.loss_count,
            "neutral_count": row.neutral_count,
            "direction_accuracy_pct": row.direction_accuracy_pct,
            "sentiment_direction_accuracy_pct": row.sentiment_direction_accuracy_pct,
            "win_rate_pct": row.win_rate_pct,
            "neutral_rate_pct": row.neutral_rate_pct,
            "avg_stock_return_pct": row.avg_stock_return_pct,
            "avg_simulated_return_pct": row.avg_simulated_return_pct,
            "stop_loss_trigger_rate": row.stop_loss_trigger_rate,
            "take_profit_trigger_rate": row.take_profit_trigger_rate,
            "ambiguous_rate": row.ambiguous_rate,
            "avg_days_to_first_hit": row.avg_days_to_first_hit,
            "advice_breakdown": json.loads(row.advice_breakdown_json) if row.advice_breakdown_json else {},
            "diagnostics": json.loads(row.diagnostics_json) if row.diagnostics_json else {},
        }

    @staticmethod
    def _normalize_learning_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
        """Normalize summary metrics to the ratio-based shape expected by Agent memory."""
        if summary is None:
            return None

        normalized = dict(summary)
        normalized["win_rate"] = BacktestService._pct_to_ratio(summary.get("win_rate_pct"), default=0.5)
        normalized["direction_accuracy"] = BacktestService._pct_to_ratio(
            summary.get("direction_accuracy_pct"),
            default=0.5,
        )

        avg_return_pct = summary.get("avg_simulated_return_pct")
        if avg_return_pct is None:
            avg_return_pct = summary.get("avg_stock_return_pct")
        normalized["avg_return"] = BacktestService._pct_to_ratio(avg_return_pct, default=0.0)
        return normalized

    @staticmethod
    def _pct_to_ratio(value: float | None, default: float = 0.0) -> float:
        try:
            return float(value) / 100.0
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _actual_movement_from_return(value: float | None) -> str | None:
        if value is None:
            return None
        try:
            actual_return = float(value)
        except (TypeError, ValueError):
            return None
        if actual_return > 0:
            return "up"
        if actual_return < 0:
            return "down"
        return "flat"

    @staticmethod
    def _build_dynamic_summary(
        *,
        rows: list[BacktestResult],
        scope: str,
        code: str | None,
        eval_window_days: int | None,
        engine_version: str,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        filtered_rows = [row for row in rows if getattr(row, "engine_version", None) == engine_version]
        if eval_window_days is not None:
            summary_window_days = int(eval_window_days)
        else:
            window_values = sorted(
                {
                    int(row.eval_window_days)
                    for row in filtered_rows
                    if getattr(row, "eval_window_days", None) is not None
                }
            )
            if len(window_values) > 1:
                logger.warning(
                    "Multiple eval_window_days values found for dynamic summary; using %s for engine_version=%s, scope=%s, code=%s",
                    window_values[0],
                    engine_version,
                    scope,
                    code,
                )
            if window_values:
                summary_window_days = window_values[0]
            else:
                summary_window_days = int(getattr(get_config(), "backtest_eval_window_days", 10))

        filtered_rows = [row for row in filtered_rows if getattr(row, "eval_window_days", None) == summary_window_days]

        if max_rows is not None and len(filtered_rows) > max_rows:
            raise ValueError(
                "Date-filtered summary matches too many rows; narrow the analysis date range or stock code."
            )

        summary = BacktestEngine.compute_summary(
            results=filtered_rows,
            scope=scope,
            code=code,
            eval_window_days=summary_window_days,
            engine_version=engine_version,
        )
        summary["code"] = None if summary.get("code") == OVERALL_SENTINEL_CODE else summary.get("code")
        summary["computed_at"] = datetime.now().isoformat()
        return summary
