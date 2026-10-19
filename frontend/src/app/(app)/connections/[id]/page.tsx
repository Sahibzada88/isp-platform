"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Connection, Invoice, Page, PackageT, Village } from "@/lib/types";
import { formatDate, formatDateTime, formatPKR, humanize } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { CustomerPicker } from "@/components/customer-picker";
import { Alert, Button, Card, DataTable, KeyValue, PageHeader, SelectField, Spinner, StatusBadge, TextAreaField, useToast, type Column } from "@/components/ui";

type Dialog = null | "transfer" | "owner" | "installation" | "activate" | "renewal" | "suspend" | "cancel" | "reactivate" | "address" | "package" | "waive";

export default function ConnectionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useAuth();
  const run = useRun();
  const toast = useToast();
  const conn = useFetch<Connection>(`/connections/${id}/`);
  const invoices = useFetch<Page<Invoice>>("/invoices/", { connection: id });
  const [dialog, setDialog] = useState<Dialog>(null);
  const [unreturned, setUnreturned] = useState(false);
  const [newAddress, setNewAddress] = useState("");
  const [pkgId, setPkgId] = useState("");
  const [tf, setTf] = useState({ village: "", address: "", reason: "", moveCustomer: false, newOwner: "", handling: "none_outstanding", note: "" });
  const villages = useFetch<Page<Village>>(dialog === "transfer" ? "/villages/" : null);
  const packages = useFetch<Page<PackageT>>(dialog === "package" ? "/packages/" : null, { active: "true" });

  if (conn.loading && !conn.data) return <Spinner className="h-6 w-6 text-brand-600" />;
  if (conn.error) return <Alert>{conn.error.status === 404 ? "Connection not found." : conn.error.message}</Alert>;
  const c = conn.data!;
  const pending = c.status === "pending_installation" || c.status === "pending_approval";
  const gatesOk = !!c.initial_payment_confirmed_at && !!c.installation_completed_at;

  // ActionDialog shows errors itself, so call the API directly and let it throw.
  const post = (path: string, body?: object) => api(`/connections/${id}/${path}/`, { method: "POST", body });
  const done = (msg: string) => { setDialog(null); conn.reload(); invoices.reload(); toast("success", msg); };

  async function createInvoice() {
    await run(async () => {
      const inv = await api<Invoice>("/invoices/", { method: "POST", body: { connection: id } });
      router.push(`/invoices/${inv.number}`);
    }, "Invoice ready.");
  }

  const actions = [
    can("billing.create_invoice") && c.status !== "cancelled" && c.status !== "suspended" && !(pending && c.initial_payment_confirmed_at) &&
      <Button key="inv" onClick={createInvoice}>{pending ? "Create / open invoice" : "Create renewal invoice"}</Button>,
    can("connections.complete_installation") && c.status === "pending_installation" && <Button key="inst" variant="secondary" onClick={() => setDialog("installation")}>Mark installation complete</Button>,
    can("connections.approve_activation") && c.status === "pending_approval" && <Button key="act" onClick={() => setDialog("activate")} disabled={!gatesOk}>Approve activation</Button>,
    can("connections.approve_activation") && c.status === "expired" && <Button key="ren" variant="secondary" onClick={() => setDialog("renewal")}>Confirm paid renewal</Button>,
    can("connections.change_address") && c.status !== "cancelled" && <Button key="addr" variant="secondary" onClick={() => { setNewAddress(c.installation_address); setDialog("address"); }}>Change address</Button>,
    can("connections.schedule_package") && c.status !== "cancelled" && <Button key="pkg" variant="secondary" onClick={() => setDialog("package")}>Change package (next renewal)</Button>,
    can("billing.waive_installation_fee") && pending && !c.installation_fee_waived && <Button key="waive" variant="secondary" onClick={() => setDialog("waive")}>Waive installation fee</Button>,
    can("transfers.request") && c.status !== "cancelled" && <Button key="tr" variant="secondary" onClick={() => { setTf((t) => ({ ...t, address: c.installation_address })); setDialog("transfer"); }}>Transfer to another village/office</Button>,
    can("transfers.request") && c.status !== "cancelled" && <Button key="ow" variant="secondary" onClick={() => setDialog("owner")}>Change owner</Button>,
    can("connections.suspend") && (c.status === "active" || c.status === "expired") && <Button key="sus" variant="secondary" onClick={() => setDialog("suspend")}>Suspend</Button>,
    can("connections.cancel") && c.status !== "cancelled" && <Button key="can" variant="danger" onClick={() => setDialog("cancel")}>Cancel connection</Button>,
    can("connections.reactivate") && c.status === "cancelled" && <Button key="rea" onClick={() => setDialog("reactivate")}>Reactivate</Button>,
  ].filter(Boolean);

  const invColumns: Column<Invoice>[] = [
    { header: "Invoice", cell: (i) => <Link href={`/invoices/${i.number}`} className="font-medium text-brand-700 hover:underline">{i.number}</Link> },
    { header: "Type", cell: (i) => humanize(i.kind) },
    { header: "Total", cell: (i) => formatPKR(i.total_amount), className: "text-end" },
    { header: "Issued", cell: (i) => formatDateTime(i.issued_at) },
    { header: "Status", cell: (i) => <StatusBadge status={i.status} /> },
  ];

  const Check = ({ ok, label, at }: { ok: boolean; label: string; at?: string | null }) => (
    <li className="flex items-center gap-2 text-sm">
      <span aria-hidden className={ok ? "text-green-600" : "text-slate-400"}>{ok ? "✔" : "○"}</span>
      <span className={ok ? "text-slate-900" : "text-slate-500"}>{label}</span>
      <span className="sr-only">{ok ? "done" : "not done"}</span>
      {at && <span className="text-xs text-slate-500">{formatDateTime(at)}</span>}
    </li>
  );

  return (
    <>
      <PageHeader title={c.connection_id} subtitle={`Customer ${c.customer} · ${c.office_code}`} actions={<StatusBadge status={c.status} />} />
      <div className="space-y-5">
        {c.status === "expired" && <Alert kind="info">This connection is expired in the software. Any router disconnection is done manually by staff.</Alert>}

        {pending && (
          <Card title="Activation checklist">
            <ul className="space-y-1.5">
              <Check ok={!!c.initial_payment_confirmed_at} label="Full payment confirmed" at={c.initial_payment_confirmed_at} />
              <Check ok={!!c.installation_completed_at} label="Installation completed" at={c.installation_completed_at} />
              <Check ok={!!c.activation_approved_at} label="Activation approved by an authorised employee" at={c.activation_approved_at} />
            </ul>
            <p className="mt-3 text-xs text-slate-500">All three are required. Payment alone, or verification alone, never activates a connection.</p>
          </Card>
        )}

        <Card title="Details">
          <KeyValue items={[
            { label: "Package", value: c.package_code },
            { label: "Next package (applies at renewal)", value: c.next_package_code || "-" },
            { label: "Expires", value: formatDateTime(c.expires_at) },
            { label: "Village", value: c.village_code },
            { label: "Installation address", value: c.installation_address },
            { label: "Installation fee", value: c.installation_fee_waived ? "Waived" : "Standard" },
            ...(c.status === "cancelled" ? [
              { label: "Cancelled", value: formatDateTime(c.cancelled_at) },
              { label: "Reason", value: c.cancellation_reason },
              { label: "Equipment return", value: humanize(c.equipment_return_status) },
            ] : []),
          ]} />
        </Card>

        {actions.length > 0 && <Card title="Actions"><div className="flex flex-wrap gap-2">{actions}</div></Card>}

        <Card title="Invoices">
          <DataTable columns={invColumns} rows={invoices.data?.results} loading={invoices.loading} rowKey={(i) => i.id} empty={{ title: "No invoices yet" }} />
        </Card>
      </div>

      <ActionDialog open={dialog === "transfer"} onClose={() => setDialog(null)} title="Request transfer" confirmLabel="Submit for approval" reasonLabel="Reason" confirmDisabled={!tf.village || !tf.address.trim()}
        description="The destination office's manager must approve. Customer and connection IDs never change; history is kept. Unpaid invoices must be settled or voided first."
        onConfirm={async (reason) => { await api("/transfers/office/", { method: "POST", body: { connection: id, to_village: Number(tf.village), new_installation_address: tf.address, reason, move_customer: tf.moveCustomer } }); setDialog(null); toast("success", "Transfer requested."); }}>
        <SelectField label="Destination village" value={tf.village} onChange={(e) => setTf({ ...tf, village: e.target.value })}><option value="">Select…</option>{villages.data?.results.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.code})</option>)}</SelectField>
        <TextAreaField label="New installation address" value={tf.address} onChange={(e) => setTf({ ...tf, address: e.target.value })} />
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={tf.moveCustomer} onChange={(e) => setTf({ ...tf, moveCustomer: e.target.checked })} /> Also move the customer's own record to the new village</label>
      </ActionDialog>
      <ActionDialog open={dialog === "owner"} onClose={() => setDialog(null)} title="Change connection owner" confirmLabel="Submit for approval" reasonLabel="Reason" confirmDisabled={!tf.newOwner || (tf.handling === "old_owner_responsible" && !tf.note.trim())}
        description="The new owner must already be registered in this office. The previous owner and their history stay as they are; unpaid amounts are never moved silently."
        onConfirm={async (reason) => { await api("/transfers/ownership/", { method: "POST", body: { connection: id, new_customer: tf.newOwner, effective_date: new Date().toLocaleDateString("en-CA"), balance_handling: tf.handling, handling_note: tf.note, reason } }); setDialog(null); toast("success", "Ownership transfer requested."); }}>
        <CustomerPicker label="New owner" officeId={c.office} onPick={(cid) => setTf((t) => ({ ...t, newOwner: cid }))} />
        <SelectField label="Unpaid balance" value={tf.handling} onChange={(e) => setTf({ ...tf, handling: e.target.value })}><option value="none_outstanding">Nothing is outstanding (the system checks)</option><option value="old_owner_responsible">Previous owner stays responsible</option></SelectField>
        {tf.handling === "old_owner_responsible" && <TextAreaField label="Describe the arrangement" value={tf.note} onChange={(e) => setTf({ ...tf, note: e.target.value })} />}
      </ActionDialog>
      <ActionDialog open={dialog === "installation"} onClose={() => setDialog(null)} title="Mark installation complete" confirmLabel="Mark complete"
        description="Confirms the technician finished the physical installation." onConfirm={async () => { await post("complete-installation"); done("Installation recorded."); }} />
      <ActionDialog open={dialog === "activate"} onClose={() => setDialog(null)} title="Approve activation" confirmLabel="Approve activation"
        description="Payment is confirmed and installation is complete. This sets the connection to Active in the software; connect the router manually."
        onConfirm={async () => { await post("approve-activation"); done("Connection activated."); }} />
      <ActionDialog open={dialog === "renewal"} onClose={() => setDialog(null)} title="Confirm paid renewal" confirmLabel="Confirm renewal"
        description="The renewal invoice is paid. Confirming sets the connection back to Active; reconnect the router manually."
        onConfirm={async () => { await post("confirm-renewal"); done("Renewal confirmed."); }} />
      <ActionDialog open={dialog === "suspend"} onClose={() => setDialog(null)} title="Suspend connection" confirmLabel="Suspend" reasonLabel="Reason"
        onConfirm={async (reason) => { await post("suspend", { reason }); done("Connection suspended."); }} />
      <ActionDialog open={dialog === "cancel"} onClose={() => setDialog(null)} title="Cancel connection" confirmLabel="Cancel connection" danger reasonLabel="Cancellation reason"
        description="History, invoices and payments are kept. Other connections of this customer are not affected."
        onConfirm={async (reason) => { await post("cancel", { reason, equipment_unreturned: unreturned }); done("Connection cancelled."); }}>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={unreturned} onChange={(e) => setUnreturned(e.target.checked)} /> Equipment has not been returned yet</label>
      </ActionDialog>
      <ActionDialog open={dialog === "reactivate"} onClose={() => setDialog(null)} title="Reactivate connection" confirmLabel="Start reactivation" reasonLabel="Reason"
        description="Payment, installation checks and approval are required again before it becomes Active."
        onConfirm={async (reason) => { await post("reactivate", { reason }); done("Reactivation started."); }} />
      <ActionDialog open={dialog === "waive"} onClose={() => setDialog(null)} title="Waive installation fee" confirmLabel="Waive fee" reasonLabel="Reason for waiver"
        description="Only possible before the fee appears on an invoice. Recorded in the audit log."
        onConfirm={async (reason) => { await post("waive-installation-fee", { reason }); done("Installation fee waived."); }} />
      <ActionDialog open={dialog === "address"} onClose={() => setDialog(null)} title="Change installation address" confirmLabel="Save address" reasonLabel="Reason"
        description="The previous address stays in the history."
        confirmDisabled={!newAddress.trim()} onConfirm={async (reason) => { await post("change-address", { new_address: newAddress, reason }); done("Address updated."); }}>
        <TextAreaField label="New address" value={newAddress} onChange={(e) => setNewAddress(e.target.value)} />
      </ActionDialog>
      <ActionDialog open={dialog === "package"} onClose={() => setDialog(null)} title="Change package at next renewal" confirmLabel="Schedule change"
        description="The current package and price stay until the current paid term ends."
        confirmDisabled={!pkgId} onConfirm={async () => { await post("schedule-package", { package: Number(pkgId) }); done("Package change scheduled."); }}>
        <SelectField label="New package" value={pkgId} onChange={(e) => setPkgId(e.target.value)}>
          <option value="">Select a package…</option>
          {packages.data?.results.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.speed_mbps} Mbps · {formatPKR(p.price_pkr)}</option>)}
        </SelectField>
      </ActionDialog>
    </>
  );
}
