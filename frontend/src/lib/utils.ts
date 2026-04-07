import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function relativeTime(isoStr: string | null | undefined): string {
  if (!isoStr) return "—";
  try {
    const dt = new Date(isoStr);
    const secs = (Date.now() - dt.getTime()) / 1000;
    if (secs < 60) return "just now";
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
  } catch {
    return String(isoStr).slice(0, 16);
  }
}

export function formatTime(isoStr: string | null | undefined): string {
  if (!isoStr) return "—";
  try {
    const dt = new Date(isoStr);
    return dt.toLocaleDateString("en-US", {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return String(isoStr).slice(0, 16);
  }
}

export function formatDuration(
  started: string | null | undefined,
  ended: string | null | undefined,
): string {
  if (!started) return "—";
  try {
    const s = new Date(started).getTime();
    const e = ended ? new Date(ended).getTime() : Date.now();
    const sec = (e - s) / 1000;
    return sec < 60 ? `${sec.toFixed(1)}s` : `${(sec / 60).toFixed(1)}m`;
  } catch {
    return "—";
  }
}

export function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "…" : s;
}

export function basename(path: string): string {
  return path.split(/[/\\]/).pop() || path;
}
