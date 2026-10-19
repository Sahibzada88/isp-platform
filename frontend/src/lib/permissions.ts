import type { Me } from "./types";

/** UI hint only. The Django API enforces every permission; hiding a button is not security. */
export function hasPermission(me: Me | null, code: string, officeCode?: string): boolean {
  if (!me) return false;
  if (me.is_owner) return true;
  if (officeCode) return (me.permissions[officeCode] || []).includes(code) || (me.permissions["*"] || []).includes(code);
  return Object.values(me.permissions).some((codes) => codes.includes(code));
}
