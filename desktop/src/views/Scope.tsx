import { useState } from "react";
import type { ExportInfo, Mode } from "../types";

interface Props {
  exportA: ExportInfo;
  exportB: ExportInfo | null;
  mode: Mode;
  onMode: (m: Mode) => void;
  busy: boolean;
  onRun: (params: { mode: Mode; conoA?: string; conoB?: string; tables?: string[] }) => void;
}

type Preset = "all" | "config" | "custom";

const MODE_HELP: Record<Mode, string> = {
  intra: "Compare two companies within Export A (config drift).",
  inter: "Compare a company in A vs a company in B (migration validation).",
  global: "Compare only tenant-wide data (CONO 0 + NO_CONO tables) across A and B.",
};

function ConoSelect({ label, conos, value, onChange }: {
  label: string;
  conos: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.currentTarget.value)}>
        <option value="">— pick company —</option>
        {conos.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ScopeView({ exportA, exportB, mode, onMode, busy, onRun }: Props) {
  const [conoA, setConoA] = useState("");
  const [conoB, setConoB] = useState("");
  const [preset, setPreset] = useState<Preset>("all");
  const [customGlob, setCustomGlob] = useState("CSY*,MITMAS,OCUSMA");

  const needsB = mode === "inter" || mode === "global";
  const needsConos = mode === "intra" || mode === "inter";
  const conosForB = mode === "intra" ? exportA.conos : exportB?.conos ?? [];

  function tablesForPreset(): string[] | undefined {
    if (preset === "all") return undefined;
    if (preset === "config") return ["CSY*", "C*"];
    return customGlob.split(",").map((s) => s.trim()).filter(Boolean);
  }

  const problems: string[] = [];
  if (needsB && !exportB) problems.push("This mode needs Export B — load it on the Load step.");
  if (needsConos && !conoA) problems.push("Pick a company for side A.");
  if (needsConos && !conoB) problems.push("Pick a company for side B.");

  function run() {
    onRun({
      mode,
      conoA: needsConos ? conoA : undefined,
      conoB: needsConos ? conoB : undefined,
      tables: tablesForPreset(),
    });
  }

  return (
    <section>
      <h2>Scope the comparison</h2>

      <div className="modes">
        {(["intra", "inter", "global"] as Mode[]).map((m) => (
          <label key={m} className={`mode ${mode === m ? "on" : ""}`}>
            <input type="radio" name="mode" checked={mode === m} onChange={() => onMode(m)} />
            <div>
              <strong>{m}</strong>
              <span className="muted">{MODE_HELP[m]}</span>
            </div>
          </label>
        ))}
      </div>

      {needsConos && (
        <div className="row-fields">
          <ConoSelect label={`Company A · ${exportA.name}`} conos={exportA.conos} value={conoA} onChange={setConoA} />
          <ConoSelect
            label={`Company B · ${(mode === "intra" ? exportA : exportB)?.name ?? "—"}`}
            conos={conosForB}
            value={conoB}
            onChange={setConoB}
          />
        </div>
      )}

      <div className="scope">
        <h3>Table scope</h3>
        <label className="field">
          <span>Preset</span>
          <select value={preset} onChange={(e) => setPreset(e.currentTarget.value as Preset)}>
            <option value="all">All tables</option>
            <option value="config">Configuration (CSY*, C*)</option>
            <option value="custom">Custom glob / list</option>
          </select>
        </label>
        {preset === "custom" && (
          <label className="field grow">
            <span>Tables</span>
            <input value={customGlob} onChange={(e) => setCustomGlob(e.currentTarget.value)} />
          </label>
        )}
        {preset === "config" && (
          <p className="muted small">
            Prefix approximation for now. The metadata category <code>MF</code> is the intended config
            default (ADR-006) once a schema cache is populated via Settings → Refresh schema.
          </p>
        )}
      </div>

      {problems.length > 0 && (
        <ul className="problems">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}

      <div className="actions">
        <button className="primary" disabled={busy || problems.length > 0} onClick={run}>
          Run comparison
        </button>
      </div>
    </section>
  );
}
