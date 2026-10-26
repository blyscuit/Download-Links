#!/usr/bin/env python3

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

# Match HTTP and HTTPS URLs.
URL_PATTERN = re.compile(r'https?://[^\s<>"\'\]\[{}]+', re.IGNORECASE)

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
CHUNK_SIZE = 1024 * 64


def extract_http_links(file_path: Path) -> list[str]:
    """Extract unique HTTP/HTTPS links from a text-based file."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"Error reading {file_path}: {exc}", file=sys.stderr)
        return []

    links = []
    seen = set()

    for match in URL_PATTERN.findall(content):
        # Remove common punctuation that may follow a URL.
        url = match.rstrip(".,;:!?")

        if url not in seen:
            seen.add(url)
            links.append(url)

    return links


def get_filename(url: str, index: int) -> str:
    """Determine a filename from the URL, with a fallback."""
    parsed = urlparse(url)
    filename = unquote(Path(parsed.path).name)

    if not filename or filename in {".", ".."}:
        filename = f"download_{index:03d}.bin"

    # Avoid path traversal and directory separators.
    filename = filename.replace("/", "_").replace("\\", "_")

    return filename


def download_file(
    session: requests.Session,
    url: str,
    destination: Path,
    index: int,
) -> bool:
    """Download a URL, skipping files that already exist."""
    filename = get_filename(url, index)
    output_path = destination / filename

    # Prevent overwriting different URLs with the same filename.
    if output_path.exists():
        stem = output_path.stem
        suffix = output_path.suffix
        output_path = destination / f"{stem}_{index}{suffix}"

    if output_path.exists():
        print(f"[SKIP] Already exists: {output_path}")
        return True

    for attempt in range(1, MAX_RETRIES + 1):
        temporary_path = output_path.with_name(output_path.name + ".part")

        try:
            with session.get(
                url,
                stream=True,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            ) as response:
                response.raise_for_status()

                with temporary_path.open("wb") as output:
                    for chunk in response.iter_content(
                        chunk_size=CHUNK_SIZE
                    ):
                        if chunk:
                            output.write(chunk)

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

    print(f"\nDownload complete: {success} successful, {failed} failed.")
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
        print("\nDownload disabled. Use --download to download the files.")


if __name__ == "__main__":
    main()
