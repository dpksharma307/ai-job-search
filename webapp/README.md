# AI Job Search — local web app

A browser interface for this repo's job-search workflow. It runs **on your own computer only** and
drives the same `/setup`, `/apply`, `/rank`, `/outcome`… commands you would type into Claude Code.
Nothing in the workflow is re-implemented: every button runs the real command.

## What you need (one-time)

| Tool | Why | Check |
|---|---|---|
| **Claude Code**, logged in | Runs the commands | `claude --version`, then run `claude` once and log in |
| **Python 3.10+** | Runs this app and the repo's tools | `python3 --version` (Windows: `python --version`) |
| **LaTeX** (TeX Live or MiKTeX) with `lualatex` + `xelatex` | Builds the CV and cover letter PDFs | `lualatex --version` |
| **Bun** | Runs the job-board search tools | `bun --version` |
| `pdftotext` (poppler) — optional | Extra PDF text check | `pdftotext -v` |

Install the app's Python packages from the repo folder:

```bash
pip install -r webapp/requirements.txt
```

## Start it

From the repo folder:

```bash
python3 -m webapp
```

(Windows: `python -m webapp`.) Your browser opens at <http://127.0.0.1:8000>. Stop it with **Ctrl+C**.
Options: `--port 8080`, `--no-browser`.

## First steps

1. **Profile → Upload** your CV, LinkedIn export, references (they stay in `documents/`).
2. **Set up my profile** and choose "Read my documents folder".
3. **Jobs → Find new jobs**, then **Score jobs**.
4. **Prepare application** on a good match. Claude evaluates your fit and *asks before drafting*;
   answer on the task page. The CV and cover letter PDFs appear under **Applications**.
5. After submitting: **I've submitted it** / **Log an update**. Interview booked? **Prepare for interview**.

## How it works

- Each button starts a **task** that runs `claude -p "/<command> …"` in this folder. Progress and
  Claude's messages show on the task page. When Claude asks you something, reply there; the next
  reply continues the same Claude session.
- Only **one task runs at a time** (the commands share your tracker and job list). Others wait in line.
- Before each task the app copies your personal files to `.webapp/snapshots/`, so every task page
  has **Undo this task's changes**. The last 20 tasks can be undone.
- Pages read your files directly (`job_search_tracker.csv`, `job_scraper/seen_jobs.json`,
  `documents/applications/…`, `cv/`, `cover_letters/`). There is no database.

## Permissions

`claude -p` cannot show permission prompts, so each task gets an explicit list:

- the repo's own `permissions.allow` from `.claude/settings.json`,
- basic file/web tools, plus a few per-command extras (e.g. `lualatex`/`xelatex` for `/apply`),
- **refused:** everything in the repo's `deny` *and* `ask` lists (e.g. `git push`, editing
  `tools/`), plus `AskUserQuestion`.

If Claude needs something that isn't allowed, the task page says which step couldn't run. Do that
step in the Claude Code terminal. This mainly affects the **Advanced** actions (*Add a job board*,
*Use my own template*), which edit protected files.

## Integrations

- **Check my inbox** needs the Gmail connector enabled on your Claude account.
- **Send to Notion** needs the Notion MCP server connected in Claude Code
  (`claude mcp add --transport http notion https://mcp.notion.com/mcp`, then `/mcp` to log in).

## Safety

- Listens on `127.0.0.1` only and rejects other `Host` headers.
- Every change request needs a per-launch token from the page, so other websites can't trigger tasks.
- Claude's messages are rendered with raw HTML disabled, and HTML reports open in a sandbox.
- Files are served only from `cv/`, `cover_letters/`, `documents/`, `reports/` and `upskill/`.
- `.webapp/` (task history, undo copies) is gitignored, like your other personal files.

## Tests

```bash
python3 -m unittest tests.test_webapp -v
```

The tests are skipped automatically if the packages above aren't installed.
