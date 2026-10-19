"""Safe image pipeline: Base64 in -> validated, normalised, re-encoded Base64 out.

Never trusts the supplied filename/MIME type. Bounded memory: payload size cap, pixel cap checked from the header
BEFORE decoding pixels, output limited to a maximum dimension.
"""
import base64
import binascii
import hashlib
import io
import warnings
from dataclasses import dataclass

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.core.exceptions import BusinessRuleError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
THUMB_SIZE = 256


@dataclass
class ProcessedImage:
    data_b64: str
    thumb_b64: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    thumb_size_bytes: int
    checksum: str
    original_format: str


def _decode(payload: str) -> bytes:
    if not isinstance(payload, str) or not payload.strip():
        raise BusinessRuleError("No image data supplied.")
    payload = payload.strip()
    if payload.startswith("data:"):  # data URL: only the part after the comma is the Base64
        header, _, payload = payload.partition(",")
        if ";base64" not in header:
            raise BusinessRuleError("Image data must be Base64 encoded.")
    if len(payload) > settings.IMAGE_MAX_B64_BYTES:
        raise BusinessRuleError(f"The image is too large (limit {settings.IMAGE_MAX_B64_BYTES // 1_000_000} MB of Base64). "
                                "Resize or compress it and try again.")
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise BusinessRuleError("The image data is not valid Base64.")


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)  # re-encoding drops EXIF/GPS and any embedded payload
    return out.getvalue()


def process_image(payload: str) -> ProcessedImage:
    raw = _decode(payload)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(raw))
            fmt = probe.format
            if fmt not in ALLOWED_FORMATS:
                raise BusinessRuleError("Unsupported image type. Use JPEG, PNG or WebP.")
            width, height = probe.size
            if width * height > settings.IMAGE_MAX_PIXELS or width < 1 or height < 1:
                raise BusinessRuleError("The image has too many pixels. Resize it and try again.")
            probe.verify()  # structural check; the object is unusable afterwards
            img = Image.open(io.BytesIO(raw))
            img.load()
    except BusinessRuleError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError, ValueError):
        raise BusinessRuleError("The file is not a valid image.")

    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    limit = settings.IMAGE_MAX_DIMENSION
    if max(img.size) > limit:
        img.thumbnail((limit, limit), Image.LANCZOS)
    main = _encode_jpeg(img, 82)
    thumb_img = img.copy()
    thumb_img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
    thumb = _encode_jpeg(thumb_img, 70)
    return ProcessedImage(
        data_b64=base64.b64encode(main).decode("ascii"), thumb_b64=base64.b64encode(thumb).decode("ascii"),
        mime_type="image/jpeg", width=img.width, height=img.height, size_bytes=len(main), thumb_size_bytes=len(thumb),
        checksum=hashlib.sha256(main).hexdigest(), original_format=fmt,
    )
