export interface Page<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface Me {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  role: string;
  is_owner: boolean;
  must_change_password: boolean;
  offices: string[];
  /** office code -> permission codes; "*" holds global permissions */
  permissions: Record<string, string[]>;
}

export interface Office { id: number; code: string; name: string; is_active: boolean; is_head_office?: boolean }
export interface Village { id: number; code: string; name: string; office: number; is_active: boolean }
export interface PackageT { id: number; code: string; name: string; speed_mbps: number; price_pkr: string; is_active: boolean }

export interface Customer {
  id: number; customer_id: string; full_name: string; phone: string; cnic: string; permanent_address: string;
  village: number; village_code: string; office: number; office_code: string; status: string;
  registered_on: string; notes: string;
}

export interface Connection {
  id: number; connection_id: string; customer: string; office: number; office_code: string; village_code: string;
  installation_address: string; package: number; package_code: string; next_package: number | null;
  next_package_code: string | null; status: string; expires_at: string | null;
  initial_payment_confirmed_at: string | null; installation_completed_at: string | null;
  activation_approved_at: string | null; installation_fee_waived: boolean; cancelled_at: string | null;
  cancellation_reason: string; equipment_return_status: string; created_at: string;
}

export interface InvoiceLine { kind: string; description: string; amount: string }
export interface Invoice {
  id: number; number: string; status: string; kind: string; office_code: string; customer: string;
  customer_name: string; connection: string; package_code: string; package_name: string; speed_mbps: number;
  package_price: string; installation_fee: string; total_amount: string; term_days: number; currency: string;
  issued_at: string; supersedes: string | null; void_reason: string; lines: InvoiceLine[];
}

export interface Payment {
  id: number; invoice: string; customer: string; connection: string; method: string; amount: string; status: string;
  reference: string; paid_at: string; entered_at: string; entered_by: number; confirmed_at: string | null;
  verified_by: number | null; rejection_reason: string; receipt: string | null; receipt_status: string | null;
}

export interface Receipt {
  number: string; status: string; issued_at: string; payment_status: string; amount: string; method: string;
  invoice: string; customer: string; connection: string;
}
