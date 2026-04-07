import { cn } from "@/lib/utils";

const styles: Record<string, string> = {
  pending: "bg-amber-50 text-amber-700",
  running: "bg-green-50 text-primary",
  completed: "bg-green-100 text-primary-dark",
  failed: "bg-red-50 text-danger",
  cancelled: "bg-gray-100 text-gray-600",
  accept: "bg-green-100 text-primary-dark",
  reject: "bg-red-50 text-danger",
};

interface Props {
  status: string;
  className?: string;
}

export default function StatusPill({ status, className }: Props) {
  const key = status.toLowerCase();
  return (
    <span
      className={cn(
        "inline-block px-2.5 py-0.5 rounded-full text-[0.72rem] font-semibold tracking-wider",
        styles[key] || "bg-gray-100 text-gray-600",
        className,
      )}
    >
      {status.toUpperCase()}
    </span>
  );
}
