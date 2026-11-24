"""Tests for the local web app (webapp/).

The app wraps the framework without re-implementing it, so these pin the
wrapper's own promises: form values become the exact slash commands the
commands document, the read-only views tolerate the same legacy file shapes
the commands do, undo restores precisely what a run changed, a run's turns
resume the same Claude session, and the local server refuses cross-site
writes, foreign Host headers and paths outside its servable folders.

Skipped when the web app's optional dependencies are not installed
(pip install -r webapp/requirements.txt).
"""
import csv
import json
import os
import stat
import sys
import textwrap
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

try:
    import fastapi  # noqa: F401
    import markdown_it  # noqa: F401
    from fastapi.testclient import TestClient
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

ROOT = Path(__file__).resolve().parent.parent
HEADER = ["date", "company", "sector", "role", "role_type", "channel", "status", "contact_person",
          "fit_rating", "notes", "cv_file", "cover_letter_file", "source", "deadline"]


def write_tracker(path: Path, rows: list[list[str]], header=HEADER) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


@unittest.skipUnless(HAVE_DEPS, "web app dependencies not installed")
class CommandBuildTests(unittest.TestCase):
    def setUp(self):
        from webapp import commands
        self.c = commands

    def build(self, name, **values):
        return self.c.COMMANDS[name].build(values)

    def test_every_framework_command_is_exposed(self):
        shipped = {p.stem for p in (ROOT / ".claude" / "commands").glob("*.md")}
        self.assertTrue(shipped <= set(self.c.COMMANDS), shipped - set(self.c.COMMANDS))

    def test_apply_takes_url_or_text_not_both(self):
        self.assertEqual(self.build("apply", posting_url="https://x.example/job/1"), "/apply https://x.example/job/1")
        self.assertEqual(self.build("apply", posting_text="Line one\nLine two"), "/apply Line one\nLine two")
        with self.assertRaises(self.c.InvalidInput):
            self.build("apply")
        with self.assertRaises(self.c.InvalidInput):
            self.build("apply", posting_url="https://x.example", posting_text="t")
        with self.assertRaises(self.c.InvalidInput):
            self.build("apply", posting_url="javascript:alert(1)")

    def test_rank_flags_match_command_spec(self):
        self.assertEqual(self.build("rank"), "/rank")
        self.assertEqual(self.build("rank", focus="data science", all="on", limit="20", top="3"),
                         "/rank data science --all --limit 20 --top 3")
        with self.assertRaises(self.c.InvalidInput):
            self.build("rank", limit="ten")

    def test_outcome_modes(self):
        self.assertEqual(self.build("outcome", mode="update", target="Acme ML engineer"), "/outcome Acme ML engineer")
        self.assertEqual(self.build("outcome", mode="followup", days="14"), "/outcome followup 14")
        self.assertEqual(self.build("outcome", mode="followup", target="acme"), "/outcome followup acme")
        self.assertEqual(self.build("outcome", mode="stale"), "/outcome stale")

    def test_single_line_fields_reject_newlines(self):
        with self.assertRaises(self.c.InvalidInput):
            self.build("interview", target="acme\n/reset all")

    def test_reset_requires_known_scope(self):
        self.assertEqual(self.build("reset", scope="profile"), "/reset profile")
        with self.assertRaises(self.c.InvalidInput):
            self.build("reset", scope="everything-and-more")
        with self.assertRaises(self.c.InvalidInput):
            self.build("reset")

    def test_gmail_and_notion(self):
        self.assertEqual(self.build("gmail-sync", target="acme", since="2026-01-02"), "/gmail-sync acme since 2026-01-02")
        with self.assertRaises(self.c.InvalidInput):
            self.build("gmail-sync", since="last week")
        self.assertEqual(self.build("notion-sync", min_score="70", rebuild=True), "/notion-sync --min-score 70 --rebuild")

    def test_repo_permissions_are_passed_through(self):
        allow = self.c.allowed_tools(self.c.COMMANDS["rank"])
        self.assertIn("Bash(python3 tools/rank_state.py:*)", allow)
        denied = self.c.disallowed_tools()
        self.assertIn("Bash(git push:*)", denied)        # an `ask` rule: no prompt possible headless
        self.assertIn("Bash(curl -X POST:*)", denied)    # a `deny` rule
        self.assertIn("AskUserQuestion", denied)
        self.assertFalse(set(allow) & set(denied))

    def test_no_command_preapproves_broad_shell(self):
        for cmd in self.c.COMMANDS.values():
            for tool in self.c.allowed_tools(cmd):
                self.assertNotIn(tool, ("Bash", "Bash(*)", "Bash(:*)"))
                self.assertFalse(tool.startswith(("Bash(git push", "Bash(curl", "Bash(rm -rf")), tool)


