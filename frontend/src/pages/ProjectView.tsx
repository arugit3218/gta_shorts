import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, media } from "../api";
import { CampaignSelect, CopyButton } from "../components/campaign";
import { FramingDialog } from "../components/framing";
import { RecipeSelect, describeRecipe } from "../components/polish";
import { PostedBadge, postedState } from "../components/posting";
import { Icon } from "../components/Icon";
import { Alert, Bar, Empty, JobTime, Modal, Spinner, StageList, StatusBadge, attempt, plural, toneOf, useDialogs, useToast } from "../components/ui";
import { ShortcutHelp } from "../components/shortcuts";
import { fmtDuration, fmtTime } from "../format";
import { clipOrder, useHotkeys, usePoll } from "../hooks";
import type { Clip, Project, Recipe } from "../types";

const PROJECT_KEYS: [string, string][] = [
  ["j / k", "Next / previous clip"],
  ["space", "Play / pause"],
  ["a", "Approve (again to undo)"],
  ["r", "Reject (again to undo)"],
  ["x", "Select / unselect"],
  ["e / enter", "Open in the editor"],
  ["shift+a", "Approve all selected"],
  ["escape", "Clear the selection"],
  ["?", "Show shortcuts"],
];

type Filter = "all" | "approved" | "candidate" | "rejected";

