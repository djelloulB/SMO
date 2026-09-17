import { useEffect, useState } from "react";
import { api, ReportSummary } from "../api";

export default function Overview() {
  const [summary, setSummary] = useState<ReportSummary | null>(null);

  useEffect(() => {
    api.summary().then(setSummary).catch(() => setSummary(null));
  }, []);

  if (!summary) return <div className="card muted">Loading overview…</div>;

  return (
    <>
      <div className="grid">
        <div className="stat">
          <div className="label">Posts</div>
          <div className="value">{summary.total_posts}</div>
          <div className="delta">All platforms</div>
        </div>
        <div className="stat">
          <div className="label">Likes</div>
          <div className="value">{summary.total_likes}</div>
          <div className="delta">Last 30 days</div>
        </div>
        <div className="stat">
          <div className="label">Views</div>
          <div className="value">{summary.total_views.toLocaleString()}</div>
          <div className="delta">Last 30 days</div>
        </div>
        <div className="stat">
          <div className="label">Reach</div>
          <div className="value">{summary.total_reach.toLocaleString()}</div>
          <div className="delta">Last 30 days</div>
        </div>
        <div className="stat">
          <div className="label">Engagement rate</div>
          <div className="value">{summary.avg_engagement_rate}%</div>
          <div className="delta">Average</div>
        </div>
      </div>

      <div className="card">
        <h3>Performance summary</h3>
        <p className="muted">
          Period: {new Date(summary.period_start).toLocaleDateString()} —{" "}
          {new Date(summary.period_end).toLocaleDateString()}
        </p>
        <p>
          <strong>{summary.total_posts}</strong> posts published across your platforms generated{" "}
          <strong>{summary.total_likes}</strong> likes and <strong>{summary.total_views.toLocaleString()}</strong>{" "}
          views, with an average engagement rate of{" "}
          <strong>{summary.avg_engagement_rate}%</strong>.
        </p>
      </div>
    </>
  );
}