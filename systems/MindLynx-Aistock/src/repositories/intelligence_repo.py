"""Intelligence repository.

SQLAlchemy data-access layer for IntelligenceSource and IntelligenceItem models.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, desc, func, select

from src.storage import DatabaseManager, IntelligenceItem, IntelligenceSource


class IntelligenceRepository:
    """DB access layer for intelligence sources and items."""

    def __init__(self, db_manager: DatabaseManager | None = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ---- Source CRUD ----

    def create_source(self, fields: dict[str, Any]) -> IntelligenceSource:
        with self.db.get_session() as session:
            row = IntelligenceSource(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_source(self, source_id: int) -> IntelligenceSource | None:
        with self.db.get_session() as session:
            return session.execute(
                select(IntelligenceSource).where(IntelligenceSource.id == source_id).limit(1)
            ).scalar_one_or_none()

    def get_source_by_name(self, name: str) -> IntelligenceSource | None:
        with self.db.get_session() as session:
            return session.execute(
                select(IntelligenceSource).where(IntelligenceSource.name == name).limit(1)
            ).scalar_one_or_none()

    def update_source(self, source_id: int, fields: dict[str, Any]) -> IntelligenceSource | None:
        with self.db.get_session() as session:
            row = session.execute(
                select(IntelligenceSource).where(IntelligenceSource.id == source_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return row

    def delete_source(self, source_id: int) -> bool:
        with self.db.get_session() as session:
            result = session.execute(
                delete(IntelligenceSource).where(IntelligenceSource.id == source_id)
            )
            session.commit()
            return bool(result.rowcount)

    def list_sources(
        self,
        *,
        enabled: bool | None = None,
        source_type: str | None = None,
        scope_type: str | None = None,
        market: str | None = None,
    ) -> tuple[list[IntelligenceSource], int]:
        conditions = []
        if enabled is not None:
            conditions.append(IntelligenceSource.enabled.is_(enabled))
        if source_type:
            conditions.append(IntelligenceSource.source_type == source_type)
        if scope_type:
            conditions.append(IntelligenceSource.scope_type == scope_type)
        if market:
            conditions.append(IntelligenceSource.market == market)

        where_clause = and_(*conditions) if conditions else True
        with self.db.get_session() as session:
            total = (
                session.execute(
                    select(func.count(IntelligenceSource.id))
                    .select_from(IntelligenceSource)
                    .where(where_clause)
                ).scalar()
                or 0
            )
            rows = (
                session.execute(
                    select(IntelligenceSource)
                    .where(where_clause)
                    .order_by(desc(IntelligenceSource.updated_at), desc(IntelligenceSource.id))
                )
                .scalars()
                .all()
            )
            return list(rows), int(total)

    def list_enabled_sources(self, *, source_type: str | None = None) -> list[IntelligenceSource]:
        conditions = [IntelligenceSource.enabled.is_(True)]
        if source_type:
            conditions.append(IntelligenceSource.source_type == source_type)
        where_clause = and_(*conditions)
        with self.db.get_session() as session:
            rows = (
                session.execute(
                    select(IntelligenceSource).where(where_clause).order_by(IntelligenceSource.id)
                )
                .scalars()
                .all()
            )
            return list(rows)

    def count_source_items(self, source_id: int) -> int:
        with self.db.get_session() as session:
            return (
                session.execute(
                    select(func.count(IntelligenceItem.id))
                    .select_from(IntelligenceItem)
                    .where(IntelligenceItem.source_id == source_id)
                ).scalar()
                or 0
            )

    # ---- Item operations ----

    def create_item(self, fields: dict[str, Any]) -> IntelligenceItem:
        with self.db.get_session() as session:
            row = IntelligenceItem(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_item(self, item_id: int) -> IntelligenceItem | None:
        with self.db.get_session() as session:
            return session.execute(
                select(IntelligenceItem).where(IntelligenceItem.id == item_id).limit(1)
            ).scalar_one_or_none()

    def item_exists(
        self,
        source_id: int | None,
        url: str,
        scope_type: str,
        scope_value: str,
        market: str,
    ) -> bool:
        """Check if an item with the same (source_id, url, scope) already exists."""
        with self.db.get_session() as session:
            conditions = [
                IntelligenceItem.url == url,
                IntelligenceItem.scope_type == scope_type,
                IntelligenceItem.scope_value == scope_value,
                IntelligenceItem.market == market,
            ]
            if source_id is not None:
                conditions.append(IntelligenceItem.source_id == source_id)
            else:
                conditions.append(IntelligenceItem.source_id.is_(None))
            result = session.execute(
                select(func.count(IntelligenceItem.id))
                .select_from(IntelligenceItem)
                .where(and_(*conditions))
                .limit(1)
            ).scalar()
            return (result or 0) > 0

    def list_items(
        self,
        *,
        source_id: int | None = None,
        source_type: str | None = None,
        scope_type: str | None = None,
        scope_value: str | None = None,
        market: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[IntelligenceItem], int]:
        conditions = []
        if source_id is not None:
            conditions.append(IntelligenceItem.source_id == source_id)
        if source_type:
            conditions.append(IntelligenceItem.source_type == source_type)
        if scope_type:
            conditions.append(IntelligenceItem.scope_type == scope_type)
        if scope_value:
            conditions.append(IntelligenceItem.scope_value == scope_value)
        if market:
            conditions.append(IntelligenceItem.market == market)

        where_clause = and_(*conditions) if conditions else True
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            total = (
                session.execute(
                    select(func.count(IntelligenceItem.id))
                    .select_from(IntelligenceItem)
                    .where(where_clause)
                ).scalar()
                or 0
            )
            rows = (
                session.execute(
                    select(IntelligenceItem)
                    .where(where_clause)
                    .order_by(desc(IntelligenceItem.published_at), desc(IntelligenceItem.fetched_at))
                    .offset(offset)
                    .limit(page_size)
                )
                .scalars()
                .all()
            )
            return list(rows), int(total)

    def get_recent_items_by_scope(
        self,
        scope_type: str,
        scope_value: str,
        limit: int = 10,
        max_days: int = 7,
    ) -> list[IntelligenceItem]:
        """Fetch recent intelligence items matching a scope, ordered by published_at DESC.

        Args:
            scope_type: e.g. 'symbol', 'market'
            scope_value: e.g. stock code like '600519'
            limit: max items to return
            max_days: only items published within this many days

        Returns:
            List of IntelligenceItem rows, newest first.
        """
        cutoff = datetime.now() - timedelta(days=max_days)
        with self.db.get_session() as session:
            rows = (
                session.execute(
                    select(IntelligenceItem)
                    .where(
                        IntelligenceItem.scope_type == scope_type,
                        IntelligenceItem.scope_value == scope_value,
                        IntelligenceItem.published_at >= cutoff,
                    )
                    .order_by(desc(IntelligenceItem.published_at))
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return list(rows)
