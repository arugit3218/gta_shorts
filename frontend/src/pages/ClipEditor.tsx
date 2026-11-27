import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, media } from "../api";
import { DEFAULT_POLISH, HOOK_TEMPLATES, HookStylePicker, PolishControls, RecipeSelect } from "../components/polish";
import { Icon } from "../components/Icon";
import { Alert, Bar, JobTime, Modal, Spinner, attempt, toneOf, useDialogs, useToast } from "../components/ui";
import { PostedControl, postedState } from "../components/posting";
import { ShortcutHelp } from "../components/shortcuts";
import { fmtTime } from "../format";
import { clipOrder, useHotkeys, usePoll } from "../hooks";
import { CheckList, CopyButton, PLATFORM_LABEL, SafeZoneOverlay, Verdict, ZoneLegend, useSafeZones } from "../components/campaign";
import type { AspectRatio, Campaign, CaptionStyle, Clip, ClipWords, Compliance, EndTextSpec, Layout, Platform, Polish, PostedMap, TextLayout, SfxCue, SfxType, Word } from "../types";
import { ScoreBreakdown } from "./ProjectView";

/** R3: 5 tabs instead of 9; each groups the old panels as sections. */
type Tab = "trim" | "text" | "look" | "sound" | "post" | "montage";
const FONTS = ["Montserrat ExtraBold", "Anton", "Bangers", "Arial Black", "Arial", "Impact", "Helvetica Neue", "Futura", "Avenir Next", "Georgia", "Verdana", "Trebuchet MS", "Courier New"];

interface Draft {
  start: number;
  end: number;
  title: string;
  hook: string;
  description: string;
  hashtags: string[];
  aspect_ratio: AspectRatio;
  layout: Layout;
  output_height: 720 | 1080;
  captions: boolean;
  caption_preset: string;
  caption_style: Partial<CaptionStyle>;
  music_volume: number;
  music: string;
  sfx: "auto" | "off" | "custom";
  sfx_cues: SfxCue[];
  blur_burned: "auto" | "off";
  hook_overlay: { enabled: boolean; text: string; duration: number };
  end_text: EndTextSpec;
  logo: boolean;
  disclosure: boolean;
  sound_id: string | null;
  sound_volume: number;
  original_volume: number;
  polish: Polish;
  words: Word[] | null; // edited caption words (absolute seconds) or null = untouched
}

function draftFrom(c: Clip): Draft {
  return {
    start: c.start,
    end: c.end,
    title: c.title ?? "",
    hook: c.hook ?? "",
    description: c.description ?? "",
    hashtags: c.hashtags,
    aspect_ratio: c.edit.aspect_ratio,
    layout: c.edit.layout,
    output_height: c.edit.output_height,
    captions: c.edit.captions,
    caption_preset: c.edit.caption_preset,
    caption_style: c.edit.caption_style ?? {},
    music_volume: c.features?.music_volume ?? c.edit.music_volume ?? 0.25,
    music: c.features?.music ?? "none",
    sfx: c.features?.sfx ?? "off",
    // only custom cues belong to the draft; automatic ones change with every render (and would look like an edit)
    sfx_cues: c.features?.sfx === "custom" ? c.features.sfx_cues : [],
    blur_burned: c.features?.blur_burned ?? "auto",
    hook_overlay: { enabled: c.features?.hook_overlay.enabled ?? false, text: c.features?.hook_overlay.text ?? c.title ?? "", duration: c.features?.hook_overlay.duration ?? 3.5 },
    end_text: {
      enabled: c.features?.end_text.enabled ?? false,
      text: c.features?.end_text.text ?? "",
      duration: c.features?.end_text.duration ?? 2.5,
      style: c.features?.end_text.style ?? "dark",
      look: c.features?.end_text.look ?? "overlay",
    },
    logo: c.features?.logo ?? false,
    disclosure: c.features?.disclosure ?? false,
    sound_id: c.features?.sound_id ?? null,
    sound_volume: c.features?.sound_volume ?? 1,
    original_volume: c.features?.original_volume ?? 0.25,
    polish: { ...DEFAULT_POLISH, ...c.features?.polish },
    words: null,
  };
}

/** Only what the user changed in this clip. Unchanged values aren't stored on the clip, so it keeps following
 * the campaign and the defaults (a later logo upload, title-text or editing change still reaches it). */
function campaignEdit(d: Draft, base: Draft, hasCampaign: boolean) {
  const out: Record<string, unknown> = {};
  const diff = (k: keyof Draft) => JSON.stringify(d[k]) !== JSON.stringify(base[k]);
  if (hasCampaign) for (const k of ["logo", "disclosure", "sound_id", "sound_volume", "original_volume"] as const) if (diff(k)) out[k] = d[k];
  for (const k of ["music", "sfx", "blur_burned"] as const) if (diff(k)) out[k] = d[k];
  if (d.sfx === "custom" && (diff("sfx_cues") || diff("sfx"))) out.sfx_cues = d.sfx_cues;
  const hook: Record<string, unknown> = {};
  for (const k of ["enabled", "text", "duration"] as const) if (d.hook_overlay[k] !== base.hook_overlay[k]) hook[k] = d.hook_overlay[k];
  if (Object.keys(hook).length) out.hook_overlay = hook;
  const end: Record<string, unknown> = {};
  for (const k of ["enabled", "text", "duration", "style", "look"] as const) if (d.end_text[k] !== base.end_text[k]) end[k] = d.end_text[k];
  if (Object.keys(end).length) out.end_text = end;
  const pol: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(d.polish)) if (JSON.stringify(v) !== JSON.stringify((base.polish as unknown as Record<string, unknown>)[k])) pol[k] = v;
  if (Object.keys(pol).length) out.polish = pol;
  return out;
}

export default function ClipEditor() {
  const { id = "" } = useParams();
  // a fresh editor per clip, so Previous / Next never carry one clip's draft into another
  return <ClipEditorInner key={id} id={id} />;
}

function EdSection({ title, hint, collapsed, children }: { title: string; hint?: string; collapsed?: boolean; children: React.ReactNode }) {
  return (
    <details className="ed-sec" open={!collapsed}>
      <summary>
        <span className="ed-title">{title}</span>
        {hint && <span className="small muted">{hint}</span>}
      </summary>
      <div className="ed-body">{children}</div>
    </details>
  );
}

const EDITOR_KEYS: [string, string][] = [
  ["mod+s", "Save"],
  ["mod+enter", "Save & render"],
  ["space", "Play / pause"],
  ["j / k", "Next / previous clip"],
  ["a", "Approve (again to undo)"],
  ["r", "Reject (again to undo)"],
  ["?", "Show shortcuts"],
];

