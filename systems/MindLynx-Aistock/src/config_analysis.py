from dataclasses import dataclass, field

@dataclass
class AnalysisConfig:
    """Analysis & backtest configuration fields extracted from Config dataclass."""
    bias_threshold: float = 5.0  # 乖离率阈值（%），超过此值提示不追高
    event_monitor_enabled: bool = False  # 启用事件驱动分析（公告+互动易）
    event_monitor_check_interval: int = 300  # 检查间隔（秒）
    event_monitor_importance_threshold: int = 7  # 重要性≥此值自动重新分析
    event_monitor_stock_codes: list[str] = field(default_factory=list)  # 监控的股票列表（留空使用 stock_list）
    report_type: str = "simple"
    report_language: str = "zh"
    report_summary_only: bool = False
    report_show_llm_model: bool = True
    report_templates_dir: str = "templates"  # Template directory (relative to project root)
    report_renderer_enabled: bool = False  # Enable Jinja2 rendering (default off for zero regression)
    report_integrity_enabled: bool = True  # Content integrity validation after LLM output
    report_integrity_retry: int = 1  # Retry count when mandatory fields missing (0 = placeholder only)
    report_history_compare_n: int = 0  # History comparison count (0 = disabled)
    backtest_enabled: bool = True
    backtest_eval_window_days: int = 10
    backtest_min_age_days: int = 14
    backtest_engine_version: str = "v1"
    backtest_neutral_band_pct: float = 2.0
    backtest_report_enabled: bool = True
    backtest_report_output_dir: str = "reports/backtest/"
    market_review_enabled: bool = True  # 是否启用大盘复盘
    market_review_region: str = "cn"
    market_review_color_scheme: str = "green_up"
    trading_day_check_enabled: bool = True
    factor_monitor_enabled: bool = True  # 是否启用因子表现监控
    factor_monitor_output_dir: str = "reports/factors/"  # IC/IR 报告输出目录
    realtime_monitor_enabled: bool = False  # 是否启用盘中实时监控
    realtime_monitor_daemon_enabled: bool = False  # 是否启用守护进程模式（自动跟随A股交易时段）
    realtime_monitor_briefing_interval: int = 900  # 简报间隔（秒），默认15分钟
    realtime_monitor_atr_multipliers: str = "2.0,2.5,3.0"  # 三级止损倍数
    realtime_monitor_volume_ratio_threshold: float = 3.0  # 量比异常阈值
    realtime_monitor_price_change_threshold: float = 2.0  # 涨跌幅异常阈值（%）
    portfolio_risk_concentration_alert_pct: float = 35.0
    portfolio_risk_drawdown_alert_pct: float = 15.0
    portfolio_risk_stop_loss_alert_pct: float = 10.0
    portfolio_risk_stop_loss_near_ratio: float = 0.8
    portfolio_risk_lookback_days: int = 180
    portfolio_fx_update_enabled: bool = True
