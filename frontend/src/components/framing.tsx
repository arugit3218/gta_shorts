import { useRef, useState } from "react";
import { api, media } from "../api";
import type { Layout, Project, StreamLayout } from "../types";
import { Icon } from "./Icon";
import { Alert, Modal, Spinner, useToast } from "./ui";

type Box = [number, number, number, number];

export const LAYOUT_OPTIONS: { key: Layout; label: string; desc: string }[] = [
  { key: "auto", label: "Auto (recommended)", desc: "Follows a big face; uses the stream layout when it finds a webcam overlay; otherwise fills the frame. No blurred bars." },
  { key: "center", label: "Fill (crop)", desc: "Fills the whole 9:16 frame by cropping the centre. Immersive; the sides of a 16:9 video are cut." },
  { key: "stream", label: "Stream: webcam + gameplay", desc: "Webcam stacked on top, gameplay filling the rest. Mark the webcam once below (or let ClipStudio find it)." },
  { key: "blur", label: "Blurred fill", desc: "Whole frame visible, with blurred bars above and below. Use when nothing may be cropped." },
  { key: "split", label: "Split (2 people)", desc: "Two faces stacked top and bottom, for two-person podcasts." },
];

const DEFAULT_STREAM: StreamLayout = { cam: null, cam_height: 0.38, game_x: 0.5, cam_top: true };

/** Normalised cover crop of ``box`` to ``aspect`` (w/h in pixels) for a source of srcAspect (w/h). */
function coverBox(box: Box, aspect: number, srcAspect: number): Box {
  const [x0, y0, x1, y1] = box;
  const wPx = (x1 - x0) * srcAspect;
  const hPx = y1 - y0;
  let cw = wPx;
  let ch = hPx;
  if (wPx / hPx > aspect) cw = hPx * aspect;
  else ch = wPx / aspect;
  const cwN = cw / srcAspect;
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  const nx0 = Math.min(Math.max(0, cx - cwN / 2), 1 - cwN);
  const ny0 = Math.min(Math.max(0, cy - ch / 2), 1 - ch);
  return [nx0, ny0, nx0 + cwN, ny0 + ch];
}

function CropTile({ src, box, w, h }: { src: string; box: Box; w: number; h: number }) {
  const bw = box[2] - box[0];
  const bh = box[3] - box[1];
  const bgW = w / bw;
  const bgH = h / bh;
  return <div style={{ width: w, height: h, backgroundImage: `url(${src})`, backgroundSize: `${bgW}px ${bgH}px`, backgroundPosition: `${-box[0] * bgW}px ${-box[1] * bgH}px`, backgroundRepeat: "no-repeat" }} />;
}

/** 9:16 mock-up of the stream layout made from the still frame (what the render will look like). */
export function StreamMock({ src, st, srcAspect, width = 150 }: { src: string; st: StreamLayout; srcAspect: number; width?: number }) {
  const H = Math.round((width * 16) / 9);
  const camH = Math.round(H * st.cam_height);
  const gameH = H - camH;
  if (!st.cam) return null;
  const cam = coverBox(st.cam, width / camH, srcAspect);
  const gw = Math.min(1, (width / gameH) / srcAspect);
  const gx0 = Math.min(Math.max(0, st.game_x - gw / 2), 1 - gw);
  const game: Box = [gx0, 0, gx0 + gw, 1];
  const tiles = [<CropTile key="c" src={src} box={cam} w={width} h={camH} />, <CropTile key="g" src={src} box={game} w={width} h={gameH} />];
  return (
    <div style={{ width, height: H, borderRadius: 12, overflow: "hidden", border: "1px solid var(--border-strong)", background: "#000", flexShrink: 0 }}>
      {st.cam_top ? tiles : tiles.reverse()}
    </div>
  );
}

