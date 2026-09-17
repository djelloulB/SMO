import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, Status } from "./api";
import Overview from "./pages/Overview";
import Posts from "./pages/Posts";
import Analytics from "./pages/Analytics";
import Recommendations from "./pages/Recommendations";

const navItems = [
  { to: "/", label: "Overview", end: true },
  { to: "/posts", label: "Posts", end: false },
  { to: "/analytics", label: "Analytics", end: false },
  { to: "/recommendations", label: "Recommendations", end: false },
];

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);

  useEffect(() => {
    api.status().then(setStatus).catch(() => setStatus(null));
  }, []);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">
          SMO<span>.</span>
        </div>
        <nav>
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <div className="topbar">
          <h1>Social Media Orchestrator</h1>
          <div className="badges">
            {status && (
              <>
                <span className="badge on">Dry run: {status.dry_run ? "ON" : "OFF"}</span>
                <span className="badge on">Human gate: {status.human_gate ? "ON" : "OFF"}</span>
                <span className="badge">Budget/day: ${status.budget_per_day_usd.toFixed(2)}</span>
              </>
            )}
          </div>
        </div>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/posts" element={<Posts />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/recommendations" element={<Recommendations />} />
        </Routes>
      </main>
    </div>
  );
}