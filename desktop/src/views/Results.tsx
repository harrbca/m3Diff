import { useEffect, useMemo, useRef, useState } from "react";
import { save } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import { rpc } from "../rpc";
import type { ChangeCounts, DiffResult, TableDiff } from "../types";
import { Drilldown } from "./Drilldown";

interface Props {
  result: DiffResult;
}

type SplitMode = "vert" | "horz";

const STATUS_ORDER = ["error", "missing_in_a", "missing_in_b", "modified", "identical"];
const SPLIT_KEY = "m3diff.splitMode";
const RATIO_KEY = "m3diff.splitRatio";

// status → results-row class + tag
const ROW_CLASS: Record<string, string> = {
  modified: "r-mod",
  missing_in_a: "r-miss",
  missing_in_b: "r-miss",
  identical: "r-idn",
  error: "r-err",
};
const TAG: Record<string, { cls: string; label: string }> = {
  modified: { cls: "mod", label: "Modified" },
  missing_in_a: { cls: "miss", label: "Missing in A" },
  missing_in_b: { cls: "miss", label: "Missing in B" },
  identical: { cls: "idn", label: "Identical" },
  error: { cls: "err", label: "Error" },
};

function StatusTag({ status }: { status: string }) {
  const t = TAG[status] ?? { cls: "", label: status };
  return <span className={`tag ${t.cls}`}>{t.label}</span>;
}

function Ledger({ c, status }: { c: ChangeCounts; status: string }) {
  if (status === "identical" || status === "error") {
    return (
      <span className="led">
        <span className="z">—</span>
      </span>
    );
  }
  const cell = (n: number, cls: "a" | "r" | "m", sign: string) => (
    <span className={n > 0 ? cls : "z"}>
      {sign}
      {n.toLocaleString()}
    </span>
  );
  return (
    <span className="led tnum">
      {cell(c.added, "a", "+")} {cell(c.removed, "r", "−")} {cell(c.modified, "m", "~")}
    </span>
  );
}

