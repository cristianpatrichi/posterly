import type { LayoutItem, Settings } from "../types";

interface SettingsPanelProps {
  settings: Settings;
  onSettingsChange: (patch: Partial<Settings>) => void;
  // The "primary" (last-selected) item feeds the slider readouts; all edits in
  // this panel apply to every selected item (selectedCount of them).
  primaryItem: LayoutItem | null;
  selectedCount: number;
  onItemChange: (patch: Partial<LayoutItem>) => void;
  onSizeChange: (width: number) => void;
  onRotateBy: (deltaDeg: number) => void;
  onBringForward: () => void;
  onSendBackward: () => void;
  onBringToFront: () => void;
  onSendToBack: () => void;
  onResetItem: () => void;
  onDeleteItem: () => void;
}

interface SegOption<T extends string> {
  value: T;
  label: string;
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: SegOption<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="group">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={"seg-btn" + (opt.value === value ? " seg-btn-on" : "")}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function Slider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        <span className="field-value">{Math.round(value * 100)}%</span>
      </span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

export default function SettingsPanel({
  settings,
  onSettingsChange,
  primaryItem,
  selectedCount,
  onItemChange,
  onSizeChange,
  onRotateBy,
  onBringForward,
  onSendBackward,
  onBringToFront,
  onSendToBack,
  onResetItem,
  onDeleteItem,
}: SettingsPanelProps) {
  return (
    <div className="panel-scroll">
      <section className="panel-section">
        <h3 className="panel-title">Layout</h3>

        <p className="panel-hint">
          Changing orientation, spacing, rotation, order or Randomize
          regenerates the layout (manual moves are reset).
        </p>

        <label className="field">
          <span className="field-label">Orientation</span>
          <Segmented
            value={settings.orientation}
            onChange={(orientation) => onSettingsChange({ orientation })}
            options={[
              { value: "landscape", label: "Landscape" },
              { value: "portrait", label: "Portrait" },
            ]}
          />
        </label>

        <label className="field">
          <span className="field-label">
            Paper size
            <span className="field-value">300 DPI</span>
          </span>
          <select
            className="num-input"
            value={settings.paper_size}
            onChange={(e) =>
              onSettingsChange({
                paper_size: e.target.value as Settings["paper_size"],
              })
            }
          >
            <optgroup label="A series">
              <option value="A5">A5 (148×210 mm)</option>
              <option value="A4">A4 (210×297 mm)</option>
              <option value="A3">A3 (297×420 mm)</option>
              <option value="A2">A2 (420×594 mm)</option>
              <option value="A1">A1 (594×841 mm)</option>
              <option value="A0">A0 (841×1189 mm)</option>
            </optgroup>
            <optgroup label="US">
              <option value="letter">Letter (8.5×11 in)</option>
              <option value="legal">Legal (8.5×14 in)</option>
            </optgroup>
            <optgroup label="Poster (cm)">
              <option value="30x40cm">30 × 40 cm</option>
              <option value="50x70cm">50 × 70 cm</option>
              <option value="70x100cm">70 × 100 cm</option>
              <option value="100x100cm">100 × 100 cm (1 m)</option>
              <option value="100x140cm">100 × 140 cm (1 m)</option>
            </optgroup>
          </select>
        </label>

        <label className="field">
          <span className="field-label">Style</span>
          <Segmented
            value={settings.look}
            onChange={(look) => onSettingsChange({ look })}
            options={[
              { value: "soft-oval", label: "Soft oval" },
              { value: "paper", label: "Rectangular" },
            ]}
          />
        </label>

        <label className="field">
          <span className="field-label">Order</span>
          <Segmented
            value={settings.order_mode}
            onChange={(order_mode) => onSettingsChange({ order_mode })}
            options={[
              { value: "random", label: "Random" },
              { value: "manual", label: "Manual" },
            ]}
          />
        </label>
      </section>

      <section className="panel-section">
        <h3 className="panel-title">Arrangement</h3>
        <Slider
          label="Spacing"
          value={settings.spacing}
          onChange={(spacing) => onSettingsChange({ spacing })}
        />
        <Slider
          label="Rotation intensity"
          value={settings.rotation_intensity}
          onChange={(rotation_intensity) =>
            onSettingsChange({ rotation_intensity })
          }
        />
      </section>

      <section className="panel-section">
        <h3 className="panel-title">Appearance</h3>
        <Slider
          label="Border size"
          value={settings.border}
          onChange={(border) => onSettingsChange({ border })}
        />
        <Slider
          label={
            selectedCount > 0
              ? `Glow / feather (${selectedCount} selected)`
              : "Glow / feather"
          }
          value={
            selectedCount > 0 && primaryItem
              ? primaryItem.feather ?? settings.feather
              : settings.feather
          }
          onChange={(feather) =>
            selectedCount > 0
              ? onItemChange({ feather })
              : onSettingsChange({ feather })
          }
        />

        <label className="field">
          <span className="field-label">Background</span>
          <div className="color-row">
            <input
              type="color"
              value={normalizeHex(settings.background)}
              onChange={(e) => onSettingsChange({ background: e.target.value })}
            />
            <input
              type="text"
              className="color-text"
              value={settings.background}
              onChange={(e) => onSettingsChange({ background: e.target.value })}
            />
          </div>
        </label>

        <label className="field">
          <span className="field-label">Seed</span>
          <input
            type="number"
            className="num-input"
            value={settings.seed}
            onChange={(e) =>
              onSettingsChange({ seed: Math.trunc(Number(e.target.value)) || 0 })
            }
          />
        </label>

        <div className="field">
          <span className="field-label">
            Margin guide
            <span className="field-value">preview only</span>
          </span>
          <div className="guide-row">
            <label className="guide-toggle">
              <input
                type="checkbox"
                checked={settings.margin_guide}
                onChange={(e) =>
                  onSettingsChange({ margin_guide: e.target.checked })
                }
              />
              <span>Show</span>
            </label>
            <input
              type="number"
              className="num-input guide-mm"
              min={0}
              max={200}
              step={1}
              value={settings.margin_guide_mm}
              disabled={!settings.margin_guide}
              onChange={(e) =>
                onSettingsChange({
                  margin_guide_mm: Math.max(0, Number(e.target.value) || 0),
                })
              }
              aria-label="Margin guide size in millimetres"
            />
            <span className="guide-unit">mm</span>
          </div>
          <p className="panel-hint">
            Dashed frame shown only in this preview — it is never printed/exported.
          </p>
        </div>
      </section>

      {selectedCount > 0 && primaryItem && (
        <section className="panel-section panel-selected">
          <h3 className="panel-title">
            {selectedCount === 1
              ? "Selected photo"
              : `${selectedCount} photos selected`}
          </h3>

          {selectedCount > 1 && (
            <p className="panel-hint">
              Changes apply to all {selectedCount} selected photos. Drag any one
              to move them together.
            </p>
          )}

          <label className="field">
            <span className="field-label">
              Rotation
              <span className="field-value">
                {Math.round(primaryItem.rotation)}°
              </span>
            </span>
            <input
              type="range"
              min={-45}
              max={45}
              step={1}
              value={primaryItem.rotation}
              onChange={(e) =>
                onItemChange({ rotation: Number(e.target.value) })
              }
            />
          </label>

          <div className="btn-row">
            <button
              type="button"
              className="mini-btn"
              onClick={() => onRotateBy(-5)}
            >
              ⟲ -5°
            </button>
            <button
              type="button"
              className="mini-btn"
              onClick={() => onRotateBy(5)}
            >
              ⟳ +5°
            </button>
          </div>

          <Slider label="Size" value={primaryItem.width} onChange={onSizeChange} />

          <div className="btn-row">
            <button type="button" className="mini-btn" onClick={onSendBackward}>
              Send backward
            </button>
            <button type="button" className="mini-btn" onClick={onBringForward}>
              Bring forward
            </button>
          </div>

          <div className="btn-row">
            <button type="button" className="mini-btn" onClick={onSendToBack}>
              ⤓ To back
            </button>
            <button type="button" className="mini-btn" onClick={onBringToFront}>
              ⤒ To front
            </button>
          </div>

          <button
            type="button"
            className="mini-btn mini-btn-wide"
            onClick={onResetItem}
          >
            {selectedCount === 1 ? "Reset photo" : "Reset photos"}
          </button>

          <button
            type="button"
            className="mini-btn mini-btn-wide mini-btn-danger"
            onClick={onDeleteItem}
          >
            🗑 {selectedCount === 1 ? "Delete photo" : `Delete ${selectedCount} photos`}
          </button>

          <p className="panel-hint">Tip: right-click a photo for quick actions.</p>
        </section>
      )}
    </div>
  );
}

/** The native <input type="color"> needs a `#rrggbb`. Fall back to a neutral
 * value for css names / shorthand so the swatch still renders without crashing
 * the controlled input. The free-text box keeps the user's exact value. */
function normalizeHex(value: string): string {
  if (/^#[0-9a-fA-F]{6}$/.test(value)) return value;
  if (/^#[0-9a-fA-F]{3}$/.test(value)) {
    const r = value[1] ?? "0";
    const g = value[2] ?? "0";
    const b = value[3] ?? "0";
    return `#${r}${r}${g}${g}${b}${b}`;
  }
  return "#f4efe6";
}
