"""FastAPI app: pages over the framework's files + endpoints that start runs.

Local-only by design: binds to 127.0.0.1, rejects any other Host header (DNS
rebinding), and every state-changing request must carry the per-launch token
from the page's <meta> tag (a cross-site page can neither read it nor send a
custom header without a CORS preflight, which this app never grants).
"""

from __future__ import annotations

import mimetypes
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import commands as cmds
from . import data
from .render import markdown
from .runner import RunManager, list_runs, load_run

HERE = Path(__file__).resolve().parent
SERVABLE_PREFIXES = ("cv/", "cover_letters/", "documents/", "reports/", "upskill/")
UPLOAD_EXTS = {".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".tex", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD = 25 * 1024 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "frame-src 'self'; object-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


def create_app(manager: RunManager | None = None, port: int = 8000) -> FastAPI:
    app = FastAPI(title="AI Job Search", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = secrets.token_urlsafe(24)
    app.state.manager = manager or RunManager()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost", "testserver"}
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["md"] = markdown
    templates.env.globals.update(COMMANDS=cmds.COMMANDS, GROUPS=cmds.GROUP_ORDER)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"error": "Unknown host"}, status_code=400)
        if request.method not in ("GET", "HEAD") and \
                not secrets.compare_digest(request.headers.get("x-app-token", ""), app.state.token):
            return JSONResponse({"error": "Missing or invalid app token - reload the page."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.update(request=request, token=app.state.token, profile=data.profile_status(),
                   busy=app.state.manager.busy(), active=name.split(".")[0])
        return templates.TemplateResponse(request, name, ctx)

    # ------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        try:
            dash = data.dashboard()
        except data.TrackerError as exc:
            return page(request, "error.html", message=str(exc))
        return page(request, "home.html", dash=dash, runs=list_runs(5))

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, show: str = "ranked", q: str = ""):
        all_jobs = data.read_jobs()
        groups = {
            "ranked": [j for j in all_jobs if j.get("status") == "ranked" and not j["tracked"]],
            "new": [j for j in all_jobs if j.get("status") in ("new", "unreachable")],
            "all": all_jobs,
        }
        shown = groups.get(show, groups["ranked"])
        if q:
            ql = q.lower()
            shown = [j for j in shown if ql in f"{j.get('title','')} {j.get('company','')}".lower()]
        return page(request, "jobs.html", jobs=shown, show=show, q=q,
                    counts={k: len(v) for k, v in groups.items()})

    @app.get("/applications", response_class=HTMLResponse)
    def applications(request: Request):
        try:
            apps = data.read_tracker()
        except data.TrackerError as exc:
            return page(request, "error.html", message=str(exc))
        columns = {b: [a for a in apps if a.bucket == b] for b in data.BUCKET_ORDER}
        return page(request, "applications.html", columns=columns, total=len(apps))

    @app.get("/applications/{app_id}", response_class=HTMLResponse)
    def application(request: Request, app_id: int):
        apps = data.read_tracker()
        match = [a for a in apps if a.id == app_id]
        if not match:
            raise HTTPException(404, "Application not found")
        a = match[0]
        files = data.application_files(a)
        docs = {}
        for f in files:
            if f["kind"] == "markdown":
                try:
                    docs[f["path"]] = (data.ROOT / f["path"]).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
        target = f"{a.company} {a.role}".strip()
        return page(request, "application.html", a=a, files=files, docs=docs, target=target)

    @app.get("/apply", response_class=HTMLResponse)
    def apply_page(request: Request, url: str = ""):
        return page(request, "apply.html", command=cmds.COMMANDS["apply"], prefill={"posting_url": url})

    @app.get("/actions", response_class=HTMLResponse)
    def actions(request: Request):
        return page(request, "actions.html")

    @app.get("/actions/{name}", response_class=HTMLResponse)
    def action(request: Request, name: str):
        command = cmds.COMMANDS.get(name)
        if not command:
            raise HTTPException(404, "Unknown action")
        prefill = {k: v for k, v in request.query_params.items()}
        return page(request, "action.html", command=command, prefill=prefill)

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request):
        return page(request, "runs.html", runs=list_runs())

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        try:
            run = load_run(run_id)
        except KeyError:
            raise HTTPException(404, "Task not found")
        later = [r["title"] for r in list_runs(1000)
                 if r.get("created", "") > run.get("created", "") and r.get("snapshot") and not r.get("undone")]
        return page(request, "run.html", run=run, later=later)

    @app.get("/profile", response_class=HTMLResponse)
    def profile(request: Request):
        return page(request, "profile.html", documents=data.documents(), categories=data.DOC_CATEGORIES)

    @app.get("/insights", response_class=HTMLResponse)
    def insights(request: Request):
        try:
            ins = data.insights()
        except data.TrackerError as exc:
            return page(request, "error.html", message=str(exc))
        return page(request, "insights.html", ins=ins)

    @app.get("/view/{path:path}", response_class=HTMLResponse)
    def view(request: Request, path: str):
        full = safe_path(path)
        if full.suffix.lower() not in (".md", ".txt"):
            return RedirectResponse(f"/files/{path}")
        return page(request, "view.html", title=full.name, path=path,
                    body=full.read_text(encoding="utf-8", errors="replace"), is_md=full.suffix.lower() == ".md")

    @app.get("/files/{path:path}")
    def files(path: str, download: bool = False):
        full = safe_path(path)
        mime = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
        headers = {}
        if full.suffix.lower() in (".html", ".htm"):
            # Model-generated HTML: no scripts reach this app's origin.
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        if download or mime not in ("application/pdf", "text/html", "text/plain", "text/markdown"):
            return FileResponse(full, media_type=mime, filename=full.name, headers=headers)
        headers["Content-Disposition"] = "inline"
        return FileResponse(full, media_type=mime, headers=headers)

    # --------------------------------------------------------------- api
    @app.post("/api/run")
    async def api_run(request: Request):
        body = await request.json()
        command = cmds.COMMANDS.get(str(body.get("command", "")))
        if not command:
            return JSONResponse({"error": "Unknown action"}, status_code=400)
        try:
            prompt = command.build(body.get("values") or {})
        except cmds.InvalidInput as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        run = app.state.manager.start(command, prompt, command.title)
        return {"run_id": run["id"]}

    @app.post("/api/runs/{run_id}/reply")
    async def api_reply(request: Request, run_id: str):
        body = await request.json()
        text = str(body.get("text", "")).strip()
        if not text or len(text) > 20000:
            return JSONResponse({"error": "Write a reply first."}, status_code=400)
        try:
            app.state.manager.reply(run_id, text)
        except KeyError:
            return JSONResponse({"error": "Task not found"}, status_code=404)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/cancel")
    def api_cancel(run_id: str):
        app.state.manager.cancel(run_id)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/undo")
    def api_undo(run_id: str):
        try:
            return app.state.manager.undo(run_id)
        except KeyError:
            return JSONResponse({"error": "Task not found"}, status_code=404)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/api/runs/{run_id}")
    def api_run_status(run_id: str, since: int = 0):
        try:
            run = load_run(run_id)
        except KeyError:
            return JSONResponse({"error": "Task not found"}, status_code=404)
        events = run.get("events", [])
        return {
            "status": run["status"], "cost_usd": run.get("cost_usd", 0), "undone": run.get("undone"),
            "event_count": len(events),
            "events": [{"kind": e["kind"], "t": e["t"],
                        "html": markdown(e["text"]) if e["kind"] == "text" else None,
                        "text": e["text"] if e["kind"] != "text" else None}
                       for e in events[since:]] if since <= len(events) else [],
            "turns": [{"input": t["input"], "output_html": markdown(t.get("output") or ""),
                       "finished": t.get("finished"), "denied": t.get("denied", [])}
                      for t in run.get("turns", [])],
        }

    @app.post("/api/documents")
    async def api_upload(category: str = Form(...), file: UploadFile = File(...)):
        if category not in data.DOC_CATEGORIES:
            return JSONResponse({"error": "Unknown document type"}, status_code=400)
        name = safe_filename(file.filename or "")
        if not name:
            return JSONResponse({"error": "That file name or type isn't supported."}, status_code=400)
        content = await file.read(MAX_UPLOAD + 1)
        if len(content) > MAX_UPLOAD:
            return JSONResponse({"error": "File is larger than 25 MB."}, status_code=400)
        folder = data.DOCUMENTS_DIR / category
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        n = 1
        while target.exists():
            target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        target.write_bytes(content)
        return {"ok": True, "name": target.name}

    @app.post("/api/documents/delete")
    async def api_delete(request: Request):
        body = await request.json()
        full = safe_path(str(body.get("path", "")))
        if full.parent.parent != data.DOCUMENTS_DIR or full.parent.name not in data.DOC_CATEGORIES:
            return JSONResponse({"error": "Only uploaded documents can be deleted here."}, status_code=400)
        full.unlink()
        return {"ok": True}

    return app


def safe_path(path: str) -> Path:
    """A file inside the servable folders, never outside the repo."""
    path = path.replace("\\", "/")
    if not path.startswith(SERVABLE_PREFIXES) or "\x00" in path:
        raise HTTPException(404, "Not found")
    full = (data.ROOT / path).resolve()
    try:
        full.relative_to(data.ROOT)
    except ValueError:
        raise HTTPException(404, "Not found")
    if not full.is_file() or full.name == ".gitkeep" or "OpenFonts" in full.parts:
        raise HTTPException(404, "Not found")
    return full


def safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    if not name or name.startswith(".") or Path(name).suffix.lower() not in UPLOAD_EXTS:
        return ""
    name = re.sub(r"[^\w .()\-]", "_", name)
    return name[:150]
