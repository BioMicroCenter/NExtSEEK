// Admin-only, sticky override for the top-level BAML router. The value is
// persisted in localStorage so a pinned route survives reloads (useful for
// demos), read by the shell at send time, and posted as `force_route` on the
// query. The server re-checks admin and ignores it for non-admins.

export type ForceRoute = "auto" | "ns" | "cc";

const KEY = "nextseek.forceRoute";

// This tab's own last choice. It wins over storage so the send carries what the dropdown shows, also in
// a browser that will not store the pin (site data blocked, quota, private mode). Storage only seeds a
// tab that has not chosen yet, which is what makes a pin sticky across reloads and tabs.
let held: ForceRoute | null = null;

export function getForceRoute(): ForceRoute {
  if (held) return held;
  try {
    const v = localStorage.getItem(KEY);
    if (v === "ns" || v === "cc") return v;
  } catch {
    /* unreadable: nothing chosen in this tab, so auto */
  }
  return "auto";
}

export function setForceRoute(v: ForceRoute): void {
  held = v;
  try {
    localStorage.setItem(KEY, v);
  } catch {
    /* storage refused: the pin holds for this tab, it just is not sticky */
  }
}
