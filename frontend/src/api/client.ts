import axios, { type AxiosProgressEvent } from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 30_000,
});

// ── Health ───────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  mongo: string;
  platform: string;
  version?: string;
  registered_job_types: string[];
}

export async function getHealth(): Promise<HealthResponse | null> {
  try {
    const { data } = await api.get<HealthResponse>("/health");
    return data;
  } catch {
    return null;
  }
}

// ── Upload ───────────────────────────────────────────────────────────

export interface UploadResponse {
  file_id: string;
  filename: string;
  path: string;
  size_bytes: number;
}

export async function uploadFile(
  file: File,
  user: string = "ui",
  onProgress?: (pct: number) => void,
): Promise<UploadResponse | null> {
  try {
    const form = new FormData();
    form.append("file", file);
    const { data } = await api.post<UploadResponse>("/upload", form, {
      params: { user },
      timeout: 60_000,
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e: AxiosProgressEvent) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
      },
    });
    return data;
  } catch {
    return null;
  }
}

export async function uploadFiles(
  files: File[],
  user: string = "ui",
): Promise<UploadResponse[]> {
  const results: UploadResponse[] = [];
  for (const f of files) {
    const r = await uploadFile(f, user);
    if (r) results.push(r);
  }
  return results;
}

// ── Jobs ─────────────────────────────────────────────────────────────

export interface JobCounts {
  total: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export interface Job {
  id: string;
  job_type: string;
  status: string;
  params: Record<string, unknown>;
  tags: string[];
  user: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  progress: number;
  progress_message: string;
}

export interface JobListResponse {
  jobs: Job[];
  counts: JobCounts;
  total: number;
}

export async function listJobs(
  params: Record<string, string | number> = {},
): Promise<JobListResponse | null> {
  try {
    const { data } = await api.get<JobListResponse>("/jobs", { params });
    return data;
  } catch {
    return null;
  }
}

export async function getJob(jobId: string): Promise<Job | null> {
  try {
    const { data } = await api.get<Job>(`/jobs/${jobId}`);
    return data;
  } catch {
    return null;
  }
}

export async function submitJob(
  endpoint: string,
  payload: Record<string, unknown>,
): Promise<{ job_id: string } | null> {
  try {
    const { data } = await api.post<{ job_id: string }>(endpoint, payload);
    return data;
  } catch {
    return null;
  }
}

export async function cancelJob(
  jobId: string,
  user: string = "ui",
): Promise<{ success: boolean } | null> {
  try {
    const { data } = await api.post(`/jobs/${jobId}/cancel`, null, { params: { user } });
    return data;
  } catch {
    return null;
  }
}

export async function retryJob(
  jobId: string,
  user: string = "ui",
): Promise<{ job_id: string } | null> {
  try {
    const { data } = await api.post(`/jobs/${jobId}/retry`, null, { params: { user } });
    return data;
  } catch {
    return null;
  }
}

export async function removeJob(
  jobId: string,
  user: string = "ui",
): Promise<{ success: boolean } | null> {
  try {
    const { data } = await api.delete(`/jobs/${jobId}`, { params: { user } });
    return data;
  } catch {
    return null;
  }
}

export async function purgeJobs(
  olderThanHours: number = 24,
  user: string = "ui",
): Promise<{ deleted: number } | null> {
  try {
    const { data } = await api.post(
      "/jobs/purge",
      { older_than_hours: olderThanHours },
      { params: { user } },
    );
    return data;
  } catch {
    return null;
  }
}

// ── Audit ────────────────────────────────────────────────────────────

export interface AuditEntry {
  action: string;
  user: string;
  timestamp: string;
  level: string;
  job_id: string | null;
  detail: Record<string, unknown>;
  result: Record<string, unknown> | null;
}

export interface AuditListResponse {
  logs: AuditEntry[];
  total: number;
}

export async function getAuditLogs(
  params: Record<string, string | number> = {},
): Promise<AuditListResponse | null> {
  try {
    const { data } = await api.get<AuditListResponse>("/audit", { params });
    return data;
  } catch {
    return null;
  }
}

// ── Sync ─────────────────────────────────────────────────────────────

export interface SyncStatus {
  pending: number;
  synced: number;
  failed: number;
}

export interface SyncRecentItem {
  job_type: string;
  file_path: string;
  synced_at: string;
  combined_result: Record<string, unknown>;
}

export async function getSyncStatus(): Promise<SyncStatus | null> {
  try {
    const { data } = await api.get<SyncStatus>("/sync/status", { timeout: 5000 });
    return data;
  } catch {
    return null;
  }
}

export async function getSyncOnline(): Promise<boolean> {
  try {
    const { data } = await api.get<{ online: boolean }>("/sync/online", { timeout: 5000 });
    return data.online;
  } catch {
    return false;
  }
}

export async function getSyncRecent(
  limit: number = 5,
): Promise<{ items: SyncRecentItem[] } | null> {
  try {
    const { data } = await api.get("/sync/recent", { params: { limit }, timeout: 5000 });
    return data;
  } catch {
    return null;
  }
}

export async function enqueuSync(payload: {
  job_type: string;
  file_path: string;
  offline_result: Record<string, unknown>;
  user: string;
}): Promise<void> {
  try {
    await api.post("/sync/enqueue", payload, { timeout: 10_000 });
  } catch {
    // silently fail like the Streamlit version
  }
}

// ── UC1 specific ─────────────────────────────────────────────────────

export async function uc1QualityCheck(
  file: File,
  user: string = "ui",
): Promise<Record<string, unknown> | null> {
  try {
    const form = new FormData();
    form.append("file", file);
    const { data } = await api.post("/uc1/quality-check", form, {
      params: { user },
      timeout: 30_000,
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  } catch {
    return null;
  }
}

// ── UC2 specific ─────────────────────────────────────────────────────

export async function uc2QualityCheck(
  file: File,
  user: string = "ui",
): Promise<Record<string, unknown> | null> {
  try {
    const form = new FormData();
    form.append("file", file);
    const { data } = await api.post("/uc2/quality-check", form, {
      params: { user },
      timeout: 30_000,
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  } catch {
    return null;
  }
}
