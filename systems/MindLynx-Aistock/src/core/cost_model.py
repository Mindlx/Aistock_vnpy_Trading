"""Transaction cost model for realistic backtest simulation.

Provides slippage simulation and commission calculation for A-shares,
HK stocks, and US stocks with configurable market-specific parameters.
"""

from dataclasses import dataclass
from enum import Enum


class SlippageModel(str, Enum):
    FIXED = "fixed"
    PERCENTAGE = "percentage"
    NONE = "none"


class CommissionModel(str, Enum):
    PER_VALUE = "per_value"
    PER_SHARE = "per_share"
    FIXED = "fixed"
    NONE = "none"


@dataclass
class CommissionConfig:
    commission_model: CommissionModel = CommissionModel.PER_VALUE
    commission_rate: float = 0.00025
    commission_per_share: float = 0.0
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0
    stamp_duty_on: str = "sell"
    sec_fee_rate: float = 0.0

    def compute(self, *, side: str, price: float, shares: int) -> float:
        value = price * shares
        if self.commission_model == CommissionModel.PER_VALUE:
            commission = value * self.commission_rate
        elif self.commission_model == CommissionModel.PER_SHARE:
            commission = shares * self.commission_per_share
        elif self.commission_model == CommissionModel.FIXED:
            commission = self.min_commission
        else:
            commission = 0.0
        commission = max(commission, self.min_commission) if self.min_commission else commission

        stamp = 0.0
        if self.stamp_duty_on == "both" or \
           (self.stamp_duty_on == "sell" and side == "sell") or \
           (self.stamp_duty_on == "buy" and side == "buy"):
            stamp = value * self.stamp_duty_rate

        sec_fee = value * self.sec_fee_rate if self.sec_fee_rate else 0.0
        return commission + stamp + sec_fee


@dataclass
class SlippageConfig:
    slippage_model: SlippageModel = SlippageModel.PERCENTAGE
    slippage_pct: float = 0.001
    fixed_points: float = 0.01

    def adjust_price(self, price: float, side: str) -> float:
        if self.slippage_model == SlippageModel.NONE:
            return price
        adjustment = self.fixed_points if self.slippage_model == SlippageModel.FIXED else price * self.slippage_pct
        if side == "buy":
            return price + adjustment
        else:
            return price - adjustment


_MARKET_COMMISSION = {
    "cn": CommissionConfig(
        commission_model=CommissionModel.PER_VALUE,
        commission_rate=0.00025,
        min_commission=5.0,
        stamp_duty_rate=0.001,
        stamp_duty_on="sell",
    ),
    "hk": CommissionConfig(
        commission_model=CommissionModel.PER_VALUE,
        commission_rate=0.001,
        min_commission=100.0,
        stamp_duty_rate=0.001,
        stamp_duty_on="both",
    ),
    "us": CommissionConfig(
        commission_model=CommissionModel.PER_SHARE,
        commission_per_share=0.005,
        min_commission=1.0,
        sec_fee_rate=0.000008,
    ),
}

_MARKET_SLIPPAGE = {
    "cn": SlippageConfig(slippage_model=SlippageModel.PERCENTAGE, slippage_pct=0.001),
    "hk": SlippageConfig(slippage_model=SlippageModel.PERCENTAGE, slippage_pct=0.0015),
    "us": SlippageConfig(slippage_model=SlippageModel.PERCENTAGE, slippage_pct=0.0005),
}


def get_market_config(market: str) -> tuple[CommissionConfig, SlippageConfig]:
    comm = _MARKET_COMMISSION.get(market, _MARKET_COMMISSION["cn"])
    slip = _MARKET_SLIPPAGE.get(market, _MARKET_SLIPPAGE["cn"])
    return comm, slip