export function ResultsView({ result }: Props) {
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState("");

  const [splitMode, setSplitMode] = useState<SplitMode>(() =>
    localStorage.getItem(SPLIT_KEY) === "horz" ? "horz" : "vert",
  );
  const [ratio, setRatio] = useState<number>(() => {
    const r = parseFloat(localStorage.getItem(RATIO_KEY) ?? "");
    return r >= 0.15 && r <= 0.85 ? r : 0.55;
  });
  useEffect(() => localStorage.setItem(SPLIT_KEY, splitMode), [splitMode]);
  useEffect(() => localStorage.setItem(RATIO_KEY, String(ratio)), [ratio]);

  // A fresh result invalidates any prior selection.
  useEffect(() => setSelected(null), [result]);

  const areaRef = useRef<HTMLDivElement>(null);
  function startResize(e: React.PointerEvent) {
    e.preventDefault();
    const area = areaRef.current;
    if (!area) return;
    const rect = area.getBoundingClientRect();
    const vertical = splitMode === "vert";
    const move = (ev: PointerEvent) => {
      const frac = vertical
        ? (ev.clientY - rect.top) / rect.height
        : (ev.clientX - rect.left) / rect.width;
      setRatio(Math.min(0.85, Math.max(0.15, frac)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

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

  async function copyJson() {
    await navigator.clipboard.writeText(JSON.stringify(result, null, 2));
    setSaveMsg("copied JSON ✓");
  }

  // Engine-rendered (render RPC): identical bytes to `m3diff compare --format`.
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

  const s = result.summary;
  const ctx =
    result.a.cono && result.b.cono
      ? `${result.mode} · ${result.a.cono} → ${result.b.cono}`
      : `${result.mode}`;
  const selectedTable = selected ? result.tables[selected] : undefined;

  return (
    <div className="ws">
      <div className="toolbar">
        <span className="tb-ctx mono">{ctx}</span>
        <div className="summary">
          <span className="s">
            <b>{s.tables_compared.toLocaleString()}</b>compared
          </span>
          <span className="s ok">
            <b>{s.identical.toLocaleString()}</b>identical
          </span>
          <span className="s mod">
            <b>{s.modified.toLocaleString()}</b>modified
          </span>
          <span className="s bad">
            <b>{(s.missing_in_a + s.missing_in_b).toLocaleString()}</b>missing
          </span>
          <span className="s bad">
            <b>{s.errors.toLocaleString()}</b>error
          </span>
        </div>
        <span className="tb-sp" />
        {saveMsg && <span className="saved">{saveMsg}</span>}
        <input
          placeholder="filter table / description / program…"
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
        />
        <select value={status} onChange={(e) => setStatus(e.currentTarget.value)}>
          <option value="all">all statuses</option>
          <option value="modified">modified</option>
          <option value="missing_in_b">missing in B</option>
          <option value="missing_in_a">missing in A</option>
          <option value="error">error</option>
          <option value="identical">identical</option>
        </select>
        <div className="splitmode" role="group" aria-label="Split orientation">
          <button
            className={splitMode === "vert" ? "on" : ""}
            title="Stack — results on top, detail below"
            onClick={() => setSplitMode("vert")}
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3">
              <rect x="1.6" y="1.6" width="12.8" height="12.8" rx="1.4" />
              <line x1="1.6" y1="8" x2="14.4" y2="8" />
            </svg>
          </button>
          <button
            className={splitMode === "horz" ? "on" : ""}
            title="Side by side — results left, detail right"
            onClick={() => setSplitMode("horz")}
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3">
              <rect x="1.6" y="1.6" width="12.8" height="12.8" rx="1.4" />
              <line x1="8" y1="1.6" x2="8" y2="14.4" />
            </svg>
          </button>
        </div>
        <button className="act" onClick={copyJson}>
          Copy JSON
        </button>
        <button className="act" onClick={() => saveAs("json")}>
          JSON
        </button>
        <button className="act" onClick={() => saveAs("csv")}>
          CSV
        </button>
        <button className="act" onClick={() => saveAs("md")}>
          MD
        </button>
      </div>

      <div className={`splitarea ${splitMode}`} ref={areaRef}>
        <div className="pane pane-top" style={{ flex: `0 0 ${ratio * 100}%` }}>
          <table className="res">
            <thead>
              <tr>
                <th className="sevc" />
                <th>Table</th>
                <th>Class</th>
                <th>Status</th>
                <th className="num">Rows A</th>
                <th className="num">Rows B</th>
                <th>+ / − / ~</th>
                <th>PK</th>
                <th>Maintained by</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([name, td]) => (
                <tr
                  key={name}
                  className={`${ROW_CLASS[td.status] ?? ""} ${td.status === "identical" ? "quiet" : ""} ${
                    selected === name ? "sel" : ""
                  }`}
                  onClick={() => setSelected(name)}
                >
                  <td className="sevc">
                    <span className="sev" />
                  </td>
                  <td>
                    <span className="tn">{name}</span>
                    {td.description && <span className="tdsc"> {td.description}</span>}
                  </td>
                  <td className="mono">{td.class || "—"}</td>
                  <td>
                    <StatusTag status={td.status} />
                  </td>
                  <td className="num flow tnum">{td.rows_a.toLocaleString()}</td>
                  <td className="num flow tnum">{td.rows_b.toLocaleString()}</td>
                  <td>
                    <Ledger c={td.counts} status={td.status} />
                  </td>
                  <td className="pkc">
                    {td.pk_source === "metadata" ? (
                      <>metadata · {td.pk.join(", ")}</>
                    ) : td.pk_source === "heuristic" ? (
                      <span className="warn">
                        heuristic ⚠ · {td.pk_degenerate ? "full row (degenerate PK)" : "full row"}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="pkc">{td.maintained_by ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="resizer" onPointerDown={startResize} title="drag to resize" />

        <div className="pane pane-bot" style={{ flex: "1 1 0%" }}>
          {selectedTable ? (
            <Drilldown
              name={selected!}
              table={selectedTable}
              conoA={result.a.cono}
              conoB={result.b.cono}
              onClose={() => setSelected(null)}
            />
          ) : (
            <div className="dock-empty">
              {rows.length} tables · select one to see its differences
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
