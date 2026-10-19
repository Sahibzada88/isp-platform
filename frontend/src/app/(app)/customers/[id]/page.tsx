"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Connection, Customer, Page, PackageT } from "@/lib/types";
import { formatDate, formatDateTime } from "@/lib/format";
import { Alert, Button, Card, DataTable, KeyValue, Modal, PageHeader, SelectField, Spinner, StatusBadge, TextAreaField, TextField, useToast, type Column } from "@/components/ui";

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const toast = useToast();
  const customer = useFetch<Customer>(`/customers/${id}/`);
  const conns = useFetch<Page<Connection>>("/connections/", { customer: id });
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);

  if (customer.loading && !customer.data) return <Spinner className="h-6 w-6 text-brand-600" />;
  if (customer.error) return <Alert>{customer.error.status === 404 ? "Customer not found." : customer.error.message}</Alert>;
  const c = customer.data!;

  const columns: Column<Connection>[] = [
    { header: "Connection", cell: (x) => <Link href={`/connections/${x.connection_id}`} className="font-medium text-brand-700 hover:underline">{x.connection_id}</Link> },
    { header: "Package", cell: (x) => x.package_code },
    { header: "Address", cell: (x) => x.installation_address },
    { header: "Expires", cell: (x) => formatDate(x.expires_at) },
    { header: "Status", cell: (x) => <StatusBadge status={x.status} /> },
  ];

  return (
    <>
      <PageHeader title={`${c.customer_id} · ${c.full_name}`} subtitle={`${c.village_code} · ${c.office_code}`}
        actions={<>
          {can("customers.edit") && <Button variant="secondary" onClick={() => setEditing(true)}>Edit details</Button>}
          {can("connections.create") && <Button onClick={() => setAdding(true)}>Add connection</Button>}
        </>} />
      <div className="space-y-5">
        <Card title="Details">
          <KeyValue items={[
            { label: "Status", value: <StatusBadge status={c.status} /> },
            { label: "Phone", value: c.phone },
            { label: "CNIC", value: c.cnic || "-" },
            { label: "Registered", value: formatDate(c.registered_on) },
            { label: "Permanent address", value: c.permanent_address },
            { label: "Notes", value: c.notes || "-" },
          ]} />
        </Card>
        <Card title="Connections">
          <DataTable columns={columns} rows={conns.data?.results} loading={conns.loading} rowKey={(x) => x.id}
            empty={{ title: "No connections yet", hint: "Add a connection to start installation." }} />
        </Card>
      </div>
      {editing && <EditDialog customer={c} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); customer.reload(); toast("success", "Customer updated."); }} />}
      {adding && <AddConnectionDialog customer={c} onClose={() => setAdding(false)} onSaved={(cid) => { setAdding(false); conns.reload(); toast("success", `Connection ${cid} created.`); }} />}
    </>
  );
}

function EditDialog({ customer, onClose, onSaved }: { customer: Customer; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ full_name: customer.full_name, phone: customer.phone, cnic: customer.cnic.includes("*") ? "" : customer.cnic, permanent_address: customer.permanent_address, notes: customer.notes });
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const masked = customer.cnic.includes("*");
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save() {
    setBusy(true); setError(null); setFields({});
    try {
      const body: Record<string, string> = { ...form };
      if (masked && !form.cnic) delete body.cnic; // keep the stored CNIC untouched
      await api(`/customers/${customer.customer_id}/`, { method: "PATCH", body });
      onSaved();
    } catch (e) {
      if (e instanceof ApiError) { setFields(e.fieldErrors); setError(Object.keys(e.fieldErrors).length ? null : e.message); }
    } finally { setBusy(false); }
  }

  return (
    <Modal open onClose={onClose} title="Edit customer"
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button><Button onClick={save} loading={busy}>Save</Button></>}>
      {error && <Alert>{error}</Alert>}
      <TextField label="Full name" value={form.full_name} onChange={set("full_name")} error={fields.full_name} />
      <TextField label="Phone" value={form.phone} onChange={set("phone")} error={fields.phone} />
      <TextField label="CNIC" value={form.cnic} onChange={set("cnic")} error={fields.cnic} placeholder={masked ? customer.cnic : ""} hint={masked ? "Hidden for your account. Leave blank to keep it unchanged." : undefined} />
      <TextAreaField label="Permanent address" value={form.permanent_address} onChange={set("permanent_address")} error={fields.permanent_address} hint="Address changes keep the previous address in history." />
      <TextAreaField label="Internal notes" value={form.notes} onChange={set("notes")} error={fields.notes} />
    </Modal>
  );
}

function AddConnectionDialog({ customer, onClose, onSaved }: { customer: Customer; onClose: () => void; onSaved: (connectionId: string) => void }) {
  const packages = useFetch<Page<PackageT>>("/packages/", { active: "true" });
  const [pkg, setPkg] = useState("");
  const [address, setAddress] = useState(customer.permanent_address);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true); setError(null); setFields({});
    try {
      const conn = await api<Connection>("/connections/", { method: "POST", body: { customer: customer.customer_id, package: Number(pkg), installation_address: address } });
      onSaved(conn.connection_id);
    } catch (e) {
      if (e instanceof ApiError) { setFields(e.fieldErrors); setError(Object.keys(e.fieldErrors).length ? null : e.message); }
    } finally { setBusy(false); }
  }

  return (
    <Modal open onClose={onClose} title="Add connection"
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button><Button onClick={save} loading={busy} disabled={!pkg || !address.trim()}>Create connection</Button></>}>
      {error && <Alert>{error}</Alert>}
      <SelectField label="Package" value={pkg} onChange={(e) => setPkg(e.target.value)} error={fields.package}>
        <option value="">Select a package…</option>
        {packages.data?.results.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.speed_mbps} Mbps · PKR {Number(p.price_pkr).toLocaleString()}</option>)}
      </SelectField>
      <TextAreaField label="Installation address" value={address} onChange={(e) => setAddress(e.target.value)} error={fields.installation_address} />
      <p className="text-xs text-slate-500">The connection starts as “Pending installation”. Activation needs payment, completed installation and approval.</p>
    </Modal>
  );
}
