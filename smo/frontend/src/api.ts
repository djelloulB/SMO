export interface Post {
  id: number;
  platform: string;
  title: string;
  status: string;
  text?: string;
  media_ref?: string | null;
  created_at?: string | null;
  published_at?: string | null;
}

export interface MetricPoint {
  platform: string;
  likes: number;
  views: number;
  reach: number;
}

export interface ReportSummary {
  total_posts: number;
  total_likes: number;
  total_views: number;
  total_reach: number;
  avg_engagement_rate: number;
  period_start: string;
  period_end: string;
}

export interface Recommendation {
  id: number;
  title: string;
  description: string;
  priority: string;
  impact: string;
}

export interface Status {
  dry_run: boolean;
  human_gate: boolean;
  budget_per_day_usd: number;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) {
      const reasons = Array.isArray(detail.reasons) ? `: ${detail.reasons.join("; ")}` : "";
      return `${detail.message}${reasons}`;
    }
  } catch {
    // fall through to the generic message
  }
  return `${res.status} ${res.statusText}`;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api/v1${path}`);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}

async function post<T>(path: string, body: unknown, token: string): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Dashboard-Token": token,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}

export const api = {
  posts: (status?: string) =>
    get<{ posts: Post[]; count: number }>(
      `/posts?limit=200${status ? `&status=${encodeURIComponent(status)}` : ""}`,
    ),
  approvePost: (id: number, token: string) => post<Post>(`/posts/${id}/approve`, {}, token),
  rejectPost: (id: number, token: string, reason: string) =>
    post<Post>(`/posts/${id}/reject`, { reason }, token),
  metrics: () => get<{ metrics: MetricPoint[] }>("/metrics"),
  summary: () => get<ReportSummary>("/reports/summary"),
  recommendations: () => get<{ recommendations: Recommendation[] }>("/recommendations"),
  status: () => get<Status>("/status"),
};
