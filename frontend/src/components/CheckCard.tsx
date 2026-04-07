import { CheckCircle2, XCircle } from "lucide-react";
import MetricCard from "./MetricCard";

interface Props {
  label: string;
  passed: boolean;
  details: Record<string, unknown>;
  reason?: string;
}

export default function CheckCard({ label, passed, details, reason }: Props) {
  const displayItems = Object.entries(details).filter(
    ([k, v]) => k !== "error" && k !== "source" && v != null,
  );

  return (
    <div className="mb-4">
      <div
        className={`flex items-center gap-2 px-4 py-2.5 rounded-lg font-semibold ${
          passed ? "bg-green-50 text-primary-dark" : "bg-red-50 text-danger"
        }`}
      >
        {passed ? <CheckCircle2 className="w-5 h-5" /> : <XCircle className="w-5 h-5" />}
        {label}: {passed ? "PASS" : "FAIL"}
      </div>
      {!passed && reason && (
        <p className="text-sm text-text-muted mt-1 ml-1 italic">{reason}</p>
      )}
      {displayItems.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
          {displayItems.map(([k, v]) => (
            <MetricCard
              key={k}
              label={k.replace(/_/g, " ")}
              value={formatMetric(v)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function formatMetric(v: unknown): string {
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : (v as number).toFixed(2);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v).slice(0, 40);
}
