import { useState } from "react";
import { ChevronDown, Download } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Artifact, ArtifactTable, ArtifactFile, ArtifactPreview } from "@/lib/types/chat";

interface Props {
  artifacts: Artifact[];
  onDownloadArtifact: (artifactKey: string) => void;
}

/**
 * Render nested cell values (CV-param objects/arrays, e.g. PRIDE metadata) as
 * readable text instead of "[object Object]". Mirrors the backend
 * `_flatten_cell` in excel_export.py.
 */
function flattenCellValue(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) {
    return value.map(flattenCellValue).filter(Boolean).join("; ");
  }
  if (typeof value === "object") {
    const o = value as Record<string, unknown>;
    if (o.name != null && o.name !== "") {
      if (o.value != null && o.value !== "") return `${o.name} (${o.value})`;
      return o.accession != null && o.accession !== ""
        ? `${o.name} [${o.accession}]`
        : String(o.name);
    }
    return Object.entries(o)
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => `${k}=${typeof v === "object" ? flattenCellValue(v) : String(v)}`)
      .join("; ");
  }
  return String(value);
}

/** Format a numeric value with comma separators, pass through non-numbers. */
function formatCell(value: unknown, column: string): string {
  if (value == null) return "";
  if (typeof value === "number") return value.toLocaleString("en-US");
  // Try parsing string numbers in the "count" column
  if (column === "count" && typeof value === "string" && /^\d+$/.test(value)) {
    return Number(value).toLocaleString("en-US");
  }
  if (typeof value === "object") return flattenCellValue(value);
  return String(value);
}

/** Detect if a column should be right-aligned (numeric data). */
function isNumericColumn(col: string, data: Record<string, unknown>[]): boolean {
  if (data.length === 0) return false;
  // Check first few rows
  return data.slice(0, 5).every((row) => {
    const v = row[col];
    return v == null || typeof v === "number" || (typeof v === "string" && /^-?\d[\d,]*\.?\d*$/.test(v));
  });
}

export function ReportArtifacts({ artifacts, onDownloadArtifact }: Props) {
  const [previewOpen, setPreviewOpen] = useState(false);

  if (!artifacts || artifacts.length === 0) return null;

  const tables = artifacts.filter(
    (a): a is ArtifactTable => a.artifact_type === "table",
  );
  const files = artifacts.filter(
    (a): a is ArtifactFile => a.artifact_type === "file",
  );
  const previews = artifacts.filter(
    (a): a is ArtifactPreview => a.artifact_type === "preview",
  );
  const hasMultipleTables = tables.length > 1;

  // Show rows_returned count if available on first table artifact
  const rowsReturned = tables.length > 0 && "rows_returned" in tables[0]
    ? (tables[0] as ArtifactTable & { rows_returned?: number }).rows_returned
    : undefined;

  return (
    <div className="mt-3 space-y-4">
      {rowsReturned != null && (
        <p className="text-sm font-medium text-muted-foreground">
          {rowsReturned.toLocaleString("en-US")} rows returned
        </p>
      )}

      {tables.map((table) => {
        const numericCols = new Set(
          table.columns.filter((col) => isNumericColumn(col, table.data)),
        );

        return (
          <div key={table.key} className="rounded-md border border-border">
            <div className="flex items-center justify-between border-b-2 border-border bg-muted/50 px-3 py-2">
              <span className="text-sm font-semibold">{table.label}</span>
              <button
                type="button"
                data-testid="artifact-download"
                data-filename={`${table.key}.csv`}
                aria-label={`Download ${table.label}`}
                onClick={() => onDownloadArtifact(table.key)}
                className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Download className="h-3 w-3" />
                Download
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b-2 border-border bg-muted/40">
                    {table.columns.map((col) => (
                      <th
                        key={col}
                        className={cn(
                          "px-3 py-2 font-semibold uppercase tracking-wide text-xs",
                          numericCols.has(col) ? "text-right" : "text-left",
                        )}
                      >
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.data.map((row, idx) => (
                    <tr
                      key={idx}
                      className="border-b border-border/50 bg-background"
                    >
                      {table.columns.map((col) => (
                        <td
                          key={col}
                          className={cn(
                            "px-3 py-1.5 tabular-nums",
                            numericCols.has(col) ? "text-right" : "text-left",
                          )}
                        >
                          {formatCell(row[col], col)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {table.truncated && table.total_rows && (
              <div className="border-t border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
                Showing first {table.data.length.toLocaleString("en-US")} of{" "}
                {table.total_rows.toLocaleString("en-US")} rows. Download for full data.
              </div>
            )}
          </div>
        );
      })}

      {/* GEO preview artifacts: collapsed by default with download button */}
      {previews.map((preview) => (
        <div key={preview.key} className="space-y-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPreviewOpen((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted"
            >
              <ChevronDown
                className={cn(
                  "h-4 w-4 transition-transform duration-200",
                  previewOpen && "rotate-180",
                )}
              />
              {preview.label}
            </button>
          </div>

          {previewOpen && (
            <div className="space-y-3 rounded-md border border-border/60 bg-muted/10 p-3">
              {preview.sheets.map((sheet) => (
                <div key={sheet.name}>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">
                    {sheet.name} ({sheet.total_rows} rows)
                  </p>
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-xs">
                      <thead>
                        <tr className="border-b-2 border-border bg-muted/40">
                          {sheet.columns.map((col) => (
                            <th key={col} className="px-2 py-1 text-left font-semibold uppercase tracking-wide">
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {sheet.data.map((row, idx) => (
                          <tr
                            key={idx}
                            className="border-b border-border/50 bg-background"
                          >
                            {sheet.columns.map((col) => (
                              <td key={col} className="px-2 py-1">
                                {String(row[col] ?? "")}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {files.map((file) => (
        <button
          key={file.key}
          type="button"
          data-testid="artifact-download"
          data-filename={file.key}
          onClick={() => onDownloadArtifact(file.key)}
          className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted"
        >
          <Download className="h-4 w-4" />
          {file.label}
        </button>
      ))}

      {hasMultipleTables && (
        <button
          type="button"
          data-testid="artifact-download"
          data-filename="all_tables.xlsx"
          onClick={() => onDownloadArtifact("all_tables")}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground hover:bg-primary/90"
        >
          <Download className="h-4 w-4" />
          Download All Tables (.xlsx)
        </button>
      )}
    </div>
  );
}
