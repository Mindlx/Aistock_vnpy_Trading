"""数据单位规范 — 全部存储统一为 volume=股, amount=元.

各数据源原生单位不一致, 在 fetcher 出口调用 `normalize_rows()` 归一到规范单位,
避免混存导致 SQL 排序/数值聚合失真 (参见 docs/incidents/2026-10-02-date-format-pollution.md
与 2026-10-02 单位审计).

原生单位对照 (实测自洽比 amount/(close*volume), 目标=1.0):
    tushare     : vol=手,   amount=千元   → ×100 / ×1000
    pytdx       : vol=手,   amount=元     → ×100 / ×1
    akshare_em  : vol=手,   amount=元     → ×100 / ×1
    efinance    : vol=手,   amount=元     → ×100 / ×1
    tencent     : vol=手,   amount=元     → ×100 / ×1
    sina        : vol=股,   amount=元(或0) → ×1 / ×1
    yfinance    : vol=股,   amount=0      → ×1 / ×1
    fallback_*  : vol=手,   amount=元     → ×100 / ×1   (stock_daily cache_fallback 实测)
"""
from __future__ import annotations

CANONICAL_VOLUME = "股"
CANONICAL_AMOUNT = "元"

# source → (volume_factor, amount_factor)
SOURCE_UNIT_FACTORS: dict[str, tuple[float, float]] = {
    "tushare": (100.0, 1000.0),
    "pytdx": (100.0, 1.0),
    "akshare_em": (100.0, 1.0),
    "akshare": (100.0, 1.0),
    "efinance": (100.0, 1.0),
    "tencent": (100.0, 1.0),
    "sina": (1.0, 1.0),
    "yfinance": (1.0, 1.0),
    "fallback_warehouse": (100.0, 1.0),
    "cache_fallback": (100.0, 1.0),
}


def normalize_volume_amount(
    volume: float, amount: float, source: str
) -> tuple[float, float]:
    """按 source 将 (volume, amount) 归一到规范单位 (股/元)。未知 source 原样返回。"""
    vf, af = SOURCE_UNIT_FACTORS.get((source or "").lower(), (1.0, 1.0))
    return (volume or 0.0) * vf, (amount or 0.0) * af


def normalize_rows(rows: list[dict]) -> list[dict]:
    """就地归一化 rows 中 volume/amount 到规范单位 (按行内 'source' 字段)。"""
    for r in rows:
        vf, af = SOURCE_UNIT_FACTORS.get((r.get("source") or "").lower(), (1.0, 1.0))
        if vf != 1.0 and r.get("volume") is not None:
            r["volume"] = float(r["volume"]) * vf
        if af != 1.0 and r.get("amount") is not None:
            r["amount"] = float(r["amount"]) * af
    return rows
