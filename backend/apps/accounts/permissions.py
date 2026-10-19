"""Permission registry. Codes are strings so new modules just add entries here."""
from .models import Role

PERMISSIONS = {
    # customers
    "customers.view": "View customers",
    "customers.create": "Create customers",
    "customers.edit": "Edit customer details",
    "customers.view_cnic": "View full CNIC",
    "customers.export": "Export customer data",
    # connections
    "connections.view": "View connections",
    "connections.create": "Create connections",
    "connections.complete_installation": "Mark installation completed",
    "connections.approve_activation": "Approve connection activation",
    "connections.cancel": "Cancel connections",
    "connections.suspend": "Suspend connections",
    "connections.reactivate": "Reactivate cancelled connections",
    "connections.change_address": "Change installation address",
    "connections.schedule_package": "Schedule package change",
    "transfers.request": "Request office / ownership transfers",
    "transfers.approve": "Approve office / ownership transfers",
    # billing & payments (modules to come)
    "billing.view": "View invoices and receipts",
    "billing.create_invoice": "Create invoices",
    "billing.void_invoice": "Void/reissue unpaid invoices",
    "billing.waive_installation_fee": "Waive the installation fee",
    "payments.record": "Record payments",
    "payments.verify": "Verify digital payments",
    "receipts.issue": "Issue receipts",
    "payments.request_correction": "Request payment corrections",
    "payments.approve_correction": "Approve payment corrections",
    "refunds.approve": "Approve refunds (owner)",
    "refunds.request": "Request refunds",
    "refunds.pay": "Record refund payouts",
    "payments.record_exception": "Record overpayment exceptions",
    "cash.deposit": "Create cash deposits",
    "cash.reconcile": "Reconcile/cancel cash deposits",
    # packages
    "packages.view": "View packages",
    "packages.manage": "Create/edit/deactivate packages",
    "packages.change_price": "Change package prices",
    # inventory
    "inventory.view": "View inventory",
    "inventory.edit": "Edit inventory",
    "inventory.approve_adjustment": "Approve stock adjustments",
    # complaints & tasks
    "complaints.view": "View all complaints of the office",
    "complaints.view_assigned": "View complaints assigned to me",
    "complaints.create": "Log complaints",
    "complaints.assign": "Assign technicians / change priority",
    "complaints.work": "Update progress, log work, resolve",
    "complaints.close": "Close complaints",
    "complaints.reopen": "Reopen complaints",
    "tasks.view": "View all tasks of the office",
    "tasks.manage": "Create and assign tasks",
    "tasks.work": "Work on tasks assigned to me",
    # people
    "employees.view": "View employees",
    "employees.manage": "Manage employees",
    "attendance.self": "Check in / out",
    "attendance.view": "View attendance of the office",
    "attendance.correct": "Correct attendance",
    "leave.request": "Request leave",
    "leave.approve": "Approve leave",
    "leads.view": "View leads",
    "leads.manage": "Create and edit leads",
    "leads.convert": "Convert leads to customers",
    "portal.manage": "Manage customer portal accounts",
    "inventory.purchase": "Record purchases",
    "inventory.use_materials": "Record material usage",
    "inventory.request_adjustment": "Request stock adjustments",
    "inventory.inspect_returns": "Inspect returned stock/equipment",
    "expenses.view": "View expenses",
    "reports.view": "View reports and dashboards",
    # finance / reports
    "expenses.record": "Record expenses",
    "expenses.approve": "Approve expenses",
    "reports.export": "Export reports",
    # global (no office)
    "offices.create": "Create offices",
    "permissions.manage": "Manage permissions",
    "audit.view": "Access audit logs",
    "system.manage": "Manage backups and system settings",
    "imports.manage": "Run data imports",
}

GLOBAL_PERMISSIONS = {"offices.create", "permissions.manage", "audit.view", "system.manage", "imports.manage"}

_SELF = ["attendance.self", "leave.request"]
_STAFF = [
    "customers.view", "customers.create", "customers.edit",
    "connections.view", "connections.create", "connections.change_address",
    "billing.view", "billing.create_invoice", "payments.record", "receipts.issue", "payments.request_correction",
    "packages.view", "refunds.request", "payments.record_exception", "cash.deposit",
    "complaints.view", "complaints.create", "complaints.assign", "complaints.close", "complaints.reopen",
    "tasks.view", "tasks.manage", "leads.view", "leads.manage", "leads.convert", "portal.manage",
    "expenses.view", "inventory.view", "transfers.request",
] + _SELF
ROLE_DEFAULTS = {
    Role.OWNER: [],  # owner is implicitly allowed everything
    Role.MANAGER: _STAFF + [
        "connections.approve_activation", "connections.cancel", "connections.suspend",
        "connections.reactivate", "connections.complete_installation", "connections.schedule_package",
        "payments.verify", "payments.approve_correction", "billing.void_invoice", "billing.waive_installation_fee",
        "cash.reconcile", "refunds.pay", "inventory.edit", "inventory.purchase", "inventory.use_materials",
        "inventory.request_adjustment", "inventory.approve_adjustment", "inventory.inspect_returns",
        "complaints.work", "tasks.work", "transfers.approve", "employees.view", "attendance.view", "attendance.correct", "leave.approve",
        "expenses.record", "expenses.approve", "reports.view", "reports.export", "customers.export",
    ],
    Role.OFFICE_STAFF: _STAFF + ["expenses.record", "inventory.request_adjustment", "inventory.purchase"],
    Role.TECHNICIAN: ["connections.view", "connections.complete_installation", "complaints.view_assigned", "complaints.work",
                      "tasks.work", "inventory.view", "inventory.use_materials", "inventory.request_adjustment"] + _SELF,
    Role.LABOUR: ["connections.view", "complaints.view_assigned", "complaints.work", "tasks.work",
                  "inventory.use_materials"] + _SELF,
    Role.COLLECTOR: ["customers.view", "connections.view", "billing.view", "billing.create_invoice", "payments.record",
                     "receipts.issue", "cash.deposit", "tasks.work"] + _SELF,
}
