"""In-app Genie — ask the Exposure Book in natural language via the Conversation API (not just an embed link).

Returns Genie's own narrative + the SQL it generated + the result rows, so the answer is inspectable and
trusted (you see the query behind the number). Degrades gracefully if the space/permission isn't set.
"""
from . import config


def ask(question: str) -> dict:
    sid = config.GENIE_SPACE_ID
    if not sid:
        return {"ok": False, "reason": "no Genie space configured"}
    try:
        w = config.get_workspace_client()
        msg = w.genie.start_conversation_and_wait(space_id=sid, content=question)
        text, sql_text, rows, cols = "", "", [], []
        for att in (msg.attachments or []):
            if getattr(att, "text", None) and getattr(att.text, "content", None):
                text = att.text.content
            q = getattr(att, "query", None)
            if q is not None:
                sql_text = getattr(q, "query", "") or getattr(q, "description", "") or ""
                try:
                    res = w.genie.get_message_attachment_query_result(
                        space_id=sid, conversation_id=msg.conversation_id, message_id=msg.id, attachment_id=att.attachment_id)
                    sr = res.statement_response
                    if sr and sr.result and sr.result.data_array:
                        cols = [c.name for c in sr.manifest.schema.columns]
                        rows = [dict(zip(cols, r)) for r in sr.result.data_array][:100]
                except Exception:
                    pass
        return {"ok": True, "text": text, "sql": sql_text, "columns": cols, "rows": rows,
                "space_id": sid, "conversation_id": msg.conversation_id}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}
