interface DeleteConfirmModalProps {
  isOpen: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
}

export function DeleteConfirmModal({
  isOpen,
  title,
  description,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  onCancel,
  onConfirm,
}: DeleteConfirmModalProps) {
  if (!isOpen) return null;

  const handleConfirm = async () => {
    await onConfirm();
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/35 px-4" role="presentation">
      <div className="w-full max-w-md rounded-2xl border border-line bg-panel shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="delete-confirm-title">
        <div className="px-5 py-5">
          <h2 id="delete-confirm-title" className="text-[16px] font-semibold tracking-tight text-ink">
            {title}
          </h2>
          <p className="mt-2 text-[13px] leading-5 text-muted">{description}</p>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-4">
          <button className="btn sm" data-testid="delete-confirm-cancel" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            className="btn sm"
            data-testid="delete-confirm-delete"
            onClick={() => void handleConfirm()}
            style={{ backgroundColor: "var(--danger)", color: "#fff", borderColor: "var(--danger)" }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
