#!/usr/bin/env python3
"""Check _data/publications.yaml against the sources of record.

Exists because a batch of entries once went live with plausible-but-invented
author given names (real surnames, wrong first names) and sat there for months.
That class of error is invisible on the page but obvious against arXiv/Crossref,
so this compares every entry it can resolve and reports what disagrees.

Checks, per paper:
  - the link resolves (and isn't stuck in a redirect loop)
  - title matches the source, ignoring case/punctuation/whitespace
  - author surnames match the source, in order
  - the `year` field matches the year at the end of `venue`

Prints a markdown report to stdout. Exit code is 0 unless --strict is passed.
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import yaml

UA = "miba.dev-publication-check (+https://miba.dev)"
TIMEOUT = 30
PAUSE = 1.0        # between papers
ARXIV_PAUSE = 3.0  # arXiv asks for one request every 3s and 429s if you rush it
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", re.I)
DOI_RE = re.compile(r"(?:doi\.org/|/doi/(?:abs/|full/)?)(10\.\d{4,9}/[^\s?#]+)", re.I)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\.\s*$")


def fetch(url, accept=None, retries=5):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if accept:
        req.add_header("Accept", accept)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(min(60, ARXIV_PAUSE * 2 ** (attempt + 1)))
                continue
            raise


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))


def norm_title(s):
    s = strip_tags(s).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def surnames(author_field):
    """Surnames, in order, from the site's 'A. Name, B. Other' author string."""
    out = []
    for raw in strip_tags(author_field).split(","):
        name = raw.strip().strip("*").replace("‡", "").strip()
        if not name or name.lower().startswith(("et al", "...")):
            continue
        # drop parenthetical notes like "(3289 authors ordered randomly)"
        name = re.sub(r"\(.*?\)", "", name).strip()
        parts = [w for w in name.split() if norm_name(w) not in NAME_SUFFIXES]
        if parts:
            out.append(norm_name(parts[-1]))
    return out


def same_name(a, b):
    """Tolerate multi-word surnames: the site renders 'Ana Pastore y Piontti' and
    takes the last token, while Crossref has family='Pastore y Piontti'."""
    return a == b or a.endswith(b) or b.endswith(a)


def same_titles(a, b):
    """Publisher metadata is often truncated (ACM drops subtitles) or a preprint
    is one revision behind. Treat a clean prefix match as the same paper."""
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return True
    return a == b or a.startswith(b) or b.startswith(a)


def norm_name(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", s.lower())


# ---------------------------------------------------------------- sources

def from_arxiv(arxiv_id):
    url = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)
    root = ET.fromstring(fetch(url))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None or entry.find("a:id", ns) is None:
        return None
    title = entry.findtext("a:title", default="", namespaces=ns)
    names = [
        norm_name(a.findtext("a:name", default="", namespaces=ns).split()[-1])
        for a in entry.findall("a:author", ns)
        if a.findtext("a:name", default="", namespaces=ns).strip()
    ]
    return {"source": f"arXiv:{arxiv_id}", "title": title, "surnames": names}


def from_crossref(doi):
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    msg = json.loads(fetch(url))["message"]
    titles = msg.get("title") or []
    names = [
        norm_name(a.get("family", ""))
        for a in msg.get("author", [])
        if a.get("family")
    ]
    return {"source": f"doi:{doi}", "title": titles[0] if titles else "", "surnames": names}


def link_status(url):
    """Return (ok, note). Follows redirects; catches the redirect loops that a
    plain status check reports as a benign 302."""
    try:
        fetch(url)
        return True, ""
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):  # publisher bot-walls, not a broken link
            return True, f"HTTP {e.code} (bot-blocked, not verified)"
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"{type(e.reason).__name__}: {e.reason}"
    except Exception as e:  # redirect loops surface here
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- checks

def check(paper, unverified, verify_links=True):
    problems = []
    title = paper.get("title", "")
    url = paper.get("paper_pdf") or ""

    venue = paper.get("venue", "")
    m = YEAR_RE.search(venue)
    if not m:
        problems.append(f"venue has no trailing year: `{venue}`")
    elif str(paper.get("year")) != m.group(1):
        problems.append(f"year field is {paper.get('year')} but venue says {m.group(1)}")

    if not url:
        return problems  # unlinked entries aren't rendered; nothing to verify

    if verify_links:
        ok, note = link_status(url)
        if not ok:
            problems.append(f"link is broken - {note}: {url}")
        elif note:
            problems.append(f"link not verified - {note}: {url}")

    ref = None
    try:
        m = ARXIV_RE.search(url)
        if m:
            time.sleep(ARXIV_PAUSE)
            ref = from_arxiv(m.group(1))
        else:
            m = DOI_RE.search(url)
            if m:
                ref = from_crossref(m.group(1).rstrip("."))
    except Exception as e:
        unverified.append(f"{strip_tags(title)} - {type(e).__name__}: {e}")
        return problems

    if not ref:
        return problems

    if not same_titles(title, ref["title"]):
        problems.append(
            f"title differs from {ref['source']}\n"
            f"    site:   {strip_tags(title)}\n"
            f"    source: {' '.join(strip_tags(ref['title']).split())}"
        )

    site_names = surnames(paper.get("authors", ""))
    ref_names = ref["surnames"]
    # very long author lists on the site are deliberately elided
    if ref_names and len(site_names) < len(ref_names) - 2 and len(ref_names) > 50:
        pass
    elif ref_names and not (len(site_names) == len(ref_names)
                            and all(same_name(x, y) for x, y in zip(site_names, ref_names))):
        extra = [n for n in site_names if not any(same_name(n, r) for r in ref_names)]
        missing = [n for n in ref_names if not any(same_name(n, s2) for s2 in site_names)]
        detail = []
        if extra:
            detail.append("not on the paper: " + ", ".join(extra))
        if missing:
            detail.append("missing from the site: " + ", ".join(missing))
        if not detail:
            detail.append("same authors, different order")
        problems.append(f"authors differ from {ref['source']} - " + "; ".join(detail))

    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="_data/publications.yaml")
    ap.add_argument("--skip-links", action="store_true",
                    help="skip link resolution (much faster; API checks still run)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when anything is reported")
    args = ap.parse_args()

    papers = yaml.safe_load(open(args.data, encoding="utf-8"))["papers"]

    findings, unverified = [], []
    for i, p in enumerate(papers):
        probs = check(p, unverified, verify_links=not args.skip_links)
        if probs:
            findings.append((p, probs))
        time.sleep(PAUSE)

    def footnote():
        if unverified:
            print(f"\n<details><summary>{len(unverified)} entries could not be checked "
                  "against a source (API unreachable or rate-limited)</summary>\n")
            for u in unverified:
                print(f"- {u}")
            print("\n</details>")

    print(f"Checked {len(papers)} entries in `{args.data}`.\n")
    if not findings:
        print("No drift found.")
        footnote()
        return 0

    print(f"**{len(findings)} of them need a look.**\n")
    for p, probs in findings:
        print(f"### {strip_tags(p.get('title',''))}")
        print(f"`{p.get('venue','')}`\n")
        for pr in probs:
            print(f"- {pr}")
        print()
    print("---")
    print("Author and title comparisons come from the arXiv API and Crossref. "
          "A mismatch is not automatically an error - preprint and published "
          "versions legitimately differ - but each one is worth reading.")
    footnote()
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
