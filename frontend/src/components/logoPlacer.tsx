import { useRef, useState } from "react";
import { media } from "../api";
import type { Brief, Platform, Rect, SafeZones } from "../types";
import { Icon } from "./Icon";

type Logo = Brief["logo"];

/** Logo rectangle on the 9:16 frame, as fractions (x0, y0, x1, y1). */
export function logoRect(logo: Logo): Rect {
  const aspect = logo.image_w && logo.image_h ? logo.image_h / logo.image_w : 0.3;
  const w = logo.width;
  const h = w * aspect * (9 / 16);
  return [logo.x - w / 2, logo.y - h / 2, logo.x + w / 2, logo.y + h / 2];
}

const inside = (r: Rect, x: number, y: number) => x >= r[0] && x <= r[2] && y >= r[1] && y <= r[3];

/** Where the title text sits for the first seconds (centred at 20% height, up to two lines). */
export const TITLE_AREA: Rect = [0.1, 0.145, 0.9, 0.255];

export function overlapsTitle(logo: Logo): boolean {
  const r = logoRect(logo);
  return logo.mode === "free" && r[0] < TITLE_AREA[2] && r[2] > TITLE_AREA[0] && r[1] < TITLE_AREA[3] && r[3] > TITLE_AREA[1];
}

/** Where the end text sits over the last seconds (centred at 45% height). */
export const END_AREA: Rect = [0.1, 0.4, 0.9, 0.5];

/** How much of a caption band (centre y, height h) the logo rectangle covers (free logos only). */
export function captionLogoOverlap(logo: Logo, y: number, h: number): number {
  if (logo.mode !== "free" || !logo.has_file) return 0;
  const r = logoRect(logo);
  if (r[2] < 0.2 || r[0] > 0.8) return 0; // captions are centred; a logo at the edge doesn't touch them
  const a = y - h / 2;
  const b = y + h / 2;
  return Math.max(0, Math.min(b, r[3]) - Math.max(a, r[1])) / h;
}

/** Fraction of the logo under a platform's UI / no-go areas (sampled on a 30x30 grid). */
export function coverage(logo: Logo, zones: SafeZones | null | undefined, platform: Platform): { ui: number; nogo: number } {
  const z = zones?.[platform];
  if (!z) return { ui: 0, nogo: 0 };
  const [x0, y0, x1, y1] = logoRect(logo);
  let ui = 0;
  let ng = 0;
  const N = 30;
  for (let i = 0; i < N; i++)
    for (let j = 0; j < N; j++) {
      const x = x0 + ((i + 0.5) / N) * (x1 - x0);
      const y = y0 + ((j + 0.5) / N) * (y1 - y0);
      if (z.ui.some((r) => inside(r, x, y))) ui++;
      if (z.nogo.some((r) => inside(r, x, y))) ng++;
    }
  return { ui: ui / (N * N), nogo: ng / (N * N) };
}

/**
 * Drag the logo on a phone frame to place it; drag the corner to resize. The red areas are where
 * Instagram/YouTube draw their own buttons and text (from the app's safe-zone data).
 */
