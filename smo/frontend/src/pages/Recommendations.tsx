import { useEffect, useState } from "react";
import { api, Recommendation } from "../api";

export default function Recommendations() {
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.recommendations().then((r) => {
      setRecs(r.recommendations);
      setLoading(false);
    });
  }, []);

  if (loading) return <div className="card muted">Loading recommendations…</div>;

  return (
    <div className="card">
      <h3>Recommendations ({recs.length})</h3>
      {recs.length === 0 ? (
        <p className="muted">No recommendations yet. More data needed.</p>
      ) : (
        recs.map((r) => (
          <div key={r.id} className="rec">
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <h4 style={{ margin: 0 }}>{r.title}</h4>
                <span className={`priority ${r.priority}`}>{r.priority.toUpperCase()}</span>
              </div>
              <p style={{ margin: "4px 0 0" }}>{r.description}</p>
            </div>
            <div className="impact">{r.impact}</div>
          </div>
        ))
      )}
    </div>
  );
}