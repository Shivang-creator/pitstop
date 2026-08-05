/* API client.

   Scan and apply both stream Server-Sent Events. `fetch` + a manual SSE parser
   rather than EventSource, because EventSource cannot issue a POST and the
   scan request carries a body. The parser below is the minimum that handles
   the framing the server emits (event: / data: / blank-line terminated). */

export type Severity = "critical" | "warning" | "notice";

export interface Channel {
  id: string;
  title: string;
  handle: string;
  subscriber_count: number;
  video_count: number;
  view_count: number;
  thumbnail_url: string;
}

export interface FindingInstance {
  check_id: string;
  severity: Severity;
  title: string;
  detail: string;
  video_id: string | null;
  video_title: string | null;
  video_url: string | null;
  thumbnail_url: string | null;
  impact_views: number;
  auto_fixable: boolean;
  evidence: Record<string, unknown>;
}

export interface FindingGroup {
  check_id: string;
  title: string;
  description: string;
  severity: Severity;
  count: number;
  impact_views: number;
  auto_fixable: number;
  instances: FindingInstance[];
}

export interface CategoryScore {
  key: string;
  label: string;
  description: string;
  score: number;
  findings: number;
  critical: number;
}

export interface PriorityVideo {
  video_id: string;
  title: string;
  issues: number;
  penalty: number;
  views_per_month: number;
  thumbnail_url: string;
  url: string;
}

export interface ScoreReport {
  score: number;
  grade: string;
  total_findings: number;
  critical: number;
  warning: number;
  notice: number;
  auto_fixable: number;
  affected_videos: number;
  categories: CategoryScore[];
  priority_videos: PriorityVideo[];
  headline: string;
}

export interface ScanResult {
  scan_id: string;
  channel: Channel;
  is_owner: boolean;
  mode: "PUBLIC" | "OWNER" | "FIXTURE";
  score: ScoreReport;
  groups: FindingGroup[];
  skipped: { check_id: string; name: string; reason: string }[];
  quota: { spent: number; budget: number; remaining: number };
  video_count: number;
}

export interface Change {
  video_id: string;
  video_title: string;
  video_url: string;
  field: string;
  current: unknown;
  proposed: unknown;
  note: string;
  check_id: string;
}

export interface PlanResult {
  changes: Change[];
  deferred: number;
  affected_videos: number;
  quota_cost: number;
  quota_budget: number;
  conflicts: { video_id: string; field: string; dropped: string; reason: string }[];
  manual_only: number;
}

export interface ApplyResult {
  applied: number;
  videos: number;
  failed: { video_id: string; field: string; error: string }[];
  quota_spent: number;
  stopped_early: string | null;
  dry_run: boolean;
  channel_url: string;
}

export interface Progress {
  phase?: string;
  stage: string;
  done: number;
  total: number | null;
}

export interface Capabilities {
  has_api_key: boolean;
  has_oauth: boolean;
  has_token: boolean;
  has_llm: boolean;
  quota_budget: number;
  check_count: number;
  fixtures: string[];
}

const BASE = "";

async function* sse(
  url: string,
  body: unknown,
): AsyncGenerator<{ event: string; data: any }> {
  const response = await fetch(BASE + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `Request failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line. Keep the trailing partial frame.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      yield { event, data: JSON.parse(dataLines.join("\n")) };
    }
  }
}

export async function runScan(
  req: { channel: string; limit?: number | null; owner?: boolean; fixture?: string | null },
  onProgress: (p: Progress) => void,
): Promise<ScanResult> {
  let result: ScanResult | null = null;
  let error: string | null = null;

  for await (const { event, data } of sse("/api/scan", req)) {
    if (event === "progress") onProgress(data as Progress);
    else if (event === "result") result = data as ScanResult;
    else if (event === "error") error = (data as { message: string }).message;
  }

  if (error) throw new Error(error);
  if (!result) throw new Error("Scan produced no result");
  return result;
}

export async function buildPlan(
  scanId: string,
  opts: { only_checks?: string[] | null; only_videos?: string[] | null } = {},
): Promise<PlanResult> {
  const response = await fetch(`${BASE}/api/scan/${scanId}/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function runApply(
  scanId: string,
  opts: { confirm: boolean; dry_run: boolean },
  onProgress: (p: Progress) => void,
): Promise<ApplyResult> {
  let result: ApplyResult | null = null;
  let error: string | null = null;

  for await (const { event, data } of sse(`/api/scan/${scanId}/apply`, opts)) {
    if (event === "progress") onProgress(data as Progress);
    else if (event === "result") result = data as ApplyResult;
    else if (event === "error") error = (data as { message: string }).message;
  }

  if (error) throw new Error(error);
  if (!result) throw new Error("Apply produced no result");
  return result;
}

export async function getCapabilities(): Promise<Capabilities> {
  const response = await fetch(`${BASE}/api/capabilities`);
  if (!response.ok) throw new Error("Could not reach the Pitstop server");
  return response.json();
}

export const fmt = {
  int: (n: number) => n.toLocaleString("en-US"),
  compact: (n: number) =>
    n >= 1_000_000
      ? `${(n / 1_000_000).toFixed(1)}M`
      : n >= 1_000
        ? `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`
        : `${n}`,
};
