import base64
import io
from datetime import timedelta

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.accounts.tests import login
from apps.audit.models import AuditLog
from apps.billing.tests import new_customer
from apps.core.exceptions import BusinessRuleError
from apps.core.testing import make_office, make_package, make_user, make_village
from apps.customers import services as cs
from apps.expenses import services as ex
from apps.media import images
from apps.media.models import Attachment, AttachmentData
from apps.operations import services as ops


def make_image(fmt="PNG", size=(40, 30), color=(200, 30, 30), mode="RGB", **save_kw):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format=fmt, **save_kw)
    return buf.getvalue()


def b64(raw):
    return base64.b64encode(raw).decode()


class ImagePipelineTests(TestCase):
    def test_png_jpeg_webp_are_normalised_to_jpeg_with_thumbnail_and_checksum(self):
        for fmt in ("PNG", "JPEG", "WEBP"):
            p = images.process_image(b64(make_image(fmt)))
            self.assertEqual((p.mime_type, p.original_format, p.width, p.height), ("image/jpeg", fmt, 40, 30))
            out = Image.open(io.BytesIO(base64.b64decode(p.data_b64)))
            self.assertEqual(out.format, "JPEG")
            self.assertEqual(len(p.checksum), 64)
            self.assertEqual(p.size_bytes, len(base64.b64decode(p.data_b64)))
            self.assertLessEqual(max(Image.open(io.BytesIO(base64.b64decode(p.thumb_b64))).size), 256)

    def test_data_url_prefix_is_accepted(self):
        p = images.process_image("data:image/png;base64," + b64(make_image("PNG")))
        self.assertEqual(p.original_format, "PNG")
        with self.assertRaises(BusinessRuleError):
            images.process_image("data:image/png;utf8," + b64(make_image("PNG")))

    def test_large_images_are_downscaled(self):
        p = images.process_image(b64(make_image("PNG", size=(3000, 1500))))
        self.assertEqual((p.width, p.height), (1600, 800))

    def test_transparency_is_flattened_and_exif_is_stripped(self):
        p = images.process_image(b64(make_image("PNG", mode="RGBA", color=(0, 0, 0, 0))))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(p.data_b64))).getpixel((1, 1)), (255, 255, 255))
        exif = Image.Exif(); exif[0x010F] = "SecretCameraMaker"; exif[0x0112] = 1
        raw = make_image("JPEG", exif=exif.tobytes())
        self.assertIn(b"SecretCameraMaker", raw)
        out = base64.b64decode(images.process_image(b64(raw)).data_b64)
        self.assertNotIn(b"SecretCameraMaker", out)
        self.assertEqual(len(Image.open(io.BytesIO(out)).getexif()), 0)

    def test_rejects_non_images_whatever_the_name_or_type_claims(self):
        bad = {"plain text": b"hello world", "html": b"<html><script>alert(1)</script></html>", "svg": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
               "php in jpg": b"\xff\xd8\xff<?php system($_GET['x']); ?>", "pdf": b"%PDF-1.4 fake", "empty": b""}
        for name, raw in bad.items():
            with self.assertRaises(BusinessRuleError, msg=name):
                images.process_image(b64(raw) if raw else "")

    def test_rejects_unsupported_real_images_and_truncated_data(self):
        with self.assertRaises(BusinessRuleError):
            images.process_image(b64(make_image("GIF")))
        with self.assertRaises(BusinessRuleError):
            images.process_image(b64(make_image("BMP")))
        raw = make_image("PNG", size=(200, 200))
        with self.assertRaises(BusinessRuleError):
            images.process_image(b64(raw[: len(raw) // 2]))      # truncated

    def test_rejects_bad_base64(self):
        for bad in ("not base64!!", "@@@@", "abc"):
            with self.assertRaises(BusinessRuleError):
                images.process_image(bad)

    @override_settings(IMAGE_MAX_B64_BYTES=1000)
    def test_payload_size_limit(self):
        with self.assertRaises(BusinessRuleError) as cm:
            images.process_image(b64(make_image("PNG", size=(300, 300))))
        self.assertIn("too large", str(cm.exception.detail))

    @override_settings(IMAGE_MAX_PIXELS=1000)
    def test_pixel_limit_checked_before_decoding(self):
        with self.assertRaises(BusinessRuleError) as cm:
            images.process_image(b64(make_image("PNG", size=(100, 100))))
        self.assertIn("too many pixels", str(cm.exception.detail))


class AttachmentApiTests(TestCase):
    def setUp(self):
        self.a, self.b = make_office("AAA"), make_office("BBB")
        self.va = make_village(self.a, "KHT")
        self.staff = make_user(Role.OFFICE_STAFF, [self.a])
        self.mgr = make_user(Role.MANAGER, [self.a])
        self.tech = make_user(Role.TECHNICIAN, [self.a])
        self.tech2 = make_user(Role.TECHNICIAN, [self.a])
        self.staff_b = make_user(Role.OFFICE_STAFF, [self.b])
        self.owner = make_user(Role.OWNER)
        self.expense = ex.create_expense(actor=self.staff, office=self.a, category="fuel", description="diesel", amount="900",
                                         expense_date=timezone.localdate(), payment_method="cash")
        cust = new_customer(self.staff, self.va, self.a)
        self.complaint = ops.create_complaint(actor=self.staff, customer=cust, category="cable_damage", description="cut")
        ops.assign(actor=self.staff, complaint=self.complaint, technician=self.tech)
        self.conn = cs.create_connection(actor=self.staff, customer=cust, package=make_package(), installation_address="x")

    def api(self, u):
        c = APIClient(); login(c, u); return c

    def upload(self, client, entity_type="expense", entity_id=None, data=None, kind="expense_receipt", **kw):
        return client.post("/api/v1/attachments/", {"entity_type": entity_type, "entity_id": entity_id or self.expense.pk, "kind": kind,
                                                    "filename": "bill.png", "data": data or b64(make_image("PNG")), **kw}, format="json")

    def test_upload_stores_base64_in_db_and_returns_only_metadata(self):
        r = self.upload(self.api(self.staff))
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertNotIn("data_b64", d)
        self.assertEqual((d["mime_type"], d["width"], d["original_format"], d["entity_type"]), ("image/jpeg", 40, "PNG", "expense"))
        att = Attachment.objects.get()
        stored = AttachmentData.objects.get(attachment=att)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(stored.data_b64))).format, "JPEG")   # Base64 text in the database
        self.assertEqual(att.size_bytes, len(base64.b64decode(stored.data_b64)))
        log = AuditLog.objects.get(action="attachment.uploaded")
        self.assertNotIn(stored.data_b64[:60], str(log.after))                                        # image never copied into the audit log

    def test_listings_and_metadata_never_read_the_content_table(self):
        self.upload(self.api(self.staff)); self.upload(self.api(self.staff))
        c = self.api(self.staff)
        with CaptureQueriesContext(connection) as ctx:
            lst = c.get(f"/api/v1/attachments/?entity_type=expense&entity_id={self.expense.pk}")
            one = c.get(f"/api/v1/attachments/{Attachment.objects.first().pk}/")
        self.assertEqual((lst.status_code, len(lst.json()), one.status_code), (200, 2, 200))
        self.assertFalse(any("media_attachmentdata" in q["sql"] for q in ctx.captured_queries))

    def test_image_and_thumbnail_download_as_private_jpeg(self):
        att_id = self.upload(self.api(self.staff)).json()["id"]
        c = self.api(self.mgr)
        r = c.get(f"/api/v1/attachments/{att_id}/image/")
        self.assertEqual((r.status_code, r["Content-Type"], r["Cache-Control"]), (200, "image/jpeg", "private, no-store"))
        self.assertEqual(Image.open(io.BytesIO(r.content)).size, (40, 30))
        t = c.get(f"/api/v1/attachments/{att_id}/thumbnail/")
        self.assertTrue(t.content.startswith(b"\xff\xd8"))
        self.assertEqual(APIClient().get(f"/api/v1/attachments/{att_id}/image/").status_code, 403)    # no public links

    def test_access_follows_the_owning_record_and_its_office(self):
        att_id = self.upload(self.api(self.staff)).json()["id"]
        for u in (self.staff_b, self.tech):                                                           # other office / no expense rights
            c = self.api(u)
            self.assertEqual(c.get(f"/api/v1/attachments/{att_id}/image/").status_code, 404, u.username)
            self.assertEqual(c.get(f"/api/v1/attachments/{att_id}/").status_code, 404)
            self.assertEqual(c.get(f"/api/v1/attachments/?entity_type=expense&entity_id={self.expense.pk}").status_code, 404)
            self.assertEqual(self.upload(c).status_code, 404)
        self.assertEqual(self.api(self.owner).get(f"/api/v1/attachments/{att_id}/image/").status_code, 200)
        self.assertEqual(Attachment.objects.count(), 1)

    def test_technicians_attach_only_to_their_own_complaints(self):
        kw = dict(entity_type="complaint", entity_id=self.complaint.pk, kind="complaint_attachment")
        self.assertEqual(self.upload(self.api(self.tech), **kw).status_code, 201)
        self.assertEqual(self.upload(self.api(self.tech2), **kw).status_code, 404)
        att = Attachment.objects.get()
        self.assertEqual(self.api(self.tech).get(f"/api/v1/attachments/{att.pk}/image/").status_code, 200)
        self.assertEqual(self.api(self.tech2).get(f"/api/v1/attachments/{att.pk}/image/").status_code, 404)
        self.assertEqual(self.upload(self.api(self.tech), entity_type="connection", entity_id=self.conn.pk, kind="installation_photo").status_code, 201)

    def test_bad_uploads_are_refused_and_store_nothing(self):
        c = self.api(self.staff)
        for data in (b64(b"<html>"), "###", b64(make_image("GIF"))):
            self.assertEqual(self.upload(c, data=data).status_code, 400)
        self.assertEqual(self.upload(c, entity_type="bogus").status_code, 400)
        self.assertEqual(self.upload(c, entity_id=999999).status_code, 404)
        self.assertEqual((Attachment.objects.count(), AttachmentData.objects.count()), (0, 0))

    @override_settings(IMAGE_MAX_PER_ENTITY=2)
    def test_per_record_image_limit(self):
        c = self.api(self.staff)
        self.assertEqual([self.upload(c).status_code for _ in range(3)], [201, 201, 400])

    def test_filename_is_sanitised_and_attachments_are_permanent(self):
        r = self.upload(self.api(self.staff), filename="../../etc/passwd<script>.png")
        self.assertNotIn("/", r.json()["filename"])
        self.assertNotIn("<", r.json()["filename"])
        att = Attachment.objects.get()
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            att.delete()
        self.assertEqual(self.api(self.staff).delete(f"/api/v1/attachments/{att.pk}/").status_code, 405)

    def test_oversized_request_is_rejected_cleanly(self):
        big = "A" * 4_100_000
        r = self.upload(self.api(self.staff), data=big)
        self.assertEqual(r.status_code, 400)
        self.assertIn("too large", r.json()["error"]["message"] + str(r.json()["error"]["details"]))