@unittest.skipUnless(HAVE_DEPS, "web app dependencies not installed")
class DataTests(unittest.TestCase):
    def setUp(self):
        from webapp import data
        self.data = data
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "documents" / "applications").mkdir(parents=True)
        (self.root / "job_scraper").mkdir()
        patches = {
            "TRACKER": self.root / "job_search_tracker.csv",
            "SEEN_JOBS": self.root / "job_scraper" / "seen_jobs.json",
            "APPLICATIONS_DIR": self.root / "documents" / "applications",
            "DOCUMENTS_DIR": self.root / "documents",
            "ROOT": self.root,
        }
        for name, value in patches.items():
            p = mock.patch.object(data, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_archive_slug_follows_documents_readme_rule(self):
        self.assertEqual(self.data.archive_slug("Novo Nordisk A/S", "ML Engineer"), "novo_nordisk_as_ml_engineer")
        self.assertEqual(self.data.archive_slug("  Acme!! ", "  "), "acme")

    def test_tracker_legacy_rows_and_statuses(self):
        write_tracker(self.data.TRACKER, [
            ["2026-01-01", "Acme", "", "ML Engineer", "", "", "applied", "", "80", "", "", "", "", "2026-02-01"],
            ["2026-01-02", "Beta", "", "Analyst", "", "", "no response", "", "", "", "", "", ""],  # 13 columns
            ["2026-01-03", "Gamma", "", "Dev", "", "", "Interview", "", "", "", "", "", "", ""],
            ["2026-01-04", "Delta", "", "Dev", "", "", "weird", "", "", "", "", "", "", ""],
        ])
        apps = self.data.read_tracker()
        self.assertEqual([a.bucket for a in apps], ["Applied", "Closed", "Interviewing", "Closed"])
        self.assertEqual(apps[1].status, "no_response")
        self.assertFalse(apps[1].is_open)
        self.assertEqual(apps[1].deadline, "")
        ins = self.data.insights()
        self.assertEqual(ins["unknown_statuses"], ["weird"])

    def test_non_utf8_tracker_is_reported(self):
        self.data.TRACKER.write_bytes(",".join(HEADER).encode() + b"\n2026-01-01,Caf\xe9,,r,,,applied,,,,,,,\n")
        with self.assertRaises(self.data.TrackerError):
            self.data.read_tracker()

    def test_jobs_sorted_by_score_and_tracker_excluded(self):
        write_tracker(self.data.TRACKER, [["2026-01-01", "Acme", "", "ML Engineer", "", "", "applied",
                                           "", "", "", "", "", "", ""]])
        self.data.SEEN_JOBS.write_text(json.dumps({"seen": {
            "a": {"title": "ML Engineer", "company": "Acme", "status": "ranked", "rank_score": 90},
            "b": {"title": "Data Scientist", "company": "Beta", "status": "ranked", "rank_score": 70,
                  "location": "FLAG"},
            "c": {"title": "Analyst", "company": "Gamma", "status": "new"},
        }}), encoding="utf-8")
        jobs = self.data.read_jobs()
        self.assertEqual([j["key"] for j in jobs], ["a", "b", "c"])
        self.assertTrue(jobs[0]["tracked"])
        self.assertEqual(jobs[1]["location_verdict"], "FLAG")
        self.assertEqual(jobs[1]["location"], "")
        dash = self.data.dashboard()
        self.assertEqual([j["key"] for j in dash["top_jobs"]], ["b"])
        self.assertEqual(dash["new_jobs"], 1)

    def test_application_files_and_archive(self):
        write_tracker(self.data.TRACKER, [["2026-01-01", "Acme", "", "ML Engineer", "", "", "drafted", "", "",
                                           "", "cv/main_acme_ml.tex", "cover_letters/cover_acme_ml.tex", "", ""]])
        (self.root / "cv").mkdir()
        (self.root / "cv" / "main_acme_ml.pdf").write_bytes(b"%PDF-1.4")
        arch = self.root / "documents" / "applications" / "acme_ml_engineer"
        arch.mkdir()
        (arch / "job_posting.md").write_text("# Posting", encoding="utf-8")
        app = self.data.read_tracker()[0]
        files = {f["label"]: f for f in self.data.application_files(app)}
        self.assertTrue(files["CV"]["exists"])
        self.assertFalse(files["Cover letter"]["exists"])
        self.assertEqual(files["job_posting.md"]["kind"], "markdown")

    def test_profile_placeholders(self):
        (self.root / "CLAUDE.md").write_text("- **Name:** [YOUR_NAME]\n", encoding="utf-8")
        self.assertFalse(self.data.profile_status()["is_set_up"])
        (self.root / "CLAUDE.md").write_text("- **Name:** Ada Lovelace\n", encoding="utf-8")
        st = self.data.profile_status()
        self.assertTrue(st["is_set_up"])
        self.assertEqual(st["name"], "Ada Lovelace")


@unittest.skipUnless(HAVE_DEPS, "web app dependencies not installed")
class SnapshotTests(unittest.TestCase):
    def test_restore_reverts_edits_creations_and_deletions(self):
        from webapp import runner
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            (root / "cv").mkdir(parents=True)
            (root / "cover_letters" / "OpenFonts").mkdir(parents=True)
            (root / "cover_letters" / "OpenFonts" / "font.otf").write_text("font")
            (root / "job_search_tracker.csv").write_text("v1")
            (root / "cv" / "keep.tex").write_text("keep")
            (root / "cv" / "gone.tex").write_text("gone")
            (root / "unrelated.txt").write_text("u")
            with mock.patch.object(runner, "SNAP_DIR", Path(tmp) / "snaps"):
                runner.take_snapshot("s1", root)
                (root / "job_search_tracker.csv").write_text("v2")
                (root / "cv" / "new.pdf").write_text("new")
                (root / "cv" / "gone.tex").unlink()
                (root / "unrelated2.txt").write_text("outside scope")
                result = runner.restore_snapshot("s1", root)
            self.assertEqual((root / "job_search_tracker.csv").read_text(), "v1")
            self.assertFalse((root / "cv" / "new.pdf").exists())
            self.assertEqual((root / "cv" / "gone.tex").read_text(), "gone")
            self.assertTrue((root / "unrelated2.txt").exists())
            self.assertTrue((root / "cover_letters" / "OpenFonts" / "font.otf").exists())
            self.assertEqual(result["removed"], 1)

    def test_uncaptured_file_is_never_deleted(self):
        from webapp import runner
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            (root / "documents").mkdir(parents=True)
            big = root / "documents" / "big.pdf"
            big.write_text("x" * 100)
            with mock.patch.object(runner, "SNAP_DIR", Path(tmp) / "snaps"), \
                    mock.patch.object(runner, "MAX_SNAPSHOT_FILE", 10):
                runner.take_snapshot("s1", root)
                runner.restore_snapshot("s1", root)
            self.assertTrue(big.exists())


FAKE_CLAUDE = textwrap.dedent('''\
    #!{python}
    import json, sys
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    resumed = "--resume" in args
    sid = args[args.index("--resume") + 1] if resumed else "sess-1"
    with open({log!r}, "a") as fh:
        fh.write(json.dumps({{"args": args, "prompt": prompt}}) + "\\n")
    def emit(o): print(json.dumps(o), flush=True)
    emit({{"type": "system", "subtype": "init", "session_id": sid}})
    emit({{"type": "assistant", "session_id": sid, "parent_tool_use_id": None, "message": {{"content": [
        {{"type": "tool_use", "name": "Bash", "input": {{"command": "python3 tools/posting_guard.py --in x"}}}}]}}}})
    text = "Done, files written." if resumed else "Fit is 80/100. Should I proceed with drafting?"
    emit({{"type": "assistant", "session_id": sid, "parent_tool_use_id": None, "message": {{"content": [
        {{"type": "text", "text": text}}]}}}})
    emit({{"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": sid,
          "total_cost_usd": 0.5, "permission_denials": [{{"tool_name": "Bash", "tool_input": {{"command": "lualatex x"}}}}]}})
''')


@unittest.skipUnless(HAVE_DEPS and os.name == "posix", "needs web app deps and a POSIX shebang")
class RunManagerTests(unittest.TestCase):
    def test_two_turns_resume_same_session(self):
        from webapp import commands, runner
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "calls.jsonl"
            fake = tmp / "claude"
            fake.write_text(FAKE_CLAUDE.format(python=sys.executable, log=str(log)))
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            repo = tmp / "repo"
            repo.mkdir()
            (repo / "job_search_tracker.csv").write_text("x")
            with mock.patch.object(runner, "RUNS_DIR", tmp / "runs"), \
                    mock.patch.object(runner, "SNAP_DIR", tmp / "snaps"), \
                    mock.patch.object(runner, "SCRATCH_DIR", tmp / "scratch"), \
                    mock.patch.object(runner.data, "ROOT", repo):
                mgr = runner.RunManager(claude_bin=str(fake))
                run = mgr.start(commands.COMMANDS["apply"], "/apply https://x.example/1", "Prepare")
                r = self._wait(runner, run["id"])
                self.assertEqual(r["status"], "waiting")
                self.assertEqual(r["session_id"], "sess-1")
                self.assertEqual(r["turns"][0]["denied"], ["Building the CV PDF"])
                self.assertIn("Running the safety check on the posting", [e["text"] for e in r["events"]])
                self.assertTrue((tmp / "snaps" / run["id"] / "manifest.json").is_file())
                mgr.reply(run["id"], "Yes, go ahead.")
                r = self._wait(runner, run["id"])
                self.assertEqual(r["status"], "finished")
                self.assertEqual(len(r["turns"]), 2)
                self.assertAlmostEqual(r["cost_usd"], 1.0)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(calls[0]["prompt"], "/apply https://x.example/1")
            self.assertNotIn("--resume", calls[0]["args"])
            self.assertEqual(calls[1]["args"][calls[1]["args"].index("--resume") + 1], "sess-1")
            self.assertIn("AskUserQuestion", calls[0]["args"])
            self.assertIn("Bash(lualatex:*)", calls[0]["args"])

    def _wait(self, runner, run_id):
        deadline = time.time() + 20
        while time.time() < deadline:
            r = runner.load_run(run_id)
            if r["status"] not in ("queued", "running"):
                return r
            time.sleep(0.1)
        self.fail("run did not finish")


@unittest.skipUnless(HAVE_DEPS, "web app dependencies not installed")
class ServerTests(unittest.TestCase):
    def setUp(self):
        from webapp import server

        class FakeManager:
            started = []

            def busy(self):
                return False

            def start(self, command, prompt, title):
                self.started.append(prompt)
                return {"id": "abcdefabcdef"}

        self.server = server
        self.manager = FakeManager()
        self.app = server.create_app(manager=self.manager)
        self.client = TestClient(self.app)
        self.token = {"X-App-Token": self.app.state.token}

    def test_pages_render(self):
        for url in ("/", "/jobs", "/applications", "/apply", "/actions", "/actions/setup",
                    "/runs", "/profile", "/insights"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn("Content-Security-Policy", r.headers)

    def test_post_requires_token(self):
        body = {"command": "rank", "values": {}}
        self.assertEqual(self.client.post("/api/run", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/run", json=body, headers={"X-App-Token": "nope"}).status_code, 403)
        r = self.client.post("/api/run", json=body, headers=self.token)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.manager.started[-1], "/rank")

    def test_foreign_host_rejected(self):
        r = self.client.get("/", headers={"Host": "evil.example:8000"})
        self.assertEqual(r.status_code, 400)

    def test_invalid_input_is_400(self):
        r = self.client.post("/api/run", json={"command": "apply", "values": {}}, headers=self.token)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/run", json={"command": "not-a-command"}, headers=self.token)
        self.assertEqual(r.status_code, 400)

    def test_file_serving_is_confined(self):
        for path in ("CLAUDE.md", ".env", "cv/../CLAUDE.md", "documents/../../etc/passwd",
                     "cover_letters/OpenFonts/fonts/lato/Lato-Reg.ttf", "documents/cv/.gitkeep"):
            self.assertEqual(self.client.get(f"/files/{path}").status_code, 404, path)

    def test_safe_filename(self):
        self.assertEqual(self.server.safe_filename("../../evil.pdf"), "evil.pdf")
        self.assertEqual(self.server.safe_filename(".bashrc"), "")
        self.assertEqual(self.server.safe_filename("script.sh"), "")
        self.assertEqual(self.server.safe_filename("My CV (2026).pdf"), "My CV (2026).pdf")

    def test_markdown_escapes_raw_html(self):
        from webapp.render import markdown
        html = str(markdown("<script>alert(1)</script> [x](javascript:alert(1)) **ok**"))
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn("<strong>ok</strong>", html)


if __name__ == "__main__":
    unittest.main()
