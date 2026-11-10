#!/usr/bin/env python3
"""Regenerate community-servers.json from Boranno Golder's BDIX-TESTER-BY-BORANNO lists.

Source (BSD 3-Clause, (c) 2022 Boranno Golder):
  https://github.com/boranno/BDIX-TESTER-BY-BORANNO

Usage:
  python tools/import_community_list.py <path-to-BORANNO-resource-dir> [output.json]

Reads ftp_server.txt, movie_server.txt, tv_server.txt and famous_ftp.txt,
deduplicates the URLs and writes a categorised JSON list.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("community-servers.json")

    sources = {
        "FTP": ["ftp_server.txt", "famous_ftp.txt"],
        "Movie": ["movie_server.txt"],
        "TV": ["tv_server.txt"],
    }

    entries = []
    seen = set()
    for category, files in sources.items():
        for fname in files:
            path = src_dir / fname
            if not path.is_file():
                print(f"warning: {path} not found, skipping", file=sys.stderr)
                continue
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                url = raw.strip()
                if not url or url.startswith("#"):
                    continue
                if "://" not in url:
                    url = "http://" + url
                key = re.sub(r"/+$", "", url.lower())
                if key in seen:
                    continue
                seen.add(key)
                host = urlparse(url).hostname or url
                entries.append({
                    "name": host,
                    "url": url,
                    "category": category,
                    "description": (
                        "Community list — via BDIX-TESTER-BY-BORANNO "
                        "(BSD-3-Clause, (c) 2022 Boranno Golder)"
                    ),
                })

    out_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}: {len(entries)} servers {dict(Counter(e['category'] for e in entries))}")

if __name__ == "__main__":
    main()
