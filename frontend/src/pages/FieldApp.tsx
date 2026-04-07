import { useState, useRef } from "react";
import PageHeader from "@/components/PageHeader";
import MetricCard from "@/components/MetricCard";
import FileUpload from "@/components/FileUpload";
import JsonViewer from "@/components/JsonViewer";
import { uploadFile, submitJob, getJob, enqueuSync, getSyncOnline } from "@/api/client";
import { useSyncStatus } from "@/hooks/useSync";
import type { Job } from "@/api/client";

interface Props {
  username: string;
}

export default function FieldApp({ username }: Props) {
  const { data: syncCounts } = useSyncStatus();
  const [forceOffline, setForceOffline] = useState(false);
  const [online, setOnline] = useState(true);
  const [useCase, setUseCase] = useState<"uc1" | "uc2">("uc1");

  const checkOnline = async () => {
    if (forceOffline) return false;
    const on = await getSyncOnline();
    setOnline(on);
    return on;
  };

  return (
    <div className="max-w-lg mx-auto">
      <PageHeader title="📱 Field App" subtitle="Mobile view for field engineers" />

      {syncCounts && (
        <div className="mb-3">
          {syncCounts.pending > 0 ? (
            <span className="inline-block px-3 py-1 rounded-full text-xs font-semibold bg-amber-50 text-amber-700">🔄 {syncCounts.pending} pending sync</span>
          ) : (
            <span className="inline-block px-3 py-1 rounded-full text-xs font-semibold bg-green-50 text-primary-dark">✓ All synced</span>
          )}
        </div>
      )}

      <label className="flex items-center gap-2 text-sm mb-3 cursor-pointer">
        <input
          type="checkbox"
          checked={forceOffline}
          onChange={(e) => { setForceOffline(e.target.checked); if (e.target.checked) setOnline(false); }}
          className="accent-primary"
        />
        📴 Simulate Offline Mode
      </label>

      {forceOffline ? (
        <div className="bg-amber-50 text-amber-700 px-3 py-2 rounded-lg text-sm mb-4">📴 Offline (simulated) — using PaddleOCR local extraction</div>
      ) : online ? (
        <div className="bg-green-50 text-primary-dark px-3 py-2 rounded-lg text-sm mb-4">🌐 Online — full AI extraction available</div>
      ) : (
        <div className="bg-amber-50 text-amber-700 px-3 py-2 rounded-lg text-sm mb-4">📴 Offline — using PaddleOCR local extraction</div>
      )}

      <hr className="section-divider" />

      <div className="flex gap-3 mb-4">
        {(["uc1", "uc2"] as const).map((uc) => (
          <label key={uc} className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="radio" name="field_uc" checked={useCase === uc} onChange={() => setUseCase(uc)} className="accent-primary" />
            {uc === "uc1" ? "📄 Scan Land Record" : "📷 Training Photo"}
          </label>
        ))}
      </div>

      {useCase === "uc1" ? (
        <UC1Flow username={username} online={!forceOffline && online} checkOnline={checkOnline} />
      ) : (
        <UC2Flow username={username} online={!forceOffline && online} checkOnline={checkOnline} />
      )}
    </div>
  );
}

// ── UC1 Flow ─────────────────────────────────────────────────────────

