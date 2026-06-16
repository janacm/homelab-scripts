#!/usr/bin/env python3
"""
fix-abs-metadata.py — Fix malformed metadata.json files in an Audiobookshelf library.

Issues fixed:
  1. description field is a dict (e.g. {'full': '...', 'short': '...'}) instead of a plain string
  2. description field is a list instead of a plain string
  3. description is a stringified Python dict literal (from a previous bad fix)
  4. Chapter start times that are None/null

Usage:
  # Dry run (default) — show what would be fixed without changing anything
  python3 fix-abs-metadata.py /path/to/audiobooks

  # Apply fixes
  python3 fix-abs-metadata.py /path/to/audiobooks --fix

  # Also strip HTML tags from descriptions
  python3 fix-abs-metadata.py /path/to/audiobooks --fix --strip-html
"""

import json
import os
import re
import ast
import sys
import shutil
from datetime import datetime
from pathlib import Path


def strip_html(text):
    """Remove HTML tags and decode common HTML entities."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("\xa0", " ")
    text = re.sub(r"\\xa0", " ", text)
    # Collapse multiple newlines/spaces
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


def extract_description(value, do_strip_html=False):
    """Extract a clean string description from various malformed types."""
    if isinstance(value, str):
        # Check if it's a stringified Python dict literal like "{'full': '...'}"
        if value.strip().startswith("{") and "'full'" in value:
            try:
                parsed = ast.literal_eval(value)
                if isinstance(parsed, dict):
                    value = parsed.get("full") or parsed.get("short") or str(parsed)
            except (ValueError, SyntaxError):
                pass  # Not a valid Python literal, keep as-is

        if do_strip_html:
            value = strip_html(value)
        return value

    if isinstance(value, dict):
        # Prefer 'full', fall back to 'short', then any first string value
        text = value.get("full") or value.get("short")
        if text is None:
            for v in value.values():
                if isinstance(v, str):
                    text = v
                    break
        if text is None:
            text = str(value)
        if do_strip_html:
            text = strip_html(text)
        return text

    if isinstance(value, list):
        parts = [str(item) for item in value if item]
        text = " ".join(parts)
        if do_strip_html:
            text = strip_html(text)
        return text

    return str(value) if value is not None else ""


def fix_metadata_file(filepath, dry_run=True, do_strip_html=False):
    """Check and optionally fix a single metadata.json file. Returns list of issues found."""
    issues = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        issues.append(f"  ⚠ Cannot parse JSON: {e}")
        return issues

    modified = False

    # --- Fix description ---
    desc = data.get("description")
    if desc is not None and not isinstance(desc, str):
        fixed = extract_description(desc, do_strip_html)
        issues.append(f"  description: {type(desc).__name__} → string ({len(fixed)} chars)")
        if not dry_run:
            data["description"] = fixed
        modified = True
    elif isinstance(desc, str) and desc.strip().startswith("{") and "'full'" in desc:
        # Stringified Python dict from a previous bad fix
        fixed = extract_description(desc, do_strip_html)
        if fixed != desc:
            issues.append(f"  description: stringified dict → clean string ({len(fixed)} chars)")
            if not dry_run:
                data["description"] = fixed
            modified = True

    # --- Fix chapter start/end times ---
    chapters = data.get("chapters", [])
    null_starts = 0
    null_ends = 0
    for i, ch in enumerate(chapters):
        if ch.get("start") is None:
            null_starts += 1
            if not dry_run:
                ch["start"] = 0
            modified = True
        if ch.get("end") is None:
            null_ends += 1
            if not dry_run:
                # Use next chapter's start, or 0 as fallback
                if i + 1 < len(chapters) and chapters[i + 1].get("start") is not None:
                    ch["end"] = chapters[i + 1]["start"]
                else:
                    ch["end"] = ch.get("start", 0)
            modified = True
    if null_starts:
        issues.append(f"  chapters: {null_starts} null start time(s) → 0")
    if null_ends:
        issues.append(f"  chapters: {null_ends} null end time(s) → inferred from next chapter")

    # --- Write if modified ---
    if modified and not dry_run:
        backup = filepath + ".bak"
        if not os.path.exists(backup):
            shutil.copy2(filepath, backup)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    return issues


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    audiobooks_dir = sys.argv[1]
    do_fix = "--fix" in sys.argv
    do_strip_html = "--strip-html" in sys.argv

    if not os.path.isdir(audiobooks_dir):
        print(f"Error: {audiobooks_dir} is not a directory")
        sys.exit(1)

    mode = "FIXING" if do_fix else "DRY RUN (use --fix to apply)"
    print(f"Scanning {audiobooks_dir} — {mode}\n")

    total_files = 0
    total_issues = 0
    files_with_issues = 0

    for root, dirs, files in os.walk(audiobooks_dir):
        # Skip backup directories and dotfiles
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "@eaDir"]

        if "metadata.json" in files:
            filepath = os.path.join(root, "metadata.json")
            total_files += 1
            issues = fix_metadata_file(filepath, dry_run=not do_fix, do_strip_html=do_strip_html)
            if issues:
                rel = os.path.relpath(filepath, audiobooks_dir)
                print(f"📖 {rel}")
                for issue in issues:
                    print(issue)
                files_with_issues += 1
                total_issues += len(issues)

    print(f"\n{'='*50}")
    print(f"Scanned {total_files} metadata.json files")
    print(f"Found {total_issues} issue(s) in {files_with_issues} file(s)")
    if not do_fix and total_issues > 0:
        print(f"\nRun with --fix to apply changes")


if __name__ == "__main__":
    main()
