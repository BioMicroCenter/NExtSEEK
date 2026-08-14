import type { DebugEntry } from "./types/chat";
import type { RouteDecidedData, CcTurnMetaData, QueryErrorData } from "./types/api";

// Formatters that turn progress events into Debug-panel entries (#4: surface the
// top-level router decision and Container-CC turn metadata that were previously
// backend-only / silently dropped). Pure + unit-tested.

export function makeDebugEntry(agent: string, summary: string): DebugEntry {
  return { agent, summary, timestamp: new Date() };
}

export function routeDecidedSummary(d: RouteDecidedData): string {
  const parts = [`→ ${d.route}`];
  if (d.model_class) parts.push(`model_class=${d.model_class}`);
  if (d.source) parts.push(`source=${d.source}`);
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
  return parts.join("  ·  ");
}
