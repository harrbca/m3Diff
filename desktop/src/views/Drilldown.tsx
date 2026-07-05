import { Fragment, useEffect, useState } from "react";
import type { RowRef, TableDiff } from "../types";

interface Props {
  name: string;
  table: TableDiff;
  conoA: string | null;
  conoB: string | null;
  onClose: () => void;
}

type Tab = "modified" | "added" | "removed";

function pkLabel(pk: (string | null)[]): string {
  return pk.map((v) => (v === null ? "(null)" : v === "" ? "(empty)" : v)).join(" · ") || "(row)";
}

function Val({ v }: { v: string | null | undefined }) {
  if (v === null || v === undefined) return <i className="empty">null</i>;
  if (v === "") return <i className="empty">empty</i>;
  return <>{v}</>;
}

// Column order for the added/removed record tables: PK columns first (in key
// order), then every other field in first-seen order across the shown rows.
function columnsOf(rows: RowRef[], pk: string[]): string[] {
  const seen = new Set<string>();
  const cols: string[] = [];
  for (const name of pk) if (name && !seen.has(name)) (seen.add(name), cols.push(name));
  for (const r of rows) for (const k of Object.keys(r.row)) if (!seen.has(k)) (seen.add(k), cols.push(k));
  return cols;
}

function RecTable({
  rows,
  table,
  kind,
  sign,
  total,
}: {
  rows: RowRef[];
  table: TableDiff;
  kind: "addt" | "delt";
  sign: string;
  total: number;
}) {
  const cols = columnsOf(rows, table.pk);
  return (
    <div className="recwrap">
      <table className={`rec ${kind}`}>
        <thead>
          <tr>
            <th className="mk">{sign}</th>
            {cols.map((c) => (
              <th key={c}>
                <span className="hcode">{c}</span>
                <span className="hdesc">{table.column_descriptions[c] ?? ""}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="mk">{sign}</td>
              {cols.map((c, j) => (
                <td key={c} className={j === 0 ? "lab" : ""}>
                  <Val v={r.row[c]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {total > rows.length && (
        <div className="rec-more">
          showing first {rows.length.toLocaleString()} of {total.toLocaleString()} — export for full detail
        </div>
      )}
    </div>
  );
}

function ModBody({
  table,
  beforeLabel,
  afterLabel,
}: {
  table: TableDiff;
  beforeLabel: string;
  afterLabel: string;
}) {
  return (
    <>
      {table.modified.map((m, i) => {
        const entries = Object.entries(m.changes);
        return (
          <div className="grp" key={i}>
            <div className="grp-head">
              <span className="gpk">{pkLabel(m.pk)}</span>
              <span className="gpkk">{table.pk.join(", ")}</span>
              <span className="gn">{entries.length ? `${entries.length} changed` : "detail dropped"}</span>
            </div>
            <div className="dgrid">
              <div className="hd">Field</div>
              <div className="hd">Description</div>
              <div className="hd b">{beforeLabel}</div>
              <div className="hd a">{afterLabel}</div>
              {entries.length === 0 ? (
                <div className="empty" style={{ gridColumn: "1 / -1" }}>
                  field detail unavailable — large table (compared by row hash)
                </div>
              ) : (
                entries.map(([field, ch]) => (
                  <Fragment key={field}>
                    <div className="c-field">{field}</div>
                    <div className="c-desc">{table.column_descriptions[field] ?? ""}</div>
                    <div className="val b">
                      <Val v={ch.a} />
                    </div>
                    <div className="val a">
                      <Val v={ch.b} />
                    </div>
                  </Fragment>
                ))
              )}
            </div>
          </div>
        );
      })}
    </>
  );
}

export function Drilldown({ name, table, conoA, conoB, onClose }: Props) {
  const initial: Tab = table.counts.modified ? "modified" : table.counts.added ? "added" : "removed";
  const [tab, setTab] = useState<Tab>(initial);
  // Re-pick the default tab whenever a different table is selected.
  useEffect(() => setTab(initial), [name]); // eslint-disable-line react-hooks/exhaustive-deps

  const beforeLabel = `Before · A${conoA ? ` (${conoA})` : ""}`;
  const afterLabel = `After · B${conoB ? ` (${conoB})` : ""}`;

  const flags: { t: string; bad?: boolean }[] = [];
  if (!table.schema_match) flags.push({ t: "schemas differ — compared on the intersection" });
  if (table.global_subset) flags.push({ t: "global subset (CONO 0 only)" });
  if (!table.modified_detail) flags.push({ t: "large table — field detail dropped" });
  if (table.pk_degenerate)
    flags.push(
      table.pk_source === "metadata"
        ? {
            t: `${table.ambiguous_keys.toLocaleString()} ambiguous PK key${
              table.ambiguous_keys === 1 ? "" : "s"
            } (blank key columns) — those rows compared by full row; all other rows keep field detail`,
          }
        : { t: "PK not unique in this export (blank key column) — whole table compared by full row" },
    );
  if (table.truncated) flags.push({ t: "rows truncated — export for full detail" });
  if (table.error) flags.push({ t: `error: ${table.error}`, bad: true });

  const c = table.counts;
  const nothing = table.status === "identical" || (!c.added && !c.removed && !c.modified);

  return (
    <>
      <div className="dock-bar">
        <span className="name">{name}</span>
        {table.description && <span className="desc">{table.description}</span>}
        <span className="dock-sp" />
        <button className="act" onClick={onClose}>
          Deselect ✕
        </button>
      </div>

      <div className="dmeta">
        <span className="kv">
          <span className={`tag ${(TAG_CLS[table.status] ?? "")}`}>{TAG_LABEL[table.status] ?? table.status}</span>
        </span>
        <span className="kv">
          <span className="k">class </span>
          <span className="v">{table.class || "—"}</span>
        </span>
        <span className="kv">
          <span className="k">pk </span>
          <span className={`v ${table.pk_source === "heuristic" ? "warn" : ""}`}>
            {table.pk_source === "metadata" ? table.pk.join(", ") : "full row"}
          </span>{" "}
          <span className="k">
            ({table.pk_source || "?"}
            {table.pk_degenerate ? ", degenerate" : ""})
          </span>
        </span>
        {table.schema_component && (
          <span className="kv">
            <span className="k">component </span>
            <span className={`v ${table.component_ambiguous ? "warn" : ""}`}>
              {table.schema_component}
              {table.component_ambiguous ? " ⚠" : ""}
            </span>
          </span>
        )}
        {table.maintained_by && (
          <span className="kv">
            <span className="k">maint </span>
            <span className="v">{table.maintained_by}</span>
          </span>
        )}
        <span className="kv">
          <span className="k">rows </span>
          <span className="v">
            {table.rows_a.toLocaleString()} → {table.rows_b.toLocaleString()}
          </span>
        </span>
      </div>

      {flags.length > 0 && (
        <div className="dflags">
          {flags.map((f, i) => (
            <span key={i} className={`f ${f.bad ? "bad" : ""}`}>
              {f.t}
            </span>
          ))}
        </div>
      )}

      {nothing ? (
        <div className="dock-empty" style={{ height: "auto", padding: "28px" }}>
          {table.error ? "This table errored — see the flag above." : "No differences in scope for this table."}
        </div>
      ) : (
        <>
          <div className="wtabs" role="tablist" aria-label="Change type">
            <button
              className="wtab mod"
              role="tab"
              aria-selected={tab === "modified"}
              onClick={() => setTab("modified")}
            >
              Modified <span className="c tnum">{c.modified.toLocaleString()}</span>
            </button>
            <button
              className="wtab add"
              role="tab"
              aria-selected={tab === "added"}
              onClick={() => setTab("added")}
            >
              Added <span className="c tnum">{c.added.toLocaleString()}</span>
            </button>
            <button
              className="wtab del"
              role="tab"
              aria-selected={tab === "removed"}
              onClick={() => setTab("removed")}
            >
              Removed <span className="c tnum">{c.removed.toLocaleString()}</span>
            </button>
          </div>

          <div className="wtabbody">
            {tab === "modified" &&
              (c.modified ? (
                <ModBody table={table} beforeLabel={beforeLabel} afterLabel={afterLabel} />
              ) : (
                <div className="rec-more">No modified rows.</div>
              ))}
            {tab === "added" &&
              (c.added ? (
                <RecTable rows={table.added} table={table} kind="addt" sign="+" total={c.added} />
              ) : (
                <div className="rec-more">No added rows.</div>
              ))}
            {tab === "removed" &&
              (c.removed ? (
                <RecTable rows={table.removed} table={table} kind="delt" sign="−" total={c.removed} />
              ) : (
                <div className="rec-more">No removed rows.</div>
              ))}
          </div>
        </>
      )}
    </>
  );
}

const TAG_CLS: Record<string, string> = {
  modified: "mod",
  missing_in_a: "miss",
  missing_in_b: "miss",
  identical: "idn",
  error: "err",
};
const TAG_LABEL: Record<string, string> = {
  modified: "Modified",
  missing_in_a: "Missing in A",
  missing_in_b: "Missing in B",
  identical: "Identical",
  error: "Error",
};
