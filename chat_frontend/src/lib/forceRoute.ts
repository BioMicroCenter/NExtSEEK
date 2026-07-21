// Admin-only, sticky override for the top-level BAML router. The value is
// persisted in localStorage so a pinned route survives reloads (useful for
// demos), read by the shell at send time, and posted as `force_route` on the
// query. The server re-checks admin and ignores it for non-admins.

export type ForceRoute = "auto" | "ns" | "cc";

const KEY = "nextseek.forceRoute";

export function getForceRoute(): ForceRoute {
  try {
    const v = localStorage.getItem(KEY);
    return v === "ns" || v === "cc" ? v : "auto";
  } catch {
    return "auto";
  }
}

export function setForceRoute(v: ForceRoute): void {
  try {
    localStorage.setItem(KEY, v);
  } catch {
    /* private mode / storage disabled — non-fatal, just not sticky */
  }
}
