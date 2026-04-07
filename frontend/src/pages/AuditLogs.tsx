import { useState } from "react";
import PageHeader from "@/components/PageHeader";
import JsonViewer from "@/components/JsonViewer";
import { useAuditLogs } from "@/hooks/useAudit";
import { formatTime, truncate } from "@/lib/utils";

export default function AuditLogs() {
  const [action, setAction] = useState("");
  const [user, setUser] = useState("");
  const [level, setLevel] = useState("All");
  const [limit, setLimit] = useState(100);
  const [since, setSince] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString().slice(0, 10);
  });
  const [until, setUntil] = useState(() => new Date().toISOString().slice(0, 10));
  const [selectedIdx, setSelectedIdx] = useState(0);

  const params: Record<string, string | number> = { limit };
  if (action) params.action = action;
  if (user) params.user = user;
  if (level !== "All") params.level = level;
  if (since) params.since = since;
  if (until) params.until = until;

  const { data } = useAuditLogs(params);
  const logs = data?.logs || [];
  const total = data?.total ?? 0;

  return (
    <div>
      <PageHeader title="Audit Trail" subtitle="Search and review all system activity logs" />

      {/* Filters */}
      <div className="card mb-4">
        <div className="grid grid-cols-4 gap-3 mb-3">
          <div>
            <label className="text-xs font-semibold text-text-muted">Action (regex)</label>
            <input
              type="text"
              value={action}
              onChange={(e) => setAction(e.target.value)}
              className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div>
            <label className="text-xs font-semibold text-text-muted">User</label>
            <input
              type="text"
              value={user}
              onChange={(e) => setUser(e.target.value)}
              className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div>
            <label className="text-xs font-semibold text-text-muted">Level</label>
            <select
              value={level}
              onChange={(e) => setLevel(e.target.value)}
              className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            >
              {["All", "info", "warn", "error"].map((l) => (
                <option key={l} value={l}>{l}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs font-semibold text-text-muted">Max results</label>
            <input
              type="number"
              value={limit}
              min={10}
              max={1000}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-semibold text-text-muted">Since</label>
            <input type="date" value={since} onChange={(e) => setSince(e.target.value)} className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
          </div>
          <div>
            <label className="text-xs font-semibold text-text-muted">Until</label>
            <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
          </div>
        </div>
      </div>

      <p className="text-xs text-text-muted mb-3">Showing {logs.length} of {total} entries</p>

      {logs.length > 0 ? (
        <div className="card overflow-hidden p-0 mb-4">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-green-50/50 text-text-muted text-xs uppercase tracking-wider">
                <th className="px-4 py-2.5 text-left">Timestamp</th>
                <th className="px-4 py-2.5 text-left">Level</th>
                <th className="px-4 py-2.5 text-left">Action</th>
                <th className="px-4 py-2.5 text-left">User</th>
                <th className="px-4 py-2.5 text-left">Job ID</th>
                <th className="px-4 py-2.5 text-left">Detail</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((entry, i) => (
                <tr
                  key={i}
                  onClick={() => setSelectedIdx(i)}
                  className={`border-t border-border cursor-pointer transition-colors ${
                    selectedIdx === i ? "bg-primary/5" : "hover:bg-green-50/30"
                  }`}
                >
                  <td className="px-4 py-2.5 text-text-muted whitespace-nowrap">{formatTime(entry.timestamp)}</td>
                  <td className="px-4 py-2.5">
                    <span className={`text-xs font-semibold uppercase ${
                      entry.level === "error" ? "text-danger" : entry.level === "warn" ? "text-warn" : "text-text-muted"
                    }`}>
                      {entry.level}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 font-medium">{entry.action}</td>
                  <td className="px-4 py-2.5 text-text-muted">{entry.user}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-muted">{(entry.job_id || "—").slice(0, 12)}</td>
                  <td className="px-4 py-2.5 text-text-muted text-xs">{truncate(JSON.stringify(entry.detail), 80)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card text-center text-text-muted py-8 mb-4">No audit logs found matching filters.</div>
      )}

      <hr className="section-divider" />

      {/* Detail viewer */}
      {logs.length > 0 && selectedIdx < logs.length && (
        <>
          <h3 className="font-semibold text-sm mb-3">Log Detail</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="card space-y-1 text-sm">
              <p><strong>Action:</strong> <code className="bg-green-50 px-1 rounded">{logs[selectedIdx].action}</code></p>
              <p><strong>User:</strong> <code className="bg-green-50 px-1 rounded">{logs[selectedIdx].user}</code></p>
              <p><strong>Level:</strong> <code className="bg-green-50 px-1 rounded">{logs[selectedIdx].level}</code></p>
              <p><strong>Job ID:</strong> <code className="bg-green-50 px-1 rounded">{logs[selectedIdx].job_id ?? "—"}</code></p>
              <p><strong>Time:</strong> <code className="bg-green-50 px-1 rounded">{logs[selectedIdx].timestamp}</code></p>
            </div>
            <div className="md:col-span-2">
              <JsonViewer data={logs[selectedIdx]} title="Full Entry JSON" defaultOpen />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
