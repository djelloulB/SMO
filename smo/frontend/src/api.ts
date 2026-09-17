export interface Post {
  id: number;
  platform: string;
  title: string;
  status: string;
  published_at?: string;
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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api/v1${path}`);
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

export const api = {
  posts: () => get<{ posts: Post[]; count: number }>("/posts"),
  metrics: () => get<{ metrics: MetricPoint[] }>("/metrics"),
  summary: () => get<ReportSummary>("/reports/summary"),
  recommendations: () => get<{ recommendations: Recommendation[] }>("/recommendations"),
  status: () => get<Status>("/status"),
};