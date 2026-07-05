import { useMemo, useState } from "react";
import { save } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import { rpc } from "../rpc";
import type { DiffResult, TableDiff } from "../types";
import { Drilldown } from "./Drilldown";

interface Props {
  result: DiffResult;
}

const STATUS_ORDER = ["error", "missing_in_a", "missing_in_b", "modified", "identical"];

function SummaryCards({ result }: Props) {
  const s = result.summary;
  const cards: [string, number, string][] = [
    ["Compared", s.tables_compared, "neutral"],
    ["Identical", s.identical, "ok"],
    ["Modified", s.modified, "warn"],
    ["Missing in B", s.missing_in_b, "bad"],
    ["Missing in A", s.missing_in_a, "bad"],
    ["Errors", s.errors, "bad"],
  ];
  return (
    <div className="cards">
      {cards.map(([label, n, tone]) => (
        <div key={label} className={`card metric ${tone}`}>
          <div className="metric-n">{n}</div>
          <div className="metric-l">{label}</div>
        </div>
      ))}
    </div>
  );
}

export function ResultsView({ result }: Props) {
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const rows = useMemo(() => {
    const entries = Object.entries(result.tables) as [string, TableDiff][];
    const q = query.toLowerCase();
    return entries
      .filter(([, td]) => (status === "all" ? true : td.status === status))
      .filter(
        ([name, td]) =>
          name.toLowerCase().includes(q) ||
          (td.maintained_by ?? "").toLowerCase().includes(q) ||
          (td.description ?? "").toLowerCase().includes(q),
      )
      .sort((a, b) => {
        const s = STATUS_ORDER.indexOf(a[1].status) - STATUS_ORDER.indexOf(b[1].status);
        return s !== 0 ? s : a[0].localeCompare(b[0]);
      });
  }, [result, status, query]);

  const [saveMsg, setSaveMsg] = useState("");

  async function copyJson() {
    await navigator.clipboard.writeText(JSON.stringify(result, null, 2));
  }

  // Engine-rendered (render RPC): a saved file is byte-identical to what
  // `m3diff compare --format <fmt>` would write for this result.
  async function saveAs(format: "json" | "csv" | "md") {
    setSaveMsg("");
    const path = await save({
      defaultPath: `m3diff-result.${format}`,
      filters: [{ name: format.toUpperCase(), extensions: [format] }],
    });
    if (!path) return;
    try {
      setSaveMsg("rendering…");
      const res = await rpc.request<{ content: string }>("render", { result, format });
      await invoke("save_text_file", { path, contents: res.content });
      setSaveMsg(`saved ✓ ${path.split(/[\\/]/).pop()}`);
    } catch (e) {
      setSaveMsg(`save failed: ${String(e)}`);
    }
  }

  return (
    <section className="results">
      <div className="results-head">
        <h2>
          Results <span className="muted">· {result.mode}</span>
        </h2>
        <div className="export-actions">
          {saveMsg && <span className="muted small">{saveMsg}</span>}
          <button className="link" onClick={copyJson}>
            Copy JSON
          </button>
          <button className="link" onClick={() => saveAs("json")}>
            Save JSON
          </button>
          <button className="link" onClick={() => saveAs("csv")}>
            Save CSV
          </button>
          <button className="link" onClick={() => saveAs("md")}>
            Save MD
          </button>
        </div>
      </div>

      <SummaryCards result={result} />

      <div className="filters">
        <input placeholder="search table…" value={query} onChange={(e) => setQuery(e.currentTarget.value)} />
        <select value={status} onChange={(e) => setStatus(e.currentTarget.value)}>
          <option value="all">all statuses</option>
          <option value="modified">modified</option>
          <option value="missing_in_b">missing in B</option>
          <option value="missing_in_a">missing in A</option>
          <option value="error">error</option>
          <option value="identical">identical</option>
        </select>
        <span className="muted small">{rows.length} tables</span>
      </div>

      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Table</th>
              <th>Class</th>
              <th>Status</th>
              <th className="num">A</th>
              <th className="num">B</th>
              <th className="num">+ / − / ~</th>
              <th>PK</th>
              <th>Maintained by</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, td]) => (
              <tr key={name} className={selected === name ? "sel" : ""} onClick={() => setSelected(name)}>
                <td>
                  <span className="mono">{name}</span>
                  {td.description && <div className="table-desc muted small">{td.description}</div>}
                </td>
                <td>{td.class}</td>
                <td>
                  <span className={`dot status-${td.status}`} /> {td.status}
                </td>
                <td className="num">{td.rows_a}</td>
                <td className="num">{td.rows_b}</td>
                <td className="num">
                  {td.counts.added} / {td.counts.removed} / {td.counts.modified}
                </td>
                <td className="mono small">
                  {td.pk_source}
                  {td.pk_source === "heuristic" ? " ⚠" : ""}
                  {td.pk_degenerate ? " (degenerate)" : ""}
                </td>
                <td className="mono small">{td.maintained_by ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && result.tables[selected] && (
        <Drilldown name={selected} table={result.tables[selected]} onClose={() => setSelected(null)} />
      )}
    </section>
  );
}
