import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Icon, type IconName } from "../components/Icon";
import { Alert, Bar, JobTime, plural } from "../components/ui";
import { fmtMB } from "../format";
import { usePoll } from "../hooks";
import type { Dashboard } from "../types";

const DISMISS_KEY = "clipstudio.gettingStarted.dismissed";

/** Home (R3): what needs you right now, then recent work. */
export default function Home() {
  const { data, error } = usePoll(api.dashboard, [], 3000);
  const sys = usePoll(api.system, [], 10000).data;
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(DISMISS_KEY) === "1";
    } catch {
      return false;
    }
  });
  const showGuide = !!data && !dismissed && (data.campaigns === 0 || data.projects === 0 || data.clips_approved === 0);
  const todos = data ? buildTodos(data) : [];

  return (
    <div className="stack lg" style={{ maxWidth: 1100 }}>
      <div className="page-head">
        <div>
          <h1>Home</h1>
          <p className="sub">What to do next, across all your campaigns and videos.</p>
        </div>
        <Link to="/create" className="btn primary lg">
          <Icon name="plus" /> Create
        </Link>
      </div>

      {error && <Alert kind="danger" title="Can't reach the ClipStudio backend">{error}</Alert>}
      {sys && sys.disk.low && (
        <Alert kind="warn" title="Disk space is low">
          Only {fmtMB(sys.disk.free_mb)} free.{" "}
          <Link to="/storage">
            <u>Free up space in Storage</u>
          </Link>{" "}
          or move the working folder to an external drive in Settings.
        </Alert>
      )}

      {showGuide && data && (
        <section className="card">
          <div className="card-head">
            <h2>Getting started</h2>
            <button
              className="btn sm ghost"
              onClick={() => {
                setDismissed(true);
                try {
                  localStorage.setItem(DISMISS_KEY, "1");
                } catch {
                  /* private mode */
                }
              }}
            >
              Hide
            </button>
          </div>
          <div className="card-body guide">
            <GuideStep n={1} done={data.campaigns > 0} title="Set up a campaign" body="Pick its type and paste the brand's brief. It remembers their logo, sound, length and caption rules. Skip this if you're clipping for yourself." to="/campaigns/new" cta="New campaign" />
            <GuideStep n={2} done={data.projects > 0} title="Add videos" body="Upload files or paste links (on the campaign page, or with Create). Clips are found and edited for you." to="/create" cta="Create" />
            <GuideStep n={3} done={data.clips_approved > 0} title="Review and post" body="Approve the clips you like (A key). Then open the campaign's Post queue: download each clip, copy its caption, post it and press Mark posted." to="/projects" cta="Videos" />
          </div>
        </section>
      )}

      {data && (
        <section className="card">
          <div className="card-head">
            <h2>To do</h2>
            <span className="small muted">in the order you'd work through it</span>
          </div>
          <div className="card-body tight">
            {todos.length === 0 && (
              <p className="small muted" style={{ padding: "10px 0" }}>
                Nothing needs you right now. Press <b>Create</b> to add videos, or open a campaign to add videos to it.
              </p>
            )}
            {GROUPS.map((g) => {
              const items = todos.filter((t) => t.group === g.key);
              if (!items.length) return null;
              return (
                <div key={g.key} className="todo-group">
                  <span className="section-title">{g.label}</span>
                  {items.map((t) => (
                    <Link key={t.key} to={t.to} className="todo-row">
                      <span className={`todo-ic ${t.tone}`}>
                        <Icon name={t.icon} size={15} />
                      </span>
                      <span className="todo-text">
                        <b>{t.title}</b>
                        {t.detail && <span className="small muted">{t.detail}</span>}
                      </span>
                      <span className="btn sm">
                        {t.cta} <Icon name="chevronRight" size={14} />
                      </span>
                    </Link>
                  ))}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {data && data.active_jobs.length > 0 && (
        <section className="card">
          <div className="card-head">
            <h2>Working on it</h2>
            <span className="badge accent">{data.active_jobs.length} active</span>
          </div>
          <div className="card-body stack">
            {data.active_jobs.map((j) => {
              const p = data.recent.find((r) => r.id === j.project_id);
              const stage = j.stage ? j.stages[j.stage]?.label : null;
              return (
                <Link key={j.id} to={`/projects/${j.project_id}`} className="stack sm" style={{ color: "inherit" }}>
                  <div className="row between">
                    <strong className="ellipsis">{p?.name ?? (j.kind === "render" ? "Rendering a clip" : "Video")}</strong>
                    <span className="small muted">{j.status === "queued" ? "Waiting" : `${stage ?? "Working"} · ${Math.round(j.progress * 100)}%`}</span>
                  </div>
                  <Bar value={j.progress} />
                  <JobTime job={j} />
                </Link>
              );
            })}
          </div>
        </section>
      )}

    </div>
  );
}

function GuideStep({ n, done, title, body, to, cta }: { n: number; done: boolean; title: string; body: string; to: string; cta: string }) {
  return (
    <div className={`guide-step ${done ? "done" : ""}`}>
      <span className="gs-n">{done ? <Icon name="check" size={14} /> : n}</span>
      <div className="stack sm" style={{ gap: 4, flex: 1 }}>
        <b>{title}</b>
        <span className="small muted">{body}</span>
      </div>
      {!done && (
        <Link className="btn sm" to={to}>
          {cta}
        </Link>
      )}
    </div>
  );
}

const GROUPS = [
  { key: "fix", label: "Fix first" },
  { key: "review", label: "Review clips" },
  { key: "render", label: "Render" },
  { key: "post", label: "Post" },
  { key: "start", label: "Not started" },
] as const;

type Todo = { key: string; group: (typeof GROUPS)[number]["key"]; icon: IconName; tone: "warn" | "danger" | "accent" | "success"; title: string; detail?: string; to: string; cta: string };

const where = (name: string, campaign: string | null) => (campaign ? `${name} · ${campaign}` : name);

function buildTodos(d: Dashboard): Todo[] {
  const t = d.todo;
  const out: Todo[] = [];
  for (const c of t.campaign_setup) out.push({ key: `setup-${c.campaign_id}`, group: "fix", icon: "alert", tone: "warn", title: `Upload ${c.missing.join(" and ")}`, detail: `Campaign: ${c.name}. Its clips can't meet the brief without it.`, to: `/campaigns/${c.campaign_id}?tab=requirements`, cta: "Fix" });
  for (const v of t.failed_videos) out.push({ key: `fv-${v.id}`, group: "fix", icon: "x", tone: "danger", title: `Video failed: ${v.name}`, detail: v.error ?? undefined, to: `/projects/${v.id}`, cta: "Open" });
  for (const r of t.failed_renders) out.push({ key: `fr-${r.id}`, group: "fix", icon: "x", tone: "danger", title: `Render failed: ${r.title || "a clip"}`, to: `/clips/${r.id}`, cta: "Open" });
  for (const r of t.review) out.push({ key: `rv-${r.project_id}`, group: "review", icon: "eye", tone: "accent", title: `${plural(r.n, "clip")} to approve or reject`, detail: where(r.name, r.campaign), to: `/projects/${r.project_id}`, cta: "Review" });
  for (const r of t.render) out.push({ key: `rn-${r.project_id}`, group: "render", icon: "refresh", tone: "accent", title: `${plural(r.n, "approved clip")} to render`, detail: where(r.name, r.campaign), to: `/projects/${r.project_id}`, cta: "Render" });
  for (const r of t.ready) out.push({ key: `rd-${r.campaign_id ?? "none"}`, group: "post", icon: "check", tone: "success", title: `${plural(r.n, "clip")} ready to post`, detail: r.name ? `Campaign: ${r.name}` : "No campaign", to: r.campaign_id ? `/campaigns/${r.campaign_id}?tab=queue` : "/projects", cta: "Post" });
  if (t.not_started) out.push({ key: "ns", group: "start", icon: "clock", tone: "warn", title: `${plural(t.not_started, "video")} added but never started`, detail: "Start them, or delete them to free disk space.", to: "/projects?view=not_started", cta: "See" });
  return out;
}
