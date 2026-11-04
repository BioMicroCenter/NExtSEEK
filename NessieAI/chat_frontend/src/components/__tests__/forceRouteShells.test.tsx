import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ReactElement } from "react";
import { act, render, screen, fireEvent } from "@testing-library/react";
import { EmbeddedApp } from "@/EmbeddedApp";
import { AppLayout } from "@/AppLayout";
import { NextseekApiService } from "@/lib/services/chatApi";
import { setForceRoute } from "@/lib/forceRoute";

// The Debug panel's Router dropdown, driven for real in both shells: what the admin
// picks there must be what submitQuery is handed at send time (and so what goes out as
// `force_route`). submitQuery is replaced, so nothing here touches a socket or the network.

let submit: ReturnType<typeof vi.spyOn>;

function stubFetch(isAdmin: boolean) {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () =>
        String(url).includes("/assistant/me/")
          ? { username: "u", is_admin: isAdmin }
          : { total: 0, sessions: [] },
    })),
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  localStorage.clear();
  submit = vi
    .spyOn(NextseekApiService.prototype, "submitQuery")
    .mockImplementation(() => new Promise<void>(() => {}));
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setForceRoute("auto");
  localStorage.clear();
});

// AppLayout takes its admin flag as a prop; EmbeddedApp asks /assistant/me/ itself.
const SHELLS: Array<[string, (isAdmin: boolean) => ReactElement]> = [
  ["EmbeddedApp", () => <EmbeddedApp />],
  ["AppLayout", (isAdmin) => <AppLayout credentialError={null} isAdmin={isAdmin} />],
];

async function pick(...values: string[]) {
  fireEvent.click(screen.getByLabelText("Toggle debug panel"));
  const select = await screen.findByLabelText("Router");
  for (const value of values) fireEvent.change(select, { target: { value } });
}

function send(text: string) {
  fireEvent.change(screen.getByTestId("chat-input"), { target: { value: text } });
  fireEvent.click(screen.getByTestId("send-button"));
}

describe.each(SHELLS)("%s: the Router dropdown for an admin", (_name, make) => {
  it.each(["ns", "cc"])("sends %s on the turn after it is picked", async (value) => {
    stubFetch(true);
    render(make(true));
    await pick(value);
    send("How many samples?");
    await vi.waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][2]).toMatchObject({ forceRoute: value });
  });

  it("sends auto again once it is put back", async () => {
    stubFetch(true);
    render(make(true));
    await pick("cc", "auto");
    send("How many samples?");
    await vi.waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][2]).toMatchObject({ forceRoute: "auto" });
  });

  // The Debug panel says whether the server took the pin: a pin the server did not apply
  // (it ignored the field, or ran a different route) shows on the router entry, closed.
  it.each([
    ["applied", { route: "container_cc", source: "forced" }, "pinned CC: applied"],
    ["ignored", { route: "nextseek_query", source: "baml" }, "pinned CC: NOT applied"],
  ])("says on the router entry that the pin was %s", async (_case, decided, line) => {
    stubFetch(true);
    render(make(true));
    await pick("cc");
    send("How many samples?");
    await vi.waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    const onProgress = submit.mock.calls[0][3];
    act(() => onProgress({ event: "route_decided", data: decided }));
    expect(await screen.findByTestId("debug-entry")).toHaveTextContent(line);
  });

  // forceRoute.ts says a browser that will not store the pin only loses stickiness. It must not lose the pin:
  // the dropdown says "pinned: CC", so the next turn has to go to CC.
  it("still sends the pin when the browser will not store it", async () => {
    stubFetch(true);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    render(make(true));
    await pick("cc");
    expect(await screen.findByText("pinned: CC")).toBeInTheDocument();
    send("How many samples?");
    await vi.waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][2]).toMatchObject({ forceRoute: "cc" });
  });

  // The reviewer's case: storage still holds an earlier "cc", will not take the new choice, and the admin
  // puts the dropdown back to auto. The dropdown says auto, so the send must say auto.
  it("sends what the dropdown shows when storage holds an older pin it will not replace", async () => {
    stubFetch(true);
    localStorage.setItem("nextseek.forceRoute", "cc");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    render(make(true));
    await pick("auto");
    send("How many samples?");
    await vi.waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][2]).toMatchObject({ forceRoute: "auto" });
  });

  it("is not shown to a member, and a stored pin is not sent for one", async () => {
    stubFetch(false);
    setForceRoute("cc");
    render(make(false));
    fireEvent.click(screen.getByLabelText("Toggle debug panel"));
    expect(screen.queryByLabelText("Router")).toBeNull();
    send("How many samples?");
    await vi.waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][2]).toMatchObject({ forceRoute: "auto" });
  });
});
