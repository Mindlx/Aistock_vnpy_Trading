#!/usr/bin/env python3
"""Backfill news_intel.content for 财经早餐 records stored before the OCR extractor was added.

For each old record (source='财经早餐', content IS NULL):
  1. Fetch the article page
  2. Try HTML parsing (#ContentBody → h3+p)  — fast, no API call
  3. Fall back to Qwen-VL LLM extraction
  4. Fall back to Baidu OCR
  5. UPDATE news_intel SET content = extracted

Usage:
    python scripts/backfill_cjzc_content.py          # dry-run (count only)
    python scripts/backfill_cjzc_content.py --apply   # actually backfill
"""

from __future__ import annotations

import logging
import os
import sys
import time

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_cjzc")

try:
    from src.storage import NewsIntel, get_db
    from src.cjzc_service import CjzcExtractor
except ImportError:
    # When running from project root, the MindLynx-Aistock packages need sys.path
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "systems", "MindLynx-Aistock"))
    from src.storage import NewsIntel, get_db
    from src.cjzc_service import CjzcExtractor


def main():
    apply = "--apply" in sys.argv

    db = get_db()
    extractor = CjzcExtractor()

    with db.session_scope() as session:
        records = (
            session.query(NewsIntel)
            .filter(NewsIntel.source == "财经早餐", NewsIntel.content.is_(None))
            .all()
        )

        logger.info("Found %d 财经早餐 records with empty content", len(records))

        if not apply:
            logger.info("DRY-RUN: pass --apply to actually backfill")
            return

        updated = 0
        failed = 0
        for record in records:
            if not record.url:
                failed += 1
                continue

            try:
                logger.info("[%d/%d] Processing: %s", updated + failed + 1, len(records), record.title[:50])
                result = extractor.extract(record.url)
                if result and result.get("success"):
                    record.content = result["text"][:5000]  # cap at 5000 chars
                    updated += 1
                    logger.info("  ✅ Extracted %d chars", len(result["text"]))
                else:
                    logger.warning("  ⚠️ Extraction returned no result")
                    failed += 1
                # Be kind to upstream APIs
                time.sleep(1.0)
            except Exception as e:
                logger.error("  ❌ Failed: %s", e)
                failed += 1

        session.commit()
        logger.info("Done: %d updated, %d failed", updated, failed)


if __name__ == "__main__":
    main()
