import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, media } from "../api";
import { LinesEditor, ListEditor, TYPE_LABEL, useSafeZones } from "../components/campaign";
import { HelpTip } from "../components/help";
import { HOOK_TEMPLATES, HookStylePicker } from "../components/polish";
import { PostQueue, PostedBadge } from "../components/posting";
import { Icon } from "../components/Icon";
import { CoverageStatus, LogoPlacer, captionLogoOverlap } from "../components/logoPlacer";
import { LAYOUT_OPTIONS } from "../components/framing";
import { IntensityPicker, MiniPhone, StylePicker, campaignOptions, describeStyle, styleOf, type Intensity, type StyleKey } from "../components/styles";
import { Alert, Bar, Modal, Spinner, StatusBadge, attempt, plural, useDialogs, useToast } from "../components/ui";
import { fmtDuration } from "../format";
import { usePoll } from "../hooks";
import type { Brief, Campaign, Platform } from "../types";

type Tab = "overview" | "queue" | "requirements" | "posting" | "brief";
const TABS: [Tab, string][] = [
  ["overview", "Overview"],
  ["queue", "Post queue"],
  ["requirements", "Requirements"],
  ["posting", "Posting"],
  ["brief", "Brief"],
];
const num = (v: string): number | null => (v.trim() === "" ? null : Number(v));

type SetFn = <K extends keyof Brief>(k: K, v: Brief[K]) => void;
type SetSubFn = <K extends "logo" | "sound_rule" | "disclosure" | "hook_overlay" | "end_text">(k: K, patch: Partial<Brief[K]>) => void;

export default function CampaignView() {
  const { id = "" } = useParams();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find(([k]) => k === params.get("tab"))?.[0] ?? "overview") as Tab;
  const q = usePoll(() => api.campaign(id), [id]);
  const camp = q.data;
  const [b, setB] = useState<Brief | null>(null);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);

  // Load the brief into the editor once per campaign; later server updates are merged explicitly.
  const [loadedId, setLoadedId] = useState<string | null>(null);
  useEffect(() => {
    if (camp && loadedId !== camp.id) {
      setB(camp.brief);
      setLoadedId(camp.id);
    }
  }, [camp, loadedId]);
  // refresh the video list while something is processing
  const busyVideos = camp?.project_list?.some((p) => p.status === "processing" || p.status === "queued");
  useEffect(() => {
    // fast while videos process, slower otherwise (renders finishing still update Ready to post)
    // reload() drops a response that a click's refresh overtook, so a poll never undoes a click
    const t = setInterval(() => document.visibilityState === "visible" && q.reload(), busyVideos ? 4000 : 12000);
    return () => clearInterval(t);
  }, [busyVideos, id, q]);

  const dirty = useMemo(() => !!camp && !!b && JSON.stringify(camp.brief) !== JSON.stringify(b), [camp, b]);
  const skipGuard = useRef(false); // set after deleting the campaign: there's nothing left to save
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && !skipGuard.current && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (q.error && !camp) return <Alert kind="danger" title="Campaign not found">{q.error}</Alert>;
  if (!camp || !b) return <div className="skeleton" style={{ height: 300 }} />;

  const set: SetFn = (k, v) => setB((x) => (x ? { ...x, [k]: v } : x));
  const setSub: SetSubFn = (k, patch) => setB((x) => (x ? { ...x, [k]: { ...x[k], ...patch } } : x));
  /** Take the whole brief from the server (after Save, or a pasted brief). */
  const applyServer = (c: Campaign) => {
    q.setData(c);
    setB(c.brief);
  };
  /** Merge only what the server owns (uploaded logo/sound files, name, style, status) so unsaved edits survive. */
  const mergeServer = (c: Campaign) => {
    q.setData(c);
    setB((x) => {
      if (!x) return c.brief;
      const sl = c.brief.logo;
      const logo = x.logo.has_file || !sl.has_file ? { ...x.logo, file_name: sl.file_name, has_file: sl.has_file, animated: sl.animated, image_w: sl.image_w, image_h: sl.image_h } : sl;
      const mine = new Map(x.sounds.map((s) => [s.id, s]));
      const sounds = c.brief.sounds.map((s) => ({ ...s, ...(mine.has(s.id) ? { name: mine.get(s.id)!.name, start: mine.get(s.id)!.start } : {}) }));
      return { ...x, logo, sounds, name: c.brief.name, style: c.brief.style, intensity: c.brief.intensity, max_clips: c.brief.max_clips, layout: c.brief.layout, caption_preset: c.brief.caption_preset };
    });
  };
  const save = async () => {
    setSaving(true);
    try {
      const { logo, sounds, ...rest } = b;
      const { file_name: _f, has_file: _h, animated: _a, image_w: _w, image_h: _ih, ...logoEdit } = logo;
      const c = await api.patchCampaign(id, { name: b.name, brief: { ...rest, logo: logoEdit, sounds: sounds.map((s) => ({ id: s.id, name: s.name, start: s.start })) } });
      applyServer(c);
      toast("Saved. Clips that need it are marked for re-render.");
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    } finally {
      setSaving(false);
    }
  };
  const exportAll = async () => {
    setExporting(true);
    try {
      const r = await api.exportApproved(id);
      const failed = r.results.filter((x) => x.error).length;
      toast(`Exported ${plural(r.exported, "clip")} to ${r.folder}${failed ? ` · ${failed} failed` : ""}`, failed ? "error" : "info");
      q.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setExporting(false);
    }
  };
  const goTab = (t: Tab) => setParams(t === "overview" ? {} : { tab: t }, { replace: true });
  const ready = camp.ready ?? [];

  return (
    <div className="stack lg" style={{ maxWidth: 1180 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div style={{ minWidth: 0 }}>
          <Link to="/campaigns" className="small muted row" style={{ gap: 4, marginBottom: 6 }}>
            <Icon name="chevronLeft" size={14} /> Campaigns
          </Link>
          <div className="row wrap" style={{ gap: 10 }}>
            <InlineName value={camp.name} onSave={async (name) => mergeServer(await api.patchCampaign(id, { name }))} />
            <span className="badge accent">{TYPE_LABEL[b.type]}</span>
            {b.source && <span className="badge">{b.source}</span>}
            {camp.status === "archived" && <span className="badge warn">archived</span>}
          </div>
          <p className="sub">
            {plural(camp.projects, "video")} · {plural(camp.clips, "clip")} · {camp.approved} approved · {camp.ready_total ?? ready.length} ready to post
            {(camp.posted_total ?? 0) > 0 && ` · ${camp.posted_total} posted`}
          </p>
        </div>
        <div className="row wrap">
          <button className="btn sm" onClick={exportAll} disabled={exporting || (camp.ready_total ?? ready.length) === 0} title="Copy every clip still to post (approved and rendered) with its captions and checklist into one folder">
            {exporting ? <Spinner /> : <Icon name="package" />} Export ready clips
          </button>
          {b.type === "music" ? (
            <Link className="btn primary" to={`/montage/new?campaign=${id}`}>
              <Icon name="music" /> Make a music edit
            </Link>
          ) : (
            <button className="btn primary" onClick={() => (goTab("overview"), setTimeout(() => document.getElementById("add-videos")?.scrollIntoView({ behavior: "smooth" }), 50))}>
              <Icon name="plus" /> Add videos
            </button>
          )}
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="Campaign sections">
        {TABS.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => goTab(k)}>
            {l}
            {k === "queue" && (camp.ready_total ?? 0) > 0 && <span className="tab-count">{camp.ready_total}</span>}
          </button>
        ))}
      </div>

      {dirty && (
        <div className="save-bar" role="status">
          <span>
            <Icon name="edit" /> Unsaved changes
          </span>
          <span className="row">
            <button className="btn sm ghost" onClick={() => setB(camp.brief)}>
              Discard
            </button>
            <button className="btn sm primary" onClick={save} disabled={saving}>
              {saving ? <Spinner /> : <Icon name="check" />} Save changes
            </button>
          </span>
        </div>
      )}

      {tab === "overview" && <OverviewTab camp={camp} b={b} onServer={mergeServer} goTab={goTab} dirty={dirty} onSave={save} />}
      {tab === "queue" && (
        <PostQueue
          camp={camp}
          goPosting={() => goTab("posting")}
          onRefresh={q.refresh}
        />
      )}
      {tab === "requirements" && <RequirementsTab camp={camp} b={b} set={set} setSub={setSub} onServer={mergeServer} />}
      {tab === "posting" && <PostingTab b={b} set={set} setSub={setSub} />}
      {tab === "brief" && <BriefTab camp={camp} b={b} set={set} onServer={applyServer} onStatus={mergeServer} onDeleted={() => (skipGuard.current = true)} dirty={dirty} />}

      {blocker.state === "blocked" && (
        <Modal
          title="Leave without saving?"
          onClose={() => blocker.reset()}
          actions={
            <>
              <button className="btn" onClick={() => blocker.reset()}>
                Stay
              </button>
              <button className="btn danger" onClick={() => blocker.proceed()}>
                Discard changes
              </button>
              <button className="btn primary" onClick={async () => ((await save()) ? blocker.proceed() : blocker.reset())}>
                Save &amp; leave
              </button>
            </>
          }
        >
          <p className="muted">You changed this campaign's requirements but didn't save them.</p>
        </Modal>
      )}
    </div>
  );
}

