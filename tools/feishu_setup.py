"""将指定的飞书多维表格链接保存为本机演示的目标。"""

import argparse
import getpass
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, quote, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from table_store import FeishuTable, LarkCliTable, TableError


class TargetLink(NamedTuple):
    host: str
    kind: str
    token: str
    table_id: str
    view_id: str

    def address(self):
        query = urlencode([(key, value) for key, value in (
            ("table", self.table_id), ("view", self.view_id)) if value])
        return f"https://{self.host}/{self.kind}/{self.token}" + ("?" + query if query else "")


def _parse_target(value):
    if not isinstance(value, str) or "\\" in value or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("请输入完整的飞书多维表格链接。")
    url = urlsplit(value.strip())
    host = (url.hostname or "").lower()
    domains = ("feishu.cn", "larksuite.com", "larkoffice.com")
    if (url.scheme != "https" or url.username is not None or url.password is not None
            or url.port not in (None, 443) or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)
            or not any(host == d or host.endswith("." + d) for d in domains)):
        raise ValueError("请输入飞书或 Lark 的 HTTPS 多维表格链接。")
    route = re.fullmatch(r"/(base|wiki)/([A-Za-z0-9_-]+)/?", url.path)
    if not route:
        raise ValueError("请从多维表格复制含 /base/ 或 /wiki/ 的地址。")
    params = parse_qs(url.query, keep_blank_values=True)
    coords = {}
    for key, prefix in (("table", "tbl"), ("view", "vew")):
        values = params.get(key, [])
        if values and (len(values) != 1 or not re.fullmatch(prefix + r"[A-Za-z0-9_-]+", values[0])):
            raise ValueError(f"链接里的 {key} 参数无效，请点开目标数据表后再复制地址。")
        coords[key] = values[0] if values else ""
    return TargetLink(host, route[1], route[2], coords["table"], coords["view"])


def target_from_url(value):
    target = _parse_target(value)
    return target.kind, target.token, target.table_id


def browser_url(config, effective_base, effective_table):
    """只为当前实际使用的 Base 和数据表生成浏览地址。旧配置可重新绑定。"""
    if (not isinstance(config, dict) or not isinstance(effective_base, str)
            or not isinstance(effective_table, str) or config.get("app_token") != effective_base
            or config.get("table_id") != effective_table or not re.fullmatch(r"[A-Za-z0-9_-]+", effective_base)
            or not re.fullmatch(r"tbl[A-Za-z0-9_-]+", effective_table)):
        return None
    try:
        source = _parse_target(config.get("source_url"))
        if source.table_id and source.table_id != effective_table:
            return None
        if source.kind == "base":
            if source.token != effective_base:
                return None
        else:
            # Wiki 的节点标识不同于 Base。仅接受配置器解析并保存的正式落点。
            resolved = _parse_target(config.get("resolved_url"))
            if resolved != TargetLink(source.host, "base", effective_base, effective_table, source.view_id):
                return None
        return TargetLink(source.host, "base", effective_base, effective_table, source.view_id).address()
    except ValueError:
        return None


def choose_table(api, app_token):
    path = "/bitable/v1/apps/" + quote(app_token, safe="") + "/tables"
    items, seen = [], set()
    query = {"page_size": 100}
    while True:
        data = api._request("GET", path, query=query)["data"]
        items.extend(data.get("items") or [])
        if not data.get("has_more"):
            break
        token = data.get("page_token")
        if not token or token in seen:
            raise TableError("未能完整读取飞书数据表列表。")
        seen.add(token)
        query["page_token"] = token
    if not items:
        raise TableError("目标文件中没有可见的数据表。")
    if len(items) == 1:
        return items[0]
    if not sys.stdin.isatty():
        raise ValueError("链接里有多张表，请在飞书内选中目标数据表后复制带 table 参数的网址。")
    for idx, item in enumerate(items, 1):
        print(f"{idx}. {item.get('name', '未命名数据表')}")
    raw = input("选择要绑定的数据表序号：").strip()
    if not raw.isdecimal() or not 1 <= int(raw) <= len(items):
        raise ValueError("输入序号无效，目标配置尚未写入。")
    return items[int(raw) - 1]


def resolve_target(url, mode):
    source = _parse_target(url)
    route, token, table_id = source.kind, source.token, source.table_id
    if route == "base" and table_id:
        return {"app_token": token, "table_id": table_id, "source_url": source.address()}
    # 链接未给出全部坐标时，仅用所选身份读取 Wiki 节点或数据表列表。
    if mode == "app":
        api = FeishuTable(":memory:", os.environ.get("FEISHU_APP_ID", ""),
                          os.environ.get("FEISHU_APP_SECRET", ""), token, table_id or "lookup")
    else:
        api = LarkCliTable(":memory:", os.getenv("LARK_CLI", "lark-cli"), token, table_id or "lookup")
    try:
        if route == "wiki":
            data = api._request("GET", "/wiki/v2/spaces/get_node", query={"token": token})["data"]
            node = data["node"]
            if node.get("obj_type") != "bitable" or not node.get("obj_token"):
                raise TableError("此知识库节点指向的内容类型不属于飞书多维表格。")
            token = node["obj_token"]
        result = {"app_token": token, "table_id": table_id, "source_url": source.address()}
        if not table_id:
            selected = choose_table(api, token)
            result["table_id"] = selected["table_id"]
            if selected.get("name"):
                result["table_name"] = selected["name"]
        if route == "wiki":
            result["resolved_url"] = TargetLink(source.host, "base", token, result["table_id"], source.view_id).address()
        if not browser_url(result, token, result["table_id"]):
            raise TableError("飞书返回的目标数据表地址无效。")
        return result
    finally:
        api._db.close()


def save_target(result, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            json.dump(result, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
        except Exception:
            tmp.close()
            temp_path.unlink(missing_ok=True)
            raise
    try:
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="根据飞书表格链接配置本机目标。")
    parser.add_argument("--mode", choices=["app", "cli"], required=True, help="app 使用应用身份，cli 使用已登录用户身份")
    parser.add_argument("--url", help="目标表地址，省略时以隐藏输入方式粘贴")
    args = parser.parse_args()
    try:
        url = args.url or getpass.getpass("粘贴目标表链接并回车（输入隐藏）：")
        result = resolve_target(url, args.mode)
        save_target(result, ROOT / "data" / "feishu-target.json")
    except (TableError, ValueError, OSError, EOFError, KeyError) as exc:
        print(f"配置未保存。{exc}", file=sys.stderr)
        return 1
    mode = "feishu" if args.mode == "app" else "feishu-cli"
    print("目标已保存至本机 data/feishu-target.json。请将此配置保留在自己电脑上。")
    if args.mode == "app":
        print("应用模式需设置 FEISHU_APP_ID 与 FEISHU_APP_SECRET，并授予目标数据表权限。")
    else:
        print("用户模式使用已登录的官方 lark-cli；若未加入 PATH，可设置 LARK_CLI 为程序完整路径。")
    print(f"在项目目录运行 python app.py --mode {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
