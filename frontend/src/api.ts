import type {
  Brief,
  BriefSummary,
  CampaignType,
  Platform,
  PostedMap,
  Campaign,
  CampaignPreset,
  Compliance,
  LibraryInfo,
  SafeZones,
  CaptionStyle,
  Clip,
  ClipEdit,
  ClipWords,
  Dashboard,
  ExistingVideo,
  Job,
  Layout,
  StreamLayout,
  MontageOptions,
  ModelsInfo,
  ProcessOptions,
  Recipe,
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
  createUrlProject: (url: string, options: ProcessOptions, name?: string, campaign_id?: string | null, allow_duplicate = false) =>
    req<Project>("POST", "/projects", { url, options, name, start: true, campaign_id: campaign_id || null, allow_duplicate }),
  /** Links already in this campaign come back under `existing` instead of being added twice. */
  createBulk: (urls: string[], options: ProcessOptions, campaign_id?: string | null, start = true) =>
    req<{ created: Project[]; errors: { url: string; error: string }[]; existing: { url: string; project: ExistingVideo }[] }>("POST", "/projects/bulk", { urls, options, campaign_id: campaign_id || null, start }),
  uploadProject: (file: File, onProgress?: (p: number) => void, signal?: AbortSignal, campaign_id?: string | null) =>
    uploadRaw<Project>(`/projects/upload?filename=${encodeURIComponent(file.name)}${campaign_id ? `&campaign_id=${campaign_id}` : ""}`, file, onProgress, signal),
  importPath: (path: string, campaign_id?: string | null) => req<Project>("POST", "/projects/import-path", { path, campaign_id: campaign_id || null }),
  patchProject: (id: string, patch: { name?: string; campaign_id?: string; stream_layout?: StreamLayout; clear_stream_layout?: boolean }) => req<Project>("PATCH", `/projects/${id}`, patch),
  detectFacecam: (id: string) => req<{ cam: [number, number, number, number] | null; samples: number; found: boolean }>("POST", `/projects/${id}/detect-facecam`),
  bulkClips: (id: string, body: { layout?: Layout; aspect_ratio?: string; output_height?: 720 | 1080; scope?: "all" | "approved" | "unrejected"; ids?: string[]; render?: boolean; recipe_id?: string; caption_position?: number; caption_position_auto?: boolean }) =>
    req<{ updated: number; rendering: number }>("POST", `/projects/${id}/clips/bulk`, body),
  addClip: (id: string, body: { start: number; end: number; title?: string; render?: boolean }) => req<Clip>("POST", `/projects/${id}/clips`, body),
  aiPrompt: (id: string) => req<{ prompt: string; chars: number; sentences: number }>("GET", `/projects/${id}/ai-prompt`),
  aiPicks: (id: string, text: string, replace: boolean) => req<{ created: number; clips: Clip[]; problems: string[] }>("POST", `/projects/${id}/ai-picks`, { text, replace, render: true }),
  estimate: (id: string) => req<{ estimate: { total_mb: number; output_mb: number; temp_mb: number }; free_mb: number; ok: boolean; min_free_mb: number }>("GET", `/projects/${id}/estimate`),
  process: (id: string, options: ProcessOptions, keep_clips = false) => req<{ job_id: string; project: Project }>("POST", `/projects/${id}/process`, { options, keep_clips }),
  clipActions: (projectId: string, ids: string[], action: "approve" | "reject" | "unreview" | "render") =>
    req<{ updated: number; rendering: number }>("POST", `/projects/${projectId}/clips/actions`, { ids, action }),
  sourceInfo: (clipId: string) => req<{ available: boolean; start?: number; end?: number; reason?: string }>("GET", `/clips/${clipId}/source-info`),
  deleteProject: (id: string, opts: { delete_exports: boolean; delete_source: boolean; delete_footage?: boolean }) =>
    req<{ freed_mb: number; exports_kept_at: string | null; footage_deleted: number }>(
      "DELETE",
      `/projects/${id}?delete_exports=${opts.delete_exports}&delete_source=${opts.delete_source}&delete_footage=${!!opts.delete_footage}`,
    ),
  deleteSource: (id: string) => req<{ freed_mb: number }>("POST", `/projects/${id}/delete-source`),
  clips: (projectId: string) => req<Clip[]>("GET", `/projects/${projectId}/clips`),
  clip: (id: string) => req<Clip>("GET", `/clips/${id}`),
  clipWords: (id: string) => req<ClipWords>("GET", `/clips/${id}/words`),
  patchClip: (
    id: string,
    patch: Partial<Pick<Clip, "title" | "hook" | "description" | "hashtags" | "status" | "start" | "end">> & {
      edit?: Partial<Omit<ClipEdit, "has_caption_edits" | "music_file">>;
      caption_words?: Word[];
      reset_captions?: boolean;
      remove_music?: boolean;
    },
  ) => req<Clip>("PATCH", `/clips/${id}`, patch),
  renderClip: (id: string) => req<{ job_id: string }>("POST", `/clips/${id}/render`),
  previewFrame: (id: string, body: { t: number; edit?: Partial<ClipEdit>; caption_words?: Word[] }) => req<Blob>("POST", `/clips/${id}/preview-frame`, body),
  uploadMusic: (id: string, file: File) => uploadRaw<Clip>(`/clips/${id}/music?filename=${encodeURIComponent(file.name)}`, file),
  captionPresets: () => req<Record<string, CaptionStyle>>("GET", "/caption-presets"),
  compliance: (id: string) => req<Compliance>("GET", `/clips/${id}/compliance`),
  exportClip: (id: string) => req<{ path: string; verdict: string; files: string[] }>("POST", `/clips/${id}/export`),
  markPosted: (id: string, platform: Platform, url?: string) => req<{ posted: PostedMap }>("PUT", `/clips/${id}/posts/${platform}`, { url: url || null }),
  unmarkPosted: (id: string, platform: Platform) => req<{ posted: PostedMap }>("DELETE", `/clips/${id}/posts/${platform}`),
  refreshClipStats: (id: string) => req<{ updated: number; failed: number; posted: PostedMap }>("POST", `/clips/${id}/posts/stats`),
  refreshCampaignStats: (id: string) => req<{ updated: number; failed: number }>("POST", `/campaigns/${id}/posts/stats`),
  // campaigns
  campaignPresets: () => req<CampaignPreset[]>("GET", "/campaign-presets"),
  safeZones: () => req<SafeZones>("GET", "/safe-zones"),
  campaigns: (include_archived = false) => req<Campaign[]>("GET", `/campaigns?include_archived=${include_archived}`),
  campaign: (id: string) => req<Campaign>("GET", `/campaigns/${id}`),
  createCampaign: (body: { type?: CampaignType; preset?: string; name?: string; text?: string; brief?: Partial<Brief> }) => req<Campaign>("POST", "/campaigns", body),
  rerenderOutdated: (id: string) => req<{ rendering: number }>("POST", `/campaigns/${id}/rerender-outdated`),
  previewCampaign: (type: CampaignType, text: string) => req<{ brief: Brief; summary: BriefSummary; found: string[] }>("POST", "/campaigns/preview", { type, text }),
  captionPreview: (brief: Partial<Brief>, platform: Platform) => req<{ caption: string; title: string; hashtags: string[]; warnings: string[] }>("POST", "/campaigns/caption-preview", { brief, platform }),
  patchCampaign: (id: string, body: { name?: string; status?: "active" | "archived"; brief?: Partial<Brief> | Record<string, unknown> }) => req<Campaign>("PATCH", `/campaigns/${id}`, body),
  deleteCampaign: (id: string) => req<{ deleted: boolean }>("DELETE", `/campaigns/${id}`),
  parseBrief: (text: string) => req<{ patch: Partial<Brief>; found: string[] }>("POST", "/campaigns/parse", { text }),
  applyBriefText: (id: string, text: string) => req<Campaign>("POST", `/campaigns/${id}/apply-text`, { text }),
  uploadLogo: (id: string, file: File) => uploadRaw<Campaign>(`/campaigns/${id}/logo?filename=${encodeURIComponent(file.name)}`, file),
  deleteLogo: (id: string) => req<Campaign>("DELETE", `/campaigns/${id}/logo`),
  uploadSound: (id: string, file: File, name: string) => uploadRaw<Campaign>(`/campaigns/${id}/sounds?filename=${encodeURIComponent(file.name)}&name=${encodeURIComponent(name)}`, file),
  addSoundRef: (id: string, name: string) => req<Campaign>("POST", `/campaigns/${id}/sounds/ref`, { name }),
  deleteSound: (id: string, sid: string) => req<Campaign>("DELETE", `/campaigns/${id}/sounds/${sid}`),
  exportApproved: (id: string) => req<{ exported: number; results: { clip_id: string; title: string; path?: string; verdict?: string; error?: string }[]; folder: string }>("POST", `/campaigns/${id}/export-approved`),
  // library
  addMontageLinks: (urls: string[], campaign_id?: string | null) => req<{ created: Project[]; errors: { url: string; error: string }[] }>("POST", "/montages/sources", { urls, campaign_id: campaign_id || null }),
  createMontage: (body: { name: string; campaign_id?: string | null; options: Partial<MontageOptions>; start?: boolean }) => req<Project>("POST", "/montages", body),
  patchMontage: (id: string, body: { name?: string; options?: Partial<MontageOptions> }) => req<Project>("PATCH", `/montages/${id}`, body),
  uploadSong: (id: string, file: File) => uploadRaw<Project>(`/montages/${id}/song?filename=${encodeURIComponent(file.name)}`, file),
  songInfo: (id: string) => req<{ name: string; duration: number; tempo: number; drop: number | null; drop_strength: number; beats: number; pinned_start: number | null }>("GET", `/montages/${id}/song-info`),
  replanMontage: (clipId: string, body: { pov?: string; audio?: "song" | "silent"; flash?: boolean; style?: "chill" | "normal" | "hype"; drop_at?: number; reshuffle?: boolean; render?: boolean }) =>
    req<Clip>("POST", `/clips/${clipId}/montage`, body),
  newVariant: (id: string) => req<Clip>("POST", `/montages/${id}/variant`),
  library: () => req<LibraryInfo>("GET", "/library"),
  addMusic: (mood: string, file: File) => uploadRaw<LibraryInfo>(`/library/music/${mood}?filename=${encodeURIComponent(file.name)}`, file),
  deleteMusic: (mood: string, name: string) => req<LibraryInfo>("DELETE", `/library/music/${mood}/${encodeURIComponent(name)}`),
  addSfx: (kind: string, file: File) => uploadRaw<LibraryInfo>(`/library/sfx/${kind}?filename=${encodeURIComponent(file.name)}`, file),
  deleteSfx: (kind: string, name: string) => req<LibraryInfo>("DELETE", `/library/sfx/${kind}/${encodeURIComponent(name)}`),
  jobs: (params: { project_id?: string; active?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (params.project_id) q.set("project_id", params.project_id);
    if (params.active) q.set("active", "true");
    return req<Job[]>("GET", `/jobs?${q}`);
  },
  cancelJob: (id: string) => req<{ cancelling: boolean }>("POST", `/jobs/${id}/cancel`),
  // recipes (saved looks)
  recipes: () => req<Recipe[]>("GET", "/recipes"),
  createRecipe: (body: Omit<Recipe, "id" | "created_at" | "updated_at">) => req<Recipe>("POST", "/recipes", body),
  updateRecipe: (id: string, body: Omit<Recipe, "id" | "created_at" | "updated_at">) => req<Recipe>("PUT", `/recipes/${id}`, body),
  deleteRecipe: (id: string) => req<{ deleted: boolean }>("DELETE", `/recipes/${id}`),
  applyRecipe: (clipId: string, recipe_id: string, render = false) => req<Clip>("POST", `/clips/${clipId}/apply-recipe`, { recipe_id, render }),
  saveRecipeFromClip: (clipId: string, name: string) => req<Recipe>("POST", `/clips/${clipId}/save-recipe`, { name }),
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
  clipSource: (clipId: string) => `/api/clips/${clipId}/source`,
  clipsZip: (projectId: string, ids?: string[]) => `/api/projects/${projectId}/clips.zip${ids?.length ? `?ids=${ids.join(",")}` : ""}`,
  frame: (projectId: string, t?: number, v?: number) => `/api/projects/${projectId}/frame?${t != null ? `t=${t}&` : ""}v=${v ?? 0}`,
  exportZip: (clipId: string) => `/api/clips/${clipId}/export.zip`,
  logo: (campaignId: string, v?: number, full?: boolean) => `/api/campaigns/${campaignId}/logo?v=${v ?? 0}${full ? "&full=true" : ""}`,
  sound: (campaignId: string, sid: string) => `/api/campaigns/${campaignId}/sounds/${sid}/audio`,
  music: (mood: string, name: string) => `/api/library/music/${mood}/${encodeURIComponent(name)}`,
  sfx: (kind: string) => `/api/sfx/${kind}`,
  transcript: (projectId: string, fmt: "srt" | "json") => `/api/projects/${projectId}/transcript.${fmt}`,
};
