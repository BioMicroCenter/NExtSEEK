import { useState } from "react";
import { getUseProd, setUseProd } from "@/lib/useProd";

interface ProdToggleProps {
  isAdmin?: boolean;
}

/**
 * Admin-only sticky toggle: query the PROD database instead of DEV. Persists to
 * localStorage; the shell reads it at send time and posts `use_prod`. Renders
 * nothing for non-admins (and the server ignores the field for them anyway).
 * Lives in the Debug panel beside the router override.
 */
export function ProdToggle({ isAdmin = false }: ProdToggleProps) {
  const [useProd, setUseProdState] = useState<boolean>(() => getUseProd());
  if (!isAdmin) return null;

  return (
    <div className="flex items-center gap-2 px-4 py-2 text-sm">
      <label htmlFor="prod-toggle" className="text-muted-foreground">
        Database
      </label>
      <label className="flex items-center gap-1 cursor-pointer select-none">
        <input
          id="prod-toggle"
          type="checkbox"
          checked={useProd}
          onChange={(e) => {
            const next = e.target.checked;
            setUseProdState(next);
            setUseProd(next);
          }}
          aria-label="Query PROD database"
        />
        <span
          className={
            useProd
              ? "font-medium text-amber-600 dark:text-amber-400"
              : "text-muted-foreground"
          }
        >
          {useProd ? "PROD" : "DEV"}
        </span>
      </label>
    </div>
  );
}
