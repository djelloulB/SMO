import { useEffect, useMemo, useState } from "react";
import { api, Post } from "../api";

const TOKEN_KEY = "smo_dashboard_token";

type Filter = "pending" | "all";

export default function Posts() {
  const [posts, setPosts] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("pending");
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? "");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api
      .posts()
      .then((r) => setPosts(r.posts))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const pending = useMemo(() => posts.filter((p) => p.status === "draft"), [posts]);
  const visible = filter === "pending" ? pending : posts;

  const updateToken = (value: string) => {
    setToken(value);
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  };

  const run = async (id: number, action: "approve" | "reject") => {
    if (!token) {
      setError("Enter the dashboard token (SMO_DASHBOARD_TOKEN) before approving or rejecting.");
      return;
    }
    let reason = "";
    if (action === "reject") {
      const answer = window.prompt("Reason for rejection (optional):");
      if (answer === null) return;
      reason = answer;
    }

    setBusyId(id);
    setError(null);
    setNotice(null);
    try {
      const updated =
        action === "approve"
          ? await api.approvePost(id, token)
          : await api.rejectPost(id, token, reason);
      setPosts((current) => current.map((p) => (p.id === updated.id ? updated : p)));
      setNotice(`Post ${id} ${updated.status}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return <div className="card muted">Loading posts…</div>;

  return (
    <div className="card">
      <div className="posts-header">
        <h3>Posts ({posts.length})</h3>
        <div className="tabs">
          <button
            type="button"
            className={filter === "pending" ? "tab active" : "tab"}
            onClick={() => setFilter("pending")}
          >
            Awaiting approval ({pending.length})
          </button>
          <button
            type="button"
            className={filter === "all" ? "tab active" : "tab"}
            onClick={() => setFilter("all")}
          >
            All ({posts.length})
          </button>
        </div>
      </div>

      <div className="token-row">
        <label htmlFor="dashboard-token">Dashboard token</label>
        <input
          id="dashboard-token"
          type="password"
          value={token}
          placeholder="SMO_DASHBOARD_TOKEN"
          autoComplete="off"
          onChange={(event) => updateToken(event.target.value)}
        />
        <span className="muted">Required to approve or reject a draft.</span>
      </div>

      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert success">{notice}</div>}

      {visible.length === 0 ? (
        <p className="muted">
          {filter === "pending"
            ? "No draft is waiting for approval."
            : "No posts yet. Connect a platform and create content."}
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Title</th>
              <th>Status</th>
              <th>Created</th>
              <th>Published</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((p) => (
              <tr key={p.id}>
                <td>{p.platform}</td>
                <td>{p.title}</td>
                <td>
                  <span className={`status-pill ${p.status}`}>{p.status}</span>
                </td>
                <td>{p.created_at ? new Date(p.created_at).toLocaleString() : "—"}</td>
                <td>{p.published_at ? new Date(p.published_at).toLocaleString() : "—"}</td>
                <td className="actions">
                  {p.status === "draft" ? (
                    <>
                      <button
                        type="button"
                        className="btn primary"
                        disabled={busyId === p.id}
                        onClick={() => run(p.id, "approve")}
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        className="btn danger"
                        disabled={busyId === p.id}
                        onClick={() => run(p.id, "reject")}
                      >
                        Reject
                      </button>
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
