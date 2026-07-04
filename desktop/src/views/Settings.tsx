import { useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { rpc, type Progress } from "../rpc";

export interface AppSettings {
  schemaDb: string;
  ionapi: string;
  ignoredFields: string;
  nullEqualsEmpty: boolean;
  maskCono: boolean;
}

interface Props {
  settings: AppSettings;
  onChange: (s: AppSettings) => void;
  onClose: () => void;
}

function PathField({ label, value, extensions, onPick }: {
  label: string;
  value: string;
  extensions: string[];
  onPick: (path: string) => void;
}) {
  async function browse() {
    const path = await open({ multiple: false, filters: [{ name: label, extensions }] });
    if (typeof path === "string") onPick(path);
  }
  return (
    <label className="field grow">
      <span>{label}</span>
      <div className="path-row">
        <input value={value} placeholder="(none)" onChange={(e) => onPick(e.currentTarget.value)} />
        <button onClick={browse}>Browse…</button>
      </div>
    </label>
  );
}

export function SettingsView({ settings, onChange, onClose }: Props) {
  const [refresh, setRefresh] = useState<string>("");
  const set = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) =>
    onChange({ ...settings, [key]: value });

  async function refreshSchema() {
    setRefresh("refreshing…");
    try {
      const res = await rpc.request<{ tables: number }>(
        "schema_refresh",
        { ionapi: settings.ionapi, schema_db: settings.schemaDb },
        (p: Progress) => setRefresh(`refreshing… ${p.done}/${p.total} (${p.table})`),
      );
      setRefresh(`done — ${res.tables} tables cached`);
    } catch (e) {
      setRefresh(`failed: ${String(e)}`);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="drill-head">
          <h3>Settings</h3>
          <button className="link" onClick={onClose}>
            close ✕
          </button>
        </div>

        <PathField label="Schema cache (SQLite)" value={settings.schemaDb} extensions={["db", "sqlite"]}
          onPick={(p) => set("schemaDb", p)} />
        <PathField label=".ionapi credentials" value={settings.ionapi} extensions={["ionapi"]}
          onPick={(p) => set("ionapi", p)} />

        <div className="refresh-row">
          <button disabled={!settings.ionapi || !settings.schemaDb} onClick={refreshSchema}>
            Refresh schema from M3
          </button>
          <span className="muted small">{refresh}</span>
        </div>

        <label className="field grow">
          <span>Ignored fields (comma-separated globs)</span>
          <input value={settings.ignoredFields} onChange={(e) => set("ignoredFields", e.currentTarget.value)} />
        </label>

        <label className="check">
          <input type="checkbox" checked={settings.nullEqualsEmpty}
            onChange={(e) => set("nullEqualsEmpty", e.currentTarget.checked)} />
          Treat null and empty string as equal
        </label>
        <label className="check">
          <input type="checkbox" checked={settings.maskCono}
            onChange={(e) => set("maskCono", e.currentTarget.checked)} />
          Mask CONO in the comparison key
        </label>

        <p className="muted small">
          The <code>.ionapi</code> file is a secret — it is only read for schema refresh, never logged.
          Schema refresh needs network access and the <code>httpx</code> extra installed.
        </p>
      </div>
    </div>
  );
}
