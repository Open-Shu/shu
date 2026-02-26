PLUGIN_MANIFEST = {
    "name": "kb_search",
    "display_name": "KB Search",
    "version": "1",
    "module": "plugins.shu_kb_search.plugin:KbSearchPlugin",
    # KB access only — all searches flow through the host capability.
    # No HTTP, auth, or identity required.
    "capabilities": ["kb"],
    # Chat-callable search operations exposed as LLM tools.
    "chat_callable_ops": ["search_chunks", "search_documents", "get_document"],
    # Same operations are feed-safe for scheduled experience execution.
    "allowed_feed_ops": ["search_chunks", "search_documents", "get_document"],
}
