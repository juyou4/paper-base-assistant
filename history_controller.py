"""把工作台的私人对话、阅读位置和写入回执连接到当前浏览器与数据表。"""

import hashlib
import re
import sqlite3
import uuid

import history_semantics as semantics
from table_store import TableError
from workspace_history import WorkspaceHistory
import batch_recovery
import execution_context


def owner_key(cookie):
    return hashlib.sha256(cookie.encode("ascii")).hexdigest()


class HistoryController:
    def __init__(self, data_dir, mode):
        self.store = WorkspaceHistory(data_dir)
        self.data_dir, self.mode = data_dir, mode

    def attach(self, chat, cookie):
        chat.history_owner = owner_key(cookie)
        chat.history_scope = None
        chat.history_conversation_id = None
        chat.execution_actor = None
        chat.attachment_owner = cookie
        chat.browser_owner = cookie

    def bind(self, chat):
        actor = execution_context.ACTIVE.get() or execution_context.actor_for(chat.table)
        old = chat.execution_actor
        if old and old["key"] != actor["key"]:
            from activity import History
            chat.table_view_id = uuid.uuid4().hex
            chat.activity = History()
            chat.last_library_results = None
        chat.execution_actor = actor
        chat.attachment_owner = (chat.browser_owner if actor["kind"] == "local" else
                                 chat.browser_owner + ":" + (actor["key"] or chat.table_view_id))
        scope = semantics.stable_scope(chat, self.mode, self.data_dir)
        if scope != chat.history_scope:
            context = self.store.active_context(chat.history_owner, scope)
            chat.history_scope = scope
            self._activate(chat, context)
        return self.ids(chat)

    def _activate(self, chat, context):
        semantics.clear_focus(chat)
        chat.history_transition = None
        chat.history_conversation_id = context["active_id"]
        self._preferences(chat, context)
        saved = context.get("focus")
        if saved and context["settings"]["resume_focus"] and context["settings"]["save_history"]:
            if not semantics.restore_focus(chat, {**saved, "scope": chat.history_scope}):
                self.store.set_focus(chat.history_owner, chat.history_scope, chat.history_conversation_id, None)

    @staticmethod
    def ids(chat):
        return {"history_scope": chat.history_scope, "history_conversation_id": chat.history_conversation_id,
                "history_focus": semantics.export_focus(chat)}

    @staticmethod
    def _preferences(chat, state):
        rules = {}
        if state["settings"]["use_preferences"]:
            for item in sorted(state["preferences"], key=lambda i: i["created"]):
                rule = semantics.parse_preference(item["text"])
                if rule:
                    rules[rule[0]] = rule[1]
        chat.history_preferences = rules

    @staticmethod
    def check(chat, scope, conversation_id):
        if (scope, conversation_id) != (chat.history_scope, chat.history_conversation_id):
            raise ValueError("当前数据表或对话已经切换，请刷新后再操作。")

    def snapshot(self, chat):
        self.bind(chat)
        state = self.store.snapshot(chat.history_owner, chat.history_scope)
        state["scope"] = chat.history_scope
        state["focus"] = semantics.export_focus(chat)
        return state

    def update(self, chat, body):
        self.bind(chat)
        if not isinstance(body, dict):
            raise ValueError("请指定对话或偏好操作。")
        self.check(chat, body.get("scope"), body.get("conversation_id"))
        owner, scope, current = chat.history_owner, chat.history_scope, chat.history_conversation_id
        action = body.get("action")
        payload = {k: v for k, v in body.items() if k not in ("scope", "conversation_id", "action")}
        if action == "recheck_batch" and set(payload) == {"target_id"} and isinstance(payload["target_id"], str):
            batch_recovery.recheck(self.store, owner, scope, chat.table, payload["target_id"])
            state = self.store.snapshot(owner, scope)
        elif action in ("new", "clear_history", "clear_focus") and not payload:
            if action == "new":
                state = self.store.new_conversation(owner, scope)
            elif action == "clear_history":
                state = self.store.clear_conversations(owner, scope)
            else:
                if chat.pending:
                    raise ValueError("当前还有待确认操作，请先处理或取消预览，再清除论文上下文。")
                semantics.clear_focus(chat)
                state = self.store.set_focus(owner, scope, current, None)
        elif action in ("open", "delete", "delete_preference") and set(payload) == {"target_id"}:
            target = payload["target_id"]
            if not isinstance(target, str) or not target:
                raise ValueError("请选择列表中的一项。")
            method = {"open": self.store.select_conversation, "delete": self.store.delete_conversation,
                      "delete_preference": self.store.delete_preference}[action]
            state = method(owner, scope, target)
        elif action == "settings" and payload and set(payload) <= {"save_history", "use_preferences", "resume_focus"}:
            state = self.store.set_settings(owner, scope, **payload)
        elif action == "add_preference" and set(payload) == {"text"}:
            text = payload["text"]
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 240:
                raise ValueError("请输入 1 至 240 字的本表偏好。")
            rule = semantics.parse_preference(text.strip())
            if not rule:
                raise ValueError("请写明证据类型和本表目标列，例如“代码链接优先写入「研究笔记」”。")
            # 实际保存成功后统一根据当前开关加载规则。
            previous = chat.history_preferences.copy()
            try:
                semantics.set_preference(chat, *rule)
                state = self.store.add_preference(owner, scope, text.strip())
            finally:
                chat.history_preferences = previous
        else:
            raise ValueError("无法识别此历史或设置操作。")
        if state["active_id"] != current:
            self._activate(chat, state)
        else:
            self._preferences(chat, state)
        state.update(scope=scope, focus=semantics.export_focus(chat))
        return state

    def _memory_command(self, chat, message):
        if chat.pending:
            return None
        if (re.search(r"上[一次批回]|刚[刚才]|上一批|最近", message) and
                re.search(r"核对|核验|复核|查[一下看]|确认.{0,4}(?:结果|状态)", message) and
                re.search(r"回填|提交|写入|成功|失败|没写|没填|断[了网]|回执|待核验|没完成", message)):
            unresolved = bool(re.search(r"没(?:有)?(?:成功|写|填|完成)|未(?:成功|提交|完成)|失败|待核验|中断|断[了网]", message))
            return {"message": batch_recovery.recheck(self.store, chat.history_owner, chat.history_scope, chat.table,
                                                      unresolved_only=unresolved)}
        if message.rstrip("？?。！!") in ("你记住了什么", "查看我的偏好", "查看本表偏好", "我的记忆设置"):
            state = self.store.active_context(chat.history_owner, chat.history_scope)
            choices = [item["text"] for item in state["preferences"]]
            switch = "已开启" if state["settings"]["use_preferences"] else "已关闭"
            return {"message": f"本表偏好{switch}。" + ("\n" + "\n".join(choices) if choices else "还没有保存写入偏好。")}
        if re.match(r"^(?:请)?(?:帮我)?(?:记住|以后|今后|下次|此后|从现在起|默认|在这张表里优先)", message):
            text = re.sub(r"^(?:请)?(?:帮我)?记住(?:我的偏好|这个偏好)?[，,:：\s]*", "", message).strip()
            if text != message and not semantics.parse_preference(text):
                text = "以后" + text
            if semantics.parse_preference(text):
                state = self.update(chat, {"action": "add_preference", "scope": chat.history_scope,
                                          "conversation_id": chat.history_conversation_id, "text": text})
                use = "新预览会优先使用该列，仍可手动更改。" if state["settings"]["use_preferences"] else "当前偏好开关已关闭。"
                return {"message": "已为当前表保存偏好。" + use}
        return None

    def respond(self, chat, message, body, request_id, options):
        old_actor = chat.execution_actor
        self.bind(chat)
        pending_id = body.get("pending_id")
        if old_actor and old_actor["key"] != chat.execution_actor["key"] and pending_id:
            raise ValueError("执行身份已切换，旧确认已失效。请用当前身份重新预览。")
        replay = (request_id, body.get("history_scope"), body.get("history_conversation_id"), pending_id, message)
        if chat.history_transition and replay == chat.history_transition[0] and not options:
            return chat.history_transition[1]
        if "history_scope" in body or "history_conversation_id" in body:
            self.check(chat, body.get("history_scope"), body.get("history_conversation_id"))
        owner, scope, thread = chat.history_owner, chat.history_scope, chat.history_conversation_id
        previous_focus = semantics.export_focus(chat)
        pending = chat.pending or {}
        stage, plan = pending.get("stage"), pending.get("plan")
        display = re.sub(r"^((?:确认(?:回填|写入|提交)|取消回填))\s+[a-f0-9]{32}$", r"\1", message)
        batch_id = pending.get("id") if stage == "evidence_confirm" else None
        key = "batch-" + batch_id if batch_id else None
        journal = batch_recovery.BatchJournal(self.store, owner, scope, thread, batch_id) if batch_id else None
        token = batch_recovery.ACTIVE.set(journal)
        identity_token = execution_context.ACTIVE.set(chat.execution_actor)
        try:
            result = self._memory_command(chat, message) or chat.respond(message, pending_id, request_id, **options)
        except (ValueError, TableError, OSError) as exc:
            operation = semantics.operation_for(message, stage, {}, error=exc, plan=plan)
            if journal and journal.operation and operation:
                operation["key"] = key
            self.store.record_turn(owner, scope, thread, request_id, display, str(exc),
                                   semantics.export_focus(chat), [operation] if operation else [])
            raise
        finally:
            batch_recovery.ACTIVE.reset(token)
            execution_context.ACTIVE.reset(identity_token)
        operation = semantics.operation_for(message, stage, result, plan=plan)
        if operation and (batch_id or pending_id) and result.get("evidence_results"):
            operation["key"] = "batch-" + (batch_id or pending_id)
        elif operation and pending_id and not result.get("pending_id") and operation["status"] != "cancelled":
            operation["key"] = "write-" + pending_id
        same_scope = scope == semantics.stable_scope(chat, self.mode, self.data_dir)
        focus = semantics.export_focus(chat) if same_scope else previous_focus
        try:
            self.store.record_turn(owner, scope, thread, request_id, display, result.get("message", ""),
                                   focus, [operation] if operation else [])
        except (sqlite3.Error, OSError):
            # 远端可能已成功，保留真实回执，让用户核对后再决定下一步。
            result["message"] = result.get("message", "") + " 本机历史未能保存，请保留本次回执。"
        self.bind(chat)
        result.update(self.ids(chat))
        confirmation = execution_context.confirmation(chat)
        if confirmation and result.get("pending_id"):
            result["confirmation"] = confirmation
            if "evidence_preview" in result:
                result["evidence_preview"]["confirmation"] = confirmation
        if not same_scope and result.get("target_changed") and pending_id and not options:
            # 建表提交成功但浏览器未收到响应时，重发同一请求只返回已有回执。
            chat.history_transition = (replay, result)
        return result
