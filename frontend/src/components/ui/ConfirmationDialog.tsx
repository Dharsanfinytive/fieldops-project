import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

interface ConfirmationDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  busyLabel?: string;
  busy?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export default function ConfirmationDialog({
  open,
  title,
  message,
  confirmLabel = "Delete",
  busyLabel = "Deleting…",
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmationDialogProps) {
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 5000,
        display: "grid",
        placeItems: "center",
        padding: 20,
        background: "rgba(15, 23, 42, 0.48)",
        backdropFilter: "blur(3px)",
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirmation-dialog-title"
        aria-describedby="confirmation-dialog-message"
        style={{
          width: "min(100%, 420px)",
          padding: 24,
          border: "1px solid #e2e8f0",
          borderRadius: 18,
          background: "#fff",
          boxShadow: "0 24px 70px rgba(15, 23, 42, 0.26)",
          fontFamily: "inherit",
        }}
      >
        <div
          style={{
            width: 44,
            height: 44,
            display: "grid",
            placeItems: "center",
            marginBottom: 15,
            borderRadius: 13,
            background: "#fff1f2",
            color: "#dc2626",
          }}
        >
          <AlertTriangle size={22} />
        </div>
        <h2
          id="confirmation-dialog-title"
          style={{ margin: "0 0 8px", color: "#172033", fontSize: 18, fontWeight: 750 }}
        >
          {title}
        </h2>
        <p
          id="confirmation-dialog-message"
          style={{ margin: 0, color: "#64748b", fontSize: 14, lineHeight: 1.55 }}
        >
          {message}
        </p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 24 }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            autoFocus
            style={{
              minHeight: 40,
              padding: "0 16px",
              border: "1px solid #dbe3eb",
              borderRadius: 9,
              background: "#fff",
              color: "#475569",
              fontSize: 13,
              fontWeight: 700,
              cursor: busy ? "not-allowed" : "pointer",
              opacity: busy ? 0.6 : 1,
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void onConfirm()}
            disabled={busy}
            style={{
              minHeight: 40,
              minWidth: 92,
              padding: "0 16px",
              border: 0,
              borderRadius: 9,
              background: "#dc2626",
              color: "#fff",
              fontSize: 13,
              fontWeight: 750,
              cursor: busy ? "not-allowed" : "pointer",
              opacity: busy ? 0.75 : 1,
            }}
          >
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
