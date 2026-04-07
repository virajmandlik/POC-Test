import { useState, useCallback, useEffect, useRef } from "react";
import PageHeader from "@/components/PageHeader";
import MetricCard from "@/components/MetricCard";
import Stepper from "@/components/Stepper";
import StepNav from "@/components/StepNav";
import FileUpload from "@/components/FileUpload";
import JsonViewer from "@/components/JsonViewer";
import { uploadFile, uploadFiles, submitJob, getJob, uc1QualityCheck } from "@/api/client";
import type { Job } from "@/api/client";
import { basename } from "@/lib/utils";

const STEPS = ["Upload", "Quality", "Extract", "Semantic", "Output"];

interface Props {
  username: string;
}

export default function LandRecords({ username }: Props) {
  const [mode, setMode] = useState<"single" | "batch">("single");

  return (
    <div>
      <PageHeader
        title="Land Record OCR & Extraction"
        subtitle="Upload Maharashtra 7/12 documents — extract, compare, and analyse"
      />
      <div className="flex gap-4 mb-4">
        {(["single", "batch"] as const).map((m) => (
          <label key={m} className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              name="uc1_mode"
              checked={mode === m}
              onChange={() => setMode(m)}
              className="accent-primary"
            />
            {m === "single" ? "Single Document" : "Batch Processing"}
          </label>
        ))}
      </div>
      {mode === "single" ? <SingleFlow username={username} /> : <BatchFlow username={username} />}
    </div>
  );
}

// ── Single Document Flow ─────────────────────────────────────────────

