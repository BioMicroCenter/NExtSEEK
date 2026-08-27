import { render, screen } from "@testing-library/react";
import { CCActivityPanel } from "./CCActivityPanel";

test("renders steps and file changes from a trace", () => {
  render(
    <CCActivityPanel
      trace={{
        schema_version: "3/trace-v1",
        cc_session_id: "s",
        ts: "t",
        transcript_line_count: 6,
        turn_count: 3,
        num_turns: 3,
        files_created: ["report.md"],
        files_modified: [],
        tools_used: { Bash: 1 },
        steps: [
          { line: 2, kind: "bash", tool: "Bash", detail: "ls /data/input", status: "ok" },
        ],
      }}
    />,
  );
  expect(screen.getByText("ls /data/input")).toBeInTheDocument();
  expect(screen.getByText("report.md")).toBeInTheDocument();
});