function UC1Flow({ username, online, checkOnline }: { username: string; online: boolean; checkOnline: () => Promise<boolean> }) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);

  const handleExtract = async () => {
    if (!file) return;
    setLoading(true);
    setProgress(0);

    const isOnline = await checkOnline();
    const mode = isOnline ? "combined" : "paddle";

    const up = await uploadFile(file, username);
    if (!up) { setLoading(false); return; }

    const resp = await submitJob("/uc1/extract", {
      file_path: up.path,
      mode,
      lang: "mr",
      user: username,
      tags: ["field_app"],
    });
    if (!resp) { setLoading(false); return; }

    const final = await pollJob(resp.job_id, (p) => setProgress(p));
    setLoading(false);

    if (final?.status === "completed" && final.result) {
      setResult(final.result as Record<string, unknown>);
      if (mode === "paddle") {
        await enqueuSync({
          job_type: "uc1",
          file_path: up.path,
          offline_result: final.result as Record<string, unknown>,
          user: username,
        });
      }
    }
  };

  const merged = result ? ((result as Record<string, unknown>).merged_extraction ?? result) as Record<string, unknown> : null;

  return (
    <div>
      <div className="card mb-4">
        <h4 className="font-semibold text-sm mb-3">📄 Scan Land Record</h4>
        <FileUpload
          accept=".pdf,.png,.jpg,.jpeg"
          label="Upload document or take photo"
          onFiles={(files) => setFile(files[0] || null)}
        />
        {file && <p className="text-xs text-text-muted mt-2">{file.name} ({(file.size / 1024).toFixed(1)} KB)</p>}
      </div>

      {file && (
        <button className="btn-primary w-full mb-4" onClick={handleExtract} disabled={loading}>
          {loading ? `Extracting… ${progress}%` : "🔍 Extract Data"}
        </button>
      )}

      {loading && (
        <div className="mb-4">
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-gradient-to-r from-primary to-primary-light h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      {merged && typeof merged === "object" && (
        <div className="card">
          <h4 className="font-semibold text-sm mb-3">📋 Extracted Data</h4>
          {[
            ["Survey No.", (merged as Record<string, string>).survey_number],
            ["Village", (merged as Record<string, string>).village],
            ["Taluka", (merged as Record<string, string>).taluka],
            ["District", (merged as Record<string, string>).district],
          ].map(([label, value]) => (
            <div key={label} className="flex justify-between py-2 border-b border-green-50 last:border-0 text-sm">
              <span className="text-text-muted font-medium">{label}</span>
              <span className="font-semibold text-right max-w-[60%]">{value || "—"}</span>
            </div>
          ))}

          {Array.isArray((merged as Record<string, unknown>).owners) &&
            ((merged as Record<string, unknown>).owners as Record<string, string>[]).map((o, i) => (
              <div key={i}>
                <div className="flex justify-between py-2 border-b border-green-50 text-sm">
                  <span className="text-text-muted font-medium">Owner</span>
                  <span className="font-semibold">{o.name || "—"}</span>
                </div>
                <div className="flex justify-between py-2 border-b border-green-50 text-sm">
                  <span className="text-text-muted font-medium">Area</span>
                  <span className="font-semibold">{o.area_hectare || "—"} ha</span>
                </div>
              </div>
            ))}

          {!online && (
            <p className="text-xs text-blue-600 mt-3">📤 Queued for auto-sync — GPT-4 Vision will enrich when online</p>
          )}

          <div className="mt-3">
            <JsonViewer data={result} title="View full JSON" />
          </div>
        </div>
      )}
    </div>
  );
}

// ── UC2 Flow ─────────────────────────────────────────────────────────

function UC2Flow({ username, online, checkOnline }: { username: string; online: boolean; checkOnline: () => Promise<boolean> }) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);

  const handleVerify = async () => {
    if (!file) return;
    setLoading(true);

    const up = await uploadFile(file, username);
    if (!up) { setLoading(false); return; }

    const isOnline = await checkOnline();
    if (!isOnline) {
      await enqueuSync({ job_type: "uc2", file_path: up.path, offline_result: {}, user: username });
      setResult({ decision: "QUEUED", message: "Photo queued for verification when internet returns" });
      setLoading(false);
      return;
    }

    const resp = await submitJob("/uc2/verify", {
      image_path: up.path,
      skip_vision: false,
      user: username,
      tags: ["field_app"],
    });
    if (!resp) { setLoading(false); return; }

    const final = await pollJob(resp.job_id, () => {});
    setLoading(false);
    if (final?.status === "completed" && final.result) {
      setResult(final.result as Record<string, unknown>);
    }
  };

  const decision = result ? String(result.decision ?? "UNKNOWN").toUpperCase() : null;

  return (
    <div>
      <div className="card mb-4">
        <h4 className="font-semibold text-sm mb-3">📷 Training Photo Verification</h4>
        <FileUpload
          accept=".jpg,.jpeg,.png,.webp"
          label="Upload training photo"
          onFiles={(files) => setFile(files[0] || null)}
        />
        {file && (
          <div className="mt-3">
            <img src={URL.createObjectURL(file)} alt="preview" className="w-full rounded-lg" />
          </div>
        )}
      </div>

      {!online && <p className="text-xs text-amber-600 mb-3">📴 Offline — photo will be queued for verification when internet returns</p>}

      {file && (
        <button className="btn-primary w-full mb-4" onClick={handleVerify} disabled={loading}>
          {loading ? "Verifying…" : "🔍 Verify Photo"}
        </button>
      )}

      {decision === "ACCEPT" && (
        <div className="bg-green-50 border-2 border-primary rounded-xl p-4 text-center text-primary-dark text-xl font-bold mb-4">
          ✅ ACCEPTED
        </div>
      )}
      {decision === "REJECT" && (
        <div className="bg-red-50 border-2 border-danger rounded-xl p-4 text-center text-danger text-xl font-bold mb-4">
          ❌ REJECTED
        </div>
      )}
      {decision === "QUEUED" && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-center text-blue-700 text-sm mb-4">
          📤 {String(result?.message ?? "Queued for processing")}
        </div>
      )}

      {result && decision !== "QUEUED" && (
        <div className="card">
          {(() => {
            const checks = (result.checks ?? {}) as Record<string, Record<string, unknown>>;
            const scene = checks.scene_analysis;
            const desc = scene?.scene_description;
            return (
              <>
                {desc && <p className="text-sm mb-2"><strong>Scene:</strong> {String(desc)}</p>}
                {scene && (
                  <div className="space-y-0">
                    {([
                      ["People Count", scene.people_count],
                      ["Training Scene", scene.is_training_scene ? "Yes" : "No"],
                      ["Outdoor/Rural", scene.is_outdoor_rural ? "Yes" : "No"],
                    ] as [string, unknown][]).map(([label, value]) => (
                      <div key={label} className="flex justify-between py-2 border-b border-green-50 last:border-0 text-sm">
                        <span className="text-text-muted font-medium">{label}</span>
                        <span className="font-semibold">{String(value ?? "—")}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            );
          })()}

          {Array.isArray(result.rejection_reasons) && (result.rejection_reasons as string[]).length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold mb-1">Rejection Reasons:</p>
              <ul className="list-disc ml-5 text-xs">
                {(result.rejection_reasons as string[]).map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}

          <div className="mt-3">
            <JsonViewer data={result} title="View full JSON" />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Poll helper ──────────────────────────────────────────────────────

async function pollJob(
  jobId: string,
  onProgress: (pct: number) => void,
  timeout = 300_000,
): Promise<Job | null> {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const job = await getJob(jobId);
    if (!job) return null;
    onProgress(Math.min(job.progress, 100));
    if (["completed", "failed", "cancelled"].includes(job.status)) return job;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return getJob(jobId);
}
