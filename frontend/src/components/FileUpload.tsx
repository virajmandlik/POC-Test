import { useCallback, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  accept: string;
  multiple?: boolean;
  label?: string;
  onFiles: (files: File[]) => void;
}

export default function FileUpload({
  accept,
  multiple = false,
  label = "Drop files here or click to upload",
  onFiles,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) onFiles(Array.from(e.target.files));
    },
    [onFiles],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      if (e.dataTransfer.files) onFiles(Array.from(e.dataTransfer.files));
    },
    [onFiles],
  );

  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={cn(
        "border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all",
        dragging
          ? "border-primary bg-primary/5"
          : "border-border hover:border-primary-light hover:bg-green-50/30",
      )}
    >
      <Upload className="w-8 h-8 mx-auto text-text-muted mb-2" />
      <p className="text-sm text-text-muted">{label}</p>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        onChange={handleChange}
        className="hidden"
      />
    </div>
  );
}
