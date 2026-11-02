import type { DebugEntry } from "./types/chat";
import type { RouteDecidedData, CcTurnMetaData, QueryErrorData } from "./types/api";
import type { ForceRoute } from "./forceRoute";

// Formatters that turn progress events into Debug-panel entries (#4: surface the
// top-level router decision and Container-CC turn metadata that were previously
// backend-only / silently dropped). Pure + unit-tested.

export function makeDebugEntry(agent: string, summary: string): DebugEntry {
  return { agent, summary, timestamp: new Date() };
}

// What the Router dropdown's pins mean on the wire: the route the server must report, with source=forced.
const PINNED_ROUTE = { ns: "nextseek_query", cc: "container_cc" } as const;
const PIN_LABEL = { ns: "C_NS", cc: "CC" } as const;

/** `pinned` is what this turn was sent with: a pin the server did not apply is said on the first line. */
export function routeDecidedSummary(d: RouteDecidedData, pinned: ForceRoute = "auto"): string {
  const parts = [`→ ${d.route}`];
  if (d.model_class) parts.push(`model_class=${d.model_class}`);
  if (d.source) parts.push(`source=${d.source}`);
  if (pinned !== "auto") {
    const applied = d.source === "forced" && d.route === PINNED_ROUTE[pinned];
    parts.push(`pinned ${PIN_LABEL[pinned]}: ${applied ? "applied" : "NOT applied"}`);
  }
  const head = parts.join("  ·  ");
  return d.reasoning ? `${head}\n${d.reasoning}` : head;
}

export function ccTurnMetaSummary(d: CcTurnMetaData): string {
  const parts: string[] = [];
  if (d.model_id) parts.push(`model=${d.model_id}`);
  parts.push(`session=${d.cc_session_id || "new"}`);
  if (typeof d.budget_usd === "number") parts.push(`budget=$${d.budget_usd}`);
  if (typeof d.turn_timeout_s === "number") parts.push(`timeout=${d.turn_timeout_s}s`);
  return parts.join("  ·  ");
}

export function queryErrorSummary(d: QueryErrorData): string {
  const parts = [d.error];
  if (d.reason) parts.push(`reason=${d.reason}`);
  if (d.agent) parts.push(`agent=${d.agent}`);
  const head = parts.join("  ·  ");
  // The raw provider error: `error` is the user's plain text since the failure messages.
  return d.detail && d.detail !== d.error ? `${head}\ndetail=${d.detail}` : head;
}
