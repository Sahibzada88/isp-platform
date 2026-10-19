"use client";

import { useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { Alert, Button, Modal, TextAreaField, useToast } from "./ui";

/** Run a mutation, toast the outcome. Returns true on success. */
export function useRun() {
  const toast = useToast();
  return async (fn: () => Promise<unknown>, success: string): Promise<boolean> => {
    try {
      await fn();
      toast("success", success);
      return true;
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
      return false;
    }
  };
}

export interface ActionDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  /** When set, a reason box is shown and required. */
  reasonLabel?: string;
  children?: ReactNode;
  onConfirm: (reason: string) => Promise<void>;
  confirmDisabled?: boolean;
}

/** Confirmation dialog for financial / destructive actions; shows API errors inline. */
export function ActionDialog({ open, onClose, title, description, confirmLabel, danger, reasonLabel, children, onConfirm, confirmDisabled }: ActionDialogProps) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => { setReason(""); setError(null); onClose(); };
  const submit = async () => {
    if (reasonLabel && !reason.trim()) { setError("A reason is required."); return; }
    setBusy(true); setError(null);
    try {
      await onConfirm(reason.trim());
      setReason("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title={title}
      footer={<>
        <Button variant="secondary" onClick={close} disabled={busy}>Cancel</Button>
        <Button variant={danger ? "danger" : "primary"} onClick={submit} loading={busy} disabled={confirmDisabled}>{confirmLabel}</Button>
      </>}
    >
      {description && <p className="text-sm text-slate-600">{description}</p>}
      {children}
      {reasonLabel && <TextAreaField label={reasonLabel} value={reason} onChange={(e) => setReason(e.target.value)} />}
      {error && <Alert>{error}</Alert>}
    </Modal>
  );
}
