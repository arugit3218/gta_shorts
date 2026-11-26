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
  music?: "none" | "auto";
  sfx?: "auto" | "off";
  blur_burned?: "auto" | "off";
  hook_overlay?: boolean;
  quick?: boolean;
  quick_start?: number;
  quick_end?: number;
  stream_layout?: StreamLayout;
  content_type?: ContentType;
  montage?: MontageOptions;
  polish?: Partial<Polish>;
  caption_style?: Partial<CaptionStyle>;
}

export type HookStyle = "box" | "dark" | "yellow" | "banner" | "bold" | "outline";

/** Retention polish (Phase 4A). */
export interface Polish {
  jumpcut: "off" | "light" | "normal" | "tight";
  fillers: boolean;
  zoom: "off" | "subtle" | "punchy";
  emphasis: boolean;
  emoji: boolean;
  progress: "off" | "top" | "bottom";
  hook_style: HookStyle;
  cold_open: boolean;
  profanity: "off" | "beep" | "mute";
  denoise: "off" | "light" | "strong";
}

export interface Recipe {
  id: string;
  name: string;
  caption_preset?: string;
  caption_style?: Partial<CaptionStyle>;
  layout?: Layout;
  aspect_ratio?: AspectRatio;
  output_height?: 720 | 1080;
  music?: "none" | "auto";
  sfx?: "auto" | "off";
  blur_burned?: "auto" | "off";
  hook_overlay?: boolean;
  polish: Partial<Polish>;
  created_at: number;
  updated_at: number;
}

export type ContentType = "auto" | "talk" | "gameplay";

export interface MontageOptions {
  length: number;
  drop_at: number;
  style: "chill" | "normal" | "hype";
  pov: string;
  variants: number;
  audio: "song" | "silent";
  flash: boolean;
  sources: string[];
  song: { kind: "campaign" | "upload"; sound_id?: string | null; use_cut?: boolean; start?: number | null; path?: string; name?: string };
}

export type AspectRatio = "9:16" | "1:1" | "16:9";
export type Layout = "auto" | "center" | "blur" | "split" | "stream";

export interface StreamLayout {
  cam: [number, number, number, number] | null;
  cam_height: number;
  game_x: number;
  cam_top: boolean;
  detected?: boolean;
}

export interface Project {
  id: string;
  name: string;
  /** active: started (or a montage) · not_started: added but never started · footage: only used as montage source */
  stage: "active" | "not_started" | "footage";
  used_in: { id: string; name: string }[];
  source_type: "url" | "upload" | "path" | "montage";
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
  campaign_id: string | null;
  campaign_name: string | null;
  video_mode: "full" | "sections" | "missing" | "montage";
  content_mode?: "talk" | "gameplay" | null;
  insights: { heatmap: boolean; chapters: number; creator_subtitles: boolean };
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
  origin?: "auto" | "quick" | "manual" | "ai" | "montage";
  mode?: "speech" | "action" | "montage";
  peak_t?: number;
  song?: string;
  song_start?: number;
  burned_text?: { y0: number; y1: number; presence: number; intervals: [number, number][] } | null;
  sfx_used?: SfxCue[];
  polish_used?: { cuts: number; removed: number; zooms: number; emphasis: number; emoji: number; censored: number; cold_open?: [number, number] };
  output_duration?: number;
  music_used?: string | null;
  output?: string;
  stream?: StreamLayout | null;
}

export type SfxType = "pop" | "whoosh" | "hit" | "riser" | "ding";
export interface SfxCue {
  type: SfxType;
  t: number;
}

export interface HookOverlay {
  enabled: boolean;
  text: string;
  duration: number;
}

export interface ClipFeatures {
  music: string;
  music_volume: number;
  sfx: "auto" | "off" | "custom";
  sfx_cues: SfxCue[];
  blur_burned: "auto" | "off";
  voice_enhance: boolean;
  hook_overlay: HookOverlay;
  logo: boolean;
  disclosure: boolean;
  sound_id: string | null;
  sound_volume: number;
  original_volume: number;
  locked: string[];
  polish: Polish;
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
  emphasis_color?: string;
  emphasis_scale?: number;
  glow?: boolean;
}

