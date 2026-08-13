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

  it("makeDebugEntry sets agent, summary, and a timestamp", () => {
    const e = makeDebugEntry("router", "hi");
    expect(e.agent).toBe("router");
    expect(e.summary).toBe("hi");
    expect(e.timestamp).toBeInstanceOf(Date);
  });
});
