// Shared types mirroring the backend (Task 2) contract exactly.
// These shapes are the single source of truth for the API layer and the UI.

export type Orientation = "landscape" | "portrait";
export type PaperSize =
  | "A5"
  | "A4"
  | "A3"
  | "A2"
  | "A1"
  | "A0"
  | "letter"
  | "legal"
  | "30x40cm"
  | "50x70cm"
  | "70x100cm"
  | "100x100cm"
  | "100x140cm";
export type Look = "soft-oval" | "paper";
export type OrderMode = "random" | "manual";
export type ExportFormat = "png" | "pdf" | "both";

export interface Settings {
  orientation: Orientation;
  paper_size: PaperSize;
  look: Look;
  order_mode: OrderMode;
  // All sliders are normalized floats in 0..1.
  spacing: number;
  rotation_intensity: number;
  border: number;
  feather: number;
  background: string; // hex like #f4efe6 or a css color name
  seed: number;
  // Preview-only dashed margin guide (inset from each paper edge in mm). Never
  // included in the export render.
  margin_guide: boolean;
  margin_guide_mm: number;
}

export interface ImageOut {
  id: string;
  filename: string;
  name: string;
  width: number;
  height: number;
  url: string; // full-resolution original, e.g. /api/projects/{id}/images/{filename}
  preview_url: string; // small WebP proxy for fast on-screen rendering
}

export interface LayoutItem {
  image_id: string;
  // Normalized 0..1 CENTER of the photo.
  x: number;
  y: number;
  // Normalized fractions of canvas width/height — box size BEFORE rotation.
  width: number;
  height: number;
  rotation: number; // degrees
  z_index: number;
  look: Look;
  // Optional per-photo glow/feather override (0..1). Absent => use settings.feather.
  feather?: number | null;
}

export interface Project {
  id: string;
  settings: Settings;
  images: ImageOut[];
  layout: LayoutItem[];
}

export interface ExportResponse {
  png_url: string;
  pdf_url: string;
  png_ready: boolean;
  pdf_ready: boolean;
}

// One entry in the user's "My collages" history.
export interface ProjectSummary {
  id: string;
  created_at: number;
  updated_at: number;
  image_count: number;
  thumb: string | null;
}
