"""
mind_TradingAgent A股股票配置

提供股票代码到 yfinance 兼容格式的映射。
yfinance 使用 Yahoo Finance 的交易所后缀规则：
- 上海交易所 (SH): 股票代码 + .SS
- 深圳交易所 (SZ): 股票代码 + .SZ

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""

# A股 → yfinance ticker 映射
# code -> (yfinance_ticker, 市场, 中文名)
A_SHARE_MARKET_MAP: dict[str, tuple[str, str, str]] = {
    # ── 深圳证券交易所 (SZ) ──
    "001390": ("001390.SZ", "SZ", "古麒绒材"),
    "300652": ("300652.SZ", "SZ", "雷迪克"),
    "000592": ("000592.SZ", "SZ", "平潭发展"),
    "300676": ("300676.SZ", "SZ", "华大基因"),
    "000999": ("000999.SZ", "SZ", "华润三九"),
    "301293": ("301293.SZ", "SZ", "三博脑科"),
    # ── 上海证券交易所 (SS) ──
    "600372": ("600372.SS", "SH", "中航机载"),
    "605368": ("605368.SS", "SH", "蓝天燃气"),
    "603189": ("603189.SS", "SH", "*ST网达"),
    "603557": ("603557.SS", "SH", "*ST起步"),
    "688202": ("688202.SS", "SH", "美迪西"),
    "603127": ("603127.SS", "SH", "昭衍新药"),
    "601801": ("601801.SS", "SH", "皖新传媒"),
}

# 用于从配置查找 yfinance ticker
STOCK_POOL_CODES = sorted(A_SHARE_MARKET_MAP.keys())

# 默认股票池（与 fusion_system 的 stock_pool.csv 一致）
DEFAULT_STOCK_CODES = [
    "001390", "300652", "600372", "605368", "000592",
    "603189", "603557", "688202", "601801", "300676",
    "603127", "000999", "301293",
]


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
