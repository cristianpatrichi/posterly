import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { ApiError } from "./api";
import type {
  ExportFormat,
  ImageOut,
  LayoutItem,
  Project,
  Settings,
} from "./types";
import CollageCanvas from "./components/CollageCanvas";
import BrandLogo from "./components/BrandLogo";
import ContextMenu from "./components/ContextMenu";
import type { ContextMenuState } from "./components/ContextMenu";
import HistoryView from "./components/HistoryView";
import PhotoStrip from "./components/PhotoStrip";
import SettingsPanel from "./components/SettingsPanel";
import Uploader from "./components/Uploader";
import { useAuth } from "./auth/AuthContext";

const STORAGE_KEY = "collage_project_id";

// Settings that change the ARRANGEMENT (positions/sizes/rotations) trigger a
// fresh auto-layout. The rest only affect rendering, so we just PUT + redraw.
// Decision (documented per spec): orientation, spacing, rotation_intensity,
// order_mode and seed re-run auto-layout (they reshape the collage); border,
// feather, background and look only update the preview/persist. `look`
// additionally rewrites every layout item's look in place. `seed` is the
// reproducible counterpart to Randomize — typing a new seed re-lays out.
const ARRANGEMENT_KEYS: ReadonlyArray<keyof Settings> = [
  "orientation",
  "paper_size",
  "spacing",
  "rotation_intensity",
  "order_mode",
  "seed",
];

const PUT_DEBOUNCE_MS = 300;

// Upload photos in small size-bounded batches instead of one giant request, so
// the LIMIT IS EFFECTIVELY PER-PHOTO: each HTTP request stays small (or carries a
// single large photo), well under any reverse-proxy/tunnel per-request body cap.
// A whole batch of photos can total far more than that cap. A photo larger than
// this threshold is sent on its own (its request is just that one photo).
const UPLOAD_BATCH_BYTES = 15 * 1024 * 1024; // ~15 MB per request

