import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, media } from "../api";
import { Icon } from "../components/Icon";
import { Alert, Bar, Empty, Modal, Spinner, StageList, StatusBadge, toneOf, useToast } from "../components/ui";
import { fmtDuration, fmtTime } from "../format";
import { usePoll } from "../hooks";
import type { Clip, Project } from "../types";

export default function ProjectView() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const proj = usePoll(() => api.project(id), [id], 1500);
  const p = proj.data;
  const active = p?.status === "processing" || p?.status === "queued";
  const clips = usePoll(() => api.clips(id), [id, p?.clip_count], 2500);
  const [filter, setFilter] = useState<"all" | "approved" | "candidate" | "rejected">("all");
  const [sort, setSort] = useState<"score" | "time">("score");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const shown = useMemo(() => {
    const list = (clips.data ?? []).filter((c) => filter === "all" || c.status === filter);
    return [...list].sort((a, b) => (sort === "score" ? b.score - a.score : a.start - b.start));
  }, [clips.data, filter, sort]);

  if (proj.error && !p) return <Alert kind="danger" title="Project not found">{proj.error}</Alert>;
  if (!p) return <div className="skeleton" style={{ height: 200 }} />;

  const reprocess = async () => {
    try {
      await api.process(p.id, {});
      proj.reload();
      toast("Processing queued");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const cancel = async () => {
    if (!p.job) return;
    try {
      await api.cancelJob(p.job.id);
      toast("Cancelling…");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const updateClip = (c: Clip) => clips.setData((list) => (list ?? []).map((x) => (x.id === c.id ? c : x)));

  return (
    <div className="stack lg">
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <Link to="/projects" className="small muted row" style={{ gap: 4, marginBottom: 6 }}>
            <Icon name="chevronLeft" size={14} /> Projects
          </Link>
          <div className="row" style={{ gap: 12 }}>
            <h1 className="ellipsis">{p.name}</h1>
            <StatusBadge status={p.status} />
          </div>
          <p className="sub">
            {fmtDuration(p.duration)} · {p.width}×{p.height}
            {p.source_url && (
              <>
                {" · "}
                <a href={p.source_url} target="_blank" rel="noreferrer noopener" className="ellipsis">
                  source link
                </a>
              </>
            )}
            {!p.source_available && p.status !== "new" && p.source_type !== "url" && " · source file missing"}
          </p>
        </div>
        <div className="row wrap">
          {p.clip_count > 0 && (
            <>
              <a className="btn sm" href={media.transcript(p.id, "srt")}>
                <Icon name="file" /> Transcript .srt
              </a>
              <a className="btn sm" href={media.transcript(p.id, "json")} download={`${p.id}-transcript.json`}>
                <Icon name="file" /> .json
              </a>
            </>
          )}
          {!active && (
            <button className="btn sm" onClick={reprocess} disabled={!p.source_available && p.source_type !== "url"}>
              <Icon name="refresh" /> {p.status === "new" ? "Start" : "Re-process"}
            </button>
          )}
          <button className="btn sm danger" onClick={() => setConfirmDelete(true)}>
            <Icon name="trash" /> Delete
          </button>
        </div>
      </div>

      {active && p.job && (
        <div className="card">
          <div className="card-head">
            <div className="stack sm" style={{ gap: 2 }}>
              <h2>{p.job.status === "queued" ? "Waiting in queue" : "Processing"}</h2>
              <span className="small muted">
                {p.job.status === "queued" ? "Jobs run one at a time to protect memory." : `${Math.round(p.job.progress * 100)}% overall`}
              </span>
            </div>
            <button className="btn sm" onClick={cancel}>
              <Icon name="stop" /> Cancel
            </button>
          </div>
          <div className="card-body stack">
            <Bar value={p.job.progress} />
            <StageList job={p.job} />
          </div>
        </div>
      )}

      {p.status === "failed" && (
        <Alert kind="danger" title="Processing failed">
          {p.error}
          <div style={{ marginTop: 10 }}>
            <button className="btn sm" onClick={reprocess}>
              <Icon name="refresh" /> Try again
            </button>
          </div>
        </Alert>
      )}
      {p.status === "cancelled" && <Alert kind="warn" title="Processing was cancelled">Click Re-process to start again. Finished steps (download, transcript) are reused.</Alert>}
      {p.status === "ready" && p.job?.stages?.score?.message?.includes("not padding") && <Alert kind="info">{p.job.stages.score.message}</Alert>}
      {p.status === "ready" && p.job?.stages?.metadata?.message?.startsWith("Local AI unavailable") && <Alert kind="warn">{p.job.stages.metadata.message}</Alert>}

      {(clips.data?.length ?? 0) > 0 && (
        <>
          <div className="row between wrap">
            <div className="row" style={{ gap: 14 }}>
              <h2>Clips</h2>
              <span className="muted small">
                {p.rendered_count}/{p.clip_count} rendered · {p.approved_count} approved
              </span>
            </div>
            <div className="row wrap">
              <div className="seg">
                {(["all", "approved", "candidate", "rejected"] as const).map((f) => (
                  <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
                    {f === "candidate" ? "unreviewed" : f}
                  </button>
                ))}
              </div>
              <div className="seg">
                <button className={sort === "score" ? "active" : ""} onClick={() => setSort("score")}>
                  By score
                </button>
                <button className={sort === "time" ? "active" : ""} onClick={() => setSort("time")}>
                  By time
                </button>
              </div>
            </div>
          </div>
          {shown.length === 0 ? (
            <Empty icon="film" title="No clips match this filter" />
          ) : (
            <div className="gallery">
              {shown.map((c) => (
                <ClipCard key={c.id} clip={c} onChange={updateClip} reload={clips.reload} />
              ))}
            </div>
          )}
        </>
      )}

      {p.status === "new" && (
        <Empty icon="play" title="Ready to process">
          <button className="btn primary" style={{ marginTop: 8 }} onClick={reprocess}>
            <Icon name="zap" /> Find clips
          </button>
        </Empty>
      )}

      {confirmDelete && <DeleteDialog project={p} onClose={() => setConfirmDelete(false)} onDeleted={() => nav("/projects")} />}
    </div>
  );
}

function DeleteDialog({ project, onClose, onDeleted }: { project: Project; onClose: () => void; onDeleted: () => void }) {
  const toast = useToast();
  const [exports, setExports] = useState(false);
  const [source, setSource] = useState(false);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.deleteProject(project.id, { delete_exports: exports, delete_source: source });
      toast(`Project deleted · ${r.freed_mb} MB freed${r.exports_kept_at ? " · rendered clips kept" : ""}`);
      onDeleted();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Delete project?"
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn danger solid" onClick={go} disabled={busy}>
            {busy ? <Spinner /> : <Icon name="trash" />} Delete
          </button>
        </>
      }
    >
      <p className="muted">Removes the project, its transcript and temporary files.</p>
      <label className="check">
        <input type="checkbox" checked={exports} onChange={(e) => setExports(e.target.checked)} />
        <span>Also delete the rendered clips (MP4 exports)</span>
      </label>
      {project.source_owned ? (
        <label className="check">
          <input type="checkbox" checked={source} onChange={(e) => setSource(e.target.checked)} />
          <span>Also delete the {project.source_type === "url" ? "downloaded" : "uploaded"} source video</span>
        </label>
      ) : (
        <p className="small muted">Your original file ({project.source_name}) is never deleted.</p>
      )}
    </Modal>
  );
}

const RENDER_LABEL: Record<string, string> = {
  pending: "Not rendered yet",
  queued: "Queued for rendering",
  rendering: "Rendering…",
  failed: "Render failed",
  stale: "Edited, re-render to update",
};

export function ClipCard({ clip: c, onChange, reload }: { clip: Clip; onChange: (c: Clip) => void; reload: () => void }) {
  const toast = useToast();
  const [why, setWhy] = useState(false);
  const tone = toneOf(c.score, c.analysis.label);
  const arClass = c.edit.aspect_ratio === "1:1" ? "ar-11" : c.edit.aspect_ratio === "16:9" ? "ar-169" : "";

  const setStatus = async (status: Clip["status"]) => {
    try {
      onChange(await api.patchClip(c.id, { status: c.status === status ? "candidate" : status }));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const render = async () => {
    try {
      await api.renderClip(c.id);
      onChange({ ...c, render_status: "queued" });
      setTimeout(reload, 500);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className={`card clip-card ${c.status === "rejected" ? "rejected" : ""}`}>
      <div className={`clip-media ${arClass}`}>
        {c.has_video && c.render_status !== "rendering" ? (
          <video src={media.video(c)} poster={c.has_thumb ? media.thumb(c) : undefined} controls preload="none" playsInline />
        ) : c.has_thumb ? (
          <img src={media.thumb(c)} alt="" />
        ) : null}
        {(!c.has_video || ["queued", "rendering", "failed"].includes(c.render_status)) && (
          <div className="overlay">
            <div className="stack sm" style={{ alignItems: "center" }}>
              {c.render_status === "rendering" || c.render_status === "queued" ? <Spinner size={22} /> : <Icon name={c.render_status === "failed" ? "alert" : "film"} size={22} />}
              <span className="small">{RENDER_LABEL[c.render_status] ?? ""}</span>
              {c.render_status === "failed" && c.render_error && <span className="tiny" style={{ opacity: 0.8 }}>{c.render_error.slice(0, 140)}</span>}
              {["pending", "failed", "stale"].includes(c.render_status) && (
                <button className="btn sm primary" onClick={render}>
                  <Icon name="play" /> Render
                </button>
              )}
            </div>
          </div>
        )}
        <div className="tl">
          <span className={`score-chip ${tone}`} title={c.analysis.label}>
            {Math.round(c.score)}
            <small>/100</small>
          </span>
        </div>
        <div className="tr">
          {c.status === "approved" && (
            <span className="badge success">
              <Icon name="check" size={11} /> approved
            </span>
          )}
          {c.render_status === "stale" && c.has_video && <span className="badge warn">edited</span>}
        </div>
      </div>
      <div className="clip-body">
        <div className="row wrap" style={{ gap: 6 }}>
          <span className={`badge ${tone}`}>{c.analysis.label}</span>
          <span className="badge">{c.category}</span>
          <span className="badge">
            {fmtTime(c.start)} · {Math.round(c.duration)}s
          </span>
        </div>
        <div className="clip-title">{c.title || "Untitled clip"}</div>
        {c.hook && <div className="clip-hook">“{c.hook}”</div>}
        <button className="btn ghost sm" style={{ alignSelf: "flex-start", marginLeft: -8 }} onClick={() => setWhy(!why)}>
          <Icon name="info" /> {why ? "Hide" : "Why this score"}
        </button>
        {why && <ScoreBreakdown clip={c} />}
      </div>
      <div className="clip-actions">
        <button className={`btn sm icon ${c.status === "approved" ? "on" : ""}`} title="Approve" aria-label="Approve" onClick={() => setStatus("approved")}>
          <Icon name="thumbsUp" />
        </button>
        <button className={`btn sm icon ${c.status === "rejected" ? "on" : ""}`} title="Reject" aria-label="Reject" onClick={() => setStatus("rejected")}>
          <Icon name="thumbsDown" />
        </button>
        <span className="spacer" />
        <Link to={`/clips/${c.id}`} className="btn sm">
          <Icon name="edit" /> Edit
        </Link>
        {c.has_video ? (
          <a className="btn sm primary" href={media.download(c)} title="Download MP4">
            <Icon name="download" />
          </a>
        ) : (
          <button className="btn sm primary" disabled title="Render first">
            <Icon name="download" />
          </button>
        )}
      </div>
    </div>
  );
}

export function ScoreBreakdown({ clip: c }: { clip: Clip }) {
  const a = c.analysis;
  return (
    <div className="stack" style={{ gap: 10, padding: "4px 0" }}>
      <div className="stack sm">
        {Object.entries(a.scores).map(([k, v]) => (
          <div className="crit" key={k}>
            <span className="muted">
              {a.criteria[k] ?? k} <span className="tiny">({a.weights[k]}%)</span>
            </span>
            <Bar value={v / 100} tone={v >= 70 ? "success" : v < 45 ? "warn" : undefined} />
            <span className="small" style={{ textAlign: "right" }}>{Math.round(v)}</span>
          </div>
        ))}
      </div>
      {a.reasons.length > 0 && (
        <div className="stack sm">
          {a.reasons.map((r) => (
            <div className="reason" key={r}>
              <Icon name="check" size={13} className="" />
              <span>{r}</span>
            </div>
          ))}
        </div>
      )}
      {a.weaknesses.length > 0 && (
        <div className="stack sm">
          {a.weaknesses.map((r) => (
            <div className="reason" key={r} style={{ color: "var(--warn)" }}>
              <Icon name="alert" size={13} />
              <span>{r}</span>
            </div>
          ))}
        </div>
      )}
      <p className="tiny muted">A rule-based estimate of how engaging this clip might be, used to rank clips. It is not a prediction of views.</p>
    </div>
  );
}
