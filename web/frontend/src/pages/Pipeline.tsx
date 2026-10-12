import { useEffect, useMemo, useState } from "react";
import { fetchApplications, fileUrl, markApplied, patchTrackerStatus } from "../api";
import type { AppFile, AppFolder, TrackerRow } from "../types";
import { Empty, Icon, LoadError, PageSkeleton, formatDate, missing } from "../ui";

// Exactly the statuses /outcome and /gmail-sync write (see STAGES in web/api/store.py).
const TRACKER_STATUSES = [
  "applied",
  "interview",
  "offer",
  "hired",
  "rejected",
  "no_response",
  "withdrawn",
  "offer_declined",
] as const;
type TrackerStatus = (typeof TRACKER_STATUSES)[number];
type ColumnKey = "prepared" | TrackerStatus | "other";
const RAIL_WHEN_EMPTY = new Set<ColumnKey>(["hired", "withdrawn", "offer_declined"]);

const COLUMNS: { key: ColumnKey; label: string; color: string }[] = [
  { key: "prepared", label: "Prepared", color: "#9a6bff" },
  { key: "applied", label: "Applied", color: "#7c8aff" },
  { key: "interview", label: "Interview", color: "#3ddc97" },
  { key: "offer", label: "Offer", color: "#2fd1c5" },
  { key: "hired", label: "Hired", color: "#5ad17a" },
  { key: "rejected", label: "Rejected", color: "#ff7a8a" },
  { key: "no_response", label: "No response", color: "#8a93a8" },
  { key: "withdrawn", label: "Withdrawn", color: "#ffc24b" },
  { key: "offer_declined", label: "Offer declined", color: "#b8a3ff" },
  { key: "other", label: "Other", color: "#6b7280" },
];

function stageOf(row: TrackerRow): string {
  return String(row.stage || row.status || "applied");
}

function columnOf(row: TrackerRow): ColumnKey {
  const stage = stageOf(row);
  return (TRACKER_STATUSES as readonly string[]).includes(stage) ? (stage as TrackerStatus) : "other";
}

function fitNumber(row: TrackerRow): number | null {
  const raw = String(row.fit_rating ?? "").trim();
  const value = Number(raw);
  return raw !== "" && Number.isFinite(value) ? Math.round(value) : null;
}

function followupInfo(row: TrackerRow): { text: string; due: boolean; warn: boolean } {
  if (row.followup_due) return { text: "follow up now", due: true, warn: true };
  const quiet = row.days_quiet == null ? "" : ` · quiet ${row.days_quiet}d`;
  if (row.next_followup) return { text: `follow up ${formatDate(row.next_followup)}${quiet}`, due: false, warn: false };
  return { text: "closed", due: false, warn: false };
}

type Drag =
  | { type: "tracker"; index: number; from: TrackerStatus }
  | { type: "prepared"; key: string; label: string }
  | null;

function FileChip({ file }: { file: AppFile }) {
  const short = file.name.length > 22 ? `${file.name.slice(0, 20)}…${file.name.split(".").pop()}` : file.name;
  return (
    <a href={fileUrl(file.path)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} title={file.name}>
      <Icon name="doc" size={12} />
      {short}
    </a>
  );
}

