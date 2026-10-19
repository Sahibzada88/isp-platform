"use client";

import { useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useFetch } from "@/lib/hooks";
import { fileToBase64Jpeg } from "@/lib/image";
import { Alert, Button, Spinner } from "./ui";

interface Att { id: number; filename: string; width: number; height: number; size_bytes: number; created_at: string }

/** Attach photos/receipts to a record. Images are compressed in the browser, sent as Base64, and re-validated by Django. */
export function ImageAttachments({ entityType, entityId, kind, canUpload }: { entityType: string; entityId: number; kind: string; canUpload: boolean }) {
  const list = useFetch<Att[]>("/attachments/", { entity_type: entityType, entity_id: entityId });
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onFile(file: File | undefined) {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const data = await fileToBase64Jpeg(file);
      await api("/attachments/", { method: "POST", body: { entity_type: entityType, entity_id: entityId, kind, filename: file.name, data } });
      list.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="space-y-3">
      {error && <Alert>{error}</Alert>}
      {list.loading && !list.data ? <Spinner /> : (
        <ul className="flex flex-wrap gap-3">
          {list.data?.map((a) => (
            <li key={a.id}>
              <a href={`/api/v1/attachments/${a.id}/image/`} target="_blank" rel="noreferrer" title={`${a.filename} · ${a.width}×${a.height}`}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`/api/v1/attachments/${a.id}/thumbnail/`} alt={a.filename} className="h-20 w-20 rounded-md border border-slate-200 object-cover" />
              </a>
            </li>
          ))}
          {list.data?.length === 0 && <li className="text-sm text-slate-500">No images attached.</li>}
        </ul>
      )}
      {canUpload && (
        <>
          <input ref={input} type="file" accept="image/*" capture="environment" className="sr-only" id={`up-${entityType}-${entityId}`} onChange={(e) => void onFile(e.target.files?.[0])} />
          <Button variant="secondary" loading={busy} onClick={() => input.current?.click()}>Add photo</Button>
        </>
      )}
    </div>
  );
}
