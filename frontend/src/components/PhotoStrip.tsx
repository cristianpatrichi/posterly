import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { ImageOut } from "../types";

interface PhotoStripProps {
  images: ImageOut[];
  selectedIds: string[];
  onSelect: (imageId: string, additive: boolean) => void;
  onReorder: (images: ImageOut[]) => void;
}

interface RowProps {
  image: ImageOut;
  index: number;
  selected: boolean;
  onSelect: (imageId: string, additive: boolean) => void;
}

function PhotoRow({ image, index, selected, onSelect }: RowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: image.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      className={"strip-row" + (selected ? " strip-row-selected" : "")}
      onClick={(e) => onSelect(image.id, e.metaKey || e.ctrlKey || e.shiftKey)}
    >
      <span
        className="strip-handle"
        aria-label="Drag to reorder"
        {...attributes}
        {...listeners}
      >
        ⠿
      </span>
      <span className="strip-index">{index + 1}</span>
      <img className="strip-thumb" src={image.preview_url} alt={image.name} />
      <span className="strip-name" title={image.name}>
        {image.name}
      </span>
    </li>
  );
}

/** Vertical, dnd-kit sortable list. Reordering changes the sequence used for the
 * next auto-layout. Canvas z-order is controlled separately (bring/send). */
export default function PhotoStrip({
  images,
  selectedIds,
  onSelect,
  onReorder,
}: PhotoStripProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = images.findIndex((i) => i.id === active.id);
    const newIndex = images.findIndex((i) => i.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    onReorder(arrayMove(images, oldIndex, newIndex));
  };

  if (images.length === 0) {
    return <p className="strip-empty">No photos yet.</p>;
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={images.map((i) => i.id)}
        strategy={verticalListSortingStrategy}
      >
        <ul className="strip">
          {images.map((image, index) => (
            <PhotoRow
              key={image.id}
              image={image}
              index={index}
              selected={selectedIds.includes(image.id)}
              onSelect={onSelect}
            />
          ))}
        </ul>
      </SortableContext>
    </DndContext>
  );
}
