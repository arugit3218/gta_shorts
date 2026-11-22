import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { scoreTone } from "../format";
import type { Job, Stage } from "../types";
import { Icon, type IconName } from "./Icon";

// ---------------------------------------------------------------- toasts
type Toast = { id: number; text: string; kind: "info" | "error" };
const ToastCtx = createContext<(text: string, kind?: "info" | "error") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "info" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---------------------------------------------------------------- modal
export function Modal({ title, children, onClose, actions }: { title: string; children: ReactNode; onClose: () => void; actions: ReactNode }) {
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="dialog" aria-modal="true" aria-label={title}>
      <div className="card modal">
        <h2>{title}</h2>
        <div className="stack">{children}</div>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          {actions}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- bits
export function Spinner({ size = 16 }: { size?: number }) {
  return <Icon name="loader" size={size} className="spin" />;
}

export function Alert({ kind = "info", title, children }: { kind?: "info" | "warn" | "danger" | "success"; title?: string; children?: ReactNode }) {
  const icon: IconName = kind === "success" ? "check" : kind === "info" ? "info" : "alert";
  return (
    <div className={`alert ${kind}`}>
      <Icon name={icon} />
      <div className="stack sm alert-body">
        {title && <strong>{title}</strong>}
        {children && <div>{children}</div>}
      </div>
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: IconName; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="big-icon">
        <Icon name={icon} size={24} />
      </div>
      <h3 style={{ color: "var(--text)" }}>{title}</h3>
      {children && <div style={{ marginTop: 8 }}>{children}</div>}
    </div>
  );
}

export function Bar({ value, tone }: { value: number; tone?: "success" | "warn" | "danger" }) {
  return (
    <div className={`bar ${tone ?? ""}`} role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}

const TONE_FROM_LABEL: Record<string, ReturnType<typeof scoreTone>> = {
  "Excellent candidate": "excellent",
  "Strong candidate": "strong",
  "Needs review": "review",
  "Low priority": "low",
};
export const toneOf = (score: number, label?: string) => (label && TONE_FROM_LABEL[label]) || scoreTone(score);

export function ScoreRing({ score, label }: { score: number; label?: string }) {
  const tone = toneOf(score, label);
  const color = { excellent: "var(--success)", strong: "var(--info)", review: "var(--warn)", low: "var(--text-3)" }[tone];
  const r = 22;
  const c = 2 * Math.PI * r;
  return (
    <div className="score-ring" title={label}>
      <svg width="52" height="52" viewBox="0 0 52 52">
        <circle cx="26" cy="26" r={r} fill="none" stroke="var(--bg-sunken)" strokeWidth="5" />
        <circle cx="26" cy="26" r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" strokeDasharray={`${(score / 100) * c} ${c}`} />
      </svg>
      <div className="num">{Math.round(score)}</div>
    </div>
  );
}

export const STATUS_BADGE: Record<string, string> = {
  new: "",
  queued: "info",
  processing: "accent",
  ready: "success",
  failed: "danger",
  cancelled: "warn",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge ${STATUS_BADGE[status] ?? ""}`}>
      {status === "processing" && <Spinner size={11} />}
      {status}
    </span>
  );
}

// ---------------------------------------------------------------- stage list
export function StageList({ job }: { job: Job }) {
  const entries = Object.entries(job.stages) as [string, Stage][];
  return (
    <div className="stages">
      {entries.map(([key, s]) => (
        <div key={key} className={`stage ${s.status}`}>
          <div className="stage-dot">
            {s.status === "done" && <Icon name="check" size={14} strokeWidth={3} />}
            {s.status === "running" && <Spinner size={14} />}
            {s.status === "failed" && <Icon name="x" size={14} strokeWidth={3} />}
            {s.status === "skipped" && <Icon name="check" size={12} />}
          </div>
          <div className="stack sm" style={{ gap: 4 }}>
            <div className="row">
              <span className="name">{s.label}</span>
              {s.status === "skipped" && <span className="badge">skipped</span>}
            </div>
            {s.status === "running" && <Bar value={s.progress} />}
            {s.message && <span className={`small ${s.status === "failed" ? "" : "muted"}`} style={s.status === "failed" ? { color: "var(--danger)" } : undefined}>{s.message}</span>}
          </div>
          <span className="small muted nowrap">{s.status === "running" && s.progress > 0 ? `${Math.round(s.progress * 100)}%` : ""}</span>
        </div>
      ))}
    </div>
  );
}
