import { useCallback, useRef, useState } from "react";
import type {
  DragEvent as ReactDragEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent,
} from "react";
import type { ImageOut, LayoutItem, Settings } from "../types";

type ResizeCorner = "nw" | "ne" | "se" | "sw";

// Paper sizes in PORTRAIT pixels at 300 DPI — must match collage_a4.PAPER_SIZES
// so the preview's aspect ratio and frame scaling track the export exactly.
const PAPER_PORTRAIT: Record<Settings["paper_size"], [number, number]> = {
  A5: [1748, 2480],
  A4: [2480, 3508],
  A3: [3508, 4961],
  A2: [4961, 7016],
  A1: [7016, 9933],
  A0: [9933, 14043],
  letter: [2550, 3300],
  legal: [2550, 4200],
  "30x40cm": [3543, 4724],
  "50x70cm": [5906, 8268],
  "70x100cm": [8268, 11811],
  "100x100cm": [11811, 11811],
  "100x140cm": [11811, 16535],
};

function paperDims(settings: Settings): { aspect: number; widthPx: number } {
  const [pw, ph] = PAPER_PORTRAIT[settings.paper_size] ?? PAPER_PORTRAIT.A4;
  // canvas (w,h): landscape swaps to (long, short).
  const [cw, ch] = settings.orientation === "landscape" ? [ph, pw] : [pw, ph];
  return { aspect: cw / ch, widthPx: cw };
}

