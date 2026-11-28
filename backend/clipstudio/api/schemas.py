"""Request bodies for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..config import AspectRatio, DurationPreset, Layout


class CaptionStyleOverrides(BaseModel):
    font: str | None = Field(None, max_length=80)
    size: float | None = Field(None, ge=0.02, le=0.12)
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    outline: float | None = Field(None, ge=0, le=0.03)
    shadow: float | None = Field(None, ge=0, le=0.03)
    bold: bool | None = None
    uppercase: bool | None = None
    position: float | None = Field(None, ge=0.05, le=0.95)
    max_words: int | None = Field(None, ge=1, le=12)
    max_lines: int | None = Field(None, ge=1, le=3)
    animation: Literal["none", "highlight", "pop", "fade", "karaoke"] | None = None
    background: bool | None = None
    emphasis_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    glow: bool | None = None

    @field_validator("font")
    @classmethod
    def _font(cls, v: str | None) -> str | None:
        if v is not None and not all(ch.isalnum() or ch in " -_" for ch in v):
            raise ValueError("invalid font name")
        return v


class PolishPatch(BaseModel):
    """Retention polish (Phase 4A); omitted fields keep the project / app default."""

    jumpcut: Literal["off", "light", "normal", "tight"] | None = None
    fillers: bool | None = None
    zoom: Literal["off", "subtle", "punchy"] | None = None
    emphasis: bool | None = None
    emoji: bool | None = None
    progress: Literal["off", "top", "bottom"] | None = None
    hook_style: Literal["box", "dark", "yellow", "banner", "bold", "outline"] | None = None
    cold_open: bool | None = None
    profanity: Literal["off", "beep", "mute"] | None = None
    denoise: Literal["off", "light", "strong"] | None = None


class ProcessOptions(BaseModel):
    duration_preset: DurationPreset | None = None
    max_clips: int | None = Field(None, ge=1, le=30)
    min_score: float | None = Field(None, ge=0, le=100)
    aspect_ratio: AspectRatio | None = None
    layout: Layout | None = None
    caption_preset: str | None = None
    output_height: Literal[720, 1080] | None = None
    auto_render: bool | None = None
    llm_enabled: bool | None = None
    music: Literal["none", "auto"] | None = None
    sfx: Literal["auto", "off"] | None = None
    blur_burned: Literal["auto", "off"] | None = None
    hook_overlay: bool | None = None
    quick: bool | None = None  # quick clip mode: one clip from your own in/out (or the whole file)
    quick_start: float | None = Field(None, ge=0)
    quick_end: float | None = Field(None, ge=0)
    polish: PolishPatch | None = None
    caption_style: CaptionStyleOverrides | None = None  # from a recipe


class InspectUrl(BaseModel):
    url: str = Field(max_length=2048)


class CreateUrlProject(BaseModel):
    url: str = Field(max_length=2048)
    name: str | None = Field(None, max_length=200)
    options: ProcessOptions = ProcessOptions()
    start: bool = True
    campaign_id: str | None = Field(None, max_length=40)
    allow_duplicate: bool = False  # the same video is already in this campaign: make another copy anyway


class BulkUrls(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)
    options: ProcessOptions = ProcessOptions()
    campaign_id: str | None = Field(None, max_length=40)
    start: bool = True  # False: add them to the campaign without processing yet ("not started")


class ImportPath(BaseModel):
    path: str = Field(max_length=4096)
    name: str | None = Field(None, max_length=200)
    campaign_id: str | None = Field(None, max_length=40)


class StreamLayout(BaseModel):
    """Webcam-over-gameplay layout for a stream (fractions of the source frame)."""

    cam: list[float] | None = Field(None, min_length=4, max_length=4)  # [x0, y0, x1, y1]; None = detect automatically
    cam_height: float = Field(0.38, ge=0.2, le=0.6)  # share of the vertical frame for the webcam
    game_x: float = Field(0.5, ge=0, le=1)  # horizontal focus of the gameplay crop
    cam_top: bool = True

    @field_validator("cam")
    @classmethod
    def _box(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return v
        x0, y0, x1, y1 = (max(0.0, min(1.0, float(a))) for a in v)
        if x1 - x0 < 0.03 or y1 - y0 < 0.03:
            raise ValueError("webcam box is too small")
        return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


class ProjectPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    campaign_id: str | None = Field(None, max_length=40)  # "" removes the campaign
    stream_layout: StreamLayout | None = None
    clear_stream_layout: bool = False


class BulkClipEdit(BaseModel):
    """Apply framing/format to many clips at once and (optionally) re-render them."""

    layout: Layout | None = None
    aspect_ratio: AspectRatio | None = None
    output_height: Literal[720, 1080] | None = None
    scope: Literal["all", "approved", "unrejected"] = "unrejected"
    ids: list[str] | None = Field(None, max_length=500)  # only these clips (overrides scope)
    render: bool = True
    recipe_id: str | None = Field(None, max_length=40)  # apply a saved recipe's look to every clip
    caption_position: float | None = Field(None, ge=0.1, le=0.92)  # where captions sit (centre, fraction of height)
    caption_position_auto: bool = False  # drop the clips' own caption position: back to the campaign / look, clear of the logo
    captions: bool | None = None  # turn captions off (False) or back on (True) for these clips



class ManualClip(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    title: str | None = Field(None, max_length=200)
    snap: bool = True
    render: bool = True


class AiPicks(BaseModel):
    text: str = Field(max_length=200_000)
    replace: bool = False
    render: bool = True


class StartProcess(BaseModel):
    options: ProcessOptions = ProcessOptions()
    keep_clips: bool = False  # re-processing: keep approved and hand-made (manual / AI pick) clips


class CaptionWord(BaseModel):
    w: str = Field(max_length=80)
    s: float = Field(ge=0)
    e: float = Field(ge=0)


class HookOverlayEdit(BaseModel):
    # every field optional: only what the user changed is stored, the rest follows the campaign / defaults
    enabled: bool | None = None
    text: str | None = Field(None, max_length=160)
    duration: float | None = Field(None, ge=1, le=10)
    style: Literal["box", "dark", "yellow", "banner", "bold", "outline"] | None = None


class EndTextEdit(BaseModel):
    # every field optional, like the title text: what isn't set follows the campaign
    enabled: bool | None = None
    text: str | None = Field(None, max_length=160)
    duration: float | None = Field(None, ge=1, le=6)
    style: Literal["box", "dark", "yellow", "banner", "bold", "outline"] | None = None
    look: Literal["overlay", "card"] | None = None


class SfxCue(BaseModel):
    type: Literal["pop", "whoosh", "hit", "riser", "ding"]
    t: float = Field(ge=0, le=600)


class PostOverrides(BaseModel):
    reels: str | None = Field(None, max_length=2200)
    shorts: str | None = Field(None, max_length=5000)


class ClipEdit(BaseModel):
    aspect_ratio: AspectRatio | None = None
    layout: Layout | None = None
    caption_preset: str | None = None
    caption_style: CaptionStyleOverrides | None = None
    output_height: Literal[720, 1080] | None = None
    captions: bool | None = None
    music_volume: float | None = Field(None, ge=0, le=1)
    # none | auto | upload | mood:<mood> | lib:<mood>/<file>
    music: str | None = Field(None, max_length=300, pattern=r"^(none|auto|upload|mood:[a-z]+|lib:[a-z]+/[^/\\]+)$")
    sfx: Literal["auto", "off", "custom"] | None = None
    sfx_cues: list[SfxCue] | None = Field(None, max_length=12)
    blur_burned: Literal["auto", "off"] | None = None
    hook_overlay: HookOverlayEdit | None = None
    end_text: EndTextEdit | None = None
    logo: bool | None = None
    disclosure: bool | None = None
    sound_id: str | None = Field(None, max_length=40)
    sound_volume: float | None = Field(None, ge=0, le=1.5)
    original_volume: float | None = Field(None, ge=0, le=1)
    post: PostOverrides | None = None
    polish: PolishPatch | None = None


class PostMark(BaseModel):
    url: str | None = Field(None, max_length=500)


class ClipPatch(BaseModel):
    title: str | None = Field(None, max_length=200)
    hook: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=2000)
    hashtags: list[str] | None = Field(None, max_length=15)
    status: Literal["candidate", "approved", "rejected"] | None = None
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)
    edit: ClipEdit | None = None
    caption_words: list[CaptionWord] | None = Field(None, max_length=2000)
    reset_captions: bool = False
    remove_music: bool = False


class PreviewFrame(BaseModel):
    t: float = Field(0.5, ge=0)
    edit: ClipEdit | None = None
    caption_words: list[CaptionWord] | None = None


class Cleanup(BaseModel):
    targets: list[Literal["temp", "partial", "cache", "orphans"]] = ["temp", "partial", "orphans"]


class RecipeBody(BaseModel):
    """A saved look ("recipe"): captions, framing, finishing and retention polish."""

    name: str = Field(min_length=1, max_length=80)
    caption_preset: str | None = Field(None, max_length=40)
    caption_style: CaptionStyleOverrides | None = None
    layout: Layout | None = None
    aspect_ratio: AspectRatio | None = None
    output_height: Literal[720, 1080] | None = None
    music: Literal["none", "auto"] | None = None
    sfx: Literal["auto", "off"] | None = None
    blur_burned: Literal["auto", "off"] | None = None
    hook_overlay: bool | None = None
    polish: PolishPatch = PolishPatch()


class ClipActions(BaseModel):
    """Act on several clips at once (project page selection)."""

    ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["approve", "reject", "unreview", "render"]