function ClipEditorInner({ id }: { id: string }) {
  const toast = useToast();
  const { confirm: confirmDlg } = useDialogs();
  const nav = useNavigate();
  const [help, setHelp] = useState(false);
  const clipQ = usePoll(() => api.clip(id), [id]);
  const wordsQ = usePoll(() => api.clipWords(id), [id]);
  const presets = usePoll(api.captionPresets, []).data;
  const clip = clipQ.data;
  const projectQ = usePoll(() => (clip ? api.project(clip.project_id) : Promise.resolve(null)), [clip?.project_id]);
  const campaignId = projectQ.data?.campaign_id;
  const campaignQ = usePoll(() => (campaignId ? api.campaign(campaignId) : Promise.resolve(null)), [campaignId]);
  const campaign = campaignQ.data;
  const zones = useSafeZones();
  const [showZones, setShowZones] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [params] = useSearchParams();
  const [tab, setTab] = useState<Tab | null>((["trim", "text", "look", "sound", "post", "montage"] as Tab[]).find((t) => t === params.get("tab")) ?? null);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const rendering = clip && ["queued", "rendering"].includes(clip.render_status);
  const jobs = usePoll(() => (clip ? api.jobs({ project_id: clip.project_id, active: true }) : Promise.resolve([])), [clip?.project_id], rendering ? 1000 : null);
  const renderJob = jobs.data?.find((j) => j.payload.clip_id === id);

  useEffect(() => {
    if (clip && !draft) setDraft(draftFrom(clip));
  }, [clip, draft]);
  const isMontage = clip?.analysis.mode === "montage";
  const activeTab: Tab = tab ?? (isMontage ? "montage" : "trim");

  // Poll the clip while it renders, then refresh the player.
  useEffect(() => {
    if (!rendering) return;
    const t = window.setInterval(clipQ.reload, 1200);
    return () => window.clearInterval(t);
  }, [rendering, clipQ.reload]);

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  const setStyle = (k: keyof CaptionStyle, v: unknown) => setDraft((d) => (d ? { ...d, caption_style: { ...d.caption_style, [k]: v } } : d));

  const baseStyle: CaptionStyle | null = presets && draft ? presets[draft.caption_preset] ?? null : null;
  const style: CaptionStyle | null = baseStyle && draft ? { ...baseStyle, ...draft.caption_style } : null;

  const dirty = useMemo(() => {
    if (!clip || !draft) return false;
    const o = draftFrom(clip);
    return JSON.stringify({ ...o, words: null }) !== JSON.stringify({ ...draft, words: null }) || draft.words !== null;
  }, [clip, draft]);

  // ---- unsaved changes: block in-app navigation and warn on tab close
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // ---- previous / next clip, in the order the project page showed them
  const siblings = usePoll(() => (clip ? api.clips(clip.project_id) : Promise.resolve([] as Clip[])), [clip?.project_id]).data;
  const order = useMemo(() => {
    if (!clip || !siblings) return [] as string[];
    const ids = new Set(siblings.map((c) => c.id));
    const saved = (clipOrder.load(clip.project_id) ?? []).filter((x) => ids.has(x));
    const rest = [...siblings].sort((a, b) => a.rank - b.rank).map((c) => c.id).filter((x) => !saved.includes(x));
    return [...saved, ...rest];
  }, [clip, siblings]);
  const pos = order.indexOf(id);
  const prevId = pos > 0 ? order[pos - 1] : null;
  const nextId = pos >= 0 && pos < order.length - 1 ? order[pos + 1] : null;

  const setStatus = async (status: "approved" | "rejected" | "candidate") => {
    if (!clip) return;
    const before = clip.status;
    const next = clip.status === status ? "candidate" : status;
    try {
      const c = await api.patchClip(id, { status: next });
      clipQ.setData(c);
      toast(next === "approved" ? "Approved" : next === "rejected" ? "Rejected" : "Marked as not reviewed", "info", {
        action: { label: "Undo", onClick: async () => clipQ.setData(await api.patchClip(id, { status: before })) },
      });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const tabQs = tab ? `?tab=${tab}` : "";
  const latest = useRef<{ save: (r: boolean) => Promise<void> | void; dirty: boolean; saving: boolean; rendering: boolean } | null>(null);
  const commitTyping = () => {
    const el = document.activeElement as HTMLElement | null;
    if (el && ["INPUT", "TEXTAREA"].includes(el.tagName)) el.blur();
  };
  useHotkeys({
    "mod+s": () => {
      commitTyping();
      setTimeout(() => latest.current && latest.current.dirty && !latest.current.saving && latest.current.save(false), 0);
    },
    "mod+enter": () => {
      commitTyping();
      setTimeout(() => latest.current && !latest.current.saving && !latest.current.rendering && latest.current.save(true), 0);
    },
    j: () => nextId && nav(`/clips/${nextId}${tabQs}`),
    k: () => prevId && nav(`/clips/${prevId}${tabQs}`),
    a: () => setStatus("approved"),
    r: () => setStatus("rejected"),
    space: () => {
      const v = document.querySelector<HTMLVideoElement>(".editor-preview video");
      if (v) v.paused ? v.play() : v.pause();
    },
    "?": () => setHelp(true),
  });

  const buildPatch = useCallback(() => {
    if (!draft || !clip) return {};
    const styleOverrides = Object.keys(draft.caption_style).length ? draft.caption_style : undefined;
    return {
      title: draft.title,
      hook: draft.hook,
      description: draft.description,
      hashtags: draft.hashtags,
      ...(draft.start !== clip.start ? { start: draft.start } : {}),
      ...(draft.end !== clip.end ? { end: draft.end } : {}),
      edit: {
        aspect_ratio: draft.aspect_ratio,
        layout: draft.layout,
        output_height: draft.output_height,
        captions: draft.captions,
        caption_preset: draft.caption_preset,
        ...(styleOverrides ? { caption_style: styleOverrides } : {}),
        music_volume: draft.music_volume,
        ...campaignEdit(draft, draftFrom(clip), !!campaign),
      },
      ...(draft.words ? { caption_words: draft.words } : {}),
    };
  }, [draft, clip, campaign]);

  const save = async (andRender: boolean) => {
    if (!draft) return;
    setSaving(true);
    try {
      const c = await api.patchClip(id, buildPatch());
      clipQ.setData(c);
      setDraft(draftFrom(c));
      if (andRender) {
        await api.renderClip(id);
        clipQ.setData({ ...c, render_status: "queued" });
        toast("Saved. Re-rendering…");
      } else toast("Saved");
      wordsQ.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };
  latest.current = { save, dirty, saving, rendering: !!rendering };

  const makePreview = async (t?: number) => {
    if (!draft || !clip) return;
    setPreviewBusy(true);
    try {
      const blob = await api.previewFrame(id, {
        t: t ?? Math.min(2, (draft.end - draft.start) / 3),
        edit: {
          aspect_ratio: draft.aspect_ratio,
          // face tracking needs a full pass; the still preview uses a static crop (stream/auto use the saved or last-detected webcam box)
          layout: draft.layout === "split" ? "center" : draft.layout,
          output_height: draft.output_height,
          captions: draft.captions,
          caption_preset: draft.caption_preset,
          ...(Object.keys(draft.caption_style).length ? { caption_style: draft.caption_style } : {}),
          ...campaignEdit(draft, draftFrom(clip), !!campaign),
        },
        caption_words: draft.words ?? undefined,
      });
      setPreview((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(blob);
      });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setPreviewBusy(false);
    }
  };

  if (clipQ.error && !clip) return <Alert kind="danger" title="Clip not found">{clipQ.error}</Alert>;
  if (!clip || !draft) return <div className="skeleton" style={{ height: 300 }} />;

  const tone = toneOf(clip.score, clip.analysis.label);
  const arClass = draft.aspect_ratio === "1:1" ? "ar-11" : draft.aspect_ratio === "16:9" ? "ar-169" : "";

  return (
    <div className="stack lg">
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <Link to={`/projects/${clip.project_id}`} className="small muted row" style={{ gap: 4, marginBottom: 6 }}>
            <Icon name="chevronLeft" size={14} /> Back to clips
          </Link>
          <div className="row" style={{ gap: 10 }}>
            <h1 className="ellipsis">Edit clip #{clip.rank}</h1>
            <span className={`badge ${tone}`}>
              {Math.round(clip.score)} · {clip.analysis.label}
            </span>
          </div>
        </div>
        <div className="row wrap" style={{ justifyContent: "flex-end" }}>
          <span className="row" style={{ gap: 4 }}>
            <button className="btn sm icon" disabled={!prevId} onClick={() => prevId && nav(`/clips/${prevId}${tabQs}`)} title="Previous clip (K)" aria-label="Previous clip">
              <Icon name="chevronLeft" />
            </button>
            <span className="small muted nowrap">{pos >= 0 ? `${pos + 1} / ${order.length}` : ""}</span>
            <button className="btn sm icon" disabled={!nextId} onClick={() => nextId && nav(`/clips/${nextId}${tabQs}`)} title="Next clip (J)" aria-label="Next clip">
              <Icon name="chevronRight" />
            </button>
          </span>
          <span className="row" style={{ gap: 4 }}>
            <button className={`btn sm ${clip.status === "approved" ? "on approve" : ""}`} onClick={() => setStatus("approved")} title="Approve (A)" aria-pressed={clip.status === "approved"}>
              <Icon name="check" /> {clip.status === "approved" ? "Approved" : "Approve"}
            </button>
            <button className={`btn sm ${clip.status === "rejected" ? "on reject" : ""}`} onClick={() => setStatus("rejected")} title="Reject (R)" aria-pressed={clip.status === "rejected"}>
              <Icon name="x" /> {clip.status === "rejected" ? "Rejected" : "Reject"}
            </button>
            {clip.status === "approved" && clip.has_video && activeTab !== "post" && (
              <button className="btn sm primary" onClick={() => setTab("post")} title="Check against the campaign, copy the caption, download and post">
                <Icon name="check" /> Post
              </button>
            )}
          </span>
          <button className="btn sm ghost icon" onClick={() => setHelp(true)} title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts">
            ?
          </button>
          <span className="divider-v" />
          {dirty && <span className="badge warn">Unsaved changes</span>}
          <button className="btn" disabled={!dirty || saving} onClick={() => setDraft(draftFrom(clip))}>
            Discard
          </button>
          <button className="btn" disabled={!dirty || saving} onClick={() => save(false)} title="Save (⌘S)">
            Save
          </button>
          <button className="btn primary" disabled={saving || !!rendering} onClick={() => save(true)} title="Save & render (⌘↵)">
            {saving || rendering ? <Spinner /> : <Icon name="refresh" />} {dirty ? "Save & render" : clip.has_video ? "Re-render" : "Render"}
          </button>
        </div>
      </div>

      <div className="editor">
        <div className="editor-preview stack">
          <div className={`card clip-media preview-wrap ${arClass}`} style={{ overflow: "hidden", maxHeight: 640 }}>
            {showZones && draft.aspect_ratio === "9:16" && <SafeZoneOverlay zones={zones} platforms={(campaign?.brief.platforms.length ? campaign.brief.platforms : ["reels", "shorts"]) as Platform[]} />}
            {preview ? (
              <img src={preview} alt="Caption preview frame" />
            ) : clip.has_video ? (
              <video key={clip.version} src={media.video(clip)} controls playsInline poster={clip.has_thumb ? media.thumb(clip) : undefined} />
            ) : (
              <div className="overlay">
                <span className="small">Not rendered yet. Click Render, or Preview a frame.</span>
              </div>
            )}
            {rendering && (
              <div className="overlay">
                <div className="stack sm" style={{ alignItems: "center", width: "70%" }}>
                  <Spinner size={22} />
                  <span className="small">{clip.render_status === "queued" ? "Waiting for the current job…" : "Rendering…"}</span>
                  {renderJob && <Bar value={renderJob.progress} />}
                  {renderJob && <JobTime job={renderJob} />}
                </div>
              </div>
            )}
          </div>
          <div className="row wrap preview-tools">
            <button className="btn sm" onClick={() => makePreview()} disabled={previewBusy}>
              {previewBusy ? <Spinner /> : <Icon name="type" />} Preview exact frame
            </button>
            {preview && (
              <button className="btn sm ghost" onClick={() => setPreview(null)}>
                Show video
              </button>
            )}
            {!isMontage && (
              <>
                <button className="btn sm" onClick={() => setTab("look")} title="Aspect ratio and how the video fills the frame">
                  <Icon name="crop" /> Framing
                </button>
                <button className="btn sm" onClick={() => setTab("text")} title="Caption words and style">
                  <Icon name="type" /> Captions
                </button>
              </>
            )}
            {draft.aspect_ratio === "9:16" && (
              <button className={`btn sm ${showZones ? "on" : ""}`} onClick={() => setShowZones(!showZones)} title="Show where Reels/Shorts UI covers the video">
                <Icon name="layers" /> Safe zones
              </button>
            )}
            <span className="spacer" />
            {clip.has_video && (
              <a className="btn sm" href={media.download(clip)}>
                <Icon name="download" /> MP4
              </a>
            )}
          </div>
          {showZones && <ZoneLegend />}
          {clip.render_status === "stale" && <Alert kind="warn">The video doesn't include your latest edits yet. Re-render to update it.</Alert>}
          {clip.render_status === "failed" && <Alert kind="danger" title="Render failed">{clip.render_error}</Alert>}
          {clip.analysis.layout_used && (
            <p className="tiny muted">
              Last render: {clip.analysis.output ? `${clip.analysis.output} · ` : ""}
              {clip.analysis.layout_used} framing{clip.analysis.face_ratio ? ` · faces in ${Math.round(clip.analysis.face_ratio * 100)}% of frames` : ""}
              {clip.analysis.burned_text ? " · burned-in captions blurred" : ""}
              {clip.analysis.music_used ? ` · music: ${clip.analysis.music_used}` : ""}
              {clip.analysis.sfx_used?.length ? ` · ${clip.analysis.sfx_used.length} sound effect(s)` : ""}
              {clip.analysis.polish_used?.cuts ? ` · ${clip.analysis.polish_used.cuts} jump cut(s), ${clip.analysis.polish_used.removed}s of dead air removed` : ""}
              {clip.analysis.polish_used?.zooms ? ` · ${clip.analysis.polish_used.zooms} zoom(s)` : ""}
              {clip.analysis.polish_used?.cold_open ? " · cold open" : ""}
              {clip.analysis.output_duration ? ` · ${clip.analysis.output_duration.toFixed(1)}s long` : ""}
            </p>
          )}
        </div>

        <div className="card">
          <div className="card-body">
            <div className="tabs" role="tablist" aria-label="Edit">
              {(
                (isMontage
                  ? [
                      ["montage", "Montage", "music"],
                      ["text", "Text & logo", "type"],
                      ["post", "Post", "clipboard"],
                    ]
                  : [
                      ["trim", "Trim", "scissors"],
                      ["text", "Captions", "type"],
                      ["look", "Framing", "crop"],
                      ["sound", "Sound", "music"],
                      ["post", "Post", "clipboard"],
                    ]) as [Tab, string, "music" | "clipboard" | "scissors" | "type" | "crop"][]
              ).map(([k, l, icon]) => (
                <button key={k} role="tab" aria-selected={activeTab === k} className={activeTab === k ? "active" : ""} onClick={() => setTab(k)}>
                  <span className="row" style={{ gap: 6 }}>
                    <Icon name={icon} size={14} />
                    {l}
                  </span>
                </button>
              ))}
            </div>

            {activeTab === "montage" && <MontagePanel clip={clip} onClip={(c) => (clipQ.setData(c), setDraft((d) => (d && dirty ? d : draftFrom(c))))} />}
            {activeTab === "trim" && (wordsQ.data ? <TrimPanel clip={clip} words={wordsQ.data} draft={draft} set={set} /> : <Spinner />)}
            {activeTab === "text" && (
              <div className="stack lg">
                {!isMontage && (
                  <EdSection title="Captions" hint="The words on screen. Fix any mistakes here.">
                    {wordsQ.data ? (
                      <CaptionsPanel
                        words={wordsQ.data}
                        draft={draft}
                        set={set}
                        onReset={async () => {
                          if (!(await confirmDlg({ title: "Reset the captions?", body: "Every word you fixed by hand in this clip goes back to the original transcript.", confirm: "Reset captions", danger: true }))) return;
                          await attempt(toast, async () => {
                            const c = await api.patchClip(id, { reset_captions: true });
                            clipQ.setData(c);
                            set("words", null);
                            wordsQ.reload();
                            toast("Captions reset to the transcript");
                          });
                        }}
                      />
                    ) : (
                      <Spinner />
                    )}
                  </EdSection>
                )}
                {!isMontage && presets && style && (
                  <EdSection title="Caption style" hint="Ready-made looks, or change font, colours, size, position and animation.">
                    <StylePanel presets={presets} draft={draft} style={style} set={set} setStyle={setStyle} onPreview={() => makePreview()} previewBusy={previewBusy} layout={clip.features?.text_layout} />
                  </EdSection>
                )}
                <EdSection title="Title, hook and hashtags" hint="The title text at the start, and the words used in the post caption.">
                  <TextPanel clip={clip} draft={draft} set={set} />
                </EdSection>
                {isMontage && (
                  <EdSection title="On-screen extras" hint="Logo, #ad and other overlays.">
                    <OverlaysPanel clip={clip} draft={draft} set={set} campaign={campaign ?? null} />
                  </EdSection>
                )}
              </div>
            )}
            {activeTab === "look" && (
              <div className="stack lg">
                <EdSection title="Framing" hint="Shape of the video and how the source fills it.">
                  <FormatPanel draft={draft} set={set} />
                </EdSection>
                <EdSection title="Editing" hint="Dead-air cuts, zooms, emphasis, progress bar.">
                  <PolishPanel clip={clip} draft={draft} set={set} dirty={dirty} onClip={(c) => (clipQ.setData(c), setDraft(draftFrom(c)))} />
                </EdSection>
                <EdSection title="On-screen extras" hint="Title text style, logo, #ad, blurring the source's own captions.">
                  <OverlaysPanel clip={clip} draft={draft} set={set} campaign={campaign ?? null} />
                </EdSection>
              </div>
            )}
            {activeTab === "sound" && <AudioPanel clip={clip} draft={draft} set={set} campaign={campaign ?? null} onClip={(c) => (clipQ.setData(c), setDraft((d) => (d ? { ...d, music: draftFrom(c).music, music_volume: draftFrom(c).music_volume } : draftFrom(c))))} />}
            {activeTab === "post" && <CampaignPanel clip={clip} dirty={dirty} campaign={campaign ?? null} onClip={() => clipQ.reload()} />}
          </div>
        </div>
      </div>
      {help && <ShortcutHelp items={EDITOR_KEYS} onClose={() => setHelp(false)} />}
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
              <button
                className="btn primary"
                onClick={async () => {
                  try {
                    await api.patchClip(id, buildPatch());
                    blocker.proceed();
                  } catch (e) {
                    toast((e as Error).message, "error");
                    blocker.reset();
                  }
                }}
              >
                Save &amp; leave
              </button>
            </>
          }
        >
          <p className="small">This clip has edits that aren't saved yet. Saving doesn't re-render; the clip is marked for re-render.</p>
        </Modal>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------------- trim
function TrimPanel({ clip, words, draft, set }: { clip: Clip; words: ClipWords; draft: Draft; set: <K extends keyof Draft>(k: K, v: Draft[K]) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const ctx = words.context_words;
  const [rlo, rhi] = words.source_range ?? [0, words.source_duration];
  const offset = words.source_offset ?? 0;
  const lo = Math.max(rlo ?? 0, Math.min(clip.start, draft.start) - 15);
  const hi = Math.min(rhi || words.source_duration || draft.end + 15, Math.max(clip.end, draft.end) + 15);
  const span = hi - lo;
  const x = (t: number) => ((t - lo) / span) * 100;

  const snap = (t: number, edge: "start" | "end") => {
    if (!ctx.length) return t;
    if (edge === "start") {
      const w = ctx.reduce((b, w) => (Math.abs(w.s - t) < Math.abs(b.s - t) ? w : b), ctx[0]);
      return Math.max(0, +(w.s - 0.12).toFixed(3));
    }
    const w = ctx.reduce((b, w) => (Math.abs(w.e - t) < Math.abs(b.e - t) ? w : b), ctx[0]);
    return +(w.e + 0.3).toFixed(3);
  };

  const drag = (edge: "start" | "end") => (e: React.PointerEvent) => {
    e.preventDefault();
    const el = ref.current!;
    const rect = el.getBoundingClientRect();
    const move = (ev: PointerEvent) => {
      const t = lo + ((ev.clientX - rect.left) / rect.width) * span;
      const v = snap(Math.max(lo, Math.min(hi, t)), edge);
      if (edge === "start" && v < draft.end - 3) set("start", v);
      if (edge === "end" && v > draft.start + 3) set("end", v);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const sentenceStarts = new Set(clip.analysis.sentences.map((s) => s.start));
  const inSel = ctx.filter((w) => w.s >= draft.start - 0.01 && w.e <= draft.end + 0.01);

  const playSel = () => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.max(0, draft.start - offset);
    v.play();
  };

  return (
    <div className="stack lg">
      <div className="stack sm">
        <div className="row between">
          <span className="label-text">Drag the handles to trim (snaps to word boundaries)</span>
          <span className="small muted">
            {fmtTime(draft.start, true)} → {fmtTime(draft.end, true)} · <strong>{(draft.end - draft.start).toFixed(1)}s</strong>
          </span>
        </div>
        <div className="timeline" ref={ref}>
          {ctx.map((w, i) => (
            <div key={i} className={`word-tick ${sentenceStarts.has(w.s) ? "sentence" : ""}`} style={{ left: `${x(w.s)}%` }} title={w.w} />
          ))}
          <div className="sel" style={{ left: `${x(draft.start)}%`, width: `${x(draft.end) - x(draft.start)}%` }} />
          <div className="handle" style={{ left: `${x(draft.start)}%` }} onPointerDown={drag("start")} role="slider" aria-label="Clip start" aria-valuenow={draft.start} />
          <div className="handle" style={{ left: `${x(draft.end)}%` }} onPointerDown={drag("end")} role="slider" aria-label="Clip end" aria-valuenow={draft.end} />
          {playhead != null && playhead >= lo && playhead <= hi && <div className="playhead" style={{ left: `${x(playhead)}%` }} />}
        </div>
        <div className="row between tiny muted">
          <span>{fmtTime(lo)}</span>
          <span>{fmtTime(hi)}</span>
        </div>
      </div>
      <div className="grid cols-2">
        <div className="field">
          <label>Start (seconds)</label>
          <input className="input" type="number" step="0.1" value={draft.start} onChange={(e) => set("start", Math.max(0, +e.target.value))} />
        </div>
        <div className="field">
          <label>End (seconds)</label>
          <input className="input" type="number" step="0.1" value={draft.end} onChange={(e) => set("end", +e.target.value)} />
        </div>
      </div>
      <div className="stack sm">
        <div className="row between">
          <span className="label-text">Source preview{offset > 0 ? " (downloaded section)" : ""}</span>
          <button className="btn sm" onClick={playSel}>
            <Icon name="play" /> Play selection
          </button>
        </div>
        <video
          ref={video}
          src={media.clipSource(clip.id)}
          controls
          preload="metadata"
          style={{ width: "100%", borderRadius: 8, background: "#000", maxHeight: 260 }}
          onTimeUpdate={(e) => {
            const t = e.currentTarget.currentTime + offset;
            setPlayhead(t);
            if (t > draft.end && !e.currentTarget.paused && t < draft.end + 0.5) e.currentTarget.pause();
          }}
        />
      </div>
      <div className="stack sm">
        <span className="label-text">Transcript of the selection</span>
        <p className="small" style={{ lineHeight: 1.6 }}>{inSel.map((w) => w.w).join(" ")}</p>
      </div>
      <details>
        <summary className="small muted" style={{ cursor: "pointer" }}>Score breakdown (from the original selection)</summary>
        <div style={{ marginTop: 10 }}>
          <ScoreBreakdown clip={clip} />
        </div>
      </details>
    </div>
  );
}

// -------------------------------------------------------------------------------- captions
const LINE_WORDS = 7;

function CaptionsPanel({ words, draft, set, onReset }: { words: ClipWords; draft: Draft; set: <K extends keyof Draft>(k: K, v: Draft[K]) => void; onReset: () => void }) {
  const base = draft.words ?? words.words;
  const lines = useMemo(() => {
    const out: Word[][] = [];
    let cur: Word[] = [];
    base.forEach((w, i) => {
      cur.push(w);
      const gap = i + 1 < base.length ? base[i + 1].s - w.e : 0;
      if (cur.length >= LINE_WORDS || /[.!?]$/.test(w.w) || gap > 0.8) {
        out.push(cur);
        cur = [];
      }
    });
    if (cur.length) out.push(cur);
    return out;
  }, [base]);

  // Editing a line keeps its time span; words are re-timed proportionally to their length.
  const editLine = (idx: number, text: string) => {
    const line = lines[idx];
    const s = line[0].s;
    const e = line[line.length - 1].e;
    const toks = text.split(/\s+/).filter(Boolean);
    let newLine: Word[];
    if (toks.length === line.length) newLine = line.map((w, i) => ({ ...w, w: toks[i] }));
    else {
      const total = toks.reduce((a, t) => a + t.length + 1, 0) || 1;
      let t = s;
      newLine = toks.map((tok) => {
        const d = ((tok.length + 1) / total) * (e - s);
        const w = { w: tok, s: +t.toFixed(3), e: +(t + d * 0.92).toFixed(3) };
        t += d;
        return w;
      });
    }
    const next = lines.map((l, i) => (i === idx ? newLine : l)).flat();
    set("words", next);
  };

  return (
    <div className="stack">
      <div className="row between">
        <p className="small muted">Fix transcription mistakes here. Timing is kept for each line.</p>
        <div className="row">
          {(draft.words || words.edited) && (
            <button className="btn sm" onClick={onReset}>
              Reset to transcript
            </button>
          )}
        </div>
      </div>
      <label className="check">
        <input type="checkbox" checked={draft.captions} onChange={(e) => set("captions", e.target.checked)} />
        <span>Burn captions into the video</span>
      </label>
      <div className="stack sm">
        {lines.map((l, i) => (
          <div className="caption-line" key={`${i}-${l[0].s}-${l.map((w) => w.w).join(" ")}`}>
            <span className="mono muted">{fmtTime(l[0].s - draft.start, true)}</span>
            <input className="input" defaultValue={l.map((w) => w.w).join(" ")} onBlur={(e) => e.target.value !== l.map((w) => w.w).join(" ") && editLine(i, e.target.value)} />
          </div>
        ))}
        {lines.length === 0 && <p className="muted">No words in this range.</p>}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------- style
function PresetSample({ p }: { p: CaptionStyle }) {
  const words = p.uppercase ? ["THIS", "IS", "IT"] : ["This", "is", "it"];
  const hi = ["highlight", "pop", "karaoke"].includes(p.animation);
  return (
    <span
      className="sample"
      style={{
        fontFamily: p.font,
        fontWeight: p.bold ? 800 : 400,
        color: p.color,
        WebkitTextStroke: p.outline ? `1px ${p.outline_color}` : undefined,
        textShadow: p.shadow ? "0 2px 4px rgba(0,0,0,.7)" : undefined,
        background: p.background ? p.background_color + "cc" : undefined,
        padding: p.background ? "2px 6px" : undefined,
        borderRadius: 4,
      }}
    >
      {words.map((w, i) => (
        <span key={i} style={{ color: hi && i === 1 ? p.highlight_color : undefined }}>
          {w}{" "}
        </span>
      ))}
    </span>
  );
}

/** Does the caption block at ``y`` overlap the logo's rows (from the last save's layout)? */
function onLogo(y: number, style: CaptionStyle, layout?: TextLayout): boolean {
  const band = layout?.logo_band;
  if (!band) return false;
  const h = style.max_lines * style.size * 1.2 + 2 * style.outline;
  return Math.min(y + h / 2, band[1]) - Math.max(y - h / 2, band[0]) > 0;
}

/** Caption height: automatic (the campaign's spot or the look's own, kept clear of the logo) or set for this clip. */
function CaptionPositionField({ draft, style, set, setStyle, layout }: { draft: Draft; style: CaptionStyle; set: SetDraft; setStyle: (k: keyof CaptionStyle, v: unknown) => void; layout?: TextLayout }) {
  const own = draft.caption_style.position != null;
  // automatic: what the last save worked out (preset changes show after saving / in Preview frame)
  const autoPos = layout && layout.caption_source !== "clip" ? layout.caption_position : style.position;
  const value = own ? style.position : autoPos;
  const why = !layout ? "" : layout.caption_moved ? "moved clear of the logo" : layout.caption_source === "campaign" ? "the campaign's position" : "the caption look's spot";
  return (
    <div className="field">
      <div className="row between" style={{ gap: 6 }}>
        <label htmlFor="cap-y">
          Vertical position: {Math.round(value * 100)}%{!own && why ? <span className="muted"> · automatic ({why})</span> : null}
        </label>
        {own && (
          <button
            className="link-btn small"
            title="Follow the campaign / caption look again, kept clear of the logo"
            onClick={() => {
              const { position: _p, ...rest } = draft.caption_style;
              set("caption_style", rest);
            }}
          >
            Automatic
          </button>
        )}
      </div>
      <input id="cap-y" type="range" min={0.1} max={0.92} step={0.01} value={value} onChange={(e) => setStyle("position", +e.target.value)} />
      {own && onLogo(value, style, layout) && <span className="small" style={{ color: "var(--warn)" }}>The logo covers part of the captions here. Move them, or press Automatic.</span>}
    </div>
  );
}

function StylePanel({
  presets,
  draft,
  style,
  set,
  setStyle,
  onPreview,
  previewBusy,
  layout,
}: {
  layout?: TextLayout;
  presets: Record<string, CaptionStyle>;
  draft: Draft;
  style: CaptionStyle;
  set: <K extends keyof Draft>(k: K, v: Draft[K]) => void;
  setStyle: (k: keyof CaptionStyle, v: unknown) => void;
  onPreview: () => void;
  previewBusy: boolean;
}) {
  return (
    <div className="stack lg">
      <div className="preset-grid">
        {Object.entries(presets).map(([k, p]) => (
          <button
            key={k}
            className={`preset ${draft.caption_preset === k ? "active" : ""}`}
            onClick={() => {
              set("caption_preset", k);
              set("caption_style", {});
            }}
          >
            <PresetSample p={p} />
            <span className="pname">{p.name}</span>
          </button>
        ))}
      </div>
      <div className="row between">
        <h3>Customise</h3>
        <div className="row">
          {Object.keys(draft.caption_style).length > 0 && (
            <button className="btn sm ghost" onClick={() => set("caption_style", {})}>
              Reset to preset
            </button>
          )}
          <button className="btn sm" onClick={onPreview} disabled={previewBusy}>
            {previewBusy ? <Spinner /> : <Icon name="type" />} Preview frame
          </button>
        </div>
      </div>
      <div className="grid cols-2">
        <div className="field">
          <label>Font</label>
          <select className="select" value={style.font} onChange={(e) => setStyle("font", e.target.value)}>
            {[...new Set([style.font, ...FONTS])].map((f) => (
              <option key={f} value={f} style={{ fontFamily: f }}>
                {f}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Animation</label>
          <select className="select" value={style.animation} onChange={(e) => setStyle("animation", e.target.value)}>
            <option value="none">None (static lines)</option>
            <option value="highlight">Highlight current word</option>
            <option value="pop">Pop-in + highlight</option>
            <option value="karaoke">Karaoke fill</option>
            <option value="fade">Fade (cinematic)</option>
          </select>
        </div>
        <div className="field">
          <label>Size: {Math.round(style.size * 1000) / 10}% of height</label>
          <input type="range" min={0.025} max={0.09} step={0.002} value={style.size} onChange={(e) => setStyle("size", +e.target.value)} />
        </div>
        <CaptionPositionField draft={draft} style={style} set={set} setStyle={setStyle} layout={layout} />
        <div className="field">
          <label>Max words per caption: {style.max_words}</label>
          <input type="range" min={1} max={10} value={style.max_words} onChange={(e) => setStyle("max_words", +e.target.value)} />
        </div>
        <div className="field">
          <label>Outline: {Math.round(style.outline * 1000) / 10}</label>
          <input type="range" min={0} max={0.015} step={0.001} value={style.outline} onChange={(e) => setStyle("outline", +e.target.value)} />
        </div>
      </div>
      <div className="row wrap" style={{ gap: 18 }}>
        {(
          [
            ["color", "Text"],
            ["highlight_color", "Highlight"],
            ["outline_color", "Outline"],
          ] as const
        ).map(([k, l]) => (
          <label key={k} className="row small" style={{ gap: 8 }}>
            <input type="color" value={style[k]} onChange={(e) => setStyle(k, e.target.value.toUpperCase())} /> {l}
          </label>
        ))}
        <label className="row small" style={{ gap: 8 }} title="Colour of emphasised keywords (Framing tab → Editing → Keyword emphasis)">
          <input type="color" value={style.emphasis_color ?? "#2BE04F"} onChange={(e) => setStyle("emphasis_color", e.target.value.toUpperCase())} /> Emphasis
        </label>
        <label className="check small">
          <input type="checkbox" checked={style.uppercase} onChange={(e) => setStyle("uppercase", e.target.checked)} /> UPPERCASE
        </label>
        <label className="check small">
          <input type="checkbox" checked={style.bold} onChange={(e) => setStyle("bold", e.target.checked)} /> Bold
        </label>
        <label className="check small">
          <input type="checkbox" checked={style.shadow > 0} onChange={(e) => setStyle("shadow", e.target.checked ? 0.004 : 0)} /> Shadow
        </label>
        <label className="check small">
          <input type="checkbox" checked={style.background} onChange={(e) => setStyle("background", e.target.checked)} /> Background box
        </label>
        <label className="check small">
          <input type="checkbox" checked={!!style.glow} onChange={(e) => setStyle("glow", e.target.checked)} /> Glow
        </label>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------- format
function FormatPanel({ draft, set }: { draft: Draft; set: <K extends keyof Draft>(k: K, v: Draft[K]) => void }) {
  return (
    <div className="stack lg">
      <div className="field">
        <span className="label-text">Aspect ratio</span>
        <div className="seg">
          {(
            [
              ["9:16", "9:16 Vertical"],
              ["1:1", "1:1 Square"],
              ["16:9", "16:9 Landscape"],
            ] as const
          ).map(([k, l]) => (
            <button key={k} className={draft.aspect_ratio === k ? "active" : ""} onClick={() => set("aspect_ratio", k)}>
              {l}
            </button>
          ))}
        </div>
      </div>
      <div className="field">
        <span className="label-text">Framing</span>
        <div className="stack sm">
          {(
            [
              ["auto", "Auto (recommended)", "Follows a big face; uses the stream layout when there's a webcam overlay; otherwise fills the frame. No blurred bars (change in Settings)."],
              ["center", "Fill (crop)", "Fills the whole 9:16 frame from the centre. Immersive; the sides of a wide video are cut."],
              ["stream", "Stream: webcam + gameplay", "Webcam on top, gameplay filling the rest. Set the webcam box once with Framing on the project page (or Auto-detect)."],
              ["blur", "Blurred fill", "Whole frame visible, with blurred bars. Use when nothing may be cropped."],
              ["split", "Split screen", "Two people stacked top and bottom. Needs two faces visible; otherwise falls back to tracking."],
            ] as const
          ).map(([k, l, d]) => (
            <label key={k} className={`hook-option ${draft.layout === k ? "active" : ""}`}>
              <input type="radio" name="layout" checked={draft.layout === k} onChange={() => set("layout", k)} />
              <span>
                <strong>{l}</strong>
                <br />
                <span className="small muted">{d}</span>
              </span>
            </label>
          ))}
        </div>
      </div>
      <div className="field">
        <span className="label-text">Resolution</span>
        <div className="seg">
          {([720, 1080] as const).map((h) => (
            <button key={h} className={draft.output_height === h ? "active" : ""} onClick={() => set("output_height", h)}>
              {h}p
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------- text
function TextPanel({ clip, draft, set }: { clip: Clip; draft: Draft; set: <K extends keyof Draft>(k: K, v: Draft[K]) => void }) {
  const [tag, setTag] = useState("");
  const copy = (text: string) => navigator.clipboard?.writeText(text);
  return (
    <div className="stack lg">
      <div className="field">
        <label htmlFor="title">Title</label>
        <input id="title" className="input" value={draft.title} maxLength={200} onChange={(e) => set("title", e.target.value)} />
      </div>
      <div className="field">
        <span className="label-text">Opening hook</span>
        <div className="stack sm">
          {clip.hooks.map((h) => (
            <label key={h} className={`hook-option ${draft.hook === h ? "active" : ""}`}>
              <input type="radio" name="hook" checked={draft.hook === h} onChange={() => set("hook", h)} />
              <span>{h}</span>
            </label>
          ))}
          <input className="input" value={draft.hook} maxLength={300} onChange={(e) => set("hook", e.target.value)} placeholder="Or write your own" />
        </div>
        <span className="hint">{clip.analysis.llm ? "Suggested by the local AI from this clip's transcript." : "Taken word-for-word from the speaker, so nothing is invented."}</span>
      </div>
      <div className="field">
        <div className="row between">
          <label htmlFor="desc">Description</label>
          <button className="btn ghost sm" onClick={() => copy(`${draft.description}\n\n${draft.hashtags.join(" ")}`)}>
            Copy with hashtags
          </button>
        </div>
        <textarea id="desc" className="textarea" rows={4} value={draft.description} maxLength={2000} onChange={(e) => set("description", e.target.value)} />
      </div>
      <div className="field">
        <span className="label-text">Hashtags</span>
        <div className="chips">
          {draft.hashtags.map((h) => (
            <span className="chip" key={h}>
              {h}
              <button aria-label={`Remove ${h}`} onClick={() => set("hashtags", draft.hashtags.filter((x) => x !== h))}>
                ×
              </button>
            </span>
          ))}
        </div>
        <input
          className="input"
          placeholder="Add a hashtag and press Enter"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && tag.trim()) {
              const t = "#" + tag.trim().replace(/^#/, "").replace(/\s+/g, "");
              if (!draft.hashtags.includes(t)) set("hashtags", [...draft.hashtags, t]);
              setTag("");
            }
          }}
        />
      </div>
      <div className="row small muted">
        <span className="badge">{clip.category}</span> Content category
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------- overlays
type SetDraft = <K extends keyof Draft>(k: K, v: Draft[K]) => void;

function PolishPanel({ clip, draft, set, dirty, onClip }: { clip: Clip; draft: Draft; set: SetDraft; dirty: boolean; onClip: (c: Clip) => void }) {
  const toast = useToast();
  const { confirm, prompt } = useDialogs();
  const [recipeId, setRecipeId] = useState("");
  const [busy, setBusy] = useState(false);
  const used = clip.analysis.polish_used;
  const apply = async () => {
    if (!recipeId) return;
    if (dirty && !(await confirm({ title: "Apply the recipe?", body: "It replaces your unsaved changes in this editor.", confirm: "Apply" }))) return;
    setBusy(true);
    try {
      onClip(await api.applyRecipe(clip.id, recipeId));
      toast("Recipe applied. Re-render to see it.");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const saveAs = async () => {
    if (dirty) {
      toast("Save your changes first, then save them as a recipe.", "error");
      return;
    }
    const name = await prompt({ title: "Save as recipe", body: "Saves this clip's caption style, framing, finishing and polish so you can apply it to other clips and videos.", label: "Recipe name", placeholder: "e.g. Podcast punchy", confirm: "Save recipe" });
    if (!name?.trim()) return;
    try {
      await api.saveRecipeFromClip(clip.id, name.trim());
      toast(`Saved recipe “${name.trim()}”`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="stack lg">
      <div className="stack sm">
        <span className="section-title">Recipe</span>
        <div className="row">
          <div style={{ flex: 1, minWidth: 180 }}>
            <RecipeSelect value={recipeId} onChange={(id) => setRecipeId(id)} placeholder="Pick a recipe…" />
          </div>
          <button className="btn" onClick={apply} disabled={!recipeId || busy}>
            {busy ? <Spinner /> : <Icon name="check" />} Apply
          </button>
          <button className="btn ghost" onClick={saveAs}>
            <Icon name="plus" /> Save as recipe
          </button>
        </div>
        <span className="hint">A recipe sets caption style, framing, finishing and everything below in one go. To use one on every clip, use “Apply recipe” on the project page.</span>
      </div>
      <div className="divider" />
      <PolishControls value={draft.polish} onChange={(p) => set("polish", { ...draft.polish, ...p })} compact />
      {used && (
        <p className="tiny muted">
          Last render: {used.cuts} jump cut(s), {used.removed}s removed · {used.zooms} zoom(s) · {used.emphasis} emphasised word(s) · {used.emoji} emoji
          {used.censored ? ` · ${used.censored} word(s) censored` : ""}
          {used.cold_open ? ` · cold open from ${used.cold_open[0].toFixed(1)}s` : ""}
        </p>
      )}
    </div>
  );
}

const END_TEMPLATES = ["Follow for part 2", "Link in bio", "Watch the full stream", "Download now"];

/** Text over the last seconds of the clip (call to action / end screen). */
function EndTextControls({ clip, draft, set }: { clip: Clip; draft: Draft; set: SetDraft }) {
  const e = draft.end_text;
  const required = !!clip.features?.end_text.required;
  const upd = (p: Partial<EndTextSpec>) => set("end_text", { ...e, ...p });
  return (
    <>
      <div className="divider" />
      <div className="stack sm">
        <label className="check">
          <input type="checkbox" checked={e.enabled} onChange={(ev) => upd({ enabled: ev.target.checked, text: e.text || "Follow for part 2" })} />
          <span>
            <strong>End text</strong> for the last seconds
            <br />
            <span className="small muted">{required ? "The campaign asks for this on every clip." : "A call to action like “Follow for part 2”. The clip keeps its length."}</span>
          </span>
        </label>
        {required && !e.enabled && <Alert kind="warn">The campaign requires end text, so this clip will fail its check without it.</Alert>}
        {e.enabled && (
          <>
            <input className="input" value={e.text} maxLength={160} aria-label="End text" placeholder="e.g. Follow for part 2" onChange={(ev) => upd({ text: ev.target.value })} />
            <div className="row wrap" style={{ gap: 6 }}>
              {END_TEMPLATES.map((t) => (
                <button key={t} type="button" className={`chip-btn ${e.text === t ? "on" : ""}`} onClick={() => upd({ text: t })}>
                  {t}
                </button>
              ))}
            </div>
            {!e.text.trim() && <span className="small" style={{ color: "var(--warn)" }}>Write the end text, or it won't appear.</span>}
            <div className="grid cols-2">
              <div className="field">
                <label htmlFor="end-dur">Last {e.duration}s</label>
                <input id="end-dur" type="range" min={1} max={Math.max(1, Math.min(6, Math.floor((draft.end - draft.start) / 2)))} step={0.5} value={e.duration} onChange={(ev) => upd({ duration: +ev.target.value })} />
              </div>
              <div className="field">
                <span className="label-text">Look</span>
                <div className="seg">
                  <button className={e.look === "overlay" ? "active" : ""} onClick={() => upd({ look: "overlay" })}>
                    Over the video
                  </button>
                  <button className={e.look === "card" ? "active" : ""} onClick={() => upd({ look: "card" })} title="The video is dimmed and blurred behind the text, and captions stop">
                    End card
                  </button>
                </div>
              </div>
            </div>
            <div className="field">
              <span className="label-text">Style</span>
              <HookStylePicker value={e.style} onChange={(v) => upd({ style: v })} />
            </div>
            <span className="tiny muted">Use “Preview frame” near the end of the clip to see it.</span>
          </>
        )}
      </div>
    </>
  );
}

function OverlaysPanel({ clip, draft, set, campaign }: { clip: Clip; draft: Draft; set: SetDraft; campaign: Campaign | null }) {
  const b = campaign?.brief;
  return (
    <div className="stack lg">
      {(clip.features?.locked ?? []).includes("hook") ? (
        <p className="small muted">Montages use the POV text (Montage tab) instead of a hook title card.</p>
      ) : (
      <div className="stack sm">
        <label className="check">
          <input type="checkbox" checked={draft.hook_overlay.enabled} onChange={(e) => set("hook_overlay", { ...draft.hook_overlay, enabled: e.target.checked })} />
          <span>
            <strong>Hook title</strong> at the start of the clip
            <br />
            <span className="small muted">A bold title near the top for the first seconds, inside the safe area.</span>
          </span>
        </label>
        {draft.hook_overlay.enabled && (
          <>
            <input className="input" value={draft.hook_overlay.text} maxLength={160} onChange={(e) => set("hook_overlay", { ...draft.hook_overlay, text: e.target.value })} placeholder="3–9 punchy words" />
            <select
              className="select"
              value=""
              onChange={(e) => e.target.value && set("hook_overlay", { ...draft.hook_overlay, text: e.target.value.replace("{title}", (clip.title || "this happened").replace(/[.!?]+$/, "")) })}
            >
              <option value="">Hook templates…</option>
              {HOOK_TEMPLATES.map((t) => (
                <option key={t} value={t}>
                  {t.replace("{title}", "<title>")}
                </option>
              ))}
            </select>
            <div className="field">
              <label>Style</label>
              <HookStylePicker value={draft.polish.hook_style} onChange={(v) => set("polish", { ...draft.polish, hook_style: v })} />
            </div>
            <div className="row wrap" style={{ gap: 6 }}>
              {[clip.title, ...clip.hooks].filter((h, i, a) => h && a.indexOf(h) === i).slice(0, 4).map((h) => (
                <button key={h} className="btn ghost sm" onClick={() => set("hook_overlay", { ...draft.hook_overlay, text: h })} title="Use this">
                  “{h.length > 48 ? h.slice(0, 46) + "…" : h}”
                </button>
              ))}
            </div>
            <div className="field">
              <label>Show for {draft.hook_overlay.duration}s</label>
              <input type="range" min={1.5} max={8} step={0.5} value={draft.hook_overlay.duration} onChange={(e) => set("hook_overlay", { ...draft.hook_overlay, duration: +e.target.value })} />
            </div>
            {draft.hook_overlay.text.split(/\s+/).filter(Boolean).length > 10 && <span className="small" style={{ color: "var(--warn)" }}>Long hooks are cut to 10 words. Shorter reads better.</span>}
          </>
        )}
      </div>
      )}
      {!(clip.features?.locked ?? []).includes("end") && <EndTextControls clip={clip} draft={draft} set={set} />}
      <div className="divider" />
      {clip.analysis.mode !== "montage" && (
      <label className="check">
        <input type="checkbox" checked={draft.blur_burned === "auto"} onChange={(e) => set("blur_burned", e.target.checked ? "auto" : "off")} />
        <span>
          <strong>Blur the source's burned-in captions</strong>
          <br />
          <span className="small muted">
            Detects subtitles already in the video (lower part of the frame) and blurs that strip only while text is visible.
            {clip.analysis.burned_text ? ` Found in the last render (${Math.round(clip.analysis.burned_text.presence * 100)}% of the clip).` : clip.analysis.layout_used ? " None found in the last render." : ""}
          </span>
        </span>
      </label>
      )}
      <div className="divider" />
      <span className="section-title">Campaign overlays</span>
      {!campaign ? (
        <p className="small muted">This video isn't in a campaign. Pick one on the project page to add its logo and disclosure.</p>
      ) : (
        <div className="stack sm">
          {b && b.logo.mode !== "none" && (
            <label className="check">
              <input type="checkbox" checked={draft.logo} onChange={(e) => set("logo", e.target.checked)} disabled={!b.logo.has_file} />
              <span>
                <strong>{b.logo.mode === "template" ? "Logo template" : "Logo"}</strong> from “{campaign.name}”
                <br />
                <span className="small muted">
                  {!b.logo.has_file ? "No logo uploaded to the campaign yet." : b.logo.mode === "template" ? "Full-frame overlay as supplied, looped; output is 1080x1920." : `Placed in the ${b.logo.zone.replace("_", " ")} zone. Adjust on the campaign page.`}
                </span>
              </span>
            </label>
          )}
          {b && b.disclosure.text && b.disclosure.on_screen && (
            <label className="check">
              <input type="checkbox" checked={draft.disclosure} onChange={(e) => set("disclosure", e.target.checked)} />
              <span>
                Show <strong>“{b.disclosure.text}”</strong> on screen ({b.disclosure.corner.replace("_", " ")})
              </span>
            </label>
          )}
          {b && b.logo.mode === "none" && !(b.disclosure.text && b.disclosure.on_screen) && <p className="small muted">This campaign has no on-screen logo or disclosure.</p>}
          <Link to={`/campaigns/${campaign.id}`} className="small">
            Edit campaign →
          </Link>
        </div>
      )}
      <p className="tiny muted">Use “Preview exact frame” to see a still frame with every overlay, and “Safe zones” to check nothing sits under the app's buttons.</p>
    </div>
  );
}

// -------------------------------------------------------------------------------- audio
const SFX_TYPES: SfxType[] = ["pop", "whoosh", "hit", "riser", "ding"];

function AudioPanel({ clip, draft, set, campaign, onClip }: { clip: Clip; draft: Draft; set: SetDraft; campaign: Campaign | null; onClip: (c: Clip) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const lib = usePoll(api.library, []).data;
  const locked = clip.features?.locked ?? [];
  const b = campaign?.brief;
  const upload = async (f: File) => {
    setBusy(true);
    try {
      onClip(await api.uploadMusic(clip.id, f));
      toast("Music added. Re-render to hear it.");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const tracks = lib ? Object.entries(lib.music).flatMap(([m, l]) => l.map((t) => ({ mood: m, name: t.name }))) : [];
  const libCount = tracks.length;
  return (
    <div className="stack lg">
      {b && b.sound_rule.mode === "burn" && (
        <div className="stack sm">
          <span className="section-title">Campaign sound</span>
          {b.sounds.filter((s) => s.has_file).length === 0 ? (
            <Alert kind="warn">The campaign has no sound files yet. Upload one on the campaign page.</Alert>
          ) : (
            <>
              <select className="select" value={draft.sound_id ?? ""} onChange={(e) => set("sound_id", e.target.value || null)}>
                {b.sounds
                  .filter((s) => s.has_file)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {s.start ? ` (from ${s.start}s)` : ""}
                    </option>
                  ))}
              </select>
              <div className="grid cols-2">
                <div className="field">
                  <label>Sound volume: {Math.round(draft.sound_volume * 100)}%</label>
                  <input type="range" min={0} max={1.5} step={0.05} value={draft.sound_volume} onChange={(e) => set("sound_volume", +e.target.value)} />
                  {draft.sound_volume < b.sound_rule.min_volume && <span className="small" style={{ color: "var(--danger)" }}>Below the campaign minimum of {Math.round(b.sound_rule.min_volume * 100)}%.</span>}
                </div>
                <div className="field">
                  <label>Original audio: {Math.round(draft.original_volume * 100)}%</label>
                  <input type="range" min={0} max={1} step={0.05} value={draft.original_volume} onChange={(e) => set("original_volume", +e.target.value)} />
                </div>
              </div>
            </>
          )}
        </div>
      )}
      {b && b.sound_rule.mode === "in_app" && (
        <Alert kind="info" title="Official sound is added in the app">
          When posting, add {b.sounds.length ? b.sounds.map((s) => `“${s.name}”`).join(" or ") : "the campaign sound"} from the in-app library at ≥ {Math.round(b.sound_rule.min_volume * 100)}% volume. It's on the export checklist.
        </Alert>
      )}

      <div className="stack sm">
        <span className="section-title">Background music</span>
        {locked.includes("music") ? (
          <p className="small muted">This campaign doesn't allow background music.</p>
        ) : (
          <>
            <select className="select" value={draft.music} onChange={(e) => set("music", e.target.value)}>
              <option value="none">No music</option>
              <option value="auto">Auto: pick by mood from your library{libCount ? "" : " (library is empty)"}</option>
              {lib?.moods.map((m) => (
                <option key={m} value={`mood:${m}`}>
                  Any {m} track
                </option>
              ))}
              {tracks.map((t) => (
                <option key={`${t.mood}/${t.name}`} value={`lib:${t.mood}/${t.name}`}>
                  {t.mood} · {t.name}
                </option>
              ))}
              {clip.edit.music_file && <option value="upload">Uploaded: {clip.edit.music_file}</option>}
            </select>
            <div className="row wrap">
              <button className="btn sm" onClick={() => input.current?.click()} disabled={busy}>
                {busy ? <Spinner /> : <Icon name="upload" />} Upload a track for this clip
              </button>
              <Link className="btn sm ghost" to="/library">
                <Icon name="library" /> Manage library
              </Link>
              <input ref={input} type="file" accept="audio/*" hidden onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) upload(f);
              }} />
            </div>
            <div className="field">
              <label>Music volume: {Math.round(draft.music_volume * 100)}%</label>
              <input type="range" min={0} max={0.6} step={0.01} value={draft.music_volume} onChange={(e) => set("music_volume", +e.target.value)} disabled={draft.music === "none"} />
              <span className="hint">The music dips automatically (about 10 dB) while someone is speaking and comes back up in pauses.</span>
            </div>
            {clip.analysis.music_used && <span className="tiny muted">Last render used: {clip.analysis.music_used}</span>}
          </>
        )}
      </div>

      <div className="stack sm">
        <span className="section-title">Sound effects</span>
        {locked.includes("sfx") ? (
          <p className="small muted">This campaign doesn't allow sound effects.</p>
        ) : (
          <>
            <div className="seg">
              {(
                [
                  ["auto", "Automatic"],
                  ["custom", "Choose myself"],
                  ["off", "Off"],
                ] as const
              ).map(([k, l]) => (
                <button
                  key={k}
                  className={draft.sfx === k ? "active" : ""}
                  onClick={() => {
                    set("sfx", k);
                    if (k === "custom" && !draft.sfx_cues.length) set("sfx_cues", clip.analysis.sfx_used ?? []);
                  }}
                >
                  {l}
                </button>
              ))}
            </div>
            {draft.sfx === "auto" && <span className="small muted">Pop on the hook title, whoosh on camera cuts, ding on numbers, hit on emphatic lines, riser into the payoff. At most 4, at least 2.5 s apart.</span>}
            {draft.sfx === "custom" && (
              <div className="stack sm">
                {draft.sfx_cues.map((c, i) => (
                  <div className="cue-row" key={i}>
                    <select className="select" value={c.type} onChange={(e) => set("sfx_cues", draft.sfx_cues.map((x, j) => (j === i ? { ...x, type: e.target.value as SfxType } : x)))}>
                      {SFX_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                    <input className="input" type="number" step={0.1} min={0} max={draft.end - draft.start} value={c.t} onChange={(e) => set("sfx_cues", draft.sfx_cues.map((x, j) => (j === i ? { ...x, t: Math.max(0, +e.target.value) } : x)))} />
                    <button className="btn sm icon" title="Listen" aria-label="Listen to this sound effect" onClick={() => new Audio(media.sfx(c.type)).play()}>
                      <Icon name="play" />
                    </button>
                    <button className="btn sm icon danger" aria-label="Remove" onClick={() => set("sfx_cues", draft.sfx_cues.filter((_, j) => j !== i))}>
                      <Icon name="x" />
                    </button>
                  </div>
                ))}
                <button className="btn sm" style={{ alignSelf: "flex-start" }} disabled={draft.sfx_cues.length >= 12} onClick={() => set("sfx_cues", [...draft.sfx_cues, { type: "pop", t: 0.1 }])}>
                  <Icon name="plus" /> Add effect
                </button>
                <span className="tiny muted">Times are seconds from the start of the clip. A riser starts at its time and peaks 1.5 s later.</span>
              </div>
            )}
            {draft.sfx === "auto" && clip.analysis.sfx_used && clip.analysis.sfx_used.length > 0 && (
              <span className="tiny muted">Last render: {clip.analysis.sfx_used.map((c) => `${c.type} @ ${c.t}s`).join(", ")}</span>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------- check & export
function CampaignPanel({ clip, dirty, campaign, onClip }: { clip: Clip; dirty: boolean; campaign: Campaign | null; onClip: () => void }) {
  const toast = useToast();
  const comp = usePoll(() => api.compliance(clip.id), [clip.id, clip.version]);
  const [busy, setBusy] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const c: Compliance | null = comp.data;
  // posted marks: shown at once after a click, then the clip reload catches up
  const [posted, setPosted] = useState<PostedMap | null>(null);
  const serverPosted = JSON.stringify(clip.posted ?? {});
  useEffect(() => setPosted(null), [clip.id, serverPosted]); // the reloaded clip (or another tab's change) takes over
  const postedNow = posted ?? clip.posted ?? {};
  const platforms = (clip.post_platforms?.length ? clip.post_platforms : ["reels", "shorts"]) as Platform[];
  const state = postedState(postedNow, platforms);

  const saveCaption = async (p: string) => {
    try {
      await api.patchClip(clip.id, { edit: { post: { [p]: edits[p] ?? "" } } });
      setEdits((e) => {
        const n = { ...e };
        delete n[p];
        return n;
      });
      comp.reload();
      onClip();
      toast("Caption saved");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const doExport = async () => {
    setBusy(true);
    try {
      const r = await api.exportClip(clip.id);
      toast(`Exported to ${r.path}`);
      onClip();
      comp.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack lg">
      {dirty && <Alert kind="warn">You have unsaved edits. Save (and re-render) before checking and exporting.</Alert>}
      {!c ? (
        comp.error ? <Alert kind="danger">{comp.error}</Alert> : <Spinner />
      ) : (
        <>
          <div className="row between">
            <div className="row">
              <Verdict v={c.verdict} />
              <span className="small muted">{c.campaign ? `Checked against “${c.campaign.name}”` : "General checks (no campaign)"}</span>
            </div>
            <button className="btn sm ghost" onClick={comp.reload}>
              <Icon name="refresh" /> Re-check
            </button>
          </div>
          <CheckList checks={c.checks} tickKey={clip.id} />
          <div className="divider" />
          <span className="section-title">Post captions</span>
          {Object.entries(c.posts).map(([p, post]) => {
            const val = edits[p] ?? post.caption;
            return (
              <div className="stack sm" key={p}>
                <div className="row between">
                  <strong className="small">
                    {PLATFORM_LABEL[p as Platform] ?? p}
                    {post.edited && <span className="badge" style={{ marginLeft: 6 }}>edited</span>}
                  </strong>
                  <div className="row">
                    {edits[p] !== undefined && (
                      <button className="btn sm primary" onClick={() => saveCaption(p)}>
                        Save
                      </button>
                    )}
                    {post.edited && edits[p] === undefined && (
                      <button
                        className="btn sm ghost"
                        onClick={() =>
                          attempt(toast, async () => {
                            await api.patchClip(clip.id, { edit: { post: { [p]: "" } } });
                            comp.reload();
                            onClip();
                            toast("Back to the campaign's caption");
                          })
                        }
                        title="Go back to the caption built from the campaign's template"
                      >
                        Use template
                      </button>
                    )}
                    <CopyButton text={val} />
                  </div>
                </div>
                {p === "shorts" && (
                  <div className="row small">
                    <span className="muted">Title:</span> <span className="ellipsis">{post.title}</span> <CopyButton text={post.title} label="Copy title" />
                  </div>
                )}
                <textarea className="textarea" rows={5} value={val} onChange={(e) => setEdits((x) => ({ ...x, [p]: e.target.value }))} />
                {post.warnings.map((w) => (
                  <span key={w} className="small" style={{ color: "var(--warn)" }}>
                    {w}
                  </span>
                ))}
              </div>
            );
          })}
          <div className="divider" />
          <div className="stack sm">
            <span className="section-title">Export package</span>
            <span className="small muted">A folder with clip.mp4, captions for each platform, checklist.md and manifest.json (timings, scores, music, effects, compliance). Re-exporting replaces it.</span>
            {clip.exported && <span className="tiny muted mono">Last export: {clip.exported.path}</span>}
            <div className="row wrap">
              <button className="btn primary" onClick={doExport} disabled={busy || !clip.has_video || clip.render_status !== "done"}>
                {busy ? <Spinner /> : <Icon name="package" />} Export{c.verdict === "fail" ? " anyway" : ""}
              </button>
              {clip.has_video && clip.render_status === "done" ? (
                <a className="btn" href={media.exportZip(clip.id)}>
                  <Icon name="download" /> Download .zip
                </a>
              ) : (
                <button className="btn" disabled title="Render the clip with its latest edits first">
                  <Icon name="download" /> Download .zip
                </button>
              )}
              {campaign && (
                <Link className="btn ghost" to={`/campaigns/${campaign.id}`}>
                  Campaign →
                </Link>
              )}
            </div>
            {clip.render_status !== "done" && <span className="small muted">Render the clip (with your latest edits) to export.</span>}
          </div>
        </>
      )}
      <div className="divider" />
      <div className="stack sm">
        <div className="row between">
          <span className="section-title">Mark as posted</span>
          {state.n > 0 && <span className={`badge ${state.done ? "success" : "info"}`}>{state.done ? "Posted everywhere" : `${state.n} of ${state.total} done`}</span>}
        </div>
        <span className="small muted">After posting it in the app, mark it here (add the post's link if you like). Posted clips leave “Ready to post” and are never deleted by re-processing.</span>
        {platforms.map((p) => (
          <div className="posted-block" key={p}>
            <strong className="small">{PLATFORM_LABEL[p]}</strong>
            {clip.has_video || postedNow[p] ? (
              <PostedControl
                clipId={clip.id}
                platform={p}
                posted={postedNow}
                onChange={(m) => {
                  setPosted(m);
                  onClip();
                }}
              />
            ) : (
              <span className="tiny muted">Render it first</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------- montage
function MontagePanel({ clip, onClip }: { clip: Clip; onClip: (c: Clip) => void }) {
  const toast = useToast();
  const m = clip.edit.montage;
  const plan = clip.edit.montage_plan;
  const [pov, setPov] = useState(m?.pov ?? "");
  const [style, setStyle] = useState(m?.style ?? "normal");
  const [dropAt, setDropAt] = useState(m?.drop_at ?? 0.4);
  const [audio, setAudio] = useState(m?.audio ?? "song");
  const [flash, setFlash] = useState(m?.flash ?? true);
  const [busy, setBusy] = useState(false);
  if (!m || !plan) return <Alert kind="warn">This variant has no cut plan. Use “Re-make all variants” on the project page.</Alert>;
  const changed = pov !== m.pov || style !== m.style || Math.abs(dropAt - m.drop_at) > 0.001 || audio !== m.audio || flash !== m.flash;
  const go = async (reshuffle: boolean) => {
    setBusy(true);
    try {
      const c = await api.replanMontage(clip.id, { pov, style, drop_at: dropAt, audio, flash, reshuffle, render: true });
      onClip({ ...c, render_status: "queued" });
      toast(reshuffle ? "New shots picked; re-rendering…" : "Saved; re-rendering…");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  return (
    <div className="stack lg">
      <div className="row wrap small muted" style={{ gap: 12 }}>
        <span>
          <strong>{Math.round(plan.tempo)}</strong> BPM
        </span>
        <span>
          Song {fmt(plan.song_start)} → {fmt(plan.song_end)}
        </span>
        <span>{plan.drop != null ? `Drop at ${plan.drop.toFixed(1)}s` : "No drop found"}</span>
        <span>{plan.slots.length} cuts</span>
      </div>
      <div className="field">
        <label>On-screen text (POV)</label>
        <input className="input" value={pov} maxLength={200} onChange={(e) => setPov(e.target.value)} placeholder="POV: …" />
      </div>
      <div className="field">
        <span className="label-text">Cut style</span>
        <div className="seg">
          {(["chill", "normal", "hype"] as const).map((s) => (
            <button key={s} className={style === s ? "active" : ""} onClick={() => setStyle(s)}>
              {s}
            </button>
          ))}
        </div>
      </div>
      <div className="field">
        <label>Drop lands at {Math.round(dropAt * 100)}% of the video</label>
        <input type="range" min={0.15} max={0.7} step={0.05} value={dropAt} onChange={(e) => setDropAt(+e.target.value)} />
      </div>
      <div className="row wrap">
        <div className="seg">
          <button className={audio === "song" ? "active" : ""} onClick={() => setAudio("song")}>
            Song in video
          </button>
          <button className={audio === "silent" ? "active" : ""} onClick={() => setAudio("silent")}>
            Silent (add in app)
          </button>
        </div>
        <label className="check small">
          <input type="checkbox" checked={flash} onChange={(e) => setFlash(e.target.checked)} /> Flash on the drop
        </label>
      </div>
      <div className="row wrap">
        <button className="btn primary" disabled={!changed || busy} onClick={() => go(false)}>
          {busy ? <Spinner /> : <Icon name="check" />} Save &amp; re-render
        </button>
        <button className="btn" disabled={busy} onClick={() => go(true)} title="Pick different shots for this variant">
          <Icon name="refresh" /> Shuffle shots
        </button>
      </div>
      <details>
        <summary className="small muted" style={{ cursor: "pointer" }}>
          Cut list ({plan.slots.length} shots)
        </summary>
        <div className="stack sm" style={{ marginTop: 8 }}>
          {plan.slots.map((s, i) => (
            <div key={i} className="row small" style={{ gap: 10 }}>
              <span className="mono muted" style={{ width: 52 }}>{s.at.toFixed(2)}s</span>
              <span className="ellipsis" style={{ flex: 1 }}>
                {s.label} @ {fmt(s.t)}
              </span>
              <span className="muted">{s.dur.toFixed(2)}s</span>
              {s.impact && <span className="badge accent">drop</span>}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
