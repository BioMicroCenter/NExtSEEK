import { Download } from "lucide-react";
import type { Artifact, ArtifactTable, ArtifactFile } from "@/lib/types/chat";

interface Props {
  artifacts: Artifact[];
  onDownloadArtifact: (artifactKey: string) => void;
}

export function ReportArtifacts({ artifacts, onDownloadArtifact }: Props) {
  if (!artifacts || artifacts.length === 0) return null;

  const tables = artifacts.filter(
    (a): a is ArtifactTable => a.artifact_type === "table",
  );
  const files = artifacts.filter(
    (a): a is ArtifactFile => a.artifact_type === "file",
  );
  const hasMultipleTables = tables.length > 1;

  return (
    <div className="mt-3 space-y-4">
      {tables.map((table) => (
        <div key={table.key} className="rounded-md border border-border">
          <div className="flex items-center justify-between border-b border-border bg-muted/50 px-3 py-2">
            <span className="text-sm font-medium">{table.label}</span>
            <button
              type="button"
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
                <tr className="border-b border-border bg-muted/30">
                  {table.columns.map((col) => (
                    <th
                      key={col}
                      className="px-3 py-1.5 text-left font-medium"
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
                    className={
                      idx % 2 === 0 ? "bg-background" : "bg-muted/20"
                    }
                  >
                    {table.columns.map((col) => (
                      <td key={col} className="px-3 py-1.5">
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

      {files.map((file) => (
        <button
          key={file.key}
          type="button"
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
