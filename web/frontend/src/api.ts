import type { ApplicationsPayload, Job, QueueConfig, QueueItem, QueueRunStatus, ScrapeEvent, ScrapePlan, TodaySummary, TrackerRow } from "./types";

async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string };
    if (data.detail) return data.detail;
  } catch {
    /* ignore */
  }
  return res.statusText || `HTTP ${res.status}`;
}

export async function fetchJobs(): Promise<Job[]> {
  const res = await fetch("/api/jobs");
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { jobs: Job[] };
  return data.jobs ?? [];
}

export async function patchJobStatus(key: string, status: string): Promise<Job> {
  const res = await fetch("/api/jobs", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, status }),
  });
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { job: Job };
  return data.job;
}

export async function fetchTracker(): Promise<{ rows: TrackerRow[]; followup_due: number }> {
  const res = await fetch("/api/tracker");
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { rows: TrackerRow[]; followup_due?: number };
  return { rows: data.rows ?? [], followup_due: data.followup_due ?? 0 };
}

export async function fetchToday(): Promise<TodaySummary> {
  const res = await fetch("/api/today");
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TodaySummary;
}

export async function markFollowedUp(index: number): Promise<TrackerRow> {
  const res = await fetch("/api/tracker/followup", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Job-Desk": "1" },
    body: JSON.stringify({ index }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return ((await res.json()) as { row: TrackerRow }).row;
}

export async function fetchApplications(): Promise<ApplicationsPayload> {
  const res = await fetch("/api/applications");
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as ApplicationsPayload;
  return {
    folders: data.folders ?? [],
    drafts: data.drafts ?? [],
    open: data.open ?? [],
    prepared: data.prepared ?? [],
    followup_due: data.followup_due ?? 0,
  };
}

export async function fetchQueue(): Promise<QueueItem[]> {
  const res = await fetch("/api/queue");
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { queue: QueueItem[] };
  return data.queue ?? [];
}

export async function enqueueJob(key: string): Promise<{ queue: QueueItem[]; created: boolean }> {
  const res = await fetch("/api/queue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { queue: QueueItem[]; created: boolean };
}

export async function dequeueJob(url: string): Promise<QueueItem[]> {
  const res = await fetch(`/api/queue?url=${encodeURIComponent(url)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { queue: QueueItem[] };
  return data.queue ?? [];
}

export async function fetchQueueRun(since = 0): Promise<QueueRunStatus> {
  const res = await fetch(`/api/queue/run?since=${since}`);
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as QueueRunStatus;
}

export async function startQueueRun(fillForms = false): Promise<QueueRunStatus> {
  const res = await fetch("/api/queue/run", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Job-Desk": "1" },
    body: JSON.stringify({ fill_forms: fillForms }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as QueueRunStatus;
}

export async function stopQueueRun(): Promise<QueueRunStatus> {
  const res = await fetch("/api/queue/stop", { method: "POST", headers: { "X-Job-Desk": "1" } });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as QueueRunStatus;
}

export async function fetchConfig(): Promise<QueueConfig> {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as QueueConfig;
}

export async function saveConfig(patch: Partial<QueueConfig>): Promise<QueueConfig> {
  const res = await fetch("/api/config", {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-Job-Desk": "1" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as QueueConfig;
}

export async function launchApply(key: string): Promise<{ launched: boolean; command: string; queue: QueueItem[] }> {
  const res = await fetch("/api/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Job-Desk": "1" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { launched: boolean; command: string; queue: QueueItem[] };
}

export async function launchFill(key: string): Promise<{ launched: boolean; command: string }> {
  const res = await fetch("/api/fill", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Job-Desk": "1" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { launched: boolean; command: string };
}

export async function markApplied(key: string): Promise<{ created: boolean; job: Job; row: TrackerRow }> {
  const res = await fetch("/api/applied", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { created: boolean; job: Job; row: TrackerRow };
}

export async function patchTrackerStatus(index: number, status: string): Promise<TrackerRow> {
  const res = await fetch("/api/tracker", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ index, status }),
  });
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { row: TrackerRow };
  return data.row;
}

export async function fetchScrapePlan(): Promise<ScrapePlan> {
  const res = await fetch("/api/scrape/plan");
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScrapePlan;
}

export function fileUrl(path: string): string {
  return `/api/files?path=${encodeURIComponent(path)}`;
}

export async function runScrape(onEvent: (event: ScrapeEvent) => void): Promise<void> {
  const res = await fetch("/api/scrape", { method: "POST" });
  if (res.status === 409) throw new Error("A scrape is already running");
  if (!res.ok) throw new Error(await readError(res));
  if (!res.body) throw new Error("No response body from scrape");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        try {
          onEvent(JSON.parse(line.slice(6)) as ScrapeEvent);
        } catch {
          /* skip malformed */
        }
      }
    }
  }
}
