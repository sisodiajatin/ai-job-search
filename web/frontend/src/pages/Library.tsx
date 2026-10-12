import { useEffect, useMemo, useState } from "react";
import { fetchApplications, fileUrl } from "../api";
import type { AppFile, AppFolder } from "../types";
import { Empty, Icon, LoadError, PageSkeleton } from "../ui";

function FolderCard({ folder }: { folder: AppFolder }) {
  const [expanded, setExpanded] = useState(false);
  const files = expanded ? folder.files : folder.files.slice(0, 4);
  return (
    <article className="folder-card">
      <div className="kcard-top">
        <h3>{folder.company && folder.title ? `${folder.company} · ${folder.title}` : folder.slug}</h3>
        {folder.outcome_status && (
          <span className={`chip ${String(folder.outcome_status).toLowerCase()}`}>{folder.outcome_status}</span>
        )}
      </div>
      <p className="co">{folder.slug}</p>
      {folder.files.length === 0 ? (
        <p className="co">Empty folder</p>
      ) : (
        <>
          <ul className="file-list">
            {files.map((file) => (
              <li key={file.path}>
                <a href={fileUrl(file.path)} target="_blank" rel="noreferrer">
                  <Icon name={file.kind === "pdf" ? "doc" : "doc"} size={13} />
                  {file.name}
                </a>
              </li>
            ))}
          </ul>
          {folder.files.length > 4 && (
            <button className="btn-quiet" type="button" onClick={() => setExpanded((v) => !v)} style={{ alignSelf: "flex-start" }}>
              {expanded ? "Show less" : `+${folder.files.length - 4} more`}
            </button>
          )}
        </>
      )}
    </article>
  );
}

export default function Library() {
  const [folders, setFolders] = useState<AppFolder[]>([]);
  const [drafts, setDrafts] = useState<AppFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetchApplications()
      .then((apps) => {
        setFolders(apps.folders);
        setDrafts(apps.drafts);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return folders;
    return folders.filter((folder) =>
      `${folder.slug} ${folder.company ?? ""} ${folder.title ?? ""}`.toLowerCase().includes(needle),
    );
  }, [folders, filter]);

  if (error) return <LoadError>Could not load library: {error}</LoadError>;
  if (loading) return <PageSkeleton variant="dashboard" />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">
            Library
            <span className="count-pill">{folders.length} archives</span>
          </h1>
          <p className="page-sub">
            Everything /apply and /outcome wrote to disk: per-application archives with the posting, CV, cover letter,
            and outcome notes. Files open straight from your machine — nothing leaves localhost.
          </p>
        </div>
        <div className="field search">
          <Icon name="search" size={14} />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter archives…"
            aria-label="Filter archives"
          />
        </div>
      </div>

      {folders.length === 0 ? (
        <Empty title="No application archives yet">
          <p>
            Run <code>/apply</code> in chat on a queued job — compiled CVs, cover letters, and outcome notes are stored
            under <code>documents/applications/</code> and show up here.
          </p>
        </Empty>
      ) : visible.length === 0 ? (
        <Empty title="No archives match">
          <p>Try a different company or role name.</p>
        </Empty>
      ) : (
        <div className="folder-grid">
          {visible.map((folder) => (
            <FolderCard folder={folder} key={folder.slug} />
          ))}
        </div>
      )}

      {drafts.length > 0 && (
        <section style={{ marginTop: "1.4rem" }}>
          <div className="card-title" style={{ margin: "0 0 0.6rem" }}>
            <h2 style={{ fontSize: "15px" }}>Compiled drafts</h2>
            <span className="hint">cv/ and cover_letters/</span>
          </div>
          <div className="folder-grid">
            {drafts.map((file) => (
              <a className="folder-card" href={fileUrl(file.path)} target="_blank" rel="noreferrer" key={file.path} style={{ color: "inherit" }}>
                <div className="kcard-top">
                  <h3>{file.name}</h3>
                  <span className="chip">pdf</span>
                </div>
                <p className="co">
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem" }}>
                    <Icon name="external" size={12} /> Open PDF
                  </span>
                </p>
              </a>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
