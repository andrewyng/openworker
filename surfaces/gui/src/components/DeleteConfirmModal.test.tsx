import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DeleteConfirmModal } from "./DeleteConfirmModal";

afterEach(cleanup);

describe("DeleteConfirmModal", () => {
  it("renders the warning copy and calls the supplied handlers", async () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn().mockResolvedValue(undefined);

    render(
      <DeleteConfirmModal
        isOpen
        title="Delete automation?"
        description="This action permanently removes the automation and it cannot be restored."
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText("Delete automation?")).toBeTruthy();
    expect(screen.getByText(/cannot be restored/i)).toBeTruthy();

    fireEvent.click(screen.getByTestId("delete-confirm-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("delete-confirm-delete"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when closed", () => {
    const { container } = render(
      <DeleteConfirmModal
        isOpen={false}
        title="Delete automation?"
        description="This action permanently removes the automation and it cannot be restored."
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(container.firstChild).toBeNull();
  });
});
