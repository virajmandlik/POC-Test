import { useState, useEffect } from "react";
import PageHeader from "@/components/PageHeader";
import MetricCard from "@/components/MetricCard";
import Stepper from "@/components/Stepper";
import StepNav from "@/components/StepNav";
import FileUpload from "@/components/FileUpload";
import CheckCard from "@/components/CheckCard";
import JsonViewer from "@/components/JsonViewer";
import { uploadFile, uploadFiles, submitJob, getJob } from "@/api/client";
import type { Job } from "@/api/client";
import { basename } from "@/lib/utils";

const STEPS = ["Upload", "Quality", "Scene & GPS", "Verdict"];

interface Props {
  username: string;
}

export default function PhotoVerification({ username }: Props) {
  const [mode, setMode] = useState<"single" | "batch">("single");

  return (
    <div>
      <PageHeader
        title="Photo Verification"
        subtitle="Verify CC training photos — quality, scene analysis, GPS & timestamp"
      />
      <div className="flex gap-4 mb-4">
        {(["single", "batch"] as const).map((m) => (
          <label key={m} className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="radio" name="uc2_mode" checked={mode === m} onChange={() => setMode(m)} className="accent-primary" />
            {m === "single" ? "Single Photo" : "Batch PDFs"}
          </label>
        ))}
      </div>
      {mode === "single" ? <SingleFlow username={username} /> : <BatchFlow username={username} />}
    </div>
  );
}

// ── Single Flow ──────────────────────────────────────────────────────

function SingleFlow({ username }: { username: string }) {
  const [step, setStep] = useState(0);
  const [completed, setCompleted] = useState<Set<number>>(new Set());
  const [file, setFile] = useState<File | null>(null);
  const [imgUrl, setImgUrl] = useState("");
  const [skipVision, setSkipVision] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");

  const markDone = (s: number) => setCompleted((p) => new Set(p).add(s));

  const nextDisabled = step === 0 && !result;

  const handleVerify = async () => {
    if (!file) return;
    setLoading(true);
    const up = await uploadFile(file, username);
    if (!up) { setLoading(false); return; }

    const resp = await submitJob("/uc2/verify", {
      image_path: up.path,
      skip_vision: skipVision,
      user: username,
      tags: ["ui", "single"],
    });
    if (!resp) { setLoading(false); return; }

    const final = await pollJob(resp.job_id, (p, m) => { setProgress(p); setProgressMsg(m); });
    setLoading(false);
    if (final?.status === "completed" && final.result) {
      setResult(final.result as Record<string, unknown>);
      markDone(0);
    }
  };

  return (
    <div>
      <Stepper steps={STEPS} current={step} completed={completed} />

      {step === 0 && (
        <div>
          <h3 className="text-lg font-semibold mb-3">Upload & Verify Photo</h3>
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
            <div className="lg:col-span-3">
              <FileUpload
                accept=".jpg,.jpeg,.png,.webp"
                label="Drop field / training photo here"
                onFiles={(files) => {
                  const f = files[0];
                  if (f) {
                    setFile(f);
                    setImgUrl(URL.createObjectURL(f));
                  }
                }}
              />
              {imgUrl && (
                <div className="mt-3 card">
                  <img src={imgUrl} alt="preview" className="max-w-md rounded-lg mb-3" />
                  <button className="btn-primary w-full" onClick={handleVerify} disabled={loading}>
                    {loading ? "Verifying…" : "Run Full Verification"}
                  </button>
                </div>
              )}
              {result && <p className="text-xs text-text-muted mt-2">Verification data available</p>}
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={skipVision} onChange={(e) => setSkipVision(e.target.checked)} className="accent-primary" />
                Skip GPT Vision (offline mode)
              </label>
            </div>
          </div>
          {loading && <ProgressBar pct={progress} msg={progressMsg} />}
        </div>
      )}

      {step === 1 && <StepQuality result={result} onDone={() => markDone(1)} />}
      {step === 2 && <StepScene result={result} imgUrl={imgUrl} onDone={() => markDone(2)} />}
      {step === 3 && <StepVerdict result={result} onDone={() => markDone(3)} />}

      <hr className="section-divider" />
      <StepNav
        current={step}
        total={STEPS.length}
        onBack={() => setStep(step - 1)}
        onNext={() => setStep(step + 1)}
        nextDisabled={nextDisabled}
        nextLabel={{ 0: "Quality →", 1: "Scene & GPS →", 2: "Verdict →" }[step] || "Next →"}
      />
    </div>
  );
}

// ── Step 1: Quality ──────────────────────────────────────────────────

function StepQuality({ result, onDone }: { result: Record<string, unknown> | null; onDone: () => void }) {
  useEffect(() => { if (result) onDone(); }, [result, onDone]);
  if (!result) return <p className="text-warn font-medium">Run verification first (step 1).</p>;

  const checks = (result.checks ?? {}) as Record<string, Record<string, unknown>>;
  const qc = checks.image_quality;

  if (qc) {
    const details: Record<string, unknown> = {};
    for (const k of ["blur_score", "sharpness", "mean_brightness", "contrast_ratio"]) {
      if (qc[k] != null) details[k] = qc[k];
    }
    return (
      <div>
        <h3 className="text-lg font-semibold mb-3">Image Quality</h3>
        <CheckCard label="Image Quality" passed={qc.passed as boolean} details={details} reason={qc.reason as string} />
      </div>
    );
  }
  return <p className="text-sm text-text-muted">No quality data available.</p>;
}

