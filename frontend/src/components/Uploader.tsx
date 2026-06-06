import { useCallback, useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

interface UploaderProps {
  onFiles: (files: File[]) => void;
  busy: boolean;
}

/** Multi-file drag-and-drop upload zone plus a click-to-pick fallback. */
export default function Uploader({ onFiles, busy }: UploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);

  const pickImages = useCallback(
    (fileList: FileList | null) => {
      if (!fileList) return;
      const files = Array.from(fileList).filter((f) =>
        f.type.startsWith("image/"),
      );
      if (files.length > 0) onFiles(files);
    },
    [onFiles],
  );

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragActive(false);
      pickImages(e.dataTransfer.files);
    },
    [pickImages],
  );

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    if (Array.from(e.dataTransfer.types).includes("Files")) {
      e.preventDefault();
      setDragActive(true);
    }
  }, []);

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      pickImages(e.target.files);
      // Reset so picking the same file again still fires onChange.
      e.target.value = "";
    },
    [pickImages],
  );

  return (
    <div
      className={"uploader" + (dragActive ? " uploader-active" : "")}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={() => setDragActive(false)}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={handleChange}
      />
      <div className="uploader-icon" aria-hidden="true">
        ⬆
      </div>
      <div className="uploader-text">
        {busy ? "Uploading…" : "Drop photos here"}
      </div>
      <div className="uploader-sub">or click to browse</div>
    </div>
  );
}
