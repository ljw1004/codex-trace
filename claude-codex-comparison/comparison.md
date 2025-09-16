## Claude vs Codex comparison for code-research

I picked a smallish-sized codebase (that of Codex CLI itself) and did codebase research on it using Claude and Codex.

**Code-research prompt**

I want to learn full details about the behavior of the `codex mcp` command...
1. It runs an MCP server, right?
2. Does this MCP server communicate over stdio?
3. Does it use some common library for MCP (or for JsonRPC underneath it)? How much does it "roll its own" vs use existing frameworks?
4. I believe that MCP supports Prompts, Resources, Tools, Completion. For each of those four headings I'd like you to make an exhaustive list of all the things it supports, with a comprehensive description of each.
5. I believe that MCP also sends notifications. I'd like an exhaustive list of all notifications it sends.

Have I missed anything in terms of what MCP supports? Does codex mcp support anything beyond MCP?

Please document all your findings in a new file "mcp.md". Thank you!

[For running Claude, I renamed AGENTS.md to CLAUDE.md]

[For a second run of Claude, I added "I want you to ultrathink in your response because it has to be thorough. Please include file+line references."]

**Results**
* Codex/GPT-5-codex - codex-result.md - codex-trace.html
   * 9 minutes
   * 53 function calls of which 4 were update_plan and 49 were shell (23 sed, 6 ls, 9 rg, 8 nl, 3 cat)
   * 54 assistant messages (parallel tool calling not supported)
   * 48 reasoning blocks, i.e on most messages, typically 1-2k (encrypted)
* Claude/Opus-4.1 - claude-result.md - claude-trace.html
   * 3 minutes
   * 19 tool calls of which 5 were TodoWrite, 11 Read, 2 Grep, 1 Write
   * 20 assistant messages (it didn't end up chosing to use parallel tool calls)
   * This prompt didn't engage "think" or "ultrathink" reasoning blocks. I tried a second time with a modified prompt to ultrathink, but it didn't change significantly.

The Codex results are clearly better -- Claude had an inaccuracy, and missed some useful details, was less comprehensive, and didn't generate the same holistic overview.
