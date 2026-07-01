import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { UploadControl } from "./UploadControl";
import type { NextseekApiService } from "@/lib/services/chatApi";

function createMockApi() {
  return {
    uploadFiles: vi.fn().mockResolvedValue({ job_id: "job-1" }),
    pollUpload: vi.fn().mockResolvedValue({ state: "SUCCESS", result: {} }),
  } as unknown as NextseekApiService;
}

describe("UploadControl", () => {
  it("renders selected files and fires upload callback on submit", async () => {
    const api = createMockApi();
    const onComplete = vi.fn();
    render(<UploadControl apiService={api} onUploadComplete={onComplete} />);

    const file = new File(["data"], "sample.csv", { type: "text/csv" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText("sample.csv")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => {
      expect(api.uploadFiles).toHaveBeenCalledWith([file]);
      expect(api.pollUpload).toHaveBeenCalledWith("job-1");
      expect(onComplete).toHaveBeenCalled();
    });
  });
});