// ── Step 2: Scene & GPS ──────────────────────────────────────────────

function StepScene({ result, imgUrl, onDone }: { result: Record<string, unknown> | null; imgUrl: string; onDone: () => void }) {
  useEffect(() => { if (result) onDone(); }, [result, onDone]);
  if (!result) return <p className="text-warn font-medium">Run verification first.</p>;

  const checks = (result.checks ?? {}) as Record<string, Record<string, unknown>>;
  const scene = checks.scene_analysis;
  const meta = checks.metadata;

  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Scene Analysis & Metadata</h3>

      {scene ? (
        scene.error && !scene.people_count ? (
          <div className="bg-red-50 text-danger px-4 py-2.5 rounded-lg mb-3">Vision API error: {String(scene.error)}</div>
        ) : (
          <>
            <CheckCard
              label="Scene Analysis"
              passed={scene.passed as boolean}
              details={Object.fromEntries(
                ["people_count", "has_multiple_people", "has_representative", "is_training_scene", "is_outdoor_rural", "confidence"]
                  .filter((k) => k in scene)
                  .map((k) => [k, scene[k]]),
              )}
              reason={scene.reason as string}
            />
            {scene.scene_description && (
              <p className="text-sm mb-3"><strong>Scene Description:</strong> {String(scene.scene_description)}</p>
            )}
            {scene.has_visible_timestamp && (
              <div className="grid grid-cols-4 gap-3 mb-3">
                <MetricCard label="Overlay Date" value={String(scene.overlay_date ?? "—")} />
                <MetricCard label="Overlay Time" value={String(scene.overlay_time ?? "—")} />
                <MetricCard label="Overlay Lat" value={String(scene.overlay_latitude ?? "—")} />
                <MetricCard label="Overlay Lon" value={String(scene.overlay_longitude ?? "—")} />
              </div>
            )}
          </>
        )
      ) : (
        <p className="text-sm text-text-muted mb-3">Scene analysis was skipped (offline mode).</p>
      )}

      <hr className="section-divider" />

      {meta ? (
        <>
          <CheckCard
            label="GPS & Timestamp (from overlay)"
            passed={meta.passed as boolean}
            details={Object.fromEntries([
              ...(meta.gps && typeof meta.gps === "object"
                ? [["GPS Lat", (meta.gps as Record<string, unknown>).lat], ["GPS Lon", (meta.gps as Record<string, unknown>).lon]]
                : []),
              ...(meta.timestamp ? [["Timestamp", meta.timestamp]] : []),
              ["Source", meta.source ?? ""],
            ])}
            reason={meta.reason as string}
          />
        </>
      ) : (
        <p className="text-sm text-text-muted">Metadata extraction was skipped (offline mode).</p>
      )}

      {imgUrl && (
        <>
          <hr className="section-divider" />
          <img src={imgUrl} alt="Analysed photo" className="max-w-md rounded-lg" />
        </>
      )}
    </div>
  );
}

// ── Step 3: Verdict ──────────────────────────────────────────────────

function StepVerdict({ result, onDone }: { result: Record<string, unknown> | null; onDone: () => void }) {
  useEffect(() => { if (result) onDone(); }, [result, onDone]);
  if (!result) return <p className="text-warn font-medium">Run verification first.</p>;

  const decision = String(result.decision ?? result.verdict ?? "UNKNOWN").toUpperCase();
  const checks = (result.checks ?? {}) as Record<string, Record<string, unknown>>;
  const reasons = (result.rejection_reasons ?? result.reasons ?? []) as string[];
  const ms = (result.metadata as Record<string, unknown>)?.processing_time_ms ?? result.processing_time_ms ?? result.elapsed_ms;

  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Final Verification Verdict</h3>

      {decision === "ACCEPT" ? (
        <div className="bg-green-50 border-2 border-primary rounded-xl p-6 text-center text-primary-dark text-2xl font-bold mb-4">
          ✅ ACCEPTED
          <p className="text-sm font-normal mt-1">This training session photo meets all verification criteria.</p>
        </div>
      ) : decision === "REJECT" ? (
        <div className="bg-red-50 border-2 border-danger rounded-xl p-6 text-center text-danger text-2xl font-bold mb-4">
          ❌ REJECTED
          <p className="text-sm font-normal mt-1">This training session photo failed verification.</p>
        </div>
      ) : (
        <div className="bg-amber-50 border-2 border-warn rounded-xl p-6 text-center text-warn text-2xl font-bold mb-4">{decision}</div>
      )}

      <h4 className="font-semibold text-sm mb-2">Check Summary</h4>
      <div className="space-y-1 text-sm mb-4">
        {Object.entries(checks).map(([name, cd]) => {
          const label = name.replace(/_/g, " ");
          const passed = cd.passed;
          const reason = cd.reason ? ` — ${cd.reason}` : "";
          return (
            <p key={name}>
              <strong>{label}:</strong>{" "}
              {passed === true ? <span className="text-primary">PASS</span> : passed === false ? <span className="text-danger">FAIL{reason}</span> : JSON.stringify(cd)}
            </p>
          );
        })}
      </div>

      {reasons.length > 0 && (
        <>
          <h4 className="font-semibold text-sm mb-2">Rejection Reasons</h4>
          <ul className="list-disc ml-5 text-sm mb-4">
            {reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </>
      )}

      {ms != null && <MetricCard label="Processing Time" value={`${String(ms)} ms`} />}

      <hr className="section-divider" />
      <JsonViewer data={result} title="Raw API Response" />
    </div>
  );
}

