"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Office, Page } from "@/lib/types";
import { humanize } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { Alert, Button, Card, DataTable, PageHeader, SelectField, StatusBadge, TextField, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;
type Tab = "items" | "equipment" | "adjustments" | "returns" | "movements";
const CATS = ["router_onu", "wifi", "cable", "connector", "jointing", "pole_clamp", "install", "tools", "computer", "office", "other"];

export default function InventoryPage() {
  const { can } = useAuth();
  const run = useRun();
  const [tab, setTab] = useState<Tab>("items");
  const [low, setLow] = useState(false);
  const items = useFetch<Page<R>>("/inventory/items/", { low: low ? "true" : "" });
  const equip = useFetch<Page<R>>(tab === "equipment" ? "/inventory/equipment/" : null);
  const adj = useFetch<Page<R>>(tab === "adjustments" ? "/inventory/adjustments/" : null);
  const rets = useFetch<Page<R>>(tab === "returns" ? "/inventory/returns/" : null);
  const moves = useFetch<Page<R>>(tab === "movements" ? "/inventory/movements/" : null);
  const offices = useFetch<Page<Office>>("/offices/");
  const [dlg, setDlg] = useState<null | { k: "item" } | { k: "purchase" | "use" | "adjust" | "return"; item: R } | { k: "assign"; eq: R } | { k: "reverse"; id: number }>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const set = (k: string) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const key = () => `ui-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`; // one key per form submit => retries cannot deduct twice
  const [useKey, setUseKey] = useState(key());
  const close = () => { setDlg(null); setF({}); };

  const icols: Column<R>[] = [
    { header: "Office", cell: (i) => i.office_code }, { header: "Item", cell: (i) => i.name }, { header: "Category", cell: (i) => humanize(i.category) },
    { header: "In stock", cell: (i) => `${i.quantity} ${i.unit}`, className: "text-end" }, { header: "Minimum", cell: (i) => i.minimum_stock, className: "text-end" },
    { header: "Faulty", cell: (i) => i.faulty_quantity, className: "text-end" }, { header: "", cell: (i) => i.low_stock ? <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">Low stock</span> : null },
    { header: "", cell: (i) => (
      <div className="flex flex-wrap gap-1">
        {!i.is_serialized && can("inventory.use_materials") && <Button variant="secondary" onClick={() => { setUseKey(key()); setDlg({ k: "use", item: i }); }}>Use</Button>}
        {can("inventory.purchase") && <Button variant="secondary" onClick={() => setDlg({ k: "purchase", item: i })}>Purchase</Button>}
        {!i.is_serialized && can("inventory.request_adjustment") && <Button variant="ghost" onClick={() => setDlg({ k: "adjust", item: i })}>Adjust</Button>}
        {!i.is_serialized && can("inventory.use_materials") && <Button variant="ghost" onClick={() => setDlg({ k: "return", item: i })}>Return</Button>}
      </div>) },
  ];
  const ecols: Column<R>[] = [
    { header: "Serial", cell: (e) => e.serial_number }, { header: "Item", cell: (e) => e.item_name }, { header: "Office", cell: (e) => e.office_code },
    { header: "Status", cell: (e) => <StatusBadge status={e.status} /> }, { header: "Connection", cell: (e) => e.connection || "-" },
    { header: "", cell: (e) => (
      <div className="flex gap-1">
        {e.status === "available" && can("inventory.use_materials") && <Button variant="secondary" onClick={() => setDlg({ k: "assign", eq: e })}>Assign</Button>}
        {e.status === "assigned" && can("inventory.use_materials") && <Button variant="secondary" onClick={async () => { if (await run(() => api(`/inventory/equipment/${e.id}/unassign/`, { method: "POST" }), "Returned; awaiting inspection.")) equip.reload(); }}>Return</Button>}
        {e.status === "pending_return" && can("inventory.inspect_returns") && <>
          <Button variant="secondary" onClick={async () => { if (await run(() => api(`/inventory/equipment/${e.id}/inspect/`, { method: "POST", body: { condition: "good" } }), "Marked good.")) equip.reload(); }}>Good</Button>
          <Button variant="ghost" onClick={async () => { if (await run(() => api(`/inventory/equipment/${e.id}/inspect/`, { method: "POST", body: { condition: "faulty" } }), "Marked faulty.")) equip.reload(); }}>Faulty</Button></>}
      </div>) },
  ];
  const acols: Column<R>[] = [
    { header: "Item", cell: (a) => a.item_name }, { header: "Requested", cell: (a) => a.requested_delta, className: "text-end" }, { header: "Approved", cell: (a) => a.approved_delta ?? "-", className: "text-end" },
    { header: "Reason", cell: (a) => a.reason }, { header: "Status", cell: (a) => <StatusBadge status={a.status} /> },
    { header: "", cell: (a) => a.status === "pending" && can("inventory.approve_adjustment") ? <div className="flex gap-1">
      <Button variant="secondary" onClick={async () => { if (await run(() => api(`/inventory/adjustments/${a.id}/approve/`, { method: "POST", body: {} }), "Adjustment approved.")) adj.reload(); }}>Approve</Button>
      <Button variant="ghost" onClick={async () => { const n = window.prompt("Reason for rejecting?"); if (n && await run(() => api(`/inventory/adjustments/${a.id}/reject/`, { method: "POST", body: { note: n } }), "Rejected.")) adj.reload(); }}>Reject</Button></div> : null },
  ];
  const rcols: Column<R>[] = [
    { header: "Item", cell: (r) => r.item_name }, { header: "Quantity", cell: (r) => r.quantity }, { header: "Status", cell: (r) => <StatusBadge status={r.status} /> }, { header: "Condition", cell: (r) => r.condition || "-" },
    { header: "", cell: (r) => r.status === "pending_inspection" && can("inventory.inspect_returns") ? <div className="flex gap-1">
      <Button variant="secondary" onClick={async () => { if (await run(() => api(`/inventory/returns/${r.id}/inspect/`, { method: "POST", body: { condition: "good" } }), "Added back to usable stock.")) rets.reload(); }}>Good</Button>
      <Button variant="ghost" onClick={async () => { if (await run(() => api(`/inventory/returns/${r.id}/inspect/`, { method: "POST", body: { condition: "faulty" } }), "Kept as faulty stock.")) rets.reload(); }}>Faulty</Button></div> : null },
  ];
  const mcols: Column<R>[] = [
    { header: "When", cell: (m) => new Date(m.created_at).toLocaleString("en-GB", { timeZone: "Asia/Karachi" }) }, { header: "Item", cell: (m) => m.item_name }, { header: "Type", cell: (m) => humanize(m.kind) },
    { header: "Change", cell: (m) => m.quantity_delta, className: "text-end" }, { header: "Balance", cell: (m) => m.balance_after, className: "text-end" }, { header: "Note", cell: (m) => m.note },
  ];
  const tabs: [Tab, string][] = [["items", "Items"], ["equipment", "Devices"], ["adjustments", "Adjustments"], ["returns", "Returns"], ["movements", "Stock ledger"]];
  const post = (path: string, body: object, msg: string) => async () => { await api(path, { method: "POST", body }); close(); items.reload(); equip.reload(); adj.reload(); rets.reload(); moves.reload(); run(async () => {}, msg); };

  return (
    <>
      <PageHeader title="Inventory" subtitle="Every stock change is a ledger entry; stock can never silently go negative"
        actions={can("inventory.edit") && <Button onClick={() => setDlg({ k: "item" })}>New item</Button>} />
      <div role="tablist" className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
        {tabs.map(([t, label]) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm font-medium ${tab === t ? "border-b-2 border-brand-600 text-brand-700" : "text-slate-600"}`}>{label}</button>)}
      </div>
      {tab === "items" && <>
        <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={low} onChange={(e) => setLow(e.target.checked)} /> Only low-stock items</label>
        {items.error && <Alert>{items.error.message}</Alert>}
        <DataTable columns={icols} rows={items.data?.results} loading={items.loading} rowKey={(i) => i.id} empty={{ title: "No items" }} /></>}
      {tab === "equipment" && <DataTable columns={ecols} rows={equip.data?.results} loading={equip.loading} rowKey={(e) => e.id} empty={{ title: "No devices recorded", hint: "Devices are created when you record a purchase of a serialized item." }} />}
      {tab === "adjustments" && <DataTable columns={acols} rows={adj.data?.results} loading={adj.loading} rowKey={(a) => a.id} empty={{ title: "No adjustment requests" }} />}
      {tab === "returns" && <DataTable columns={rcols} rows={rets.data?.results} loading={rets.loading} rowKey={(r) => r.id} empty={{ title: "No returns" }} />}
      {tab === "movements" && <DataTable columns={mcols} rows={moves.data?.results} loading={moves.loading} rowKey={(m) => m.id} empty={{ title: "No stock movements" }} />}

      <ActionDialog open={dlg?.k === "item"} onClose={close} title="New inventory item" confirmLabel="Create" confirmDisabled={!f.office || !f.name}
        onConfirm={post("/inventory/items/", { office: Number(f.office), name: f.name, category: f.category || "other", unit: f.unit || "pcs", is_serialized: f.serialized === "yes", minimum_stock: f.min || "0" }, "Item created.")}>
        <SelectField label="Office" value={f.office || ""} onChange={set("office")}><option value="">Select…</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField>
        <TextField label="Name" value={f.name || ""} onChange={set("name")} />
        <div className="grid grid-cols-2 gap-3"><SelectField label="Category" value={f.category || "other"} onChange={set("category")}>{CATS.map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</SelectField>
          <TextField label="Unit" value={f.unit || "pcs"} onChange={set("unit")} /></div>
        <div className="grid grid-cols-2 gap-3"><TextField label="Minimum stock" value={f.min || ""} onChange={set("min")} /><SelectField label="Tracking" value={f.serialized || "no"} onChange={set("serialized")}><option value="no">By quantity</option><option value="yes">Per device (serial number)</option></SelectField></div>
      </ActionDialog>
      <ActionDialog open={dlg?.k === "purchase"} onClose={close} title={`Record purchase${dlg && "item" in dlg ? `: ${dlg.item.name}` : ""}`} confirmLabel="Record purchase" confirmDisabled={!f.qty || !f.price || !f.date}
        onConfirm={post("/inventory/purchases/", { item: (dlg as any)?.item?.id, quantity: f.qty, unit_price: f.price, purchase_date: f.date, supplier_notes: f.notes || "", ...((dlg as any)?.item?.is_serialized ? { serials: (f.serials || "").split(/[\s,]+/).filter(Boolean) } : {}) }, "Purchase recorded.")}>
        <div className="grid grid-cols-2 gap-3"><TextField label="Quantity" value={f.qty || ""} onChange={set("qty")} /><TextField label="Unit price (PKR)" value={f.price || ""} onChange={set("price")} /></div>
        <TextField label="Purchase date" type="date" value={f.date || ""} onChange={set("date")} />
        {(dlg as any)?.item?.is_serialized && <TextField label="Serial numbers" hint="Separate with commas or spaces; one per device." value={f.serials || ""} onChange={set("serials")} />}
        <TextField label="Supplier / invoice notes" value={f.notes || ""} onChange={set("notes")} />
      </ActionDialog>
      <ActionDialog open={dlg?.k === "use"} onClose={close} title={`Use materials${dlg && "item" in dlg ? `: ${dlg.item.name}` : ""}`} confirmLabel="Deduct stock" confirmDisabled={!f.qty || !(f.complaint || f.connection)}
        description="Link the usage to a job. Retrying this form never deducts twice."
        onConfirm={post("/inventory/usages/", { item: (dlg as any)?.item?.id, quantity: f.qty, idempotency_key: useKey, complaint: (f.complaint || "").toUpperCase(), connection: (f.connection || "").toUpperCase() }, "Stock deducted.")}>
        <TextField label="Quantity" value={f.qty || ""} onChange={set("qty")} /><TextField label="Complaint number" placeholder="AAA-CMP-000001" value={f.complaint || ""} onChange={set("complaint")} />
        <TextField label="…or connection" placeholder="CON-00001" value={f.connection || ""} onChange={set("connection")} />
      </ActionDialog>
      <ActionDialog open={dlg?.k === "adjust"} onClose={close} title="Request stock adjustment" confirmLabel="Submit for approval" confirmDisabled={!f.delta || !f.reason}
        description="Stock changes only after a manager approves."
        onConfirm={post("/inventory/adjustments/", { item: (dlg as any)?.item?.id, delta: f.delta, reason: f.reason }, "Adjustment requested.")}>
        <TextField label="Change (+ or −)" placeholder="-5" value={f.delta || ""} onChange={set("delta")} /><TextField label="Reason" value={f.reason || ""} onChange={set("reason")} />
      </ActionDialog>
      <ActionDialog open={dlg?.k === "return"} onClose={close} title="Return unused material" confirmLabel="Submit return" confirmDisabled={!f.qty}
        description="Returned material is inspected before it rejoins usable stock."
        onConfirm={post("/inventory/returns/", { item: (dlg as any)?.item?.id, quantity: f.qty, note: f.note || "" }, "Return submitted for inspection.")}>
        <TextField label="Quantity" value={f.qty || ""} onChange={set("qty")} /><TextField label="Note" value={f.note || ""} onChange={set("note")} />
      </ActionDialog>
      <ActionDialog open={dlg?.k === "assign"} onClose={close} title="Assign device to a connection" confirmLabel="Assign" confirmDisabled={!f.connection}
        onConfirm={async () => { await api(`/inventory/equipment/${(dlg as any).eq.id}/assign/`, { method: "POST", body: { connection: f.connection.toUpperCase() } }); close(); equip.reload(); }}>
        <TextField label="Connection" placeholder="CON-00001" value={f.connection || ""} onChange={set("connection")} />
      </ActionDialog>
    </>
  );
}
