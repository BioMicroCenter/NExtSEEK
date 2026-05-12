import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SessionListItem } from "../SessionListItem";
import type { SessionListItem as SessionListItemModel } from "@/lib/types/api";

const item: SessionListItemModel = {
  session_id: "uuid-1",
  title: "Hello world",
  created_at: "2026-05-12T00:00:00Z",
  updated_at: "2026-05-12T00:00:00Z",
  query_count: 3,
  preview: "first query",
};

describe("SessionListItem", () => {
  it("renders title", () => {
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });

  it("calls onSelect when clicked", () => {
    const onSelect = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={onSelect} onRename={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByText("Hello world"));
    expect(onSelect).toHaveBeenCalledWith("uuid-1");
  });

  it("does not call onSelect when disabled", () => {
    const onSelect = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={true} onSelect={onSelect} onRename={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByText("Hello world"));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("rename: clicking Rename icon swaps to input, Enter saves", () => {
    const onRename = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={vi.fn()} onRename={onRename} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /^rename$/i }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Renamed" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onRename).toHaveBeenCalledWith("uuid-1", "Renamed");
  });

  it("rename: Escape cancels", () => {
    const onRename = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={vi.fn()} onRename={onRename} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /^rename$/i }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Cancelled" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onRename).not.toHaveBeenCalled();
    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });

  it("delete: clicking Delete icon then Confirm calls onDelete", () => {
    const onDelete = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={vi.fn()} onRename={vi.fn()} onDelete={onDelete} />);
    const deleteIcon = screen.getAllByRole("button", { name: /^delete$/i })[0];
    fireEvent.click(deleteIcon);
    const confirmButtons = screen.getAllByRole("button", { name: /^delete$/i });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    expect(onDelete).toHaveBeenCalledWith("uuid-1");
  });
});