function SingleFlow({ username }: { username: string }) {
  const [step, setStep] = useState(0);
  const [completed, setCompleted] = useState<Set<number>>(new Set());
  const [file, setFile] = useState<File | null>(null);
  const [filePath, setFilePath] = useState("");
  const [fileName, setFileName] = useState("");
  const [extractMode, setExtractMode] = useState("combined");
  const [lang, setLang] = useState("mr");
  const [qcResult, setQcResult] = useState<Record<string, unknown> | null>(null);
  const [extractResult, setExtractResult] = useState<Record<string, unknown> | null>(null);
  const [semanticResult, setSemanticResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");

  const markDone = (s: number) => setCompleted((p) => new Set(p).add(s));

  const nextDisabled =
    (step === 0 && !filePath) ||
    (step === 2 && !extractResult);

  const nextLabels: Record<number, string> = { 0: "Quality →", 1: "Extract →", 2: "Semantic →", 3: "Output →" };

  return (
    <div>
      <Stepper steps={STEPS} current={step} completed={completed} />

      {step === 0 && (
        <StepUpload
          file={file}
          setFile={setFile}
          filePath={filePath}
          fileName={fileName}
          extractMode={extractMode}
          setExtractMode={setExtractMode}
          lang={lang}
          setLang={setLang}
          loading={loading}
          onUpload={async () => {
            if (!file) return;
            setLoading(true);
            const res = await uploadFile(file, username);
            setLoading(false);
            if (res) {
              setFilePath(res.path);
              setFileName(res.filename);
              markDone(0);
            }
          }}
        />
      )}

      {step === 1 && (
        <StepQuality
          file={file}
          filePath={filePath}
          fileName={fileName}
          qcResult={qcResult}
          loading={loading}
          onRun={async () => {
            if (!file) { markDone(1); return; }
            setLoading(true);
            const res = await uc1QualityCheck(file, username);
            setLoading(false);
            if (res) { setQcResult(res); markDone(1); }
          }}
          onSkip={() => markDone(1)}
        />
      )}

      {step === 2 && (
        <StepExtract
          filePath={filePath}
          fileName={fileName}
          extractMode={extractMode}
          lang={lang}
          username={username}
          loading={loading}
          progress={progress}
          progressMsg={progressMsg}
          result={extractResult}
          onRun={async () => {
            setLoading(true);
            setProgress(0);
            const resp = await submitJob("/uc1/extract", {
              file_path: filePath,
              mode: extractMode,
              lang,
              user: username,
              tags: ["ui", "single"],
            });
            if (!resp) { setLoading(false); return; }
            const final = await pollJob(resp.job_id, (p, m) => { setProgress(p); setProgressMsg(m); });
            setLoading(false);
            if (final?.status === "completed" && final.result) {
              setExtractResult(final.result as Record<string, unknown>);
              markDone(2);
            }
          }}
        />
      )}

      {step === 3 && (
        <StepSemantic
          extractResult={extractResult}
          semanticResult={semanticResult}
          username={username}
          loading={loading}
          progress={progress}
          progressMsg={progressMsg}
          onRun={async () => {
            if (!extractResult) return;
            setLoading(true);
            setProgress(0);
            const resp = await submitJob("/uc1/semantic", {
              extraction_data: extractResult,
              user: username,
              tags: ["ui", "semantic"],
            });
            if (!resp) { setLoading(false); return; }
            const final = await pollJob(resp.job_id, (p, m) => { setProgress(p); setProgressMsg(m); });
            setLoading(false);
            if (final?.status === "completed" && final.result) {
              setSemanticResult(final.result as Record<string, unknown>);
              markDone(3);
            }
          }}
        />
      )}

      {step === 4 && (
        <StepOutput
          extractResult={extractResult}
          semanticResult={semanticResult}
          fileName={fileName}
          extractMode={extractMode}
          onDone={() => markDone(4)}
        />
      )}

      <hr className="section-divider" />
      <StepNav
        current={step}
        total={STEPS.length}
        onBack={() => setStep(step - 1)}
        onNext={() => setStep(step + 1)}
        nextDisabled={nextDisabled}
        nextLabel={nextLabels[step] || "Next →"}
      />
    </div>
  );
}

// ── Step 0: Upload ───────────────────────────────────────────────────

function StepUpload({
  file, setFile, filePath, fileName, extractMode, setExtractMode, lang, setLang, loading, onUpload,
}: {
  file: File | null;
  setFile: (f: File | null) => void;
  filePath: string;
  fileName: string;
  extractMode: string;
  setExtractMode: (m: string) => void;
  lang: string;
  setLang: (l: string) => void;
  loading: boolean;
  onUpload: () => void;
}) {
  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Upload Document</h3>
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <div className="lg:col-span-3">
          <FileUpload
            accept=".pdf,.png,.jpg,.jpeg,.webp"
            label="Drop PDF or scanned image here"
            onFiles={(files) => setFile(files[0] || null)}
          />
          {file && (
            <div className="mt-3 card">
              {file.type.startsWith("image/") ? (
                <img src={URL.createObjectURL(file)} alt={file.name} className="max-w-md rounded-lg" />
              ) : (
                <p className="text-sm"><strong>{file.name}</strong> — {(file.size / 1024).toFixed(1)} KB</p>
              )}
              <button className="btn-primary mt-3" onClick={onUpload} disabled={loading}>
                {loading ? "Uploading…" : "Upload to Server"}
              </button>
            </div>
          )}
          {filePath && <p className="text-xs text-text-muted mt-2">File ready: <code>{fileName}</code></p>}
        </div>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-semibold text-text-muted">Extraction mode</label>
            <select
              value={extractMode}
              onChange={(e) => setExtractMode(e.target.value)}
              className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="combined">Combined (best)</option>
              <option value="paddle">PaddleOCR</option>
              <option value="vision">GPT-4 Vision</option>
            </select>
          </div>
          <div>
            <label className="text-xs font-semibold text-text-muted">Language</label>
            <select
              value={lang}
              onChange={(e) => setLang(e.target.value)}
              className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="mr">Marathi</option>
              <option value="hi">Hindi</option>
              <option value="en">English</option>
            </select>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Step 1: Quality ──────────────────────────────────────────────────

function StepQuality({
  file, filePath, fileName, qcResult, loading, onRun, onSkip,
}: {
  file: File | null;
  filePath: string;
  fileName: string;
  qcResult: Record<string, unknown> | null;
  loading: boolean;
  onRun: () => void;
  onSkip: () => void;
}) {
  if (!filePath) return <p className="text-warn font-medium">Upload a document first (step 1).</p>;

  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Quality Check</h3>
      <p className="text-xs text-text-muted mb-3">File: <code>{fileName}</code></p>
      {file ? (
        <button className="btn-primary mb-4" onClick={onRun} disabled={loading}>
          {loading ? "Analysing…" : "Run Quality Gate"}
        </button>
      ) : (
        <div>
          <p className="text-sm text-text-muted mb-2">The uploaded file is no longer in memory. You can skip to Extract or re-upload.</p>
          <button className="btn-secondary" onClick={onSkip}>Skip Quality Check</button>
        </div>
      )}
      {qcResult && <QualityResults data={qcResult} />}
    </div>
  );
}

function QualityResults({ data }: { data: Record<string, unknown> }) {
  const passed = (data.gate_passed ?? data.passed ?? false) as boolean;
  const reasons = (data.gate_reasons ?? data.issues ?? []) as string[];
  return (
    <div>
      {passed ? (
        <div className="bg-green-50 text-primary-dark px-4 py-2.5 rounded-lg font-semibold mb-3">Quality gate PASSED</div>
      ) : (
        <div className="bg-red-50 text-danger px-4 py-2.5 rounded-lg font-semibold mb-3">
          Quality gate FAILED
          {reasons.length > 0 && <ul className="mt-1 font-normal text-sm list-disc ml-5">{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
        </div>
      )}
      <div className="grid grid-cols-5 gap-3">
        <MetricCard label="Width" value={data.width as number || 0} />
        <MetricCard label="Height" value={data.height as number || 0} />
        <MetricCard label="Sharp" value={String(data.sharpness ?? "—")} />
        <MetricCard label="Bright" value={typeof data.mean_brightness === "number" ? data.mean_brightness.toFixed(0) : "—"} />
        <MetricCard label="Contrast" value={typeof data.contrast_ratio === "number" ? data.contrast_ratio.toFixed(2) : "—"} />
      </div>
    </div>
  );
}

// ── Step 2: Extract ──────────────────────────────────────────────────

function StepExtract({
  filePath, fileName, extractMode, lang, username, loading, progress, progressMsg, result, onRun,
}: {
  filePath: string;
  fileName: string;
  extractMode: string;
  lang: string;
  username: string;
  loading: boolean;
  progress: number;
  progressMsg: string;
  result: Record<string, unknown> | null;
  onRun: () => void;
}) {
  if (!filePath) return <p className="text-warn font-medium">Upload a document first.</p>;

  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Run Extraction Pipeline</h3>
      <p className="text-xs text-text-muted mb-3">File: <code>{fileName}</code> · Mode: <code>{extractMode}</code></p>
      <button className="btn-primary mb-4" onClick={onRun} disabled={loading}>
        {loading ? "Running…" : "Run Extraction"}
      </button>
      {loading && (
        <div className="mb-4">
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-gradient-to-r from-primary to-primary-light h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
          </div>
          <p className="text-xs text-text-muted mt-1">{progress}% {progressMsg}</p>
        </div>
      )}
      {result && <ExtractionSummary result={result} />}
    </div>
  );
}

function ExtractionSummary({ result }: { result: Record<string, unknown> }) {
  const merged = (result.merged_extraction ?? result) as Record<string, unknown>;
  const timing = result.timing_seconds as Record<string, number> | undefined;

  return (
    <div>
      {timing && (
        <div className="grid grid-cols-4 gap-3 mb-3">
          {Object.entries(timing).map(([k, v]) => (
            <MetricCard key={k} label={k.replace(/_/g, " ")} value={`${v.toFixed(1)}s`} />
          ))}
        </div>
      )}
      {typeof merged === "object" && merged && (
        <>
          <h4 className="font-semibold text-sm mb-2">Document Overview</h4>
          <div className="grid grid-cols-4 gap-3 mb-3">
            <MetricCard label="Document Type" value={String(merged.document_type ?? "—")} />
            <MetricCard label="Report Date" value={String(merged.report_date ?? "—")} />
            <MetricCard label="State" value={String(merged.state ?? "—")} />
            <MetricCard label="Survey No." value={String(merged.survey_number ?? "—")} />
          </div>
          <div className="grid grid-cols-4 gap-3 mb-3">
            <MetricCard label="District" value={String(merged.district ?? "—")} />
            <MetricCard label="Taluka" value={String(merged.taluka ?? "—")} />
            <MetricCard label="Village" value={String(merged.village ?? "—")} />
            <MetricCard label="Sub Division" value={String(merged.sub_division ?? "—")} />
          </div>

          {Array.isArray(merged.owners) && merged.owners.length > 0 && (
            <>
              <h4 className="font-semibold text-sm mb-2">Owners</h4>
              <div className="card overflow-hidden p-0 mb-3">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-green-50/50 text-text-muted text-xs uppercase">
                      <th className="px-3 py-2 text-left">Name</th>
                      <th className="px-3 py-2 text-left">Account No.</th>
                      <th className="px-3 py-2 text-left">Area (ha)</th>
                      <th className="px-3 py-2 text-left">Assessment (Rs)</th>
                      <th className="px-3 py-2 text-left">Mutation Ref</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(merged.owners as Record<string, unknown>[]).map((o, i) => (
                      <tr key={i} className="border-t border-border">
                        <td className="px-3 py-2">{String(o.name ?? "")}</td>
                        <td className="px-3 py-2">{String(o.account_number ?? "")}</td>
                        <td className="px-3 py-2">{String(o.area_hectare ?? "")}</td>
                        <td className="px-3 py-2">{String(o.assessment_rupees ?? "")}</td>
                        <td className="px-3 py-2">{String(o.mutation_ref ?? "")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
      <JsonViewer data={merged} title="View Full Extracted JSON" />
    </div>
  );
}

// ── Step 3: Semantic ─────────────────────────────────────────────────

function StepSemantic({
  extractResult, semanticResult, username, loading, progress, progressMsg, onRun,
}: {
  extractResult: Record<string, unknown> | null;
  semanticResult: Record<string, unknown> | null;
  username: string;
  loading: boolean;
  progress: number;
  progressMsg: string;
  onRun: () => void;
}) {
  if (!extractResult) return <p className="text-warn font-medium">Run extraction first (step 3).</p>;

  const semData = semanticResult
    ? ((semanticResult as Record<string, unknown>).semantic_knowledge_graph ?? semanticResult) as Record<string, unknown>
    : null;

  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Semantic Analysis & Knowledge Graph</h3>
      <p className="text-sm text-text-muted mb-3">
        Analyze the extracted data to build an ownership chain, identify current vs original owners,
        and visualize encumbrances and land relationships.
      </p>
      <button className="btn-primary mb-4" onClick={onRun} disabled={loading}>
        {loading ? "Analysing…" : "Run Semantic Analysis"}
      </button>
      {loading && (
        <div className="mb-4">
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-gradient-to-r from-primary to-primary-light h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
          </div>
          <p className="text-xs text-text-muted mt-1">{progress}% {progressMsg}</p>
        </div>
      )}
      {semData && <SemanticView data={semData} />}
    </div>
  );
}

function SemanticView({ data }: { data: Record<string, unknown> }) {
  const summary = (data.land_summary ?? {}) as Record<string, unknown>;
  const original = (data.original_owner ?? {}) as Record<string, unknown>;
  const current = (data.current_owners ?? []) as Record<string, unknown>[];
  const enc = (data.encumbrances_mapped ?? []) as Record<string, unknown>[];
  const dates = (data.key_dates ?? {}) as Record<string, unknown>;

  return (
    <div>
      {Object.keys(summary).length > 0 && (
        <>
          <h4 className="font-semibold text-sm mb-2">Land Summary</h4>
          <div className="grid grid-cols-4 gap-3 mb-3">
            <MetricCard label="Survey No." value={String(summary.survey_number ?? "—")} />
            <MetricCard label="Village" value={String(summary.village ?? "—")} />
            <MetricCard label="Taluka" value={String(summary.taluka ?? "—")} />
            <MetricCard label="District" value={String(summary.district ?? "—")} />
          </div>
          <div className="grid grid-cols-4 gap-3 mb-3">
            <MetricCard label="Total Area" value={`${summary.total_area_hectare ?? "—"} ha`} />
            <MetricCard label="Cultivable" value={`${summary.cultivable_hectare ?? "—"} ha`} />
            <MetricCard label="Uncultivable" value={`${summary.uncultivable_hectare ?? "—"} ha`} />
            <MetricCard label="Tenure" value={String(summary.tenure_type ?? "—")} />
          </div>
        </>
      )}

      {original && !!(original as Record<string, unknown>).name && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 mb-3 text-sm">
          <strong>Original Owner:</strong> {String((original as Record<string, unknown>).name)} — {String((original as Record<string, unknown>).notes ?? "")}
        </div>
      )}

      {current.length > 0 && (
        <>
          <h4 className="font-semibold text-sm mb-2">Current Owners</h4>
          <div className="card overflow-hidden p-0 mb-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-green-50/50 text-text-muted text-xs uppercase">
                  <th className="px-3 py-2 text-left">Name</th>
                  <th className="px-3 py-2 text-left">Account No.</th>
                  <th className="px-3 py-2 text-left">Area (ha)</th>
                  <th className="px-3 py-2 text-left">Assessment (Rs)</th>
                </tr>
              </thead>
              <tbody>
                {current.map((o, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="px-3 py-2">{String(o.name ?? "")}</td>
                    <td className="px-3 py-2">{String(o.account_number ?? "")}</td>
                    <td className="px-3 py-2">{String(o.area_hectare ?? "")}</td>
                    <td className="px-3 py-2">{String(o.assessment_rupees ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {enc.length > 0 && (
        <>
          <h4 className="font-semibold text-sm mb-2">Encumbrances (Loans & Mortgages)</h4>
          <div className="card overflow-hidden p-0 mb-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-green-50/50 text-text-muted text-xs uppercase">
                  <th className="px-3 py-2 text-left">Owner</th>
                  <th className="px-3 py-2 text-left">Bank</th>
                  <th className="px-3 py-2 text-left">Amount (Rs)</th>
                  <th className="px-3 py-2 text-left">Type</th>
                  <th className="px-3 py-2 text-left">Mutation Ref</th>
                </tr>
              </thead>
              <tbody>
                {enc.map((e, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="px-3 py-2">{String(e.owner_name ?? "")}</td>
                    <td className="px-3 py-2">{String(e.bank_name ?? "")}</td>
                    <td className="px-3 py-2">{String(e.amount_rupees ?? "")}</td>
                    <td className="px-3 py-2">{String(e.type ?? "")}</td>
                    <td className="px-3 py-2">{String(e.mutation_ref ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {dates && Object.values(dates).some(Boolean) && (
        <>
          <h4 className="font-semibold text-sm mb-2">Key Dates</h4>
          <div className="grid grid-cols-3 gap-3 mb-3">
            <MetricCard label="Report Date" value={String(dates.report_date ?? "—")} />
            <MetricCard label="Last Mutation No." value={String(dates.last_mutation_number ?? "—")} />
            <MetricCard label="Last Mutation Date" value={String(dates.last_mutation_date ?? "—")} />
          </div>
        </>
      )}

      <JsonViewer data={data} title="View Full Semantic JSON" />
    </div>
  );
}

// ── Step 4: Output ───────────────────────────────────────────────────

function StepOutput({
  extractResult, semanticResult, fileName, extractMode, onDone,
}: {
  extractResult: Record<string, unknown> | null;
  semanticResult: Record<string, unknown> | null;
  fileName: string;
  extractMode: string;
  onDone: () => void;
}) {
  useEffect(() => { onDone(); }, [onDone]);

  if (!extractResult) return <p className="text-warn font-medium">Run extraction first.</p>;

  const final = {
    extraction: extractResult,
    semantic: semanticResult,
    metadata: { file: fileName, mode: extractMode },
  };

  const merged = (extractResult.merged_extraction ?? {}) as Record<string, unknown>;
  const sem = semanticResult
    ? ((semanticResult as Record<string, unknown>).semantic_knowledge_graph ?? semanticResult) as Record<string, unknown>
    : null;

  const downloadJson = () => {
    const blob = new Blob([JSON.stringify(final, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "uc1_output.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Final Output</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div className="card">
          <h4 className="font-semibold text-sm mb-2">Extraction Summary</h4>
          <div className="space-y-1 text-sm">
            {["document_type", "report_date", "district", "taluka", "village", "survey_number"].map((k) => (
              <p key={k}><strong>{k.replace(/_/g, " ")}:</strong> {String((merged as Record<string, string>)[k] ?? "—")}</p>
            ))}
          </div>
        </div>
        <div className="card">
          <h4 className="font-semibold text-sm mb-2">Semantic Summary</h4>
          {sem ? (
            <div className="space-y-1 text-sm">
              <p><strong>Total Area:</strong> {String((sem.land_summary as Record<string, unknown>)?.total_area_hectare ?? "—")} ha</p>
              <p><strong>Tenure:</strong> {String((sem.land_summary as Record<string, unknown>)?.tenure_type ?? "—")}</p>
              <p><strong>Current Owners:</strong> {(sem.current_owners as unknown[])?.length ?? 0}</p>
              <p><strong>Encumbrances:</strong> {(sem.encumbrances_mapped as unknown[])?.length ?? 0}</p>
            </div>
          ) : (
            <p className="text-xs text-text-muted">No semantic analysis run yet</p>
          )}
        </div>
      </div>
      <JsonViewer data={final} title="View Full JSON Output" />
      <button className="btn-primary mt-4" onClick={downloadJson}>Download JSON</button>
    </div>
  );
}

// ── Batch Processing ─────────────────────────────────────────────────

function BatchFlow({ username }: { username: string }) {
  const [files, setFiles] = useState<File[]>([]);
  const [filePaths, setFilePaths] = useState<string[]>([]);
  const [batchMode, setBatchMode] = useState("combined");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  const handleUpload = async () => {
    if (files.length === 0) return;
    setLoading(true);
    const uploaded = await uploadFiles(files, username);
    setFilePaths(uploaded.map((u) => u.path));
    setLoading(false);
  };

  const handleProcess = async () => {
    if (filePaths.length === 0) return;
    setLoading(true);
    setProgress(0);
    const resp = await submitJob("/uc1/batch", {
      file_paths: filePaths,
      mode: batchMode,
      lang: "mr",
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

  return (
    <div>
      <hr className="section-divider" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <FileUpload
          accept=".pdf,.png,.jpg,.jpeg"
          multiple
          label="Drop PDFs / images here"
          onFiles={(f) => { setFiles(f); setFilePaths([]); setResult(null); }}
        />
        <div>
          <label className="text-xs font-semibold text-text-muted">Extraction mode</label>
          <div className="flex gap-3 mt-1">
            {["combined", "paddle", "vision"].map((m) => (
              <label key={m} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input type="radio" name="batch_mode" checked={batchMode === m} onChange={() => setBatchMode(m)} className="accent-primary" />
                {m === "combined" ? "Combined" : m === "paddle" ? "PaddleOCR" : "GPT-4 Vision"}
              </label>
            ))}
          </div>
        </div>
      </div>

      {files.length > 0 && filePaths.length === 0 && (
        <button className="btn-primary mb-4" onClick={handleUpload} disabled={loading}>
          {loading ? "Uploading…" : `Upload ${files.length} files`}
        </button>
      )}

      {filePaths.length > 0 && (
        <>
          <p className="text-sm font-medium mb-2">{filePaths.length} documents ready</p>
          <button className="btn-primary mb-4 w-full" onClick={handleProcess} disabled={loading}>
            {loading ? "Processing…" : "Process All"}
          </button>
        </>
      )}

      {loading && (
        <div className="mb-4">
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-gradient-to-r from-primary to-primary-light h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
          </div>
          <p className="text-xs text-text-muted mt-1">{progress}% {progressMsg}</p>
        </div>
      )}

      {result && (
        <div>
          <h3 className="font-semibold text-sm mb-3">Extraction Results</h3>
          <div className="grid grid-cols-3 gap-3 mb-3">
            <MetricCard label="Total" value={result.total as number ?? 0} />
            <MetricCard label="Succeeded" value={result.succeeded as number ?? 0} />
            <MetricCard label="Failed" value={result.failed as number ?? 0} />
          </div>
          <JsonViewer data={result} title="View Full Batch Result" />
        </div>
      )}
    </div>
  );
}

// ── Poll helper ──────────────────────────────────────────────────────

async function pollJob(
  jobId: string,
  onProgress: (pct: number, msg: string) => void,
  timeout = 300_000,
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
