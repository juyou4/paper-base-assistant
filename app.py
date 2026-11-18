"""论文入表助手的本机 HTTP 服务。运行 python app.py --open 即可启动。"""

import argparse
import json
import mimetypes
import os
import secrets
import threading
import time
import uuid
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import model_support
import papers
from table_store import FeishuTable, LarkCliTable, LocalTable, TableError
from workflow import Conversation


ROOT = Path(__file__).resolve().parent


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
        self.feishu_url = ""
        if mode != "local":
            from tools.feishu_setup import browser_url
            self.feishu_url = browser_url(config, base, table_id)
        self.sessions = {}
        self.lock = threading.Lock()

    def session(self, cookie):
        now = time.monotonic()
        with self.lock:
            if cookie not in self.sessions or now - self.sessions[cookie][1] > 7200:
                self.sessions = {k: v for k,v in self.sessions.items() if now - v[1] <= 7200}
                cookie = secrets.token_urlsafe(24)
                self.sessions[cookie] = [Conversation(self.table, self.data_dir, self.name), now]
            self.sessions[cookie][1] = now
            return cookie, self.sessions[cookie][0]

    def state(self, chat=None):
        agent = model_support.selection_info(chat.selected_model if chat else None)
        return {"mode": "local" if self.mode == "local" else "feishu", "table_name": self.name,
                "fields": self.table.schema(), "rows": self.table.rows(),
                "sample": {"title": papers.SAMPLE["title"], "url": "https://arxiv.org/abs/" + papers.SAMPLE_ID},
                "download_url": "/api/sample.pdf", "feishu_url": self.feishu_url,
                "capabilities": {"model": agent["enabled"], "agent": agent, "activity": True}}

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
        headers = {"Set-Cookie": f"paper_session_{self.server.server_port}={cookie}; HttpOnly; SameSite=Strict; Path=/"} if cookie else {}
        self.send_body(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code, headers)

    def browser_session(self):
        jar = SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        sid = jar.get(f"paper_session_{self.server.server_port}") or jar.get("paper_session")
        return self.server.app.session(sid.value if sid else None)

    def allowed_host(self):
        addr = self.headers.get("Host", "")
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        return addr in expected and self.headers.get("Origin", "http://" + addr) == "http://" + addr

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method):
        run = None
        cookie = None
        try:
            if not self.allowed_host():
                self.send_json({"error": "请从本机服务地址打开。"}, 403)
                return
            route = urlsplit(self.path)
            app = self.server.app
            if method == "GET" and route.path == "/api/state":
                cookie, chat = self.browser_session()
                self.send_json(app.state(chat), cookie=cookie)
            elif method == "GET" and route.path == "/api/activity":
                ids = parse_qs(route.query).get("request_id", [])
                if len(ids) != 1:
                    raise ValueError("请指定一条请求的编号。")
                request_id = str(uuid.UUID(ids[0]))
                cookie, chat = self.browser_session()
                self.send_json(chat.activity.snapshot(request_id), cookie=cookie)
            elif method == "POST" and route.path in {"/api/chat", "/api/model"}:
                if self.headers.get_content_type() != "application/json":
                    self.send_json({"error": "请求体需要为 JSON。"}, 415)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384:
                    self.send_json({"error": "指令太长或为空。"}, 400)
                    return
                body = json.loads(self.rfile.read(length))
                cookie, chat = self.browser_session()
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
                with run.recording(), chat.lock, model_support.using_model(chat.selected_model), model_support.trace_calls() as steps:
                    result = chat.respond(message, body.get("pending_id"), request_id)
                    if steps:
                        result["agent"] = {**model_support.agent_info(), "steps": steps}
                result["activity"] = run.snapshot()
                self.send_json(result, cookie=cookie)
            elif method == "GET" and route.path in {"/api/sample.pdf", "/api/pdf"}:
                target = papers.SAMPLE_ID if route.path == "/api/sample.pdf" else parse_qs(route.query).get("paper", [""])[0]
                if len(target) > 2048:
                    raise ValueError("无效的论文地址。")
                paper = papers.resolve_paper(target, app.data_dir)
                local = papers.download_pdf(paper, app.data_dir)
                self.send_body(local.read_bytes(), "application/pdf", extra={"Content-Disposition": f'inline; filename="{local.name}"'})
            elif method == "GET" and route.path in {"/", "/index.html", "/app.js", "/style.css"}:
                name = route.path.strip("/") or "index.html"
                mime = mimetypes.guess_type(name)[0] or "text/plain"
                self.send_body((ROOT / "web" / name).read_bytes(), mime + "; charset=utf-8")
            else:
                self.send_json({"error": "找不到该资源。"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, TableError) as exc:
            self.send_error_result(str(exc), 400, run, cookie)
        except Exception:
            self.send_error_result("处理失败，请检查服务配置和数据源，再重试。", 500, run, cookie)

    def send_error_result(self, message, code, run, cookie):
        result = {"error": message}
        if run is not None:
            result["activity"] = run.snapshot()
        self.send_json(result, code, cookie=cookie)


def main():
    parser = argparse.ArgumentParser(description="论文入表助手")
    parser.add_argument("--mode", choices=["local", "feishu", "feishu-cli"], default=os.getenv("PAPER_BASE_MODE", "local"))
    parser.add_argument("--agent", choices=["codex", "compatible", "rules"], help="选择真实推理后端或本地规则模式")
    parser.add_argument("--model", help="交给推理后端的模型名称")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--open", action="store_true", help="启动后打开本机浏览器")
    args = parser.parse_args()
    if args.agent:
        os.environ["PAPER_AGENT_PROVIDER"] = args.agent
    if args.model:
        os.environ["PAPER_MODEL_NAME"] = args.model
    app = Application(args.mode, args.data_dir)
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
        app.table.close()


if __name__ == "__main__":
    main()
