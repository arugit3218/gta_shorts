export type StageStatus = "pending" | "running" | "done" | "skipped" | "failed";

export interface Stage {
  label: string;
  status: StageStatus;
  progress: number;
  message: string;
}

export interface Job {
  id: string;
  project_id?: string;
  kind: "process" | "render";
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string | null;
  progress: number;
  message: string | null;
  stages: Record<string, Stage>;
  error: string | null;
  payload: { clip_id?: string };
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface ProcessOptions {
  duration_preset?: string;
  max_clips?: number;
  min_score?: number;
  aspect_ratio?: AspectRatio;
  layout?: Layout;
  caption_preset?: string;
  output_height?: 720 | 1080;
  auto_render?: boolean;
  llm_enabled?: boolean;
}

export type AspectRatio = "9:16" | "1:1" | "16:9";
export type Layout = "auto" | "center" | "blur" | "split";

export interface Project {
  id: string;
  name: string;
  source_type: "url" | "upload" | "path";
  source_url: string | null;
  source_name: string | null;
  source_available: boolean;
  source_owned: boolean;
  duration: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  status: "new" | "queued" | "processing" | "ready" | "failed" | "cancelled";
  error: string | null;
  options: ProcessOptions;
  created_at: number;
  updated_at: number;
  clip_count: number;
  rendered_count: number;
  approved_count: number;
  best_score: number | null;
  thumb_clip_id: string | null;
  job: Job | null;
  has_audio?: boolean;
}

export interface ClipAnalysis {
  scores: Record<string, number>;
  criteria: Record<string, string>;
  weights: Record<string, number>;
  reasons: string[];
  weaknesses: string[];
  label: string;
  heuristic_score: number;
  transcript: string;
  sentences: { start: number; end: number; text: string }[];
  llm: boolean;
  layout_used?: string;
  face_ratio?: number;
}

export interface CaptionStyle {
  name: string;
  font: string;
  size: number;
  color: string;
  highlight_color: string;
  outline_color: string;
  outline: number;
  shadow: number;
  bold: boolean;
  uppercase: boolean;
  position: number;
  max_words: number;
  max_lines: number;
  animation: "none" | "highlight" | "pop" | "fade" | "karaoke";
  background: boolean;
  background_color: string;
}

export interface ClipEdit {
  aspect_ratio: AspectRatio;
  layout: Layout;
  caption_preset: string;
  caption_style?: Partial<CaptionStyle>;
  output_height: 720 | 1080;
  captions: boolean;
  has_caption_edits: boolean;
  music: string | null;
  music_volume?: number;
}

export interface Clip {
  id: string;
  project_id: string;
  rank: number;
  start: number;
  end: number;
  duration: number;
  score: number;
  title: string;
  hook: string;
  description: string;
  category: string;
  status: "candidate" | "approved" | "rejected";
  render_status: "pending" | "queued" | "rendering" | "done" | "failed" | "stale";
  render_error: string | null;
  analysis: ClipAnalysis;
  hooks: string[];
  hashtags: string[];
  edit: ClipEdit;
  has_video: boolean;
  has_thumb: boolean;
  version: number;
}

export interface Word {
  w: string;
  s: number;
  e: number;
}

export interface ClipWords {
  start: number;
  end: number;
  words: Word[];
  context_words: Word[];
  edited: boolean;
  source_duration: number;
}

export interface Settings {
  mode: "low" | "balanced" | "high";
  work_dir: string;
  transcription_model: string;
  transcription_chunk_seconds: number;
  output_height: 720 | 1080;
  aspect_ratio: AspectRatio;
  layout: Layout;
  caption_preset: string;
  duration_preset: string;
  max_clips: number;
  min_score: number;
  auto_render: boolean;
  keep_intermediates: boolean;
  normalise_audio: boolean;
  max_download_height: 720 | 1080;
  min_free_gb: number;
  weights: Record<string, number>;
  thresholds: { excellent: number; strong: number; review: number };
  llm_enabled: boolean;
  llm_url: string;
  llm_model: string;
}

export interface SystemStatus {
  mode: string;
  memory: { total_mb: number; available_mb: number; percent: number };
  disk: { path: string; total_mb: number; free_mb: number; min_free_mb: number; low: boolean };
  tools: { ffmpeg: boolean; ffprobe: boolean };
  worker: { current_job: string | null; queued: number };
  work_dir: string;
  privacy: { bind: string; model_downloads: boolean; llm_enabled: boolean };
}

export interface Dashboard {
  projects: number;
  videos_processed: number;
  clips: number;
  clips_rendered: number;
  clips_approved: number;
  minutes_processed: number;
  recent: Project[];
  active_jobs: Job[];
  disk_free_mb: number;
}

export interface UrlInfo {
  url: string;
  title: string;
  duration: number;
  width: number | null;
  height: number | null;
  thumbnail: string | null;
  uploader: string | null;
  extractor: string | null;
  estimated_bytes: number | null;
  estimate: { source_mb: number; temp_mb: number; output_mb: number; total_mb: number };
  free_mb: number;
  existing_project: { id: string; name: string } | null;
}

export interface ModelsInfo {
  transcription: { id: string; label: string; disk_gb: number; ram_gb: number; languages: string[]; installed: boolean }[];
  selected: string;
  llm: { enabled: boolean; available: boolean; status: string; model: string; url: string };
  ram_total_mb: number;
  downloads_allowed: boolean;
}

export interface StorageInfo {
  work_dir: string;
  free_mb: number;
  total_mb: number;
  downloads_mb: number;
  uploads_mb: number;
  cache_mb: number;
  temp_mb: number;
  partial_mb: number;
  orphans: { name: string; mb: number }[];
  orphan_project_dirs: { name: string; mb: number }[];
  projects: { id: string; name: string; clips_mb: number; other_mb: number; source_mb: number; source_owned: boolean; source_exists: boolean }[];
}
