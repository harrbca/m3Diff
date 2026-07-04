import type { Classification, ExportInfo } from "../types";

interface Props {
  exportA: ExportInfo | null;
  exportB: ExportInfo | null;
  onLoad: (side: "a" | "b") => void;
  onClear: (side: "a" | "b") => void;
  onNext: () => void;
}

function classBreakdown(classifications: Classification[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const c of classifications) counts.set(c.class, (counts.get(c.class) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function ExportCard({ info, side, onLoad, onClear }: {
  info: ExportInfo | null;
  side: "a" | "b";
  onLoad: (side: "a" | "b") => void;
  onClear: (side: "a" | "b") => void;
}) {
  const label = side === "a" ? "Export A" : "Export B (optional)";
  if (!info) {
    return (
      <div className="card slot empty">
        <h3>{label}</h3>
        <p className="muted">No export loaded.</p>
        <button onClick={() => onLoad(side)}>Browse…</button>
      </div>
    );
  }
  const nonEmpty = info.classifications.filter((c) => c.class !== "EMPTY").length;
  return (
    <div className="card slot">
      <div className="slot-head">
        <h3>{label}</h3>
        <button className="link" onClick={() => onClear(side)}>
          clear
        </button>
      </div>
      <div className="filename" title={info.path}>
        {info.name}
      </div>
      <dl className="stats">
        <div>
          <dt>Tables</dt>
          <dd>{info.classifications.length}</dd>
        </div>
        <div>
          <dt>Non-empty</dt>
          <dd>{nonEmpty}</dd>
        </div>
        <div>
          <dt>Companies</dt>
          <dd>{info.conos.length ? info.conos.join(", ") : "—"}</dd>
        </div>
      </dl>
      <div className="chips">
        {classBreakdown(info.classifications).map(([cls, n]) => (
          <span key={cls} className={`chip cls-${cls}`}>
            {cls} {n}
          </span>
        ))}
      </div>
      <button onClick={() => onLoad(side)}>Choose different…</button>
    </div>
  );
}

export function UploadView({ exportA, exportB, onLoad, onClear, onNext }: Props) {
  return (
    <section>
      <h2>Load exports</h2>
      <p className="muted">
        Pick one M3 export zip to compare two companies within it, or two exports to compare across
        tenants.
      </p>
      <div className="slots">
        <ExportCard info={exportA} side="a" onLoad={onLoad} onClear={onClear} />
        <ExportCard info={exportB} side="b" onLoad={onLoad} onClear={onClear} />
      </div>
      <div className="actions">
        <button className="primary" disabled={!exportA} onClick={onNext}>
          Next: choose scope →
        </button>
      </div>
    </section>
  );
}