export function LogoPlacer({
  campaignId,
  version,
  logo,
  zones,
  platform,
  title,
  end,
  captions,
  onChange,
}: {
  title?: boolean;
  /** show where the end text goes */
  end?: boolean;
  /** caption band (centre y and height, fractions); draggable up/down when onChange is given */
  captions?: { y: number; h: number; onChange?: (y: number) => void };
  campaignId: string;
  version: number;
  logo: Logo;
  zones: SafeZones | null | undefined;
  platform: Platform;
  onChange: (patch: Partial<Logo>) => void;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ mode: "move" | "size" | "cap"; sx: number; sy: number; x: number; y: number; w: number } | null>(null);
  const [active, setActive] = useState(false);
  const z = zones?.[platform];
  const free = logo.mode === "free";
  const r = logoRect(logo);
  const box = (rect: Rect, cls: string, key: string, label?: string) => (
    <div key={key} className={`lp-zone ${cls}`} style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${(rect[2] - rect[0]) * 100}%`, height: `${(rect[3] - rect[1]) * 100}%` }}>
      {label && <span>{label}</span>}
    </div>
  );
  const down = (mode: "move" | "size") => (e: React.PointerEvent) => {
    if (!free) return;
    e.preventDefault();
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { mode, sx: e.clientX, sy: e.clientY, x: logo.x, y: logo.y, w: logo.width };
    setActive(true);
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    const f = frame.current?.getBoundingClientRect();
    if (!d || !f) return;
    const dx = (e.clientX - d.sx) / f.width;
    const dy = (e.clientY - d.sy) / f.height;
    if (d.mode === "cap") {
      captions?.onChange?.(Math.round(Math.max(0.1, Math.min(0.92, d.y + dy)) * 100) / 100);
      return;
    }
    if (d.mode === "size") {
      onChange({ width: Math.round(Math.max(0.08, Math.min(1, d.w + dx * 2)) * 1000) / 1000 });
    } else {
      const [rx0, ry0, rx1, ry1] = logoRect({ ...logo, x: d.x, y: d.y });
      const hw = (rx1 - rx0) / 2;
      const hh = (ry1 - ry0) / 2;
      const nx = Math.max(hw, Math.min(1 - hw, d.x + dx));
      const ny = Math.max(hh, Math.min(1 - hh, d.y + dy));
      onChange({ x: Math.round(nx * 1000) / 1000, y: Math.round(ny * 1000) / 1000 });
    }
  };
  const up = () => {
    drag.current = null;
    setActive(false);
  };
  // keyboard nudging for accessibility: arrows move, +/- resize
  const key = (e: React.KeyboardEvent) => {
    if (!free) return;
    const step = e.shiftKey ? 0.05 : 0.01;
    const m: Record<string, Partial<Logo>> = {
      ArrowLeft: { x: Math.max(0, logo.x - step) },
      ArrowRight: { x: Math.min(1, logo.x + step) },
      ArrowUp: { y: Math.max(0, logo.y - step) },
      ArrowDown: { y: Math.min(1, logo.y + step) },
      "+": { width: Math.min(1, logo.width + step) },
      "=": { width: Math.min(1, logo.width + step) },
      "-": { width: Math.max(0.08, logo.width - step) },
    };
    if (m[e.key]) {
      e.preventDefault();
      onChange(m[e.key]);
    }
  };
  return (
    <div className={`lp-frame ${active ? "dragging" : ""}`} ref={frame} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
      <div className="lp-scene" aria-hidden="true" />
      {captions ? (
        <div
          className={`lp-cap ${captions.onChange ? "movable" : ""}`}
          style={{ top: `${(captions.y - captions.h / 2) * 100}%`, height: `${captions.h * 100}%` }}
          role={captions.onChange ? "slider" : undefined}
          tabIndex={captions.onChange ? 0 : undefined}
          aria-label={captions.onChange ? "Caption position. Drag up or down, or use the arrow keys." : undefined}
          aria-valuenow={captions.onChange ? Math.round(captions.y * 100) : undefined}
          aria-valuemin={captions.onChange ? 10 : undefined}
          aria-valuemax={captions.onChange ? 92 : undefined}
          aria-hidden={captions.onChange ? undefined : true}
          onKeyDown={(e) => {
            if (!captions.onChange || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
            e.preventDefault();
            const step = (e.shiftKey ? 0.05 : 0.01) * (e.key === "ArrowUp" ? -1 : 1);
            captions.onChange(Math.round(Math.max(0.1, Math.min(0.92, captions.y + step)) * 100) / 100);
          }}
          onPointerDown={(e) => {
            if (!captions.onChange) return;
            e.preventDefault();
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
            drag.current = { mode: "cap", sx: e.clientX, sy: e.clientY, x: 0, y: captions.y, w: 0 };
            setActive(true);
          }}
        >
          <span>
            YOUR <i>CAPTIONS</i>
          </span>
        </div>
      ) : (
        <div className="lp-cap" style={{ top: "60%" }} aria-hidden="true">
          <span>
            YOUR <i>CAPTION</i>
          </span>
        </div>
      )}
      {z?.ui.map((rect, i) => box(rect, "ui", `ui${i}`, i === 0 ? "app buttons & text" : undefined))}
      {z?.nogo.map((rect, i) => box(rect, "nogo", `ng${i}`))}
      {title && box(TITLE_AREA, "title", "title", "title text (first seconds)")}
      {end && box(END_AREA, "title", "end", "end text (last seconds)")}
      {logo.mode === "template" && logo.has_file && <img className="lp-template" src={media.logo(campaignId, version)} alt="Brand's logo template" />}
      {free && (
        <div
          className="lp-logo"
          role="slider"
          tabIndex={0}
          aria-label="Logo position. Arrow keys move it, plus and minus resize it."
          aria-valuetext={`${Math.round(logo.x * 100)}% across, ${Math.round(logo.y * 100)}% down, ${Math.round(logo.width * 100)}% wide`}
          onKeyDown={key}
          onPointerDown={down("move")}
          style={{ left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${(r[2] - r[0]) * 100}%`, height: `${(r[3] - r[1]) * 100}%`, opacity: logo.opacity }}
        >
          {logo.has_file ? <img src={media.logo(campaignId, version)} alt="" draggable={false} /> : <span className="lp-ph">LOGO</span>}
          <span className="lp-handle" onPointerDown={down("size")} aria-hidden="true" />
        </div>
      )}
      {logo.mode === "template" && !logo.has_file && (
        <div className="lp-empty">
          <Icon name="image" size={22} />
          <span>Upload the brand's template file</span>
        </div>
      )}
    </div>
  );
}