export interface ClipEdit {
  aspect_ratio: AspectRatio;
  layout: Layout;
  caption_preset: string;
  caption_style?: Partial<CaptionStyle>;
  output_height: 720 | 1080;
  captions: boolean;
  has_caption_edits: boolean;
  music?: string | null;
  music_file?: string | null;
  music_volume?: number;
  sfx?: "auto" | "off" | "custom";
  sfx_cues?: SfxCue[];
  blur_burned?: "auto" | "off";
  hook_overlay?: { enabled: boolean; text?: string | null; duration: number; style?: HookStyle | null };
  polish?: Partial<Polish>;
  logo?: boolean;
  disclosure?: boolean;
  sound_id?: string | null;
  sound_volume?: number;
  original_volume?: number;
  post?: { reels?: string | null; shorts?: string | null };
  montage?: { pov: string; audio: "song" | "silent"; flash: boolean; style: "chill" | "normal" | "hype"; drop_at: number; seed: number };
  montage_plan?: MontagePlan;
}

export interface MontagePlan {
  song_start: number;
  song_end: number;
  length: number;
  tempo: number;
  drop: number | null;
  style: string;
  seed: number;
  drop_at: number;
  slots: { at: number; dur: number; file: string; t: number; mx: number; label: string; energy: number; impact: boolean; moment: string }[];
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
  features?: ClipFeatures;
  exported?: { path: string; at: number; verdict: string } | null;
  /** Platforms it was marked as posted on (with the post link, if one was added). */
  posted?: PostedMap;
  /** Platforms its campaign posts to (both without a campaign). */
  post_platforms?: Platform[];
}

export type PostedMap = Partial<Record<Platform, { url: string | null; at: number }>>;

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
  source_offset: number;
  source_range: [number, number];
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
  download_strategy: "smart" | "full";
  use_heatmap: boolean;
  use_creator_subtitles: boolean;
  music_default: "none" | "auto";
  sfx_default: boolean;
  blur_burned_default: "auto" | "off";
  hook_overlay_default: boolean;
  voice_enhance: boolean;
  auto_fallback: "fill" | "blur";
  auto_stream: boolean;
  follow_action: boolean;
  content_type: ContentType;
  action_weights: { audio: number; motion: number; reaction: number; hud: number };
  polish: Polish;
}

export interface SystemStatus {
  mode: string;
  memory: { total_mb: number; available_mb: number; percent: number };
  disk: { path: string; total_mb: number; free_mb: number; min_free_mb: number; low: boolean };
  tools: { ffmpeg: boolean; ffprobe: boolean };
  worker: { current_job: string | null; queued: number };
  work_dir: string;
  privacy: { bind: string; model_downloads: boolean; llm_enabled: boolean };
  downloads: { js_runtime: string | null; ejs: boolean; youtube_ready: boolean };
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
  campaigns: number;
  todo: {
    review: { project_id: string; name: string; campaign: string | null; n: number }[];
    render: { project_id: string; name: string; campaign: string | null; n: number }[];
    failed_renders: { id: string; title: string; project_id: string }[];
    ready: { campaign_id: string | null; name: string | null; n: number }[];
    failed_videos: { id: string; name: string; error: string | null }[];
    not_started: number;
    campaign_setup: { campaign_id: string; name: string; missing: string[] }[];
  };
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
  library_mb: number;
  exports_mb: number;
  campaigns_mb: number;
  orphans: { name: string; mb: number }[];
  orphan_project_dirs: { name: string; mb: number }[];
  projects: { id: string; name: string; stage?: "active" | "not_started" | "footage"; clips_mb: number; other_mb: number; source_mb: number; source_owned: boolean; source_exists: boolean; smart?: boolean }[];
}

// ------------------------------------------------------------------ campaigns
export type CampaignType = "clipping" | "logo" | "music" | "ugc";
export type Platform = "reels" | "shorts";
export type LogoZone = "any" | "top_half" | "bottom_half" | "top" | "middle" | "bottom" | "top_left" | "top_right" | "bottom_left" | "bottom_right";
export type Corner = "top_left" | "top_right" | "bottom_left" | "bottom_right";

