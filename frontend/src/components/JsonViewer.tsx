import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface Props {
  data: unknown;
  title?: string;
  defaultOpen?: boolean;
}

export default function JsonViewer({ data, title = "View JSON", defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border border-border rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-4 py-3 text-sm font-medium text-text-muted hover:bg-green-50/50 transition-colors"
      >
        {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        {title}
      </button>
      {open && (
        <pre className="px-4 pb-4 text-xs overflow-auto max-h-96 bg-gray-50">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}
