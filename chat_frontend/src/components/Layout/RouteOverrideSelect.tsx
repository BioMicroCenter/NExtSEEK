import { useState } from "react";
import { getForceRoute, setForceRoute, type ForceRoute } from "@/lib/forceRoute";

interface RouteOverrideSelectProps {
  isAdmin?: boolean;
}

/**
 * Admin-only sticky control that supersedes the top-level BAML router:
 * BAML (auto) · C_NS (force core chat_nextseek) · CC (force Container-Claude).
 * Persists to localStorage; the shell reads it at send time and posts
 * `force_route`. Renders nothing for non-admins (and the server ignores the
 * field for them regardless).
 */
export function RouteOverrideSelect({ isAdmin = false }: RouteOverrideSelectProps) {
  const [route, setRoute] = useState<ForceRoute>(() => getForceRoute());
  if (!isAdmin) return null;

  const pinned = route !== "auto";
  return (
    <div className="flex items-center gap-2 px-4 py-2 text-sm">
      <label htmlFor="route-override" className="text-muted-foreground">
        Router
      </label>
      <select
        id="route-override"
        value={route}
        onChange={(e) => {
          const next = e.target.value as ForceRoute;
          setRoute(next);
          setForceRoute(next);
        }}
        className={`rounded border bg-background px-2 py-1 text-sm ${
          pinned
            ? "border-amber-500 text-amber-600 dark:text-amber-400"
            : "border-input"
        }`}
      >
        <option value="auto">BAML (auto)</option>
        <option value="ns">C_NS</option>
        <option value="cc">CC</option>
      </select>
      {pinned ? (
        <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
          pinned: {route === "ns" ? "C_NS" : "CC"}
        </span>
      ) : null}
    </div>
  );
}
