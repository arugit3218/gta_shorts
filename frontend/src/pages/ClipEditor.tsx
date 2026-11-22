import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, media } from "../api";
import { Icon } from "../components/Icon";
import { Alert, Bar, Spinner, toneOf, useToast } from "../components/ui";
import { fmtTime } from "../format";
import { usePoll } from "../hooks";
import type { AspectRatio, CaptionStyle, Clip, ClipWords, Layout, Word } from "../types";
import { ScoreBreakdown } from "./ProjectView";

type Tab = "trim" | "captions" | "style" | "format" | "text" | "music";
const FONTS = ["Arial Black", "Arial", "Impact", "Helvetica Neue", "Futura", "Avenir Next", "Georgia", "Verdana", "Trebuchet MS", "Courier New"];

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
    music_volume: c.edit.music_volume ?? 0.12,
    words: null,
  };
}

export default function ClipEditor() {
  const { id = "" } = useParams();
  const toast = useToast();
  const clipQ = usePoll(() => api.clip(id), [id]);
  const wordsQ = usePoll(() => api.clipWords(id), [id]);
  const presets = usePoll(api.captionPresets, []).data;
  const clip = clipQ.data;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [tab, setTab] = useState<Tab>("trim");
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const rendering = clip && ["queued", "rendering"].includes(clip.render_status);
  const jobs = usePoll(() => (clip ? api.jobs({ project_id: clip.project_id, active: true }) : Promise.resolve([])), [clip?.project_id], rendering ? 1000 : null);
  const renderJob = jobs.data?.find((j) => j.payload.clip_id === id);

  useEffect(() => {
    if (clip && !draft) setDraft(draftFrom(clip));
  }, [clip, draft]);

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
      },
      ...(draft.words ? { caption_words: draft.words } : {}),
    };
  }, [draft, clip]);

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

  const makePreview = async (t?: number) => {
    if (!draft || !clip) return;
    setPreviewBusy(true);
    try {
      const blob = await api.previewFrame(id, {
        t: t ?? Math.min(2, (draft.end - draft.start) / 3),
        edit: {
          aspect_ratio: draft.aspect_ratio,
          // tracking needs a full face pass; the still preview uses a static crop
          layout: draft.layout === "blur" ? "blur" : "center",
          output_height: draft.output_height,
          captions: draft.captions,
          caption_preset: draft.caption_preset,
          ...(Object.keys(draft.caption_style).length ? { caption_style: draft.caption_style } : {}),
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
        <div className="row">
          {dirty && <span className="small muted">Unsaved changes</span>}
          <button className="btn" disabled={!dirty || saving} onClick={() => setDraft(draftFrom(clip))}>
            Discard
          </button>
          <button className="btn" disabled={!dirty || saving} onClick={() => save(false)}>
            Save
          </button>
          <button className="btn primary" disabled={saving || !!rendering} onClick={() => save(true)}>
            {saving || rendering ? <Spinner /> : <Icon name="refresh" />} {dirty ? "Save & render" : "Re-render"}
          </button>
        </div>
      </div>

      <div className="editor">
        <div className="editor-preview stack">
          <div className={`card clip-media ${arClass}`} style={{ overflow: "hidden", maxHeight: 640 }}>
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
                </div>
              </div>
            )}
          </div>
          <div className="row">
            <button className="btn sm" onClick={() => makePreview()} disabled={previewBusy}>
              {previewBusy ? <Spinner /> : <Icon name="type" />} Preview captions
            </button>
            {preview && (
              <button className="btn sm ghost" onClick={() => setPreview(null)}>
                Show video
              </button>
            )}
            <span className="spacer" />
            {clip.has_video && (
              <a className="btn sm" href={media.download(clip)}>
                <Icon name="download" /> MP4
              </a>
            )}
          </div>
          {clip.render_status === "stale" && <Alert kind="warn">The video doesn't include your latest edits yet. Re-render to update it.</Alert>}
          {clip.render_status === "failed" && <Alert kind="danger" title="Render failed">{clip.render_error}</Alert>}
          {clip.analysis.layout_used && (
            <p className="tiny muted">
              Last render: {clip.analysis.layout_used} framing{clip.analysis.face_ratio ? ` · faces in ${Math.round(clip.analysis.face_ratio * 100)}% of frames` : ""}
            </p>
          )}
        </div>

        <div className="card">
          <div className="card-body">
            <div className="tabs">
              {(
                [
                  ["trim", "Trim", "scissors"],
                  ["captions", "Captions", "type"],
                  ["style", "Caption style", "sparkles"],
                  ["format", "Format", "crop"],
                  ["text", "Title & hook", "edit"],
                  ["music", "Music", "music"],
                ] as const
              ).map(([k, l, icon]) => (
                <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
                  <span className="row" style={{ gap: 6 }}>
                    <Icon name={icon} size={14} />
                    {l}
                  </span>
                </button>
              ))}
            </div>

            {tab === "trim" && wordsQ.data && <TrimPanel clip={clip} words={wordsQ.data} draft={draft} set={set} />}
            {tab === "trim" && !wordsQ.data && <Spinner />}
            {tab === "captions" && wordsQ.data && <CaptionsPanel words={wordsQ.data} draft={draft} set={set} onReset={async () => {
              const c = await api.patchClip(id, { reset_captions: true });
              clipQ.setData(c);
              set("words", null);
              wordsQ.reload();
              toast("Captions reset to the transcript");
            }} />}
            {tab === "style" && presets && style && (
              <StylePanel presets={presets} draft={draft} style={style} set={set} setStyle={setStyle} onPreview={() => makePreview()} previewBusy={previewBusy} />
            )}
            {tab === "format" && <FormatPanel draft={draft} set={set} />}
            {tab === "text" && <TextPanel clip={clip} draft={draft} set={set} />}
            {tab === "music" && <MusicPanel clip={clip} draft={draft} set={set} onClip={(c) => clipQ.setData(c)} />}
          </div>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------- trim
function TrimPanel({ clip, words, draft, set }: { clip: Clip; words: ClipWords; draft: Draft; set: <K extends keyof Draft>(k: K, v: Draft[K]) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const ctx = words.context_words;
  const lo = Math.max(0, Math.min(clip.start, draft.start) - 15);
  const hi = Math.min(words.source_duration || draft.end + 15, Math.max(clip.end, draft.end) + 15);
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
    v.currentTime = draft.start;
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
          <span className="label-text">Source preview</span>
          <button className="btn sm" onClick={playSel}>
            <Icon name="play" /> Play selection
          </button>
        </div>
        <video
          ref={video}
          src={media.source(clip.project_id)}
          controls
          preload="metadata"
          style={{ width: "100%", borderRadius: 8, background: "#000", maxHeight: 260 }}
          onTimeUpdate={(e) => {
            const t = e.currentTarget.currentTime;
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
          <div className="caption-line" key={`${i}-${l[0].s}`}>
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

function StylePanel({
  presets,
  draft,
  style,
  set,
  setStyle,
  onPreview,
  previewBusy,
}: {
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
        <div className="field">
          <label>Vertical position: {Math.round(style.position * 100)}%</label>
          <input type="range" min={0.1} max={0.92} step={0.01} value={style.position} onChange={(e) => setStyle("position", +e.target.value)} />
        </div>
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
              ["auto", "Auto", "Tracks the main face when there is one; otherwise uses blurred fill."],
              ["center", "Centre crop", "Static crop from the middle of the frame."],
              ["blur", "Blurred fill", "Whole frame, with a blurred copy filling the empty space. Good for gameplay and screen recordings."],
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

// -------------------------------------------------------------------------------- music
function MusicPanel({ clip, draft, set, onClip }: { clip: Clip; draft: Draft; set: <K extends keyof Draft>(k: K, v: Draft[K]) => void; onClip: (c: Clip) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
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
  const remove = async () => onClip(await api.patchClip(clip.id, { remove_music: true }));
  return (
    <div className="stack lg">
      <Alert kind="info">Only use music you own or have a licence for. It's mixed quietly under the speech and fades out at the end.</Alert>
      {clip.edit.music ? (
        <div className="row between card pad">
          <span className="row">
            <Icon name="music" /> {clip.edit.music}
          </span>
          <button className="btn sm danger" onClick={remove}>
            <Icon name="trash" /> Remove
          </button>
        </div>
      ) : (
        <button className="btn" onClick={() => input.current?.click()} disabled={busy}>
          {busy ? <Spinner /> : <Icon name="upload" />} Add background music (MP3, M4A, WAV…)
        </button>
      )}
      <input ref={input} type="file" accept="audio/*" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      <div className="field">
        <label>Music volume: {Math.round(draft.music_volume * 100)}%</label>
        <input type="range" min={0} max={0.5} step={0.01} value={draft.music_volume} onChange={(e) => set("music_volume", +e.target.value)} disabled={!clip.edit.music} />
      </div>
    </div>
  );
}
