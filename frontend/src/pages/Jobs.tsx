import { useState, useCallback } from "react";
import PageHeader from "@/components/PageHeader";
import MetricCard from "@/components/MetricCard";
import StatusPill from "@/components/StatusPill";
import JsonViewer from "@/components/JsonViewer";
import { useHealth } from "@/hooks/useHealth";
import { useJobList } from "@/hooks/useJobs";
import { cancelJob, retryJob, removeJob, purgeJobs, getJob } from "@/api/client";
import { formatTime, formatDuration } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";

export default function Jobs() {
  const qc = useQueryClient();
  const { data: health } = useHealth();

  const [filters, setFilters] = useState({
    pending: true,
    running: true,
    completed: true,
    failed: true,
    cancelled: true,
  });
  const [filterType, setFilterType] = useState("All");
  const [filterUser, setFilterUser] = useState("");
  const [filterLimit, setFilterLimit] = useState(50);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [detailData, setDetailData] = useState<Record<string, unknown> | null>(null);
  const [purgeHours, setPurgeHours] = useState(24);

  const statusList = Object.entries(filters)
    .filter(([, v]) => v)
    .map(([k]) => k);

  const params: Record<string, string | number> = { limit: filterLimit };
  if (statusList.length > 0 && statusList.length < 5) params.status = statusList.join(",");
  if (filterType !== "All") params.job_type = filterType;
  if (filterUser) params.user = filterUser;

  const { data: jobData } = useJobList(params, autoRefresh ? 5000 : undefined);
  const counts = jobData?.counts;
  const jobs = jobData?.jobs || [];
  const types = ["All", ...(health?.registered_job_types || [])];

  const refresh = useCallback(() => qc.invalidateQueries({ queryKey: ["jobs"] }), [qc]);

  const handleAction = useCallback(
    async (action: string) => {
      if (!selectedId) return;
      if (action === "Details") {
        const j = await getJob(selectedId);
        setDetailData(j as Record<string, unknown> | null);
        return;
      }
      if (action === "Cancel") await cancelJob(selectedId);
      if (action === "Retry") await retryJob(selectedId);
      if (action === "Remove") await removeJob(selectedId);
      refresh();
    },
    [selectedId, refresh],
  );

  const handlePurge = useCallback(async () => {
    await purgeJobs(purgeHours);
    refresh();
  }, [purgeHours, refresh]);

  return (
    <div>
      <PageHeader title="Job Management" subtitle="Monitor and control background processing jobs" />

      <div className="grid grid-cols-5 gap-3 mb-5">
        <MetricCard label="Total" value={counts?.total ?? 0} />
        <MetricCard label="Pending" value={counts?.pending ?? 0} />
        <MetricCard label="Running" value={counts?.running ?? 0} />
        <MetricCard label="Done" value={counts?.completed ?? 0} />
        <MetricCard label="Failed" value={counts?.failed ?? 0} />
      </div>

      <hr className="section-divider" />

      {/* Filters */}
      <details className="card mb-4">
        <summary className="cursor-pointer text-sm font-semibold">Filters</summary>
        <div className="mt-3 space-y-3">
          <div>
            <p className="text-xs font-semibold text-text-muted mb-1">Status:</p>
            <div className="flex gap-4">
              {(["pending", "running", "completed", "failed", "cancelled"] as const).map((s) => (
                <label key={s} className="flex items-center gap-1.5 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={filters[s]}
                    onChange={(e) => setFilters((p) => ({ ...p, [s]: e.target.checked }))}
                    className="accent-primary"
                  />
                  {s.charAt(0).toUpperCase() + s.slice(1)}
                </label>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs font-semibold text-text-muted">Job Type</label>
              <select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              >
                {types.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs font-semibold text-text-muted">User</label>
              <input
                type="text"
                value={filterUser}
                onChange={(e) => setFilterUser(e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-text-muted">Max</label>
              <input
                type="number"
                value={filterLimit}
                min={10}
                max={500}
                onChange={(e) => setFilterLimit(Number(e.target.value))}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          </div>
        </div>
      </details>

      {/* Table */}
      {jobs.length > 0 ? (
        <div className="card overflow-hidden p-0 mb-4">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-green-50/50 text-text-muted text-xs uppercase tracking-wider">
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-left">Type</th>
                <th className="px-4 py-2.5 text-left">User</th>
                <th className="px-4 py-2.5 text-left">Progress</th>
                <th className="px-4 py-2.5 text-left">Created</th>
                <th className="px-4 py-2.5 text-left">Duration</th>
                <th className="px-4 py-2.5 text-left">ID</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr
                  key={j.id}
                  onClick={() => setSelectedId(j.id)}
                  className={`border-t border-border cursor-pointer transition-colors ${
                    selectedId === j.id ? "bg-primary/5" : "hover:bg-green-50/30"
                  }`}
                >
                  <td className="px-4 py-2.5"><StatusPill status={j.status} /></td>
                  <td className="px-4 py-2.5 font-medium">{j.job_type}</td>
                  <td className="px-4 py-2.5 text-text-muted">{j.user}</td>
                  <td className="px-4 py-2.5">{j.progress}%</td>
                  <td className="px-4 py-2.5 text-text-muted">{formatTime(j.created_at)}</td>
                  <td className="px-4 py-2.5 text-text-muted">{formatDuration(j.started_at, j.completed_at)}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-muted">{j.id.slice(0, 12)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card text-center text-text-muted py-8 mb-4">No jobs match these filters.</div>
      )}

      <hr className="section-divider" />

      {/* Actions */}
      <h3 className="font-semibold text-sm mb-3">Actions</h3>
      <div className="flex gap-3 items-end mb-4">
        <div className="flex-1">
          <label className="text-xs text-text-muted font-semibold">Job</label>
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">Select a job</option>
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>{j.id}</option>
            ))}
          </select>
        </div>
        {["Details", "Cancel", "Retry", "Remove"].map((a) => (
          <button
            key={a}
            onClick={() => handleAction(a)}
            disabled={!selectedId}
            className={a === "Remove" ? "btn-danger" : "btn-secondary"}
          >
            {a}
          </button>
        ))}
      </div>

      {detailData && <JsonViewer data={detailData} title="Job Details" defaultOpen />}

      <hr className="section-divider" />

      {/* Purge */}
      <div className="flex gap-3 items-end mb-4">
        <div>
          <label className="text-xs text-text-muted font-semibold">Purge older than (hours)</label>
          <input
            type="number"
            value={purgeHours}
            min={1}
            onChange={(e) => setPurgeHours(Number(e.target.value))}
            className="w-32 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <button className="btn-danger" onClick={handlePurge}>Purge</button>
      </div>

      {/* Auto-refresh */}
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          checked={autoRefresh}
          onChange={(e) => setAutoRefresh(e.target.checked)}
          className="accent-primary"
        />
        Auto-refresh (5s)
      </label>
    </div>
  );
}
