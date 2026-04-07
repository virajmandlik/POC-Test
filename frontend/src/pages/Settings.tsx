import PageHeader from "@/components/PageHeader";
import MetricCard from "@/components/MetricCard";
import { useHealth } from "@/hooks/useHealth";

interface Props {
  username: string;
  onUsernameChange: (v: string) => void;
}

export default function Settings({ username, onUsernameChange }: Props) {
  const { data: health } = useHealth();

  return (
    <div>
      <PageHeader title="Settings" subtitle="System configuration and connection details" />

      {/* User identity */}
      <h3 className="font-semibold text-sm mb-3">User Identity</h3>
      <div className="card mb-5">
        <label className="text-xs font-semibold text-text-muted">Your username (used for audit logs)</label>
        <input
          type="text"
          value={username}
          onChange={(e) => onUsernameChange(e.target.value)}
          className="w-full mt-1 px-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </div>

      <hr className="section-divider" />

      {/* API Connection */}
      <h3 className="font-semibold text-sm mb-3">API Connection</h3>
      <div className="card mb-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <p className="text-sm"><strong>Endpoint:</strong> <code className="bg-green-50 px-1 rounded">/api</code> (proxied)</p>
            {health ? (
              <div className="mt-2 px-3 py-2 bg-green-50 rounded-lg text-sm text-primary-dark font-medium">
                Status: {health.status.toUpperCase()} | v{health.version ?? "?"}
              </div>
            ) : (
              <div className="mt-2 px-3 py-2 bg-red-50 rounded-lg text-sm text-danger font-medium">
                Cannot reach API server
              </div>
            )}
          </div>
          <div>
            {health && (
              <>
                <p className="text-sm"><strong>MongoDB:</strong> <code className="bg-green-50 px-1 rounded">{health.mongo}</code></p>
                <p className="text-sm mt-1"><strong>Platform:</strong> <code className="bg-green-50 px-1 rounded">{health.platform}</code></p>
              </>
            )}
          </div>
        </div>
        {health && (
          <a
            href="/api/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block mt-3 text-sm text-primary font-medium hover:underline"
          >
            📖 Open API Documentation
          </a>
        )}
      </div>

      <hr className="section-divider" />

      {/* Registered Pipelines */}
      <h3 className="font-semibold text-sm mb-3">Registered Pipelines</h3>
      <div className="card mb-5">
        {health ? (
          <div className="space-y-1">
            {health.registered_job_types.map((jt) => {
              const cat = jt.includes("uc1") ? "Land Records" : jt.includes("uc2") ? "Photo Verification" : "System";
              const icon = jt.includes("uc1") ? "📄" : jt.includes("uc2") ? "🌳" : "⚙️";
              return (
                <p key={jt} className="text-sm">
                  {icon} <code className="bg-green-50 px-1 rounded font-semibold">{jt}</code> — <span className="text-text-muted italic">{cat}</span>
                </p>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-text-muted">Connect to API to see registered pipelines.</p>
        )}
      </div>
    </div>
  );
}
