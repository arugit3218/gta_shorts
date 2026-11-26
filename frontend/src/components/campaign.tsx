import { useEffect, useState } from "react";
import { api } from "../api";
import { usePoll } from "../hooks";
import type { Compliance, ComplianceCheck, Platform, Rect, SafeZones } from "../types";
import { Icon, type IconName } from "./Icon";
import { Spinner } from "./ui";

export const PLATFORM_LABEL: Record<Platform, string> = { reels: "Instagram Reels", shorts: "YouTube Shorts" };
export const TYPE_LABEL: Record<string, string> = { clipping: "Clips", logo: "Logo", music: "Music", ugc: "UGC" };

/** Platform UI / no-go overlay drawn over a 9:16 preview (A10). */
export function SafeZoneOverlay({ zones, platforms, allowed }: { zones?: SafeZones | null; platforms: Platform[]; allowed?: Rect | null }) {
  if (!zones) return null;
  const box = (r: Rect, cls: string, key: string) => (
    <div key={key} className={`zone ${cls}`} style={{ left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${(r[2] - r[0]) * 100}%`, height: `${(r[3] - r[1]) * 100}%` }} />
  );
  return (
    <div className="zones" aria-hidden="true">
      {allowed && box(allowed, "allowed", "allowed")}
      {platforms.flatMap((p) => zones[p]?.ui.map((r, i) => box(r, "ui", `${p}-ui-${i}`)) ?? [])}
      {platforms.flatMap((p) => zones[p]?.nogo.map((r, i) => box(r, "nogo", `${p}-ng-${i}`)) ?? [])}
    </div>
  );
}

export function ZoneLegend({ allowed }: { allowed?: boolean }) {
  return (
    <div className="zone-legend">
      <span>
        <i className="lg-ui" />
        Partly covered by app buttons
      </span>
      <span>
        <i className="lg-nogo" />
        Always covered
      </span>
      {allowed && (
        <span>
          <i style={{ border: "2px dashed var(--success)" }} />
          Allowed logo zone
        </span>
      )}
    </div>
  );
}

export function useSafeZones() {
  return usePoll(api.safeZones, []).data;
}

const CHECK_ICON: Record<ComplianceCheck["status"], IconName> = { pass: "check", warn: "alert", fail: "x", manual: "clipboard", info: "info" };

export function Verdict({ v }: { v: Compliance["verdict"] }) {
  const text = v === "pass" ? "Ready to post" : v === "warn" ? "Check warnings" : "Fix before posting";
  return (
    <span className={`verdict ${v}`}>
      <Icon name={v === "pass" ? "check" : "alert"} size={13} /> {text}
    </span>
  );
}

export function CheckList({ checks, tickKey }: { checks: ComplianceCheck[]; tickKey?: string }) {
  const order = { fail: 0, warn: 1, manual: 3, pass: 2, info: 4 } as const;
  const sorted = [...checks].sort((a, b) => order[a.status] - order[b.status]);
  // "You confirm" steps can be ticked; ticks are remembered per clip on this Mac
  const storeKey = tickKey ? `clipstudio.ticks.${tickKey}` : null;
  const [ticks, setTicks] = useState<string[]>(() => {
    if (!storeKey) return [];
    try {
      return JSON.parse(localStorage.getItem(storeKey) || "[]");
    } catch {
      return [];
    }
  });
  const toggle = (label: string) => {
    const next = ticks.includes(label) ? ticks.filter((x) => x !== label) : [...ticks, label];
    setTicks(next);
    try {
      if (storeKey) localStorage.setItem(storeKey, JSON.stringify(next));
    } catch {
      /* private mode */
    }
  };
  const manual = sorted.filter((c) => c.status === "manual");
  const done = manual.filter((c) => ticks.includes(c.label)).length;
  return (
    <div className="check-list">
      {sorted.map((c, i) =>
        c.status === "manual" && storeKey ? (
          <label key={`${c.id}-${i}`} className={`check-item manual tickable ${ticks.includes(c.label) ? "ticked" : ""}`}>
            <input type="checkbox" checked={ticks.includes(c.label)} onChange={() => toggle(c.label)} />
            <span>
              <strong style={{ fontWeight: 600 }}>{c.label}</strong>
              {c.detail && <span className="muted"> · {c.detail}</span>}
            </span>
          </label>
        ) : (
          <div key={`${c.id}-${i}`} className={`check-item ${c.status}`}>
            <span className="ic">
              <Icon name={CHECK_ICON[c.status]} size={12} strokeWidth={3} />
            </span>
            <span>
              <strong style={{ fontWeight: 600 }}>{c.label}</strong>
              {c.detail && <span className="muted"> · {c.detail}</span>}
              {c.status === "manual" && <span className="badge" style={{ marginLeft: 6 }}>you confirm</span>}
            </span>
          </div>
        ),
      )}
      {storeKey && manual.length > 0 && (
        <span className={`small ${done === manual.length ? "" : "muted"}`} style={{ marginTop: 4 }}>
          {done === manual.length ? "✓ All your steps are ticked." : `${done} of ${manual.length} of your steps ticked`}
        </span>
      )}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked */
        }
      }}
    >
      <Icon name={done ? "check" : "copy"} /> {done ? "Copied" : label}
    </button>
  );
}

export function CampaignSelect({ value, onChange, allowNone = true, disabled }: { value: string | null | undefined; onChange: (id: string) => void; allowNone?: boolean; disabled?: boolean }) {
  const q = usePoll(() => api.campaigns(), []);
  const missing = !!value && !!q.data && !q.data.some((c) => c.id === value);
  const cur = usePoll(() => (missing && value ? api.campaign(value) : Promise.resolve(null)), [missing, value]).data;
  if (!q.data) return <Spinner />;
  return (
    <select className="select" value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
      {allowNone && <option value="">No campaign</option>}
      {missing && <option value={value!}>{cur ? `${cur.name} (archived)` : "Archived campaign"}</option>}
      {q.data.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
          {c.brief.source ? ` · ${c.brief.source}` : ""} ({TYPE_LABEL[c.brief.type]})
        </option>
      ))}
    </select>
  );
}

export function ListEditor({ items, onChange, placeholder }: { items: string[]; onChange: (v: string[]) => void; placeholder: string }) {
  const [v, setV] = useState("");
  const add = () => {
    const t = v.trim();
    if (t && !items.includes(t)) onChange([...items, t]);
    setV("");
  };
  return (
    <div className="stack sm">
      {items.length > 0 && (
        <div className="chips">
          {items.map((x) => (
            <span className="chip" key={x}>
              {x}
              <button aria-label={`Remove ${x}`} onClick={() => onChange(items.filter((y) => y !== x))}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <input className="input" value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())} onBlur={add} />
    </div>
  );
}

export function LinesEditor({ items, onChange, placeholder, rows = 4 }: { items: string[]; onChange: (v: string[]) => void; placeholder: string; rows?: number }) {
  const [text, setText] = useState(items.join("\n"));
  // follow outside changes (e.g. a pasted brief) unless the text already says the same thing
  const joined = items.join("\n");
  useEffect(() => {
    setText((t) => (t.split("\n").map((l) => l.trim()).filter(Boolean).join("\n") === joined ? t : joined));
  }, [joined]);
  return (
    <textarea
      className="textarea"
      rows={rows}
      value={text}
      placeholder={placeholder}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => onChange(text.split("\n").map((l) => l.trim()).filter(Boolean))}
    />
  );
}
