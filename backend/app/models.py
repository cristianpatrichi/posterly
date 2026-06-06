"""Pydantic v2 schemas for the collage backend.

This module holds request/response models ONLY. No filesystem or rendering IO
lives here -- those belong in ``storage`` and ``render_service`` respectively.

The shapes here mirror the persisted ``project.json`` contract exactly so the
frontend (Task 3) and Docker packaging (Task 4) have a stable interface.
"""

from __future__ import annotations

from typing import Literal, Optional

from PIL import ImageColor
from pydantic import BaseModel, ConfigDict, Field, field_validator


Orientation = Literal["landscape", "portrait"]
Look = Literal["soft-oval", "paper"]
OrderMode = Literal["random", "manual"]
ExportFormat = Literal["png", "pdf", "both"]
PaperSize = Literal[
    "A5", "A4", "A3", "A2", "A1", "A0",
    "letter", "legal",
    "30x40cm", "50x70cm", "70x100cm", "100x100cm", "100x140cm",
]


class Settings(BaseModel):
    """Project-wide collage settings.

    The slider-style fields (``spacing``, ``rotation_intensity``, ``border``,
    ``feather``) are normalized floats in ``0..1``; ``render_service`` maps them
    to concrete collage/render parameters at auto-layout and export time.
    """

    model_config = ConfigDict(extra="ignore")

    orientation: Orientation = "landscape"
    paper_size: PaperSize = "A4"
    look: Look = "soft-oval"
    order_mode: OrderMode = "random"
    spacing: float = Field(default=0.5, ge=0.0, le=1.0)
    rotation_intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    border: float = Field(default=0.5, ge=0.0, le=1.0)
    feather: float = Field(default=0.5, ge=0.0, le=1.0)
    background: str = "#f4efe6"
    seed: Optional[int] = 7
    # Preview-only dashed margin guide on the paper: a dashed rectangle inset
    # ``margin_guide_mm`` from each paper edge, shown in the web UI to help frame
    # content. It is NEVER rendered into the export (render_service ignores it).
    margin_guide: bool = False
    margin_guide_mm: float = Field(default=10.0, ge=0.0, le=1000.0)

    @field_validator("background")
    @classmethod
    def _validate_background(cls, value: str) -> str:
        """Reject colors PIL/the renderer cannot parse (-> 422, not a 500).

        Accepts normal hex (``#f4efe6``) and CSS color names (``white``); junk
        like ``"not-a-color"`` raises a validation error. This is defense in
        depth alongside the export handler, which also catches color failures.
        """
        try:
            ImageColor.getrgb(value)
        except ValueError as exc:
            raise ValueError(f"invalid background color: {value!r}") from exc
        return value


class ImageMeta(BaseModel):
    """A stored uploaded image and its natural (EXIF-transposed) pixel size.

    Note: ``name`` is the original, client-supplied filename and is NOT
    sanitized here. The frontend must escape it when rendering (treat as text;
    never feed it to ``dangerouslySetInnerHTML``).
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    filename: str
    name: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class ImageOut(ImageMeta):
    """An image as returned to the client, with fetchable URLs.

    ``url`` is the full-resolution original; ``preview_url`` is a small WebP proxy
    for fast on-screen rendering (same aspect ratio, so the crop matches export).
    """

    url: str
    preview_url: str


class LayoutItem(BaseModel):
    """One placed photo in normalized canvas coordinates (0..1, center-anchored)."""

    model_config = ConfigDict(extra="ignore")

    image_id: str
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    rotation: float = 0.0
    z_index: int = 0
    look: Look = "soft-oval"
    # Optional per-photo glow/feather override (0..1). None => use settings.feather.
    feather: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class Project(BaseModel):
    """The full persisted project document (``project.json``)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    settings: Settings = Field(default_factory=Settings)
    images: list[ImageMeta] = Field(default_factory=list)
    layout: list[LayoutItem] = Field(default_factory=list)


class ProjectOut(BaseModel):
    """Project as returned by the API: images carry a ``url`` for the frontend."""

    model_config = ConfigDict(extra="ignore")

    id: str
    settings: Settings
    images: list[ImageOut] = Field(default_factory=list)
    layout: list[LayoutItem] = Field(default_factory=list)


class UpdateProjectRequest(BaseModel):
    """Body for ``PUT /api/projects/{id}``.

    All fields are optional so the caller can save only settings, only the
    layout, or only reorder images; the handler merges into the persisted
    document. ``image_order`` is a list of image ids giving the new ordering;
    the strip uses it so a manual-order auto-layout follows the chosen sequence.
    """

    model_config = ConfigDict(extra="ignore")

    settings: Optional[Settings] = None
    layout: Optional[list[LayoutItem]] = None
    image_order: Optional[list[str]] = None


class DeleteImagesRequest(BaseModel):
    """Body for ``POST /api/projects/{id}/images/delete`` (batch delete)."""

    model_config = ConfigDict(extra="ignore")

    image_ids: list[str] = Field(default_factory=list)


class AutoLayoutRequest(BaseModel):
    """Body for ``POST /api/projects/{id}/auto-layout``.

    Optional ``settings`` are applied (persisted) before regenerating the layout.
    """

    model_config = ConfigDict(extra="ignore")

    settings: Optional[Settings] = None


class ExportRequest(BaseModel):
    """Body for ``POST /api/projects/{id}/export``."""

    model_config = ConfigDict(extra="ignore")

    format: ExportFormat = "both"


class ExportResponse(BaseModel):
    """Result of an export: download URLs plus readiness flags per format."""

    png_url: str
    pdf_url: str
    png_ready: bool
    pdf_ready: bool


# --------------------------------------------------------------------------- #
# Auth request bodies
# --------------------------------------------------------------------------- #
class GoogleAuthRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credential: str
    turnstile_token: Optional[str] = None


class OtpRequestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    turnstile_token: Optional[str] = None


class OtpVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    code: str
