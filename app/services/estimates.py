from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.domain.pricing import PricingError, QuoteRequest, quote
from app.models import Estimate, EstimateStatus, JobRequest
from app.services.catalog import latest_catalog_version

ESTIMATE_VALIDITY = timedelta(days=7)
OPEN = (EstimateStatus.DRAFT, EstimateStatus.NEEDS_REVIEW, EstimateStatus.ISSUED, EstimateStatus.ACCEPTED)


def issue_estimate(session: Session, job: JobRequest, now: datetime) -> Estimate:
    if now.utcoffset() is None:
        raise ValueError("now needs a timezone")
    if job.unresolved_scope:
        raise PricingError("work without a published price needs the owner")
    request = job.quote_request()

    # one estimate at a time per vehicle, priced from details nobody changed since they were read
    revision = session.scalar(
        select(JobRequest.revision).where(JobRequest.id == job.id).with_for_update(key_share=True)
    )
    if revision != job.revision:
        raise StaleDataError(f"job request {job.id} changed since it was read")

    current = session.scalar(
        select(Estimate).where(Estimate.job_request_id == job.id, Estimate.status.in_(OPEN))
    )
    if current and now < current.expires_at and QuoteRequest.model_validate(current.intake) == request:
        # unchanged details keep their price, even after the owner publishes new ones
        return current

    published = latest_catalog_version(session, job.business_id)
    if published is None:
        raise PricingError("no published catalog")
    result = quote(published.catalog, request)

    if current:
        current.status = EstimateStatus.EXPIRED if now >= current.expires_at else EstimateStatus.SUPERSEDED
        # closed before the new one opens, or the one-open-estimate index refuses it
        session.flush()
    latest = session.scalar(select(func.max(Estimate.version)).where(Estimate.job_request_id == job.id))
    estimate = Estimate(
        business_id=job.business_id,
        job_request_id=job.id,
        catalog_version_id=published.id,
        version=(latest or 0) + 1,
        status=EstimateStatus.ISSUED,
        intake=request.model_dump(mode="json"),
        lines=[asdict(line) for line in result.lines],
        currency=result.currency,
        subtotal_minor=result.subtotal_minor,
        tax_minor=result.tax_minor,
        total_minor=result.total_minor,
        deposit_minor=result.deposit_minor,
        event_minutes=result.event_minutes,
        buffer_minutes=result.buffer_minutes,
        issued_at=now,
        expires_at=now + ESTIMATE_VALIDITY,
    )
    session.add(estimate)
    session.flush()
    return estimate
