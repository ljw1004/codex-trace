# Codex-trace

This is a wrapper designed to get network traces from the VSCode Codex extension.
It captures the requests posted to the OpenAI model, and the responses received back.
It pretty-prints the output: [example.html](https://ljw1004.github.io/codex-trace/example.html)

Why would you want to use this? -- The only reason I can imagine is (1) you're curious how
exactly your tool works under the hood, (2) it's too hard to figure this out by reading
the codex source code.

## Installation + use

1. Download the single file codex-trace.py, anywhere on your hard disk, and mark it executable.
2. Within VSCode, Settings > search for "codex" > CLI Executable, and point to the downloaded file.
3. Restart VSCode to pick up the change.
4. You'll find logs in ~/codex-trace, as .html files

To uninstall: delete the VSCode setting, and restart VSCode.

## How it works

The VSCode Codex extension normally works by spawning `codex mcp`, a codex binary that's distributed
within the extension. The previous section had you install codex-trace by telling it to
spawn `codex-trace.py mcp` instead. What codex-trace does is
1. Invokes the underlying `codex mcp` but with `RUST_LOG=codex_core=trace,codex_mcp_server=info`
2. Passes through stdin+stdout as normally, so the VSCode extension can talk with the MCP server
3. Intercepts stderr (the RUST_LOG debugging output), parses it, and appends it to the log
