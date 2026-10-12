import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { enqueueJob, fetchToday, fileUrl, markApplied, markFollowedUp } from "../api";
import type { AppFile, AppFolder, TodaySummary } from "../types";
import { Empty, Icon, LoadError, PageSkeleton, Stat, copyText, formatDate } from "../ui";

function pdfFiles(item: AppFolder): AppFile[] {
  return (item.files ?? []).filter((file) => file.kind === "pdf" || file.name.toLowerCase().endsWith(".pdf")).slice(0, 2);
}

export default function Today() {
  const [data, setData] = useState<TodaySummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  function reload() {
    return fetchToday()
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!flash) return;
    const timer = window.setTimeout(() => setFlash(null), 4000);
    return () => window.clearTimeout(timer);
  }, [flash]);

  async function act(id: string, work: () => Promise<string>) {
    setBusy(id);
    try {
      setFlash(await work());
      await reload();
    } catch (err) {
      setFlash(err instanceof Error ? err.message : "That did not work");
    } finally {
      setBusy(null);
    }
  }

  if (error && !data) return <LoadError>Could not load Today: {error}</LoadError>;
  if (loading || !data) return <PageSkeleton variant="list" />;

  const { followups, ready, queued, picks, counts } = data;
  const nothing = followups.length === 0 && ready.length === 0 && queued.length === 0 && picks.length === 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">
            Today
            <span className="count-pill">{counts.badge} to do</span>
          </h1>
          <p className="page-sub">
            What needs you right now: follow-ups that have gone quiet, drafts waiting to be sent, and a few fresh listings
            worth a look.
          </p>
        </div>
      </div>

      {flash && <p className="flash">{flash}</p>}

      <div className="stat-grid">
        <Stat label="Follow-ups" value={counts.followups} meta={counts.followups > 0 ? "quiet 10+ days" : "none due"} icon="clock" alert={counts.followups > 0} />
        <Stat label="Ready to send" value={counts.ready} meta="drafted, not submitted" icon="doc" alert={counts.ready > 0} />
        <Stat label="Queued" value={counts.queued} meta="waiting for /apply" icon="queue" />
        <Stat label="Worth a look" value={picks.length} meta="new, high fit" icon="jobs" />
      </div>

      <section className="card card-pad" style={{ marginBottom: "0.9rem" }}>
        <div className="card-title">
          <h2>Check Gmail for updates</h2>
          <span className="hint">interview invites, assessments, offers, rejections</span>
        </div>
        <p className="note" style={{ marginTop: 0 }}>
          Scans Gmail for status signals on your open applications and proposes tracker updates for you to approve — nothing is
          written automatically.
        </p>
        <button
          className="btn-quiet"
          type="button"
          onClick={async () => {
            const ok = await copyText("/gmail-sync");
            setFlash(ok ? "Copied /gmail-sync. Paste it in chat to check for updates." : "Could not copy. Type /gmail-sync in chat.");
          }}
        >
          <Icon name="copy" size={13} /> Check in chat
        </button>
      </section>

      {nothing && (
        <Empty title="Nothing needs you right now">
          <p>
            Run <strong>Search listings</strong> on the <Link to="/jobs">Jobs</Link> page to pull in new roles.
          </p>
        </Empty>
      )}

      {followups.length > 0 && (
        <section className="card card-pad" style={{ marginBottom: "0.9rem" }}>
          <div className="card-title">
            <h2>Follow up</h2>
            <span className="hint">no reply for 10+ days, fewer than two nudges sent</span>
          </div>
          <ul className="file-list">
            {followups.map((row) => (
              <li key={row.index} style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
                <Icon name="alert" size={14} />
                <span style={{ flex: "1 1 14rem" }}>
                  <strong>{row.company}</strong> · {row.role} — quiet {row.days_quiet}d (last contact {formatDate(row.last_contact)})
                </span>
                <button
                  className="btn-quiet"
                  type="button"
                  onClick={async () => {
                    const ok = await copyText("/outcome followup");
                    setFlash(ok ? "Copied /outcome followup. Paste it in chat to draft the message." : "Could not copy. Type /outcome followup in chat.");
                  }}
                >
                  <Icon name="copy" size={13} /> Draft in chat
                </button>
                <button
                  className="btn-quiet"
                  type="button"
                  disabled={busy === `f${row.index}`}
                  onClick={() =>
                    void act(`f${row.index}`, async () => {
                      await markFollowedUp(row.index ?? 0);
                      return `Noted: followed up with ${row.company} today.`;
                    })
                  }
                >
                  <Icon name="check" size={13} /> I followed up
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {ready.length > 0 && (
        <section className="card card-pad" style={{ marginBottom: "0.9rem" }}>
          <div className="card-title">
            <h2>Ready to send</h2>
            <span className="hint">drafts you have not submitted yet</span>
          </div>
          <ul className="file-list">
            {ready.map((item) => {
              const label = item.company && item.title ? `${item.company} · ${item.title}` : item.slug;
              return (
                <li key={`${item.kind}-${item.slug}`} style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
                  <span style={{ flex: "1 1 14rem" }}>
                    <strong>{item.company || item.slug}</strong>
                    {item.title ? ` · ${item.title}` : ""}
                  </span>
                  {pdfFiles(item).map((file) => (
                    <a className="btn-quiet" key={file.path} href={fileUrl(file.path)} target="_blank" rel="noreferrer" title={file.name}>
                      <Icon name="doc" size={13} /> {file.name.toLowerCase().startsWith("cover") ? "Letter" : "CV"}
                    </a>
                  ))}
                  <button
                    className="btn-quiet"
                    type="button"
                    disabled={busy === `r${item.key}`}
                    onClick={() =>
                      void act(`r${item.key}`, async () => {
                        const result = await markApplied(String(item.key));
                        return result.created ? `Logged ${label} as submitted.` : `${label} is already on the tracker.`;
                      })
                    }
                  >
                    <Icon name="send" size={13} /> Mark submitted
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="note" style={{ marginTop: "0.7rem", marginBottom: 0 }}>
            Open both PDFs before you send. After submitting you can also just tell Claude "applied".
          </p>
        </section>
      )}

      {queued.length > 0 && (
        <section className="card card-pad" style={{ marginBottom: "0.9rem" }}>
          <div className="card-title">
            <h2>Queued</h2>
            <Link to="/jobs" className="btn-quiet">
              Open Jobs
            </Link>
          </div>
          <p className="note" style={{ marginTop: 0 }}>
            {queued.map((item) => item.company || item.title).join(" · ")}. Use <strong>Run queue</strong> or{" "}
            <strong>Apply now</strong> on the Jobs page.
          </p>
        </section>
      )}

      {picks.length > 0 && (
        <section>
          <div className="card-title" style={{ margin: "1.1rem 0 0.6rem" }}>
            <h2 style={{ fontSize: "15px" }}>Worth a look</h2>
            <Link to="/jobs" className="btn-quiet">
              All jobs
            </Link>
          </div>
          <div className="folder-grid">
            {picks.map((job) => (
              <article className="folder-card" key={job.key}>
                <div className="kcard-top">
                  <h3>{job.title || job.key}</h3>
                  <span className={`chip ${String(job.fit || "unscored").toLowerCase()}`}>{job.fit || "unscored"}</span>
                </div>
                <p className="co">
                  {job.company || "Unknown company"}
                  {job.location ? ` · ${job.location}` : ""} · seen {formatDate(job.first_seen)}
                </p>
                <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", flexWrap: "wrap" }}>
                  {job.url && (
                    <a href={job.url} target="_blank" rel="noreferrer" style={{ fontSize: "12.5px", display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
                      <Icon name="external" size={13} /> Open posting
                    </a>
                  )}
                  <button
                    className="btn-quiet"
                    type="button"
                    disabled={busy === `p${job.key}`}
                    onClick={() =>
                      void act(`p${job.key}`, async () => {
                        const result = await enqueueJob(job.key);
                        return result.created ? `Queued ${job.company || job.key}.` : `${job.company || job.key} is already queued.`;
                      })
                    }
                  >
                    <Icon name="queue" size={13} /> Queue for /apply
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
