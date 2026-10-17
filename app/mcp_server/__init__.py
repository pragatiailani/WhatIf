"""MCP server exposing the book graph, the book text and the chat session to the inference agent.

`graph_tools`, `text_tools` and `session_tools` hold the logic as plain functions; `server`
registers them as MCP tools. Everything reaches the databases through `app.repo`.
"""
