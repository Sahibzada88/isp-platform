"""PDF generation with ReportLab (pure Python: works on Vercel). Generated on demand, never stored."""
from io import BytesIO

from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .models import Invoice, Receipt

BUSINESS_NAME = "Internet Service Provider"


def _local(dt):
    return timezone.localtime(dt).strftime("%d %b %Y %H:%M") if dt else "-"


def _pkr(value):
    return f"PKR {value:,.2f}"


def _stamp(c, text, x, y, size=36):
    c.saveState()
    c.setFillGray(0.85)
    c.setFont("Helvetica-Bold", size)
    c.translate(x, y)
    c.rotate(30)
    c.drawCentredString(0, 0, text)
    c.restoreState()


def invoice_pdf(invoice: Invoice) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 25 * mm
    c.setFont("Helvetica-Bold", 18); c.drawString(20 * mm, y, "INVOICE")
    c.setFont("Helvetica", 10); c.drawRightString(w - 20 * mm, y, BUSINESS_NAME)
    y -= 10 * mm
    rows = [("Invoice No", invoice.number), ("Issue date", _local(invoice.issued_at)),
            ("Customer", f"{invoice.customer.full_name} ({invoice.customer.customer_id})"),
            ("Connection", invoice.connection.connection_id), ("Office", invoice.office.name),
            ("Package", f"{invoice.package_name} - {invoice.speed_mbps} Mbps, {invoice.term_days} days"),
            ("Status", invoice.get_status_display().upper())]
    for label, value in rows:
        c.setFont("Helvetica-Bold", 10); c.drawString(20 * mm, y, label)
        c.setFont("Helvetica", 10); c.drawString(55 * mm, y, str(value)); y -= 6 * mm
    y -= 6 * mm
    c.line(20 * mm, y, w - 20 * mm, y); y -= 6 * mm
    c.setFont("Helvetica-Bold", 10); c.drawString(20 * mm, y, "Description"); c.drawRightString(w - 20 * mm, y, "Amount")
    y -= 6 * mm
    c.setFont("Helvetica", 10)
    for line in invoice.lines.all():
        c.drawString(20 * mm, y, line.description); c.drawRightString(w - 20 * mm, y, _pkr(line.amount)); y -= 6 * mm
    c.line(20 * mm, y, w - 20 * mm, y); y -= 7 * mm
    c.setFont("Helvetica-Bold", 12); c.drawString(20 * mm, y, "Total payable")
    c.drawRightString(w - 20 * mm, y, _pkr(invoice.total_amount))
    c.setFont("Helvetica", 8); c.drawString(20 * mm, 15 * mm, "An invoice is a request for payment, not a receipt.")
    if invoice.status == Invoice.Status.VOID:
        _stamp(c, "VOID", w / 2, h / 2, 80)
    c.showPage(); c.save()
    return buf.getvalue()


def receipt_pdf(receipt: Receipt) -> bytes:
    """80 mm thermal-printer-friendly roll layout."""
    p = receipt.payment
    width, height = 80 * mm, 150 * mm
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    y = height - 8 * mm
    c.setFont("Helvetica-Bold", 11); c.drawCentredString(width / 2, y, BUSINESS_NAME); y -= 6 * mm
    c.setFont("Helvetica-Bold", 10); c.drawCentredString(width / 2, y, "PAYMENT RECEIPT"); y -= 7 * mm
    c.setFont("Helvetica", 8)
    verification = ("Verified" if p.status == p.Status.CONFIRMED and p.method != p.Method.CASH
                    else "Pending verification" if p.status == p.Status.PENDING_VERIFICATION else
                    "N/A (cash)" if p.method == p.Method.CASH and p.status == p.Status.CONFIRMED else
                    "Reversed" if p.status == p.Status.REVERSED else "Rejected")
    rows = [("Receipt", receipt.number), ("Date", _local(p.paid_at)), ("Customer", p.customer.full_name),
            ("Customer ID", p.customer.customer_id), ("Connection", p.connection.connection_id),
            ("Invoice", p.invoice.number), ("Method", p.get_method_display())]
    if p.reference:
        rows.append(("Reference", p.reference))
    rows += [("Verification", verification), ("Amount", _pkr(p.amount))]
    for label, value in rows:
        c.drawString(4 * mm, y, label); c.drawRightString(width - 4 * mm, y, str(value)); y -= 5 * mm
    y -= 3 * mm
    if receipt.status == Receipt.Status.INVALIDATED:
        c.setFont("Helvetica-Bold", 12); c.drawCentredString(width / 2, y, "*** INVALIDATED ***"); y -= 5 * mm
        c.setFont("Helvetica", 7); c.drawCentredString(width / 2, y, "This receipt is no longer valid.")
    else:
        c.setFont("Helvetica", 7); c.drawCentredString(width / 2, y, "Thank you.")
    c.showPage(); c.save()
    return buf.getvalue()
