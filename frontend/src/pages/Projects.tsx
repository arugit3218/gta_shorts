import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, media } from "../api";
import { Icon } from "../components/Icon";
import { Bar, Empty, StatusBadge, useDialogs, useToast } from "../components/ui";
import { fmtAgo, fmtDuration } from "../format";
import { usePoll } from "../hooks";
import type { Project } from "../types";

export function ProjectList({ projects }: { projects: Project[] | null }) {
  if (!projects) {
    return (
      <div className="card-body stack">
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton" style={{ height: 40 }} />
        ))}
      </div>
    );
  }
  return (
    <div>
      {projects.map((p) => (
        <Link to={p.stage === "not_started" ? `/new?setup=${p.id}` : `/projects/${p.id}`} key={p.id} className="proj-row" title={p.stage === "not_started" ? "Choose settings and start" : undefined}>
          <div className="proj-thumb">
            {p.thumb_clip_id ? <img src={media.thumb({ id: p.thumb_clip_id, version: Math.round(p.updated_at) })} alt="" /> : <Icon name={p.source_type === "url" ? "link" : "film"} />}
          </div>
          <div className="stack sm" style={{ minWidth: 0, gap: 3 }}>
            <strong className="ellipsis">{p.name}</strong>
            <span className="small muted ellipsis">
              {p.duration ? fmtDuration(p.duration) : "–"}
              {p.width ? ` · ${p.width}×${p.height}` : ""} · {p.source_type === "url" ? "URL" : p.source_type === "upload" ? "Upload" : "Local file"} · {fmtAgo(p.created_at)}
            </span>
            {(p.status === "processing" || p.status === "queued") && p.job && <Bar value={p.job.progress} />}
          </div>
          <div className="small muted nowrap">{p.clip_count ? `${p.clip_count} clip${p.clip_count === 1 ? "" : "s"}${p.best_score ? ` · best ${Math.round(p.best_score)}` : ""}` : ""}</div>
          <StatusBadge status={p.status} />
        </Link>
      ))}
    </div>
  );
}

export default function Projects() {
  const toast = useToast();
  const { confirm } = useDialogs();
  const { data, reload } = usePoll(api.projects, [], 4000);
  const [params] = useSearchParams();
  const [showWaiting, setShowWaiting] = useState(params.get("view") === "not_started");
  // Only started work is listed. Videos uploaded but never started sit behind "Not started";
  // montage source videos are shown inside their montage.
  const active = data?.filter((p) => p.stage === "active") ?? null;
  const waiting = data?.filter((p) => p.stage === "not_started") ?? [];
  const deleteWaiting = async () => {
    const ok = await confirm({
      title: `Delete ${waiting.length === 1 ? "1 video that was never started" : `${waiting.length} videos that were never started`}?`,
      body: "Uploaded and downloaded copies are deleted to free disk space. Files you used in place stay where they are.",
      confirm: "Delete",
      danger: true,
    });
    if (!ok) return;
    let freed = 0;
    let done = 0;
    for (const p of waiting) {
      try {
        freed += (await api.deleteProject(p.id, { delete_exports: true, delete_source: true })).freed_mb;
        done++;
      } catch (e) {
        toast(`${p.name}: ${(e as Error).message}`, "error");
      }
    }
    toast(done === waiting.length ? `Deleted ${done} · ${freed} MB freed` : `Deleted ${done} of ${waiting.length} · ${freed} MB freed`, done === waiting.length ? "info" : "error");
    setShowWaiting(false);
    reload();
  };
  return (
    <div className="stack lg">
      <div className="page-head">
        <div>
          <h1>Videos</h1>
          <p className="sub">{active ? `${active.length} video${active.length === 1 ? "" : "s"} and music edits. Open one to review its clips.` : ""}</p>
        </div>
        <div className="row">
          {waiting.length > 0 && (
            <button className="btn sm" onClick={() => setShowWaiting((v) => !v)} title="Videos you uploaded but never started">
              <Icon name={showWaiting ? "chevronLeft" : "clock"} /> {showWaiting ? "Back to videos" : `Not started (${waiting.length})`}
            </button>
          )}
          <Link to="/create" className="btn primary">
            <Icon name="plus" /> Create
          </Link>
        </div>
      </div>
      {showWaiting && waiting.length > 0 ? (
        <div className="card">
          <div className="card-head">
            <h2>Not started</h2>
            <button className="btn sm danger" onClick={deleteWaiting}>
              <Icon name="trash" /> Delete all
            </button>
          </div>
          <p className="small muted" style={{ padding: "12px 20px 0" }}>
            Uploaded or added, but never processed. Click one to choose its settings and start it.
          </p>
          <ProjectList projects={waiting} />
        </div>
      ) : (
        <div className="card">
          {active && active.length === 0 ? (
            <Empty icon="film" title="No videos yet">
              Upload a video or paste a link to get started.
            </Empty>
          ) : (
            <ProjectList projects={active} />
          )}
        </div>
      )}
    </div>
  );
}
