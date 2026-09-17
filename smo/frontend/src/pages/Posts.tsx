import { useEffect, useState } from "react";
import { api, Post } from "../api";

export default function Posts() {
  const [posts, setPosts] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.posts().then((r) => {
      setPosts(r.posts);
      setLoading(false);
    });
  }, []);

  if (loading) return <div className="card muted">Loading posts…</div>;

  return (
    <div className="card">
      <h3>Posts ({posts.length})</h3>
      {posts.length === 0 ? (
        <p className="muted">No posts yet. Connect a platform and create content.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Title</th>
              <th>Status</th>
              <th>Published</th>
            </tr>
          </thead>
          <tbody>
            {posts.map((p) => (
              <tr key={p.id}>
                <td>{p.platform}</td>
                <td>{p.title}</td>
                <td>
                  <span className={`status-pill ${p.status}`}>{p.status}</span>
                </td>
                <td>{p.published_at ? new Date(p.published_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}