// Admin-only, sticky per-turn wall-clock override for Container-CC turns. The
// value (seconds) is persisted in localStorage, read by the shell at send time,
// and posted as `max_turn_length_s`. The server admin-gates it and clamps to
// [30, NEXTSEEK_CC_TIMEOUT_HARD_MAX]. null = no override (server default). Lives
// in the Debug panel beside the router override and PROD toggle.

const KEY = "nextseek.maxTurnLength";

export function getMaxTurnLength(): number | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

export function setMaxTurnLength(v: number | null): void {
  try {
    if (v && v > 0) {
      localStorage.setItem(KEY, String(Math.floor(v)));
    } else {
      localStorage.removeItem(KEY);
    }
  } catch {
    /* private mode / storage disabled — non-fatal, just not sticky */
  }
}
