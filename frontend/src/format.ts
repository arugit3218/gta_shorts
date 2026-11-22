export function fmtTime(sec: number | null | undefined, withMs = false): string {
  if (sec == null || !isFinite(sec)) return "–";
  const s = Math.max(0, sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const ss = withMs ? r.toFixed(1).padStart(4, "0") : Math.floor(r).toString().padStart(2, "0");
  return h ? `${h}:${m.toString().padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return "–";
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ${Math.round(sec % 60)}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function fmtMB(mb: number | null | undefined): string {
  if (mb == null) return "–";
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  if (mb >= 10) return `${Math.round(mb)} MB`;
  return `${mb.toFixed(1)} MB`;
}

export function fmtAgo(ts: number): string {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function scoreTone(score: number): "excellent" | "strong" | "review" | "low" {
  if (score >= 85) return "excellent";
  if (score >= 70) return "strong";
  if (score >= 55) return "review";
  return "low";
}
