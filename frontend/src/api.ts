import type {
  CaptionStyle,
  Clip,
  ClipEdit,
  ClipWords,
  Dashboard,
  Job,
  ModelsInfo,
  ProcessOptions,
  Project,
  Settings,
  StorageInfo,
  SystemStatus,
  UrlInfo,
  Word,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function req<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    ...init,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = j.error || (Array.isArray(j.detail) ? j.detail.map((d: { msg: string }) => d.msg).join("; ") : j.detail) || msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(msg, res.status);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : res.blob()) as Promise<T>;
}

/** Raw-body upload with progress (XHR, since fetch has no upload progress). */
function uploadRaw<T>(path: string, file: File, onProgress?: (p: number) => void, signal?: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let data: { error?: string } = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(data.error || `Upload failed (${xhr.status})`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("Network error during upload", 0));
    xhr.onabort = () => reject(new ApiError("Upload cancelled", 0));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(file);
  });
}

export const api = {
  system: () => req<SystemStatus>("GET", "/system"),
  dashboard: () => req<Dashboard>("GET", "/dashboard"),
  projects: () => req<Project[]>("GET", "/projects"),
  project: (id: string) => req<Project>("GET", `/projects/${id}`),
  inspectUrl: (url: string) => req<UrlInfo>("POST", "/sources/inspect-url", { url }),
  createUrlProject: (url: string, options: ProcessOptions, name?: string) => req<Project>("POST", "/projects", { url, options, name, start: true }),
  uploadProject: (file: File, onProgress?: (p: number) => void, signal?: AbortSignal) =>
    uploadRaw<Project>(`/projects/upload?filename=${encodeURIComponent(file.name)}`, file, onProgress, signal),
  importPath: (path: string) => req<Project>("POST", "/projects/import-path", { path }),
  estimate: (id: string) => req<{ estimate: { total_mb: number; output_mb: number; temp_mb: number }; free_mb: number; ok: boolean; min_free_mb: number }>("GET", `/projects/${id}/estimate`),
  process: (id: string, options: ProcessOptions) => req<{ job_id: string; project: Project }>("POST", `/projects/${id}/process`, { options }),
  deleteProject: (id: string, opts: { delete_exports: boolean; delete_source: boolean }) =>
    req<{ freed_mb: number; exports_kept_at: string | null }>("DELETE", `/projects/${id}?delete_exports=${opts.delete_exports}&delete_source=${opts.delete_source}`),
  deleteSource: (id: string) => req<{ freed_mb: number }>("POST", `/projects/${id}/delete-source`),
  clips: (projectId: string) => req<Clip[]>("GET", `/projects/${projectId}/clips`),
  clip: (id: string) => req<Clip>("GET", `/clips/${id}`),
  clipWords: (id: string) => req<ClipWords>("GET", `/clips/${id}/words`),
  patchClip: (
    id: string,
    patch: Partial<Pick<Clip, "title" | "hook" | "description" | "hashtags" | "status" | "start" | "end">> & {
      edit?: Partial<Omit<ClipEdit, "has_caption_edits" | "music">>;
      caption_words?: Word[];
      reset_captions?: boolean;
      remove_music?: boolean;
    },
  ) => req<Clip>("PATCH", `/clips/${id}`, patch),
  renderClip: (id: string) => req<{ job_id: string }>("POST", `/clips/${id}/render`),
  previewFrame: (id: string, body: { t: number; edit?: Partial<ClipEdit>; caption_words?: Word[] }) => req<Blob>("POST", `/clips/${id}/preview-frame`, body),
  uploadMusic: (id: string, file: File) => uploadRaw<Clip>(`/clips/${id}/music?filename=${encodeURIComponent(file.name)}`, file),
  captionPresets: () => req<Record<string, CaptionStyle>>("GET", "/caption-presets"),
  jobs: (params: { project_id?: string; active?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (params.project_id) q.set("project_id", params.project_id);
    if (params.active) q.set("active", "true");
    return req<Job[]>("GET", `/jobs?${q}`);
  },
  cancelJob: (id: string) => req<{ cancelling: boolean }>("POST", `/jobs/${id}/cancel`),
  settings: () => req<Settings>("GET", "/settings"),
  saveSettings: (patch: Partial<Settings>) => req<Settings>("PUT", "/settings", patch),
  models: () => req<ModelsInfo>("GET", "/models"),
  storage: () => req<StorageInfo>("GET", "/storage"),
  cleanup: (targets: string[]) => req<{ freed_mb: number }>("POST", "/storage/cleanup", { targets }),
};

export const media = {
  video: (c: Clip) => `/api/clips/${c.id}/video?v=${c.version}`,
  thumb: (c: { id: string; version?: number }) => `/api/clips/${c.id}/thumb?v=${c.version ?? 0}`,
  download: (c: Clip) => `/api/clips/${c.id}/download`,
  source: (projectId: string) => `/api/projects/${projectId}/source`,
  transcript: (projectId: string, fmt: "srt" | "json") => `/api/projects/${projectId}/transcript.${fmt}`,
};