// ── Batch PDFs ───────────────────────────────────────────────────────

function BatchFlow({ username }: { username: string }) {
  const [files, setFiles] = useState<File[]>([]);
  const [filePaths, setFilePaths] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [filter, setFilter] = useState<string[]>(["ACCEPT", "REJECT", "ERROR"]);

  const handleUpload = async () => {
    setLoading(true);
    const uploaded = await uploadFiles(files, username);
    setFilePaths(uploaded.map((u) => u.path));
    setLoading(false);
  };

  const handleProcess = async () => {
    setLoading(true);
    setProgress(0);
    const resp = await submitJob("/uc2/batch", {
      pdf_paths: filePaths,
      user: username,
      tags: ["ui", "batch"],
    });
    if (!resp) { setLoading(false); return; }
    const final = await pollJob(resp.job_id, (p, m) => { setProgress(p); setProgressMsg(m); });
    setLoading(false);
    if (final?.status === "completed" && final.result) {
      setResult(final.result as Record<string, unknown>);
    }
  };

  const downloadCsv = () => {
    const results = (result?.results ?? result?.rows ?? result?.details ?? []) as Record<string, unknown>[];
    if (!results.length) return;
    const headers = Object.keys(results[0]);
    const csv = [headers.join(","), ...results.map((r) => headers.map((h) => JSON.stringify(r[h] ?? "")).join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "uc2_verification_results.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <hr className="section-divider" />
      <FileUpload
        accept=".pdf"
        multiple
        label="Upload CC training PDFs"
        onFiles={(f) => { setFiles(f); setFilePaths([]); setResult(null); }}
      />

      {files.length > 0 && filePaths.length === 0 && (
        <button className="btn-primary mt-3 mb-4" onClick={handleUpload} disabled={loading}>
          {loading ? "Uploading…" : `Upload ${files.length} PDFs`}
        </button>
      )}

      {filePaths.length > 0 && !result && (
        <>
          <p className="text-sm font-medium mt-3 mb-2">{filePaths.length} PDFs ready for processing</p>
          <button className="btn-primary w-full mb-4" onClick={handleProcess} disabled={loading}>
            {loading ? "Processing…" : "Process All PDFs"}
          </button>
        </>
      )}

      {loading && <ProgressBar pct={progress} msg={progressMsg} />}

      {result && (
        <div className="mt-4">
          <h3 className="font-semibold text-sm mb-3">Verification Results</h3>
          <div className="grid grid-cols-4 gap-3 mb-4">
            <MetricCard label="Total" value={result.total as number ?? 0} />
            <MetricCard label="Accepted" value={result.accepted as number ?? 0} />
            <MetricCard label="Rejected" value={result.rejected as number ?? 0} />
            <MetricCard label="Errors" value={result.errors as number ?? 0} />
          </div>

          <div className="flex gap-3 mb-3">
            {["ACCEPT", "REJECT", "ERROR"].map((f) => (
              <label key={f} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={filter.includes(f)}
                  onChange={(e) => setFilter((prev) => e.target.checked ? [...prev, f] : prev.filter((x) => x !== f))}
                  className="accent-primary"
                />
                {f}
              </label>
            ))}
          </div>

          <button className="btn-secondary mb-4" onClick={downloadCsv}>Download Full Results CSV</button>
          <JsonViewer data={result} title="View Raw Result" />
        </div>
      )}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────

function ProgressBar({ pct, msg }: { pct: number; msg: string }) {
  return (
    <div className="mb-4 mt-3">
      <div className="w-full bg-gray-200 rounded-full h-2">
        <div className="bg-gradient-to-r from-primary to-primary-light h-2 rounded-full transition-all" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-text-muted mt-1">{pct}% {msg}</p>
    </div>
  );
}

async function pollJob(
  jobId: string,
  onProgress: (pct: number, msg: string) => void,
  timeout = 600_000,
): Promise<Job | null> {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const job = await getJob(jobId);
    if (!job) return null;
    onProgress(Math.min(job.progress, 100), job.progress_message);
    if (["completed", "failed", "cancelled"].includes(job.status)) return job;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return getJob(jobId);
}
