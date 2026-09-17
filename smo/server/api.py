"""Dashboard REST API (consumed by the SMO web UI)."""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..connectors.linkedin import get_linkedin_connector
from ..config.security import security_layer
from ..memory.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["dashboard"])


# --- Schemas ---
class Post(BaseModel):
    id: int
    platform: str
    title: str
    status: str
    published_at: Optional[str] = None


class PublishRequest(BaseModel):
    account_id: str
    text: str
    visibility: str = "PUBLIC"


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


@router.get("/posts")
def list_posts(platform: Optional[str] = Query(default=None, description="Filter by platform")):
    """List posts (with optional platform filter)."""
    posts = _SAMPLE_POSTS if not platform else [p for p in _SAMPLE_POSTS if p["platform"] == platform]
    return {"posts": posts, "count": len(posts)}


@router.get("/metrics")
def get_metrics():
    """Aggregate engagement metrics per platform."""
    return {"metrics": _SAMPLE_METRICS}


@router.get("/reports/summary")
def get_report_summary():
    """Summary report of performance over the last 30 days."""
    now = datetime.utcnow()
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
        result = await connector.publish_post(token, req.text, req.visibility)
        logger.info("LinkedIn post published for account %s", req.account_id)
        return {"status": "published", "result": result}
    except Exception as e:
        logger.exception("LinkedIn publish failed")
        raise HTTPException(status_code=500, detail=f"Publish failed: {e}")