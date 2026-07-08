"""
mind_TradingAgent A股股票配置

提供股票代码到 yfinance 兼容格式的映射。
yfinance 使用 Yahoo Finance 的交易所后缀规则：
- 上海交易所 (SH): 股票代码 + .SS
- 深圳交易所 (SZ): 股票代码 + .SZ

自选股从 config/stock_pool.csv 自动加载（单源配置）。
增减股票只需编辑该 CSV，本文件无需手动修改。

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""

import csv
from pathlib import Path

# ── 从 stock_pool.csv 自动加载 ──
_STOCK_POOL_PATH = Path(__file__).resolve().parent.parent / "config" / "stock_pool.csv"

A_SHARE_MARKET_MAP: dict[str, tuple[str, str, str]] = {}
DEFAULT_STOCK_CODES: list[str] = []

if _STOCK_POOL_PATH.exists():
    with open(_STOCK_POOL_PATH) as _f:
        for _row in csv.DictReader(_f):
            _code = _row["code"]
            _mk = _row["market"]
            _suffix = ".SS" if _mk == "SH" else ".SZ"
            _name = _row["name"]
            A_SHARE_MARKET_MAP[_code] = (f"{_code}{_suffix}", _mk, _name)
            DEFAULT_STOCK_CODES.append(_code)

STOCK_POOL_CODES = sorted(A_SHARE_MARKET_MAP.keys())


def get_yfinance_ticker(code: str) -> str:
    """获取 A 股代码对应的 yfinance ticker 符号。

    Args:
        code: A 股 6 位数字代码，如 "601801"

    Returns:
        yfinance 格式 ticker，如 "601801.SS"

    Raises:
        KeyError: 未知股票代码
    """
    return A_SHARE_MARKET_MAP[code][0]


def get_stock_name(code: str) -> str:
    """获取股票中文名称。"""
    return A_SHARE_MARKET_MAP[code][2]


def get_market(code: str) -> str:
    """获取股票所在市场代码。"""
    return A_SHARE_MARKET_MAP[code][1]


def is_shanghai(code: str) -> bool:
    """判断是否为上海交易所股票（6/5/9 开头）。"""
    return code.startswith(('6', '5', '9'))


def is_shenzhen(code: str) -> bool:
    """判断是否为深圳交易所股票。"""
    return not is_shanghai(code)


def batch_to_yfinance_tickers(codes: list[str]) -> list[str]:
    """批量转换 A 股代码为 yfinance tickers。"""
    result = []
    for code in codes:
        try:
            result.append(get_yfinance_ticker(code))
        except KeyError:
            # 未知代码用默认规则转换
            suffix = ".SS" if is_shanghai(code) else ".SZ"
            result.append(f"{code}{suffix}")
    return result
