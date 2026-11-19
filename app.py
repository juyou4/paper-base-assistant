"""论文入表助手的本机 HTTP 服务。运行 python app.py --open 即可启动。"""

import argparse
import json
import os
import re
import secrets
import threading
import time
import uuid
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import model_support
import paper_attachments
import execution_context
import pdf_page_preview
import papers
from history_controller import HistoryController, owner_key
from table_store import FeishuTable, LarkCliTable, LocalTable, TableError
from workflow import Conversation, _fields
from workspace_actions import target_link
from record_pages import read_page


ROOT = Path(__file__).resolve().parent
WEB_TYPES = {
    "/index.html": "text/html", "/style.css": "text/css", "/app.js": "text/javascript",
    "/rich-text.js": "text/javascript", "/history.js": "text/javascript", "/vendor/katex/katex.min.js": "text/javascript",
    "/vendor/katex/katex.min.css": "text/css", "/vendor/markdown-it/markdown-it.min.js": "text/javascript",
}
KATEX_FONT = re.compile(r"/vendor/katex/fonts/KaTeX_[A-Za-z0-9-]+\.woff2")
ATTACHMENT_ROUTE = re.compile(r"/api/attachments/([0-9a-f]{64})(/bind)?")


class Application:
    def __init__(self, mode, data_dir):
        self.mode = mode
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db = self.data_dir / "records.sqlite3"
        config_file = self.data_dir / "feishu-target.json"
        config = json.loads(config_file.read_text(encoding="utf-8-sig")) if mode != "local" and config_file.is_file() else {}
        base = os.environ.get("FEISHU_APP_TOKEN") or config.get("app_token", "")
        table_id = os.environ.get("FEISHU_TABLE_ID") or config.get("table_id", "")
        if mode == "local":
            self.table = LocalTable(db)
        elif mode == "feishu":
            self.table = FeishuTable(db, os.getenv("FEISHU_APP_ID"), os.getenv("FEISHU_APP_SECRET"), base, table_id)
        else:
            self.table = LarkCliTable(db, os.getenv("LARK_CLI", "lark-cli"), base, table_id)
        self.name = self.table.name if mode == "local" else (os.getenv("FEISHU_TABLE_NAME") or config.get("table_name") or "飞书测试表")
        if mode != "local" and not table_id:
            self.name = "尚未选择数据表"
        self.feishu_url = ""
        if mode != "local":
            from tools.feishu_setup import browser_url
            self.feishu_url = browser_url(config, base, table_id)
        self.base_name = config.get("base_name", "") if self.feishu_url else ""
        self.sessions = {}
        self.lock = threading.Lock()
        self.history = HistoryController(self.data_dir, mode)

    def session(self, cookie):
        now = time.monotonic()
        with self.lock:
            if cookie not in self.sessions or now - self.sessions[cookie][1] > 7200:
                for key, (old_chat, seen) in list(self.sessions.items()):
                    if now - seen > 7200:
                        old_chat.close()
                        del self.sessions[key]
                if not (isinstance(cookie, str) and re.fullmatch(r"[A-Za-z0-9_-]{32}", cookie)
                        and (self.history.store.has_owner(owner_key(cookie))
                             or paper_attachments.AttachmentLibrary(self.data_dir, cookie).index.is_file())):
                    cookie = secrets.token_urlsafe(24)
                chat = Conversation(self.table, self.data_dir, self.name, self.feishu_url, self.base_name)
                self.history.attach(chat, cookie)
                self.sessions[cookie] = [chat, now]
            self.sessions[cookie][1] = now
            return cookie, self.sessions[cookie][0]

    def state(self, chat=None):
        agent = model_support.selection_info(chat.selected_model if chat else None)
        with chat.lock if chat else self.lock:
            table = chat.table if chat else self.table
            name = chat.table_name if chat else self.name
            bound = bool(getattr(table, "table_id", True))
            link = target_link(table, chat.base_url) if chat else self.feishu_url
            fields = table.schema() if bound else []
            titles = [col["name"] for col in _fields(fields, "title")]
            links = [col["name"] for col in _fields(fields, "url")]
            page = read_page(table, titles) if bound else {"rows": [], "reading_progress": None, "title_aliases": {},
                                                           "page": {"next": "", "total": 0, "query": "", "progress": ""}}
            base_name = chat.base_name if chat else self.base_name
            pending = chat.pending if chat else None
            dialog = None
            if pending:
                if pending["stage"] == "evidence_confirm":
                    reply = pending["reply"]
                    dialog = {"id": pending["id"], "message": reply["message"], "evidence_preview": reply["evidence_preview"]}
                else:
                    dialog = {k: pending[k] for k in ("id", "message", "choices")}
                if chat.execution_actor:
                    dialog["confirmation"] = execution_context.confirmation(chat)
            workspace_pending = dialog if pending and pending["stage"] in {"workspace_name", "workspace_create"} else None
        return {"mode": "local" if self.mode == "local" else "feishu", "table_name": name,
                "target_bound": bound, "base_name": base_name, "workspace_pending": workspace_pending, "pending_dialog": dialog,
                "fields": fields, **page,
                "identity_fields": {"titles": titles, "links": links},
                "sample": {"title": papers.SAMPLE["title"], "url": "https://arxiv.org/abs/" + papers.SAMPLE_ID},
                "download_url": "/api/sample.pdf", "feishu_url": link,
                "capabilities": {"model": agent["enabled"], "agent": agent, "activity": True},
                "connection": execution_context.connection(chat) if chat and chat.execution_actor else None}

    def close(self):
        for chat, _ in self.sessions.values():
            chat.close()
        self.table.close()

    def switch_model(self, chat, name):
        with chat.lock:
            info = model_support.selection_info(chat.selected_model)
            if not isinstance(name, str) or not info["enabled"] or name not in info["models"]:
                raise ValueError("请选择当前推理服务提供的模型。")
            default = model_support.agent_info()["model"]
            chat.selected_model = None if name == default else name
            return {"agent": {**info, "model": name}, "message": f"当前对话已选择 {name}。"}


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperBase"

    def log_message(self, *_args):
        # 私人论文地址、查询参数和记录不进入控制台。
        pass

    def send_body(self, data, mime, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, code=200, cookie=None):
        headers = {"Set-Cookie": f"paper_session_{self.server.server_port}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"} if cookie else {}
        self.send_body(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code, headers)

    def browser_session(self):
        jar = SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        sid = jar.get(f"paper_session_{self.server.server_port}") or jar.get("paper_session")
        app = self.server.app
        cookie, chat = app.session(sid.value if sid else None)
        actor = execution_context.actor_for(chat.table)
        execution_context.ACTIVE.set(actor)
        route = urlsplit(self.path).path
        if route == "/api/activity":
            # 跟踪长任务时不能等待执行锁；切号后的旧运行不再展示。
            if chat.execution_actor and chat.execution_actor["key"] != actor["key"]:
                raise ValueError("执行身份已切换，请刷新工作台。")
        elif route != "/api/chat" or not chat.execution_actor or chat.execution_actor["key"] != actor["key"]:
            with chat.lock:
                previous = chat.execution_actor
                app.history.bind(chat)
                if previous and previous["key"] != actor["key"] and self.command == "POST":
                    raise ValueError("执行身份已切换，旧操作已失效。请刷新后重新预览。")
        return cookie, chat

    def allowed_host(self):
        addr = self.headers.get("Host", "")
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        return addr in expected and self.headers.get("Origin", "http://" + addr) == "http://" + addr

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method):
        token = execution_context.ACTIVE.set(None)
        try:
            self.dispatch_request(method)
        finally:
            execution_context.ACTIVE.reset(token)

    def dispatch_request(self, method):
        run = None
        cookie = None
        agent = None
        try:
            if not self.allowed_host():
                self.send_json({"error": "请从本机服务地址打开。"}, 403)
                return
            route = urlsplit(self.path)
            app = self.server.app
            if method == "GET" and route.path == "/api/state":
                cookie, chat = self.browser_session()
                with chat.lock:
                    history_identity = app.history.bind(chat)
                    snapshot = app.state(chat)
                    snapshot.update(history_identity)
                    snapshot["table_view_id"] = chat.table_view_id
                snapshot["attachments"] = paper_attachments.AttachmentLibrary(app.data_dir, chat.attachment_owner).list()
                self.send_json(snapshot, cookie=cookie)
            elif method == "GET" and route.path == "/api/connection":
                cookie, chat = self.browser_session()
                with chat.lock:
                    self.send_json(execution_context.connection(chat), cookie=cookie)
            elif method == "GET" and route.path in {"/api/records/page", "/api/records/one"}:
                cookie, chat = self.browser_session()
                args = parse_qs(route.query, keep_blank_values=True)
                with chat.lock:
                    if args.get("view") != [chat.table_view_id]:
                        raise ValueError("当前数据表已经切换，请刷新后再查询。")
                    if route.path.endswith("/one"):
                        if set(args) != {"view", "id"} or len(args["id"]) != 1 or not 0 < len(args["id"][0]) <= 200:
                            raise ValueError("请选择当前表中的一条记录。")
                        row = chat.table.get(args["id"][0])
                        result = {"row": row, "reading_status": (chat.table.reading_progress().get(row["id"], "待读")
                                  if isinstance(chat.table, LocalTable) else None)}
                    else:
                        if set(args) - {"view", "cursor", "q", "progress"} or any(len(v) != 1 for v in args.values()):
                            raise ValueError("清单分页条件无效。")
                        titles = [f["name"] for f in _fields(chat.table.schema(), "title")]
                        result = read_page(chat.table, titles, args.get("cursor", [""])[0], args.get("q", [""])[0],
                                           args.get("progress", [""])[0])
                    self.send_json({**result, "table_view_id": chat.table_view_id}, cookie=cookie)
            elif method == "GET" and route.path == "/api/workspace-history":
                cookie, chat = self.browser_session()
                with chat.lock:
                    self.send_json(app.history.snapshot(chat), cookie=cookie)
            elif method == "GET" and route.path == "/api/workspace-history/page":
                params = parse_qs(route.query, keep_blank_values=True)
                if set(params) != {"scope", "conversation_id", "kind", "before"} or any(len(v) != 1 for v in params.values()):
                    raise ValueError("请从当前对话或操作记录继续加载。")
                cookie, chat = self.browser_session()
                with chat.lock:
                    app.history.bind(chat)
                    app.history.check(chat, params["scope"][0], params["conversation_id"][0])
                    page = app.history.store.page(chat.history_owner, chat.history_scope, chat.history_conversation_id,
                                                  params["kind"][0], params["before"][0])
                self.send_json(page, cookie=cookie)
            elif route.path == "/api/attachments" or ATTACHMENT_ROUTE.fullmatch(route.path):
                cookie, chat = self.browser_session()
                self.attachment_request(method, route, cookie, chat)
            elif method == "GET" and not route.query and (page_route := pdf_page_preview.ROUTE.fullmatch(route.path)):
                cookie, chat = self.browser_session()
                library = paper_attachments.AttachmentLibrary(app.data_dir, chat.attachment_owner)
                source = pdf_page_preview.evidence_file(chat, library, page_route[1],
                                                        int(page_route[2]), int(page_route[3]))
                if source is None:
                    self.send_json({"error": "当前待确认预览中没有这项 PDF 原页。"}, 404, cookie)
                    return
                try:
                    png = pdf_page_preview.render(*source)
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, 422, cookie)
                    return
                self.send_body(png, "image/png")
            elif method == "GET" and route.path == "/api/activity":
                ids = parse_qs(route.query).get("request_id", [])
                if len(ids) != 1:
                    raise ValueError("请指定一条请求的编号。")
                request_id = str(uuid.UUID(ids[0]))
                cookie, chat = self.browser_session()
                self.send_json(chat.activity.snapshot(request_id), cookie=cookie)
            elif method == "POST" and route.path in {"/api/chat", "/api/model", "/api/reading-progress",
                                                     "/api/records/compare", "/api/records/export", "/api/workspace-history"}:
                if self.headers.get_content_type() != "application/json":
                    self.send_json({"error": "请求体需要为 JSON。"}, 415)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384:
                    self.send_json({"error": "指令太长或为空。"}, 400)
                    return
                body = json.loads(self.rfile.read(length))
                cookie, chat = self.browser_session()
                if route.path == "/api/workspace-history":
                    with chat.lock:
                        self.send_json(app.history.update(chat, body), cookie=cookie)
                    return
                if route.path.startswith("/api/records/"):
                    from saved_compare import comparison_data, read_selection
                    with chat.lock:
                        if not isinstance(body, dict) or body.get("table_view_id") != chat.table_view_id:
                            raise ValueError("当前数据表已经切换，请刷新后重选。")
                        if route.path.endswith("/compare"):
                            if set(body) != {"table_view_id", "records"}:
                                raise ValueError("请选择要比较的论文。")
                            schema = chat.table.schema()
                            selection = read_selection(chat, schema, body["records"])
                            result = comparison_data(chat.table_name, schema, selection,
                                                     ["摘要", "方法", "关键数据", "图表", "公式", "资源链接", "笔记"], table=chat.table)
                        else:
                            from research_exports import export_records, local_backup
                            if (not isinstance(body.get("format"), str) or set(body) not in (
                                    {"table_view_id", "format"}, {"table_view_id", "format", "records"})):
                                raise ValueError("请选择导出格式和范围。")
                            if body["format"] == "json":
                                if "records" in body:
                                    raise ValueError("本地备份包含整张数据表。")
                                result = local_backup(chat.table, chat.table_name)
                            else:
                                schema = chat.table.schema()
                                rows = ([item[0] for item in read_selection(chat, schema, body["records"], minimum=1)]
                                        if "records" in body else chat.table.rows())
                                result = export_records(chat.table, schema, rows, body["format"], chat.table_name)
                    self.send_json(result, cookie=cookie)
                    return
                if route.path == "/api/reading-progress":
                    required = {"record_id", "expected", "status"}
                    if (not isinstance(body, dict) or set(body) not in (required, required | {"history_scope", "history_conversation_id"})
                            or not all(isinstance(v, str) for v in body.values())):
                        raise ValueError("请指定当前记录和阅读进度。")
                    with chat.lock:
                        if not isinstance(chat.table, LocalTable):
                            raise ValueError("该操作适用于本地阅读清单。")
                        app.history.bind(chat)
                        if "history_scope" in body:
                            app.history.check(chat, body["history_scope"], body["history_conversation_id"])
                        chat.table.set_reading_progress(body["record_id"], body["expected"], body["status"])
                        actual = chat.table.reading_progress().get(body["record_id"])
                        app.history.store.record_operation(chat.history_owner, chat.history_scope, chat.history_conversation_id,
                            uuid.uuid4().hex, {"action": "reading_progress",
                            "status": "verified" if actual == body["status"] else "unverified",
                            "summary": f"阅读进度由「{body['expected']}」调整为「{body['status']}」。",
                            "details": [{"label": "记录 ID", "value": body["record_id"], "record_id": body["record_id"]},
                                        {"label": "回读进度", "value": actual or "未取得"}]})
                    self.send_json({"status": body["status"]}, cookie=cookie)
                    return
                if route.path == "/api/model":
                    if not isinstance(body, dict) or set(body) != {"model"}:
                        raise ValueError("请选择一个模型。")
                    self.send_json(app.switch_model(chat, body["model"]), cookie=cookie)
                    return
                if not isinstance(body, dict) or not isinstance(body.get("message"), str):
                    raise ValueError("请发送一条文字指令。")
                message = body["message"].strip()
                if not 0 < len(message) <= 2048:
                    raise ValueError("指令需要为 1～2048 个字符。")
                request_id = str(uuid.uuid4()) if body.get("request_id") is None else str(uuid.UUID(str(body["request_id"])))
                run = chat.activity.begin(request_id)
                with (run.recording(), chat.lock, model_support.using_model(chat.selected_model),
                      model_support.trace_calls(include_failed=True) as steps, paper_attachments.session_scope(app.data_dir, chat.attachment_owner)):
                    agent = {**model_support.agent_info(), "steps": steps}
                    options = {}
                    if "evidence_selection" in body:
                        if not isinstance(body["evidence_selection"], list):
                            raise ValueError("请选择要回填的证据项。")
                        options["evidence_selection"] = body["evidence_selection"]
                    if "evidence_fields" in body and body["evidence_fields"] is not None:
                        if not isinstance(body["evidence_fields"], list):
                            raise ValueError("目标列设置无效，请重新选择。")
                        options["evidence_fields"] = body["evidence_fields"]
                    result = app.history.respond(chat, message, body, request_id, options)
                    if steps:
                        result["agent"] = agent
                result["activity"] = run.snapshot()
                self.send_json(result, cookie=cookie)
            elif method == "GET" and route.path in {"/api/sample.pdf", "/api/pdf"}:
                target = papers.SAMPLE_ID if route.path == "/api/sample.pdf" else parse_qs(route.query).get("paper", [""])[0]
                if len(target) > 2048:
                    raise ValueError("无效的论文地址。")
                paper = papers.resolve_paper(target, app.data_dir)
                local = papers.download_pdf(paper, app.data_dir)
                self.send_body(local.read_bytes(), "application/pdf", extra={"Content-Disposition": f'inline; filename="{local.name}"'})
            elif method == "GET" and (route.path == "/" or route.path in WEB_TYPES or KATEX_FONT.fullmatch(route.path)):
                path = "/index.html" if route.path == "/" else route.path
                asset = ROOT / "web" / path[1:]
                if not asset.is_file():
                    self.send_json({"error": "找不到该资源。"}, 404)
                    return
                mime = WEB_TYPES[path] + "; charset=utf-8" if path in WEB_TYPES else "font/woff2"
                self.send_body(asset.read_bytes(), mime)
            else:
                self.send_json({"error": "找不到该资源。"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, TableError) as exc:
            self.send_error_result(str(exc), 400, run, cookie, agent)
        except Exception:
            self.send_error_result("处理失败，请检查服务配置和数据源，再重试。", 500, run, cookie, agent)

    def send_error_result(self, message, code, run, cookie, agent):
        result = {"error": message}
        if agent and agent["steps"]:
            result["agent"] = agent
        if run is not None:
            result["activity"] = run.snapshot()
        self.send_json(result, code, cookie=cookie)

    def attachment_request(self, method, route, cookie, chat):
        app = self.server.app
        library = paper_attachments.AttachmentLibrary(app.data_dir, chat.attachment_owner)
        match = ATTACHMENT_ROUTE.fullmatch(route.path)
        try:
            if method == "GET" and match and not match[2]:
                pdf = library.path(match[1])
                self.send_body(pdf.read_bytes(), "application/pdf", extra={"Content-Disposition": 'inline; filename="paper.pdf"'})
                return
            if method == "POST" and not match:
                size = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/pdf":
                    self.send_json({"error": "附件需要为 PDF。"}, 415, cookie)
                    return
                if not 0 < size <= paper_attachments.MAX_BYTES:
                    self.send_json({"error": "PDF 文件大小上限为 50 MB。"}, 413, cookie)
                    return
                item = library.receive(self.rfile.read(size), unquote(self.headers.get("X-Pdf-Name", "paper.pdf")))
                self.send_json(item, cookie=cookie)
                return
            if method == "POST" and match and match[2]:
                size = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < size <= 8192:
                    raise ValueError("请指定要关联的论文。")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("请选择目标论文。")
                library.path(match[1])
                if set(body) == {"record_id", "expected_url"} and all(isinstance(v, str) for v in body.values()):
                    from evidence_workflow import _field
                    with chat.lock:
                        if not getattr(chat.table, "table_id", True):
                            raise ValueError("还没有当前数据表，请先建表，或直接关联论文链接。")
                        schema = chat.table.schema()
                        row = chat.table.get(body["record_id"])
                    if not row:
                        raise ValueError("目标记录已变更，请刷新阅读清单。")
                    fields = row["fields"]
                    url = fields.get(_field(schema, "url")["name"])
                    title = fields.get(_field(schema, "title")["name"])
                    if not url:
                        raise ValueError("请先为该条目补充论文链接。")
                    if paper_attachments.normalize_ref(url) != paper_attachments.normalize_ref(body["expected_url"]):
                        raise ValueError("清单中这条记录的链接已经变化，请刷新后重新选择。")
                elif set(body) == {"paper_ref", "title"} and all(isinstance(v, str) for v in body.values()):
                    url = paper_attachments.normalize_ref(body["paper_ref"])
                    title = body["title"].strip()
                    if not title:
                        title = papers.resolve_paper(url, app.data_dir).get("title", "")
                else:
                    raise ValueError("请选择清单中的论文，或提供论文链接与标题。")
                self.send_json(library.bind(match[1], url, title), cookie=cookie)
                return
        except FileNotFoundError:
            self.send_json({"error": "当前会话没有这份附件。"}, 404, cookie)
            return
        self.send_json({"error": "找不到该资源。"}, 404, cookie)


def main():
    parser = argparse.ArgumentParser(description="论文入表助手")
    parser.add_argument("--mode", choices=["local", "feishu", "feishu-cli"], default=os.getenv("PAPER_BASE_MODE", "local"))
    parser.add_argument("--agent", choices=["codex", "compatible", "rules"], help="选择真实推理后端或本地规则模式")
    parser.add_argument("--model", help="交给推理后端的模型名称")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--demo", action="store_true", help="打开独立的本地示例清单，含两篇已核对出处的论文")
    parser.add_argument("--open", action="store_true", help="启动后打开本机浏览器")
    args = parser.parse_args()
    if args.demo:
        if args.mode != "local":
            parser.error("--demo 适用于本地清单，请使用 --mode local。")
        from demo_library import prepare_demo
        args.data_dir = prepare_demo(args.data_dir / "interview-demo")
    if args.agent:
        os.environ["PAPER_AGENT_PROVIDER"] = args.agent
    if args.model:
        os.environ["PAPER_MODEL_NAME"] = args.model
    app = Application(args.mode, args.data_dir)
    if args.demo:
        app.name = "论文研读示例"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.app = app
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"论文入表助手  {url}  [{args.mode}]  Ctrl+C 退出", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