interface CollageCanvasProps {
  settings: Settings;
  layout: LayoutItem[];
  imagesById: Map<string, ImageOut>;
  selectedIds: string[];
  onSelect: (imageId: string, additive: boolean) => void;
  onClearSelection: () => void;
  onMovePositions: (positions: { id: string; x: number; y: number }[]) => void;
  onRotateItems: (rotations: { id: string; rotation: number }[]) => void;
  onResizeItems: (sizes: { id: string; width: number; height: number }[]) => void;
  onDropFiles: (files: File[]) => void;
  onOpenMenu: (x: number, y: number) => void;
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function clampSize(v: number): number {
  return Math.max(0.02, Math.min(1, v));
}

// CSS clip-path (in the BEHIND photo's local box, as %) of the region where the
// COVER photo overlaps it -- so the "glass" peek reveals only the hidden corner,
// not the whole behind photo. Works for arbitrary rotations: it maps the cover's
// four (rotated) corners into the behind photo's (rotated) local frame. Needs the
// sheet's pixel size because rotation mixes the x/y axes (which have different
// px-per-fraction when the sheet isn't square).
function overlapClipPath(
  cover: LayoutItem,
  behind: LayoutItem,
  rect: { width: number; height: number },
): string | null {
  const sw = rect.width;
  const sh = rect.height;
  const bw = behind.width * sw;
  const bh = behind.height * sh;
  if (bw <= 0 || bh <= 0 || sw <= 0 || sh <= 0) return null;

  const bAng = (-behind.rotation * Math.PI) / 180;
  const cosB = Math.cos(bAng);
  const sinB = Math.sin(bAng);
  const bcx = behind.x * sw;
  const bcy = behind.y * sh;

  const cAng = (-cover.rotation * Math.PI) / 180;
  const cosC = Math.cos(cAng);
  const sinC = Math.sin(cAng);
  const ccx = cover.x * sw;
  const ccy = cover.y * sh;
  const cw = cover.width * sw;
  const ch = cover.height * sh;

  const corners: [number, number][] = [
    [-cw / 2, -ch / 2],
    [cw / 2, -ch / 2],
    [cw / 2, ch / 2],
    [-cw / 2, ch / 2],
  ];
  const pts = corners.map(([lx, ly]) => {
    // cover corner -> screen px
    const sx = ccx + lx * cosC - ly * sinC;
    const sy = ccy + lx * sinC + ly * cosC;
    // screen px -> behind photo's local box, then -> % of that box
    const rx = sx - bcx;
    const ry = sy - bcy;
    const lxb = rx * cosB + ry * sinB;
    const lyb = -rx * sinB + ry * cosB;
    const px = ((lxb + bw / 2) / bw) * 100;
    const py = ((lyb + bh / 2) / bh) * 100;
    return `${px.toFixed(2)}% ${py.toFixed(2)}%`;
  });
  return `polygon(${pts.join(", ")})`;
}

export default function CollageCanvas({
  settings,
  layout,
  imagesById,
  selectedIds,
  onSelect,
  onClearSelection,
  onMovePositions,
  onRotateItems,
  onResizeItems,
  onDropFiles,
  onOpenMenu,
}: CollageCanvasProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  // Glass "peek" of a photo hidden behind another: which photo, and the
  // clip-path (overlap-only) so the glass shows just the covered corner.
  const [peek, setPeek] = useState<{ id: string; clip: string | null } | null>(
    null,
  );
  // Tracks an in-progress canvas drag of one or more photos (group drag). We
  // capture each dragged item's start position and move them all by the same
  // normalized delta.
  const dragRef = useRef<{
    pointerId: number;
    ids: string[];
    startClientX: number;
    startClientY: number;
    starts: Map<string, { x: number; y: number }>;
  } | null>(null);
  const rotateRef = useRef<{
    pointerId: number;
    ids: string[];
    centerX: number;
    centerY: number;
    startAngle: number;
    starts: Map<string, number>;
  } | null>(null);
  const rotateCleanupRef = useRef<(() => void) | null>(null);
  const resizeRef = useRef<{
    pointerId: number;
    ids: string[];
    centerX: number;
    centerY: number;
    startDistance: number;
    starts: Map<string, { width: number; height: number }>;
  } | null>(null);
  const resizeCleanupRef = useRef<(() => void) | null>(null);

  // Approximate the backend render so the preview matches the export:
  //  - The backend draws each photo on a white rectangular CARD whose thickness
  //    is the `border` slider (render_service maps it to ~10..56px on the A4).
  //    We express the card frame in cqw (1cqw = 1% of the canvas width) so it
  //    scales with the on-screen sheet exactly like the export scales on the A4.
  //  - The soft-oval `feather` slider scales the white glow softness.
  const { aspect, widthPx: canvasWpx } = paperDims(settings);
  // border slider -> px, matching render_service (MIN_BORDER_PX..MAX_BORDER_PX).
  const borderPx = 10 + settings.border * 46;
  // soft-oval floors the white border at 38px in the renderer (render_photo).
  const framePx = settings.look === "soft-oval" ? Math.max(borderPx, 38) : borderPx;
  // Frame as % of the canvas width so it scales with the chosen paper.
  const frameCqw = (framePx / canvasWpx) * 100;

  // Preview-only margin guide: a dashed rectangle inset `margin_guide_mm` from
  // each paper edge. Convert mm -> px at 300 DPI, then to a % of the canvas
  // (different per axis since the sheet isn't square); clamp so it can't invert.
  // This is purely a UI overlay -- the server export never draws it.
  const MM_TO_PX_300DPI = 300 / 25.4;
  const guidePx =
    Math.max(0, settings.margin_guide_mm || 0) * MM_TO_PX_300DPI;
  const canvasHpx = aspect > 0 ? canvasWpx / aspect : canvasWpx;
  const guideInsetX = canvasWpx > 0 ? Math.min(49, (guidePx / canvasWpx) * 100) : 0;
  const guideInsetY = canvasHpx > 0 ? Math.min(49, (guidePx / canvasHpx) * 100) : 0;

  const pickControlTarget = useCallback(
    (clientX: number, clientY: number) => {
      const sheet = sheetRef.current;
      if (!sheet || dragRef.current || rotateRef.current || resizeRef.current) return;
      const rect = sheet.getBoundingClientRect();
      const frameScreenPx = (frameCqw / 100) * rect.width;
      const hits = [...layout]
        .sort((a, b) => b.z_index - a.z_index)
        .filter((item) => {
          if (!imagesById.has(item.image_id)) return false;
          const centerX = rect.left + item.x * rect.width;
          const centerY = rect.top + item.y * rect.height;
          const dx = clientX - centerX;
          const dy = clientY - centerY;
          const theta = (item.rotation * Math.PI) / 180;
          const localX = dx * Math.cos(theta) - dy * Math.sin(theta);
          const localY = dx * Math.sin(theta) + dy * Math.cos(theta);
          const halfW = (item.width * rect.width) / 2 + frameScreenPx;
          const halfH = (item.height * rect.height) / 2 + frameScreenPx;
          return Math.abs(localX) <= halfW && Math.abs(localY) <= halfH;
        });
      const top = hits[0];
      if (!top) {
        setHoveredId(null);
        setPeek(null);
        return;
      }
      // Highlight the top photo under the cursor. If a photo sits beneath it
      // here, reveal that one through a "glass" peek clipped to just the
      // overlapping region (so the lower photo's hidden corner is visible).
      setHoveredId((prev) => (prev === top.image_id ? prev : top.image_id));
      const behind = hits.length > 1 ? hits[1] : null;
      if (behind) {
        const clip = overlapClipPath(top, behind, rect);
        // Only update state when it actually changed, so moving the pointer over
        // a static overlap doesn't re-render the whole photo list every frame.
        setPeek((prev) =>
          prev && prev.id === behind.image_id && prev.clip === clip
            ? prev
            : { id: behind.image_id, clip },
        );
      } else {
        setPeek(null);
      }
    },
    [frameCqw, imagesById, layout],
  );

  // ----- File drop onto the canvas (alternative to the upload zone) -------- //
  const handleDragOver = useCallback((e: ReactDragEvent<HTMLDivElement>) => {
    if (Array.from(e.dataTransfer.types).includes("Files")) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  }, []);

  const handleDrop = useCallback(
    (e: ReactDragEvent<HTMLDivElement>) => {
      const files = Array.from(e.dataTransfer.files).filter((f) =>
        f.type.startsWith("image/"),
      );
      if (files.length > 0) {
        e.preventDefault();
        onDropFiles(files);
      }
    },
    [onDropFiles],
  );

  const handleSheetPointerMove = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      pickControlTarget(e.clientX, e.clientY);
    },
    [pickControlTarget],
  );

  const handleSheetPointerLeave = useCallback(() => {
    if (dragRef.current || rotateRef.current || resizeRef.current) return;
    setHoveredId(null);
    setPeek(null);
  }, []);

  // ----- Select / drag photo(s) on the canvas ----------------------------- //
  const handlePointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>, imageId: string) => {
      // Only the left button selects/drags; right-click is handled by
      // onContextMenu (stop propagation so the sheet doesn't clear selection).
      if (e.button !== 0) {
        e.stopPropagation();
        return;
      }
      e.stopPropagation();
      const additive = e.shiftKey || e.metaKey || e.ctrlKey;
      if (additive) {
        // Modifier-click toggles this photo in the selection; no drag.
        onSelect(imageId, true);
        return;
      }
      // If the photo is already part of a multi-selection, drag the whole group;
      // otherwise make it the sole selection and drag just it.
      const inSelection = selectedIds.includes(imageId);
      const dragIds = inSelection && selectedIds.length > 1 ? selectedIds : [imageId];
      if (!inSelection || selectedIds.length <= 1) onSelect(imageId, false);

      const starts = new Map<string, { x: number; y: number }>();
      for (const it of layout) {
        if (dragIds.includes(it.image_id)) starts.set(it.image_id, { x: it.x, y: it.y });
      }
      dragRef.current = {
        pointerId: e.pointerId,
        ids: dragIds,
        startClientX: e.clientX,
        startClientY: e.clientY,
        starts,
      };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    },
    [onSelect, selectedIds, layout],
  );

  const handlePointerMove = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      // Hover detection lives on the sheet's onPointerMove (events bubble up to
      // it), so this per-photo handler only drives an active drag -- no
      // duplicate pickControlTarget hit-test per move.
      const drag = dragRef.current;
      const sheet = sheetRef.current;
      if (!drag || drag.pointerId !== e.pointerId || !sheet) return;
      const rect = sheet.getBoundingClientRect();
      const dx = (e.clientX - drag.startClientX) / rect.width;
      const dy = (e.clientY - drag.startClientY) / rect.height;
      const positions = drag.ids
        .map((id) => {
          const s = drag.starts.get(id);
          return s ? { id, x: clamp01(s.x + dx), y: clamp01(s.y + dy) } : null;
        })
        .filter((p): p is { id: string; x: number; y: number } => p !== null);
      if (positions.length > 0) onMovePositions(positions);
    },
    [onMovePositions],
  );

  const handlePointerEnter = useCallback(
    (e: PointerEvent<HTMLDivElement>, imageId: string) => {
      pickControlTarget(e.clientX, e.clientY);
      setHoveredId((prev) => prev ?? imageId);
    },
    [pickControlTarget],
  );

  const endDrag = useCallback((e: PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === e.pointerId) {
      dragRef.current = null;
    }
  }, []);

  const handleRotatePointerDown = useCallback(
    (e: PointerEvent<HTMLButtonElement>, item: LayoutItem) => {
      if (e.button !== 0) {
        e.stopPropagation();
        return;
      }
      e.preventDefault();
      e.stopPropagation();

      const inSelection = selectedIds.includes(item.image_id);
      const rotateIds = inSelection && selectedIds.length > 1 ? selectedIds : [item.image_id];
      if (!inSelection || selectedIds.length <= 1) onSelect(item.image_id, false);

      const photo = e.currentTarget.closest(
        ".photo, .photo-controls-layer",
      ) as HTMLElement | null;
      const rect = photo?.getBoundingClientRect();
      if (!rect) return;
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const starts = new Map<string, number>();
      for (const it of layout) {
        if (rotateIds.includes(it.image_id)) starts.set(it.image_id, it.rotation);
      }

      rotateRef.current = {
        pointerId: e.pointerId,
        ids: rotateIds,
        centerX,
        centerY,
        startAngle: Math.atan2(e.clientY - centerY, e.clientX - centerX),
        starts,
      };
      rotateCleanupRef.current?.();
      const applyRotate = (clientX: number, clientY: number) => {
        const rotate = rotateRef.current;
        if (!rotate) return;
        const angle = Math.atan2(clientY - rotate.centerY, clientX - rotate.centerX);
        const deltaDeg = ((angle - rotate.startAngle) * 180) / Math.PI;
        const rotations = rotate.ids
          .map((id) => {
            const start = rotate.starts.get(id);
            return typeof start === "number" ? { id, rotation: start - deltaDeg } : null;
          })
          .filter((r): r is { id: string; rotation: number } => r !== null);
        if (rotations.length > 0) onRotateItems(rotations);
      };
      const handleWindowMove = (event: globalThis.PointerEvent) => {
        if (rotateRef.current?.pointerId !== event.pointerId) return;
        if (event.buttons === 0) {
          rotateRef.current = null;
          rotateCleanupRef.current?.();
          rotateCleanupRef.current = null;
          return;
        }
        event.preventDefault();
        applyRotate(event.clientX, event.clientY);
      };
      const handleWindowEnd = (event: globalThis.PointerEvent) => {
        if (rotateRef.current?.pointerId !== event.pointerId) return;
        rotateRef.current = null;
        rotateCleanupRef.current?.();
        rotateCleanupRef.current = null;
      };
      window.addEventListener("pointermove", handleWindowMove);
      window.addEventListener("pointerup", handleWindowEnd);
      window.addEventListener("pointercancel", handleWindowEnd);
      rotateCleanupRef.current = () => {
        window.removeEventListener("pointermove", handleWindowMove);
        window.removeEventListener("pointerup", handleWindowEnd);
        window.removeEventListener("pointercancel", handleWindowEnd);
      };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [layout, onRotateItems, onSelect, selectedIds],
  );

  const endRotate = useCallback((e: PointerEvent<HTMLButtonElement>) => {
    if (rotateRef.current?.pointerId === e.pointerId) {
      rotateRef.current = null;
      rotateCleanupRef.current?.();
      rotateCleanupRef.current = null;
    }
  }, []);

  const handleResizePointerDown = useCallback(
    (e: PointerEvent<HTMLButtonElement>, item: LayoutItem, _corner: ResizeCorner) => {
      if (e.button !== 0) {
        e.stopPropagation();
        return;
      }
      e.preventDefault();
      e.stopPropagation();

      const inSelection = selectedIds.includes(item.image_id);
      const resizeIds = inSelection && selectedIds.length > 1 ? selectedIds : [item.image_id];
      if (!inSelection || selectedIds.length <= 1) onSelect(item.image_id, false);

      const photo = e.currentTarget.closest(
        ".photo, .photo-controls-layer",
      ) as HTMLElement | null;
      const rect = photo?.getBoundingClientRect();
      if (!rect) return;
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const startDistance = Math.max(
        1,
        Math.hypot(e.clientX - centerX, e.clientY - centerY),
      );
      const starts = new Map<string, { width: number; height: number }>();
      for (const it of layout) {
        if (resizeIds.includes(it.image_id)) {
          starts.set(it.image_id, { width: it.width, height: it.height });
        }
      }

      resizeRef.current = {
        pointerId: e.pointerId,
        ids: resizeIds,
        centerX,
        centerY,
        startDistance,
        starts,
      };
      resizeCleanupRef.current?.();
      const applyResize = (clientX: number, clientY: number) => {
        const resize = resizeRef.current;
        if (!resize) return;
        const distance = Math.max(
          1,
          Math.hypot(clientX - resize.centerX, clientY - resize.centerY),
        );
        const scale = Math.max(0.15, Math.min(5, distance / resize.startDistance));
        const sizes = resize.ids
          .map((id) => {
            const start = resize.starts.get(id);
            return start
              ? {
                  id,
                  width: clampSize(start.width * scale),
                  height: clampSize(start.height * scale),
                }
              : null;
          })
          .filter(
            (s): s is { id: string; width: number; height: number } => s !== null,
          );
        if (sizes.length > 0) onResizeItems(sizes);
      };
      const handleWindowMove = (event: globalThis.PointerEvent) => {
        if (resizeRef.current?.pointerId !== event.pointerId) return;
        event.preventDefault();
        applyResize(event.clientX, event.clientY);
      };
      const handleWindowEnd = (event: globalThis.PointerEvent) => {
        if (resizeRef.current?.pointerId !== event.pointerId) return;
        resizeRef.current = null;
        resizeCleanupRef.current?.();
        resizeCleanupRef.current = null;
      };
      window.addEventListener("pointermove", handleWindowMove);
      window.addEventListener("pointerup", handleWindowEnd);
      window.addEventListener("pointercancel", handleWindowEnd);
      resizeCleanupRef.current = () => {
        window.removeEventListener("pointermove", handleWindowMove);
        window.removeEventListener("pointerup", handleWindowEnd);
        window.removeEventListener("pointercancel", handleWindowEnd);
      };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [layout, onResizeItems, onSelect, selectedIds],
  );

  const endResize = useCallback((e: PointerEvent<HTMLButtonElement>) => {
    if (resizeRef.current?.pointerId === e.pointerId) {
      resizeRef.current = null;
      resizeCleanupRef.current?.();
      resizeCleanupRef.current = null;
    }
  }, []);

  // Right-click a photo: select it (if not already selected) and open the menu.
  const handleContextMenu = useCallback(
    (e: ReactMouseEvent<HTMLDivElement>, imageId: string) => {
      e.preventDefault();
      e.stopPropagation();
      if (!selectedIds.includes(imageId)) onSelect(imageId, false);
      onOpenMenu(e.clientX, e.clientY);
    },
    [selectedIds, onSelect, onOpenMenu],
  );

  // Render in z-order so stacking is correct (higher z_index = on top).
  const ordered = [...layout].sort((a, b) => a.z_index - b.z_index);
  const selectedPrimaryId = selectedIds[selectedIds.length - 1] ?? null;
  // Controls follow the SELECTION (not hover) so the handles are stable and don't
  // vanish as the pointer moves toward them; they render on a top layer so they
  // stay grabbable even when another photo is in front.
  const controlsItem =
    ordered.find((item) => item.image_id === selectedPrimaryId) ?? null;
  const peekItem = peek
    ? ordered.find((item) => item.image_id === peek.id) ?? null
    : null;
  const peekImage = peekItem ? imagesById.get(peekItem.image_id) ?? null : null;

  return (
    <div className="canvas-wrap">
      <div
        ref={sheetRef}
        className="canvas-sheet"
        style={
          {
            // --ar drives both the aspect-ratio and the contain-fit width
            // (see .canvas-sheet in styles.css). Derived from paper + orientation.
            "--ar": String(aspect),
            // --frame is the white print card thickness; --fin/--fout feather the
            // soft-oval edge into the card. Together they approximate the backend
            // render so the preview matches the export (consumed in styles.css).
            "--frame": `${frameCqw.toFixed(3)}cqw`,
            // --fin/--fout feather the soft-oval edge into the white card to match
            // the renderer's gaussian-blurred oval mask (more feather = softer).
            "--fin": `${(88 - settings.feather * 38).toFixed(0)}%`,
            "--fout": `${(92 - settings.feather * 2).toFixed(0)}%`,
            aspectRatio: String(aspect),
            background: settings.background,
          } as React.CSSProperties
        }
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        onPointerMove={handleSheetPointerMove}
        onPointerLeave={handleSheetPointerLeave}
        onPointerDown={() => onClearSelection()}
        onContextMenu={(e) => e.preventDefault()}
      >
        {ordered.length === 0 && (
          <div className="canvas-empty">
            <p className="canvas-empty-title">Your collage preview</p>
            <p className="canvas-empty-sub">
              Drop photos here or use the upload zone on the left.
            </p>
          </div>
        )}

        {ordered.map((item) => {
          const image = imagesById.get(item.image_id);
          if (!image) return null;
          const isSelected = selectedIds.includes(item.image_id);
          const isHovered = hoveredId === item.image_id;
          const isOval = item.look === "soft-oval";
          // Per-photo glow/feather (0..1): the override if set, else the global.
          // Drives this photo's oval mask so the slider can affect only the
          // selected photo(s). Same mapping as the sheet fallback above.
          const effFeather =
            typeof item.feather === "number" ? item.feather : settings.feather;

          // WYSIWYG geometry — MUST mirror the backend export:
          //  - left/top use the normalized CENTER (x,y).
          //  - translate(-50%,-50%) anchors the box on that center.
          //  - rotate is NEGATED: backend PIL rotate() is CCW for +angle,
          //    CSS rotate() is CW for +angle, so we use -rotation.
          //  - width/height are % of the canvas div (== normalized fractions).
          const boxStyle: React.CSSProperties = {
            left: `${item.x * 100}%`,
            top: `${item.y * 100}%`,
            width: `${item.width * 100}%`,
            height: `${item.height * 100}%`,
            transform: `translate(-50%, -50%) rotate(${-item.rotation}deg)`,
            "--photo-rotation": `${item.rotation}deg`,
            "--fin": `${(88 - effFeather * 38).toFixed(0)}%`,
            "--fout": `${(92 - effFeather * 2).toFixed(0)}%`,
            zIndex: item.z_index,
          } as React.CSSProperties;

          // `look` styling lives in CSS (.photo-oval / .photo-paper), driven by
          // the --frame / --glow vars set on the sheet. Geometry (boxStyle) is
          // exact; the white card + oval glow are visual approximations of the
          // renderer's output, scaled in cqw so they track the export.
          const lookClass = isOval ? "photo-oval" : "photo-paper";

          return (
            <div
              key={item.image_id}
              className={
                "photo" +
                (isSelected ? " photo-selected" : "") +
                (isHovered ? " photo-hovered" : "") +
                ` ${lookClass}`
              }
              style={boxStyle}
              onPointerDown={(e) => handlePointerDown(e, item.image_id)}
              onPointerMove={handlePointerMove}
              onPointerEnter={(e) => handlePointerEnter(e, item.image_id)}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onContextMenu={(e) => handleContextMenu(e, item.image_id)}
            >
              <img
                src={image.preview_url}
                alt={image.name}
                draggable={false}
                className="photo-img"
              />
              {isSelected && <span className="photo-ring" aria-hidden="true" />}
            </div>
          );
        })}
        {settings.margin_guide && (
          <div
            className="canvas-margin-guide"
            style={{ inset: `${guideInsetY}% ${guideInsetX}%` }}
            aria-hidden="true"
          />
        )}
        {peekItem && peekImage && peek?.clip && (
          <div
            className="photo-peek-layer"
            style={
              {
                left: `${peekItem.x * 100}%`,
                top: `${peekItem.y * 100}%`,
                width: `${peekItem.width * 100}%`,
                height: `${peekItem.height * 100}%`,
                transform: `translate(-50%, -50%) rotate(${-peekItem.rotation}deg)`,
                clipPath: peek.clip,
                WebkitClipPath: peek.clip,
              } as React.CSSProperties
            }
            aria-hidden="true"
          >
            <img src={peekImage.preview_url} alt="" draggable={false} />
          </div>
        )}
        {controlsItem && (
          <div
            className="photo-controls-layer"
            style={
              {
                left: `${controlsItem.x * 100}%`,
                top: `${controlsItem.y * 100}%`,
                width: `${controlsItem.width * 100}%`,
                height: `${controlsItem.height * 100}%`,
                transform: `translate(-50%, -50%) rotate(${-controlsItem.rotation}deg)`,
                "--photo-rotation": `${controlsItem.rotation}deg`,
              } as React.CSSProperties
            }
          >
            <button
              type="button"
              className="photo-corner-handle photo-rotate-handle"
              aria-label="Rotate photo"
              title="Rotate"
              onPointerDown={(e) => handleRotatePointerDown(e, controlsItem)}
              onPointerUp={endRotate}
              onPointerCancel={endRotate}
            >
              <svg
                className="handle-icon"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.4}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M21 12a9 9 0 1 1-2.64-6.36" />
                <path d="M21 3.5V9h-5.5" />
              </svg>
            </button>
            <button
              type="button"
              className="photo-corner-handle photo-resize-handle"
              aria-label="Resize photo"
              title="Resize"
              onPointerDown={(e) => handleResizePointerDown(e, controlsItem, "sw")}
              onPointerUp={endResize}
              onPointerCancel={endResize}
            >
              <svg
                className="handle-icon"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.4}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M4 12h16" />
                <path d="M7 8.5 3.5 12 7 15.5" />
                <path d="M17 8.5 20.5 12 17 15.5" />
              </svg>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
