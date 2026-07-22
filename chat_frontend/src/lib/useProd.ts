// Admin-only, sticky toggle for querying the PROD database instead of DEV. The
// value is persisted in localStorage so it survives reloads, read by the shell
// at send time, and posted as `use_prod` on the query. The server re-checks
// admin and ignores it for non-admins. Lives in the Debug panel next to the
// router override (both are admin, sticky, and read at send time).

const KEY = "nextseek.useProd";

export function getUseProd(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function setUseProd(v: boolean): void {
  try {
    localStorage.setItem(KEY, v ? "1" : "0");
  } catch {
    /* private mode / storage disabled — non-fatal, just not sticky */
  }
}