/** Draw / move the webcam box on a still frame. */
function BoxPicker({ src, box, onChange }: { src: string; box: Box | null; onChange: (b: Box) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const pos = (e: { clientX: number; clientY: number }) => {
    const r = ref.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))] as const;
  };
  const down = (e: React.PointerEvent) => {
    e.preventDefault();
    const [px, py] = pos(e);
    const inside = box && px > box[0] && px < box[2] && py > box[1] && py < box[3];
    const start = box;
    const move = (ev: PointerEvent) => {
      const [qx, qy] = pos(ev);
      if (inside && start) {
        const w = start[2] - start[0];
        const h = start[3] - start[1];
        const nx = Math.min(Math.max(0, start[0] + qx - px), 1 - w);
        const ny = Math.min(Math.max(0, start[1] + qy - py), 1 - h);
        onChange([nx, ny, nx + w, ny + h]);
      } else {
        const b: Box = [Math.min(px, qx), Math.min(py, qy), Math.max(px, qx), Math.max(py, qy)];
        if (b[2] - b[0] > 0.03 && b[3] - b[1] > 0.03) onChange(b);
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <div ref={ref} onPointerDown={down} style={{ position: "relative", cursor: "crosshair", userSelect: "none", touchAction: "none", borderRadius: 8, overflow: "hidden", background: "#000" }}>
      <img src={src} alt="Frame from the video" draggable={false} style={{ width: "100%", display: "block" }} />
      {box && (
        <div
          style={{
            position: "absolute",
            left: `${box[0] * 100}%`,
            top: `${box[1] * 100}%`,
            width: `${(box[2] - box[0]) * 100}%`,
            height: `${(box[3] - box[1]) * 100}%`,
            outline: "2px solid var(--accent)",
            boxShadow: "0 0 0 9999px rgba(0,0,0,.45)",
            cursor: "move",
          }}
        />
      )}
    </div>
  );
}

export function FramingDialog({ project, selected = [], onClose, onDone }: { project: Project; selected?: string[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const saved = project.options.stream_layout;
  const [layout, setLayout] = useState<Layout>(saved?.cam ? "stream" : ((project.options.layout as Layout | undefined) ?? "auto"));
  const [st, setSt] = useState<StreamLayout>({ ...DEFAULT_STREAM, ...(saved ?? {}) });
  const [t, setT] = useState<number | undefined>(undefined);
  const [frameV, setFrameV] = useState(0);
  const [detecting, setDetecting] = useState(false);
  const [scope, setScope] = useState<"selected" | "unrejected" | "approved" | "all">(selected.length ? "selected" : "unrejected");
  const [busy, setBusy] = useState(false);
  const src = media.frame(project.id, t, frameV);
  const srcAspect = project.width && project.height ? project.width / project.height : 16 / 9;
  const showStream = layout === "stream" || layout === "auto";

  const detect = async () => {
    setDetecting(true);
    try {
      const r = await api.detectFacecam(project.id);
      if (r.cam) {
        setSt((s) => ({ ...s, cam: r.cam }));
        toast("Webcam found. Adjust the box if needed.");
      } else toast("No webcam overlay found. Drag a box around it on the frame.", "error");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setDetecting(false);
    }
  };

  const apply = async () => {
    if (layout === "stream" && !st.cam) {
      toast("Mark the webcam on the frame first (or use Detect).", "error");
      return;
    }
    setBusy(true);
    try {
      if (showStream && st.cam) await api.patchProject(project.id, { stream_layout: { cam: st.cam, cam_height: st.cam_height, game_x: st.game_x, cam_top: st.cam_top } });
      else if (saved && !st.cam) await api.patchProject(project.id, { clear_stream_layout: true });
      const r = await api.bulkClips(project.id, scope === "selected" ? { layout, ids: selected, render: true } : { layout, scope, render: true });
      toast(`${r.updated} clip(s) re-rendering with the new framing`);
      onDone();
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };

  return (
    <Modal
      title={scope === "selected" ? `Framing for ${selected.length} selected clip${selected.length === 1 ? "" : "s"}` : "Framing for many clips"}
      wide
      onClose={onClose}
      actions={
        <>
          <select className="select" style={{ width: "auto" }} aria-label="Which clips" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
            {selected.length > 0 && <option value="selected">The {selected.length} selected clip{selected.length === 1 ? "" : "s"}</option>}
            <option value="unrejected">All except rejected</option>
            <option value="approved">Approved only</option>
            <option value="all">All clips</option>
          </select>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" onClick={apply} disabled={busy}>
            {busy ? <Spinner /> : <Icon name="refresh" />} Apply &amp; re-render
          </button>
        </>
      }
    >
      <div className="stack sm">
        {LAYOUT_OPTIONS.map((o) => (
          <label key={o.key} className={`hook-option ${layout === o.key ? "active" : ""}`}>
            <input type="radio" name="bulk-layout" checked={layout === o.key} onChange={() => setLayout(o.key)} />
            <span>
              <strong>{o.label}</strong>
              <br />
              <span className="small muted">{o.desc}</span>
            </span>
          </label>
        ))}
      </div>
      {showStream && (
        <div className="stack">
          <div className="row between">
            <span className="section-title">Webcam position {layout === "auto" && "(optional for Auto)"}</span>
            <div className="row">
              <button className="btn sm" onClick={() => (setT(Math.random() * (project.duration ?? 60)), setFrameV((v) => v + 1))} title="Show a different moment">
                <Icon name="refresh" /> Other frame
              </button>
              <button className="btn sm" onClick={detect} disabled={detecting}>
                {detecting ? <Spinner /> : <Icon name="eye" />} Detect webcam
              </button>
              {st.cam && (
                <button className="btn sm ghost" onClick={() => setSt((s) => ({ ...s, cam: null }))}>
                  Clear
                </button>
              )}
            </div>
          </div>
          <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
            <div className="stack sm" style={{ flex: 1, minWidth: 0 }}>
              <BoxPicker src={src} box={st.cam} onChange={(b) => setSt((s) => ({ ...s, cam: b }))} />
              <span className="tiny muted">Drag to draw a box around the webcam; drag the box to move it.</span>
            </div>
            {st.cam ? <StreamMock src={src} st={st} srcAspect={srcAspect} /> : <div className="small muted" style={{ width: 150 }}>Mark the webcam to see the 9:16 preview.</div>}
          </div>
          {st.cam && (
            <div className="grid cols-2">
              <div className="field">
                <label>Webcam height: {Math.round(st.cam_height * 100)}% of the frame</label>
                <input type="range" min={0.2} max={0.6} step={0.01} value={st.cam_height} onChange={(e) => setSt((s) => ({ ...s, cam_height: +e.target.value }))} />
              </div>
              <div className="field">
                <label>Gameplay focus: {st.game_x < 0.45 ? "left" : st.game_x > 0.55 ? "right" : "centre"}</label>
                <input type="range" min={0} max={1} step={0.01} value={st.game_x} onChange={(e) => setSt((s) => ({ ...s, game_x: +e.target.value }))} />
              </div>
              <label className="check small">
                <input type="checkbox" checked={st.cam_top} onChange={(e) => setSt((s) => ({ ...s, cam_top: e.target.checked }))} /> Webcam on top (off = bottom)
              </label>
            </div>
          )}
          {layout === "auto" && !st.cam && <Alert kind="info">Auto will try to find the webcam in each clip by itself. Marking it here makes every clip consistent.</Alert>}
        </div>
      )}
    </Modal>
  );
}
