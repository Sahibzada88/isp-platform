"use client";

import { useState } from "react";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Customer, Page } from "@/lib/types";
import { TextField } from "./ui";

/** Search-as-you-type customer chooser (by ID, name or phone). `onPick` receives the customer_id. */
export function CustomerPicker({ label = "Customer", onPick, officeId }: { label?: string; onPick: (customerId: string, c: Customer) => void; officeId?: number | string }) {
  const [text, setText] = useState("");
  const [chosen, setChosen] = useState<Customer | null>(null);
  const q = useDebounced(text, 300);
  const { data, loading } = useFetch<Page<Customer>>(q.trim().length >= 2 && !chosen ? "/customers/" : null, { search: q.trim(), office: officeId, page_size: 6 });
  return (
    <div>
      <TextField label={label} placeholder="Type an ID, name or phone" value={chosen ? `${chosen.customer_id} · ${chosen.full_name}` : text}
        onChange={(e) => { setChosen(null); setText(e.target.value); }} autoComplete="off" />
      {!chosen && q.trim().length >= 2 && (
        <ul className="mt-1 max-h-48 overflow-y-auto rounded-md border border-slate-200 bg-white text-sm shadow-sm" role="listbox" aria-label="Matching customers">
          {loading && <li className="px-3 py-2 text-slate-500">Searching…</li>}
          {data?.results.length === 0 && <li className="px-3 py-2 text-slate-500">No match</li>}
          {data?.results.map((c) => (
            <li key={c.id}><button type="button" role="option" aria-selected={false} className="block w-full px-3 py-2 text-start hover:bg-slate-50"
              onClick={() => { setChosen(c); onPick(c.customer_id, c); }}>
              <span className="font-medium">{c.customer_id}</span> · {c.full_name} <span className="text-slate-500">· {c.phone}</span></button></li>
          ))}
        </ul>
      )}
    </div>
  );
}
