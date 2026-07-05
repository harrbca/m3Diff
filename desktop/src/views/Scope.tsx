import { useState } from "react";
import type { ExportInfo, Mode } from "../types";

interface Props {
  exportA: ExportInfo;
  exportB: ExportInfo | null;
  mode: Mode;
  onMode: (m: Mode) => void;
  busy: boolean;
  hasSchemaDb: boolean;
  onRun: (params: {
    mode: Mode;
    conoA?: string;
    conoB?: string;
    tables?: string[];
    categories?: string[];
  }) => void;
}

type Preset = "all" | "categories" | "custom";

// Metadata table categories (ADR-006/016). MF is the config-drift default.
const CATEGORIES: { code: string; label: string; hint: string }[] = [
  { code: "MF", label: "MF · master + config", hint: "items, customers, system configuration" },
  { code: "TF", label: "TF · transactions", hint: "orders, invoices, ledgers" },
  { code: "WF", label: "WF · work files", hint: "in-progress scratch data (usually noise)" },
  { code: "ST", label: "ST · statistics", hint: "statistics and temporary output" },
  { code: "SF", label: "SF · derived", hint: "join-dynamic pseudo-tables" },
];

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

export function ScopeView({ exportA, exportB, mode, onMode, busy, hasSchemaDb, onRun }: Props) {
  const [conoA, setConoA] = useState("");
  const [conoB, setConoB] = useState("");
  const [preset, setPreset] = useState<Preset>("all");
  const [cats, setCats] = useState<string[]>(["MF"]);
  const [customGlob, setCustomGlob] = useState("CSY*,MITMAS,OCUSMA");

  const needsB = mode === "inter" || mode === "global";
  const needsConos = mode === "intra" || mode === "inter";
  const conosForB = mode === "intra" ? exportA.conos : exportB?.conos ?? [];

  function scopeForPreset(): { tables?: string[]; categories?: string[] } {
    if (preset === "all") return {};
    if (preset === "categories") return { categories: cats };
    return { tables: customGlob.split(",").map((s) => s.trim()).filter(Boolean) };
  }

  function toggleCat(code: string, on: boolean) {
    setCats((prev) => (on ? [...prev, code] : prev.filter((c) => c !== code)));
  }

  const problems: string[] = [];
  if (needsB && !exportB) problems.push("This mode needs Export B — load it on the Load step.");
  if (needsConos && !conoA) problems.push("Pick a company for side A.");
  if (needsConos && !conoB) problems.push("Pick a company for side B.");
  if (preset === "categories" && !hasSchemaDb)
    problems.push("Category scoping needs the schema cache — set Schema DB in Settings (⚙) and refresh.");
  if (preset === "categories" && cats.length === 0) problems.push("Pick at least one category.");

  function run() {
    onRun({
      mode,
      conoA: needsConos ? conoA : undefined,
      conoB: needsConos ? conoB : undefined,
      ...scopeForPreset(),
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
            <option value="categories">By metadata category</option>
            <option value="custom">Custom glob / list</option>
          </select>
        </label>
        {preset === "categories" && (
          <div className="cats">
            {CATEGORIES.map((c) => (
              <label key={c.code} className="cat">
                <input
                  type="checkbox"
                  checked={cats.includes(c.code)}
                  onChange={(e) => toggleCat(c.code, e.currentTarget.checked)}
                />
                <span>{c.label}</span>
                <span className="muted small">— {c.hint}</span>
              </label>
            ))}
            <p className="muted small">
              Categories come from the schema cache (Settings → Refresh schema). Tables not in the
              cache are only selectable via a custom glob.
            </p>
          </div>
        )}
        {preset === "custom" && (
          <label className="field grow">
            <span>Tables</span>
            <input value={customGlob} onChange={(e) => setCustomGlob(e.currentTarget.value)} />
          </label>
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
