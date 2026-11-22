"""Request bodies for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..config import AspectRatio, DurationPreset, Layout


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


class InspectUrl(BaseModel):
    url: str = Field(max_length=2048)


class CreateUrlProject(BaseModel):
    url: str = Field(max_length=2048)
    name: str | None = Field(None, max_length=200)
    options: ProcessOptions = ProcessOptions()
    start: bool = True


class ImportPath(BaseModel):
    path: str = Field(max_length=4096)
    name: str | None = Field(None, max_length=200)


class StartProcess(BaseModel):
    options: ProcessOptions = ProcessOptions()


class CaptionWord(BaseModel):
    w: str = Field(max_length=80)
    s: float = Field(ge=0)
    e: float = Field(ge=0)


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

    @field_validator("font")
    @classmethod
    def _font(cls, v: str | None) -> str | None:
        if v is not None and not all(ch.isalnum() or ch in " -_" for ch in v):
            raise ValueError("invalid font name")
        return v


class ClipEdit(BaseModel):
    aspect_ratio: AspectRatio | None = None
    layout: Layout | None = None
    caption_preset: str | None = None
    caption_style: CaptionStyleOverrides | None = None
    output_height: Literal[720, 1080] | None = None
    captions: bool | None = None
    music_volume: float | None = Field(None, ge=0, le=1)


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
