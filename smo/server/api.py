"""Dashboard REST API (consumed by the SMO web UI).

Read endpoints are open to the dashboard. The two mutations — approving or
rejecting a draft, i.e. the human gate — require the ``X-Dashboard-Token``
header to match ``SMO_DASHBOARD_TOKEN``; when the token is unset they are
disabled (secure by default). Every mutation is written to ``audit_actions``
(REC-008) with the ``dashboard`` actor.
"""
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from ..connectors.linkedin import get_linkedin_connector
from ..config.security import security_layer
from ..config.settings import settings
from ..memory.sqlite_store import SQLiteStore
from ..security.audit import audit_logger
from ..services.content_service import (
    ContentError,
    ContentService,
    InvalidTransition,
    ModerationError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["dashboard"])


# --- Schemas ---
class Post(BaseModel):
    id: int
    platform: str
    title: str
    status: str
    text: str = ""
    media_ref: Optional[str] = None
    created_at: Optional[str] = None
    published_at: Optional[str] = None


class RejectRequest(BaseModel):
    reason: str = ""


class PublishRequest(BaseModel):
    account_id: str
    text: str
    visibility: str = "PUBLIC"
    organization_id: Optional[str] = None
    author_urn: Optional[str] = None


class MetricPoint(BaseModel):
    platform: str
    likes: int
    views: int
    reach: int


class ReportSummary(BaseModel):
    total_posts: int
    total_likes: int
    total_views: int
    total_reach: int
    avg_engagement_rate: float
    period_start: str
    period_end: str


class Recommendation(BaseModel):
    id: int
    title: str
    description: str
    priority: str
    impact: str


# --- Sample data (mock layer — replaced by real connectors in Phase MVP+) ---
_SAMPLE_POSTS = [
    {"platform": "linkedin", "title": "How we built an autonomous content pipeline", "status": "published"},
    {"platform": "linkedin", "title": "5 lessons from scaling a side project", "status": "published"},
    {"platform": "wordpress", "title": "Open source social orchestration", "status": "scheduled"},
]

_SAMPLE_METRICS = [
    {"platform": "linkedin", "likes": 48, "views": 1200, "reach": 2100},
    {"platform": "linkedin", "likes": 32, "views": 890, "reach": 1400},
    {"platform": "wordpress", "likes": 12, "views": 240, "reach": 500},
]


# --- Human gate (dashboard approvals) ---
def _payload_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()[:32]


def _audit(tool: str, payload: dict, result: str) -> None:
    audit_logger.log(
        actor_id="dashboard",
        tool=tool,
        payload_hash=_payload_hash(payload),
        result=result,
    )


def require_dashboard_token(x_dashboard_token: Optional[str] = Header(default=None)) -> None:
    """Require the dashboard token for mutations; disabled when it is not configured."""
    expected = settings.smo_dashboard_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Dashboard approvals are disabled; set SMO_DASHBOARD_TOKEN to enable them",
        )
    provided = (x_dashboard_token or "").encode("utf-8")
    if not provided or not secrets.compare_digest(provided, expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Dashboard-Token")


def _serialize_post(record) -> Post:
    text = record.variant_text or record.text or ""
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return Post(
        id=record.id,
        platform=record.platform or "-",
        title=first_line[:120],
        status=record.status,
        text=text,
        media_ref=record.media_ref,
        created_at=record.created_at.isoformat() if record.created_at else None,
        published_at=record.published_at.isoformat() if record.published_at else None,
    )


def _workflow_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ModerationError):
        return HTTPException(
            status_code=422, detail={"message": str(exc), "reasons": list(exc.reasons)}
        )
    if isinstance(exc, InvalidTransition):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ContentError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected workflow error")


