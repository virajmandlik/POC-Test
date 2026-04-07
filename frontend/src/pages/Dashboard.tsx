import PageHeader from "@/components/PageHeader";
import MetricCard from "@/components/MetricCard";
import StatusPill from "@/components/StatusPill";
import { useHealth } from "@/hooks/useHealth";
import { useJobList } from "@/hooks/useJobs";
import { useAuditLogs } from "@/hooks/useAudit";
import { useSyncStatus, useSyncOnline, useSyncRecent } from "@/hooks/useSync";
import { relativeTime, basename } from "@/lib/utils";

export default function Dashboard() {
  const { data: health } = useHealth();
  const { data: jobData } = useJobList({ limit: 8 }, 10_000);
  const { data: auditData } = useAuditLogs({ limit: 6 });
  const { data: sync } = useSyncStatus();
  const { data: online } = useSyncOnline();
  const { data: syncRecent } = useSyncRecent(5);

  const counts = jobData?.counts;
  const jobs = jobData?.jobs || [];
  const logs = auditData?.logs || [];
  const recentItems = syncRecent?.items || [];

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="System overview and recent activity" />

      {/* Metric row */}
      <div className="grid grid-cols-5 gap-3 mb-5">
        <MetricCard label="Total" value={counts?.total ?? 0} />
        <MetricCard label="Pending" value={counts?.pending ?? 0} />
        <MetricCard label="Running" value={counts?.running ?? 0} />
        <MetricCard label="Done" value={counts?.completed ?? 0} />
        <MetricCard label="Failed" value={counts?.failed ?? 0} />
      </div>

      <hr className="section-divider" />

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Recent Jobs */}
        <div className="lg:col-span-3">
          <h3 className="font-semibold text-sm mb-3">Recent Jobs</h3>
          {jobs.length > 0 ? (
            <div className="card overflow-hidden p-0">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-green-50/50 text-text-muted text-xs uppercase tracking-wider">
                    <th className="px-4 py-2.5 text-left">Status</th>
                    <th className="px-4 py-2.5 text-left">Type</th>
                    <th className="px-4 py-2.5 text-left">User</th>
                    <th className="px-4 py-2.5 text-left">When</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((j) => (
                    <tr key={j.id} className="border-t border-border hover:bg-green-50/30 transition-colors">
                      <td className="px-4 py-2.5">
                        <StatusPill status={j.status} />
                      </td>
                      <td className="px-4 py-2.5 font-medium">{j.job_type}</td>
                      <td className="px-4 py-2.5 text-text-muted">{j.user}</td>
                      <td className="px-4 py-2.5 text-text-muted">{relativeTime(j.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="card text-center text-text-muted py-8">
              No jobs yet — go to <strong>Land Records</strong> or <strong>Photo Verification</strong> to start.
            </div>
          )}
        </div>

        {/* System */}
        <div className="lg:col-span-2 space-y-4">
          <div>
            <h3 className="font-semibold text-sm mb-3">System</h3>
            <div className="card">
              {health ? (
                <>
                  <div className="grid grid-cols-2 gap-3 mb-3">
                    <MetricCard label="API" value={`v${health.version ?? "?"}`} />
                    <MetricCard label="Mongo" value={health.mongo === "connected" ? "✓" : "✗"} />
                  </div>
                  <p className="text-xs text-text-muted">
                    Platform: <code className="bg-green-50 px-1 rounded">{health.platform}</code>
                  </p>
                </>
              ) : (
                <p className="text-danger font-medium">API offline</p>
              )}
            </div>
          </div>

          <div>
            <h3 className="font-semibold text-sm mb-3">Pipelines</h3>
            <div className="card space-y-1">
              {health?.registered_job_types.map((jt) => (
                <p key={jt} className="text-xs text-text-muted">
                  {jt.includes("uc1") ? "📄" : jt.includes("uc2") ? "🌳" : "⚙️"}{" "}
                  <code className="bg-green-50 px-1 rounded">{jt}</code>
                </p>
              ))}
            </div>
          </div>
        </div>
      </div>

      <hr className="section-divider" />

      {/* Sync Queue */}
      <h3 className="font-semibold text-sm mb-3">Offline Sync Queue</h3>
      {sync ? (
        <>
          <div className="grid grid-cols-4 gap-3 mb-3">
            <MetricCard label="Pending" value={sync.pending} />
            <MetricCard label="Synced" value={sync.synced} />
            <MetricCard label="Failed" value={sync.failed} />
            <MetricCard label="VPN/Internet" value={online ? "🟢 Online" : "🔴 Offline"} />
          </div>
          {recentItems.length > 0 && (
            <div className="card">
              <p className="text-xs font-semibold mb-2">Recent Synced Results:</p>
              {recentItems.map((item, i) => {
                const cr = item.combined_result || {};
                return (
                  <p key={i} className="text-xs text-text-muted py-1 border-b border-green-50 last:border-0">
                    🔄 <strong>{item.job_type.toUpperCase()}</strong> — <code>{basename(item.file_path)}</code> — synced {relativeTime(item.synced_at)}
                    {item.job_type === "uc1" && !!(cr as Record<string, unknown>).merged_extraction && (
                      <span className="block ml-5">
                        Survey: {((cr as Record<string, unknown>).merged_extraction as Record<string, string>)?.survey_number ?? "—"} |
                        Village: {((cr as Record<string, unknown>).merged_extraction as Record<string, string>)?.village ?? "—"}
                      </span>
                    )}
                    {item.job_type === "uc2" && !!(cr as Record<string, string>).decision && (
                      <span className="block ml-5">Verdict: <strong>{(cr as Record<string, string>).decision}</strong></span>
                    )}
                  </p>
                );
              })}
            </div>
          )}
        </>
      ) : (
        <p className="text-xs text-text-muted">Sync queue not available</p>
      )}

      <hr className="section-divider" />

      {/* Activity feed */}
      <h3 className="font-semibold text-sm mb-3">Recent Activity</h3>
      {logs.length > 0 ? (
        <div className="space-y-1">
          {logs.map((entry, i) => {
            const icon = { info: "ℹ️", warn: "⚠️", error: "🔴" }[entry.level] || "ℹ️";
            return (
              <p key={i} className="text-sm">
                {icon} <strong>{entry.action}</strong> — <span className="text-text-muted">{entry.user}</span> · {relativeTime(entry.timestamp)}
              </p>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-text-muted">No recent activity.</p>
      )}
    </div>
  );
}