export default function ProjectView() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const { confirm } = useDialogs();
  const [busyPoll, setBusyPoll] = useState(true);
  const proj = usePoll(() => api.project(id), [id], busyPoll ? 1500 : 6000);
  const p = proj.data;
  const active = p?.status === "processing" || p?.status === "queued";
  const clips = usePoll(() => api.clips(id), [id, p?.clip_count], busyPoll ? 2500 : 8000);
  const working = active || !!clips.data?.some((c) => c.render_status === "queued" || c.render_status === "rendering");
  useEffect(() => setBusyPoll(working), [working]);
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<"score" | "time">("score");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [addClip, setAddClip] = useState(false);
  const [aiPicks, setAiPicks] = useState(false);
  const [framing, setFraming] = useState<false | "all" | "selected">(false);
  const [recipe, setRecipe] = useState<false | "all" | "selected">(false);
  const [capPos, setCapPos] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [help, setHelp] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const montage = p?.source_type === "montage";
  const songInfo = usePoll(() => (montage && p?.status === "ready" ? api.songInfo(id) : Promise.resolve(null)), [id, montage, p?.status]);

  const all = useMemo(() => clips.data ?? [], [clips.data]);
  const counts = useMemo(() => {
    const c = { all: all.length, approved: 0, candidate: 0, rejected: 0 };
    for (const x of all) c[x.status] += 1;
    return c;
  }, [all]);
  const shown = useMemo(() => {
    const list = all.filter((c) => filter === "all" || c.status === filter);
    return [...list].sort((a, b) => (sort === "score" ? b.score - a.score : a.start - b.start));
  }, [all, filter, sort]);
  // the editor's Previous / Next follow this order
  useEffect(() => {
    if (shown.length) clipOrder.save(id, shown.map((c) => c.id));
  }, [id, shown]);
  // forget selections of clips that are gone (re-processed / deleted)
  useEffect(() => {
    setSelected((s) => {
      const ids = new Set(all.map((c) => c.id));
      const next = new Set([...s].filter((x) => ids.has(x)));
      return next.size === s.size ? s : next;
    });
  }, [all]);

  // clips already posted everywhere don't need a fresh render (that video is out)
  const approvedNotRendered = all.filter((c) => c.status === "approved" && ["pending", "failed", "stale"].includes(c.render_status) && !postedState(c.posted, c.post_platforms).done);
  const approvedRendered = all.filter((c) => c.status === "approved" && c.has_video && c.render_status === "done");
  const sel = shown.filter((c) => selected.has(c.id));
  const updateClip = (c: Clip) => clips.setData((list) => (list ?? []).map((x) => (x.id === c.id ? c : x)));

  const setStatus = async (c: Clip, status: Clip["status"]) => {
    const before = c.status;
    const next = c.status === status ? "candidate" : status;
    try {
      updateClip(await api.patchClip(c.id, { status: next }));
      toast(next === "approved" ? `Approved #${c.rank}` : next === "rejected" ? `Rejected #${c.rank}` : `#${c.rank} back to not reviewed`, "info", {
        action: { label: "Undo", onClick: async () => updateClip(await api.patchClip(c.id, { status: before })) },
      });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const bulk = async (ids: string[], action: "approve" | "reject" | "unreview" | "render") => {
    if (!ids.length) return;
    setBusy(true);
    try {
      const r = await api.clipActions(id, ids, action);
      toast(action === "render" ? `Rendering ${plural(r.rendering, "clip")}` : `${plural(r.updated, "clip")} ${action === "approve" ? "approved" : action === "reject" ? "rejected" : "marked not reviewed"}`);
      await clips.reload();
      proj.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const toggle = (cid: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(cid)) n.delete(cid);
      else n.add(cid);
      return n;
    });
  const move = (d: 1 | -1) => {
    if (!shown.length) return;
    const i = focus ? shown.findIndex((c) => c.id === focus) : -1;
    const next = shown[Math.max(0, Math.min(shown.length - 1, i < 0 ? 0 : i + d))];
    setFocus(next.id);
    document.querySelector(`[data-clip-id="${next.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  const focused = shown.find((c) => c.id === focus) ?? null;
  useHotkeys(
    {
      j: () => move(1),
      k: () => move(-1),
      arrowleft: () => move(-1),
      arrowright: () => move(1),
      a: () => focused && setStatus(focused, "approved"),
      r: () => focused && setStatus(focused, "rejected"),
      x: () => focused && toggle(focused.id),
      e: () => focused && nav(`/clips/${focused.id}`),
      enter: () => focused && nav(`/clips/${focused.id}`),
      "shift+a": () => bulk(sel.map((c) => c.id), "approve"),
      escape: () => setSelected(new Set()),
      space: () => {
        const v = focused && document.querySelector<HTMLVideoElement>(`[data-clip-id="${focused.id}"] video`);
        if (v) v.paused ? v.play() : v.pause();
      },
      "?": () => setHelp(true),
    },
    !!p && p.clip_count > 0,
  );

  if (proj.error && !p) return <Alert kind="danger" title="Project not found">{proj.error}</Alert>;
  if (!p) return <div className="skeleton" style={{ height: 200 }} />;

  const startNow = async (keep = false) => {
    try {
      await api.process(p.id, {}, keep);
      proj.reload();
      toast("Processing queued");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  // starting fresh (or after a failure with nothing to lose) needs no questions
  const reprocess = () => (p.clip_count === 0 ? startNow() : setReprocessing(true));
  const cancel = async () => {
    if (!p.job) return;
    try {
      await api.cancelJob(p.job.id);
      toast("Cancelling…");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const changeCampaign = async (cid: string) => {
    if (p.rendered_count > 0 && (cid || p.campaign_id)) {
      const ok = await confirm({
        title: cid ? "Move this project into the campaign?" : "Remove the campaign?",
        body: cid
          ? `Its logo, sound and text rules apply to every clip, and clips switch to 9:16. ${plural(p.rendered_count, "rendered clip")} will need re-rendering.`
          : `Clips lose the campaign's logo and rules. ${plural(p.rendered_count, "rendered clip")} will need re-rendering.`,
        confirm: cid ? "Move to campaign" : "Remove campaign",
      });
      if (!ok) return;
    }
    try {
      proj.setData(await api.patchProject(p.id, { campaign_id: cid }));
      clips.reload();
      if (p.rendered_count > 0)
        toast(cid ? "Campaign set. Clips need re-rendering." : "Campaign removed. Clips need re-rendering.", "info", {
          action: {
            label: "Re-render all",
            onClick: () =>
              attempt(toast, async () => {
                const r = await api.bulkClips(p.id, { scope: "unrejected", render: true });
                toast(`Re-rendering ${plural(r.rendering, "clip")}`);
                clips.reload();
              }),
          },
        });
      else toast(cid ? "Campaign set" : "Campaign removed");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className="stack lg">
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <Link to={p.campaign_id ? `/campaigns/${p.campaign_id}` : "/projects"} className="small muted row" style={{ gap: 4, marginBottom: 6 }}>
            <Icon name="chevronLeft" size={14} /> {p.campaign_id ? p.campaign_name ?? "Campaign" : "Videos"}
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
          <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
            {p.campaign_id ? (
              <Link to={`/campaigns/${p.campaign_id}`} className="badge accent">
                <Icon name="target" size={11} /> {p.campaign_name}
              </Link>
            ) : (
              <span className="badge">No campaign</span>
            )}
            {montage && <span className="badge accent">Music edit</span>}
            {montage && songInfo.data && (
              <span className="badge">
                {songInfo.data.name} · {Math.round(songInfo.data.tempo)} BPM{songInfo.data.drop != null ? ` · drop ${songInfo.data.drop.toFixed(1)}s` : ""}
              </span>
            )}
            {montage && <span className="badge">{p.options.montage?.sources.length ?? 0} source video(s)</span>}
            {p.content_mode && !montage && <span className="badge" title="How clips were found">{p.content_mode === "gameplay" ? "Gameplay / action mode" : "Talk mode"}</span>}
            {p.video_mode === "sections" && <span className="badge info" title="Only the parts of the video your clips need were downloaded">Smart download · sections only</span>}
            {p.insights?.heatmap && <span className="badge success" title="YouTube's 'Most replayed' data was used in ranking">Most replayed used</span>}
            {p.insights?.chapters > 0 && <span className="badge">{p.insights.chapters} chapters</span>}
          </div>
        </div>
        <div className="row wrap">
          {p.clip_count > 0 && !montage && (
            <>
              <a className="btn sm" href={media.transcript(p.id, "srt")}>
                <Icon name="file" /> Transcript .srt
              </a>
              <a className="btn sm" href={media.transcript(p.id, "json")} download={`${p.id}-transcript.json`}>
                <Icon name="file" /> .json
              </a>
            </>
          )}
          <div style={{ width: 190 }} title="Campaign">
            <CampaignSelect value={p.campaign_id} onChange={changeCampaign} />
          </div>
          {montage && p.clip_count > 0 && !active && (
            <button
              className="btn sm"
              onClick={async () => {
                try {
                  await api.newVariant(p.id);
                  toast("New variant queued");
                  clips.reload();
                  proj.reload();
                } catch (e) {
                  toast((e as Error).message, "error");
                }
              }}
            >
              <Icon name="plus" /> New variant
            </button>
          )}
          {p.status === "ready" && !active && !montage && (
            <>
              <button className="btn sm" disabled={p.clip_count === 0} onClick={() => setFraming("all")} title="Change framing (fill, stream layout…) for many clips. Tip: select clips first to change only those.">
                <Icon name="crop" /> Framing
              </button>
              <button className="btn sm" disabled={p.clip_count === 0} onClick={() => setRecipe("all")} title="Apply a saved look (captions, polish, finishing) to every clip">
                <Icon name="zap" /> Apply recipe
              </button>
              <button className="btn sm" onClick={() => setAddClip(true)} title="Make a clip from your own in/out points">
                <Icon name="scissors" /> Add clip
              </button>
              <button className="btn sm" onClick={() => setAiPicks(true)} title="Get a second opinion from any AI chat">
                <Icon name="sparkles" /> AI picks
              </button>
            </>
          )}
          {!active && (
            <button
              className="btn sm"
              onClick={() => (p.status === "new" && !montage ? nav(`/new?setup=${p.id}`) : reprocess())}
              disabled={!p.source_available && p.source_type !== "url"}
              title={!p.source_available && p.source_type !== "url" ? "The source video file is missing (deleted or moved), so this video can't be processed again. Upload it again as a new video." : undefined}
            >
              <Icon name="refresh" /> {p.status === "new" ? (montage ? "Start" : "Choose settings") : montage ? "Re-make all variants" : "Re-process"}
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
                {p.job.status === "running" && (
                  <>
                    {" · "}
                    <JobTime job={p.job} />
                  </>
                )}
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
      {p.status === "ready" && (p.job?.stages?.score?.message?.includes("not padding") || p.job?.stages?.score?.message?.includes("you asked for")) && <Alert kind="info">{p.job.stages.score.message}</Alert>}
      {p.status === "ready" && !montage && all.length === 0 && clips.data && (
        <Empty icon="scissors" title="No clips were found in this video">
          <p className="small muted" style={{ maxWidth: 480, margin: "0 auto 12px" }}>
            No moment scored above the minimum quality. Make one yourself, ask an AI chat to pick moments, or re-process with a lower “Minimum score” (Settings → Basics).
          </p>
          <div className="row" style={{ justifyContent: "center" }}>
            <button className="btn primary" onClick={() => setAddClip(true)}>
              <Icon name="scissors" /> Add a clip by hand
            </button>
            <button className="btn" onClick={() => setAiPicks(true)}>
              <Icon name="sparkles" /> AI picks
            </button>
          </div>
        </Empty>
      )}
      {p.status === "ready" && p.job?.stages?.metadata?.message?.startsWith("Local AI unavailable") && <Alert kind="warn">{p.job.stages.metadata.message}</Alert>}

      {all.length > 0 && (
        <>
          <div className="row between wrap">
            <div className="row wrap" style={{ gap: 14 }}>
              <h2>Clips</h2>
              <span className="muted small">
                {p.rendered_count}/{p.clip_count} rendered · {p.approved_count} approved
              </span>
              {approvedNotRendered.length > 0 && (
                <button className="btn sm primary" disabled={busy} onClick={() => bulk(approvedNotRendered.map((c) => c.id), "render")}>
                  <Icon name="play" /> Render approved ({approvedNotRendered.length})
                </button>
              )}
              {approvedRendered.length > 0 && (
                <a className="btn sm" href={media.clipsZip(p.id)} title="All rendered approved clips in one .zip">
                  <Icon name="download" /> Download approved ({approvedRendered.length})
                </a>
              )}
            </div>
            <div className="row wrap">
              <div className="seg" role="radiogroup" aria-label="Filter clips">
                {(["all", "approved", "candidate", "rejected"] as const).map((f) => (
                  <button key={f} role="radio" aria-checked={filter === f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
                    {f === "candidate" ? "unreviewed" : f} <span className="count">{counts[f]}</span>
                  </button>
                ))}
              </div>
              <div className="seg" role="radiogroup" aria-label="Sort clips">
                <button role="radio" aria-checked={sort === "score"} className={sort === "score" ? "active" : ""} onClick={() => setSort("score")}>
                  By score
                </button>
                <button role="radio" aria-checked={sort === "time"} className={sort === "time" ? "active" : ""} onClick={() => setSort("time")}>
                  By time
                </button>
              </div>
              <button className="btn sm ghost icon" onClick={() => setHelp(true)} title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts">
                ?
              </button>
            </div>
          </div>

          <div className={`select-bar ${sel.length ? "active" : ""}`}>
            <label className="check small">
              <input
                type="checkbox"
                checked={shown.length > 0 && sel.length === shown.length}
                ref={(el) => {
                  if (el) el.indeterminate = sel.length > 0 && sel.length < shown.length;
                }}
                onChange={(e) => setSelected(e.target.checked ? new Set(shown.map((c) => c.id)) : new Set())}
              />
              {sel.length ? `${sel.length} selected` : "Select"}
            </label>
            {sel.length === 0 ? (
              <span className="row wrap small muted" style={{ gap: 6 }}>
                Quick select:
                <button className="btn ghost sm" onClick={() => setSelected(new Set(shown.filter((c) => c.status === "candidate").map((c) => c.id)))}>
                  Unreviewed
                </button>
                <button className="btn ghost sm" onClick={() => setSelected(new Set(shown.filter((c) => c.score >= 70).map((c) => c.id)))}>
                  Score 70+
                </button>
                <button className="btn ghost sm" onClick={() => setSelected(new Set(shown.filter((c) => (!c.has_video || c.render_status === "stale") && !postedState(c.posted, c.post_platforms).done).map((c) => c.id)))}>
                  Needs render
                </button>
              </span>
            ) : (
              <span className="row wrap" style={{ gap: 6 }}>
                <button className="btn sm" disabled={busy} onClick={() => bulk(sel.map((c) => c.id), "approve")}>
                  <Icon name="thumbsUp" /> Approve
                </button>
                <button className="btn sm" disabled={busy} onClick={() => bulk(sel.map((c) => c.id), "reject")}>
                  <Icon name="thumbsDown" /> Reject
                </button>
                <button className="btn sm" disabled={busy} onClick={() => bulk(sel.map((c) => c.id), "render")}>
                  <Icon name="play" /> Render
                </button>
                {!montage && (
                  <>
                    <button className="btn sm" disabled={busy} onClick={() => setFraming("selected")} title="Change framing for just these clips">
                      <Icon name="crop" /> Framing
                    </button>
                    <button className="btn sm" disabled={busy} onClick={() => setRecipe("selected")} title="Apply a saved look to just these clips">
                      <Icon name="zap" /> Apply recipe
                    </button>
                    <button className="btn sm" disabled={busy} onClick={() => setCapPos(true)} title="Move or turn off the captions of just these clips">
                      <Icon name="type" /> Captions
                    </button>
                  </>
                )}
                {sel.some((c) => c.has_video && c.render_status === "done") && (
                  <a className="btn sm" href={media.clipsZip(p.id, sel.filter((c) => c.has_video && c.render_status === "done").map((c) => c.id))} title="Only clips whose video includes their latest edits">
                    <Icon name="download" /> Download ({sel.filter((c) => c.has_video && c.render_status === "done").length})
                  </a>
                )}
                <button className="btn ghost sm" onClick={() => setSelected(new Set())}>
                  Clear
                </button>
              </span>
            )}
          </div>

          {shown.length === 0 ? (
            <Empty icon="film" title="No clips match this filter" />
          ) : (
            <div className="gallery">
              {shown.map((c) => (
                <ClipCard
                  key={c.id}
                  clip={c}
                  onChange={updateClip}
                  reload={clips.reload}
                  onStatus={(s) => setStatus(c, s)}
                  selected={selected.has(c.id)}
                  onSelect={() => toggle(c.id)}
                  focused={focus === c.id}
                  onFocus={() => setFocus(c.id)}
                />
              ))}
            </div>
          )}
        </>
      )}

      {p.status === "new" && p.used_in.length > 0 && (
        <Alert kind="info" title="Source video for a music montage">
          Used in {p.used_in.map((m, i) => (
            <span key={m.id}>
              {i > 0 && ", "}
              <Link to={`/projects/${m.id}`}>{m.name}</Link>
            </span>
          ))}
          . It isn't listed under Videos because it hasn't been clipped on its own. Choose settings below if you also want clips from it.
        </Alert>
      )}

      {p.status === "new" && !montage && (
        <Empty icon="play" title="Not started">
          <p className="small muted">This video was added but hasn't been processed yet.</p>
          <div className="row" style={{ justifyContent: "center", marginTop: 8 }}>
            <Link className="btn primary" to={`/new?setup=${p.id}`}>
              <Icon name="settings" /> Choose settings
            </Link>
            <button className="btn" onClick={() => startNow()} title="Uses the defaults from Settings">
              <Icon name="zap" /> Start with default settings
            </button>
          </div>
        </Empty>
      )}
      {p.status === "new" && montage && (
        <Empty icon="play" title="Not started">
          <button className="btn primary" style={{ marginTop: 8 }} onClick={() => startNow()}>
            <Icon name="zap" /> Make montage
          </button>
        </Empty>
      )}

      {montage && <MontageSources project={p} />}

      {confirmDelete && <DeleteDialog project={p} postedClips={all.filter((c) => Object.keys(c.posted ?? {}).length > 0).length} onClose={() => setConfirmDelete(false)} onDeleted={() => nav(p.campaign_id ? `/campaigns/${p.campaign_id}` : "/projects")} />}
      {addClip && <AddClipDialog project={p} onClose={() => setAddClip(false)} onDone={() => (clips.reload(), proj.reload())} />}
      {framing && <FramingDialog project={p} selected={framing === "selected" ? sel.map((c) => c.id) : []} onClose={() => setFraming(false)} onDone={() => (clips.reload(), proj.reload())} />}
      {capPos && <CaptionPositionDialog project={p} clips={sel} onClose={() => setCapPos(false)} onDone={() => (clips.reload(), proj.reload())} />}
      {recipe && <RecipeDialog project={p} selected={recipe === "selected" ? sel.map((c) => c.id) : []} onClose={() => setRecipe(false)} onDone={() => (clips.reload(), proj.reload())} />}
      {aiPicks && <AiPicksDialog project={p} clips={all} onClose={() => setAiPicks(false)} onDone={() => (clips.reload(), proj.reload())} />}
      {reprocessing && <ReprocessDialog project={p} clips={all} onClose={() => setReprocessing(false)} onStart={(keep) => (setReprocessing(false), startNow(keep))} />}
      {help && <ShortcutHelp items={PROJECT_KEYS} onClose={() => setHelp(false)} />}
    </div>
  );
}

/** Caption height for the selected clips: automatic (campaign / look, kept clear of the logo) or one height. */
function CaptionPositionDialog({ project, clips, onClose, onDone }: { project: Project; clips: Clip[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const first = clips[0]?.features?.text_layout?.caption_position ?? 0.7;
  const [mode, setMode] = useState<"auto" | "set" | "off">("set");
  // the campaign switched captions off: clip settings only matter once it turns them back on
  const campaignOff = clips.some((c) => c.features?.captions === false && c.edit.captions !== false);
  const [y, setY] = useState(Math.round(first * 100) / 100);
  const [busy, setBusy] = useState(false);
  const rendered = clips.filter((c) => c.has_video).length;
  const go = async () => {
    setBusy(true);
    try {
      // setting a height (or automatic) also turns captions back on for clips that had them off
      const what = mode === "off" ? { captions: false } : mode === "auto" ? { caption_position_auto: true, captions: true } : { caption_position: y, captions: true };
      // only clips that already have a video re-render; the others pick it up when they're rendered
      const withVideo = clips.filter((c) => c.has_video).map((c) => c.id);
      const without = clips.filter((c) => !c.has_video).map((c) => c.id);
      let updated = 0;
      let rendering = 0;
      if (withVideo.length) {
        const r = await api.bulkClips(project.id, { ids: withVideo, render: true, ...what });
        updated += r.updated;
        rendering += r.rendering;
      }
      if (without.length) updated += (await api.bulkClips(project.id, { ids: without, render: false, ...what })).updated;
      const did = mode === "off" ? "Captions turned off on" : "Captions set on";
      toast(updated ? `${did} ${plural(updated, "clip")}${rendering ? `, ${rendering} re-rendering` : ""}` : "Nothing to change: those clips already use this");
      onDone();
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Captions for ${plural(clips.length, "selected clip")}`}
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy} onClick={go}>
            {busy ? <Spinner /> : <Icon name="check" />} Apply{rendered ? " & re-render" : ""}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="seg">
          <button className={mode === "set" ? "active" : ""} onClick={() => setMode("set")}>
            Set the height
          </button>
          <button className={mode === "auto" ? "active" : ""} onClick={() => setMode("auto")}>
            Automatic
          </button>
          <button className={mode === "off" ? "active" : ""} onClick={() => setMode("off")}>
            No captions
          </button>
        </div>
        {mode === "off" ? (
          <p className="small muted" style={{ margin: 0 }}>
            These clips will have no captions. The title text, end text and logo stay. Choose a height or Automatic later to bring captions back.
          </p>
        ) : mode === "set" ? (
          <div className="field">
            <label htmlFor="bulk-cap">Captions {Math.round(y * 100)}% from the top</label>
            <input id="bulk-cap" type="range" min={0.1} max={0.92} step={0.01} value={y} onChange={(e) => setY(+e.target.value)} />
            <span className="hint">Around 60–75% is usual. Higher numbers sit lower on the screen; the very bottom is covered by the app's caption and buttons.</span>
          </div>
        ) : (
          <p className="small muted" style={{ margin: 0 }}>
            Back to the campaign's caption position (or the caption look's own), moved clear of the logo when they'd overlap.
          </p>
        )}
        {campaignOff && mode !== "off" && <Alert kind="info">Captions are turned off for this video's campaign, so they won't show until the campaign turns them back on (Requirements → Text on the video).</Alert>}
        {rendered > 0 && <span className="small muted">{plural(rendered, "rendered clip")} will re-render{mode === "off" ? " without captions" : " with the new setting"}.</span>}
      </div>
    </Modal>
  );
}

function ReprocessDialog({ project: p, clips, onClose, onStart }: { project: Project; clips: Clip[]; onClose: () => void; onStart: (keep: boolean) => void }) {
  const nav = useNavigate();
  const montage = p.source_type === "montage";
  const approved = clips.filter((c) => c.status === "approved").length;
  const handmade = clips.filter((c) => c.status !== "approved" && ["manual", "ai"].includes(c.analysis.origin ?? "")).length;
  const rendered = clips.filter((c) => c.has_video).length;
  const precious = approved + handmade;
  const [keep, setKeep] = useState(precious > 0 && !montage);
  // clips marked as posted are always kept (their post history and links must survive)
  const isPosted = (c: Clip) => Object.keys(c.posted ?? {}).length > 0;
  const posted = clips.filter(isPosted).length;
  const lost = clips.filter((c) => !isPosted(c) && !(keep && !montage && (c.status === "approved" || ["manual", "ai"].includes(c.analysis.origin ?? "")))).length;
  return (
    <Modal
      title={montage ? "Re-make all variants?" : "Re-process this video?"}
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          {!montage && (
            <button className="btn" onClick={() => nav(`/new?reprocess=${p.id}`)}>
              <Icon name="settings" /> Change settings first
            </button>
          )}
          <button className={`btn ${lost > 0 ? "danger-solid" : "primary"}`} onClick={() => onStart(keep && !montage)}>
            <Icon name="refresh" /> {montage ? "Re-make" : "Re-process"}
          </button>
        </>
      }
    >
      <p className="small">
        Finds clips again with the same settings. The download and transcript are reused, so this is quicker than the first time.
      </p>
      <div className="alert warn small">
        <Icon name="alert" />
        <span>
          {lost > 0 ? (
            <>
              <strong>{plural(lost, "clip")} will be deleted</strong>
              {rendered > 0 ? `, including rendered videos` : ""}.
            </>
          ) : (
            <>No clips will be deleted.</>
          )}{" "}
          This project has {plural(clips.length, "clip")}: {approved} approved, {handmade} made by hand or AI picks, {rendered} rendered.
          {posted > 0 && ` ${plural(posted, "clip")} marked as posted ${posted === 1 ? "is" : "are"} always kept.`}
        </span>
      </div>
      {!montage && precious > 0 && (
        <label className="check">
          <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
          <span>
            Keep the {plural(precious, "approved or hand-made clip")}{posted ? " (posted clips are kept either way)" : ""}
            <br />
            <span className="small muted">New clips that overlap them are skipped.</span>
          </span>
        </label>
      )}
    </Modal>
  );
}

function DeleteDialog({ project, postedClips, onClose, onDeleted }: { project: Project; postedClips: number; onClose: () => void; onDeleted: () => void }) {
  const toast = useToast();
  const [exports, setExports] = useState(false);
  const [source, setSource] = useState(false);
  const [footage, setFootage] = useState(false);
  const [busy, setBusy] = useState(false);
  const montage = project.source_type === "montage";
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.deleteProject(project.id, { delete_exports: exports, delete_source: source, delete_footage: montage && footage });
      toast(`Video deleted · ${r.freed_mb} MB freed${r.footage_deleted ? ` · ${plural(r.footage_deleted, "source video")} deleted` : ""}${r.exports_kept_at ? " · rendered clips kept" : ""}`);
      onDeleted();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Delete this video?"
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
      {postedClips > 0 && (
        <Alert kind="warn">
          {plural(postedClips, "clip")} of this video {postedClips === 1 ? "is" : "are"} marked as posted. Their post marks and links are deleted too.
        </Alert>
      )}
      <label className="check">
        <input type="checkbox" checked={exports} onChange={(e) => setExports(e.target.checked)} />
        <span>Also delete the rendered clips (MP4 exports)</span>
      </label>
      {montage ? (
        <label className="check">
          <input type="checkbox" checked={footage} onChange={(e) => setFootage(e.target.checked)} />
          <span>
            Also delete the source videos added for this montage
            <br />
            <span className="small muted">Only videos that were never clipped on their own and aren't used by another montage. Files you used in place stay where they are.</span>
          </span>
        </label>
      ) : project.source_owned ? (
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

/** The videos a montage cuts from. They don't appear under Projects unless they were clipped on their own. */
function MontageSources({ project }: { project: Project }) {
  const ids = project.options.montage?.sources ?? [];
  const all = usePoll(api.projects, [ids.join(",")]).data;
  if (!ids.length) return null;
  const list = ids.map((id) => all?.find((x) => x.id === id)).filter((x): x is Project => !!x);
  return (
    <section className="card">
      <div className="card-head">
        <h2>Source videos</h2>
        <span className="small muted">{plural(ids.length, "video")}</span>
      </div>
      <div className="card-body stack sm">
        {!all ? (
          <Spinner />
        ) : (
          list.map((s) => (
            <div key={s.id} className="list-row row" style={{ justifyContent: "space-between" }}>
              <span className="row" style={{ minWidth: 0, gap: 8 }}>
                <Icon name={s.source_type === "url" ? "link" : "film"} />
                <span className="ellipsis">{s.name}</span>
              </span>
              <span className="row nowrap small muted">
                {fmtDuration(s.duration)}
                {s.video_mode === "missing" ? " · not downloaded yet" : s.video_mode === "sections" ? " · sections only" : ""}
                {s.stage === "active" ? (
                  <Link className="btn sm ghost" to={`/projects/${s.id}`}>
                    Open project
                  </Link>
                ) : (
                  <Link className="btn sm ghost" to={`/new?setup=${s.id}`} title="Also find clips in this video on its own">
                    Find clips in it
                  </Link>
                )}
              </span>
            </div>
          ))
        )}
        {all && list.length < ids.length && <span className="small muted">{plural(ids.length - list.length, "source video")} no longer exist.</span>}
      </div>
    </section>
  );
}

/** One line saying what was done to the clip (R3): captions, logo, cuts, zooms, music… */
function Applied({ clip: c }: { clip: Clip }) {
  const a = c.analysis;
  const f = c.features;
  const bits: string[] = [];
  const montage = a.mode === "montage";
  if (montage) bits.push("cut on the beat");
  else if (c.edit.captions !== false && f?.captions !== false) bits.push("captions");
  else bits.push("no captions");
  if (f?.logo) bits.push("logo");
  if (!montage && f?.hook_overlay?.enabled) bits.push("title text");
  const pu = a.polish_used;
  if (c.has_video) {
    if (pu?.removed) bits.push(`${pu.removed}s dead air cut`);
    if (pu?.zooms) bits.push(`${pu.zooms} zoom${pu.zooms === 1 ? "" : "s"}`);
    if (pu?.cold_open) bits.push("cold open");
    if (a.music_used) bits.push("music");
    if (a.sfx_used?.length) bits.push(`${a.sfx_used.length} sound effect${a.sfx_used.length === 1 ? "" : "s"}`);
    if (a.burned_text) bits.push("source captions blurred");
  } else if (f?.polish && f.polish.jumpcut !== "off" && !montage) bits.push("dead air will be cut");
  if (!bits.length) return null;
  return (
    <div className="applied" title={c.has_video ? "What was done to this clip" : "What will be done when it's rendered"}>
      <Icon name="sparkles" size={12} /> {bits.join(" · ")}
    </div>
  );
}

const RENDER_LABEL: Record<string, string> = {
  pending: "Not rendered yet",
  queued: "Queued for rendering",
  rendering: "Rendering…",
  failed: "Render failed",
  stale: "Edited, re-render to update",
};

export function ClipCard({
  clip: c,
  onChange,
  reload,
  onStatus,
  selected,
  onSelect,
  focused,
  onFocus,
}: {
  clip: Clip;
  onChange: (c: Clip) => void;
  reload: () => void;
  onStatus?: (s: Clip["status"]) => void;
  selected?: boolean;
  onSelect?: () => void;
  focused?: boolean;
  onFocus?: () => void;
}) {
  const toast = useToast();
  const [why, setWhy] = useState(false);
  const [src, setSrc] = useState<{ start: number; end: number } | null>(null);
  const [srcBusy, setSrcBusy] = useState(false);
  const tone = toneOf(c.score, c.analysis.label);
  const arClass = c.edit.aspect_ratio === "1:1" ? "ar-11" : c.edit.aspect_ratio === "16:9" ? "ar-169" : "";

  const setStatus = async (status: Clip["status"]) => {
    if (onStatus) return onStatus(status);
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
  const playSource = async () => {
    setSrcBusy(true);
    try {
      const r = await api.sourceInfo(c.id);
      if (!r.available || r.start == null || r.end == null) toast(r.reason || "The video for this part isn't downloaded yet. Render the clip to fetch it.", "error");
      else setSrc({ start: r.start, end: r.end });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSrcBusy(false);
    }
  };
  const showRendered = c.has_video && c.render_status !== "rendering";

  return (
    <div className={`card clip-card ${c.status === "rejected" ? "rejected" : ""} ${selected ? "selected" : ""} ${focused ? "focused" : ""}`} data-clip-id={c.id} onMouseDown={onFocus}>
      <div className={`clip-media ${arClass}`}>
        {src && !showRendered ? (
          <>
            <video
              src={`${media.clipSource(c.id)}#t=${src.start},${src.end}`}
              controls
              autoPlay
              playsInline
              onTimeUpdate={(e) => {
                const v = e.currentTarget;
                if (v.currentTime >= src.end) v.pause();
              }}
            />
            <div className="bl">
              <span className="badge" title="Straight from the source video: not cropped, no captions or effects yet">source preview</span>
            </div>
          </>
        ) : showRendered ? (
          <video src={media.video(c)} poster={c.has_thumb ? media.thumb(c) : undefined} controls preload="metadata" playsInline />
        ) : c.has_thumb ? (
          <img src={media.thumb(c)} alt="" />
        ) : null}
        {!src && (!c.has_video || ["queued", "rendering", "failed"].includes(c.render_status)) && (
          <div className="overlay">
            <div className="stack sm" style={{ alignItems: "center" }}>
              {c.render_status === "rendering" || c.render_status === "queued" ? <Spinner size={22} /> : <Icon name={c.render_status === "failed" ? "alert" : "film"} size={22} />}
              <span className="small">{RENDER_LABEL[c.render_status] ?? ""}</span>
              {c.render_status === "failed" && c.render_error && (
                <span className="tiny" style={{ opacity: 0.8 }} title={c.render_error}>
                  {c.render_error.slice(0, 140)}
                </span>
              )}
              {["pending", "failed", "stale"].includes(c.render_status) && (
                <div className="row" style={{ gap: 6 }}>
                  <button className="btn sm primary" onClick={render}>
                    <Icon name="play" /> Render
                  </button>
                  {!c.has_video && c.analysis.mode !== "montage" && (
                    <button className="btn sm" onClick={playSource} disabled={srcBusy} title="Watch this part of the original video before rendering">
                      {srcBusy ? <Spinner /> : <Icon name="film" />} Preview
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
        <div className="tl">
          {onSelect && (
            <label className="card-check" title="Select (X)" onMouseDown={(e) => e.stopPropagation()}>
              <input type="checkbox" checked={!!selected} onChange={onSelect} aria-label={`Select clip ${c.rank}`} />
            </label>
          )}
          <span className={`score-chip ${tone}`} title={c.analysis.label}>
            {Math.round(c.score)}
            <small>/100</small>
          </span>
        </div>
        <div className="tr">
          {c.status === "approved" && !postedState(c.posted, c.post_platforms).n && (
            <span className="badge success">
              <Icon name="check" size={11} /> approved
            </span>
          )}
          <PostedBadge posted={c.posted} platforms={c.post_platforms} />
          {c.render_status === "stale" && c.has_video && <span className="badge warn">edited</span>}
          {c.exported && <span className="badge info" title={c.exported.path}>exported</span>}
        </div>
      </div>
      <div className="clip-body">
        <div className="row wrap" style={{ gap: 6 }}>
          <span className={`badge ${tone}`}>{c.analysis.label}</span>
          <span className="badge">{c.category}</span>
          {c.analysis.origin && c.analysis.origin !== "auto" && <span className="badge accent">{c.analysis.origin === "ai" ? "AI pick" : c.analysis.origin === "manual" ? "manual" : "quick"}</span>}
          <span className="badge">
            {fmtTime(c.start)} · {Math.round(c.duration)}s
          </span>
        </div>
        <div className="clip-title">{c.title || "Untitled clip"}</div>
        {c.hook && <div className="clip-hook">“{c.hook}”</div>}
        <Applied clip={c} />
        <button className="btn ghost sm" style={{ alignSelf: "flex-start", marginLeft: -8 }} onClick={() => setWhy(!why)}>
          <Icon name="info" /> {why ? "Hide" : "Why this score"}
        </button>
        {why && <ScoreBreakdown clip={c} />}
      </div>
      <div className="clip-actions">
        <button className={`btn sm ${c.status === "approved" ? "on approve" : ""}`} title="Approve (A)" aria-pressed={c.status === "approved"} onClick={() => setStatus("approved")}>
          <Icon name="thumbsUp" /> {c.status === "approved" ? "Approved" : "Approve"}
        </button>
        <button className={`btn sm icon ${c.status === "rejected" ? "on reject" : ""}`} title="Reject (R)" aria-label="Reject" aria-pressed={c.status === "rejected"} onClick={() => setStatus("rejected")}>
          <Icon name="thumbsDown" />
        </button>
        <span className="spacer" />
        <Link to={`/clips/${c.id}`} className="btn sm icon" title="Edit (E)" aria-label="Edit clip">
          <Icon name="edit" />
        </Link>
        {c.status === "approved" && c.has_video ? (
          postedState(c.posted, c.post_platforms).done ? (
            <Link to={`/clips/${c.id}?tab=post`} className="btn sm" title="Posted everywhere. Open to see or edit the post links">
              <Icon name="check" /> Posted
            </Link>
          ) : (
            <Link to={`/clips/${c.id}?tab=post`} className="btn sm primary" title="Check it against the campaign, copy the caption, download and mark it posted">
              <Icon name="check" /> Post
            </Link>
          )
        ) : c.has_video ? (
          <a className="btn sm" href={media.download(c)} title="Download MP4" aria-label="Download MP4">
            <Icon name="download" />
          </a>
        ) : (
          <button className="btn sm" disabled title="Render first" aria-label="Download MP4 (render first)">
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

function AddClipDialog({ project, onClose, onDone }: { project: Project; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("30");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const parse = (v: string) => {
    const m = v.trim().match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
    return m ? (m[1] ? +m[1] * 60 : 0) + +m[2] : NaN;
  };
  const s = parse(start);
  const e = parse(end);
  const valid = !isNaN(s) && !isNaN(e) && e - s >= 3 && e - s <= 180 && e <= (project.duration ?? Infinity) + 0.1;
  return (
    <Modal
      title="Add a clip from your own in/out points"
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn primary"
            disabled={!valid || busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.addClip(project.id, { start: s, end: e, title: title.trim() || undefined, render: true });
                toast("Clip added and queued for rendering");
                onDone();
                onClose();
              } catch (err) {
                toast((err as Error).message, "error");
                setBusy(false);
              }
            }}
          >
            {busy ? <Spinner /> : <Icon name="scissors" />} Add &amp; render
          </button>
        </>
      }
    >
      <p className="small muted">Times as seconds or m:ss. The edges snap to word boundaries; the clip is scored and explained like any other, and gets the campaign template.</p>
      <div className="grid cols-2">
        <div className="field">
          <label>Start</label>
          <input className="input" value={start} onChange={(ev) => setStart(ev.target.value)} placeholder="1:28" autoFocus />
        </div>
        <div className="field">
          <label>End</label>
          <input className="input" value={end} onChange={(ev) => setEnd(ev.target.value)} placeholder="2:01" />
        </div>
      </div>
      <div className="field">
        <label>Title (optional)</label>
        <input className="input" value={title} maxLength={200} onChange={(ev) => setTitle(ev.target.value)} />
      </div>
      {!valid && <span className="small" style={{ color: "var(--warn)" }}>Clips must be 3 s to 3 min and inside the video ({fmtDuration(project.duration)}).</span>}
    </Modal>
  );
}

function AiPicksDialog({ project, clips, onClose, onDone }: { project: Project; clips: Clip[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const { confirm } = useDialogs();
  const isPosted = (c: Clip) => Object.keys(c.posted ?? {}).length > 0; // posted clips are never replaced
  const postedN = clips.filter(isPosted).length;
  const replaceable = clips.length - postedN;
  const approved = clips.filter((c) => c.status === "approved" && !isPosted(c)).length;
  const prompt = usePoll(() => api.aiPrompt(project.id), [project.id]);
  const [answer, setAnswer] = useState("");
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="AI picks: a second opinion from any AI chat"
      wide
      dirty={!!answer.trim()}
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Close
          </button>
          <button
            className="btn primary"
            disabled={!answer.trim() || busy}
            onClick={async () => {
              if (replace && replaceable && !(await confirm({ title: `Delete the ${plural(replaceable, "current clip")}?`, body: `${approved ? `${approved} of them are approved. ` : ""}The AI picks replace them, and their rendered videos are deleted.${postedN ? ` ${plural(postedN, "clip")} marked as posted ${postedN === 1 ? "stays" : "stay"}.` : ""}`, confirm: "Replace clips", danger: true }))) return;
              setBusy(true);
              try {
                const r = await api.aiPicks(project.id, answer, replace);
                toast(`${plural(r.created, "clip")} added${r.problems.length ? ` · ${r.problems.length} skipped` : ""}`);
                onDone();
                onClose();
              } catch (err) {
                toast((err as Error).message, "error");
                setBusy(false);
              }
            }}
          >
            {busy ? <Spinner /> : <Icon name="check" />} Import picks
          </button>
        </>
      }
    >
      <ol className="small" style={{ paddingLeft: 18, margin: 0, lineHeight: 1.7 }}>
        <li>Copy the prompt (rules + timestamped transcript).</li>
        <li>Paste it into Claude, ChatGPT or any chat you use.</li>
        <li>Paste the chat's answer below. ClipStudio checks the times, snaps them to words and scores each pick.</li>
      </ol>
      {prompt.data ? (
        <div className="row between">
          <span className="small muted">
            {prompt.data.sentences} sentences · {Math.round(prompt.data.chars / 1000)}k characters
          </span>
          <CopyButton text={prompt.data.prompt} label="Copy prompt" />
        </div>
      ) : prompt.error ? (
        <Alert kind="warn">{prompt.error}</Alert>
      ) : (
        <Spinner />
      )}
      <textarea className="textarea code" rows={8} value={answer} onChange={(ev) => setAnswer(ev.target.value)} placeholder='{"clips": [{"start": "1:28.0", "end": "2:01.5", "title": "…"}]}' />
      <label className="check small">
        <input type="checkbox" checked={replace} onChange={(ev) => setReplace(ev.target.checked)} /> Replace the current clips{replaceable ? ` (deletes ${plural(replaceable, "clip")}${approved ? `, ${approved} approved` : ""})` : ""}. Otherwise the picks are added alongside.
      </label>
      <p className="tiny muted">ClipStudio doesn't send anything anywhere. You choose what to paste where.</p>
    </Modal>
  );
}

function RecipeDialog({ project, selected = [], onClose, onDone }: { project: Project; selected?: string[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [id, setId] = useState("");
  const [picked, setPicked] = useState<Recipe | null>(null);
  const [scope, setScope] = useState<"selected" | "unrejected" | "approved" | "all">(selected.length ? "selected" : "unrejected");
  const [render, setRender] = useState(true);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.bulkClips(project.id, scope === "selected" ? { recipe_id: id, ids: selected, render } : { recipe_id: id, scope, render });
      toast(`Recipe applied to ${plural(r.updated, "clip")}${r.rendering ? ", re-rendering" : ""}`);
      onDone();
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Apply a recipe to clips"
      onClose={onClose}
      actions={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" onClick={go} disabled={!id || busy}>
            {busy ? <Spinner /> : <Icon name="zap" />} Apply
          </button>
        </>
      }
    >
      <div className="stack">
        <RecipeSelect value={id} onChange={(v, r) => (setId(v), setPicked(r))} placeholder="Pick a recipe…" />
        {picked && <span className="small muted">{describeRecipe(picked)}</span>}
        <div className="seg">
          {(
            [
              ...(selected.length ? ([["selected", `${selected.length} selected`]] as const) : []),
              ["unrejected", "All except rejected"],
              ["approved", "Approved only"],
              ["all", "Every clip"],
            ] as const
          ).map(([k, l]) => (
            <button key={k} className={scope === k ? "active" : ""} onClick={() => setScope(k)}>
              {l}
            </button>
          ))}
        </div>
        <label className="check">
          <input type="checkbox" checked={render} onChange={(e) => setRender(e.target.checked)} /> Re-render them now
        </label>
        {project.campaign_id && <span className="hint">This project is in a campaign: its format (9:16, resolution) and music/effects rules still apply.</span>}
        <span className="hint">A recipe is a saved copy of one clip's look (captions, framing, editing). Make one in a clip (Framing tab → Editing → Save as recipe) or in Settings → Advanced → Recipes.</span>
      </div>
    </Modal>
  );
}
