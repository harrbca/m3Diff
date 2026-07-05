// TypeScript mirror of the engine result contract (engine/src/m3diff/contract.py)
// and classification (classify.py). Kept in sync manually; the engine is the
// source of truth (ADR-005).

export interface SideInfo {
  file: string;
  cono: string | null;
  tables: number;
  rows: number | null;
}

export interface Settings {
  ignored_fields: string[];
  null_equals_empty: boolean;
  pk_mask: string[];
}

export interface Summary {
  tables_compared: number;
  identical: number;
  modified: number;
  missing_in_a: number;
  missing_in_b: number;
  errors: number;
}

export interface ChangeCounts {
  added: number;
  removed: number;
  modified: number;
}

export interface RowRef {
  pk: (string | null)[];
  row: Record<string, string>;
}

export interface FieldChange {
  a: string | null;
  b: string | null;
}

export interface ModRef {
  pk: (string | null)[];
  changes: Record<string, FieldChange>;
}

export interface TableDiff {
  class: string;
  pk: string[];
  pk_source: string;
  schema_component: string | null;
  component_ambiguous: boolean;
  schema_match: boolean;
  rows_a: number;
  rows_b: number;
  status: string;
  counts: ChangeCounts;
  added: RowRef[];
  removed: RowRef[];
  modified: ModRef[];
  truncated: boolean;
  global_subset: boolean;
  modified_detail: boolean;
  pk_degenerate: boolean;
  maintained_by: string | null;
  description: string | null;
  column_descriptions: Record<string, string>;
  error: string | null;
}

export interface DiffResult {
  tool_version: string;
  mode: string;
  generated_at: string;
  a: SideInfo;
  b: SideInfo;
  settings: Settings;
  summary: Summary;
  tables: Record<string, TableDiff>;
}

export interface Classification {
  table: string;
  class: string;
  fields: number;
  cono_field: string | null;
  cono_ambiguous: boolean;
  rows: number;
  rows_global: number;
  conos: string[];
  error: string | null;
}

export interface ClassifyResult {
  tables: Classification[];
}

export type Mode = "intra" | "inter" | "global";

export interface ExportInfo {
  path: string;
  name: string;
  classifications: Classification[];
  conos: string[]; // distinct non-zero CONOs observed
}
