import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { adoptTerminalSession } from "../sessionAdoption";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "../..");

function makeSessions(pendingNewChat: boolean) {
  return {
    pendingNewChat,
    promoteCreatedSession: vi.fn(),
    refresh: vi.fn(),
  };
}

describe("adoptTerminalSession", () => {
  it("promotes the created session while a new chat is pending", () => {
    const sessions = makeSessions(true);
    adoptTerminalSession(sessions, "sess-1");
    expect(sessions.promoteCreatedSession).toHaveBeenCalledWith("sess-1");
    expect(sessions.refresh).not.toHaveBeenCalled();
  });

  it("only refreshes the list once the chat is already adopted", () => {
    const sessions = makeSessions(false);
    adoptTerminalSession(sessions, "sess-1");
    expect(sessions.promoteCreatedSession).not.toHaveBeenCalled();
    expect(sessions.refresh).toHaveBeenCalled();
  });

  it("does nothing without a session id", () => {
    for (const sid of [null, undefined, ""]) {
      const sessions = makeSessions(true);
      adoptTerminalSession(sessions, sid);
      expect(sessions.promoteCreatedSession).not.toHaveBeenCalled();
      expect(sessions.refresh).not.toHaveBeenCalled();
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// #38 — "a new session is created after an errored turn".
//
// useSessions initialises `pendingNewChat` to true and ONLY
// `promoteCreatedSession` clears it. Until it is cleared every send posts
// `force_new: true` and the backend unconditionally creates a new session.
//
// Both shells adopted the session in their `query_complete` branch and nowhere
// else, so an errored turn left `pendingNewChat` true and the next send made a
// second, empty session. The backend hands out the same `session_id` on
// `query_error` (assistant/pipeline_adapter.py sets it for BOTH terminal
// events), so the error path can and must adopt it too.
//
// This is a source-level guard because the handler is defined inline in each
// shell component. It fails against the pre-fix files, which is the point.
// ─────────────────────────────────────────────────────────────────────────────

const SHELLS = ["EmbeddedApp.tsx", "AppLayout.tsx"];

/** The body of `case "<name>": { ... break; }` in a shell's event switch. */
function caseBody(source: string, name: string): string {
  const start = source.indexOf(`case "${name}": {`);
  if (start === -1) throw new Error(`no case "${name}" in shell`);
  const end = source.indexOf("break;", start);
  if (end === -1) throw new Error(`case "${name}" has no break`);
  return source.slice(start, end);
}

describe.each(SHELLS)("%s adopts the session on every terminal event", (shell) => {
  const source = readFileSync(resolve(SRC, shell), "utf8");

  it("adopts on query_complete", () => {
    expect(caseBody(source, "query_complete")).toContain("adoptTerminalSession");
  });

  it("adopts on query_error too (#38)", () => {
    expect(caseBody(source, "query_error")).toContain("adoptTerminalSession");
  });

  it("passes the event's own session_id as the fallback", () => {
    expect(caseBody(source, "query_error")).toContain("d.session_id");
  });

  it("routes adoption through the shared helper, not an inlined copy", () => {
    // The duplication is what let three of four call sites drift into step and
    // one not. Re-inlining the branch would silently reopen that.
    expect(caseBody(source, "query_error")).not.toContain("promoteCreatedSession");
    expect(caseBody(source, "query_complete")).not.toContain("promoteCreatedSession");
  });
});
