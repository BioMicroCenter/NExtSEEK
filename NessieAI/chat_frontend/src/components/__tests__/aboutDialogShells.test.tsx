import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ReactElement } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { EmbeddedApp } from "@/EmbeddedApp";
import { AppLayout } from "@/AppLayout";

// EmbeddedApp and AppLayout are hand-maintained twins, and they have drifted
// at real cost before (#38). The About page has to open from both: the
// embedded shell is the one Django ships, the standalone one is where the UI is
// developed. This renders each shell for real and clicks its toolbar button.

beforeEach(() => {
  // HeaderBar (AppLayout's toolbar) reads the colour-scheme preference on mount.
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () =>
        String(url).includes("/assistant/me/") ? { is_admin: false } : { total: 0, sessions: [] },
    })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const SHELLS: Array<[string, () => ReactElement]> = [
  ["EmbeddedApp", () => <EmbeddedApp />],
  ["AppLayout", () => <AppLayout credentialError={null} />],
];

describe.each(SHELLS)("%s mounts the About page", (_name, make) => {
  it("opens it from the toolbar", async () => {
    render(make());
    expect(screen.queryByRole("dialog", { name: "About Nessie" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "About Nessie" }));
    expect(await screen.findByRole("dialog", { name: "About Nessie" })).toBeInTheDocument();
  });
});
