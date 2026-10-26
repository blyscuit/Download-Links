#!/usr/bin/env python3

import argparse
import mimetypes
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

# Match HTTP and HTTPS URLs, stopping at binary/control characters.
URL_PATTERN = re.compile(
    r'https?://[^\s<>"\'\]\[{}\x00-\x20\x7f-\xff]+',
    re.IGNORECASE,
)

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
CHUNK_SIZE = 1024 * 64


def extract_http_links(file_path: Path) -> list[str]:
    """Extract unique HTTP/HTTPS links from a text-based file."""
    try:
        raw_content = file_path.read_bytes()
        content = raw_content.decode(
            "utf-8", errors="replace"
        ).replace("\x00", "")
    except OSError as exc:
        print(f"Error reading {file_path}: {exc}", file=sys.stderr)
        return []

    links = []
    seen = set()

    for match in URL_PATTERN.findall(content):
        url = match.rstrip(".,;:!?")

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            continue

        if url not in seen:
            seen.add(url)
            links.append(url)

    return links


def get_filename(url: str, index: int) -> str:
    """Get a sanitized filename from the URL."""
    parsed = urlparse(url)
    filename = unquote(Path(parsed.path).name)

    if not filename or filename in {".", ".."}:
        filename = f"download_{index:03d}"

    # Remove unsafe filename characters.
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    filename = filename.strip("._") or f"download_{index:03d}"

    return filename


def get_extension(content_type: str, file_header: bytes) -> str:
    """Determine the file extension from Content-Type or file signature."""
    content_type = content_type.split(";")[0].strip().lower()

    # Use the HTTP Content-Type header first.
    extension = mimetypes.guess_extension(content_type)

    if extension == ".jpe":
        extension = ".jpg"

    if extension and extension != ".bin":
        return extension

    # Fall back to identifying common file formats by their signatures.
    if file_header.startswith(b"\xff\xd8\xff"):
        return ".jpg"

    if file_header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"

    if file_header.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"

    if (
        len(file_header) >= 12
        and file_header[:4] == b"RIFF"
        and file_header[8:12] == b"WEBP"
    ):
        return ".webp"

    if file_header.startswith(b"%PDF-"):
        return ".pdf"

    if file_header.startswith(b"PK\x03\x04"):
        return ".zip"

    if file_header.startswith(b"BM"):
        return ".bmp"

    if file_header.startswith(b"II*\x00") or file_header.startswith(b"MM\x00*"):
        return ".tif"

    return ".bin"


def download_file(
    session: requests.Session,
    url: str,
    destination: Path,
    index: int,
) -> bool:
    """Download a URL and determine its extension when necessary."""
    filename = get_filename(url, index)
    original_path = Path(filename)

    stem = original_path.stem
    suffix = original_path.suffix

    # Build a unique base filename to prevent overwriting other downloads.
    output_path = destination / filename

    if output_path.exists():
        output_path = destination / f"{stem}_{index}{suffix}"

    # If the URL has an extension, check whether the file already exists.
    if suffix and output_path.exists() and output_path.stat().st_size > 0:
        print(f"[SKIP] Already exists: {output_path}")
        return True

    for attempt in range(1, MAX_RETRIES + 1):
        temporary_path = destination / f".download_{index:03d}.part"

        try:
            with session.get(
                url,
                stream=True,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            ) as response:
                response.raise_for_status()

                content_type = response.headers.get(
                    "Content-Type", ""
                )

                file_header = b""

                with temporary_path.open("wb") as output:
                    for chunk in response.iter_content(
                        chunk_size=CHUNK_SIZE
                    ):
                        if not chunk:
                            continue

                        if len(file_header) < 32:
                            needed = 32 - len(file_header)
                            file_header += chunk[:needed]

                        output.write(chunk)

            # Determine the extension if the URL doesn't provide one.
            if not suffix:
                extension = get_extension(content_type, file_header)
                output_path = destination / f"{filename}{extension}"

                if (
                    output_path.exists()
                    and output_path.stat().st_size > 0
                ):
                    temporary_path.unlink(missing_ok=True)
                    print(f"[SKIP] Already exists: {output_path}")
                    return True

                # Avoid overwriting another file with the same name.
                if output_path.exists():
                    output_path = destination / (
                        f"{stem}_{index}{extension}"
                    )

            temporary_path.replace(output_path)

            print(f"[OK] {url}\n     -> {output_path}")
            return True

        except (requests.RequestException, OSError) as exc:
            temporary_path.unlink(missing_ok=True)

            print(
                f"[ERROR] Attempt {attempt}/{MAX_RETRIES}: "
                f"{url}\n        {exc}",
                file=sys.stderr,
            )

            if attempt < MAX_RETRIES:
                time.sleep(attempt)

    return False


def download_all_files(
    links: list[str],
    output_dir: Path,
) -> None:
    """Download all extracted links into the specified directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "SteamWorkshopLinkExtractor/1.0"}
    )

    success = 0
    failed = 0

    try:
        for index, url in enumerate(links, start=1):
            print(f"\n[{index}/{len(links)}] Downloading...")

            if download_file(session, url, output_dir, index):
                success += 1
            else:
                failed += 1
    finally:
        session.close()

    print(
        f"\nDownload complete: {success} successful, "
        f"{failed} failed."
    )
    print(f"Output directory: {output_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract HTTP/HTTPS links from a Steam Workshop file. "
            "Downloads are disabled by default."
        )
    )

    parser.add_argument(
        "file",
        type=Path,
        help="Path to the Steam Workshop file",
    )

    parser.add_argument(
        "--download",
        action="store_true",
        help="Download all extracted links",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("workshop_downloads"),
        help="Download directory (default: workshop_downloads)",
    )

    args = parser.parse_args()

    if not args.file.is_file():
        parser.error(f"File does not exist: {args.file}")

    links = extract_http_links(args.file)

    print(f"Found {len(links)} unique HTTP/HTTPS links:\n")

    for index, url in enumerate(links, start=1):
        print(f"{index}. {url}")

    if args.download:
        if not links:
            print("\nNo links to download.")
            return

        print(f"\nDownloading files to: {args.output}")
        download_all_files(links, args.output)
    else:
        print(
            "\nDownload disabled. Use --download to download the files."
        )


if __name__ == "__main__":
    main()
