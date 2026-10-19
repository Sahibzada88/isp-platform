"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useFetch } from "@/lib/hooks";
import type { Customer, Page, Village } from "@/lib/types";
import { Alert, Button, Card, Modal, PageHeader, SelectField, TextAreaField, TextField, useToast } from "@/components/ui";

interface Duplicate { customer_id: string; matches: string[] }

export default function NewCustomerPage() {
  const router = useRouter();
  const toast = useToast();
  const villages = useFetch<Page<Village>>("/villages/");
  const [form, setForm] = useState({ full_name: "", phone: "", cnic: "", permanent_address: "", village: "", notes: "" });
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dups, setDups] = useState<Duplicate[] | null>(null);
  const [overrideReason, setOverrideReason] = useState("");

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(confirm: boolean) {
    setBusy(true); setError(null); setFields({});
    try {
      const body = { ...form, village: Number(form.village), ...(confirm ? { confirm_duplicates: true, override_reason: overrideReason } : {}) };
      const c = await api<Customer>("/customers/", { method: "POST", body });
      toast("success", `Customer ${c.customer_id} created.`);
      router.replace(`/customers/${c.customer_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.code === "possible_duplicate") {
        setDups(((e.details as { duplicates?: Duplicate[] })?.duplicates) || []);
      } else if (e instanceof ApiError) {
        setFields(e.fieldErrors);
        setError(Object.keys(e.fieldErrors).length ? null : e.message);
      } else setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  const onSubmit = (e: FormEvent) => { e.preventDefault(); void submit(false); };

  return (
    <div className="max-w-2xl">
      <PageHeader title="New customer" subtitle="A permanent customer ID is generated on save" />
      {villages.data && villages.data.results.length === 0 && <div className="mb-4"><Alert kind="info">No villages exist yet. Ask the owner to add offices and villages under “Offices & villages”.</Alert></div>}
      <Card>
        <form onSubmit={onSubmit} className="space-y-4">
          {error && <Alert>{error}</Alert>}
          <TextField label="Full name" value={form.full_name} onChange={set("full_name")} error={fields.full_name} required />
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="Phone" inputMode="tel" value={form.phone} onChange={set("phone")} error={fields.phone} required />
            <TextField label="CNIC" inputMode="numeric" placeholder="35202-1234567-1" value={form.cnic} onChange={set("cnic")} error={fields.cnic} hint="Optional. 13 digits." />
          </div>
          <SelectField label="Village" value={form.village} onChange={set("village")} error={fields.village} required>
            <option value="">Select a village…</option>
            {villages.data?.results.filter((v) => v.is_active).map((v) => <option key={v.id} value={v.id}>{v.name} ({v.code})</option>)}
          </SelectField>
          <TextAreaField label="Permanent address" value={form.permanent_address} onChange={set("permanent_address")} error={fields.permanent_address} required />
          <TextAreaField label="Internal notes" value={form.notes} onChange={set("notes")} error={fields.notes} />
          <div className="flex gap-2">
            <Button type="submit" loading={busy}>Create customer</Button>
            <Link href="/customers" className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50">Cancel</Link>
          </div>
        </form>
      </Card>

      <Modal open={dups !== null} onClose={() => setDups(null)} title="Possible duplicate customer"
        footer={<>
          <Button variant="secondary" onClick={() => setDups(null)}>Go back</Button>
          <Button onClick={() => { setDups(null); void submit(true); }} disabled={!overrideReason.trim()}>Create anyway</Button>
        </>}>
        <p className="text-sm text-slate-600">Existing customers share details with this one. Families and shared numbers are normal; check before continuing.</p>
        <ul className="divide-y divide-slate-100 rounded-md border border-slate-200">
          {dups?.map((d) => (
            <li key={d.customer_id} className="flex items-center justify-between px-3 py-2 text-sm">
              <Link href={`/customers/${d.customer_id}`} target="_blank" className="font-medium text-brand-700 hover:underline">{d.customer_id}</Link>
              <span className="text-slate-500">{d.matches.join(", ")}</span>
            </li>
          ))}
        </ul>
        <TextAreaField label="Why is this a different customer?" value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} hint="Recorded in the audit log." />
      </Modal>
    </div>
  );
}
