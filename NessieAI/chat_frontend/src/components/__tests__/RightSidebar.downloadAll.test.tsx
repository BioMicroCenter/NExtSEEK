/**
 * The Debug Output panel's "All files" button: the whole chat as one zip.
 *
 * It keys on the chat on screen, never on the Debug panel's bundle. The panel
 * is anchored to the newest turn (debugForTurns), so a chat whose newest turn
 * wrote no bundle, or a CC-only chat, has no bundle id at all while its older
 * turns still hold files. Gating on the bundle would leave that chat with a
 * dead button.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RightSidebar } from "@/components/Layout/RightSidebar";
import type { DebugData } from "@/lib/types/chat";

const noBundle: DebugData = { entries: [], bundleId: null, query: "" };

describe("download all files of a chat", () => {
  it("is live on an open chat whose panel has no bundle", () => {
    const onDownloadAll = vi.fn();
    render(
      <RightSidebar isOpen onOpenChange={() => {}} debugData={noBundle}
        onDownload={() => {}} activeSessionId="sess-1" onDownloadAll={onDownloadAll} />,
    );

    const button = screen.getByTestId("session-download");
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
});
