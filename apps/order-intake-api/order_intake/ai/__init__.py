"""LINE customer AI assistant: OpenAI-compatible LLM + RAG over ERP knowledge.

The assistant never touches a database and never holds channel or ERP write
credentials beyond the scoped service token: every capability is a thin tool
over ``nextgen_erp`` whitelisted methods, always keyed by the verified LINE
sender id from the webhook.
"""

from .assistant import AIAssistant

__all__ = ["AIAssistant"]
