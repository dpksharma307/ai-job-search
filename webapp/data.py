"""Read-only views over the framework's own files.

The files stay the system of record: this module never writes. It tolerates
the same legacy shapes the commands tolerate (13-column tracker rows, the
legacy `no response` / `offer declined` spellings, entries without
`deadline` or `posted_date`) and never infers a missing value.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import rank_state  # noqa: E402  (reuse the framework's own parsing rules)

TRACKER = ROOT / "job_search_tracker.csv"
SEEN_JOBS = ROOT / "job_scraper" / "seen_jobs.json"
APPLICATIONS_DIR = ROOT / "documents" / "applications"
DOCUMENTS_DIR = ROOT / "documents"
DOC_CATEGORIES = {
    "cv": "CV / resume",
    "linkedin": "LinkedIn export",
    "diplomas": "Diplomas & transcripts",
    "references": "Reference letters",
    "projects": "Project summaries",
    "postings": "Saved job postings",
}

# Tracker status vocabulary (/outcome) mapped to the six buckets /html-report uses.
BUCKETS = {
    "drafted": "Drafted",
    "applied": "Applied",
    "interview": "Interviewing",
    "offer": "Offer",
    "hired": "Hired",
}
FINAL = {"hired", "rejected", "no_response", "offer_declined", "withdrawn"}
BUCKET_ORDER = ["Drafted", "Applied", "Interviewing", "Offer", "Hired", "Closed"]


def normalise_status(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_")


def bucket(status: str) -> str:
    return BUCKETS.get(normalise_status(status), "Closed")


def archive_slug(company: str, role: str) -> str:
    """documents/README.md 'Subfolder naming': lowercase, spaces to
    underscores, drop anything not a letter/digit/underscore, collapse and trim."""
    text = f"{company}_{role}".lower().replace(" ", "_")
    text = "".join(ch for ch in text if ch.isalnum() or ch == "_")
    return re.sub(r"_+", "_", text).strip("_")


@dataclass
class Application:
    id: int
    row: dict
    status: str = ""
    bucket: str = ""
    archive: Path | None = None
    files: list[dict] = field(default_factory=list)

    def __getattr__(self, name):  # template convenience: app.company etc.
        row = self.__dict__.get("row") or {}
        if name in row:
            return row[name] or ""
        raise AttributeError(name)

    @property
    def is_open(self) -> bool:
        return self.status not in FINAL

    @property
    def last_activity(self) -> date | None:
        """Latest dated note (YYYY-MM-DD) or the row date - display only."""
        dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", self.row.get("notes") or "")
        parsed = [d for d in (rank_state.parse_iso(x) for x in dates + [self.row.get("date") or ""]) if d]
        return max(parsed) if parsed else None


def read_tracker() -> list[Application]:
    if not TRACKER.is_file():
        return []
    try:
        with TRACKER.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except UnicodeDecodeError:
        raise TrackerError("Your tracker file is not saved as UTF-8. Re-save it as 'CSV UTF-8'.")
    apps = []
    for i, row in enumerate(rows, start=1):
        row = {k: (v or "") for k, v in row.items() if k}
        status = normalise_status(row.get("status"))
        app = Application(id=i, row=row, status=status, bucket=bucket(status))
        app.archive = find_archive(row.get("company", ""), row.get("role", ""))
        apps.append(app)
    return apps


class TrackerError(Exception):
    pass


def find_archive(company: str, role: str) -> Path | None:
    slug = archive_slug(company, role)
    if slug and (APPLICATIONS_DIR / slug).is_dir():
        return APPLICATIONS_DIR / slug
    return None


def application_files(app: Application) -> list[dict]:
    """PDFs and archive documents belonging to one tracker row."""
    files = []
    for col, label in (("cv_file", "CV"), ("cover_letter_file", "Cover letter")):
        src = app.row.get(col, "").strip()
        if not src:
            continue
        pdf = (ROOT / src).with_suffix(".pdf")
        files.append({"label": label, "path": rel(pdf), "exists": pdf.is_file(), "kind": "pdf"})
    if app.archive:
        for p in sorted(app.archive.iterdir()):
            if p.is_file():
                files.append({"label": p.name, "path": rel(p), "exists": True,
                              "kind": "pdf" if p.suffix.lower() == ".pdf" else
                              "markdown" if p.suffix.lower() in (".md", ".txt") else "other"})
    return files


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def read_jobs() -> list[dict]:
    if not SEEN_JOBS.is_file():
        return []
    try:
        doc = json.loads(SEEN_JOBS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    seen = doc.get("seen") if isinstance(doc, dict) and "seen" in doc else doc
    if not isinstance(seen, dict):
        return []
    try:
        tracked = rank_state.tracker_pairs(TRACKER)
    except SystemExit:  # tracker not UTF-8: read_tracker reports it
        tracked = set()
    jobs = []
    for key, entry in seen.items():
        if not isinstance(entry, dict):
            continue
        job = dict(entry)
        job["key"] = key
        job["location_verdict"] = rank_state.entry_location_verdict(entry)
        if entry.get("location") in ("PASS", "FAIL", "FLAG"):
            job["location"] = ""  # legacy verdict stored under `location`, not a place
        score = entry.get("rank_score")
        job["score"] = score if isinstance(score, (int, float)) else None
        job["tracked"] = (rank_state.norm(entry.get("company")), rank_state.norm(entry.get("title"))) in tracked
        job["deadline_date"] = rank_state.parse_iso(entry.get("deadline"))
        jobs.append(job)
    jobs.sort(key=lambda j: (j["score"] is None, -(j["score"] or 0), j.get("first_seen") or ""), reverse=False)
    return jobs


def profile_status() -> dict:
    """How much of the profile is still template placeholders."""
    claude_md = ROOT / "CLAUDE.md"
    text = claude_md.read_text(encoding="utf-8") if claude_md.is_file() else ""
    placeholders = sorted(set(re.findall(r"\[YOUR_[A-Z_]+\]", text)))
    name = ""
    m = re.search(r"\*\*Name:\*\*\s*(.+)", text)
    if m and "[YOUR_" not in m.group(1):
        name = m.group(1).strip()
    return {"placeholders": placeholders, "is_set_up": not placeholders, "name": name}


def documents() -> dict[str, list[dict]]:
    out = {}
    for cat in DOC_CATEGORIES:
        d = DOCUMENTS_DIR / cat
        items = []
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_file() and p.name != ".gitkeep":
                    items.append({"name": p.name, "path": rel(p), "size": p.stat().st_size})
        out[cat] = items
    return out


def dashboard(today: date | None = None) -> dict:
    today = today or date.today()
    apps = read_tracker()
    jobs = read_jobs()
    counts = {b: 0 for b in BUCKET_ORDER}
    for a in apps:
        counts[a.bucket] += 1
    soon = today + timedelta(days=14)
    deadlines = []
    for a in apps:
        d = rank_state.parse_iso(a.row.get("deadline"))
        if a.status == "drafted" and d and today <= d <= soon:
            deadlines.append({"what": f"{a.company} — {a.role}", "date": d, "link": f"/applications/{a.id}",
                              "note": "Drafted, not yet submitted"})
    for j in jobs:
        d = j["deadline_date"]
        if j.get("status") == "ranked" and not j["tracked"] and d and today <= d <= soon \
                and (j["score"] or 0) >= 60:
            deadlines.append({"what": f"{j.get('company')} — {j.get('title')}", "date": d,
                              "link": f"/jobs#{j['key']}", "note": f"Score {j['score']}"})
    deadlines.sort(key=lambda x: x["date"])
    quiet = [a for a in apps if a.status == "applied" and a.last_activity
             and (today - a.last_activity).days >= 10]
    interviews = [a for a in apps if a.status in ("interview", "offer")]
    new_jobs = sum(1 for j in jobs if j.get("status") in ("new", "unreachable"))
    top_jobs = [j for j in jobs if j["score"] is not None and j.get("status") == "ranked"
                and not j["tracked"]][:5]
    return {"counts": counts, "total": len(apps), "deadlines": deadlines, "quiet": quiet,
            "interviews": interviews, "new_jobs": new_jobs, "top_jobs": top_jobs,
            "jobs_total": len(jobs)}


def insights() -> dict:
    apps = read_tracker()
    counts = {b: 0 for b in BUCKET_ORDER}
    by_month: dict[str, int] = {}
    unknown = set()
    for a in apps:
        counts[a.bucket] += 1
        if a.bucket == "Closed" and a.status not in FINAL:
            unknown.add(a.row.get("status", ""))
        d = rank_state.parse_iso(a.row.get("date"))
        if d:
            by_month[d.strftime("%Y-%m")] = by_month.get(d.strftime("%Y-%m"), 0) + 1
    sent = sum(1 for a in apps if a.status != "drafted")
    reached_interview = sum(1 for a in apps if a.bucket in ("Interviewing", "Offer", "Hired")
                            or "interview" in (a.row.get("notes") or "").lower())
    reports = []
    for pattern in ("reports/*.html", "upskill/report-*.md"):
        for p in sorted(ROOT.glob(pattern), reverse=True):
            reports.append({"name": p.name, "path": rel(p)})
    return {"counts": counts, "total": len(apps), "sent": sent, "reached_interview": reached_interview,
            "by_month": sorted(by_month.items()), "unknown_statuses": sorted(unknown), "reports": reports}