export interface LogoSpec {
  mode: "none" | "template" | "free";
  file_name: string | null;
  has_file?: boolean;
  animated: boolean;
  image_w: number | null;
  image_h: number | null;
  zone: LogoZone;
  x: number;
  y: number;
  width: number;
  opacity: number;
  max_ui_cover: number;
  max_nogo_overlap: number;
}

export interface SoundSpec {
  id: string;
  name: string;
  file_name: string | null;
  has_file?: boolean;
  start: number;
  duration: number | null;
}

export interface Brief {
  name: string;
  source: string;
  preset: string;
  type: CampaignType;
  url: string;
  cpm: number | null;
  min_views: number | null;
  max_payout: number | null;
  min_engagement: number | null;
  keep_live_days: number | null;
  platforms: Platform[];
  min_duration: number | null;
  max_duration: number | null;
  output_height: 720 | 1080;
  logo: LogoSpec;
  sounds: SoundSpec[];
  sound_rule: { mode: "none" | "in_app" | "burn"; min_volume: number; original_volume: number };
  disclosure: { text: string; on_screen: boolean; in_caption: boolean; corner: Corner };
  hook_overlay: { enabled: boolean; duration: number; text: string };
  allow_music: boolean;
  allow_sfx: boolean;
  english_only: boolean;
  caption_template: string;
  required_hashtags: string[];
  required_mentions: string[];
  banned_keywords: string[];
  content_notes: string;
  rules: string[];
  checklist: string[];
  raw_brief: string;
  required_caption_text: string;
  brief_sources: Record<string, string>;
  /** how this campaign's videos are made (Style template key) */
  style: string;
  intensity: "" | "clean" | "balanced" | "punchy";
  max_clips: number | null;
  /** overrides of the style ("" = the style's choice) */
  layout: "" | Layout;
  caption_preset: string;
}

export interface BriefSummary {
  video: { key: string; label: string; detail: string; quote: string | null; todo: string | null }[];
  reference: { key: string; label: string; value: string; quote: string | null }[];
  checklist: string[];
  rules: string[];
}

export interface ReadyClip {
  id: string;
  project_id: string;
  title: string;
  duration: number;
  verdict: "pass" | "warn" | "fail" | "unknown";
  fails: number;
  updated_at: number;
  exported: boolean;
  project_name: string;
  has_video: boolean;
  render_status: Clip["render_status"];
  posted: PostedMap;
  /** Caption (and YouTube title) per platform; only for clips still to post. */
  captions?: Partial<Record<Platform, { caption: string; title: string; warnings: string[] }>>;
}

export interface Campaign {
  id: string;
  name: string;
  status: "active" | "archived";
  brief: Brief;
  created_at: number;
  updated_at: number;
  projects: number;
  clips: number;
  rendered: number;
  approved: number;
  exported: number;
  project_list?: Project[];
  ready?: ReadyClip[];
  ready_total?: number;
  posted_clips?: ReadyClip[];
  posted_total?: number;
  post_platforms?: Platform[];
  outdated?: number;
  summary?: BriefSummary | null;
  exports?: { id: string; clip_id: string; path: string; created_at: number; title: string | null; project_id: string | null }[];
  found?: string[];
  notes?: string[];
}

export interface CampaignPreset {
  key: string;
  label: string;
  description: string;
  brief: Brief;
}

export type Rect = [number, number, number, number];
export type SafeZones = Record<Platform, { label: string; ui: Rect[]; nogo: Rect[] }>;

export interface ComplianceCheck {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | "manual" | "info";
  detail: string;
}

export interface PostText {
  platform: Platform;
  title: string;
  caption: string;
  hashtags: string[];
  warnings: string[];
  edited?: boolean;
}

export interface Compliance {
  verdict: "pass" | "warn" | "fail";
  fails: number;
  warnings: number;
  manual: number;
  checks: ComplianceCheck[];
  campaign: { id: string; name: string } | null;
  posts: Record<string, PostText>;
  checked_at: number;
}

export interface LibraryInfo {
  moods: string[];
  music: Record<string, { name: string; mood: string; mb: number }[]>;
  sfx_types: SfxType[];
  sfx: Record<string, { name: string; mb: number }[]>;
  path: string;
}
