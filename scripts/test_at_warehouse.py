"""验证 AT 数据仓库 vendor 集成是否正常。

用法:
    cd /home/bluekuma/workspace/Aistock_vnpy_Trading
    .venv/bin/python scripts/test_at_warehouse.py
"""
import logging
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AT_ROOT = os.path.join(PROJECT_ROOT, "systems", "mind_TradingAgent")
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, AT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("test_at_warehouse")


def test_warehouse_reader():
    log.info("=" * 50)
    log.info("Test 1: WarehouseReader.get_daily")
    try:
        from services.data_warehouse import WarehouseReader
        reader = WarehouseReader()
        df = reader.get_daily_df("601801", days=120)
        assert not df.empty
        log.info("  ✅ OHLCV: %d rows, columns=%s", len(df), list(df.columns))
        assert "close" in df.columns
        log.info("  ✅ Latest close: %.2f", df["close"].iloc[-1])
    except Exception as e:
        log.error("  ❌ FAILED: %s", e)
        return False
    return True


def test_resolve_instrument_identity():
    log.info("=" * 50)
    log.info("Test 2: resolve_instrument_identity")
    try:
        from mind_tradingagent.agents.utils.agent_utils import resolve_instrument_identity

        identity = resolve_instrument_identity("601801")
        log.info("  ✅ identity(601801): %s", identity)
        assert identity.get("company_name")
        assert identity.get("exchange")

        identity2 = resolve_instrument_identity("601801.SS")
        log.info("  ✅ identity(601801.SS): %s", identity2)
        assert identity2.get("company_name")

        identity3 = resolve_instrument_identity("AAPL")
        log.info("  ✅ identity(AAPL): %s", identity3)
    except Exception as e:
        log.error("  ❌ FAILED: %s", e)
        return False
    return True


def test_warehouse_vendor_methods():
    log.info("=" * 50)
    log.info("Test 3: Warehouse vendor methods")
    try:
        from mind_tradingagent.dataflows.warehouse import (
            get_stock_data, get_fundamentals, get_news, get_balance_sheet, get_cashflow,
        )
        from mind_tradingagent.dataflows.errors import NoMarketDataError
    except ImportError as e:
        log.error("  ❌ Import warehouse module failed: %s", e)
        return False

    ok = True
    try:
        result = get_stock_data("601801", "2026-05-01", "2026-05-29")
        assert result and "601801" in result
        log.info("  ✅ get_stock_data: %d lines", len(result.strip().split("\n")))
    except Exception as e:
        log.error("  ❌ get_stock_data FAILED: %s", e)
        ok = False

    try:
        result = get_fundamentals("601801", "2026-05-29")
        assert result and "Fundamentals" in result
        log.info("  ✅ get_fundamentals: %d chars", len(result))
    except Exception as e:
        log.error("  ❌ get_fundamentals FAILED: %s", e)
        ok = False

    try:
        result = get_news("601801", "2026-05-01", "2026-05-29")
        assert result and "News" in result
        log.info("  ✅ get_news: %d chars", len(result))
    except NoMarketDataError:
        log.info("  ⚠️ get_news: no news data (warehouse limitation)")
    except Exception as e:
        log.error("  ❌ get_news FAILED (non-critical): %s", e)

    try:
        result = get_balance_sheet("601801", "A", "2026-05-29")
        assert result and "Balance Sheet" in result
        log.info("  ✅ get_balance_sheet: %d chars", len(result))
    except Exception as e:
        log.warning("  ⚠️ get_balance_sheet: %s (non-critical)", e)

    return ok


def test_vendor_dispatch():
    log.info("=" * 50)
    log.info("Test 4: route_to_vendor dispatch")
    try:
        from mind_tradingagent.dataflows.interface import route_to_vendor
        from mind_tradingagent.dataflows.config import set_config
        set_config({"data_vendors": {
            "core_stock_apis": "warehouse",
            "technical_indicators": "warehouse",
            "fundamental_data": "warehouse",
            "news_data": "warehouse",
        }})
        result = route_to_vendor("get_stock_data", "601801", "2026-05-01", "2026-05-29")
        assert result and "601801" in str(result)
        log.info("  ✅ route_to_vendor(get_stock_data): %d chars", len(str(result)))
    except Exception as e:
        log.error("  ❌ route_to_vendor FAILED: %s", e)
        return False
    return True


def test_sentinels():
    log.info("=" * 50)
    log.info("Test 5: NO_DATA sentinels")
    try:
        from mind_tradingagent.dataflows.warehouse import (
            get_macro_indicators, get_prediction_markets,
        )
        macro = get_macro_indicators("GDP", "2026-05-29", 30)
        assert "DATA_UNAVAILABLE" in macro
        log.info("  ✅ get_macro_indicators -> DATA_UNAVAILABLE")
        pm = get_prediction_markets("election", 5)
        assert "DATA_UNAVAILABLE" in pm
        log.info("  ✅ get_prediction_markets -> DATA_UNAVAILABLE")
    except Exception as e:
        log.error("  ❌ sentinel test FAILED: %s", e)
        return False
    return True


if __name__ == "__main__":
    results = {
        "warehouse_reader": test_warehouse_reader(),
        "resolve_identity": test_resolve_instrument_identity(),
        "vendor_methods": test_warehouse_vendor_methods(),
        "vendor_dispatch": test_vendor_dispatch(),
        "sentinels": test_sentinels(),
    }
    log.info("=" * 50)
    log.info("RESULTS")
    for name, passed in results.items():
        log.info("  %s: %s", "✅" if passed else "❌", name)
    all_pass = all(results.values())
    log.info("=" * 50)
    log.info("OVERALL: %s", "✅ ALL PASSED" if all_pass else "❌ SOME FAILED")
    sys.exit(0 if all_pass else 1)
