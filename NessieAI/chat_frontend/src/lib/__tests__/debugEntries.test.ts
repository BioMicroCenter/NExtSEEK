import { describe, it, expect } from "vitest";
import {
  routeDecidedSummary,
  ccTurnMetaSummary,
  queryErrorSummary,
  makeDebugEntry,
} from "@/lib/debugEntries";

describe("debugEntries formatters", () => {
  it("routeDecidedSummary shows route, model_class, source", () => {
    expect(
      routeDecidedSummary({ route: "container_cc", model_class: "sonnet", source: "router" }),
    ).toBe("→ container_cc  ·  model_class=sonnet  ·  source=router");
  });

  it("routeDecidedSummary appends reasoning on a new line", () => {
    const s = routeDecidedSummary({ route: "nextseek_query", reasoning: "structured lookup" });
    expect(s).toContain("→ nextseek_query");
    expect(s).toContain("\nstructured lookup");
  });

  it("routeDecidedSummary says a pin was applied when the server forced that route", () => {
    expect(routeDecidedSummary({ route: "container_cc", source: "forced" }, "cc")).toBe(
      "→ container_cc  ·  source=forced  ·  pinned CC: applied",
    );
    expect(routeDecidedSummary({ route: "nextseek_query", source: "forced" }, "ns")).toContain(
      "pinned C_NS: applied",
    );
  });

  it("routeDecidedSummary says so when a pin was not applied (router ran, or the wrong route)", () => {
    // The server ignored the field: the router decided.
    expect(routeDecidedSummary({ route: "nextseek_query", source: "baml" }, "cc")).toBe(
      "→ nextseek_query  ·  source=baml  ·  pinned CC: NOT applied",
    );
    // Forced, but not to what was asked.
    expect(routeDecidedSummary({ route: "container_cc", source: "forced" }, "ns")).toContain(
      "pinned C_NS: NOT applied",
    );
  });

  it("routeDecidedSummary is unchanged when nothing was pinned", () => {
    const d = { route: "nextseek_query", source: "baml" };
    expect(routeDecidedSummary(d, "auto")).toBe(routeDecidedSummary(d));
    expect(routeDecidedSummary(d, "auto")).not.toContain("pinned");
  });

  it("ccTurnMetaSummary shows model, session, budget, timeout", () => {
    expect(
      ccTurnMetaSummary({
        model_id: "us.anthropic.claude-x",
        cc_session_id: "sess-1",
        budget_usd: 2.5,
        turn_timeout_s: 180,
      }),
    ).toBe("model=us.anthropic.claude-x  ·  session=sess-1  ·  budget=$2.5  ·  timeout=180s");
  });

  it("ccTurnMetaSummary shows 'new' session when none", () => {
    expect(ccTurnMetaSummary({ model_id: "m", cc_session_id: null })).toContain("session=new");
  });

  it("queryErrorSummary includes reason + agent", () => {
    expect(
      queryErrorSummary({ error: "exceeded 180s", reason: "exec_timeout", agent: "container_cc" }),
    ).toBe("exceeded 180s  ·  reason=exec_timeout  ·  agent=container_cc");
  });

  it("queryErrorSummary shows the raw detail on its own line", () => {
    expect(
      queryErrorSummary({
        error: "The AI model was unavailable during this turn, so I could not finish your question.",
        reason: "model_unavailable",
        agent: "container_cc",
        detail: "API Error: 529 overloaded",
      }),
    ).toBe(
      "The AI model was unavailable during this turn, so I could not finish your question." +
        "  ·  reason=model_unavailable  ·  agent=container_cc\ndetail=API Error: 529 overloaded",
    );
  });

  it("queryErrorSummary leaves out an empty detail or one that repeats the error", () => {
    expect(queryErrorSummary({ error: "boom", agent: "parser", detail: "" })).toBe("boom  ·  agent=parser");
    expect(queryErrorSummary({ error: "boom", detail: "boom" })).toBe("boom");
  });

  it("makeDebugEntry sets agent, summary, and a timestamp", () => {
    const e = makeDebugEntry("router", "hi");
    expect(e.agent).toBe("router");
    expect(e.summary).toBe("hi");
    expect(e.timestamp).toBeInstanceOf(Date);
  });
});
