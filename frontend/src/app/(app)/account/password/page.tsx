"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Button, Card, PageHeader, TextField, useToast } from "@/components/ui";

export default function ChangePasswordPage() {
  const { user, refresh } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [oldPassword, setOld] = useState("");
  const [newPassword, setNew] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null); setFields({});
    if (newPassword !== confirm) { setFields({ confirm: "Passwords do not match." }); return; }
    setBusy(true);
    try {
      await api("/auth/change-password/", { method: "POST", body: { old_password: oldPassword, new_password: newPassword } });
      toast("success", "Password changed.");
      await refresh();
      router.replace("/customers");
    } catch (err) {
      if (err instanceof ApiError) { setFields(err.fieldErrors); setError(Object.keys(err.fieldErrors).length ? null : err.message); }
      else setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-md">
      <PageHeader title="Change password" />
      <Card>
        <form onSubmit={onSubmit} className="space-y-4">
          {user?.must_change_password && <Alert kind="info">Your password was reset by staff. Please choose a new one to continue.</Alert>}
          {error && <Alert>{error}</Alert>}
          <TextField label="Current password" type="password" autoComplete="current-password" value={oldPassword} onChange={(e) => setOld(e.target.value)} error={fields.old_password} required />
          <TextField label="New password" type="password" autoComplete="new-password" hint="At least 10 characters; not common or all digits." value={newPassword} onChange={(e) => setNew(e.target.value)} error={fields.new_password} required />
          <TextField label="Confirm new password" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} error={fields.confirm} required />
          <Button type="submit" loading={busy}>Change password</Button>
        </form>
      </Card>
    </div>
  );
}
