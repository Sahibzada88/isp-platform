"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, ensureCsrf, pdfUrl } from "@/lib/api";
import { formatDate, formatDateTime, formatPKR, humanize } from "@/lib/format";
import { ActionDialog } from "@/components/actions";
import { Alert, Button, Card, EmptyState, Spinner, StatusBadge, SelectField, TextAreaField, TextField } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;
type Tab = "connections" | "invoices" | "receipts" | "complaints";

export default function PortalPage() {
  const [me, setMe] = useState<R | null | undefined>(undefined);
  const refresh = useCallback(async () => { try { setMe(await api<R>("/portal/auth/me/")); } catch { setMe(null); } }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  if (me === undefined) return <div className="flex h-screen items-center justify-center"><Spinner className="h-8 w-8 text-brand-600" /></div>;
  if (me === null) return <Login onDone={refresh} />;
  if (me.must_change_password) return <ChangePassword onDone={refresh} />;
  return <Home me={me} onLogout={async () => { await api("/portal/auth/logout/", { method: "POST" }); setMe(null); }} />;
}

function Login({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState(""); const [p, setP] = useState(""); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null);
    try { await ensureCsrf(); await api("/portal/auth/login/", { method: "POST", body: { username: u, password: p } }); onDone(); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "Could not reach the server."); } finally { setBusy(false); }
  }
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div><h1 className="text-xl font-semibold">Customer portal</h1><p className="text-sm text-slate-500">Check your connection, bills and receipts</p></div>
        {err && <Alert>{err}</Alert>}
        <TextField label="Username" autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} required />
        <TextField label="Password" type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} required />
        <Button type="submit" loading={busy} className="w-full">Sign in</Button>
        <p className="text-xs text-slate-500">Accounts are created by your internet office. Forgot your password? Ask the office to reset it.</p>
      </form>
    </main>
  );
}

function ChangePassword({ onDone }: { onDone: () => void }) {
  const [o, setO] = useState(""); const [n, setN] = useState(""); const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) { e.preventDefault(); setErr(null); try { await api("/portal/auth/change-password/", { method: "POST", body: { old_password: o, new_password: n } }); onDone(); } catch (x) { setErr(x instanceof ApiError ? x.message : "Failed."); } }
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="text-xl font-semibold">Choose a new password</h1><Alert kind="info">Your password was set by the office. Please choose your own to continue.</Alert>
        {err && <Alert>{err}</Alert>}
        <TextField label="Current password" type="password" value={o} onChange={(e) => setO(e.target.value)} required />
        <TextField label="New password" type="password" hint="At least 10 characters." value={n} onChange={(e) => setN(e.target.value)} required />
        <Button type="submit" className="w-full">Save password</Button>
      </form>
    </main>
  );
}

