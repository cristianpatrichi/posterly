import { useEffect, useState } from "react";
import type { MouseEvent } from "react";
import * as api from "../api";
import type { ProjectSummary } from "../types";

interface HistoryViewProps {
  onClose: () => void;
  onOpen: (id: string) => void;
  onNew: () => void;
}

/** "My collages": the signed-in user's saved projects (thumbnail + date),
 *  clickable to reopen, with delete. */
export default function HistoryView({ onClose, onOpen, onNew }: HistoryViewProps) {
  const [items, setItems] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api.listProjects();
        if (!cancelled) setItems(list);
      } catch {
        if (!cancelled) setError("Could not load your collages.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function remove(id: string, e: MouseEvent) {
    e.stopPropagation();
    try {
      await api.deleteProject(id);
      setItems((prev) => (prev ? prev.filter((p) => p.id !== id) : prev));
    } catch {
      setError("Could not delete that collage.");
    }
  }

  return (
    <div className="modal-overlay" onPointerDown={onClose}>
      <div className="modal" onPointerDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>My collages</h2>
          <div className="modal-head-actions">
            <button className="btn btn-primary" onClick={onNew}>
              + New collage
            </button>
            <button className="banner-close" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>
        {error && <p className="login-error">{error}</p>}
        {items === null ? (
          <p className="modal-empty">Loading…</p>
        ) : items.length === 0 ? (
          <p className="modal-empty">No collages yet — make one!</p>
        ) : (
          <ul className="history-grid">
            {items.map((p) => (
              <li key={p.id} className="history-card" onClick={() => onOpen(p.id)}>
                <div className="history-thumb">
                  {p.thumb ? (
                    <img
                      src={`/api/projects/${p.id}/images/${p.thumb}?preview=1`}
                      alt=""
                    />
                  ) : (
                    <div className="history-thumb-empty">no photos</div>
                  )}
                </div>
                <div className="history-meta">
                  <span>
                    {p.image_count} photo{p.image_count === 1 ? "" : "s"}
                  </span>
                  <span className="history-date">
                    {new Date(p.updated_at * 1000).toLocaleDateString()}
                  </span>
                </div>
                <button
                  className="history-del"
                  onClick={(e) => remove(p.id, e)}
                  aria-label="Delete collage"
                  title="Delete"
                >
                  🗑
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
