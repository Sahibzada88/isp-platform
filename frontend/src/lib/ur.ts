/** Urdu phrasebook keyed by the English UI string. Anything missing simply stays English. Invoices/receipts remain English-only. */
export const UR: Record<string, string> = {
  // navigation / pages
  "Dashboard": "ڈیش بورڈ", "Customers": "صارفین", "Connections": "کنکشنز", "Invoices": "انوائسز", "Payments": "ادائیگیاں", "Receipts": "رسیدیں",
  "Complaints": "شکایات", "Tasks": "کام", "Leads": "نئے گاہک", "Inventory": "اسٹاک", "Expenses": "اخراجات", "Reports": "رپورٹس", "Employees": "ملازمین",
  "Packages": "پیکجز", "Audit log": "آڈٹ لاگ", "Data import": "ڈیٹا امپورٹ", "Notifications": "اطلاعات", "Attendance & leave": "حاضری اور چھٹی",
  "Cash & corrections": "نقد اور اصلاحات", "Transfers": "منتقلیاں", "Opening balances": "ابتدائی بقایا", "Settings": "سیٹنگز",
  // actions
  "Save": "محفوظ کریں", "Cancel": "منسوخ", "Close": "بند کریں", "Create": "بنائیں", "Search": "تلاش", "Approve": "منظور کریں", "Reject": "مسترد کریں",
  "Previous": "پچھلا", "Next": "اگلا", "Sign in": "لاگ اِن", "Sign out": "لاگ آؤٹ", "Download PDF": "پی ڈی ایف ڈاؤن لوڈ", "Print / PDF": "پرنٹ / پی ڈی ایف",
  "New customer": "نیا صارف", "Add connection": "کنکشن شامل کریں", "Edit details": "تفصیل درست کریں", "Record payment": "ادائیگی درج کریں",
  "Verify": "تصدیق کریں", "Log complaint": "شکایت درج کریں", "New task": "نیا کام", "New lead": "نیا گاہک", "New expense": "نیا خرچ", "New item": "نیا آئٹم",
  "Check in": "حاضری لگائیں", "Check out": "چھٹی کریں", "Request leave": "چھٹی کی درخواست", "Mark all read": "سب پڑھا ہوا کریں", "Report a problem": "مسئلہ بتائیں",
  "Create invoice": "انوائس بنائیں", "Mark paid": "ادا شدہ لکھیں", "Reconcile": "حساب ملائیں", "Add photo": "تصویر شامل کریں",
  // table columns / fields
  "Name": "نام", "Phone": "فون", "Village": "گاؤں", "Office": "دفتر", "Status": "حالت", "Amount": "رقم", "Total": "کل", "Date": "تاریخ", "Method": "طریقہ",
  "Reference": "حوالہ", "Customer": "صارف", "Customer ID": "صارف آئی ڈی", "Connection": "کنکشن", "Package": "پیکج", "Address": "پتہ", "Expires": "میعاد ختم",
  "Description": "تفصیل", "Category": "قسم", "Priority": "ترجیح", "Reason": "وجہ", "Notes": "نوٹس", "Registered": "اندراج", "Issued": "جاری", "Entered": "درج شدہ",
  "Password": "پاس ورڈ", "Username": "صارف نام", "Role": "کردار", "Offices": "دفاتر", "Speed": "رفتار", "Price": "قیمت", "Validity": "مدت", "Item": "آئٹم",
  "In stock": "اسٹاک میں", "Minimum": "کم از کم", "Employee": "ملازم", "From": "سے", "To": "تک", "Due": "آخری تاریخ", "Opened": "کھلی", "Assigned to": "ذمہ دار",
  "Type": "قسم", "Source": "ذریعہ", "Created": "بنایا گیا", "Receipt": "رسید", "Invoice": "انوائس", "Collector": "وصول کنندہ", "Expected": "متوقع", "Variance": "فرق",
  // statuses (after humanize)
  "Active": "فعال", "Expired": "ختم", "Cancelled": "منسوخ", "Suspended": "معطل", "Pending installation": "تنصیب باقی", "Pending approval": "منظوری باقی",
  "Open": "کھلا", "Paid": "ادا شدہ", "Void": "کالعدم", "Payment pending": "ادائیگی کی تصدیق باقی", "Pending verification": "تصدیق باقی", "Confirmed": "تصدیق شدہ",
  "Rejected": "مسترد", "Reversed": "واپس شدہ", "Pending": "زیر التواء", "Approved": "منظور شدہ", "New": "نئی", "Assigned": "تفویض", "In progress": "جاری",
  "Waiting": "انتظار میں", "Resolved": "حل شدہ", "Closed": "بند", "Reopened": "دوبارہ کھلی", "Todo": "کرنا ہے", "Done": "مکمل", "Valid": "درست",
  "Invalidated": "کالعدم", "Submitted": "جمع شدہ", "Reconciled": "حساب ملا ہوا", "Converted": "تبدیل شدہ", "Lost": "ختم", "Contacted": "رابطہ ہوا",
  "Urgent": "فوری", "High": "اونچی", "Medium": "درمیانی", "Low": "کم", "Cash": "نقد", "Easypaisa": "ایزی پیسہ", "Jazzcash": "جاز کیش", "Bank transfer": "بینک ٹرانسفر",
  // messages
  "No customers found": "کوئی صارف نہیں ملا", "No connections found": "کوئی کنکشن نہیں ملا", "No invoices found": "کوئی انوائس نہیں ملی",
  "No payments found": "کوئی ادائیگی نہیں ملی", "No complaints found": "کوئی شکایت نہیں ملی", "No tasks": "کوئی کام نہیں", "Nothing to show yet": "ابھی کچھ نہیں",
  "You're all caught up": "کوئی نئی اطلاع نہیں", "Loading": "لوڈ ہو رہا ہے",
  "Customers & connections": "صارفین اور کنکشنز", "Collections (verified money only)": "وصولیاں (صرف تصدیق شدہ رقم)", "Open complaints": "کھلی شکایات",
  "Operations": "آپریشنز", "Details": "تفصیلات", "Actions": "اقدامات", "History": "تاریخ", "Work log": "کام کا ریکارڈ", "Customer portal": "کسٹمر پورٹل",
  "My connections": "میرے کنکشن", "Bills": "بل", "Staff sign in": "عملے کا لاگ اِن", "Username or e-mail": "صارف نام یا ای میل",
};
