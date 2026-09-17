import { useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import { api, MetricPoint } from "../api";

const COLORS = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#9333ea"];

export default function Analytics() {
  const [metrics, setMetrics] = useState<MetricPoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.metrics().then((r) => {
      setMetrics(r.metrics);
      setLoading(false);
    });
  }, []);

  if (loading) return <div className="card muted">Loading analytics…</div>;

  return (
    <>
      <div className="card">
        <h3>Engagement by platform</h3>
        <div style={{ height: 300 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={metrics}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e6ee" />
              <XAxis dataKey="platform" stroke="#6b7688" />
              <YAxis stroke="#6b7688" />
              <Tooltip />
              <Bar dataKey="likes" fill="#2563eb" name="Likes" radius={[4, 4, 0, 0]} />
              <Bar dataKey="views" fill="#16a34a" name="Views" radius={[4, 4, 0, 0]} />
              <Bar dataKey="reach" fill="#d97706" name="Reach" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <h3>Reach distribution</h3>
        <div style={{ height: 300 }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={metrics}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={100}
                dataKey="reach"
                nameKey="platform"
                label={({ platform, reach }) => `${platform}: ${reach.toLocaleString()}`}
              >
                {metrics.map((_, i) => (
                  <Cell key={`cell-${i}`} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <h3>Raw metrics</h3>
        <table>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Likes</th>
              <th>Views</th>
              <th>Reach</th>
              <th>Engagement rate</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.platform}>
                <td>{m.platform}</td>
                <td>{m.likes}</td>
                <td>{m.views.toLocaleString()}</td>
                <td>{m.reach.toLocaleString()}</td>
                <td>
                  {(m.views ? (m.likes / m.views * 100).toFixed(2) : "0")}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}