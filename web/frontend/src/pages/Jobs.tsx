import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  dequeueJob,
  enqueueJob,
  fetchJobs,
  fetchQueue,
  fetchQueueRun,
  fetchScrapePlan,
  fileUrl,
  launchApply,
  launchFill,
  markApplied,
  patchJobStatus,
  runScrape,
  startQueueRun,
  stopQueueRun,
} from "../api";
import type { Job, QueueItem, QueueRunStatus, ScrapeEvent, ScrapePlan } from "../types";
import { Empty, Icon, LoadError, PageSkeleton, formatDate } from "../ui";
import JobDrawer from "./JobDrawer";

const STATUS_ORDER: Record<string, number> = {
  new: 0,
  ranked: 1,
  evaluated: 2,
  skipped: 3,
  expired: 4,
};

function sortJobs(jobs: Job[]): Job[] {
  return [...jobs].sort((a, b) => {
    const sa = STATUS_ORDER[String(a.status)] ?? 9;
    const sb = STATUS_ORDER[String(b.status)] ?? 9;
    if (sa !== sb) return sa - sb;
    const ra = typeof a.rank_score === "number" ? a.rank_score : -1;
    const rb = typeof b.rank_score === "number" ? b.rank_score : -1;
    if (rb !== ra) return rb - ra;
    return String(b.first_seen || "").localeCompare(String(a.first_seen || ""));
  });
}

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("open");
  const [fit, setFit] = useState("all");
  const [portal, setPortal] = useState("all");
  const [q, setQ] = useState("");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [scraping, setScraping] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [logOpen, setLogOpen] = useState(true);
  const [plan, setPlan] = useState<ScrapePlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [run, setRun] = useState<QueueRunStatus | null>(null);
  const [runLog, setRunLog] = useState<string[]>([]);
  const lastEventId = useRef(0);
  const wasRunning = useRef(false);

  function reload() {
    return Promise.all([fetchJobs(), fetchQueue()]).then(([nextJobs, nextQueue]) => {
      setJobs(nextJobs);
      setQueue(nextQueue);
    });
  }

  useEffect(() => {
    void Promise.all([
      reload().catch((err: Error) => setError(err.message)),
      fetchScrapePlan()
        .then(setPlan)
        .catch(() => setPlan(null)),
    ]).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!flash) return;
    const timer = window.setTimeout(() => setFlash(null), 4000);
    return () => window.clearTimeout(timer);
  }, [flash]);

  function applyRunStatus(next: QueueRunStatus) {
    if (next.events.length > 0) {
      setRunLog((lines) => [...lines, ...next.events.map((e) => `${e.ts} ${e.message}`)]);
      lastEventId.current = next.last_id;
    }
    setRun(next);
  }

  // Pick up a run that is already going (page reload) or just finished.
  useEffect(() => {
    void fetchQueueRun(0)
      .then((next) => {
        if (next.running || next.results.length > 0) applyRunStatus(next);
      })
      .catch(() => undefined);
  }, []);

  // Poll while a run is active; refresh the table and queue when it ends.
  useEffect(() => {
    if (!run?.running) return;
    const timer = window.setInterval(() => {
      void fetchQueueRun(lastEventId.current)
        .then((next) => {
          applyRunStatus(next);
          void reload().catch(() => undefined);
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [run?.running]);

  useEffect(() => {
    if (wasRunning.current && run && !run.running) {
      void reload().catch(() => undefined);
      setFlash(`Queue run finished. ${run.message}`);
    }
    wasRunning.current = Boolean(run?.running);
  }, [run?.running]);

  async function runQueue(fillForms = false) {
    setRunLog([]);
    lastEventId.current = 0;
    const next = await startQueueRun(fillForms);
    applyRunStatus(next);
    setFlash(
      fillForms
        ? "Queue run started. Every job is drafted first; then each employer form opens in Chrome, one at a time, filled with that job's CV and letter. Review and submit each yourself. Nothing is submitted for you."
        : "Queue run started. Each job is evaluated, then drafted if it passes. Nothing is submitted.",
    );
  }

  async function stopRun() {
    const next = await stopQueueRun();
    setRun(next);
  }

  const portals = useMemo(() => {
    const set = new Set<string>();
    for (const job of jobs) {
      if (job.portal) set.add(job.portal);
    }
    return [...set].sort();
  }, [jobs]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return sortJobs(
      jobs.filter((job) => {
        if (status === "open") {
          const openStatus = job.status === "new" || job.status === "ranked" || job.status === "evaluated";
          if (!openStatus && !job.queued) return false;
          if ((job.status === "skipped" || job.status === "expired") && !job.queued) return false;
        } else if (status !== "all" && job.status !== status) {
          return false;
        }
        if (fit !== "all" && String(job.fit || "unscored") !== fit) return false;
        if (portal !== "all" && job.portal !== portal) return false;
        if (!needle) return true;
        const hay = `${job.title} ${job.company} ${job.location} ${job.note}`.toLowerCase();
        return hay.includes(needle);
      }),
    );
  }, [jobs, status, fit, portal, q]);

  const openJob = useMemo(() => jobs.find((job) => job.key === openKey) ?? null, [jobs, openKey]);

  async function queueToggle(job: Job) {
    if (job.queued) {
      const next = await dequeueJob(String(job.url || job.key));
      setQueue(next);
      setJobs((current) =>
        current.map((row) => (row.url === job.url || row.key === job.key ? { ...row, queued: false } : row)),
      );
      setFlash(`Removed ${job.company || job.key} from the queue.`);
    } else {
      const result = await enqueueJob(job.key);
      setQueue(result.queue);
      setJobs((current) => current.map((row) => (row.key === job.key ? { ...row, queued: true } : row)));
      setFlash(
        result.created
          ? `Queued ${job.company || job.key}. In chat, run /apply queue.`
          : `${job.company || job.key} is already in the queue.`,
      );
    }
  }

  async function applyNow(job: Job) {
    const result = await launchApply(job.key);
    setQueue(result.queue);
    setJobs((current) => current.map((row) => (row.key === job.key ? { ...row, queued: false } : row)));
    setFlash(
      `Started ${job.company || job.key}: tailoring your CV and cover letter, then opening Chrome so you can watch the application fill live. Review highlighted questions before submitting.`,
    );
  }

  async function fillForm(job: Job) {
    await launchFill(job.key);
    setFlash(
      `Watch ${job.company || job.key}'s application fill live in Chrome. The progress panel shows each field and upload. Review the form and highlighted questions, then submit yourself.`,
    );
  }

  async function submitted(job: Job) {
    const result = await markApplied(job.key);
    setJobs((current) => current.map((row) => (row.key === job.key ? { ...row, ...result.job } : row)));
    setFlash(
      result.created
        ? `Logged ${job.company || job.key} as submitted. Use /outcome in chat for interviews or offers.`
        : `${job.company || job.key} is already on the tracker (${result.row.status || "applied"}).`,
    );
  }

  async function skip(job: Job) {
    const updated = await patchJobStatus(job.key, "skipped");
    setJobs((current) => current.map((row) => (row.key === job.key ? { ...row, ...updated } : row)));
    setFlash(`Skipped ${job.company || job.key}.`);
  }

  async function scrape() {
    setScraping(true);
    setLog([]);
    setLogOpen(true);
    setError(null);
    try {
      await runScrape((event: ScrapeEvent) => {
        if (event.message) {
          setLog((lines) => [...lines, event.message as string]);
        }
      });
      await reload();
      setFlash("Search finished — new listings are in the table.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scrape failed");
    } finally {
      setScraping(false);
    }
  }

  if (error && jobs.length === 0 && !scraping) {
    return <LoadError>Could not load jobs: {error}</LoadError>;
  }
  if (loading) return <PageSkeleton variant="list" />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">
            Jobs
            <span className="count-pill">
              {filtered.length} of {jobs.length}
            </span>
          </h1>
          <p className="page-sub">
            Click a row to open its details. <strong>Apply now</strong> is in the bar at the bottom of that panel: it shows the fit
            evaluation, asks you, then drafts your documents and fills the form. Queue several jobs with <strong>Queue for /apply</strong>
            and a <strong>Run queue</strong> button appears above the table.
          </p>
        </div>
        <button className="btn" type="button" onClick={() => void scrape()} disabled={scraping}>
          <Icon name="radar" size={15} />
          {scraping ? "Searching…" : "Search listings"}
        </button>
      </div>

      {plan && (
        <p className="note">
          Plan: {plan.calls.length} searches, last {plan.jobage} days, {plan.limit} per call ·{" "}
          {plan.portals.length > 0 ? plan.portals.join(", ") : "no portals enabled"}
        </p>
      )}

      {flash && <p className="flash">{flash}</p>}
      {error && jobs.length > 0 && <p className="flash error">{error}</p>}

      {queue.length > 0 ? (
        <div className="queue-panel">
          <span className="q-title">
            <Icon name="queue" size={15} /> Apply queue ({queue.length})
          </span>
          <p>
            Next in chat: <code>/apply queue</code>
          </p>
          {run?.running ? (
            <button className="btn-quiet btn-danger" type="button" onClick={() => void stopRun().catch((err: Error) => setFlash(err.message))}>
              <Icon name="close" size={13} /> Stop
            </button>
          ) : (
            <button
              className="btn"
              type="button"
              title="Runs /apply --auto on each queued job in turn: skips failed eligibility or a score under 60, drafts the rest. Nothing is submitted."
              onClick={() => void runQueue().catch((err: Error) => setFlash(err.message))}
            >
              <Icon name="send" size={14} /> Run queue (drafts only)
            </button>
          )}
          {!run?.running && (
            <button
              className="btn"
              type="button"
              title="Drafts every queued job (same rules as Run queue), then opens each employer form in Chrome one at a time, uploads that job's CV and cover letter, and fills it. You review and submit each one; the next opens when you close the window. Nothing is submitted for you."
              onClick={() => void runQueue(true).catch((err: Error) => setFlash(err.message))}
            >
              <Icon name="send" size={14} /> Run queue and fill forms
            </button>
          )}
          <div className="queue-items">
            {queue.map((item) => (
              <span className="queue-item" key={item.url}>
                {item.company || item.title || item.url}
                <button
                  type="button"
                  aria-label={`Remove ${item.company || item.url} from queue`}
                  onClick={() =>
                    void dequeueJob(item.url).then((next) => {
                      setQueue(next);
                      setJobs((current) =>
                        current.map((row) => (row.url === item.url || row.key === item.url ? { ...row, queued: false } : row)),
                      );
                    })
                  }
                >
                  <Icon name="close" size={12} />
                </button>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {run && (run.running || run.results.length > 0 || runLog.length > 0) ? (
        <div className="card card-pad" style={{ marginBottom: "0.9rem" }}>
          <p className="note" style={{ marginTop: 0 }}>
            <strong>Queue run</strong> · {run.running ? `working on ${run.current?.company || run.current?.title || "next job"}` : run.message}
          </p>
          {run.results.length > 0 && (
            <div className="queue-items" style={{ marginBottom: "0.5rem" }}>
              {run.results.map((r) => (
                <span className="queue-item" key={r.url} title={r.reason || ""}>
                  {r.company || r.title || r.url}
                  <span className={`chip ${r.status}`} style={{ marginLeft: "0.4rem" }}>
                    {r.status}
                    {typeof r.score === "number" ? ` ${r.score}` : ""}
                  </span>
                  {r.form && (
                    <span className={`chip ${r.form.status}`} style={{ marginLeft: "0.3rem" }} title={r.form.reason || ""}>
                      form: {r.form.status.replace("_", " ")}
                    </span>
                  )}
                </span>
              ))}
            </div>
          )}
          {runLog.length > 0 && <pre className="log">{runLog.slice(-200).join("\n")}</pre>}
        </div>
      ) : null}

      <div className="card card-pad" style={{ marginBottom: "0.9rem" }}>
        <div className="toolbar">
          <div className="field search">
            <Icon name="search" size={14} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, company, location…" aria-label="Search jobs" />
          </div>
          <div className="field">
            <span>Status</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="open">Open</option>
              <option value="all">All</option>
              <option value="new">New</option>
              <option value="ranked">Ranked</option>
              <option value="evaluated">Evaluated</option>
              <option value="skipped">Skipped</option>
              <option value="expired">Expired</option>
            </select>
          </div>
          <div className="field">
            <span>Fit</span>
            <select value={fit} onChange={(e) => setFit(e.target.value)}>
              <option value="all">All</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
              <option value="unscored">Unscored</option>
            </select>
          </div>
          <div className="field">
            <span>Portal</span>
            <select value={portal} onChange={(e) => setPortal(e.target.value)}>
              <option value="all">All</option>
              {portals.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div className="spacer" />
          {log.length > 0 && (
            <button className="btn-quiet" type="button" onClick={() => setLogOpen((v) => !v)}>
              {logOpen ? "Hide search log" : `Search log (${log.length})`}
            </button>
          )}
        </div>
        {log.length > 0 && logOpen && <pre className="log">{log.join("\n")}</pre>}
      </div>

      {jobs.length === 0 ? (
        <Empty title="No listings yet">
          <p>
            Hit <strong>Search listings</strong> to pull from your enabled portals, or keep using <code>/scrape</code> in
            chat — both feed this table.
          </p>
        </Empty>
      ) : filtered.length === 0 ? (
        <Empty title="No matches">
          <p>Clear the search or widen the filters to see the rest of the table.</p>
        </Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Role</th>
                <th>Location</th>
                <th>Status</th>
                <th>Fit</th>
                <th>Score</th>
                <th>Docs</th>
                <th>Seen</th>
                <th aria-label="Open details" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((job) => (
                <Fragment key={job.key}>
                  <tr
                    className="rowlink"
                    onClick={() => setOpenKey(job.key)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") setOpenKey(job.key);
                    }}
                    tabIndex={0}
                    aria-expanded={openKey === job.key}
                  >
                    <td>
                      <span className="cell-main">{job.title || job.key}</span>
                      {job.company && <p className="cell-sub">{job.company}</p>}
                    </td>
                    <td>{job.location || "-"}</td>
                    <td>
                      <span className={`chip ${String(job.status || "").toLowerCase()}`}>{String(job.status || "-")}</span>
                      {job.queued && <span className="chip queued">queued</span>}
                      {job.tracker_status && (
                        <span className={`chip ${String(job.tracker_status).toLowerCase()}`}>{job.tracker_status}</span>
                      )}
                    </td>
                    <td>
                      <span className={`chip ${String(job.fit || "unscored").toLowerCase()}`}>{job.fit || "unscored"}</span>
                    </td>
                    <td className="mono">{typeof job.rank_score === "number" ? job.rank_score : "-"}</td>
                    <td>
                      <div className="row-actions">
                        {job.cv_pdf && (
                          <a className="btn-quiet" href={fileUrl(job.cv_pdf)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                            CV
                          </a>
                        )}
                        {job.cover_pdf && (
                          <a className="btn-quiet" href={fileUrl(job.cover_pdf)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                            Letter
                          </a>
                        )}
                        {!job.cv_pdf && !job.cover_pdf ? "-" : null}
                      </div>
                    </td>
                    <td>{formatDate(job.first_seen)}</td>
                    <td>
                      <span className="icon-btn" aria-hidden="true">
                        <Icon name="external" size={13} />
                      </span>
                    </td>
                  </tr>
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openJob && (
        <JobDrawer
          job={openJob}
          onClose={() => setOpenKey(null)}
          onQueueToggle={(job) => void queueToggle(job).catch((err: Error) => setFlash(err.message))}
          onApplyNow={(job) => void applyNow(job).catch((err: Error) => setFlash(err.message))}
          onFillForm={(job) => void fillForm(job).catch((err: Error) => setFlash(err.message))}
          onSubmitted={(job) => void submitted(job).catch((err: Error) => setFlash(err.message))}
          onSkip={(job) => {
            setOpenKey(null);
            void skip(job).catch((err: Error) => setFlash(err.message));
          }}
          onFlash={setFlash}
        />
      )}
    </>
  );
}
