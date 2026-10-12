import { useState } from "react";
import { fileUrl } from "../api";
import type { Job } from "../types";
import { Drawer, Icon, copyText, formatDate, missing } from "../ui";

type Props = {
  job: Job;
  onClose: () => void;
  onQueueToggle: (job: Job) => void;
  onApplyNow: (job: Job) => void;
  onFillForm: (job: Job) => void;
  onSubmitted: (job: Job) => void;
  onSkip: (job: Job) => void;
  onFlash: (message: string) => void;
};

function PdfRow({ label, path, jobName }: { label: string; path?: string | null; jobName: string }) {
  const [open, setOpen] = useState(false);
  if (!path) return null;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
        <a className="btn-quiet" href={fileUrl(path)} target="_blank" rel="noreferrer">
          <Icon name="doc" size={13} /> {label}
        </a>
        <button className="btn-quiet" type="button" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide preview" : "Preview"}
        </button>
      </div>
      {open && <iframe className="pdf-frame" title={`${label} preview — ${jobName}`} src={fileUrl(path)} />}
    </div>
  );
}

export default function JobDrawer({ job, onClose, onQueueToggle, onApplyNow, onFillForm, onSubmitted, onSkip, onFlash }: Props) {
  const strengths = Array.isArray(job.strengths) ? (job.strengths as string[]) : [];
  const gaps = Array.isArray(job.gaps) ? (job.gaps as string[]) : [];
  const applyCmd = `/apply ${job.url || job.key}`;
  const canSkip = job.status !== "skipped";
  const canApplyNow = !job.tracker_status;
  const canFillForm = Boolean(job.cv_pdf) && !job.tracker_status;

  return (
    <Drawer onClose={onClose} title={job.title || job.key}>
      <div className="drawer-head">
        <div className="drawer-head-top">
          <div>
            <h2>{job.title || job.key}</h2>
            <p className="company">
              {job.company || "Unknown company"}
              {job.location ? ` · ${job.location}` : ""}
            </p>
          </div>
          <button className="icon-btn" type="button" onClick={onClose} aria-label="Close details">
            <Icon name="close" />
          </button>
        </div>
        <div className="drawer-chips">
          <span className={`chip ${String(job.status || "").toLowerCase()}`}>{missing(job.status)}</span>
          <span className={`chip ${String(job.fit || "unscored").toLowerCase()}`}>{job.fit || "unscored"}</span>
          {job.queued && <span className="chip queued">queued</span>}
          {job.tracker_status && <span className={`chip ${String(job.tracker_status).toLowerCase()}`}>{job.tracker_status}</span>}
          {job.eligibility && <span className={`chip ${String(job.eligibility).toLowerCase()}`}>{job.eligibility}</span>}
          {job.portal && <span className="chip">{job.portal}</span>}
        </div>
      </div>

      <div className="drawer-body">
        <section className="drawer-section">
          <h3>Details</h3>
          <dl className="kv">
            <dt>Posting</dt>
            <dd>
              {job.url ? (
                <a href={job.url} target="_blank" rel="noreferrer">
                  Open listing <Icon name="external" size={12} />
                </a>
              ) : (
                "-"
              )}
            </dd>
            <dt>Seen</dt>
            <dd>{formatDate(job.first_seen)}</dd>
            <dt>Posted</dt>
            <dd>{formatDate(job.posting_date)}</dd>
            <dt>Rank score</dt>
            <dd className="mono">{typeof job.rank_score === "number" ? job.rank_score : "-"}</dd>
            {job.deadline ? (
              <>
                <dt>Deadline</dt>
                <dd>{formatDate(String(job.deadline))}</dd>
              </>
            ) : null}
            {job.application_folder ? (
              <>
                <dt>Archive</dt>
                <dd>
                  <a href={fileUrl(`${job.application_folder}/outcome.md`)} target="_blank" rel="noreferrer">
                    {String(job.application_folder).split("/").pop()}
                  </a>
                </dd>
              </>
            ) : null}
          </dl>
        </section>

        {job.note && (
          <section className="drawer-section">
            <h3>Notes from /rank</h3>
            <div className="callout">{job.note}</div>
          </section>
        )}

        {job.rank_verdict && (
          <section className="drawer-section">
            <h3>Verdict</h3>
            <div className="callout">{job.rank_verdict}</div>
          </section>
        )}

        {(strengths.length > 0 || gaps.length > 0) && (
          <section className="drawer-section">
            <h3>Fit assessment</h3>
            {strengths.length > 0 && (
              <ul className="plus-list">
                {strengths.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            )}
            {gaps.length > 0 && (
              <ul className="minus-list" style={{ marginTop: strengths.length > 0 ? "0.5rem" : 0 }}>
                {gaps.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            )}
          </section>
        )}

        {job.auto_status ? (
          <section className="drawer-section">
            <h3>Auto run</h3>
            <div className="callout">
              <strong>{String(job.auto_status)}</strong>
              {typeof job.auto_score === "number" ? ` (score ${job.auto_score})` : ""}
              {job.auto_reason ? `: ${String(job.auto_reason)}` : ""}
            </div>
            {Array.isArray(job.auto_warnings) && job.auto_warnings.length > 0 && (
              <ul className="minus-list" style={{ marginTop: "0.5rem" }}>
                {(job.auto_warnings as string[]).map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            )}
          </section>
        ) : null}

        {job.application_condition != null && String(job.application_condition) !== "" && (
          <section className="drawer-section">
            <h3>Condition</h3>
            <div className="callout">{String(job.application_condition)}</div>
          </section>
        )}

        {(job.cv_pdf || job.cover_pdf) && (
          <section className="drawer-section" style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            <h3>Materials</h3>
            <PdfRow label="CV" path={job.cv_pdf} jobName={job.company || job.key} />
            <PdfRow label="Cover letter" path={job.cover_pdf} jobName={job.company || job.key} />
          </section>
        )}

        <section className="drawer-section">
          <h3>Run in chat</h3>
          <div className="apply-cmd">
            <span>{applyCmd}</span>
            <button
              className="icon-btn"
              type="button"
              aria-label="Copy apply command"
              onClick={async () => {
                const ok = await copyText(applyCmd);
                onFlash(ok ? `Copied ${applyCmd}` : "Could not copy — select the command manually.");
              }}
            >
              <Icon name="copy" size={13} />
            </button>
          </div>
        </section>
      </div>

      <div className="drawer-foot">
        {canApplyNow && (
          <button className="btn" type="button" onClick={() => onApplyNow(job)} title="Shows the fit evaluation and asks before drafting. If you say yes, it tailors your CV and cover letter, then opens Chrome and fills the application for your review">
            <Icon name="send" size={14} /> Apply now
          </button>
        )}
        {canFillForm && (
          <button
            className="btn"
            type="button"
            onClick={() => onFillForm(job)}
            title="Opens Chrome on the employer's form, fills your details and uploads the PDFs. Never submits."
          >
            <Icon name="doc" size={14} /> Fill form
          </button>
        )}
        <button className="btn-quiet" type="button" onClick={() => onQueueToggle(job)}>
          <Icon name="queue" size={13} />
          {job.queued ? "Remove from queue" : "Queue for /apply"}
        </button>
        <button className="btn-quiet" type="button" onClick={() => onSubmitted(job)}>
          <Icon name="check" size={13} /> Mark submitted
        </button>
        {canSkip && (
          <button className="btn-quiet btn-danger" type="button" onClick={() => onSkip(job)}>
            <Icon name="minus" size={13} /> Skip
          </button>
        )}
      </div>
    </Drawer>
  );
}
