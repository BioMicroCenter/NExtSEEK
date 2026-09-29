/**
 * The Debug Output panel's "All files" button: the whole chat as one zip.
 *
 * It keys on the chat on screen, never on the Debug panel's bundle. The panel
 * is anchored to the newest turn (debugForTurns), so a chat whose newest turn
 * wrote no bundle, or a CC-only chat, has no bundle id at all while its older
 * turns still hold files. Gating on the bundle would leave that chat with a
 * dead button.
 *
 * One click is one download: the button stays disabled while the download is
 * being handed over, and for a moment after, since a link hand-off returns at
 * once while the server is still planning the zip.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DOWNLOAD_ALL_HOLD_MS, RightSidebar } from "@/components/Layout/RightSidebar";
import type { DebugData } from "@/lib/types/chat";

const noBundle: DebugData = { entries: [], bundleId: null, query: "" };

function renderSidebar(onDownloadAll: () => Promise<void> | void) {
  render(
    <RightSidebar isOpen onOpenChange={() => {}} debugData={noBundle}
      onDownload={() => {}} activeSessionId="sess-1" onDownloadAll={onDownloadAll} />,
  );
  return screen.getByTestId("session-download");
}

describe("download all files of a chat", () => {
  afterEach(() => vi.useRealTimers());

  it("is live on an open chat whose panel has no bundle", () => {
    const onDownloadAll = vi.fn();
    const button = renderSidebar(onDownloadAll);

    expect(button).toBeEnabled();
    expect(screen.getByTestId("json-download")).toBeDisabled();

    fireEvent.click(button);
    expect(onDownloadAll).toHaveBeenCalledTimes(1);
  });

  it("is disabled before a chat exists", () => {
    render(
      <RightSidebar isOpen onOpenChange={() => {}} debugData={noBundle}
        onDownload={() => {}} activeSessionId={null} onDownloadAll={() => {}} />,
    );

    expect(screen.getByTestId("session-download")).toBeDisabled();
  });

  it("starts one download for a double click", () => {
    const onDownloadAll = vi.fn(() => new Promise<void>(() => {}));
    const button = renderSidebar(onDownloadAll);

    fireEvent.click(button);
    fireEvent.click(button);

    expect(onDownloadAll).toHaveBeenCalledTimes(1);
  });

  it("is disabled while the download is pending and comes back once it is handed over", async () => {
    vi.useFakeTimers();
    let finish!: () => void;
    const onDownloadAll = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const button = renderSidebar(onDownloadAll);

    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");

    // Still pending well past the hold: the fetch path lasts as long as the body does.
    await act(async () => { await vi.advanceTimersByTimeAsync(DOWNLOAD_ALL_HOLD_MS * 3); });
    expect(button).toBeDisabled();

    await act(async () => { finish(); await vi.advanceTimersByTimeAsync(0); });
    expect(button).toBeDisabled();

    await act(async () => { await vi.advanceTimersByTimeAsync(DOWNLOAD_ALL_HOLD_MS); });
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute("aria-busy", "true");

    fireEvent.click(button);
    expect(onDownloadAll).toHaveBeenCalledTimes(2);
  });

  it("comes back after a failed download too", async () => {
    vi.useFakeTimers();
    const button = renderSidebar(() => Promise.reject(new Error("403")));

    fireEvent.click(button);
    expect(button).toBeDisabled();

    await act(async () => { await vi.advanceTimersByTimeAsync(DOWNLOAD_ALL_HOLD_MS); });
    expect(button).toBeEnabled();
  });
});
