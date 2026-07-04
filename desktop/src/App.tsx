import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import "./styles.css";
import { rpc, RpcCancelled, type Progress } from "./rpc";
import type { ClassifyResult, DiffResult, ExportInfo, Mode } from "./types";
import { UploadView } from "./views/Upload";
import { ScopeView } from "./views/Scope";
import { ResultsView } from "./views/Results";
import { SettingsView, type AppSettings } from "./views/Settings";

type Step = "upload" | "scope" | "results";

const DEFAULT_SETTINGS: AppSettings = {
  schemaDb: "",
  ionapi: "",
  ignoredFields: "*lmdt,*rgdt,*rgtm,*lmts,*chno,*chid",
  nullEqualsEmpty: true,
  maskCono: true,
};

function distinctConos(result: ClassifyResult): string[] {
  const seen = new Set<string>();
  for (const c of result.tables) for (const cono of c.conos) seen.add(cono);
  return [...seen].sort((x, y) => Number(x) - Number(y));
}

export default function App() {
  const [step, setStep] = useState<Step>("upload");
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);

  const [backend, setBackend] = useState<string>("connecting…");
  const [exportA, setExportA] = useState<ExportInfo | null>(null);
  const [exportB, setExportB] = useState<ExportInfo | null>(null);

  const [mode, setMode] = useState<Mode>("intra");
  const [result, setResult] = useState<DiffResult | null>(null);
  const [busy, setBusy] = useState<null | { what: string; id: number; progress?: Progress }>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    rpc
      .ping()
      .then((r) => setBackend(`engine ${r.version}`))
      .catch((e) => setBackend(`backend error: ${String(e)}`));
  }, []);

  async function loadExport(side: "a" | "b") {
    setError("");
    const path = await open({ multiple: false, filters: [{ name: "M3 export", extensions: ["zip"] }] });
    if (typeof path !== "string") return;
    const setter = side === "a" ? setExportA : setExportB;
    setBusy({ what: `classifying ${side.toUpperCase()}`, id: -1 });
    try {
      const res = await rpc.request<ClassifyResult>("classify", { export: path });
      const name = path.split(/[\\/]/).pop() ?? path;
      setter({ path, name, classifications: res.tables, conos: distinctConos(res) });
    } catch (e) {
      setError(`Failed to read ${path}: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function runCompare(params: { mode: Mode; conoA?: string; conoB?: string; tables?: string[] }) {
    if (!exportA) return;
    setError("");
    setResult(null);
    const compareParams: Record<string, unknown> = {
      mode: params.mode,
      a: exportA.path,
      b: exportB?.path,
      cono_a: params.conoA,
      cono_b: params.conoB,
      tables: params.tables && params.tables.length ? params.tables : undefined,
      ignored_fields: settings.ignoredFields.split(",").map((s) => s.trim()).filter(Boolean),
      schema_db: settings.schemaDb || undefined,
      null_equals_empty: settings.nullEqualsEmpty,
      mask_cono: settings.maskCono,
      generated_at: new Date().toISOString(),
    };
    const handle = rpc.start<DiffResult>("compare", compareParams, (p) =>
      setBusy((b) => (b ? { ...b, progress: p } : b)),
    );
    setBusy({ what: "comparing", id: handle.id });
    try {
      const res = await handle.done;
      setResult(res);
      setStep("results");
    } catch (e) {
      if (e instanceof RpcCancelled) setError("Comparison cancelled.");
      else setError(`Comparison failed: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  function cancel() {
    if (busy && busy.id >= 0) rpc.cancel(busy.id);
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          m3diff <span className="backend">{backend}</span>
        </div>
        <nav className="steps">
          <button className={step === "upload" ? "on" : ""} onClick={() => setStep("upload")}>
            1 · Load
          </button>
          <button className={step === "scope" ? "on" : ""} disabled={!exportA} onClick={() => setStep("scope")}>
            2 · Scope
          </button>
          <button className={step === "results" ? "on" : ""} disabled={!result} onClick={() => setStep("results")}>
            3 · Results
          </button>
        </nav>
        <button className="gear" onClick={() => setShowSettings(true)} title="Settings">
          ⚙
        </button>
      </header>

      {error && <div className="banner error">{error}</div>}
      {busy && (
        <div className="banner busy">
          {busy.what}
          {busy.progress ? ` — ${busy.progress.done}/${busy.progress.total} (${busy.progress.table})` : "…"}
          {busy.id >= 0 && (
            <button className="link" onClick={cancel}>
              cancel
            </button>
          )}
        </div>
      )}

      <main className="content">
        {step === "upload" && (
          <UploadView
            exportA={exportA}
            exportB={exportB}
            onLoad={loadExport}
            onClear={(side) => (side === "a" ? setExportA(null) : setExportB(null))}
            onNext={() => setStep("scope")}
          />
        )}
        {step === "scope" && exportA && (
          <ScopeView
            exportA={exportA}
            exportB={exportB}
            mode={mode}
            onMode={setMode}
            busy={busy != null}
            onRun={runCompare}
          />
        )}
        {step === "results" && result && <ResultsView result={result} />}
      </main>

      {showSettings && (
        <SettingsView settings={settings} onChange={setSettings} onClose={() => setShowSettings(false)} />
      )}
    </div>
  );
}