@router.get("/posts")
def list_posts(
    platform: Optional[str] = Query(default=None, description="Filter by platform"),
    status: Optional[str] = Query(default=None, description="Filter by workflow status"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List real content items, newest first (``status=draft`` = awaiting approval)."""
    records = ContentService(SQLiteStore()).list(status=status, limit=limit)
    if platform:
        records = [record for record in records if (record.platform or "") == platform]
    posts = [_serialize_post(record).model_dump() for record in records]
    return {"posts": posts, "count": len(posts)}


@router.post("/posts/{content_id}/approve")
def approve_post(content_id: int, _: None = Depends(require_dashboard_token)):
    """Approve a draft (human gate), audited as the ``dashboard`` actor."""
    service = ContentService(SQLiteStore())
    if service.get(content_id) is None:
        raise HTTPException(status_code=404, detail=f"content {content_id} not found")
    try:
        record = service.approve(content_id)
    except ContentError as exc:
        _audit("approve_draft", {"content_id": content_id}, "failure")
        raise _workflow_error(exc)
    _audit("approve_draft", {"content_id": content_id}, "success")
    return _serialize_post(record).model_dump()


@router.post("/posts/{content_id}/reject")
def reject_post(
    content_id: int,
    req: Optional[RejectRequest] = None,
    _: None = Depends(require_dashboard_token),
):
    """Reject a draft (human gate), audited as the ``dashboard`` actor."""
    reason = (req.reason if req else "").strip()
    service = ContentService(SQLiteStore())
    if service.get(content_id) is None:
        raise HTTPException(status_code=404, detail=f"content {content_id} not found")
    try:
        record = service.reject(content_id)
    except ContentError as exc:
        _audit("reject_draft", {"content_id": content_id, "reason": reason}, "failure")
        raise _workflow_error(exc)
    _audit("reject_draft", {"content_id": content_id, "reason": reason}, "success")
    return _serialize_post(record).model_dump()



@router.get("/metrics")
def get_metrics():
    """Aggregate engagement metrics per platform."""
    return {"metrics": _SAMPLE_METRICS}


@router.get("/reports/summary")
def get_report_summary():
    """Summary report of performance over the last 30 days."""
    now = datetime.now(timezone.utc)
    summary = ReportSummary(
        total_posts=len(_SAMPLE_POSTS),
        total_likes=sum(m["likes"] for m in _SAMPLE_METRICS),
        total_views=sum(m["views"] for m in _SAMPLE_METRICS),
        total_reach=sum(m["reach"] for m in _SAMPLE_METRICS),
        avg_engagement_rate=round(
            sum(m["likes"] / max(m["views"], 1) for m in _SAMPLE_METRICS) / len(_SAMPLE_METRICS) * 100, 2
        ),
        period_start=(now - timedelta(days=30)).isoformat(),
        period_end=now.isoformat(),
    )
    return summary.model_dump()


@router.get("/recommendations")
def get_recommendations():
    """AI/rule recommendations on posting strategy."""
    recs = [
        Recommendation(
            id=1,
            title="Publish earlier on weekdays",
            description="Your LinkedIn posts perform 40% better between 08:00–10:00 local time.",
            priority="high",
            impact="Estimated +15% engagement",
        ),
        Recommendation(
            id=2,
            title="Double down on technical topics",
            description="Engineering-focused posts have the highest view rate in the last 30 days.",
            priority="medium",
            impact="Higher reach per post",
        ),
        Recommendation(
            id=3,
            title="Add a weekly recap thread",
            description="Recap content shows strong retention and repeat engagement.",
            priority="low",
            impact="Builds audience loyalty",
        ),
    ]
    return {"recommendations": [r.model_dump() for r in recs]}


@router.get("/status")
def dashboard_status():
    """Lightweight status for the UI header."""
    return {
        "dry_run": security_layer.dry_run,
        "human_gate": security_layer.human_gate,
        "budget_per_day_usd": security_layer.budget_per_day_usd,
    }


@router.post("/linkedin/publish")
async def publish_linkedin(req: PublishRequest):
    """Publish a post to LinkedIn."""
    connector = get_linkedin_connector()
    if not connector:
        raise HTTPException(status_code=503, detail="LinkedIn not configured")

    token = connector.load_token(req.account_id)
    if not token:
        raise HTTPException(status_code=404, detail="Account not connected")

    # Refresh if needed
    if token.is_expired():
        if not token.refresh_token:
            raise HTTPException(status_code=401, detail="Token expired and no refresh token")
        token = await connector.refresh_access_token(token.refresh_token)
        connector.save_token(req.account_id, token)

    try:
        result = await connector.publish_post(
            token,
            req.text,
            req.visibility,
            organization_id=req.organization_id,
            author_urn=req.author_urn,
        )
        logger.info("LinkedIn post published for account %s", req.account_id)
        return {"status": "published", "result": result}
    except Exception as e:
        logger.exception("LinkedIn publish failed")
        raise HTTPException(status_code=500, detail=f"Publish failed: {e}")