import { Link } from "react-router-dom";
import { api, media } from "../api";
import { Icon } from "../components/Icon";
import { Bar, Empty, StatusBadge } from "../components/ui";
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
        <Link to={`/projects/${p.id}`} key={p.id} className="proj-row">
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
          <div className="small muted nowrap">{p.clip_count ? `${p.clip_count} clips${p.best_score ? ` · best ${Math.round(p.best_score)}` : ""}` : ""}</div>
          <StatusBadge status={p.status} />
        </Link>
      ))}
    </div>
  );
}

export default function Projects() {
  const { data } = usePoll(api.projects, [], 4000);
  return (
    <div className="stack lg">
      <div className="page-head">
        <div>
          <h1>Projects</h1>
          <p className="sub">{data ? `${data.length} project${data.length === 1 ? "" : "s"}` : ""}</p>
        </div>
        <Link to="/new" className="btn primary">
          <Icon name="plus" /> New project
        </Link>
      </div>
      <div className="card">
        {data && data.length === 0 ? (
          <Empty icon="film" title="No projects yet">
            Upload a video or paste a link to get started.
          </Empty>
        ) : (
          <ProjectList projects={data} />
        )}
      </div>
    </div>
  );
}