function InlineName({ value, onSave }: { value: string; onSave: (v: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const commit = async () => {
    setEditing(false);
    if (v.trim() && v.trim() !== value) await onSave(v.trim());
    else setV(value);
  };
  if (editing)
    return (
      <input
        className="input name-input"
        value={v}
        autoFocus
        aria-label="Campaign name"
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") (setV(value), setEditing(false));
        }}
      />
    );
  return (
    <h1 className="name-inline">
      <span className="ellipsis">{value}</span>
      <button className="btn sm ghost icon" aria-label="Rename campaign" onClick={() => setEditing(true)}>
        <Icon name="edit" />
      </button>
    </h1>
  );
}

// ================================================================================== Overview
function OverviewTab({ camp, b, onServer, goTab, dirty, onSave }: { camp: Campaign; b: Brief; onServer: (c: Campaign) => void; goTab: (t: Tab) => void; dirty: boolean; onSave: () => Promise<boolean> }) {
  const list = camp.project_list ?? [];
  const ready = camp.ready ?? [];
  const saved = camp.brief;
  const [clipInstead, setClipInstead] = useState(false);
  const music = b.type === "music";
  // videos just added: highlighted in the list for a few seconds, which is scrolled into view
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!fresh.size) return;
    const t = setTimeout(() => setFresh(new Set()), 6000);
    return () => clearTimeout(t);
  }, [fresh]);
  const added = (ids: string[]) => {
    if (!ids.length) return;
    setFresh(new Set(ids));
    setTimeout(() => document.getElementById("campaign-videos")?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
  };
  const steps: { done: boolean; label: string; tab?: Tab; action?: string }[] = [
    { done: !!saved.raw_brief, label: "Brief read", tab: "brief", action: "Paste it" },
  ];
  if (saved.type === "logo" || saved.logo.mode !== "none") steps.push({ done: !!saved.logo.has_file, label: saved.logo.mode === "template" ? "Upload the brand's template file" : "Upload and place the logo", tab: "requirements", action: "Do it" });
  if (saved.sound_rule.mode === "burn") steps.push({ done: saved.sounds.some((s) => s.has_file), label: "Upload the brand's sound file", tab: "requirements", action: "Do it" });
  if (saved.disclosure.text || saved.required_caption_text) steps.push({ done: true, label: `Caption ready (${[saved.disclosure.text, saved.required_caption_text ? "required text" : ""].filter(Boolean).join(" + ")})` });
  if (!music) steps.push({ done: !!saved.style, label: "Style chosen", action: "Choose" });
  const done = steps.filter((s) => s.done).length;

  return (
    <div className="stack lg">
    {camp.summary && <BriefRead camp={camp} goTab={goTab} />}
    {(camp.outdated ?? 0) > 0 && <OutdatedBar camp={camp} onServer={onServer} />}
    <div className="cols-main">
      <div className="stack lg">
        {music && !clipInstead ? (
          <section className="card" id="add-videos">
            <div className="card-head">
              <h2>Make a music edit</h2>
            </div>
            <div className="card-body stack">
              <p className="small muted" style={{ margin: 0 }}>
                Pick the song (or the campaign's sound), add video clips or links, and ClipStudio cuts them on the beat with the strongest shot on the drop.
              </p>
              <div className="row">
                <Link className="btn primary" to={`/montage/new?campaign=${camp.id}`}>
                  <Icon name="music" /> Make a music edit
                </Link>
                <button className="link-btn" onClick={() => setClipInstead(true)}>
                  Or clip a long video instead
                </button>
              </div>
            </div>
          </section>
        ) : (
          <AddVideos camp={camp} onServer={onServer} goTab={goTab} dirty={dirty} onSave={onSave} onAdded={added} />
        )}
        <section className="card" id="campaign-videos">
          <div className="card-head">
            <h2>Videos</h2>
            <span className="small muted">{list.length}</span>
          </div>
          <div className="card-body tight">
            {list.length === 0 ? (
              <p className="small muted" style={{ padding: "8px 0" }}>
                {music ? "Nothing yet. Make a music edit to start." : "No videos yet. Add some above: clips are found and made with this campaign's requirements."}
              </p>
            ) : (
              list.map((p) => {
                const working = p.status === "processing" || p.status === "queued";
                return (
                  <div className={`video-row ${fresh.has(p.id) ? "fresh" : ""}`} key={p.id}>
                    <div className="vr-thumb">{p.thumb_clip_id ? <img src={media.thumb({ id: p.thumb_clip_id, version: Math.round(p.updated_at) })} alt="" /> : <Icon name={p.source_type === "montage" ? "music" : "film"} />}</div>
                    <div style={{ minWidth: 0 }}>
                      <b className="ellipsis" style={{ display: "block" }}>
                        {p.name}
                      </b>
                      <span className="small muted">
                        {p.duration ? `${fmtDuration(p.duration)} · ` : ""}
                        {working ? p.job?.message || "Working…" : p.stage === "not_started" ? "Not started" : `${plural(p.clip_count, "clip")} · ${p.approved_count} approved`}
                      </span>
                      {working && p.job && <Bar value={p.job.progress} />}
                    </div>
                    <StatusBadge status={p.status} />
                    {p.stage === "not_started" ? (
                      <Link className="btn sm" to={`/new?setup=${p.id}`}>
                        Choose settings
                      </Link>
                    ) : (
                      <Link className="btn sm" to={`/projects/${p.id}`}>
                        {p.clip_count ? "Review clips" : "Open"} <Icon name="chevronRight" size={14} />
                      </Link>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </section>
      </div>
      <div className="stack lg">
        <section className="card">
          <div className="card-head">
            <h2>Setup</h2>
            <span className="small muted">
              {done} of {steps.length} done
            </span>
          </div>
          <div className="card-body tight">
            {steps.map((s) => (
              <div className="setup-item" key={s.label}>
                <span className={s.done ? "dot-ok" : "dot-todo"}>
                  <Icon name={s.done ? "check" : "alert"} size={12} />
                </span>
                <span style={{ flex: 1 }}>{s.label}</span>
                {!s.done && s.tab && (
                  <button className="btn sm" onClick={() => goTab(s.tab!)}>
                    {s.action}
                  </button>
                )}
                {!s.done && !s.tab && (
                  <button className="btn sm" onClick={() => document.getElementById("add-videos")?.scrollIntoView({ behavior: "smooth" })}>
                    {s.action}
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <h2>Ready to post</h2>
            <span className={`badge ${camp.ready_total ? "success" : ""}`}>{camp.ready_total ?? ready.length}</span>
          </div>
          <div className="card-body tight">
            {ready.length === 0 ? (
              <p className="small muted" style={{ padding: "8px 0" }}>
                {(camp.posted_total ?? 0) > 0 ? `All caught up: ${plural(camp.posted_total ?? 0, "clip")} posted. ` : ""}
                Approved, rendered clips appear here, ready to download, caption and mark as posted.
              </p>
            ) : (
              ready.slice(0, 4).map((r) => (
                <div className="ready-row" key={r.id}>
                  <img className="rr-thumb" src={media.thumb({ id: r.id, version: Math.round(r.updated_at) })} alt="" />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <b className="ellipsis" style={{ display: "block" }}>
                      {r.title || "Untitled clip"}
                    </b>
                    <span className={`tiny ${r.verdict === "fail" ? "danger-text" : "muted"}`}>
                      {fmtDuration(r.duration)} · {r.verdict === "pass" ? "all checks passed" : r.verdict === "warn" ? "check warnings" : r.verdict === "fail" ? `${plural(r.fails, "problem")} to fix` : "not checked"}
                    </span>
                  </div>
                  <PostedBadge posted={r.posted} platforms={camp.post_platforms} />
                </div>
              ))
            )}
            {(ready.length > 0 || (camp.posted_total ?? 0) > 0) && (
              <div style={{ padding: "10px 0" }}>
                <button className={`btn sm ${ready.length ? "primary" : ""}`} onClick={() => goTab("queue")}>
                  Open post queue <Icon name="chevronRight" size={14} />
                </button>
                {(camp.ready_total ?? 0) > 4 && <span className="tiny muted" style={{ marginLeft: 8 }}>and {(camp.ready_total ?? 0) - 4} more</span>}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
    </div>
  );
}

/** What was read from the pasted brief, next to the settings it filled (replaces the old preview page). */
function BriefRead({ camp, goTab }: { camp: Campaign; goTab: (t: Tab) => void }) {
  const key = `clipstudio.briefChecked.${camp.id}.${camp.brief.raw_brief.length}`;
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  const s = camp.summary!;
  const todo = s.video.filter((v) => v.todo);
  if (hidden)
    return (
      <button className="link-btn" style={{ alignSelf: "flex-start" }} onClick={() => setHidden(false)}>
        Show what was read from the brief
      </button>
    );
  const TAB_FOR: Record<string, Tab> = { required_text: "posting", disclosure: "posting", tags: "posting", english: "posting", banned: "posting" };
  return (
    <section className="card brief-read">
      <div className="card-head">
        <h2>Read from your brief</h2>
        <span className="row">
          <span className="small muted">Check it. Anything wrong? Click it to fix.</span>
          <button
            className="btn sm"
            onClick={() => {
              setHidden(true);
              try {
                localStorage.setItem(key, "1");
              } catch {
                /* private mode */
              }
            }}
          >
            <Icon name="check" /> Looks right
          </button>
        </span>
      </div>
      <div className="card-body">
        <div className="brief-read-grid">
          <div>
            <span className="section-title">Changes how your videos are made</span>
            {s.video.length === 0 && <p className="small muted">Nothing found. Set the requirements yourself.</p>}
            {s.video.map((v) => (
              <button key={v.key} className="u-row u-btn" onClick={() => goTab(TAB_FOR[v.key] ?? "requirements")} title="Open where this is set">
                <span className={v.todo ? "need" : "ok"}>
                  <Icon name={v.todo ? "alert" : "check"} />
                </span>
                <span className="what">
                  <b>{v.label}</b>
                  {v.todo && <span className="todo">To do: {v.todo}</span>}
                  {v.quote && <span className="src">“{v.quote}”</span>}
                </span>
                <Icon name="chevronRight" size={14} />
              </button>
            ))}
          </div>
          <div>
            <span className="section-title">For your reference (doesn't change videos)</span>
            {s.reference.length ? (
              <dl className="kv" style={{ marginTop: 8 }}>
                {s.reference.map((r) => (
                  <div key={r.key} style={{ display: "contents" }}>
                    <dt>{r.label}</dt>
                    <dd>{r.value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="small muted">No pay or targets in the brief.</p>
            )}
            {todo.length > 0 && (
              <Alert kind="warn">
                {todo.length === 1 ? "One thing" : `${todo.length} things`} to finish before adding videos: {todo.map((t) => t.todo!.toLowerCase()).join("; ")}.
              </Alert>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

/** Requirements changed after clips were rendered: re-render the approved ones in one go. */
function OutdatedBar({ camp, onServer }: { camp: Campaign; onServer: (c: Campaign) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  return (
    <Alert kind="warn" title={`${plural(camp.outdated ?? 0, "approved clip")} no longer match${camp.outdated === 1 ? "es" : ""} the campaign`}>
      <span className="row wrap" style={{ gap: 10 }}>
        The requirements changed after they were rendered, so they're out of “Ready to post” until re-rendered.
        <button
          className="btn sm"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const r = await api.rerenderOutdated(camp.id);
              toast(`Re-rendering ${plural(r.rendering, "clip")}`);
              onServer(await api.campaign(camp.id));
            } catch (e) {
              toast((e as Error).message, "error");
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? <Spinner /> : <Icon name="refresh" />} Re-render them now
        </button>
      </span>
    </Alert>
  );
}

function AddVideos({ camp, onServer, goTab, dirty, onSave, onAdded }: { camp: Campaign; onServer: (c: Campaign) => void; goTab: (t: Tab) => void; dirty: boolean; onSave: () => Promise<boolean>; onAdded: (ids: string[]) => void }) {
  const toast = useToast();
  const settings = usePoll(api.settings, []).data;
  const [mode, setMode] = useState<"upload" | "links">("upload");
  const [links, setLinks] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [styleOpen, setStyleOpen] = useState(false);
  // videos wait here until the user confirms the settings they'll be made with
  // projects = videos already in the campaign but not started (added before the requirements were ready)
  const [pending, setPending] = useState<{ files?: File[]; urls?: string[]; projects?: string[] } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const saved = camp.brief;
  const st = styleOf(saved.style || null);
  const maxClips = saved.max_clips ?? settings?.max_clips ?? 8;
  const options = () => ({ ...campaignOptions(saved), max_clips: maxClips });
  const presets = usePoll(api.captionPresets, []).data;
  const framingLabel = LAYOUT_OPTIONS.find((o) => o.key === (saved.layout || settings?.layout || "auto"))?.label.replace(" (recommended)", "") ?? "Auto";
  const captionLabel = presets?.[saved.caption_preset || st.caption_preset]?.name ?? saved.caption_preset ?? st.caption_preset;
  const urls = links.split(/\s+/).filter((u) => /^https?:\/\//i.test(u));

  const ensureStyle = async () => {
    if (!saved.style) onServer(await api.patchCampaign(camp.id, { brief: { style: st.key } }));
  };
  const notStarted = (camp.project_list ?? []).filter((p) => p.stage === "not_started" && p.source_type !== "montage");
  /** start videos already added to the campaign (not started yet) with its settings as they are now */
  const startExisting = async (ids: string[]) => {
    await ensureStyle();
    setBusy(`Starting ${plural(ids.length, "video")}…`);
    const ok: string[] = [];
    for (const id of ids) {
      try {
        await api.process(id, options());
        ok.push(id);
      } catch (e) {
        toast((e as Error).message, "error");
      }
    }
    setBusy(null);
    if (ok.length) toast(`${plural(ok.length, "video")} started. Finding clips now: see Videos below.`);
    onServer(await api.campaign(camp.id));
    onAdded(ok);
  };
  /** ``start`` false: add the videos to the campaign without processing them yet */
  const upload = async (files: FileList | File[], start = true) => {
    const all = Array.from(files); // copy now: clearing the input empties the live FileList
    await ensureStyle();
    let ok = 0;
    const ids: string[] = [];
    for (let i = 0; i < all.length; i++) {
      setBusy(all.length > 1 ? `Uploading ${i + 1} of ${all.length}: ${all[i].name}` : `Uploading ${all[i].name}`);
      try {
        const label = all.length > 1 ? `Uploading ${i + 1} of ${all.length}: ${all[i].name}` : `Uploading ${all[i].name}`;
        const p = await api.uploadProject(all[i], (f) => setBusy(`${label} · ${Math.round(f * 100)}%`), undefined, camp.id);
        if (start) await api.process(p.id, options());
        ids.push(p.id);
        ok++;
      } catch (e) {
        toast(`${all[i].name}: ${(e as Error).message}`, "error");
      }
    }
    setBusy(null);
    if (ok) toast(start ? `${plural(ok, "video")} added. Finding clips now: see Videos below.` : `${plural(ok, "video")} added, not started yet. Start them from Overview once the requirements look right.`);
    onServer(await api.campaign(camp.id));
    if (start) onAdded(ids);
    return ok;
  };
  const addLinks = async (urls: string[], start = true) => {
    await ensureStyle();
    setBusy(`Adding ${plural(urls.length, "link")}…`);
    try {
      const r = await api.createBulk(urls, options(), camp.id, start);
      if (r.errors.length) toast(`${r.errors.length} link(s) skipped: ${r.errors[0].error}`, "error");
      if (r.existing.length) toast(`${plural(r.existing.length, "link")} already in this campaign (“${r.existing[0].project.name}”), so not added again`);
      if (r.created.length)
        toast(start ? `${plural(r.created.length, "link")} added. Downloading and finding clips now: see Videos below.` : `${plural(r.created.length, "link")} added, not started yet. Start them from Overview once the requirements look right.`);
      setLinks("");
      onServer(await api.campaign(camp.id));
      if (start) onAdded([...r.created.map((p) => p.id), ...r.existing.map((e) => e.project.id)]);
      return r.created.length + r.existing.length;
    } catch (e) {
      toast((e as Error).message, "error");
      return 0;
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card" id="add-videos">
      <div className="card-head">
        <h2>Add videos</h2>
        <span className="small muted">Clips are made with this campaign's requirements and style</span>
      </div>
      <div className="card-body stack">
        {notStarted.length > 0 && !busy && (
          <div className="alert info not-started-bar">
            <Icon name="clock" />
            <div className="alert-body row between wrap" style={{ gap: 10 }}>
              <span>
                <b>{plural(notStarted.length, "video")} added, not started yet</b>
                <br />
                <span className="small">{notStarted.slice(0, 3).map((p) => p.name).join(", ")}{notStarted.length > 3 ? ` and ${notStarted.length - 3} more` : ""}. Start when the requirements look right.</span>
              </span>
              <button className="btn sm primary" onClick={() => setPending({ projects: notStarted.map((p) => p.id) })}>
                <Icon name="zap" /> Start making clips
              </button>
            </div>
          </div>
        )}
        <div className="seg" role="tablist">
          <button role="tab" aria-selected={mode === "upload"} className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}>
            <Icon name="upload" size={13} /> Upload files
          </button>
          <button role="tab" aria-selected={mode === "links"} className={mode === "links" ? "active" : ""} onClick={() => setMode("links")}>
            <Icon name="link" size={13} /> Paste links
          </button>
        </div>
        {busy ? (
          <div className="row" style={{ padding: "18px 0" }}>
            <Spinner /> <span className="small">{busy}</span>
          </div>
        ) : mode === "upload" ? (
          <div
            className={`dropzone ${over ? "over" : ""}`}
            style={{ padding: "24px 20px" }}
            role="button"
            tabIndex={0}
            onClick={() => input.current?.click()}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
            onDragOver={(e) => (e.preventDefault(), setOver(true))}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              if (e.dataTransfer.files.length) setPending({ files: Array.from(e.dataTransfer.files) });
            }}
          >
            <div className="big-icon">
              <Icon name="upload" size={22} />
            </div>
            <h3>Drop videos here or click to browse</h3>
            <p className="muted small" style={{ marginTop: 6 }}>
              One or several files. Each starts finding clips right away.
            </p>
            <input
              ref={input}
              type="file"
              accept="video/*,.mkv,.mts,.m2ts"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) setPending({ files: Array.from(e.target.files) });
                e.target.value = "";
              }}
            />
          </div>
        ) : (
          <div className="stack sm">
            <textarea className="textarea" rows={3} value={links} onChange={(e) => setLinks(e.target.value)} placeholder="YouTube, Twitch, Kick… one link per line" aria-label="Video links" />
            <div className="row">
              <button className="btn primary" onClick={() => setPending({ urls })} disabled={!urls.length}>
                <Icon name="link" /> {urls.length > 1 ? `Add ${urls.length} links` : "Add link"}
              </button>
              <span className="small muted">Downloaded and processed one after another.</span>
            </div>
          </div>
        )}
        <div className="row between wrap">
          <div className="row" style={{ gap: 12, minWidth: 0 }}>
            <MiniPhone s={st} small />
            <div style={{ minWidth: 0 }}>
              <div className="small">
                <b>Style: {st.label}</b>
                {!saved.style && <span className="tiny muted"> (default)</span>}
              </div>
              <div className="tiny muted">{describeStyle(st.key, saved.intensity || null)}</div>
              <div className="tiny muted">
                Framing: <b>{framingLabel}</b> · Captions: <b>{captionLabel}</b>
              </div>
            </div>
          </div>
          <div className="row">
            <span className="small muted">Up to {maxClips} clips per video</span>
            <button className="btn sm" onClick={() => setStyleOpen(true)}>
              Change style
            </button>
            <Link className="btn sm ghost" to={`/new?campaign=${camp.id}`} title="The full form, with every setting">
              More options
            </Link>
          </div>
        </div>
      </div>
      {styleOpen && <StyleDialog camp={camp} maxClips={maxClips} onClose={() => setStyleOpen(false)} onSaved={onServer} />}
      {pending && !styleOpen && (
        <StartDialog
          camp={camp}
          count={pending.files?.length ?? pending.urls?.length ?? pending.projects?.length ?? 0}
          kind={pending.urls ? "link" : "file"}
          added={!!pending.projects}
          summary={[
            ["Clip length", saved.min_duration || saved.max_duration ? `${saved.min_duration ?? "any"}–${saved.max_duration ?? "any"} seconds (campaign)` : `${settings?.duration_preset ?? "15-30"} seconds (your default in Settings)`],
            ["Clips per video", `up to ${maxClips}, weak moments skipped`],
            ["Style", `${st.label} · ${(saved.intensity || st.intensity)} editing`],
            ["Framing", framingLabel],
            ["Captions", `${captionLabel}${saved.caption_position != null ? ` · ${Math.round(saved.caption_position * 100)}% down` : ""}${saved.logo.mode !== "none" ? ", kept clear of the logo" : ""}`],
            ["End text", saved.end_text.enabled && saved.end_text.text.trim() ? `“${saved.end_text.text}” for the last ${saved.end_text.duration}s` : "off"],
            ["Title text", saved.type === "music" || !saved.hook_overlay.enabled ? "off" : saved.hook_overlay.text ? `“${saved.hook_overlay.text}” on every clip` : "written for each clip"],
            ["Logo", saved.logo.mode === "none" ? "none" : saved.logo.has_file ? (saved.logo.mode === "template" ? "brand's template file" : "placed as set on Requirements") : "missing"],
            ["Caption", [saved.disclosure.in_caption && saved.disclosure.text, saved.required_caption_text && "required text"].filter(Boolean).join(" + ") || "hook + hashtags"],
          ]}
          onCancel={() => setPending(null)}
          onChange={async (t) => {
            if (t === "style") return setStyleOpen(true); // the picked videos wait; this box comes back after
            const p = pending;
            setPending(null);
            // keep the videos: add them to the campaign (not started), then open the requirements
            if (p.files) await upload(p.files, false);
            else if (p.urls) await addLinks(p.urls, false);
            goTab(t);
          }}
          dirty={dirty}
          onStart={async () => {
            const p = pending;
            if (dirty && !(await onSave())) return;
            setPending(null);
            if (p.files) upload(p.files);
            else if (p.urls) addLinks(p.urls);
            else if (p.projects) startExisting(p.projects);
          }}
        />
      )}
    </section>
  );
}

function StartDialog({
  dirty,
  camp,
  count,
  kind,
  added,
  summary,
  onCancel,
  onChange,
  onStart,
}: {
  camp: Campaign;
  count: number;
  kind: "file" | "link";
  /** the videos are already in the campaign (not started) */
  added?: boolean;
  summary: [string, string][];
  onCancel: () => void;
  onChange: (t: Tab | "style") => void;
  onStart: () => void;
  dirty?: boolean;
}) {
  const b = camp.brief;
  const missing: string[] = [];
  if ((b.type === "logo" || b.logo.mode !== "none") && !b.logo.has_file) missing.push(b.logo.mode === "template" ? "the brand's logo template file" : "the logo file");
  if (b.sound_rule.mode === "burn" && !b.sounds.some((x) => x.has_file)) missing.push("the brand's sound file");
  return (
    <Modal
      title={`Make clips from ${plural(count, kind === "file" ? "video" : "link")}?`}
      onClose={onCancel}
      actions={
        <>
          <button className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="btn" onClick={() => onChange("requirements")} title={added ? "The videos stay in the campaign, not started" : "The videos are added to the campaign now (not started) and you can start them after"}>
            Change requirements first
          </button>
          <button className="btn primary" onClick={onStart}>
            <Icon name="zap" /> Start making clips
          </button>
        </>
      }
    >
      {missing.length > 0 && (
        <Alert kind="warn" title="Setup isn't finished">
          This campaign still needs {missing.join(" and ")}. Clips made now won't meet the brief until you add it and re-render them.
        </Alert>
      )}
      {dirty && <Alert kind="info">You have unsaved changes on this campaign. They'll be saved first, so these videos use them.</Alert>}
      <p className="small muted" style={{ margin: 0 }}>
        They'll be made with this campaign's settings. Videos are processed one after another{kind === "link" ? ", after downloading" : ""}. Want to change the requirements first? The
        videos are kept in the campaign, not started, until you start them.
      </p>
      <dl className="kv start-kv">
        {summary.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <button className="link-btn" style={{ alignSelf: "flex-start" }} onClick={() => onChange("style")}>
        Change style, framing or captions
      </button>
    </Modal>
  );
}

function StyleDialog({ camp, maxClips, onClose, onSaved }: { camp: Campaign; maxClips: number; onClose: () => void; onSaved: (c: Campaign) => void }) {
  const toast = useToast();
  const [style, setStyle] = useState<StyleKey>(styleOf(camp.brief.style || null).key);
  const [intensity, setIntensity] = useState<Intensity>((camp.brief.intensity || styleOf(camp.brief.style || null).intensity) as Intensity);
  const [clips, setClips] = useState(maxClips);
  const [layout, setLayout] = useState<string>(camp.brief.layout || "");
  const [caption, setCaption] = useState<string>(camp.brief.caption_preset || "");
  const [busy, setBusy] = useState(false);
  const presets = usePoll(api.captionPresets, []).data;
  const styleCaption = styleOf(style).caption_preset;
  return (
    <Modal
      title="Style for this campaign's clips"
      onClose={onClose}
      wide
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                onSaved(await api.patchCampaign(camp.id, { brief: { style, intensity, max_clips: clips, layout: layout as Brief["layout"], caption_preset: caption } }));
                toast("Style saved. Videos you add from now on use it.");
                onClose();
              } catch (e) {
                toast((e as Error).message, "error");
                setBusy(false);
              }
            }}
          >
            {busy ? <Spinner /> : <Icon name="check" />} Use this style
          </button>
        </>
      }
    >
      <p className="small muted" style={{ marginTop: 0 }}>
        Used for videos you add from now on. Every clip can still be changed in the editor. <HelpTip topic="style" label="What's a style?" />
      </p>
      <StylePicker
        value={style}
        onChange={(k) => {
          setStyle(k);
          setIntensity(styleOf(k).intensity);
        }}
      />
      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <IntensityPicker value={intensity} onChange={setIntensity} />
        <div className="field">
          <label>
            Clips per video: {clips} <HelpTip topic="clips_per_video" />
          </label>
          <input type="range" min={1} max={30} value={clips} onChange={(e) => setClips(+e.target.value)} aria-label="Clips per video" />
        </div>
      </div>
      <div className="stack" style={{ marginTop: 16 }}>
        <div className="field">
          <span className="label-text">Framing</span>
          <div className="stack sm" role="radiogroup" aria-label="Framing">
            {LAYOUT_OPTIONS.map((o) => {
              const on = (layout || "auto") === o.key;
              return (
                <label key={o.key} className={`hook-option ${on ? "active" : ""}`}>
                  <input type="radio" name="camp-layout" checked={on} onChange={() => setLayout(o.key === "auto" ? "" : o.key)} />
                  <span>
                    <strong>{o.label}</strong>
                    <br />
                    <span className="small muted">{o.desc}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </div>
        <div className="field">
          <label htmlFor="camp-caption">Caption look</label>
          <select id="camp-caption" className="select" value={caption} onChange={(e) => setCaption(e.target.value)}>
            <option value="">Use the style's look ({presets?.[styleCaption]?.name ?? styleCaption})</option>
            {presets &&
              Object.entries(presets).map(([k, pr]) => (
                <option key={k} value={k}>
                  {pr.name}
                </option>
              ))}
          </select>
          <span className="hint">Fine-tune fonts and colours per clip in the editor (Captions &amp; text tab).</span>
        </div>
      </div>
    </Modal>
  );
}

// ================================================================================== Requirements
function RequirementsTab({ camp, b, set, setSub, onServer }: { camp: Campaign; b: Brief; set: SetFn; setSub: SetSubFn; onServer: (c: Campaign) => void }) {
  const zones = useSafeZones();
  const [plat, setPlat] = useState<Platform>(b.platforms[0] ?? "reels");
  const togglePlat = (p: Platform) => set("platforms", b.platforms.includes(p) ? b.platforms.filter((x) => x !== p) : [...b.platforms, p]);
  const extrasAllowed = b.allow_music && b.allow_sfx;
  const music = b.type === "music";
  // the caption band shown on the phone: the campaign's position, or the caption look's own spot
  const presets = usePoll(api.captionPresets, []).data;
  const look = presets?.[b.caption_preset || styleOf(b.style || null).caption_preset];
  const capH = look ? look.max_lines * look.size * 1.2 + 2 * look.outline : 0.12;
  const lookY = look?.position ?? 0.7;
  const capY = b.caption_position ?? lookY;
  return (
    <div className="req-grid">
      <div className="stack req-preview">
        {b.logo.mode !== "none" || !music ? (
          <>
            <LogoPlacer
              campaignId={camp.id}
              version={camp.updated_at}
              logo={b.logo}
              zones={zones}
              platform={plat}
              title={b.hook_overlay.enabled && !music}
              end={b.end_text.enabled && !!b.end_text.text.trim() && !music}
              captions={music ? undefined : { y: capY, h: capH, onChange: (y) => set("caption_position", y) }}
              onChange={(p) => setSub("logo", p)}
            />
            <div className="seg" style={{ alignSelf: "center" }} role="tablist" aria-label="Show the buttons of">
              {(["reels", "shorts"] as const).map((p) => (
                <button key={p} role="tab" aria-selected={plat === p} className={plat === p ? "active" : ""} onClick={() => setPlat(p)}>
                  {p === "reels" ? "Instagram Reels" : "YouTube Shorts"}
                </button>
              ))}
            </div>
            <div className="zone-legend" style={{ justifyContent: "center" }}>
              <span>
                <i className="lg-ui" /> Partly covered by app buttons
              </span>
              <span>
                <i className="lg-nogo" /> Always covered
              </span>
              {b.hook_overlay.enabled && !music && (
                <span>
                  <i className="lg-title" /> Title / end text
                </span>
              )}
            </div>
            {!music && captionLogoOverlap(b.logo, capY, capH) > 0 && (
              <Alert kind="info">The logo sits where the captions go. On every clip the captions move just above or below the logo automatically, or drag the captions band yourself.</Alert>
            )}
            {b.logo.mode === "none" && <span className="small muted" style={{ textAlign: "center" }}>No logo in this campaign. Drag the captions band to set where captions sit.</span>}
          </>
        ) : (
          <div className="card card-body small muted">No logo in this campaign. Choose a logo option on the right if the brand wants one.</div>
        )}
      </div>
      <div className="stack lg">
        <LogoCard camp={camp} b={b} setSub={setSub} onServer={onServer} />
        <section className="card">
          <div className="card-head">
            <h2>
              Clip length <HelpTip topic="clip_length" />
            </h2>
            {(b.min_duration || b.max_duration) && <span className="from-brief">applied when finding clips</span>}
          </div>
          <div className="card-body stack sm">
            <div className="row">
              <input className="input" style={{ width: 90 }} type="number" min={1} step={0.5} placeholder="any" aria-label="Minimum length in seconds" value={b.min_duration ?? ""} onChange={(e) => set("min_duration", num(e.target.value))} />
              <span className="muted">to</span>
              <input className="input" style={{ width: 90 }} type="number" min={1} placeholder="any" aria-label="Maximum length in seconds" value={b.max_duration ?? ""} onChange={(e) => set("max_duration", num(e.target.value))} />
              <span className="muted">seconds</span>
            </div>
            {b.min_duration && b.max_duration && b.min_duration >= b.max_duration ? <Alert kind="warn">The minimum must be shorter than the maximum.</Alert> : null}
            <span className="explain">Clips are found and cut to fit. Leave empty for no limit.</span>
          </div>
        </section>
        {!music && <TextOnVideoCard b={b} set={set} setSub={setSub} lookY={lookY} />}
        <SoundCard camp={camp} b={b} set={set} setSub={setSub} onServer={onServer} />
        <section className="card">
          <div className="card-head">
            <h2>What the brand allows</h2>
          </div>
          <div className="card-body tight">
            <div className="q-row">
              <div className="q">
                <b>
                  Add our own background music or sound effects? <HelpTip topic="extras" />
                </b>
                <span>{extrasAllowed ? "Clips may get quiet music and small sound effects, depending on the style." : b.allow_music ? "Sound effects stay off for every clip." : b.allow_sfx ? "Background music stays off for every clip." : "Both stay off for every clip."}</span>
              </div>
              <div className="seg">
                <button className={extrasAllowed ? "active" : ""} onClick={() => (set("allow_music", true), set("allow_sfx", true))}>
                  Yes
                </button>
                <button className={!b.allow_music && !b.allow_sfx ? "active" : ""} onClick={() => (set("allow_music", false), set("allow_sfx", false))}>
                  No
                </button>
                {b.allow_music !== b.allow_sfx && <span className="badge" style={{ alignSelf: "center", margin: "0 6px" }}>{b.allow_music ? "music only" : "effects only"}</span>}
              </div>
            </div>
            <div className="q-row">
              <div className="q">
                <b>
                  Where will you post? <HelpTip topic="platforms" />
                </b>
                <span>Keeps the logo and captions clear of each app's buttons.</span>
              </div>
              <div className="row">
                {(["reels", "shorts"] as const).map((p) => (
                  <label key={p} className="check small">
                    <input type="checkbox" checked={b.platforms.includes(p)} onChange={() => togglePlat(p)} /> {p === "reels" ? "Reels" : "Shorts"}
                  </label>
                ))}
              </div>
            </div>
          </div>
        </section>
        <details className="adv">
          <summary>Advanced: video size, on-screen “#ad”, logo limits</summary>
          <div className="stack" style={{ marginTop: 12 }}>
            <div className="field">
              <span className="label-text">Video size</span>
              <div className="seg">
                {([720, 1080] as const).map((h) => (
                  <button key={h} className={b.output_height === h ? "active" : ""} onClick={() => set("output_height", h)}>
                    {h === 1080 ? "1080x1920 (best)" : "720x1280 (faster)"}
                  </button>
                ))}
              </div>
            </div>
            <label className="check">
              <input type="checkbox" checked={b.disclosure.on_screen} onChange={(e) => setSub("disclosure", { on_screen: e.target.checked, text: b.disclosure.text || "#ad" })} />
              <span>
                Also show “{b.disclosure.text || "#ad"}” on the video
                <br />
                <span className="small muted">Most brands only need it in the caption (Posting tab).</span>
              </span>
            </label>
            {b.disclosure.on_screen && (
              <div className="field">
                <label>Corner</label>
                <select className="select" value={b.disclosure.corner} onChange={(e) => setSub("disclosure", { corner: e.target.value as Brief["disclosure"]["corner"] })}>
                  <option value="top_left">Top left</option>
                  <option value="top_right">Top right</option>
                  <option value="bottom_left">Bottom left</option>
                  <option value="bottom_right">Bottom right</option>
                </select>
              </div>
            )}
            {b.logo.mode !== "none" && (
              <div className="grid cols-2">
                <div className="field">
                  <label>Logo may be under app buttons: up to {Math.round(b.logo.max_ui_cover * 100)}%</label>
                  <input type="range" min={0} max={1} step={0.05} value={b.logo.max_ui_cover} onChange={(e) => setSub("logo", { max_ui_cover: +e.target.value })} />
                </div>
                <div className="field">
                  <label>In the always-covered area: up to {Math.round(b.logo.max_nogo_overlap * 100)}%</label>
                  <input type="range" min={0} max={1} step={0.05} value={b.logo.max_nogo_overlap} onChange={(e) => setSub("logo", { max_nogo_overlap: +e.target.value })} />
                </div>
              </div>
            )}
          </div>
        </details>
      </div>
    </div>
  );
}

const END_TEMPLATES = ["Follow for part 2", "Link in bio", "Watch the full stream", "Download now"];

/** Captions position, title text and end text: everything written on the video, in one place. */
function TextOnVideoCard({ b, set, setSub, lookY }: { b: Brief; set: SetFn; setSub: SetSubFn; lookY: number }) {
  const custom = b.caption_position != null;
  const e = b.end_text;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Text on the video</h2>
        <span className="small muted">captions, title text, end text</span>
      </div>
      <div className="card-body tight">
        <div className="q-row">
          <div className="q">
            <b>Where do captions go?</b>
            <span>
              {custom ? `${Math.round((b.caption_position ?? 0) * 100)}% down the screen on every clip. Drag the band on the phone, or use the slider.` : `The caption look's own spot (${Math.round(lookY * 100)}% down).`}{" "}
              {b.logo.mode !== "none" ? "If the logo is in the way, they move just above or below it." : ""}
            </span>
          </div>
          <div className="seg">
            <button className={!custom ? "active" : ""} onClick={() => set("caption_position", null)}>
              Automatic
            </button>
            <button className={custom ? "active" : ""} onClick={() => set("caption_position", custom ? b.caption_position : lookY)}>
              Choose
            </button>
          </div>
        </div>
        {custom && (
          <div className="field" style={{ padding: "0 0 12px" }}>
            <label htmlFor="cap-pos">Captions: {Math.round((b.caption_position ?? 0) * 100)}% from the top</label>
            <input id="cap-pos" type="range" min={0.1} max={0.92} step={0.01} value={b.caption_position ?? lookY} onChange={(ev) => set("caption_position", +ev.target.value)} />
            <span className="hint">A clip's own position (set in the editor) still wins for that clip.</span>
          </div>
        )}
        <div className="q-row">
          <div className="q">
            <b>
              Title text for the first few seconds? <HelpTip topic="hook_title" />
            </b>
            <span>A short hook like “Nobody expected this” at the top. Helps people keep watching.</span>
          </div>
          <div className="seg">
            <button className={b.hook_overlay.enabled ? "active" : ""} onClick={() => setSub("hook_overlay", { enabled: true })}>
              Yes
            </button>
            <button className={!b.hook_overlay.enabled ? "active" : ""} onClick={() => setSub("hook_overlay", { enabled: false })}>
              No
            </button>
          </div>
        </div>
        {b.hook_overlay.enabled && (
          <>
            <TitleTextChoice b={b} setSub={setSub} />
            <div className="field" style={{ paddingBottom: 12 }}>
              <label htmlFor="hook-dur">Title text shows for {b.hook_overlay.duration}s</label>
              <input id="hook-dur" type="range" min={1.5} max={8} step={0.5} value={b.hook_overlay.duration} onChange={(ev) => setSub("hook_overlay", { duration: +ev.target.value })} />
            </div>
          </>
        )}
        <div className="q-row">
          <div className="q">
            <b>Text at the end of every clip?</b>
            <span>A call to action or the brand's end screen, e.g. “Follow for part 2”, for the last few seconds.</span>
          </div>
          <div className="seg">
            <button className={e.enabled ? "active" : ""} onClick={() => setSub("end_text", { enabled: true, text: e.text || "Follow for part 2" })}>
              Yes
            </button>
            <button className={!e.enabled ? "active" : ""} onClick={() => setSub("end_text", { enabled: false })}>
              No
            </button>
          </div>
        </div>
        {e.enabled && (
          <div className="stack sm" style={{ paddingBottom: 14 }}>
            <input className="input" value={e.text} maxLength={120} aria-label="End text" placeholder="e.g. Follow for part 2" onChange={(ev) => setSub("end_text", { text: ev.target.value })} />
            <div className="row wrap" style={{ gap: 6 }}>
              {END_TEMPLATES.map((t) => (
                <button key={t} type="button" className={`chip-btn ${e.text === t ? "on" : ""}`} onClick={() => setSub("end_text", { text: t })}>
                  {t}
                </button>
              ))}
            </div>
            <span className="hint">{"{title}"} is replaced with each clip's title. Every clip is checked for it before posting.</span>
            {!e.text.trim() && <Alert kind="warn">Write the end text, or it won't appear on any clip.</Alert>}
            <div className="grid cols-2">
              <div className="field">
                <label htmlFor="end-dur">Shows for the last {e.duration}s</label>
                <input id="end-dur" type="range" min={1} max={6} step={0.5} value={e.duration} onChange={(ev) => setSub("end_text", { duration: +ev.target.value })} />
              </div>
              <div className="field">
                <span className="label-text">Look</span>
                <div className="seg">
                  <button className={e.look === "overlay" ? "active" : ""} onClick={() => setSub("end_text", { look: "overlay" })} title="Text over the video">
                    Over the video
                  </button>
                  <button className={e.look === "card" ? "active" : ""} onClick={() => setSub("end_text", { look: "card" })} title="The video is dimmed and blurred behind the text, and captions stop">
                    End card
                  </button>
                </div>
              </div>
            </div>
            <div className="field">
              <span className="label-text">Style</span>
              <HookStylePicker value={e.style} onChange={(v) => setSub("end_text", { style: v })} />
            </div>
            <span className="hint">The clip keeps its length: the text covers its last seconds{e.look === "card" ? ", over a dimmed, blurred frame" : ""}.</span>
          </div>
        )}
      </div>
    </section>
  );
}

/** Title text: written per clip from what's said, or the campaign's own words on every clip. */
function TitleTextChoice({ b, setSub }: { b: Brief; setSub: SetSubFn }) {
  const own = b.hook_overlay.text !== "";
  return (
    <div className="title-choice stack sm">
      <div className="seg">
        <button className={!own ? "active" : ""} onClick={() => setSub("hook_overlay", { text: "" })}>
          Write it for each clip
        </button>
        <button className={own ? "active" : ""} onClick={() => setSub("hook_overlay", { text: b.hook_overlay.text || "Wait for it…" })}>
          Use my text on every clip
        </button>
      </div>
      {!own ? (
        <span className="hint">Each clip gets its own title from what's said in it. You can still change any clip's title in the editor.</span>
      ) : (
        <>
          <input className="input" value={b.hook_overlay.text} maxLength={120} aria-label="Title text" placeholder="e.g. Nobody expected this" onChange={(e) => setSub("hook_overlay", { text: e.target.value })} />
          <div className="chips">
            {HOOK_TEMPLATES.map((t) => (
              <button key={t} type="button" className={`chip-btn ${b.hook_overlay.text === t ? "on" : ""}`} onClick={() => setSub("hook_overlay", { text: t })}>
                {t}
              </button>
            ))}
          </div>
          <span className="hint">
            <code>{"{title}"}</code> is replaced by each clip's title. A title typed in the editor for one clip still wins for that clip.
          </span>
        </>
      )}
    </div>
  );
}

function LogoCard({ camp, b, setSub, onServer }: { camp: Campaign; b: Brief; setSub: SetSubFn; onServer: (c: Campaign) => void }) {
  const toast = useToast();
  const { confirm } = useDialogs();
  const zones = useSafeZones();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const logo = b.logo;
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const c = await api.uploadLogo(camp.id, f);
      onServer(c);
      toast(c.notes?.length ? c.notes.join(" ") : "Logo uploaded");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card">
      <div className="card-head">
        <h2>
          Logo <HelpTip topic="logo_mode" />
        </h2>
        <div className="seg">
          {(
            [
              ["none", "No logo"],
              ["free", "Place a logo image"],
              ["template", "Brand's template file"],
            ] as const
          ).map(([k, l]) => (
            <button key={k} className={logo.mode === k ? "active" : ""} onClick={() => setSub("logo", { mode: k })}>
              {l}
            </button>
          ))}
        </div>
      </div>
      {logo.mode !== "none" && (
        <div className="card-body stack">
          <p className="explain" style={{ margin: 0 }}>
            {logo.mode === "free" ? (
              <>
                Drag the logo on the preview to place it, drag its corner to resize (or click it and use the arrow keys). <b className="danger-text">Red areas</b> are covered by Instagram/YouTube buttons. <HelpTip topic="safe_zones" label="Why?" />
              </>
            ) : (
              "A full-screen transparent file (often LOGO_PRE_PLACED, 1080x1920) laid over the whole video exactly as supplied. It can't be moved or resized; animated files loop for the whole clip."
            )}
          </p>
          <CoverageStatus logo={logo} zones={zones} platforms={b.platforms} title={b.hook_overlay.enabled && b.type !== "music"} />
          <div className="row wrap">
            <button className={`btn sm ${logo.has_file ? "" : "primary"}`} onClick={() => input.current?.click()} disabled={busy}>
              {busy ? <Spinner /> : <Icon name="upload" />} {logo.has_file ? "Replace file" : logo.mode === "template" ? "Upload template file" : "Upload logo"}
            </button>
            {logo.has_file && (
              <>
                <span className="tiny muted">
                  {logo.file_name} · {logo.image_w}x{logo.image_h}
                  {logo.animated ? " · animated" : ""}
                </span>
                <button
                  className="btn sm ghost icon danger"
                  aria-label="Remove logo file"
                  onClick={async () => (await confirm({ title: "Remove the logo?", body: "Clips rendered after this won't carry the logo until you upload it again.", confirm: "Remove logo", danger: true })) && attempt(toast, async () => onServer(await api.deleteLogo(camp.id)))}
                >
                  <Icon name="trash" />
                </button>
              </>
            )}
            <input ref={input} type="file" accept=".png,.gif,.apng,image/png,image/gif,image/apng" hidden onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) upload(f);
              }} />
          </div>
          {!logo.has_file && <span className="small muted">PNG with a transparent background{logo.mode === "template" ? ", or GIF for animated templates" : ""}.</span>}
          {logo.mode === "free" && (
            <div className="field">
              <label>See-through: {Math.round((1 - logo.opacity) * 100)}%</label>
              <input type="range" min={0} max={0.7} step={0.05} value={1 - logo.opacity} onChange={(e) => setSub("logo", { opacity: Math.round((1 - +e.target.value) * 100) / 100 })} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function SoundCard({ camp, b, set, setSub, onServer }: { camp: Campaign; b: Brief; set: SetFn; setSub: SetSubFn; onServer: (c: Campaign) => void }) {
  const toast = useToast();
  const { confirm } = useDialogs();
  const input = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const rule = b.sound_rule;
  const upload = async (f: File) => {
    setBusy(true);
    try {
      onServer(await api.uploadSound(camp.id, f, name || f.name.replace(/\.[^.]+$/, "")));
      setName("");
      toast("Sound added");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const CARDS = [
    ["none", "No required sound", "Clips keep their own audio."],
    ["in_app", "Add the sound when posting", "You pick the brand's sound inside Instagram/YouTube. The checklist tells you where to start it."],
    ["burn", "Put the sound in the video", "Upload the sound file; it's mixed into every clip."],
  ] as const;
  return (
    <section className="card">
      <div className="card-head">
        <h2>
          Sound <HelpTip topic="sound" />
        </h2>
      </div>
      <div className="card-body stack">
        <div className="sound-cards" role="radiogroup" aria-label="Campaign sound">
          {CARDS.map(([k, l, d]) => (
            <button key={k} type="button" role="radio" aria-checked={rule.mode === k} className={`sound-card ${rule.mode === k ? "on" : ""}`} onClick={() => setSub("sound_rule", { mode: k })}>
              <b>{l}</b>
              {d}
            </button>
          ))}
        </div>
        {rule.mode !== "none" && (
          <>
            <div className="grid cols-2">
              <div className="field">
                <label>At least {Math.round(rule.min_volume * 100)}% volume</label>
                <input type="range" min={0} max={1} step={0.05} value={rule.min_volume} onChange={(e) => setSub("sound_rule", { min_volume: +e.target.value })} />
              </div>
              {rule.mode === "burn" && (
                <div className="field">
                  <label>Original audio underneath: {Math.round(rule.original_volume * 100)}%</label>
                  <input type="range" min={0} max={1} step={0.05} value={rule.original_volume} onChange={(e) => setSub("sound_rule", { original_volume: +e.target.value })} />
                  <span className="hint">0% = only the brand's sound.</span>
                </div>
              )}
            </div>
            <div className="stack sm">
              <span className="label-text">The brand's sounds</span>
              {b.sounds.length === 0 && <span className="small muted">{rule.mode === "burn" ? "Upload the sound file below." : "Optional: add the song's name (and its file, to line up music edits with the beat)."}</span>}
              {b.sounds.map((s, i) => (
                <div className="list-row" key={s.id}>
                  <Icon name="music" />
                  <input className="input" style={{ flex: 1 }} aria-label="Sound name" value={s.name} onChange={(e) => set("sounds", b.sounds.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                  {s.has_file ? (
                    <>
                      <label className="small muted nowrap" title="Where the brand's cut of the song starts in the file">
                        starts at <input className="input" type="number" min={0} step={0.5} style={{ width: 66 }} value={s.start} onChange={(e) => set("sounds", b.sounds.map((x, j) => (j === i ? { ...x, start: Math.max(0, +e.target.value) } : x)))} />s
                      </label>
                      <audio src={media.sound(camp.id, s.id)} controls preload="none" style={{ height: 30, width: 150 }} />
                    </>
                  ) : (
                    <span className="badge">name only</span>
                  )}
                  <button className="btn sm icon ghost danger" aria-label={`Remove ${s.name}`} onClick={async () => (await confirm({ title: `Remove “${s.name}”?`, confirm: "Remove sound", danger: true })) && attempt(toast, async () => onServer(await api.deleteSound(camp.id, s.id)))}>
                    <Icon name="trash" />
                  </button>
                </div>
              ))}
              <div className="row wrap">
                <input className="input" style={{ flex: 1, minWidth: 160 }} placeholder="Song – Artist" aria-label="New sound name" value={name} onChange={(e) => setName(e.target.value)} />
                <button className="btn sm" disabled={busy} onClick={() => input.current?.click()}>
                  {busy ? <Spinner /> : <Icon name="upload" />} Upload file
                </button>
                <button className="btn sm ghost" disabled={!name.trim()} onClick={() => attempt(toast, async () => (onServer(await api.addSoundRef(camp.id, name.trim())), setName("")))}>
                  <Icon name="plus" /> Name only
                </button>
                <input ref={input} type="file" accept="audio/*" hidden onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) upload(f);
              }} />
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

// ================================================================================== Posting
function PostingTab({ b, set, setSub }: { b: Brief; set: SetFn; setSub: SetSubFn }) {
  const [plat, setPlat] = useState<Platform>(b.platforms[0] ?? "reels");
  const [cap, setCap] = useState<{ caption: string; warnings: string[] } | null>(null);
  const [newItem, setNewItem] = useState("");
  const key = JSON.stringify([b.caption_template, b.disclosure, b.required_caption_text, b.required_hashtags, b.required_mentions, plat]);
  useEffect(() => {
    const t = setTimeout(() => api.captionPreview(b, plat).then(setCap).catch(() => undefined), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const auto: string[] = [];
  if (b.min_duration || b.max_duration) auto.push(`Length ${b.min_duration ?? 0}–${b.max_duration ?? "any"} s`);
  if (b.type === "logo" || b.logo.mode !== "none") auto.push("Logo visible the whole clip, not under app buttons");
  if (b.disclosure.text && b.disclosure.in_caption) auto.push(`${b.disclosure.text} in the caption`);
  if (b.required_caption_text) auto.push("Required text in the caption, exactly");
  if (b.required_hashtags.length || b.required_mentions.length) auto.push("Required hashtags and mentions in the caption");
  if (!b.allow_music) auto.push("No background music");
  if (!b.allow_sfx) auto.push("No sound effects");
  if (b.sound_rule.mode === "burn") auto.push(`Brand's sound in the video at ${Math.round(b.sound_rule.min_volume * 100)}%+`);
  if (b.english_only) auto.push("All text in English");
  if (b.banned_keywords.length) auto.push(`No banned topics (${b.banned_keywords.length})`);
  auto.push("Vertical 9:16, rendered with the latest edits", "Not already posted for another campaign");
  const manual = [...(b.sound_rule.mode === "in_app" ? [`Add the brand's sound in the app at ${Math.round(b.sound_rule.min_volume * 100)}%+ volume`] : []), ...b.checklist];

  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      <section className="card">
        <div className="card-head">
          <h2>Caption</h2>
          <div className="seg">
            {(b.platforms.length ? b.platforms : (["reels", "shorts"] as const)).map((p) => (
              <button key={p} className={plat === p ? "active" : ""} onClick={() => setPlat(p)}>
                {p === "reels" ? "Reels" : "Shorts"}
              </button>
            ))}
          </div>
        </div>
        <div className="card-body stack">
          <p className="explain" style={{ margin: 0 }}>
            Every clip gets a caption like this. The first line is each clip's own hook.
          </p>
          <CaptionPreview text={cap?.caption ?? ""} disclosure={b.disclosure.in_caption ? b.disclosure.text : ""} required={b.required_caption_text} />
          {cap?.warnings.map((w) => (
            <Alert key={w} kind="warn">
              {w}
            </Alert>
          ))}
          <div className="tag-legend">
            <span>
              <i className="lg-disc" />
              paid-post label
            </span>
            <span>
              <i className="lg-req" />
              required text (checked on every clip)
            </span>
          </div>
          <div className="field">
            <label>
              Required caption text, exactly as written <HelpTip topic="required_text" />
            </label>
            <textarea className="textarea" rows={3} value={b.required_caption_text} placeholder="e.g. Please play responsibly. 19+…" onChange={(e) => set("required_caption_text", e.target.value)} />
            <span className="hint">A clip fails the check if this is missing or changed. Leave empty if the brand doesn't ask for it.</span>
          </div>
          <div className="grid cols-2">
            <div className="field">
              <label>Paid-post label</label>
              <input className="input" value={b.disclosure.text} maxLength={80} placeholder="#ad, #sponsored… (empty = none)" onChange={(e) => setSub("disclosure", { text: e.target.value, in_caption: true })} />
            </div>
            <div className="field">
              <label>Required hashtags</label>
              <ListEditor items={b.required_hashtags} onChange={(v) => set("required_hashtags", v)} placeholder="#hashtag, then Enter" />
            </div>
          </div>
          <div className="field">
            <label>Required @mentions</label>
            <ListEditor items={b.required_mentions} onChange={(v) => set("required_mentions", v)} placeholder="@account, then Enter" />
          </div>
          <details className="adv">
            <summary>Change the caption layout</summary>
            <div className="stack sm" style={{ marginTop: 10 }}>
              <textarea className="textarea code" rows={5} value={b.caption_template} onChange={(e) => set("caption_template", e.target.value)} aria-label="Caption layout" />
              <span className="hint">
                Words in braces are filled per clip: {"{hook}"} the clip's hook line · {"{title}"} its title · {"{hashtags}"} its hashtags · {"{campaign_hashtags}"} / {"{mentions}"} the required ones · {"{disclosure}"} the paid-post label · {"{required_text}"} the required text (always added).
              </span>
            </div>
          </details>
          <details className="adv">
            <summary>Text rules: English only, banned words</summary>
            <div className="stack" style={{ marginTop: 10 }}>
              <label className="check">
                <input type="checkbox" checked={b.english_only} onChange={(e) => set("english_only", e.target.checked)} /> All captions and on-screen text must be English
              </label>
              <div className="field">
                <label>Banned words and topics</label>
                <ListEditor items={b.banned_keywords} onChange={(v) => set("banned_keywords", v)} placeholder="word or phrase, then Enter" />
                <span className="hint">Checked against what's said in the clip, its title, on-screen text and caption.</span>
              </div>
            </div>
          </details>
        </div>
      </section>
      <section className="card">
        <div className="card-head">
          <h2>
            Before you post <HelpTip topic="checklist" />
          </h2>
          <span className="small muted">shown on every clip's Post screen</span>
        </div>
        <div className="card-body stack sm">
          <span className="section-title">Checked automatically</span>
          {auto.map((a) => (
            <div className="cl-item" key={a}>
              <span className="dot-ok">
                <Icon name="check" size={12} />
              </span>
              {a}
              <span className="auto">auto</span>
            </div>
          ))}
          <span className="section-title" style={{ marginTop: 10 }}>
            You tick these
          </span>
          {manual.length === 0 && <span className="small muted">Nothing yet.</span>}
          {manual.map((m) => {
            const editable = b.checklist.includes(m);
            return (
              <div className="cl-item" key={m}>
                <span className="cl-box" />
                <span style={{ flex: 1 }}>{m}</span>
                {editable && (
                  <button className="btn sm ghost icon" aria-label={`Remove “${m}”`} onClick={() => set("checklist", b.checklist.filter((x) => x !== m))}>
                    <Icon name="x" />
                  </button>
                )}
              </div>
            );
          })}
          <form
            className="row"
            style={{ marginTop: 6 }}
            onSubmit={(e) => {
              e.preventDefault();
              const t = newItem.trim();
              if (t && !b.checklist.includes(t)) set("checklist", [...b.checklist, t]);
              setNewItem("");
            }}
          >
            <input className="input" value={newItem} onChange={(e) => setNewItem(e.target.value)} placeholder="Add a step, e.g. Comment the song name" aria-label="New checklist step" />
            <button className="btn sm" disabled={!newItem.trim()}>
              <Icon name="plus" /> Add
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}

export function CaptionPreview({ text, disclosure, required }: { text: string; disclosure: string; required: string }) {
  // highlight the paid-post label and the required text inside the generated caption
  const parts: { t: string; k?: "disc" | "req" }[] = [];
  let rest = text;
  const marks = [
    { s: required.trim(), k: "req" as const },
    { s: disclosure.trim(), k: "disc" as const },
  ].filter((m) => m.s);
  while (rest) {
    let best: { i: number; s: string; k: "disc" | "req" } | null = null;
    for (const m of marks) {
      const i = rest.indexOf(m.s);
      if (i >= 0 && (!best || i < best.i)) best = { i, s: m.s, k: m.k };
    }
    if (!best) {
      parts.push({ t: rest });
      break;
    }
    if (best.i) parts.push({ t: rest.slice(0, best.i) });
    parts.push({ t: best.s, k: best.k });
    rest = rest.slice(best.i + best.s.length);
  }
  return (
    <div className="caption-box" aria-label="Caption preview">
      {parts.map((p, i) => (p.k ? <mark key={i} className={p.k}>{p.t}</mark> : <span key={i}>{p.t}</span>))}
      {!text && <span className="muted">…</span>}
    </div>
  );
}

// ================================================================================== Brief
function BriefTab({ camp, b, set, onServer, onStatus, onDeleted, dirty }: { camp: Campaign; b: Brief; set: SetFn; onServer: (c: Campaign) => void; onStatus: (c: Campaign) => void; onDeleted: () => void; dirty: boolean }) {
  const toast = useToast();
  const nav = useNavigate();
  const { confirm } = useDialogs();
  const [paste, setPaste] = useState(false);
  const field = (label: string, k: "cpm" | "max_payout" | "min_views" | "min_engagement" | "keep_live_days", step = "1") => (
    <div className="field">
      <label>{label}</label>
      <input className="input" type="number" step={step} value={b[k] ?? ""} onChange={(e) => set(k, num(e.target.value))} />
    </div>
  );
  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      <div className="stack lg">
        <section className="card">
          <div className="card-head">
            <h2>
              Pay &amp; targets <HelpTip topic="reference" />
            </h2>
            <span className="small muted">for your reference</span>
          </div>
          <div className="card-body stack">
            <div className="grid cols-2">
              {field("Pay per 1,000 views ($)", "cpm", "0.01")}
              {field("Max payout ($)", "max_payout")}
              {field("Minimum views", "min_views")}
              {field("Minimum engagement (%)", "min_engagement", "0.1")}
              {field("Keep posts live (days)", "keep_live_days")}
              <div className="field">
                <label>Campaign site / brand</label>
                <input className="input" value={b.source} placeholder="e.g. Clipster, Whop" onChange={(e) => set("source", e.target.value)} />
              </div>
            </div>
            <div className="field">
              <label>Brief link</label>
              <input className="input" value={b.url} placeholder="https://…" onChange={(e) => set("url", e.target.value)} />
            </div>
            <p className="explain" style={{ margin: 0 }}>
              These don't change your videos. They're kept for your records and will be used for earnings tracking.
            </p>
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <h2>
              Notes for AI picks <HelpTip topic="ai_notes" />
            </h2>
          </div>
          <div className="card-body">
            <textarea className="textarea" rows={3} value={b.content_notes} onChange={(e) => set("content_notes", e.target.value)} placeholder="e.g. Big-win reactions, funny moments. No losses." aria-label="Notes for AI picks" />
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <h2>Brand's rules</h2>
          </div>
          <div className="card-body stack sm">
            <LinesEditor items={b.rules} onChange={(v) => set("rules", v)} placeholder="One rule per line" rows={5} />
            <span className="hint">Do's and don'ts from the brief, one per line. Included in each clip's export notes and in AI picks.</span>
          </div>
        </section>
      </div>
      <div className="stack lg">
        <section className="card">
          <div className="card-head">
            <h2>Original brief</h2>
            <button
              className="btn sm"
              onClick={async () => {
                if (dirty && !(await confirm({ title: "Discard unsaved changes?", body: "Pasting a brief reloads the campaign from it, so unsaved edits on this page are lost. Save first to keep them.", confirm: "Continue" }))) return;
                setPaste(true);
              }}
            >
              <Icon name="clipboard" /> Paste updated brief
            </button>
          </div>
          <div className="card-body">{b.raw_brief ? <div className="brief-raw">{b.raw_brief}</div> : <p className="small muted">No brief pasted yet.</p>}</div>
        </section>
        <section className="card card-body row between">
          <div className="stack sm" style={{ gap: 2 }}>
            <strong>{camp.status === "archived" ? "Archived" : "Done with this campaign?"}</strong>
            <span className="small muted">Archiving hides it from lists. Deleting keeps your videos and clips.</span>
          </div>
          <div className="row">
            <button
              className="btn sm"
              onClick={async () => {
                try {
                  // merge only the status: unsaved edits on this page stay
                  onStatus(await api.patchCampaign(camp.id, { status: camp.status === "archived" ? "active" : "archived" }));
                } catch (e) {
                  toast((e as Error).message, "error");
                }
              }}
            >
              {camp.status === "archived" ? "Unarchive" : "Archive"}
            </button>
            <button
              className="btn sm danger"
              onClick={async () => {
                if (await confirm({ title: "Delete this campaign?", body: "Removes the campaign, its logo and sound files. Videos, clips and exported folders are kept.", confirm: "Delete campaign", danger: true })) {
                  try {
                    await api.deleteCampaign(camp.id);
                    onDeleted();
                    toast("Campaign deleted");
                    nav("/campaigns");
                  } catch (e) {
                    toast((e as Error).message, "error");
                  }
                }
              }}
            >
              <Icon name="trash" /> Delete
            </button>
          </div>
        </section>
      </div>
      {paste && <PasteBrief id={camp.id} onClose={() => setPaste(false)} onDone={onServer} />}
    </div>
  );
}

function PasteBrief({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: (c: Campaign) => void }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="Paste an updated brief"
      onClose={onClose}
      dirty={!!text.trim()}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn primary"
            disabled={!text.trim() || busy}
            onClick={async () => {
              setBusy(true);
              try {
                const c = await api.applyBriefText(id, text);
                onDone(c);
                toast(c.found?.length ? `Updated: ${c.found.slice(0, 4).join(" · ")}${c.found.length > 4 ? "…" : ""}` : "Nothing recognised; edit the requirements by hand");
                onClose();
              } catch (e) {
                toast((e as Error).message, "error");
                setBusy(false);
              }
            }}
          >
            {busy ? <Spinner /> : <Icon name="check" />} Apply
          </button>
        </>
      }
    >
      <p className="small muted">Requirements mentioned in the text are updated; everything else stays as it is. The campaign keeps its type.</p>
      <textarea className="textarea" rows={10} value={text} onChange={(e) => setText(e.target.value)} autoFocus aria-label="Updated brief" />
    </Modal>
  );
}
