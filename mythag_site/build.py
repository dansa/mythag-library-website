"""Build the site and replace rendered PNG image URLs with cached AVIF assets."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from PIL import Image, ImageChops, features

from mythag_site.awakeners import (
    GENERATED_CONFIG,
    AwakenerValidationError,
    prepare_awakeners,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_IMAGES = ROOT / "lib" / "images"
SITE_ROOT = ROOT / "site"
CACHE_ROOT = ROOT / ".avif-cache"
ENCODER_OPTIONS: dict[str, int | str] = {
    "quality": 70,
    "speed": 6,
    "subsampling": "4:4:4",
}
WHEEL_MAX_EDGE = 640
WHEEL_RESAMPLING = Image.Resampling.LANCZOS
IMAGE_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
SOURCE_ATTRIBUTE = re.compile(
    r"(?P<prefix>(?<![-\w])src\s*=\s*)(?P<quote>['\"])(?P<url>[^'\"]+)(?P=quote)",
    re.IGNORECASE,
)
WIDTH_ATTRIBUTE = re.compile(
    r"(?<![-\w])width\s*=\s*(?P<quote>['\"])(?P<value>[^'\"]*)(?P=quote)",
    re.IGNORECASE,
)
HEIGHT_ATTRIBUTE = re.compile(
    r"(?<![-\w])height\s*=\s*(?P<quote>['\"])(?P<value>[^'\"]*)(?P=quote)",
    re.IGNORECASE,
)


def is_wheel(source: Path) -> bool:
    try:
        relative = source.resolve().relative_to(SOURCE_IMAGES.resolve())
    except ValueError:
        return False
    return bool(relative.parts) and relative.parts[0].lower() == "wheels"


def source_digest(source: Path) -> str:
    policy: dict[str, object] = {"format": "AVIF", **ENCODER_OPTIONS}
    if is_wheel(source):
        policy["resize"] = {
            "max_edge": WHEEL_MAX_EDGE,
            "resampling": WHEEL_RESAMPLING.name,
        }
    serialized_policy = json.dumps(
        policy, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    digest = hashlib.sha256(serialized_policy + b"\0")
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_cached(source: Path, cached: Path) -> bool:
    """Create or reuse an AVIF cache entry. Return True when it was encoded."""
    digest = source_digest(source)
    digest_file = cached.with_suffix(".avif.sha256")
    if cached.is_file() and digest_file.is_file():
        if digest_file.read_text(encoding="ascii").strip() == digest:
            return False

    cached.parent.mkdir(parents=True, exist_ok=True)
    temporary = cached.with_suffix(".tmp.avif")
    with Image.open(source) as original:
        with original.copy() as delivery:
            if is_wheel(source):
                delivery.thumbnail(
                    (WHEEL_MAX_EDGE, WHEEL_MAX_EDGE), WHEEL_RESAMPLING
                )
            size = delivery.size
            original_alpha = delivery.convert("RGBA").getchannel("A").copy()
            delivery.save(
                temporary,
                "AVIF",
                **ENCODER_OPTIONS,
            )

    with Image.open(temporary) as converted:
        if converted.size != size:
            raise RuntimeError(f"AVIF dimensions changed for {source}")
        converted_alpha = converted.convert("RGBA").getchannel("A")
        alpha_error = ImageChops.difference(
            original_alpha, converted_alpha
        ).getextrema()[1]
        if alpha_error > 32:
            raise RuntimeError(
                f"AVIF alpha error {alpha_error}/255 exceeded 32/255 for {source}"
            )

    temporary.replace(cached)
    digest_file.write_text(f"{digest}\n", encoding="ascii")
    return True


def generate_avif_assets() -> tuple[int, int, int, int]:
    encoded = 0
    reused = 0
    png_bytes = 0
    avif_bytes = 0

    for source in sorted(SOURCE_IMAGES.rglob("*.png")):
        relative = source.relative_to(SOURCE_IMAGES).with_suffix(".avif")
        cached = CACHE_ROOT / relative
        destination = SITE_ROOT / "images" / relative
        if encode_cached(source, cached):
            encoded += 1
        else:
            reused += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, destination)
        png_bytes += source.stat().st_size
        avif_bytes += cached.stat().st_size

    return encoded, reused, png_bytes, avif_bytes


def local_png(url: str, html_file: Path) -> Path | None:
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or not parsed.path.lower().endswith(".png"):
        return None

    decoded_path = unquote(parsed.path)
    if decoded_path.startswith("/"):
        png_file = SITE_ROOT / decoded_path.lstrip("/")
    else:
        png_file = html_file.parent / decoded_path

    try:
        png_file.resolve().relative_to(SITE_ROOT.resolve())
    except ValueError:
        return None

    if not png_file.with_suffix(".avif").is_file():
        return None

    return png_file


def avif_url(url: str, html_file: Path) -> str | None:
    if local_png(url, html_file) is None:
        return None

    parsed = urlsplit(url)
    avif_path = f"{parsed.path[:-4]}.avif"
    return urlunsplit(("", "", avif_path, parsed.query, parsed.fragment))


@lru_cache(maxsize=None)
def image_size(source: Path) -> tuple[int, int]:
    with Image.open(source) as image:
        return image.size


def add_intrinsic_dimensions(tag: str, source: Path) -> str:
    width_match = WIDTH_ATTRIBUTE.search(tag)
    height_match = HEIGHT_ATTRIBUTE.search(tag)
    if width_match and height_match:
        return tag

    source_width, source_height = image_size(source)

    if width_match:
        try:
            width = float(width_match.group("value"))
        except ValueError:
            return tag
        height = round(source_height * width / source_width)
        attributes = f' height="{height}"'
    elif height_match:
        try:
            height = float(height_match.group("value"))
        except ValueError:
            return tag
        width = round(source_width * height / source_height)
        attributes = f' width="{width}"'
    else:
        attributes = f' width="{source_width}" height="{source_height}"'

    insert_at = tag.rfind("/>")
    if insert_at == -1:
        insert_at = tag.rfind(">")
    return f"{tag[:insert_at]}{attributes}{tag[insert_at:]}"


def rewrite_html_images() -> tuple[int, int]:
    changed_files = 0
    changed_urls = 0

    for html_file in sorted(SITE_ROOT.rglob("*.html")):
        html = html_file.read_text(encoding="utf-8")
        replacements = 0

        def replace_tag(tag_match: re.Match[str]) -> str:
            nonlocal replacements
            tag = tag_match.group(0)
            source_match = SOURCE_ATTRIBUTE.search(tag)
            if source_match is None:
                return tag

            source = local_png(source_match.group("url"), html_file)
            replacement = avif_url(source_match.group("url"), html_file)
            if replacement is None:
                return tag

            rewritten_tag = SOURCE_ATTRIBUTE.sub(
                lambda match: (
                    f'{match.group("prefix")}{match.group("quote")}'
                    f'{replacement}{match.group("quote")}'
                ),
                tag,
                count=1,
            )
            replacements += 1
            assert source is not None
            return add_intrinsic_dimensions(rewritten_tag, source.with_suffix(".avif"))

        rewritten = IMAGE_TAG.sub(replace_tag, html)
        if replacements:
            html_file.write_text(rewritten, encoding="utf-8", newline="")
            changed_files += 1
            changed_urls += replacements

    return changed_files, changed_urls


def main() -> None:
    if not features.check("avif"):
        raise SystemExit("Pillow was installed without AVIF support")

    zensical = shutil.which("zensical")
    if zensical is None:
        raise SystemExit("zensical must be available on PATH")

    try:
        guides = prepare_awakeners()
    except AwakenerValidationError as error:
        raise SystemExit(str(error)) from error

    subprocess.run(
        [
            zensical,
            "build",
            "--clean",
            "--config-file",
            str(GENERATED_CONFIG),
        ],
        cwd=ROOT,
        check=True,
    )
    encoded, reused, png_bytes, avif_bytes = generate_avif_assets()
    changed_files, changed_urls = rewrite_html_images()
    reduction = (1 - avif_bytes / png_bytes) * 100 if png_bytes else 0
    print(
        f"Awakener content: {len(guides)} guides valid\n"
        "AVIF delivery: "
        f"{encoded} encoded, {reused} cached, {changed_urls} image URLs across "
        f"{changed_files} HTML files, {reduction:.1f}% fewer image bytes"
    )


if __name__ == "__main__":
    main()
