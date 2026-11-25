"""Registry of the framework's commands as the web app exposes them.

The app never re-implements a command: each entry only describes the form the
user fills in and how its answers become the slash-command string that
`claude -p` runs unchanged. Validation here is about shape (one line, sane
length, known choices), never about job-search rules - those live in
.claude/commands/*.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent.parent / ".claude" / "settings.json"

# Tools every run may use without a prompt. `claude -p` cannot show a
# permission prompt, so anything not granted here or in .claude/settings.json
# is refused and Claude reports it. `ask` rules in settings.json stay refusals.
BASE_TOOLS = [
    "Read", "Glob", "Grep", "Edit", "Write", "WebFetch", "WebSearch",
    "Task", "Agent", "Skill", "TodoWrite",
    "Bash(mkdir -p:*)",
]

LATEX_TOOLS = [
    "Bash(lualatex:*)", "Bash(xelatex:*)", "Bash(pdftotext:*)",
    "Bash(cd cv:*)", "Bash(cd cover_letters:*)", "Bash(cd ../cover_letters:*)",
    "Bash(rm -f cv/main_:*)",
]


@dataclass
class Field:
    name: str
    label: str
    kind: str = "text"  # text | textarea | url | number | checkbox | select
    help: str = ""
    required: bool = False
    options: list[tuple[str, str]] = field(default_factory=list)
    placeholder: str = ""


@dataclass
class Command:
    name: str
    title: str
    verb: str
    summary: str
    group: str
    fields: list[Field] = field(default_factory=list)
    extra_tools: list[str] = field(default_factory=list)
    advanced: bool = False
    note: str = ""

    def build(self, values: dict) -> str:
        """Turn validated form values into the exact slash command."""
        return BUILDERS[self.name](clean(values, self.fields))


class InvalidInput(ValueError):
    pass


SINGLE_LINE = re.compile(r"^[^\r\n\x00]*$")


def clean(values: dict, fields: list[Field]) -> dict:
    out: dict = {}
    for f in fields:
        raw = values.get(f.name)
        if f.kind == "checkbox":
            out[f.name] = raw in (True, "on", "true", "1", 1)
            continue
        text = "" if raw is None else str(raw).strip()
        if f.kind != "textarea" and not SINGLE_LINE.match(text):
            raise InvalidInput(f"{f.label}: must be a single line")
        if "\x00" in text:
            raise InvalidInput(f"{f.label}: invalid character")
        limit = 60000 if f.kind == "textarea" else 500
        if len(text) > limit:
            raise InvalidInput(f"{f.label}: too long")
        if f.required and not text:
            raise InvalidInput(f"{f.label} is required")
        if text and f.kind == "number":
            if not text.isdigit() or not (1 <= int(text) <= 1000):
                raise InvalidInput(f"{f.label}: enter a whole number between 1 and 1000")
        if text and f.kind == "url" and not re.match(r"^https?://\S+$", text):
            raise InvalidInput(f"{f.label}: enter a full link starting with http:// or https://")
        if text and f.kind == "select" and text not in {v for v, _ in f.options}:
            raise InvalidInput(f"{f.label}: unknown choice")
        out[f.name] = text
    return out


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _apply(v: dict) -> str:
    url, text = v.get("posting_url", ""), v.get("posting_text", "")
    if bool(url) == bool(text):
        raise InvalidInput("Give either a link to the job posting or the posting text, not both")
    return f"/apply {url or text}"


def _rank(v: dict) -> str:
    return _join(
        "/rank",
        v.get("focus", ""),
        "--all" if v.get("all") else "",
        f"--limit {v['limit']}" if v.get("limit") else "",
        f"--top {v['top']}" if v.get("top") else "",
    )


def _setup(v: dict) -> str:
    section = v.get("section", "")
    return f"/setup --section {section}" if section else "/setup"


def _outcome(v: dict) -> str:
    mode, target, days = v.get("mode", ""), v.get("target", ""), v.get("days", "")
    if mode == "followup":
        return _join("/outcome followup", target or days)
    if mode == "stale":
        return _join("/outcome stale", days)
    return _join("/outcome", target)


def _gmail(v: dict) -> str:
    since = v.get("since", "")
    if since and not re.match(r"^\d{4}-\d{2}-\d{2}$", since):
        raise InvalidInput("Since: use the format YYYY-MM-DD")
    return _join("/gmail-sync", v.get("target", ""), f"since {since}" if since else "")


def _notion(v: dict) -> str:
    return _join(
        "/notion-sync",
        f"--min-score {v['min_score']}" if v.get("min_score") else "",
        "--all" if v.get("all") else "",
        "--rebuild" if v.get("rebuild") else "",
    )


def _add_template(v: dict) -> str:
    return _join("/add-template", v.get("mode", ""), v.get("path", ""))


def _reset(v: dict) -> str:
    return f"/reset {v['scope']}"


BUILDERS = {
    "setup": _setup,
    "apply": _apply,
    "scrape": lambda v: "/scrape",
    "rank": _rank,
    "interview": lambda v: _join("/interview", v.get("target", "")),
    "outcome": _outcome,
    "expand": lambda v: "/expand",
    "gmail-sync": _gmail,
    "notion-sync": _notion,
    "html-report": lambda v: "/html-report",
    "upskill": lambda v: _join("/upskill", v.get("posting_url", "")),
    "add-portal": lambda v: _join("/add-portal", v.get("portal_url", "")),
    "add-template": _add_template,
    "reset": _reset,
}

TARGET = Field("target", "Company (and role)", placeholder="e.g. Acme ML engineer",
               help="Leave empty and Claude will list your applications and ask which one.")

COMMANDS: dict[str, Command] = {c.name: c for c in [
    Command(
        "setup", "Set up my profile", "Start setup",
        "Build your profile from your documents, a single CV, or a guided interview. "
        "Claude will ask which way you prefer.",
        "Profile",
        [Field("section", "Only update one section (optional)", "select", options=[
            ("", "Full setup"), ("identity", "Identity & contact"), ("education", "Education"),
            ("experience", "Professional experience"), ("skills", "Technical skills"),
            ("publications", "Publications & awards"), ("behavioral", "Behavioral profile"),
            ("goals", "Career goals & preferences"), ("references", "References"),
            ("search", "Job search configuration")])],
        extra_tools=["Bash(git remote get-url:*)"],
    ),
    Command(
        "apply", "Prepare an application", "Prepare application",
        "Check a job posting, evaluate your fit, then write a tailored CV and cover letter "
        "and check them. Claude asks before drafting.",
        "Applications",
        [Field("posting_url", "Link to the job posting", "url", placeholder="https://..."),
         Field("posting_text", "...or paste the posting text", "textarea",
               help="Use this when the site needs a login or blocks automatic reading.")],
        extra_tools=LATEX_TOOLS,
    ),
    Command(
        "scrape", "Find new jobs", "Find jobs",
        "Search your job boards for new postings that match your profile.",
        "Jobs", extra_tools=["Bash(bun --version)"],
    ),
    Command(
        "rank", "Score jobs", "Score jobs",
        "Score the jobs found so far against your profile and build a shortlist.",
        "Jobs",
        [Field("focus", "Focus on (optional)", placeholder="e.g. data science"),
         Field("all", "Re-score everything I haven't applied to", "checkbox",
               help="Useful after you've updated your profile."),
         Field("limit", "How many jobs to score", "number", placeholder="10"),
         Field("top", "Shortlist size", "number", placeholder="5")],
    ),
    Command(
        "interview", "Prepare for an interview", "Prepare",
        "Build a prep pack for a scheduled interview, then optionally practise with a mock interview.",
        "Applications", [TARGET],
    ),
    Command(
        "outcome", "Log an update", "Log update",
        "Record what happened: submitted, interview invite, offer, rejection. "
        "Can also draft follow-up messages or tidy up quiet applications.",
        "Applications",
        [Field("mode", "What do you want to do?", "select", options=[
            ("update", "Log an update on an application"),
            ("followup", "Draft follow-ups for quiet applications"),
            ("stale", "Tidy up applications with no news for a long time")]),
         TARGET,
         Field("days", "Days without news (optional)", "number",
               help="Follow-ups default to 10 days, tidy-up to 60.")],
    ),
    Command(
        "expand", "Discover more skills", "Discover",
        "Look through your documents and public profiles for skills your profile doesn't list yet. "
        "Nothing is added without your approval.",
        "Profile",
    ),
    Command(
        "gmail-sync", "Check my inbox", "Check inbox",
        "Scan Gmail for replies about your applications and propose status updates. "
        "Nothing changes until you approve.",
        "Applications",
        [TARGET, Field("since", "Look back from (optional)", placeholder="YYYY-MM-DD")],
        extra_tools=["mcp__claude_ai_Gmail"],
        note="Needs the Gmail connector enabled for your Claude account.",
    ),
    Command(
        "notion-sync", "Send to Notion", "Sync",
        "Publish your ranked jobs and applications to a Notion database (one-way).",
        "Insights",
        [Field("min_score", "Minimum score (optional)", "number", placeholder="60"),
         Field("all", "Include every ranked job", "checkbox"),
         Field("rebuild", "Rebuild existing pages", "checkbox")],
        extra_tools=["mcp__notion"],
        note="Needs the Notion MCP server connected in Claude Code.",
    ),
    Command(
        "html-report", "Create offline report", "Create report",
        "Build a self-contained dashboard file of your applications you can share or keep.",
        "Insights",
    ),
    Command(
        "upskill", "Find skill gaps", "Analyse",
        "Compare the jobs you're targeting with your profile and get a learning plan.",
        "Insights",
        [Field("posting_url", "Analyse one job only (optional link)", "url")],
    ),
    Command(
        "add-portal", "Add a job board", "Start",
        "Build a search connector for a job site in your market and test it live.",
        "Settings",
        [Field("portal_url", "Job board address (optional)", "url")],
        extra_tools=["Bash(bun install)", "Bash(bun run typecheck)", "Bash(bun run test)",
                     "Bash(bun run src/cli.ts:*)", "Bash(cd .agents/skills/:*)"],
        advanced=True,
        note="Advanced: Claude writes and runs code. Some steps may still need the Claude Code terminal.",
    ),
    Command(
        "add-template", "Use my own CV or cover letter design", "Start",
        "Register your own LaTeX or Typst template, test-compile it and make it the default.",
        "Settings",
        [Field("mode", "What do you want to do?", "select", options=[
            ("", "Add a new template"), ("list", "List my templates"), ("switch", "Switch template")]),
         Field("path", "Template file path in this folder (optional)",
               placeholder="e.g. templates/my_cv.tex")],
        extra_tools=LATEX_TOOLS + ["Bash(pdflatex:*)", "Bash(typst compile:*)", "Bash(cd templates/:*)"],
        advanced=True,
    ),
    Command(
        "reset", "Start over", "Review reset",
        "Clear your profile, your documents, or both. Claude first shows exactly what will be "
        "cleared and waits for you to confirm.",
        "Settings",
        [Field("scope", "What to reset", "select", required=True, options=[
            ("profile", "My profile"), ("documents", "My documents"), ("all", "Everything")])],
        note="The app also keeps a copy so you can undo this run from its page.",
    ),
]}

GROUP_ORDER = ["Jobs", "Applications", "Profile", "Insights", "Settings"]


def repo_permissions(path: Path = SETTINGS) -> dict:
    """The repo's own allow/ask/deny lists from .claude/settings.json."""
    try:
        perms = json.loads(path.read_text(encoding="utf-8")).get("permissions") or {}
    except (OSError, json.JSONDecodeError):
        perms = {}
    return {k: [str(x) for x in perms.get(k) or []] for k in ("allow", "ask", "deny")}


def allowed_tools(command: Command | None, path: Path = SETTINGS) -> list[str]:
    """Pre-approved tools for a run: the repo's allow list (passed explicitly,
    so it applies even where Claude Code does not load project settings in
    headless mode), the base set, and the command's own extras."""
    out: list[str] = []
    for tool in repo_permissions(path)["allow"] + BASE_TOOLS + (command.extra_tools if command else []):
        if tool not in out:
            out.append(tool)
    return out


def disallowed_tools(path: Path = SETTINGS) -> list[str]:
    """The repo's deny list plus its ask list: a headless run cannot show a
    prompt, so anything the repo wants a human to approve is refused here and
    left for the Claude Code terminal."""
    perms = repo_permissions(path)
    return perms["deny"] + perms["ask"] + ["AskUserQuestion"]
