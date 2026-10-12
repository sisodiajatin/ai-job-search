import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchApplications, fetchJobs, fetchQueue, fetchTracker } from "../api";
import type { ApplicationsPayload, Job, QueueItem, TrackerRow } from "../types";
import { Donut, Funnel, Trend, weeklyBuckets, type DonutSlice, type FunnelStage } from "../charts";
import { Empty, Icon, LoadError, PageSkeleton, Stat, formatDate } from "../ui";

const STATUS_COLORS: Record<string, string> = {
  applied: "var(--accent)",
  interview: "var(--green)",
  offer: "#2fd1c5",
  hired: "#5ad17a",
  rejected: "var(--rose)",
  no_response: "#8a93a8",
  withdrawn: "var(--amber)",
  offer_declined: "#b8a3ff",
};

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tracker, setTracker] = useState<TrackerRow[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [apps, setApps] = useState<ApplicationsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([fetchJobs(), fetchTracker(), fetchQueue(), fetchApplications()])
      .then(([j, t, q, a]) => {
        setJobs(j);
        setTracker(t.rows);
        setQueue(q);
        setApps(a);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const stats = useMemo(() => {
    const fresh = jobs.filter((j) => j.status === "new").length;
    const highFit = jobs.filter((j) => String(j.fit || "").toLowerCase() === "high").length;
    const prepared = apps ? apps.prepared.length : jobs.filter((j) => String(j.application_status || "").startsWith("prepared")).length;
    const submitted = tracker.length;
    const stageOf = (r: TrackerRow) => String(r.stage || r.status || "applied");
    const interviews = tracker.filter((r) => stageOf(r) === "interview").length;
    const offers = tracker.filter((r) => ["offer", "hired", "offer_declined"].includes(stageOf(r))).length;
    const followups = tracker.filter((r) => r.followup_due).length;
    const responded = tracker.filter((r) => ["interview", "offer", "hired", "offer_declined", "rejected"].includes(stageOf(r))).length;
    const responseRate = submitted > 0 ? Math.round((responded / submitted) * 100) : null;
    return { fresh, highFit, prepared, submitted, interviews, offers, followups, responseRate, queued: queue.length };
  }, [jobs, tracker, queue, apps]);

  const funnel = useMemo<FunnelStage[]>(
    () => [
      { label: "Discovered", count: jobs.length },
      { label: "High fit", count: stats.highFit },
      { label: "Prepared", count: stats.prepared, muted: true },
      { label: "Submitted", count: stats.submitted, muted: true },
      { label: "Interviews", count: stats.interviews, muted: true },
      { label: "Offers", count: stats.offers, muted: true },
    ],
    [jobs.length, stats],
  );

  const donut = useMemo<DonutSlice[]>(() => {
    const order = ["applied", "interview", "offer", "hired", "rejected", "no_response", "withdrawn", "offer_declined"];
    const counts: Record<string, number> = {};
    for (const row of tracker) {
      const key = String(row.stage || row.status || "applied");
      counts[key] = (counts[key] ?? 0) + 1;
    }
    return order
      .filter((key) => counts[key])
      .map((key) => ({ label: key.replace("_", " "), count: counts[key], color: STATUS_COLORS[key] ?? "#8a93a8" }));
  }, [tracker]);

  const trend = useMemo(() => weeklyBuckets(tracker.map((r) => String(r.date || "")), 8), [tracker]);

  const followUps = useMemo(
    () => tracker.filter((r) => r.followup_due).sort((a, b) => (b.days_quiet ?? 0) - (a.days_quiet ?? 0)),
    [tracker],
  );

  const recent = useMemo(
    () =>
      [...jobs]
        .filter((j) => j.status === "new")
        .sort((a, b) => {
          const fit = (j: Job) => (String(j.fit).toLowerCase() === "high" ? 1 : 0);
          if (fit(b) !== fit(a)) return fit(b) - fit(a);
          return String(b.first_seen).localeCompare(String(a.first_seen));
        })
        .slice(0, 6),
    [jobs],
  );

  if (error) return <LoadError>Could not load dashboard: {error}</LoadError>;
  if (loading) return <PageSkeleton variant="dashboard" />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-sub">
            Queue promising listings on <Link to="/jobs">Jobs</Link>, run <code>/apply queue</code> in chat, then mark
            them submitted. Interviews and offers are logged by <code>/outcome</code>.
          </p>
        </div>
      </div>

      <div className="stat-grid">
        <Stat label="Listings" value={jobs.length} meta={`${stats.fresh} new`} icon="jobs" />
        <Stat label="Queued" value={stats.queued} meta="for /apply queue" icon="queue" />
        <Stat label="Prepared" value={stats.prepared} meta="drafts on disk" icon="doc" />
        <Stat label="Submitted" value={stats.submitted} meta={stats.responseRate == null ? "no responses yet" : `${stats.responseRate}% responded`} icon="send" />
        <Stat label="Interviews" value={stats.interviews} meta={stats.offers > 0 ? `${stats.offers} offer${stats.offers === 1 ? "" : "s"}` : "in loop"} icon="check" />
        <Stat label="Follow-ups" value={stats.followups} meta={stats.followups > 0 ? "due — run /outcome followup" : "none due"} icon="clock" alert={stats.followups > 0} />
      </div>

      <div className="grid-2">
        <section className="card card-pad">
          <div className="card-title">
            <h2>Search funnel</h2>
            <span className="hint">discovered → offer</span>
          </div>
          <Funnel stages={funnel} />
        </section>
        <section className="card card-pad">
          <div className="card-title">
            <h2>Tracker</h2>
            <span className="hint">by stage</span>
          </div>
          {donut.length === 0 ? (
            <Empty title="No applications yet">
              <p>Mark a listing Submitted on the Jobs page after you send it.</p>
            </Empty>
          ) : (
            <Donut slices={donut} caption="logged" />
          )}
        </section>
      </div>

      <div className="grid-2-even">
        <section className="card card-pad">
          <div className="card-title">
            <h2>Applications per week</h2>
            <span className="hint">last 8 weeks</span>
          </div>
          <Trend buckets={trend} />
        </section>
        <section className="card card-pad">
          <div className="card-title">
            <h2>Follow-ups due</h2>
            <span className="hint">quiet 10+ days</span>
          </div>
          {followUps.length === 0 ? (
            <Empty title="Nothing needs a nudge">
              <p>Applications gone quiet for 10+ days will surface here.</p>
            </Empty>
          ) : (
            <ul className="file-list">
              {followUps.map((row) => (
                <li key={row.index}>
                  <a>
                    <Icon name="alert" size={14} />
                    <span>
                      <strong>{row.company}</strong> · {row.role} — quiet {row.days_quiet}d
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          )}
          {followUps.length > 0 && (
            <p className="note" style={{ marginTop: "0.7rem", marginBottom: 0 }}>
              Run <code>/outcome followup</code> in chat to draft the messages.
            </p>
          )}
        </section>
      </div>

      <section>
        <div className="card-title" style={{ margin: "1.1rem 0 0.6rem" }}>
          <h2 style={{ fontSize: "15px" }}>Fresh high-fit listings</h2>
          <Link to="/jobs" className="btn-quiet">
            All jobs
          </Link>
        </div>
        {recent.length === 0 ? (
          <Empty title="No new listings">
            <p>
              Run <strong>Search listings</strong> on the <Link to="/jobs">Jobs</Link> page to pull from your portals.
            </p>
          </Empty>
        ) : (
          <div className="folder-grid">
            {recent.map((job) => (
              <article className="folder-card" key={job.key}>
                <div className="kcard-top">
                  <h3>{job.title || job.key}</h3>
                  <span className={`chip ${String(job.fit || "unscored").toLowerCase()}`}>{job.fit || "unscored"}</span>
                </div>
                <p className="co">
                  {job.company || "Unknown company"}
                  {job.location ? ` · ${job.location}` : ""} · seen {formatDate(job.first_seen)}
                </p>
                {job.url && (
                  <a href={job.url} target="_blank" rel="noreferrer" style={{ fontSize: "12.5px", display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
                    <Icon name="external" size={13} /> Open posting
                  </a>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
