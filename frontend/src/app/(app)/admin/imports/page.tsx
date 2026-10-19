"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Alert, Button, Card, PageHeader, SelectField } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const HINT: Record<string, string> = {
  packages: "code,name,speed_mbps,price_pkr,validity_days",
  customers: "legacy_customer_id,full_name,phone,village_code,status,package_code  (+ optional: legacy_connection_id, cnic, address, office_code, installation_address, expiry_date, registration_date, outstanding_balance, balance_verified, notes)",
};

export default function ImportsPage() {
  const [kind, setKind] = useState("customers");
  const [text, setText] = useState(""); const [name, setName] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [report, setReport] = useState<any>(null);

  async function onFile(file: File | undefined) { if (!file) return; setName(file.name); setText(await file.text()); setReport(null); }
  async function run(dry: boolean) {
    setBusy(true); setError(null);
    try { setReport(await api("/imports/", { method: "POST", body: { kind, csv: text, filename: name, dry_run: dry } })); }
    catch (e) { setReport(null); setError(e instanceof ApiError ? e.message : "Import failed."); } finally { setBusy(false); }
  }
  const stat = (l: string, v: any, warn = false) => <div className={`rounded-md border px-3 py-2 ${warn ? "border-amber-200 bg-amber-50" : "border-slate-200"}`}><div className="text-xs uppercase text-slate-500">{l}</div><div className="text-lg font-semibold">{v}</div></div>;

  return (
    <>
      <PageHeader title="Data import" subtitle="Always run a dry run first. Re-running the same file is safe: nothing is duplicated, merged or overwritten." />
      <Card className="mb-4">
        <div className="space-y-3">
          <SelectField label="What are you importing?" value={kind} onChange={(e) => { setKind(e.target.value); setReport(null); }}><option value="customers">Customers, connections and opening balances</option><option value="packages">Packages</option></SelectField>
          <p className="text-xs text-slate-500">Columns: <code>{HINT[kind]}</code>. Dates as YYYY-MM-DD. At most 3,000 rows per file.</p>
          <input type="file" accept=".csv,text/csv" onChange={(e) => void onFile(e.target.files?.[0])} aria-label="CSV file" />
          <div className="flex gap-2"><Button variant="secondary" disabled={!text} loading={busy} onClick={() => run(true)}>Dry run (nothing is saved)</Button>
            <Button disabled={!text || !report?.dry_run} loading={busy} onClick={() => run(false)}>Import for real</Button></div>
          {report && !report.dry_run && <Alert kind="success">Imported as batch #{report.batch_id}.</Alert>}
          {error && <Alert>{error}</Alert>}
        </div>
      </Card>
      {report && (
        <Card title={report.dry_run ? "Dry-run result (not saved)" : "Import result"}>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {stat("Rows in file", report.source_rows)}{stat("Imported", report.imported_rows)}{stat("Rejected", report.rejected_rows, report.rejected_rows > 0)}
            {stat("Already imported", report.skipped_already_imported)}{stat("Conflicts", report.duplicate_or_conflict_rows, report.duplicate_or_conflict_rows > 0)}{stat("Need review", report.rows_needing_manual_review, report.rows_needing_manual_review > 0)}
          </div>
          <p className="mt-3 text-sm">Opening balances — verified: <b>PKR {report.opening_balance_verified}</b>, unverified: <b>PKR {report.opening_balance_unverified}</b>. Counts {report.reconciles ? "reconcile with the file." : "DO NOT reconcile; do not go live."}</p>
          {report.issues.length > 0 && (
            <div className="mt-4 overflow-x-auto"><table className="min-w-full text-start text-sm"><thead className="text-xs uppercase text-slate-500"><tr><th className="py-1 pr-4">Row</th><th className="pr-4">Level</th><th className="pr-4">Code</th><th>Message</th></tr></thead>
              <tbody>{report.issues.map((i: any, k: number) => <tr key={k} className="border-t border-slate-100"><td className="py-1 pr-4">{i.row}</td><td className="pr-4">{i.level}</td><td className="pr-4 font-mono text-xs">{i.code}</td><td>{i.message}</td></tr>)}</tbody></table>
              {report.issues_truncated && <p className="mt-2 text-xs text-slate-500">Showing the first 500 issues.</p>}</div>)}
        </Card>
      )}
    </>
  );
}
