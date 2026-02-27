import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ReportArtifacts } from "../ChatPanel/ReportArtifacts";
import type { ArtifactTable, ArtifactFile } from "@/lib/types/chat";

const sampleTable: ArtifactTable = {
  artifact_type: "table",
  key: "samples",
  label: "Samples",
  columns: ["UID", "Title", "SampleType"],
  data: [
    { UID: "NHP-260225MIT-1", Title: "Blood sample", SampleType: "NHP_blood" },
    { UID: "NHP-260225MIT-2", Title: "Tissue sample", SampleType: "NHP_tissue" },
  ],
};

const protocolTable: ArtifactTable = {
  artifact_type: "table",
  key: "protocols",
  label: "Protocols",
  columns: ["name", "description"],
  data: [{ name: "RNA-seq", description: "Standard protocol" }],
};

const geoFile: ArtifactFile = {
  artifact_type: "file",
  key: "geo_seq_workbooks",
  label: "GEO Submission Workbook",
  file_format: "xlsx",
};

describe("ReportArtifacts", () => {
  it("renders nothing when artifacts is empty", () => {
    const { container } = render(
      <ReportArtifacts artifacts={[]} onDownloadArtifact={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders a table with correct headers and row data", () => {
    render(
      <ReportArtifacts artifacts={[sampleTable]} onDownloadArtifact={() => {}} />,
    );
    expect(screen.getByText("UID")).toBeInTheDocument();
    expect(screen.getByText("Title")).toBeInTheDocument();
    expect(screen.getByText("SampleType")).toBeInTheDocument();
    expect(screen.getByText("NHP-260225MIT-1")).toBeInTheDocument();
    expect(screen.getByText("Blood sample")).toBeInTheDocument();
  });

  it("renders zebra-striped rows", () => {
    const { container } = render(
      <ReportArtifacts artifacts={[sampleTable]} onDownloadArtifact={() => {}} />,
    );
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].className).toContain("bg-background");
    expect(rows[1].className).toContain("bg-muted/20");
  });

  it("renders a download button per table artifact", () => {
    render(
      <ReportArtifacts artifacts={[sampleTable]} onDownloadArtifact={() => {}} />,
    );
    expect(screen.getByLabelText("Download Samples")).toBeInTheDocument();
  });

  it("renders file artifact as a download button with label", () => {
    render(
      <ReportArtifacts artifacts={[geoFile]} onDownloadArtifact={() => {}} />,
    );
    expect(screen.getByText("GEO Submission Workbook")).toBeInTheDocument();
  });

  it("renders 'Download All Tables' when 2+ tables present", () => {
    render(
      <ReportArtifacts
        artifacts={[sampleTable, protocolTable]}
        onDownloadArtifact={() => {}}
      />,
    );
    expect(screen.getByText("Download All Tables (.xlsx)")).toBeInTheDocument();
  });

  it("does NOT render 'Download All Tables' with only 1 table", () => {
    render(
      <ReportArtifacts artifacts={[sampleTable]} onDownloadArtifact={() => {}} />,
    );
    expect(screen.queryByText("Download All Tables (.xlsx)")).toBeNull();
  });

  it("calls onDownloadArtifact with correct key on per-table download click", () => {
    const handler = vi.fn();
    render(
      <ReportArtifacts artifacts={[sampleTable]} onDownloadArtifact={handler} />,
    );
    fireEvent.click(screen.getByLabelText("Download Samples"));
    expect(handler).toHaveBeenCalledWith("samples");
  });

  it("calls onDownloadArtifact('all_tables') on Download All click", () => {
    const handler = vi.fn();
    render(
      <ReportArtifacts
        artifacts={[sampleTable, protocolTable]}
        onDownloadArtifact={handler}
      />,
    );
    fireEvent.click(screen.getByText("Download All Tables (.xlsx)"));
    expect(handler).toHaveBeenCalledWith("all_tables");
  });
});
