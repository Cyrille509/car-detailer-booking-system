import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.pricing import Catalog
from app.models import Business, CatalogVersion


def publish_catalog(
    session: Session, business_id: uuid.UUID, settings: Mapping[str, Any], published_by: uuid.UUID
) -> CatalogVersion:
    # raises ValidationError before anything is written
    content = Catalog.model_validate(settings).model_dump(mode="json")
    # concurrent publishes wait here in turn, so each one sees the version before it;
    # key_share leaves rows that only reference the business, like new customers, unblocked
    locked = session.scalar(
        select(Business.id).where(Business.id == business_id).with_for_update(key_share=True)
    )
    if locked is None:
        raise LookupError(f"unknown business {business_id}")

    latest = latest_catalog_version(session, business_id)
    if latest is not None and latest.content == content:
        # a repeated publish, like a double click, is not a new version
        return latest
    published = CatalogVersion(
        business_id=business_id,
        version=latest.version + 1 if latest else 1,
        content=content,
        published_by=published_by,
    )
    session.add(published)
    session.flush()
    return published


def latest_catalog_version(session: Session, business_id: uuid.UUID) -> CatalogVersion | None:
    return session.scalar(
        select(CatalogVersion)
        .where(CatalogVersion.business_id == business_id)
        .order_by(CatalogVersion.version.desc())
        .limit(1)
    )
