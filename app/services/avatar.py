"""Avatar image and display helpers."""

from __future__ import annotations

from io import BytesIO

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app import config
from app.services.users import display_user_name


def user_initials(user) -> str:
    """Compute initials for user avatar fallbacks."""
    display_name = display_user_name(user)
    words = [word for word in display_name.replace("@", " ").replace(".", " ").split() if word]
    if not words:
        return "U"
    if len(words) == 1:
        return words[0][:2].upper()
    return f"{words[0][0]}{words[-1][0]}".upper()


def user_has_avatar(user) -> bool:
    """Return whether a user row contains avatar data."""
    return bool(user is not None and "avatar_data" in user.keys() and user["avatar_data"])


def parse_avatar_size(raw_size: str) -> int:
    """Clamp a requested avatar size to supported bounds."""
    try:
        requested_size = int(raw_size)
    except (TypeError, ValueError):
        requested_size = config.AVATAR_SIZE_DEFAULT_PX
    return max(config.AVATAR_SIZE_MIN_PX, min(requested_size, config.AVATAR_SIZE_MAX_PX))


async def process_avatar_upload(upload_file: UploadFile | None, *, requested_size: int = config.AVATAR_SIZE_DEFAULT_PX) -> bytes:
    """Validate and resize an uploaded avatar image."""
    if not upload_file or not upload_file.filename:
        raise ValueError("Choose an image file to upload.")
    upload_bytes = await upload_file.read()
    if not upload_bytes:
        raise ValueError("Choose an image file to upload.")
    if len(upload_bytes) > config.AVATAR_UPLOAD_MAX_BYTES:
        raise ValueError("Profile images must be 2 MB or smaller.")

    size = parse_avatar_size(str(requested_size))
    try:
        with Image.open(BytesIO(upload_bytes)) as image:
            image = image.convert("RGB")
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (size, size), (255, 255, 255))
            left = (size - image.width) // 2
            top = (size - image.height) // 2
            canvas.paste(image, (left, top))
            output = BytesIO()
            canvas.save(output, format="WEBP", quality=78, method=6)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Upload a PNG, JPEG, GIF, or WebP image.") from exc

    return output.getvalue()