export default function Pipeline() {
  const [rows, setRows] = useState<TrackerRow[]>([]);
  const [prepared, setPrepared] = useState<AppFolder[]>([]);
  const [drafts, setDrafts] = useState<AppFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [drag, setDrag] = useState<Drag>(null);
  const [overCol, setOverCol] = useState<string | null>(null);

  function reload() {
    return fetchApplications()
      .then((apps) => {
        setRows(apps.open);
        setPrepared(apps.prepared);
        setDrafts(apps.drafts);
      })
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

  const byStatus = useMemo(() => {
    const map: Record<string, TrackerRow[]> = {};
    for (const status of TRACKER_STATUSES) map[status] = [];
    map.other = [];
    for (const row of rows) map[columnOf(row)].push(row);
    return map;
  }, [rows]);

  async function moveTracker(index: number, to: TrackerStatus) {
    const row = rows.find((item) => item.index === index);
    if (!row || stageOf(row) === to) return;
    const from = String(row.status || "applied");
    setRows((current) => current.map((item) => (item.index === index ? { ...item, status: to, stage: to } : item)));
    try {
      await patchTrackerStatus(index, to);
      setFlash(`${row.company || "Application"} moved to ${to.replace("_", " ")}.`);
    } catch (err) {
      setFlash(err instanceof Error ? err.message : "Could not update status");
      setRows((current) => current.map((item) => (item.index === index ? { ...item, status: from, stage: undefined } : item)));
    }
  }

  async function submitPrepared(key: string, label: string) {
    try {
      const result = await markApplied(key);
      await reload();
      setFlash(
        result.created
          ? `Logged ${label} as submitted — tracker row created.`
          : `${label} is already on the tracker (${result.row.status || "applied"}).`,
      );
    } catch (err) {
      setFlash(err instanceof Error ? err.message : "Could not mark submitted");
    }
  }

  function onDrop(status: ColumnKey) {
    if (!drag) return;
    if (drag.type === "tracker") {
      if (status !== "prepared" && status !== "other") void moveTracker(drag.index, status);
    } else if (status === "applied") {
      void submitPrepared(drag.key, drag.label);
    }
    setDrag(null);
    setOverCol(null);
  }

  const columnCount = (key: ColumnKey) => (key === "prepared" ? prepared.length : (byStatus[key] ?? []).length);
  const visibleColumns = COLUMNS.filter((column) => column.key !== "other" || columnCount("other") > 0);
  // Rarely used stages collapse to a thin rail while empty. They remain drop targets and the card picker still reaches them.
  const isRail = (key: ColumnKey) => RAIL_WHEN_EMPTY.has(key) && columnCount(key) === 0;
  const gridTemplate = visibleColumns.map((column) => (isRail(column.key) ? "3rem" : "minmax(160px, 1fr)")).join(" ");

  if (error && rows.length === 0 && prepared.length === 0) {
    return <LoadError>Could not load pipeline: {error}</LoadError>;
  }
  if (loading) return <PageSkeleton variant="board" />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">
            Pipeline
            <span className="count-pill">{rows.length} tracked</span>
          </h1>
          <p className="page-sub">
            Drag a card to update its stage — or use the picker on the card. Cards start on the Jobs page: mark a
            listing <strong>Submitted</strong> there and it lands on this board.
          </p>
        </div>
      </div>

      {flash && <p className="flash">{flash}</p>}

      <div className="kanban" style={{ gridTemplateColumns: gridTemplate }}>
        {visibleColumns.map((column) => {
          const count = columnCount(column.key);
          const droppable = column.key !== "prepared" && column.key !== "other";
          return (
            <section
              className={`kcol${overCol === column.key && drag ? " drag-over" : ""}${isRail(column.key) ? " rail" : ""}`}
              key={column.key}
              onDragOver={(e) => {
                if (!drag || !droppable) return;
                if (drag.type === "prepared" && column.key !== "applied") return;
                e.preventDefault();
                setOverCol(column.key);
              }}
              onDragLeave={() => setOverCol((current) => (current === column.key ? null : current))}
              onDrop={(e) => {
                e.preventDefault();
                onDrop(column.key);
              }}
            >
              <header className="kcol-head">
                <span className="kcol-dot" style={{ background: column.color }} />
                <h3>{column.label}</h3>
                <span className="n">{count}</span>
              </header>
              <div className="kcol-body">
                {column.key === "prepared" ? (
                  prepared.length === 0 ? (
                    <p className="note" style={{ margin: "0.3rem 0.2rem", fontSize: "12px" }}>
                      Drafts from /apply land here before you send them.
                    </p>
                  ) : (
                    prepared.map((item) => {
                      const label = item.company && item.title ? `${item.company} · ${item.title}` : item.slug;
                      const draggable = item.kind === "listing" && Boolean(item.key);
                      return (
                        <article
                          className={`kcard${drag?.type === "prepared" && drag.key === item.key ? " dragging" : ""}`}
                          key={`${item.kind}-${item.slug}`}
                          draggable={draggable}
                          onDragStart={() => setDrag({ type: "prepared", key: String(item.key), label })}
                          onDragEnd={() => {
                            setDrag(null);
                            setOverCol(null);
                          }}
                          title={draggable ? "Drag onto Applied once you submit" : "Archive packet — submit via the Jobs page"}
                        >
                          <div className="kcard-top">
                            <h4>{item.title || item.slug}</h4>
                            {item.outcome_status && (
                              <span className={`chip ${String(item.outcome_status).toLowerCase()}`}>{item.outcome_status}</span>
                            )}
                          </div>
                          <p className="co">{item.company || item.slug}</p>
                          <div className="kcard-foot">
                            <span>{item.files.length} file{item.files.length === 1 ? "" : "s"}</span>
                            <span className="grow" />
                            {item.files.slice(0, 2).map((file) => (
                              <FileChip file={file} key={file.path} />
                            ))}
                          </div>
                        </article>
                      );
                    })
                  )
                ) : (byStatus[column.key] ?? []).length === 0 ? (
                  <p className="note" style={{ margin: "0.3rem 0.2rem", fontSize: "12px" }}>
                    {drag ? "Drop to move here" : "Nothing here yet."}
                  </p>
                ) : (
                  (byStatus[column.key] ?? []).map((row) => (
                    <article
                      className={`kcard${drag?.type === "tracker" && drag.index === row.index ? " dragging" : ""}`}
                      key={row.index}
                      draggable
                      onDragStart={() =>
                        setDrag({ type: "tracker", index: row.index ?? 0, from: String(row.status || "applied") as TrackerStatus })
                      }
                      onDragEnd={() => {
                        setDrag(null);
                        setOverCol(null);
                      }}
                    >
                      <div className="kcard-top">
                        <h4>{row.role || "Untitled role"}</h4>
                        {fitNumber(row) != null && (
                          <span className="chip fitnum" title="Fit score when you applied">
                            {fitNumber(row)}
                          </span>
                        )}
                      </div>
                      <p className="co">
                        {row.company || "Unknown"} · applied {formatDate(row.date)}
                      </p>
                      <div className="kcard-foot">
                        <span className={`quiet${followupInfo(row).warn ? " warn" : ""}`}>{followupInfo(row).text}</span>
                        <span className="grow" />
                        {row.cv_file && <FileChip file={{ name: "CV", path: row.cv_file.replace(/\\/g, "/"), kind: "pdf" }} />}
                        {row.cover_letter_file && (
                          <FileChip file={{ name: "Letter", path: row.cover_letter_file.replace(/\\/g, "/"), kind: "pdf" }} />
                        )}
                      </div>
                      <select
                        aria-label={`Change stage for ${row.company || "application"}`}
                        value={stageOf(row)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => void moveTracker(row.index ?? 0, e.target.value as TrackerStatus)}
                      >
                        {TRACKER_STATUSES.map((status) => (
                          <option key={status} value={status}>
                            {status.replace("_", " ")}
                          </option>
                        ))}
                        {columnOf(row) === "other" && <option value={stageOf(row)}>{missing(row.status)}</option>}
                      </select>
                    </article>
                  ))
                )}
              </div>
            </section>
          );
        })}
      </div>

      {drafts.length > 0 && (
        <section style={{ marginTop: "1.1rem" }}>
          <div className="card-title" style={{ margin: "0 0 0.6rem" }}>
            <h2 style={{ fontSize: "15px" }}>Unlinked draft PDFs</h2>
          </div>
          <div className="folder-grid">
            <article className="folder-card">
              <h3>Drafts in cv/ and cover_letters/</h3>
              <p className="co">{drafts.length} compiled PDF{drafts.length === 1 ? "" : "s"} not tied to a tracked application</p>
              <ul className="file-list">
                {drafts.map((file) => (
                  <li key={file.path}>
                    <FileChip file={file} />
                  </li>
                ))}
              </ul>
            </article>
          </div>
        </section>
      )}

      {rows.length === 0 && prepared.length === 0 && (
        <Empty title="Pipeline is empty">
          <p>
            Mark listings <strong>Submitted</strong> on the Jobs page — they appear here as cards you can drag through
            interviews, offers, and closes.
          </p>
        </Empty>
      )}
    </>
  );
}