function batchBySize(files: File[]): File[][] {
  const batches: File[][] = [];
  let current: File[] = [];
  let size = 0;
  for (const file of files) {
    if (current.length > 0 && size + file.size > UPLOAD_BATCH_BYTES) {
      batches.push(current);
      current = [];
      size = 0;
    }
    current.push(file);
    size += file.size;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

export default function App() {
  const { user, logout } = useAuth();
  const [historyOpen, setHistoryOpen] = useState(false);
  // Mobile: the Settings panel becomes a slide-up bottom sheet.
  const [settingsSheetOpen, setSettingsSheetOpen] = useState(false);
  const [project, setProject] = useState<Project | null>(null);
  // Multi-selection: ordered list of selected image ids (last = "primary",
  // used for the panel's slider readouts). Cmd/Ctrl/Shift+click toggles.
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [autoLayouting, setAutoLayouting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [downloadMenuOpen, setDownloadMenuOpen] = useState(false);
  const [menu, setMenu] = useState<ContextMenuState | null>(null);

  // Debounced PUT machinery. We keep the latest project in a ref so the timer
  // always flushes the freshest settings+layout.
  const putTimer = useRef<number | null>(null);
  const latestRef = useRef<Project | null>(null);
  latestRef.current = project;

  // Mirror selection in a ref so handlers (which run before re-render) read the
  // freshest set, same pattern as latestRef for the project.
  const selectedIdsRef = useRef<string[]>([]);
  selectedIdsRef.current = selectedIds;

  // Tracks the in-flight auto-layout POST (if any). The export flow awaits this
  // before persisting so the downloaded file always matches the screen.
  const pendingAutoLayoutRef = useRef<Promise<void> | null>(null);

  // Hidden file input backing the mobile action bar's "Add photos" button.
  const addInputRef = useRef<HTMLInputElement>(null);

  // Clean up the debounced PUT timer on unmount so a pending flush can't fire
  // after the component is gone.
  useEffect(
    () => () => {
      if (putTimer.current !== null) window.clearTimeout(putTimer.current);
    },
    [],
  );

  // --------------------------------------------------------------------- //
  // Bootstrap: restore or create a project.
  // --------------------------------------------------------------------- //
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const storedId = localStorage.getItem(STORAGE_KEY);
        let proj: Project | null = null;
        if (storedId) {
          try {
            proj = await api.getProject(storedId);
          } catch (e) {
            if (e instanceof ApiError && e.status === 404) {
              proj = null; // stale id — fall through to create
            } else {
              throw e;
            }
          }
        }
        if (!proj) {
          // Don't spawn a fresh empty collage on every login. Reuse the user's
          // most recent existing project; only create one if they have none.
          const existing = await api.listProjects(); // newest first
          const newestId = existing[0]?.id;
          proj = newestId
            ? await api.getProject(newestId)
            : await api.createProject();
          localStorage.setItem(STORAGE_KEY, proj.id);
        }
        if (!cancelled) setProject(proj);
      } catch (e) {
        if (!cancelled) setError(describe(e, "Could not start a project."));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const imagesById = useMemo(() => {
    const map = new Map<string, ImageOut>();
    for (const img of project?.images ?? []) map.set(img.id, img);
    return map;
  }, [project]);

  // The "primary" item is the last one added to the selection; its values feed
  // the panel's sliders. Edits apply to ALL selected items, not just this one.
  const primaryId = selectedIds.length ? selectedIds[selectedIds.length - 1] : null;
  const primaryItem = useMemo<LayoutItem | null>(() => {
    if (!project || !primaryId) return null;
    return project.layout.find((l) => l.image_id === primaryId) ?? null;
  }, [project, primaryId]);

  // --------------------------------------------------------------------- //
  // Selection (single click = replace; Cmd/Ctrl/Shift+click = toggle).
  // --------------------------------------------------------------------- //
  const handleSelect = useCallback((imageId: string, additive: boolean) => {
    setSelectedIds((prev) =>
      additive
        ? prev.includes(imageId)
          ? prev.filter((id) => id !== imageId)
          : [...prev, imageId]
        : [imageId],
    );
  }, []);

  const clearSelection = useCallback(() => setSelectedIds([]), []);

  // --------------------------------------------------------------------- //
  // Persistence: debounced PUT of settings + layout.
  // --------------------------------------------------------------------- //
  const schedulePut = useCallback(() => {
    if (putTimer.current !== null) window.clearTimeout(putTimer.current);
    putTimer.current = window.setTimeout(() => {
      putTimer.current = null;
      const proj = latestRef.current;
      if (!proj) return;
      api
        .updateProject(proj.id, {
          settings: proj.settings,
          layout: proj.layout,
        })
        .catch((e) => setError(describe(e, "Could not save changes.")));
    }, PUT_DEBOUNCE_MS);
  }, []);

  // Flush any pending PUT immediately and return a promise (used before export).
  const flushPut = useCallback(async (): Promise<void> => {
    if (putTimer.current !== null) {
      window.clearTimeout(putTimer.current);
      putTimer.current = null;
    }
    const proj = latestRef.current;
    if (!proj) return;
    await api.updateProject(proj.id, {
      settings: proj.settings,
      layout: proj.layout,
    });
  }, []);

  const runAutoLayout = useCallback(async (): Promise<void> => {
    const proj = latestRef.current;
    if (!proj || proj.images.length === 0) return;
    setAutoLayouting(true);
    const run = (async () => {
      try {
        const updated = await api.autoLayout(proj.id, proj.settings);
        setProject(updated);
        latestRef.current = updated;
      } catch (e) {
        setError(describe(e, "Could not regenerate the layout."));
      }
    })();
    // Track the in-flight promise so handleDownload can await it before export.
    pendingAutoLayoutRef.current = run;
    try {
      await run;
    } finally {
      // Only clear if no newer auto-layout has superseded this one.
      if (pendingAutoLayoutRef.current === run) {
        pendingAutoLayoutRef.current = null;
        setAutoLayouting(false);
      }
    }
  }, []);

  // --------------------------------------------------------------------- //
  // Settings changes.
  // --------------------------------------------------------------------- //
  const handleSettingsChange = useCallback(
    (patch: Partial<Settings>) => {
      const prev = latestRef.current;
      if (!prev) return;
      const settings = { ...prev.settings, ...patch };
      let layout = prev.layout;

      // Style change: rewrite every item's look so the whole collage switches.
      if (patch.look && patch.look !== prev.settings.look) {
        layout = layout.map((item) => ({ ...item, look: patch.look as Settings["look"] }));
      }

      const next = { ...prev, settings, layout };
      // Update the ref SYNCHRONOUSLY before kicking off auto-layout. setProject's
      // updater runs after this function returns, so if we relied on it,
      // runAutoLayout() would read the stale settings and the change (e.g.
      // orientation) would be lost when the auto-layout result is applied.
      latestRef.current = next;
      setProject(next);

      const triggersArrangement = ARRANGEMENT_KEYS.some((k) => k in patch);
      if (triggersArrangement) {
        void runAutoLayout();
      } else {
        schedulePut();
      }
    },
    [runAutoLayout, schedulePut],
  );

  // --------------------------------------------------------------------- //
  // Upload flow.
  // --------------------------------------------------------------------- //
  const handleFiles = useCallback(
    async (files: File[]) => {
      const proj = latestRef.current;
      if (!proj || files.length === 0) return;
      setUploading(true);
      setError(null);
      const hadLayout = proj.layout.length > 0;
      // Send photos in small batches (a big photo goes alone) so each request is
      // small -- the cap is per-photo, not on the whole batch (see batchBySize).
      const batches = batchBySize(files);
      setUploadProgress({ done: 0, total: files.length });
      try {
        let current = proj;
        let uploaded = 0;
        for (const batch of batches) {
          current = await api.uploadImages(proj.id, batch);
          uploaded += batch.length;
          setProject(current); // show photos as they arrive
          latestRef.current = current;
          setUploadProgress({ done: uploaded, total: files.length });
        }
        // Lay out once, after all photos are in, so each one gets placed.
        if (!hadLayout || current.layout.length < current.images.length) {
          const laidOut = await api.autoLayout(proj.id, current.settings);
          setProject(laidOut);
          latestRef.current = laidOut;
        }
      } catch (e) {
        setError(describe(e, "Upload failed."));
      } finally {
        setUploading(false);
        setUploadProgress(null);
      }
    },
    [],
  );

  // --------------------------------------------------------------------- //
  // Toolbar actions.
  // --------------------------------------------------------------------- //
  const handleNewCollage = useCallback(async () => {
    setError(null);
    setHistoryOpen(false);
    try {
      const proj = await api.createProject();
      localStorage.setItem(STORAGE_KEY, proj.id);
      setProject(proj);
      latestRef.current = proj;
      setSelectedIds([]);
    } catch (e) {
      setError(describe(e, "Could not create a new collage."));
    }
  }, []);

  // Open a past collage from the history view.
  const handleOpenProject = useCallback(async (id: string) => {
    setHistoryOpen(false);
    setError(null);
    try {
      const proj = await api.getProject(id);
      localStorage.setItem(STORAGE_KEY, proj.id);
      setProject(proj);
      latestRef.current = proj;
      setSelectedIds([]);
    } catch (e) {
      setError(describe(e, "Could not open that collage."));
    }
  }, []);

  const handleRandomize = useCallback(async () => {
    const proj = latestRef.current;
    if (!proj || proj.images.length === 0) return;
    const settings: Settings = {
      ...proj.settings,
      seed: Math.floor(Math.random() * 1_000_000),
    };
    const next = { ...proj, settings };
    setProject(next);
    latestRef.current = next;
    await runAutoLayout();
  }, [runAutoLayout]);

  // --------------------------------------------------------------------- //
  // Per-item manual edits.
  // --------------------------------------------------------------------- //
  // Apply a per-item patch to EVERY selected item and persist (debounced).
  const updateSelected = useCallback(
    (make: (item: LayoutItem) => Partial<LayoutItem>) => {
      const proj = latestRef.current;
      const set = new Set(selectedIdsRef.current);
      if (!proj || set.size === 0) return;
      const layout = proj.layout.map((it) =>
        set.has(it.image_id) ? { ...it, ...make(it) } : it,
      );
      const next = { ...proj, layout };
      latestRef.current = next;
      setProject(next);
      schedulePut();
    },
    [schedulePut],
  );

  // Set absolute positions for a batch of items (used by group drag on canvas).
  const handleMovePositions = useCallback(
    (positions: { id: string; x: number; y: number }[]) => {
      const proj = latestRef.current;
      if (!proj || positions.length === 0) return;
      const byId = new Map(positions.map((p) => [p.id, p]));
      const layout = proj.layout.map((it) => {
        const p = byId.get(it.image_id);
        return p ? { ...it, x: p.x, y: p.y } : it;
      });
      const next = { ...proj, layout };
      latestRef.current = next;
      setProject(next);
      schedulePut();
    },
    [schedulePut],
  );

  // Rotation-handle drag on the canvas. The canvas sends absolute rotations for
  // one photo, or for the whole current selection when rotating a selected group.
  const handleRotateItems = useCallback(
    (rotations: { id: string; rotation: number }[]) => {
      const proj = latestRef.current;
      if (!proj || rotations.length === 0) return;
      const byId = new Map(rotations.map((r) => [r.id, r.rotation]));
      const layout = proj.layout.map((it) => {
        const rotation = byId.get(it.image_id);
        return typeof rotation === "number" ? { ...it, rotation } : it;
      });
      const next = { ...proj, layout };
      latestRef.current = next;
      setProject(next);
      schedulePut();
    },
    [schedulePut],
  );

  // Corner-resize drag on the canvas. The canvas sends absolute normalized
  // dimensions for one photo, or for the current selected group.
  const handleResizeItems = useCallback(
    (sizes: { id: string; width: number; height: number }[]) => {
      const proj = latestRef.current;
      if (!proj || sizes.length === 0) return;
      const byId = new Map(sizes.map((s) => [s.id, s]));
      const layout = proj.layout.map((it) => {
        const size = byId.get(it.image_id);
        return size ? { ...it, width: size.width, height: size.height } : it;
      });
      const next = { ...proj, layout };
      latestRef.current = next;
      setProject(next);
      schedulePut();
    },
    [schedulePut],
  );

  // Absolute field set across the selection (e.g. rotation slider).
  const handleItemChange = useCallback(
    (patch: Partial<LayoutItem>) => updateSelected(() => patch),
    [updateSelected],
  );

  const handleRotateBy = useCallback(
    (deltaDeg: number) =>
      updateSelected((it) => ({ rotation: it.rotation + deltaDeg })),
    [updateSelected],
  );

  // Size slider: scale each selected item to the given normalized width while
  // preserving its OWN aspect ratio (per-item, so mixed shapes stay undistorted).
  const handleSizeChange = useCallback(
    (width: number) =>
      updateSelected((it) => {
        const ratio = it.width > 0 ? it.height / it.width : 1;
        const w = Math.max(0.02, Math.min(1, width));
        const h = Math.max(0.02, Math.min(1, w * ratio));
        return { width: w, height: h };
      }),
    [updateSelected],
  );

  // Move the whole selection (as a block, preserving its relative order) within
  // the stacking order, then reassign contiguous z_index (0..n-1) to everyone.
  // Works for one or many selected items.
  const reorderZ = useCallback(
    (op: "front" | "back" | "forward" | "backward") => {
      const proj = latestRef.current;
      const ids = selectedIdsRef.current;
      if (!proj || ids.length === 0) return;
      const set = new Set(ids);
      const sorted = [...proj.layout].sort((a, b) => a.z_index - b.z_index);
      const selected = sorted.filter((i) => set.has(i.image_id));
      const others = sorted.filter((i) => !set.has(i.image_id));
      let order: LayoutItem[];
      if (op === "front") order = [...others, ...selected];
      else if (op === "back") order = [...selected, ...others];
      else {
        // Step the block up/down by one position among the non-selected items.
        const firstSel = sorted.findIndex((i) => set.has(i.image_id));
        const othersBefore = sorted
          .slice(0, firstSel)
          .filter((i) => !set.has(i.image_id)).length;
        let at = op === "forward" ? othersBefore + 1 : othersBefore - 1;
        at = Math.max(0, Math.min(others.length, at));
        order = [...others];
        order.splice(at, 0, ...selected);
      }
      const zById = new Map(order.map((it, i) => [it.image_id, i]));
      const layout = proj.layout.map((it) => ({
        ...it,
        z_index: zById.get(it.image_id) ?? it.z_index,
      }));
      const next = { ...proj, layout };
      latestRef.current = next;
      setProject(next);
      schedulePut();
    },
    [schedulePut],
  );

  const handleBringForward = useCallback(() => reorderZ("forward"), [reorderZ]);
  const handleSendBackward = useCallback(() => reorderZ("backward"), [reorderZ]);
  const handleBringToFront = useCallback(() => reorderZ("front"), [reorderZ]);
  const handleSendToBack = useCallback(() => reorderZ("back"), [reorderZ]);

  // Reset the selected photo(s): recompute a fresh auto-layout for the whole set
  // and adopt the auto position/size/rotation for each selected item (others
  // keep the user's manual arrangement).
  const handleResetItem = useCallback(async () => {
    const proj = latestRef.current;
    const ids = selectedIdsRef.current;
    if (!proj || ids.length === 0) return;
    try {
      const fresh = await api.autoLayout(proj.id, proj.settings);
      const autoById = new Map(fresh.layout.map((i) => [i.image_id, i]));
      const set = new Set(ids);
      const current = latestRef.current;
      if (!current) return;
      const layout = current.layout.map((item) => {
        if (!set.has(item.image_id)) return item;
        const auto = autoById.get(item.image_id);
        return auto
          ? {
              ...item,
              x: auto.x,
              y: auto.y,
              width: auto.width,
              height: auto.height,
              rotation: auto.rotation,
            }
          : item;
      });
      const next = { ...current, layout };
      setProject(next);
      latestRef.current = next;
      schedulePut();
    } catch (e) {
      setError(describe(e, "Could not reset the photo."));
    }
  }, [schedulePut]);

  // Zoom (resize) all selected items by a factor, keeping aspect.
  const handleZoom = useCallback(
    (factor: number) =>
      updateSelected((it) => ({
        width: Math.max(0.02, Math.min(1, it.width * factor)),
        height: Math.max(0.02, Math.min(1, it.height * factor)),
      })),
    [updateSelected],
  );

  // Delete all selected photos (removes from images + layout + disk).
  const handleDeleteSelected = useCallback(async () => {
    const proj = latestRef.current;
    const ids = selectedIdsRef.current;
    if (!proj || ids.length === 0) return;
    setSelectedIds([]);
    try {
      const updated = await api.deleteImages(proj.id, ids);
      setProject(updated);
      latestRef.current = updated;
    } catch (e) {
      setError(describe(e, "Could not delete photos."));
    }
  }, []);

  // Delete / Backspace removes the selection (unless typing in a form field).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Delete" && e.key !== "Backspace") return;
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if (selectedIdsRef.current.length === 0) return;
      e.preventDefault();
      void handleDeleteSelected();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handleDeleteSelected]);

  // Open/close the right-click menu (stable identities for ContextMenu effects).
  const handleOpenMenu = useCallback((x: number, y: number) => {
    setMenu({ x, y });
  }, []);
  const handleCloseMenu = useCallback(() => setMenu(null), []);

  // --------------------------------------------------------------------- //
  // Reorder photo strip (changes sequence used by the next auto-layout).
  // --------------------------------------------------------------------- //
  const handleReorder = useCallback(
    (images: ImageOut[]) => {
      const prev = latestRef.current;
      if (!prev) return;
      const next = { ...prev, images };
      // Synchronous ref update before runAutoLayout reads it (see note in
      // handleSettingsChange about setProject's deferred updater).
      latestRef.current = next;
      setProject(next);
      // Persist the new image order to the backend FIRST. Auto-layout reads the
      // stored order, so without this the regenerate (and its response) would
      // revert the strip to the original order. In manual mode we then
      // regenerate so the new sequence is reflected on the canvas immediately.
      void (async () => {
        try {
          await api.updateProject(next.id, {
            image_order: images.map((i) => i.id),
          });
          if (latestRef.current?.settings.order_mode === "manual") {
            await runAutoLayout();
          }
        } catch (e) {
          setError(describe(e, "Could not reorder photos."));
        }
      })();
    },
    [runAutoLayout],
  );

  // --------------------------------------------------------------------- //
  // Export / download.
  // --------------------------------------------------------------------- //
  const handleDownload = useCallback(
    async (format: ExportFormat) => {
      const proj = latestRef.current;
      if (!proj) return;
      setExporting(true);
      setExportError(null);
      setDownloadMenuOpen(false);
      try {
        // Wait for any in-flight auto-layout to settle FIRST, otherwise we'd
        // PUT the stale (pre-regeneration) layout and the auto-layout would
        // overwrite server state afterwards — making the download desync from
        // the screen.
        if (pendingAutoLayoutRef.current) {
          await pendingAutoLayoutRef.current;
        }
        // Persist the current settings+layout next so the server renders
        // exactly what's on screen.
        await flushPut();
        const result = await api.exportProject(proj.id, format);
        // Trigger downloads via hidden <a download>.
        if ((format === "png" || format === "both") && result.png_ready) {
          triggerDownload(api.downloadUrl(proj.id, "png"));
        }
        if ((format === "pdf" || format === "both") && result.pdf_ready) {
          triggerDownload(api.downloadUrl(proj.id, "pdf"));
        }
      } catch (e) {
        setExportError(describe(e, "Export failed."));
      } finally {
        setExporting(false);
      }
    },
    [flushPut],
  );

  // --------------------------------------------------------------------- //
  // Render.
  // --------------------------------------------------------------------- //
  if (loading) {
    return (
      <div className="app-loading">
        <div className="spinner" />
        <p>Loading collage…</p>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="app-loading">
        <p className="banner-error">{error ?? "Something went wrong."}</p>
        <button className="btn" onClick={handleNewCollage}>
          Start a new collage
        </button>
      </div>
    );
  }

  const hasImages = project.images.length > 0;
  // Block exporting while an arrangement regeneration or upload is in flight so
  // the user can't trigger a download mid-regeneration (which could desync).
  const exportBusy = exporting || autoLayouting || uploading;

  return (
    <div className="app">
      <header className="toolbar">
        <BrandLogo />
        <div className="toolbar-actions">
          <button className="btn btn-ghost" onClick={handleNewCollage}>
            New collage
          </button>
          <button className="btn btn-ghost" onClick={() => setHistoryOpen(true)}>
            My collages
          </button>
          <button
            className="btn btn-ghost"
            onClick={handleRandomize}
            disabled={!hasImages}
          >
            Randomize
          </button>

          <div className="download">
            <div className="split-btn">
              <button
                className="btn btn-primary"
                onClick={() => handleDownload("png")}
                disabled={!hasImages || exportBusy}
              >
                {exporting ? "Rendering…" : "Download collage"}
              </button>
              <button
                className="btn btn-primary split-caret"
                onClick={() => setDownloadMenuOpen((o) => !o)}
                disabled={!hasImages || exportBusy}
                aria-label="Choose download format"
              >
                ▾
              </button>
            </div>
            {downloadMenuOpen && (
              <ul className="download-menu">
                <li>
                  <button
                    onClick={() => handleDownload("png")}
                    disabled={exportBusy}
                  >
                    PNG
                  </button>
                </li>
                <li>
                  <button
                    onClick={() => handleDownload("pdf")}
                    disabled={exportBusy}
                  >
                    PDF
                  </button>
                </li>
              </ul>
            )}
            {exportError && (
              <div className="download-error">{exportError}</div>
            )}
          </div>

          <div className="toolbar-user">
            <span className="user-email" title={user ?? ""}>
              {user}
            </span>
            <button className="btn btn-ghost" onClick={() => void logout()}>
              Logout
            </button>
          </div>
        </div>
      </header>

      {error && (
        <div className="banner-error" role="alert">
          {error}
          <button className="banner-close" onClick={() => setError(null)}>
            ✕
          </button>
        </div>
      )}

      <div className="layout">
        <aside className="panel panel-left">
          <h2 className="panel-heading">Photos</h2>
          <Uploader onFiles={handleFiles} busy={uploading} />
          {hasImages && project.settings.order_mode === "random" && (
            <p className="strip-hint">
              Order is randomized by seed — switch to Manual order to control the
              sequence.
            </p>
          )}
          <div className="strip-wrap">
            <PhotoStrip
              images={project.images}
              selectedIds={selectedIds}
              onSelect={handleSelect}
              onReorder={handleReorder}
            />
          </div>
        </aside>

        <main className="stage">
          <CollageCanvas
            settings={project.settings}
            layout={project.layout}
            imagesById={imagesById}
            selectedIds={selectedIds}
            onSelect={handleSelect}
            onClearSelection={clearSelection}
            onMovePositions={handleMovePositions}
            onRotateItems={handleRotateItems}
            onResizeItems={handleResizeItems}
            onDropFiles={handleFiles}
            onOpenMenu={handleOpenMenu}
          />
          {(uploading || autoLayouting) && (
            <div className="stage-overlay" role="status" aria-live="polite">
              <div className="spinner" />
              <p>
                {uploading
                  ? uploadProgress && uploadProgress.total > 1
                    ? `Uploading photos… ${uploadProgress.done}/${uploadProgress.total}`
                    : "Uploading photos…"
                  : "Building your collage…"}
              </p>
            </div>
          )}
        </main>

        <aside
          className={"panel panel-right" + (settingsSheetOpen ? " sheet-open" : "")}
        >
          <div className="sheet-head">
            <h2 className="panel-heading">Settings</h2>
            <button
              className="sheet-close"
              onClick={() => setSettingsSheetOpen(false)}
            >
              Done
            </button>
          </div>
          <SettingsPanel
            settings={project.settings}
            onSettingsChange={handleSettingsChange}
            primaryItem={primaryItem}
            selectedCount={selectedIds.length}
            onItemChange={handleItemChange}
            onSizeChange={handleSizeChange}
            onRotateBy={handleRotateBy}
            onBringForward={handleBringForward}
            onSendBackward={handleSendBackward}
            onBringToFront={handleBringToFront}
            onSendToBack={handleSendToBack}
            onResetItem={handleResetItem}
            onDeleteItem={handleDeleteSelected}
          />
        </aside>
      </div>

      {/* Mobile bottom action bar: thumb-reachable primary actions. */}
      <nav className="mobile-actionbar" aria-label="Quick actions">
        <button
          type="button"
          className="mab-btn"
          onClick={() => addInputRef.current?.click()}
        >
          <span className="mab-ico" aria-hidden="true">＋</span>
          <span>Add</span>
        </button>
        <button
          type="button"
          className="mab-btn"
          onClick={handleRandomize}
          disabled={!hasImages}
        >
          <span className="mab-ico" aria-hidden="true">🎲</span>
          <span>Shuffle</span>
        </button>
        <button
          type="button"
          className="mab-btn mab-primary"
          onClick={() => handleDownload("png")}
          disabled={!hasImages || exportBusy}
        >
          <span className="mab-ico" aria-hidden="true">⬇</span>
          <span>{exporting ? "…" : "Download"}</span>
        </button>
        <button
          type="button"
          className="mab-btn"
          onClick={() => setSettingsSheetOpen(true)}
        >
          <span className="mab-ico" aria-hidden="true">⚙</span>
          <span>Settings</span>
        </button>
      </nav>

      {settingsSheetOpen && (
        <div
          className="sheet-backdrop"
          onClick={() => setSettingsSheetOpen(false)}
          aria-hidden="true"
        />
      )}

      <input
        ref={addInputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) void handleFiles(files);
          e.target.value = "";
        }}
      />

      {menu && (
        <ContextMenu
          state={menu}
          count={selectedIds.length}
          onClose={handleCloseMenu}
          onDelete={handleDeleteSelected}
          onBringToFront={handleBringToFront}
          onSendToBack={handleSendToBack}
          onBringForward={handleBringForward}
          onSendBackward={handleSendBackward}
          onRotateBy={handleRotateBy}
          onZoom={handleZoom}
          onReset={handleResetItem}
        />
      )}

      {historyOpen && (
        <HistoryView
          onClose={() => setHistoryOpen(false)}
          onOpen={handleOpenProject}
          onNew={handleNewCollage}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------------- //
// Helpers
// ------------------------------------------------------------------------- //
function triggerDownload(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = ""; // let the server's Content-Disposition filename win
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// Turn an error into a human-friendly message. We map the few HTTP statuses
// users can realistically hit to plain language, and otherwise keep a generic
// fallback rather than leaking raw status codes / server internals.
function describe(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    switch (e.status) {
      case 413:
        return "Upload too large for the server's limit — try a smaller photo, or raise the reverse-proxy upload size limit.";
      case 400:
        return "That file isn't a supported image.";
      case 404:
        return `${fallback} It may have been removed.`;
      default:
        return `${fallback} Please try again.`;
    }
  }
  if (e instanceof Error) {
    // Network/parse errors carry no useful status — keep it generic.
    return `${fallback} Please check your connection and try again.`;
  }
  return fallback;
}