export function CoverageStatus({ logo, zones, platforms, title }: { logo: Logo; zones: SafeZones | null | undefined; platforms: Platform[]; title?: boolean }) {
  if (logo.mode !== "free") return null;
  const clash = title && overlapsTitle(logo) ? (
    <div className="alert warn" style={{ marginTop: 8 }}>
      <Icon name="alert" />
      <div className="alert-body">The logo sits where the title text goes (dashed box). The title text moves just below the logo automatically; move the logo if you'd rather keep the title at the top.</div>
    </div>
  ) : null;
  const plats = platforms.length ? platforms : (["reels", "shorts"] as Platform[]);
  const worst = plats.map((p) => ({ p, ...coverage(logo, zones, p) })).sort((a, b) => b.ui + b.nogo - (a.ui + a.nogo))[0];
  if (!worst) return null;
  const ui = Math.round(worst.ui * 100);
  const ng = Math.round(worst.nogo * 100);
  const name = worst.p === "reels" ? "Instagram" : "YouTube";
  if (ui === 0 && ng === 0)
    return (
      <>
        <div className="alert success">
          <Icon name="check" />
          <div className="alert-body">
            <b>Fully visible.</b> No app buttons cover the logo on {plats.length > 1 ? "Reels or Shorts" : name}.
          </div>
        </div>
        {clash}
      </>
    );
  const fail = worst.ui > logo.max_ui_cover || worst.nogo > logo.max_nogo_overlap;
  return (
    <>
    <div className={`alert ${fail ? "danger" : "warn"}`}>
      <Icon name="alert" />
      <div className="alert-body">
        <b>
          {ui}% of the logo is under {name}'s buttons{ng ? `, ${ng}% where it's always covered` : ""}.
        </b>{" "}
        {fail ? `The brand allows at most ${Math.round(logo.max_ui_cover * 100)}% (and ${Math.round(logo.max_nogo_overlap * 100)}% in the dark red area). Drag it out of the red.` : "Within the brand's limits, but clearer if you move it."}
      </div>
    </div>
    {clash}
    </>
  );
}
