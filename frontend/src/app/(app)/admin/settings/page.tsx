"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useFetch } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import { Alert, Button, Card, PageHeader, SelectField, StatusBadge, TextField, useToast } from "@/components/ui";
import { useRun } from "@/components/actions";

/* eslint-disable @typescript-eslint/no-explicit-any */
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export default function SettingsPage() {
  const toast = useToast();
  const run = useRun();
  const cfg = useFetch<Record<string, any>>("/settings/billing/");
  const backups = useFetch<any[]>("/system/backups/");
  const [f, setF] = useState({ fee: "0", mode: "included", month: "7" });
  useEffect(() => { if (cfg.data) setF({ fee: String(cfg.data.installation_fee_pkr), mode: cfg.data.installation_fee_mode, month: String(cfg.data.financial_year_start_month) }); }, [cfg.data]);
  const save = async () => { if (await run(() => api("/settings/billing/", { method: "PATCH", body: { installation_fee_pkr: f.fee, installation_fee_mode: f.mode, financial_year_start_month: Number(f.month) } }), "Settings saved and logged.")) cfg.reload(); };
  const backup = async () => { try { await api("/system/backups/", { method: "POST" }); toast("success", "Backup requested. It runs in GitHub Actions; check the workflow for the result."); } catch (e: any) { toast("error", e.message); } backups.reload(); };

  return (
    <>
      <PageHeader title="Settings" subtitle="Owner-level configuration. Every change is written to the audit log." />
      {cfg.error && <Alert>{cfg.error.message}</Alert>}
      <div className="max-w-2xl space-y-5">
        <Card title="Billing">
          <div className="space-y-4">
            <TextField label="Installation fee (PKR)" value={f.fee} onChange={(e) => setF({ ...f, fee: e.target.value })} hint="Applies to new connections; a fee can be waived per connection with a reason." />
            <SelectField label="How the fee is billed" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}>
              <option value="included">Included in the first bill (one invoice)</option><option value="separate">Separate invoice, paid before the first term is invoiced</option></SelectField>
            <SelectField label="Financial year starts in" value={f.month} onChange={(e) => setF({ ...f, month: e.target.value })}>{MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}</SelectField>
            <Button onClick={save}>Save settings</Button>
          </div>
        </Card>
        <Card title="Database backup" actions={<Button variant="secondary" onClick={backup}>Back up now</Button>}>
          <p className="mb-3 text-sm text-slate-600">Backups run daily in GitHub Actions and are stored encrypted outside this app. “Back up now” asks GitHub to run one immediately (needs the server to be configured for it).</p>
          {backups.data?.length === 0 ? <p className="text-sm text-slate-500">No manual backups requested yet.</p> : (
            <ul className="divide-y divide-slate-100 text-sm">{backups.data?.map((b) => <li key={b.id} className="flex justify-between py-2"><span>{formatDateTime(b.requested_at)} · {b.requested_by}</span><span className="flex items-center gap-2"><span className="text-xs text-slate-500">{b.detail}</span><StatusBadge status={b.status === "requested" ? "active" : "rejected"} /></span></li>)}</ul>)}
        </Card>
      </div>
    </>
  );
}
