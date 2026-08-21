#!/usr/bin/env python3
"""Build papers.json (site root) from the Google Sheets "published paper Web site" CSV export.

Usage:
    python scripts/csv_to_papers.py [path/to/Sheet1.csv]

The CSV must have the header row:
    Author, SAIL co-author, Author notification, Conference / Journal, Status,
    paper Title, Authors, link 1, link 2, Abstract, THEME

How SAIL authors are detected:
- A global SAIL-member set is built from:
  * FIRST_TO_FULL below (known members; extend when someone joins),
  * every row's "Author" / "SAIL co-author" flags (first names are expanded
    via FIRST_TO_FULL, or resolved against that row's author list),
  * authors carrying a " (SAIL)" annotation in the "Authors" column
    (the sheet annotates affiliations as e.g. "Aristide Baratin (SAIL)"
    or "Thomas George (Mila)"; annotations are stripped from the output).
- An author is marked sail=true if their name matches a member (exact,
  double-surname, or truncated-surname match; accent-insensitive).
- Trailing "*" (equal-contribution markers) are stripped from names.
- Multi-line quoted fields (Google Sheets wraps long cells) are handled by
  the csv module.
- Rows without a "paper Title" are separators and are skipped.
- The paper list keeps the CSV row order; the author list keeps the
  published order from the "Authors" column.

Re-run after each Google Sheets export, then commit papers.json.
"""

import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

# First name (as used in the Author / SAIL co-author columns) -> full name.
# Extend this as new SAIL members join.
FIRST_TO_FULL = {
    "doha": "doha hwang",
    "yash": "yash goyal",
    "seb": "sebastien lachapelle",
    "sebastien": "sebastien lachapelle",
    "damien": "damien scieur",
    "simon": "simon lacoste-julien",
    "reza": "reza babanezhad",
    "alexia": "alexia jolicoeur-martineau",
    "aristide": "aristide baratin",
    "boris": "boris knyazev",
    "yan": "yan zhang",
    "marwa": "marwa el halabi",
    "jihye": "jihye kim",
}

AFFIL_RE = re.compile(r"^(.*?)\s*\((SAIL|Mila)\)\s*$", re.IGNORECASE)


def norm(s: str) -> str:
    """Lowercase, strip accents and collapse whitespace for name matching."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def expand_flag(flag: str) -> str:
    """Map a first-name flag to a full name; pass full names through."""
    n = norm(flag)
    return FIRST_TO_FULL.get(n, n)


def clean_name(raw: str) -> tuple[str, bool]:
    """Strip '*' contribution markers and ' (SAIL)'/' (Mila)' annotations.
    Returns (name, sail_annotated)."""
    name = raw.strip().rstrip("*").strip()
    m = AFFIL_RE.match(name)
    if m:
        return m.group(1).strip(), m.group(2).upper() == "SAIL"
    return name, False


def is_truncation(author_norm: str, member_norm: str) -> bool:
    """True if the author name is a truncated form of the member, e.g.
    'alexia jolicoeur' for 'alexia jolicoeur-martineau' (same part count,
    earlier parts equal, last token a prefix of at least 4 chars)."""
    a, f = author_norm.split(), member_norm.split()
    return (
        len(a) == len(f)
        and len(a) > 1
        and a[:-1] == f[:-1]
        and len(a[-1]) >= 4
        and f[-1].startswith(a[-1])
    )


def matches(author_norm: str, member_norm: str) -> bool:
    # Equality covers exact matches; author-startswith covers double
    # surnames (member "reza babanezhad" vs "reza babanezhad harikandeh");
    # truncation covers short surnames in the sheet ("Alexia Jolicoeur").
    return (
        author_norm == member_norm
        or author_norm.startswith(member_norm + " ")
        or is_truncation(author_norm, member_norm)
    )


def main() -> None:
    csv_path = Path(sys.argv[1] if len(sys.argv) > 1 else "published paper Web site - Sheet1.csv")
    out_path = Path(__file__).resolve().parent.parent / "papers.json"

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    header_idx = next(
        (i for i, r in enumerate(rows) if r and norm(r[0]) == "author"), None
    )
    if header_idx is None:
        sys.exit(f"error: no header row (first cell 'Author') found in {csv_path}")

    header = [norm(c) for c in rows[header_idx]]

    def col(name: str) -> int:
        try:
            return header.index(name)
        except ValueError:
            sys.exit(f"error: missing column '{name}' in header: {rows[header_idx]}")

    c_author = col("author")
    c_coauthor = col("sail co-author")
    c_notif = col("author notification")
    c_venue = col("conference / journal")
    c_title = col("paper title")
    c_authors = col("authors")
    c_link1 = col("link 1")
    c_link2 = col("link 2")

    def cell(row, i):
        return row[i].strip() if i < len(row) else ""

    # Pass 1: parse rows.
    papers, unmatched = [], []
    for row in rows[header_idx + 1 :]:
        title = cell(row, c_title)
        if not title:
            continue  # separator / empty row

        authors = []
        for a in re.split(r"[,\r\n]+", cell(row, c_authors)):
            a = a.strip().rstrip("*").strip()
            if not a or a.startswith("*"):
                continue
            name, sail_ann = clean_name(a)
            authors.append({"name": name, "sail_ann": sail_ann})
        flags = [expand_flag(cell(row, c_author))]
        flags += [expand_flag(p) for p in cell(row, c_coauthor).split(",") if p.strip()]
        flags = [f for f in flags if f]

        links = [l for l in (cell(row, c_link1), cell(row, c_link2)) if l]
        papers.append(
            {
                "title": title,
                "venue": cell(row, c_venue),
                "notification": cell(row, c_notif),
                "authors": authors,
                "links": links,
                "flags": flags,  # internal, dropped before output
            }
        )

    # Pass 2: build the global SAIL-member set.
    members: set[str] = set(FIRST_TO_FULL.values())
    for p in papers:
        for f in p["flags"]:
            parts = f.split()
            if len(parts) > 1:
                members.add(f)  # full name given in a flag column
            else:
                # first-name-only flag not in the map: resolve it against
                # this row's author list (e.g. "Namyeong" -> "namyeong kwon")
                found = [
                    norm(a["name"])
                    for a in p["authors"]
                    if norm(a["name"]).split() and norm(a["name"]).split()[0] == f
                ]
                if not found:
                    unmatched.append(f"  flag '{f}' matched no author (paper: {p['title']})")
                members.update(found)
        for a in p["authors"]:
            if a["sail_ann"]:
                members.add(norm(a["name"]))

    # Pass 3: mark authors.
    for p in papers:
        for a in p["authors"]:
            an = norm(a["name"])
            a["sail"] = a["sail_ann"] or any(matches(an, m) for m in members)
            del a["sail_ann"]
        for f in p["flags"]:
            if not any(matches(norm(a["name"]), f) for a in p["authors"]):
                unmatched.append(f"  flag '{f}' matched no author (paper: {p['title']})")
        del p["flags"]

    out_path.write_text(
        json.dumps(papers, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    sail_total = sum(a["sail"] for p in papers for a in p["authors"])
    print(f"wrote {out_path}: {len(papers)} papers, {sail_total} SAIL authors marked")
    if unmatched:
        print("warning: SAIL flags that matched no author in the list:")
        print("\n".join(sorted(set(unmatched))))


if __name__ == "__main__":
    main()
