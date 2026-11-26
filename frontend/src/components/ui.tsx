import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { scoreTone } from "../format";
import type { Job, Stage } from "../types";
import { Icon, type IconName } from "./Icon";

// ---------------------------------------------------------------- toasts
type ToastAction = { label: string; onClick: () => void };
type Toast = { id: number; text: string; kind: "info" | "error"; action?: ToastAction };
type PushToast = (text: string, kind?: "info" | "error", opts?: { action?: ToastAction }) => void;
const ToastCtx = createContext<PushToast>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback<PushToast>((text, kind = "info", opts) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind, action: opts?.action }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : opts?.action ? 6000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <span>{t.text}</span>
            {t.action && (
              <button
                className="toast-action"
                onClick={() => {
                  t.action?.onClick();
                  setToasts((all) => all.filter((x) => x.id !== t.id));
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---------------------------------------------------------------- modal
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Dialog: Esc closes, focus moves inside (and back afterwards), Tab stays inside.
 *  ``dirty``: the backdrop doesn't close it (so a click outside can't lose typed input). */
// open dialogs, newest last: only the top one reacts to Esc / Tab
const modalStack: number[] = [];
let modalSeq = 0;

export function Modal({ title, children, onClose, actions, wide, dirty }: { title: string; children: ReactNode; onClose: () => void; actions: ReactNode; wide?: boolean; dirty?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    const myId = ++modalSeq;
    modalStack.push(myId);
    const before = document.activeElement as HTMLElement | null;
    const first = box.current?.querySelector<HTMLElement>("[autofocus], input, textarea, select") ?? box.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (modalStack.at(-1) !== myId) return;
      if (e.key === "Escape") {
        if (document.querySelector(".help-pop")) return; // an open "?" tip closes first
        e.stopPropagation();
        if (!dirtyRef.current) closeRef.current(); // typed text isn't thrown away by Esc
      } else if (e.key === "Tab" && box.current) {
        const items = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE));
        if (!items.length) return;
        const [a, b] = [items[0], items[items.length - 1]];
        if (e.shiftKey && document.activeElement === a) (e.preventDefault(), b.focus());
        else if (!e.shiftKey && document.activeElement === b) (e.preventDefault(), a.focus());
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      const i = modalStack.indexOf(myId);
      if (i >= 0) modalStack.splice(i, 1);
      before?.focus?.();
    };
  }, []);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && !dirty && onClose()} role="dialog" aria-modal="true" aria-label={title}>
      <div className={`card modal ${wide ? "wide" : ""}`} ref={box}>
        <h2>{title}</h2>
        <div className="stack">{children}</div>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          {actions}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- confirm / prompt (instead of the browser's pop-ups)
type ConfirmOpts = { title: string; body?: ReactNode; confirm?: string; cancel?: string; danger?: boolean };
type PromptOpts = { title: string; body?: ReactNode; label?: string; initial?: string; placeholder?: string; confirm?: string };
type Pending =
  | { kind: "confirm"; opts: ConfirmOpts; resolve: (v: boolean) => void }
  | { kind: "prompt"; opts: PromptOpts; resolve: (v: string | null) => void };
const DialogCtx = createContext<{ confirm: (o: ConfirmOpts) => Promise<boolean>; prompt: (o: PromptOpts) => Promise<string | null> }>({
  confirm: async () => false,
  prompt: async () => null,
});

export function DialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [text, setText] = useState("");
  const confirm = useCallback((opts: ConfirmOpts) => new Promise<boolean>((resolve) => setPending({ kind: "confirm", opts, resolve })), []);
  const prompt = useCallback(
    (opts: PromptOpts) =>
      new Promise<string | null>((resolve) => {
        setText(opts.initial ?? "");
        setPending({ kind: "prompt", opts, resolve });
      }),
    [],
  );
  const done = (ok: boolean) => {
    if (!pending) return;
    if (pending.kind === "confirm") pending.resolve(ok);
    else pending.resolve(ok && text.trim() ? text.trim() : null);
    setPending(null);
  };
  return (
    <DialogCtx.Provider value={{ confirm, prompt }}>
      {children}
      {pending && (
        <Modal
          title={pending.opts.title}
          onClose={() => done(false)}
          actions={
            <>
              <button className="btn" onClick={() => done(false)}>
                {(pending.kind === "confirm" && pending.opts.cancel) || "Cancel"}
              </button>
              <button
                className={`btn ${pending.kind === "confirm" && pending.opts.danger ? "danger-solid" : "primary"}`}
                onClick={() => done(true)}
                disabled={pending.kind === "prompt" && !text.trim()}
                autoFocus={pending.kind === "confirm"}
              >
                {pending.opts.confirm ?? "OK"}
              </button>
            </>
          }
        >
          {pending.opts.body && <div className="small">{pending.opts.body}</div>}
          {pending.kind === "prompt" && (
            <div className="field">
              {pending.opts.label && <label htmlFor="dlg-prompt">{pending.opts.label}</label>}
              <input
                id="dlg-prompt"
                className="input"
                value={text}
                placeholder={pending.opts.placeholder}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && text.trim() && done(true)}
              />
            </div>
          )}
        </Modal>
      )}
    </DialogCtx.Provider>
  );
}
export const useDialogs = () => useContext(DialogCtx);

// ---------------------------------------------------------------- time left
const fmtSecs = (s: number) => (s < 60 ? `${Math.max(1, Math.round(s))}s` : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);

/** "1:20 elapsed · ~2 min left", estimated from progress so far (shown once there's enough to go on). */
export function JobTime({ job }: { job: Pick<Job, "started_at" | "progress" | "status"> }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((x) => x + 1), 1000);
    return () => window.clearInterval(t);
  }, []);
  if (job.status !== "running" || !job.started_at) return job.status === "queued" ? <span className="small muted">Waiting for the current job…</span> : null;
  const elapsed = Date.now() / 1000 - job.started_at;
  const p = job.progress;
  const left = p > 0.04 && elapsed > 4 ? (elapsed / p) * (1 - p) : null;
  return (
    <span className="small muted nowrap">
      {fmtSecs(elapsed)} elapsed{left != null ? ` · ~${left < 60 ? `${Math.max(5, Math.round(left / 5) * 5)}s` : `${Math.round(left / 60)} min`} left` : " · estimating…"}
    </span>
  );
}

/** Run a server action; show its error as a toast instead of failing silently. */
export async function attempt(toast: (msg: string, kind?: "info" | "error") => void, fn: () => Promise<unknown>): Promise<boolean> {
  try {
    await fn();
    return true;
  } catch (e) {
    toast((e as Error).message, "error");
    return false;
  }
}

export const plural = (n: number, word: string, many?: string) => `${n} ${n === 1 ? word : many ?? word + "s"}`;

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
      {status === "new" ? "not started" : status}
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
