"""
===================================
定时调度模块
===================================

职责：
1. 支持每日定时执行股票分析
2. 支持定时执行大盘复盘
3. 支持多时点多任务注册（每日/每周）
4. 优雅处理信号，确保可靠退出

依赖：
- schedule: 轻量级定时任务库
"""

import logging
import re
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """
    优雅退出处理器

    捕获 SIGTERM/SIGINT 信号，确保任务完成后再退出
    """

    def __init__(self):
        self.shutdown_requested = False
        self._lock = threading.Lock()

        # 注册信号处理器
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """信号处理函数"""
        with self._lock:
            if not self.shutdown_requested:
                logger.info(f"收到退出信号 ({signum})，等待当前任务完成...")
                self.shutdown_requested = True

    @property
    def should_shutdown(self) -> bool:
        """检查是否应该退出"""
        with self._lock:
            return self.shutdown_requested


_DAYS_OF_WEEK = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})


class Scheduler:
    """
    定时任务调度器

    基于 schedule 库实现，支持：
    - 每日定时执行
    - 启动时立即执行
    - 多时点多任务注册（每日/每周）
    - 优雅退出
    """

    def __init__(
        self,
        schedule_time: str = "18:00",
        schedule_time_provider: Callable[[], str] | None = None,
    ):
        """
        初始化调度器

        Args:
            schedule_time: 每日执行时间，格式 "HH:MM"
        """
        try:
            import schedule

            self.schedule = schedule
        except ImportError:
            logger.error("schedule 库未安装，请执行: pip install schedule")
            raise ImportError("请安装 schedule 库: pip install schedule")

        self.schedule_time = schedule_time
        self._schedule_time_provider = schedule_time_provider
        self.shutdown_handler = GracefulShutdown()
        self._task_callback: Callable | None = None
        self._daily_job: Any | None = None
        self._background_tasks: list[dict[str, Any]] = []
        self._running = False
        self._named_tasks: list[dict[str, Any]] = []  # registered multi-timed tasks

    def set_daily_task(self, task: Callable, run_immediately: bool = True):
        """
        设置每日定时任务（主任务）

        Args:
            task: 要执行的任务函数（无参数）
            run_immediately: 是否在设置后立即执行一次
        """
        self._task_callback = task
        if not self._configure_daily_task(self.schedule_time):
            raise ValueError(f"无效的定时执行时间: {self.schedule_time!r}")

        if run_immediately:
            logger.info("立即执行一次任务...")
            self._safe_run_task()

    def add_daily_task(
        self,
        task: Callable,
        schedule_time: str,
        task_name: str = "unnamed_daily",
    ) -> None:
        """
        注册一个每日定时任务（支持多时点多任务）。

        Args:
            task: 无参数的可调用对象。
            schedule_time: 执行时间，格式 "HH:MM"（24 小时制）。
            task_name: 用于日志记录的人类可读名称。
        """
        candidate = (schedule_time or "").strip()
        if not self._is_valid_schedule_time(candidate):
            logger.warning("无效的定时时间 %r，跳过任务 %s", schedule_time, task_name)
            return
        self.schedule.every().day.at(candidate).do(
            self._safe_run_named_task, task, task_name
        )
        self._named_tasks.append({"name": task_name, "time": candidate, "type": "daily"})
        logger.info("已注册每日定时任务: %s @ %s", task_name, candidate)

    def add_weekly_task(
        self,
        task: Callable,
        schedule_time: str,
        day_of_week: str = "sunday",
        task_name: str = "unnamed_weekly",
    ) -> None:
        """
        注册一个每周定时任务。

        Args:
            task: 无参数的可调用对象。
            schedule_time: 执行时间，格式 "HH:MM"（24 小时制）。
            day_of_week: 星期几（monday/tuesday/wednesday/thursday/friday/saturday/sunday）。
            task_name: 用于日志记录的人类可读名称。
        """
        candidate = (schedule_time or "").strip()
        if not self._is_valid_schedule_time(candidate):
            logger.warning("无效的定时时间 %r，跳过任务 %s", schedule_time, task_name)
            return
        dow = day_of_week.lower()
        if dow not in _DAYS_OF_WEEK:
            logger.warning("无效的星期 %r，跳过任务 %s", day_of_week, task_name)
            return
        getattr(self.schedule.every(), dow).at(candidate).do(
            self._safe_run_named_task, task, task_name
        )
        self._named_tasks.append({"name": task_name, "time": candidate, "type": f"weekly/{dow}"})
        logger.info("已注册每周定时任务: %s @ %s %s", task_name, day_of_week, candidate)

    def _safe_run_named_task(self, task: Callable, task_name: str) -> None:
        """安全执行指定名称的定时任务（带异常捕获与日志记录）。"""
        try:
            logger.info("[%s] 开始执行 - %s", task_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            task()
            logger.info("[%s] 执行完成", task_name)
        except Exception as e:
            logger.exception("[%s] 执行失败: %s", task_name, e)

    @staticmethod
    def _is_valid_schedule_time(schedule_time: str) -> bool:
        """Validate time string in HH:MM 24-hour format."""
        candidate = (schedule_time or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", candidate):
            return False
        return True

    def _cancel_daily_job(self) -> None:
        """Remove the currently registered daily job if one exists."""
        if self._daily_job is None:
            return

        if hasattr(self.schedule, "cancel_job"):
            self.schedule.cancel_job(self._daily_job)
        else:  # pragma: no cover - compatibility fallback
            jobs = getattr(self.schedule, "jobs", None)
            if isinstance(jobs, list) and self._daily_job in jobs:
                jobs.remove(self._daily_job)

        self._daily_job = None

    def _configure_daily_task(self, schedule_time: str) -> bool:
        """(Re)register the daily job at the requested time."""
        candidate = (schedule_time or "").strip()
        if not self._is_valid_schedule_time(candidate):
            logger.warning(
                "检测到无效的定时执行时间 %r，继续沿用当前时间 %s",
                schedule_time,
                self.schedule_time,
            )
            return False

        previous_time = self.schedule_time
        self._cancel_daily_job()
        self._daily_job = self.schedule.every().day.at(candidate).do(self._safe_run_task)
        self.schedule_time = candidate

        if previous_time == candidate:
            logger.info("已设置每日定时任务，执行时间: %s", self.schedule_time)
        else:
            logger.info(
                "检测到 SCHEDULE_TIME 变更，已将每日定时任务从 %s 更新为 %s",
                previous_time,
                self.schedule_time,
            )
        return True

    def _refresh_daily_schedule_if_needed(self) -> None:
        """Reload daily schedule time from the latest runtime config if needed."""
        if self._task_callback is None or self._schedule_time_provider is None:
            return

        try:
            latest_schedule_time = (self._schedule_time_provider() or "").strip()
        except Exception as exc:  # pragma: no cover - defensive branch
            logger.warning("读取最新 SCHEDULE_TIME 失败，继续沿用 %s: %s", self.schedule_time, exc)
            return

        if not latest_schedule_time or latest_schedule_time == self.schedule_time:
            return

        if self._configure_daily_task(latest_schedule_time):
            logger.info("更新后的下次执行时间: %s", self._get_next_run_time())

    def _safe_run_task(self):
        """安全执行主任务（带异常捕获）"""
        if self._task_callback is None:
            return

        try:
            logger.info("=" * 50)
            logger.info(f"主定时任务开始执行 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("=" * 50)

            self._task_callback()

            logger.info(f"主定时任务执行完成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        except Exception as e:
            logger.exception(f"主定时任务执行失败: {e}")

    def add_background_task(
        self,
        task: Callable,
        interval_seconds: int,
        run_immediately: bool = False,
        name: str | None = None,
    ) -> None:
        """Register a periodic background task executed inside the scheduler loop.

        Note: The scheduler loop polls every 30 seconds, so *interval_seconds*
        below 30 will be clamped to 30 to avoid promising unreachable precision.
        """
        clamped_interval = max(30, int(interval_seconds))
        if int(interval_seconds) < 30:
            logger.warning(
                "后台任务 %s 请求间隔 %ds，但调度循环每 30s 轮询一次，已自动调整为 30s",
                name or getattr(task, "__name__", "background_task"),
                interval_seconds,
            )
        entry = {
            "task": task,
            "interval_seconds": clamped_interval,
            "last_run": 0.0,
            "name": name or getattr(task, "__name__", "background_task"),
            "thread": None,
            "running": False,
        }
        if not run_immediately:
            entry["last_run"] = time.time()
        self._background_tasks.append(entry)
        logger.info(
            "已注册后台任务: %s（间隔 %s 秒，立即执行=%s）",
            entry["name"],
            entry["interval_seconds"],
            run_immediately,
        )
        if run_immediately:
            self._start_background_task(entry)

    def _start_background_task(self, entry: dict[str, Any]) -> bool:
        """Start one background task in a dedicated daemon thread."""
        worker = entry.get("thread")
        if worker is not None and worker.is_alive():
            return False

        def _runner() -> None:
            try:
                logger.info("后台任务开始执行: %s", entry["name"])
                entry["task"]()
            except Exception as exc:
                logger.exception("后台任务执行失败 [%s]: %s", entry["name"], exc)
            finally:
                entry["running"] = False
                entry["thread"] = None

        entry["last_run"] = time.time()
        entry["running"] = True
        worker = threading.Thread(
            target=_runner,
            daemon=True,
            name=f"scheduler-bg-{entry['name']}",
        )
        entry["thread"] = worker
        worker.start()
        return True

    def _run_background_tasks(self) -> None:
        """Execute any background tasks whose interval has elapsed."""
        if not self._background_tasks:
            return

        now = time.time()
        for entry in self._background_tasks:
            worker = entry.get("thread")
            if worker is not None and worker.is_alive():
                continue
            if entry.get("running"):
                entry["running"] = False
                entry["thread"] = None
            if now - entry["last_run"] < entry["interval_seconds"]:
                continue
            self._start_background_task(entry)

    def run(self):
        """
        运行调度器主循环

        阻塞运行，直到收到退出信号
        """
        self._running = True
        logger.info("调度器开始运行...")
        logger.info(f"下次执行时间: {self._get_next_run_time()}")

        while self._running and not self.shutdown_handler.should_shutdown:
            self._refresh_daily_schedule_if_needed()
            self.schedule.run_pending()
            self._run_background_tasks()
            time.sleep(30)  # 每30秒检查一次

            # 每小时打印一次心跳
            if datetime.now().minute == 0 and datetime.now().second < 30:
                logger.info(f"调度器运行中... 下次执行: {self._get_next_run_time()}")

        logger.info("调度器已停止")

    def _get_next_run_time(self) -> str:
        """获取下次执行时间"""
        jobs = self.schedule.get_jobs()
        if jobs:
            next_times = [job.next_run for job in jobs if job.next_run is not None]
            if next_times:
                next_run = min(next_times)
                return next_run.strftime("%Y-%m-%d %H:%M:%S")
        return "未设置"

    def stop(self):
        """停止调度器"""
        self._running = False


def run_with_schedule(
    task: Callable,
    schedule_time: str = "18:00",
    run_immediately: bool = True,
    background_tasks: list[dict[str, Any]] | None = None,
    schedule_time_provider: Callable[[], str] | None = None,
    additional_daily_tasks: list[dict[str, Any]] | None = None,
    additional_weekly_tasks: list[dict[str, Any]] | None = None,
):
    """
    便捷函数：使用定时调度运行任务

    Args:
        task: 要执行的任务函数
        schedule_time: 每日执行时间
        run_immediately: 是否立即执行一次
        background_tasks: 可选的后台任务定义列表。每项为一个字典，
            需包含 `task` 与 `interval_seconds`，可选包含 `name`
            和 `run_immediately`。`interval_seconds` 单位为秒。
        schedule_time_provider: 可选的时间提供器；调度器每轮检查前会读取，
            当返回值变化时自动重建 daily job。
        additional_daily_tasks: 额外的每日多时点任务列表。
            每项：{"task": Callable, "time": "HH:MM", "name": str}
        additional_weekly_tasks: 每周定时任务列表。
            每项：{"task": Callable, "time": "HH:MM", "day": str, "name": str}
    """
    scheduler = Scheduler(
        schedule_time=schedule_time,
        schedule_time_provider=schedule_time_provider,
    )
    for entry in background_tasks or []:
        scheduler.add_background_task(
            task=entry["task"],
            interval_seconds=entry["interval_seconds"],
            run_immediately=entry.get("run_immediately", False),
            name=entry.get("name"),
        )
    for entry in additional_daily_tasks or []:
        scheduler.add_daily_task(
            task=entry["task"],
            schedule_time=entry["time"],
            task_name=entry.get("name", "daily_extra"),
        )
    for entry in additional_weekly_tasks or []:
        scheduler.add_weekly_task(
            task=entry["task"],
            schedule_time=entry["time"],
            day_of_week=entry.get("day", "sunday"),
            task_name=entry.get("name", "weekly_extra"),
        )
    scheduler.set_daily_task(task, run_immediately=run_immediately)
    scheduler.run()


if __name__ == "__main__":
    # 测试定时调度
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    )

    def test_task():
        print(f"任务执行中... {datetime.now()}")
        time.sleep(2)
        print("任务完成!")

    print("启动测试调度器（按 Ctrl+C 退出）")
    run_with_schedule(test_task, schedule_time="23:59", run_immediately=True)
