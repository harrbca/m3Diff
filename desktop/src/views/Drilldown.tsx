import { useState } from "react";
import type { ModRef, RowRef, TableDiff } from "../types";

interface Props {
  name: string;
  table: TableDiff;
  onClose: () => void;
}

type Tab = "modified" | "added" | "removed";
const PAGE = 50;

function pkLabel(pk: (string | null)[]): string {
  return pk.map((v) => (v === null ? "∅" : v === "" ? "″″" : v)).join(" · ") || "(row)";
}

function RowList({ rows }: { rows: RowRef[] }) {
  const [limit, setLimit] = useState(PAGE);
  return (
    <>
      <ul className="difflist">
        {rows.slice(0, limit).map((r, i) => (
          <li key={i}>
            <code className="pk">{pkLabel(r.pk)}</code>
          </li>
        ))}
      </ul>
      {rows.length > limit && (
        <button className="link" onClick={() => setLimit(limit + PAGE)}>
          show more ({rows.length - limit} left)
        </button>
      )}
    </>
  );
}

function ModList({ rows }: { rows: ModRef[] }) {
  const [limit, setLimit] = useState(PAGE);
  return (
    <>
      <ul className="difflist">
        {rows.slice(0, limit).map((m, i) => (
          <li key={i}>
            <code className="pk">{pkLabel(m.pk)}</code>
            <div className="changes">
              {Object.entries(m.changes).map(([field, ch]) => (
                <div key={field} className="change">
                  <span className="fname mono">{field}</span>
                  <span className="old">{ch.a ?? "∅"}</span>
                  <span className="arrow">→</span>
                  <span className="new">{ch.b ?? "∅"}</span>
                </div>
              ))}
              {Object.keys(m.changes).length === 0 && <span className="muted small">field detail unavailable</span>}
            </div>
          </li>
        ))}
      </ul>
      {rows.length > limit && (
        <button className="link" onClick={() => setLimit(limit + PAGE)}>
          show more ({rows.length - limit} left)
        </button>
      )}
    </>
  );
}

export function Drilldown({ name, table, onClose }: Props) {
  const [tab, setTab] = useState<Tab>(
    table.counts.modified ? "modified" : table.counts.added ? "added" : "removed",
  );

  return (
    <div className="drilldown">
      <div className="drill-head">
        <h3 className="mono">{name}</h3>
        {table.description && <span className="muted small">{table.description}</span>}
        <button className="link" onClick={onClose}>
          close ✕
        </button>
      </div>

      <div className="meta">
        <span className="chip">{table.class}</span>
        <span className={`chip status-${table.status}`}>{table.status}</span>
        {table.maintained_by && (
          <span className="chip pgm" title="Maintaining program (from M3 metadata)">
            {table.maintained_by}
          </span>
        )}
        <span className="muted small">
          PK [{table.pk.join(", ") || "—"}] · {table.pk_source}
          {table.schema_component ? ` · ${table.schema_component}` : ""}
          {table.component_ambiguous ? " · component ambiguous ⚠" : ""}
        </span>
      </div>
      <div className="notes">
        {!table.schema_match && <span className="note warn">schemas differ — compared on intersection</span>}
        {table.global_subset && <span className="note">global subset (CONO 0 only)</span>}
        {!table.modified_detail && <span className="note warn">large table — field detail dropped</span>}
        {table.pk_degenerate && (
          <span className="note warn">
            PK not unique in this export (blank key column) — compared by full row
          </span>
        )}
        {table.truncated && <span className="note warn">rows truncated — see JSON/CSV for full detail</span>}
        {table.error && <span className="note bad">error: {table.error}</span>}
      </div>

      <div className="tabs">
        <button className={tab === "modified" ? "on" : ""} onClick={() => setTab("modified")}>
          Modified {table.counts.modified}
        </button>
        <button className={tab === "added" ? "on" : ""} onClick={() => setTab("added")}>
          Added {table.counts.added}
        </button>
        <button className={tab === "removed" ? "on" : ""} onClick={() => setTab("removed")}>
          Removed {table.counts.removed}
        </button>
      </div>

      <div className="tabbody">
        {tab === "modified" && <ModList rows={table.modified} />}
        {tab === "added" && <RowList rows={table.added} />}
        {tab === "removed" && <RowList rows={table.removed} />}
      </div>
    </div>
  );
}
