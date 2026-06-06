import { useEffect, useRef } from "react";

export interface ContextMenuState {
  x: number;
  y: number;
}

interface ContextMenuProps {
  state: ContextMenuState;
  count: number;
  onClose: () => void;
  onDelete: () => void;
  onBringToFront: () => void;
  onSendToBack: () => void;
  onBringForward: () => void;
  onSendBackward: () => void;
  onRotateBy: (deg: number) => void;
  onZoom: (factor: number) => void;
  onReset: () => void;
}

/** Custom right-click menu of quick actions for the selected photo(s). It is
 *  positioned at the cursor and closes on action, outside click, scroll or Esc. */
export default function ContextMenu({
  state,
  count,
  onClose,
  onDelete,
  onBringToFront,
  onSendToBack,
  onBringForward,
  onSendBackward,
  onRotateBy,
  onZoom,
  onReset,
}: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = () => onClose();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    // Escape works immediately (deterministic). The outside-pointerdown/scroll
    // closers are deferred a tick so the opening right-click doesn't close it.
    window.addEventListener("keydown", onKey);
    const id = window.setTimeout(() => {
      window.addEventListener("pointerdown", close);
      window.addEventListener("scroll", close, true);
      window.addEventListener("resize", close);
    }, 0);
    return () => {
      window.clearTimeout(id);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [onClose]);

  // Keep the menu inside the viewport.
  const MENU_W = 210;
  const MENU_H = 360;
  const left = Math.min(state.x, window.innerWidth - MENU_W - 8);
  const top = Math.min(state.y, window.innerHeight - MENU_H - 8);

  const run = (fn: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    fn();
    onClose();
  };

  const label = count > 1 ? `${count} photos` : "photo";

  return (
    <div
      ref={ref}
      className="context-menu"
      style={{ left, top }}
      onPointerDown={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="context-menu-head">{label}</div>
      <button className="ctx-item" onClick={run(onBringToFront)}>
        ⤒ Bring to front
      </button>
      <button className="ctx-item" onClick={run(onSendToBack)}>
        ⤓ Send to back
      </button>
      <button className="ctx-item" onClick={run(onBringForward)}>
        ↑ Bring forward
      </button>
      <button className="ctx-item" onClick={run(onSendBackward)}>
        ↓ Send backward
      </button>
      <div className="context-menu-sep" />
      <button className="ctx-item" onClick={run(() => onRotateBy(-15))}>
        ⟲ Rotate left 15°
      </button>
      <button className="ctx-item" onClick={run(() => onRotateBy(15))}>
        ⟳ Rotate right 15°
      </button>
      <button className="ctx-item" onClick={run(() => onZoom(1.1))}>
        ＋ Zoom in
      </button>
      <button className="ctx-item" onClick={run(() => onZoom(1 / 1.1))}>
        － Zoom out
      </button>
      <button className="ctx-item" onClick={run(onReset)}>
        ↺ Reset
      </button>
      <div className="context-menu-sep" />
      <button className="ctx-item ctx-item-danger" onClick={run(onDelete)}>
        🗑 Delete {label}
      </button>
    </div>
  );
}
