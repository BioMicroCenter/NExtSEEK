import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMessages } from "../useMessages";

describe("useMessages", () => {
  it("addUserMessage creates a message with isUser=true", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addUserMessage("hello");
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].isUser).toBe(true);
    expect(result.current.messages[0].content).toBe("hello");
    expect(result.current.messages[0].messageType).toBe("text");
  });

  it("addAssistantMessage creates a message with isUser=false", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addAssistantMessage("response");
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].isUser).toBe(false);
    expect(result.current.messages[0].content).toBe("response");
    expect(result.current.messages[0].messageType).toBe("text");
  });

  it("addSystemMessage creates a message with messageType='system'", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addSystemMessage("system notice");
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].isUser).toBe(false);
    expect(result.current.messages[0].messageType).toBe("system");
  });

  it("clearMessages empties the array", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addUserMessage("one");
      result.current.addAssistantMessage("two");
    });
    expect(result.current.messages).toHaveLength(2);

    act(() => {
      result.current.clearMessages();
    });
    expect(result.current.messages).toHaveLength(0);
  });
});

describe("useMessages — hydrateFromTurns", () => {
  it("replaces messages with paired user+assistant entries per turn", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.addUserMessage("stale");
    });

    act(() => {
      result.current.hydrateFromTurns([
        { bundle_id: 1, user_query: "first", reply: "one", mode: "x" },
        { bundle_id: 2, user_query: "second", reply: "two", mode: "x" },
      ]);
    });

    expect(result.current.messages).toHaveLength(4);
    expect(result.current.messages[0].isUser).toBe(true);
    expect(result.current.messages[0].content).toBe("first");
    expect(result.current.messages[1].isUser).toBe(false);
    expect(result.current.messages[1].content).toBe("one");
    expect(result.current.messages[1].bundleId).toBe(1);
    expect(result.current.messages[2].content).toBe("second");
    expect(result.current.messages[3].bundleId).toBe(2);
  });

  it("hydrate of empty turns clears messages", () => {
    const { result } = renderHook(() => useMessages());
    act(() => {
      result.current.addUserMessage("stale");
    });
    act(() => {
      result.current.hydrateFromTurns([]);
    });
    expect(result.current.messages).toHaveLength(0);
  });

  it("hydrateFromTurns: rebuilds Search Details for NExtSEEK-engine turns", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.hydrateFromTurns([
        {
          bundle_id: 1,
          user_query: "Find all NHP samples in IMPAcTb",
          reply: "A total of 704...",
          mode: "graph_query",
          ts: "2026-09-02T13:09:30Z",
          debug_entries: [
            { agent: "parser", summary: "graph_query" },
            { agent: "neo4j", summary: "704 rows" },
          ],
        },
      ]);
    });

    const assistant = result.current.messages[1];
    expect(assistant.debugEntries).toHaveLength(2);
    expect(assistant.debugEntries![0].agent).toBe("parser");
    expect(assistant.debugEntries![1].summary).toBe("704 rows");
  });

  it("hydrateFromTurns: stamps rebuilt entries with the turn timestamp", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.hydrateFromTurns([
        {
          bundle_id: 1,
          user_query: "q",
          reply: "r",
          mode: "graph_query",
          ts: "2026-09-02T13:09:30Z",
          debug_entries: [{ agent: "parser", summary: "graph_query" }],
        },
      ]);
    });

    const entry = result.current.messages[1].debugEntries![0];
    expect(entry.timestamp).toBeInstanceOf(Date);
    expect(entry.timestamp.toISOString()).toBe("2026-09-02T13:09:30.000Z");
  });

  it("hydrateFromTurns: a turn with no debug_entries yields an empty panel, not a crash", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.hydrateFromTurns([
        { bundle_id: 1, user_query: "q", reply: "r", mode: "cc" },
      ]);
    });

    expect(result.current.messages[1].debugEntries).toEqual([]);
  });

  it("hydrateFromTurns: every message has a unique React key, even with duplicate bundle_ids", () => {
    const { result } = renderHook(() => useMessages());

    // System Q-style session: 1 wizard turn (bundle_id=0) + 1 reporter turn (bundle_id=2)
    act(() => {
      result.current.hydrateFromTurns([
        { bundle_id: 0, user_query: "What can NExtSEEK do?", reply: "Long help text", mode: "system", ts: null },
        { bundle_id: 2, user_query: "Whats the NIH Reporter link", reply: "MetNet investigation...", mode: "reporter", ts: null },
      ]);
    });
    expect(result.current.messages).toHaveLength(4);

    // NFCORE-style session: 1 new_search + 9 wizard (all bundle_id=0) + 1 reporter — keys would collide before the fix
    act(() => {
      result.current.hydrateFromTurns([
        { bundle_id: 1, user_query: "Find me ndma treated mice", reply: "195 records", mode: "new_search", ts: null },
        { bundle_id: 0, user_query: "lets make an nfcore samplesheet", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 0, user_query: "rnaseq", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 0, user_query: "What kind of metadata", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 0, user_query: "What are the unique values", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 0, user_query: "I'd like 2 mice", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 0, user_query: "Yes, lock it in. Whats next?", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 0, user_query: "Cohorts should be dose info", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 0, user_query: "Yes, lock it in", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 0, user_query: "build it", reply: "...", mode: "nfcore_wizard", ts: null },
        { bundle_id: 2, user_query: "Confirm", reply: "Confirmed!", mode: "reporter", ts: null },
      ]);
    });
    expect(result.current.messages).toHaveLength(22);

    const ids = result.current.messages.map((m) => m.id);
    const uniqueIds = new Set(ids);
    expect(uniqueIds.size).toBe(ids.length);
  });

  it("hydrateFromTurns: artifacts from a Turn are attached to the assistant message", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.hydrateFromTurns([
        {
          bundle_id: 7,
          user_query: "Find me monkeys",
          reply: "Found 3 monkeys.",
          mode: "new_search",
          ts: null,
          artifacts: [
            { artifact_type: "table", key: "samples", label: "Samples", columns: ["UID"], data: [{ UID: "MUS-1" }] },
          ],
        },
      ]);
    });

    expect(result.current.messages).toHaveLength(2);
    const assistant = result.current.messages[1];
    expect(assistant.isUser).toBe(false);
    expect(assistant.artifacts).toBeDefined();
    expect(assistant.artifacts).toHaveLength(1);
    expect(assistant.artifacts![0]).toMatchObject({ artifact_type: "table", key: "samples" });
  });

  it("hydrateFromTurns maps cc_traces and mode onto assistant messages", () => {
    const { result } = renderHook(() => useMessages());

    act(() => {
      result.current.hydrateFromTurns([
        {
          bundle_id: 0,
          user_query: "analyze",
          reply: "Done.",
          mode: "cc",
          cc_traces: [
            {
              schema_version: "3/trace-v1",
              cc_session_id: "s1",
              ts: "t",
              transcript_line_count: 1,
              turn_count: 1,
              steps: [],
              tools_used: {},
              files_created: [],
              files_modified: [],
            },
          ],
        },
      ]);
    });

    const assistant = result.current.messages[1];
    expect(assistant.mode).toBe("cc");
    expect(assistant.ccTraces).toHaveLength(1);
    expect(assistant.ccTraces![0].cc_session_id).toBe("s1");
  });
});