function Home({ me, onLogout }: { me: R; onLogout: () => void }) {
  const [tab, setTab] = useState<Tab>("connections");
  const [rows, setRows] = useState<R[] | null>(null); const [err, setErr] = useState<string | null>(null);
  const [dlg, setDlg] = useState(false); const [f, setF] = useState({ category: "internet_down", description: "", connection: "" });
  const [conns, setConns] = useState<R[]>([]);
  const load = useCallback(async (t: Tab) => { setRows(null); setErr(null); try { setRows(await api<R[]>(`/portal/${t}/`)); } catch (e) { setErr(e instanceof ApiError ? e.message : "Could not load."); } }, []);
  useEffect(() => { void load(tab); }, [tab, load]);
  useEffect(() => { api<R[]>("/portal/connections/").then(setConns).catch(() => {}); }, []);
  const tabs: [Tab, string][] = [["connections", "My connections"], ["invoices", "Bills"], ["receipts", "Receipts"], ["complaints", "Complaints"]];

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <div><div className="text-sm font-semibold text-brand-900">Customer portal</div><div className="text-xs text-slate-500">{me.name} · {me.customer_id}</div></div>
        <Button variant="secondary" onClick={onLogout}>Sign out</Button>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6">
        <div role="tablist" className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
          {tabs.map(([t, l]) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm font-medium ${tab === t ? "border-b-2 border-brand-600 text-brand-700" : "text-slate-600"}`}>{l}</button>)}
        </div>
        {err && <Alert>{err}</Alert>}
        {rows === null && !err && <Spinner className="h-6 w-6 text-brand-600" />}
        {rows && rows.length === 0 && <Card><EmptyState title="Nothing to show yet" /></Card>}
        <div className="space-y-3">
          {tab === "connections" && rows?.map((c) => (
            <Card key={c.connection_id} title={`${c.connection_id} · ${c.package_name} (${c.speed_mbps} Mbps)`} actions={<StatusBadge status={c.status} />}>
              <dl className="grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-xs uppercase text-slate-500">Valid until</dt><dd>{formatDateTime(c.expires_at)}</dd></div><div><dt className="text-xs uppercase text-slate-500">Address</dt><dd>{c.installation_address}</dd></div>
                {c.next_package_name && <div><dt className="text-xs uppercase text-slate-500">Next package</dt><dd>{c.next_package_name}</dd></div>}
                {c.charges.amount_due && <div><dt className="text-xs uppercase text-slate-500">{c.charges.source === "open_invoice" ? `Amount due (${c.charges.invoice})` : "Renewal price"}</dt><dd className="font-semibold">{formatPKR(c.charges.amount_due)}</dd></div>}</dl>
              <p className="mt-3 text-sm text-slate-700">{c.charges.instructions}</p>
            </Card>))}
          {tab === "invoices" && rows?.map((i) => (
            <Card key={i.number} title={i.number} actions={<StatusBadge status={i.status} />}><div className="flex items-center justify-between text-sm"><div>{i.package_name} · {i.connection}<div className="text-xs text-slate-500">{formatDateTime(i.issued_at)}</div></div>
              <div className="text-end"><div className="font-semibold">{formatPKR(i.total_amount)}</div><a className="text-brand-700 hover:underline" href={pdfUrl(`/portal/invoices/${i.number}/pdf/`)} target="_blank" rel="noreferrer">Download PDF</a></div></div></Card>))}
          {tab === "receipts" && rows?.map((r) => (
            <Card key={r.number} title={r.number} actions={<StatusBadge status={r.status} />}><div className="flex items-center justify-between text-sm"><div>{r.invoice} · {humanize(r.method)}<div className="text-xs text-slate-500">{formatDateTime(r.issued_at)}</div></div>
              <div className="text-end"><div className="font-semibold">{formatPKR(r.amount)}</div><a className="text-brand-700 hover:underline" href={pdfUrl(`/portal/receipts/${r.number}/pdf/`)} target="_blank" rel="noreferrer">Download PDF</a></div></div></Card>))}
          {tab === "complaints" && <><div><Button onClick={() => setDlg(true)}>Report a problem</Button></div>
            {rows?.map((c) => (
              <Card key={c.number} title={`${c.number} · ${humanize(c.category)}`} actions={<StatusBadge status={c.status} />}><p className="text-sm">{c.description}</p>
                {c.resolution_notes && <p className="mt-2 text-sm text-green-800">Resolution: {c.resolution_notes}</p>}
                <ol className="mt-2 space-y-0.5 text-xs text-slate-500">{c.updates.map((u: R, k: number) => <li key={k}>{formatDateTime(u.at)} — {humanize(u.status)}</li>)}</ol></Card>))}</>}
        </div>
      </main>
      <ActionDialog open={dlg} onClose={() => setDlg(false)} title="Report a problem" confirmLabel="Submit" confirmDisabled={!f.description.trim()}
        onConfirm={async () => { await api("/portal/complaints/", { method: "POST", body: { ...f } }); setDlg(false); setF({ category: "internet_down", description: "", connection: "" }); void load("complaints"); }}>
        <SelectField label="Connection" value={f.connection} onChange={(e) => setF({ ...f, connection: e.target.value })}><option value="">Not sure / general</option>{conns.map((c) => <option key={c.connection_id} value={c.connection_id}>{c.connection_id} · {c.installation_address}</option>)}</SelectField>
        <SelectField label="Problem" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{["internet_down", "slow_speed", "cable_damage", "router_issue", "payment_issue", "other"].map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</SelectField>
        <TextAreaField label="Tell us what is happening" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </ActionDialog>
    </div>
  );
}
