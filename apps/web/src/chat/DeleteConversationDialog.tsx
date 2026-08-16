interface DeleteConversationDialogProps {
  readonly open: boolean;
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
}

export function DeleteConversationDialog({
  onCancel,
  onConfirm,
  open,
}: DeleteConversationDialogProps) {
  if (!open) return null;
  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        aria-labelledby="delete-dialog-title"
        aria-modal="true"
        className="dialog-card"
        role="dialog"
      >
        <h2 id="delete-dialog-title">删除这段会话？</h2>
        <p>会话会从列表中移除。该操作真实调用后端删除接口，不只是在浏览器里隐藏。</p>
        <div className="dialog-actions">
          <button onClick={onCancel} type="button">
            取消
          </button>
          <button className="danger-button" onClick={onConfirm} type="button">
            确认删除
          </button>
        </div>
      </section>
    </div>
  );
}
