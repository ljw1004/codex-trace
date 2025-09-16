# `codex mcp` architecture and behavior

## Entry point and runtime
- The `codex mcp` subcommand in the CLI simply invokes `codex_mcp_server::run_main` (`codex-rs/cli/src/main.rs:162`). The real implementation lives in the `codex-mcp-server` crate.
- `run_main` (`codex-rs/mcp-server/src/lib.rs:27`) sets up a Tokio runtime with three tasks:
  - a stdin reader that deserializes newline-delimited `mcp_types::JSONRPCMessage` values from standard input;
  - a processor task that routes each message; and
  - a stdout writer that serializes every outgoing message back to newline-delimited JSON on standard output.
- The command therefore runs a long-lived Model Context Protocol (MCP) server that communicates exclusively over stdio. No sockets or transports beyond stdio are used.
- Configuration is loaded at startup via `codex_core::config::Config::load_with_cli_overrides`, so any `codex mcp -c key=value` overrides are honored before the first request is processed.

## Protocol stack and libraries
- JSON-RPC and MCP wire types are provided by the workspace-local `mcp-types` crate, which is generated from the official MCP 2025-06-18 schema. The server does not rely on a higher-level MCP framework; it hand-writes the dispatcher in `MessageProcessor`.
- Tokio channels (`tokio::sync::mpsc`) are used for internal fan-out of incoming requests and outgoing messages.
- Tracing is used for diagnostics (`tracing_subscriber::fmt` installed in `run_main`).
- MCP features that are not implemented yet are surfaced only as log lines; no placeholder responses are sent.

## MCP handshake and core requests
- `initialize`: handled in `MessageProcessor::handle_initialize` (`codex-rs/mcp-server/src/message_processor.rs:222`). Only the tools capability is advertised (`ServerCapabilitiesTools { list_changed: Some(true) }`). Prompts, resources, completions, logging, and experimental capabilities are `None`.
- Duplicate initialize calls yield an `INVALID_REQUEST` JSON-RPC error (message: "initialize called more than once").
- `ping`: returns an empty result object.
- Other standard MCP request kinds (resources, prompts, completions, logging set-level) are parsed but only logged; no responses are emitted because the server does not claim support for those capabilities.

## Tools
### `codex`
- Described by `CodexToolCallParam` (`codex-rs/mcp-server/src/codex_tool_config.rs:15`). Required field: `prompt`. Optional overrides: `model`, `profile`, `cwd`, `approval-policy`, `sandbox`, `config` (a map of CLI override keys), `base-instructions`, and `include-plan-tool`.
- The JSON schema exposed during `tools/list` is defined in the same file and verified by unit tests.
- When invoked (`MessageProcessor::handle_tool_call_codex`, `codex-rs/mcp-server/src/message_processor.rs:317`):
  - Input is parsed and converted into a `codex_core::config::Config` via `CodexToolCallParam::into_config`.
  - A new Codex conversation is created (`ConversationManager::new_conversation`).
  - The server immediately emits a `codex/event` notification that contains the `SessionConfigured` event; the notification has an `_meta.request_id` pointing back to the outstanding MCP `tools/call` request (`codex-rs/mcp-server/src/codex_tool_runner.rs:43`).
  - The initial user prompt is submitted to the conversation (`Op::UserInput`).
  - `run_codex_tool_session_inner` then streams every Codex event as `codex/event` notifications. Selected events trigger extra behavior:
    - `ExecApprovalRequest` → an MCP `elicitation` request asking the client to approve shell execution (`codex-rs/mcp-server/src/exec_approval.rs`). The client must reply with a `decision` (`Approve`/`Deny`), which the server forwards to Codex.
    - `ApplyPatchApprovalRequest` → an MCP `elicitation` request for patch approval (`codex-rs/mcp-server/src/patch_approval.rs`).
    - `TaskComplete` → fulfills the original `tools/call` by returning a `CallToolResult` whose sole text block is the agent's final reply.
    - `Error` → returns an error result containing the Codex error message.
  - Any transport failure in `conversation.next_event()` also returns a `CallToolResult` flagged with `is_error: true`.
  - The server records a mapping from the MCP request id to the Codex conversation id so later `notifications/cancelled` can interrupt the Codex run.

### `codex-reply`
- The schema is `CodexToolCallReplyParam` (`codex-rs/mcp-server/src/codex_tool_config.rs:113`). Fields: `conversationId` (stringified UUID) and `prompt`.
- `MessageProcessor::handle_tool_call_codex_session_reply` (`codex-rs/mcp-server/src/message_processor.rs:366`) looks up an existing conversation, submits the new user input, and reuses the same event-streaming logic as the initial `codex` tool.
- Cancellation handling and approval elicitations behave identically to the first call.

### Tool discovery and errors
- `tools/list` (`message_processor.rs:305`) always returns the two tool descriptors above. `next_cursor` is never used.
- Any other `tools/call` name yields an error result with `is_error: true` and a user-visible `Unknown tool '<name>'` message.

## Prompts
- `prompts/list` and `prompts/get` are parsed but only logged (`message_processor.rs:286-300`). The server neither advertises the prompts capability nor returns data, so prompts are effectively unsupported today.

## Resources
- `resources/list`, `resources/templates/list`, `resources/read`, `resources/subscribe`, and `resources/unsubscribe` are all stubs that just log (`message_processor.rs:256-280`). Corresponding capability fields are absent from the initialize response, so MCP clients should not expect resource support.

## Completions
- `completion/complete` is logged and ignored (`message_processor.rs:520`). The completions capability is `None`, so completions are not offered.

## Notifications emitted by the server
- `codex/event` (`outgoing_message.rs:109`): emitted for every Codex `Event` that arises during a tool invocation or certain Codex-side operations. Payload shape:
  - `_meta.request_id` (when the notification belongs to a live `tools/call`)
  - The serialized `Event` payload from `codex-core`, untouched.
- `codex/event/<Variant>` (`codex_message_processor.rs:1099`): produced when the Codex-specific `addConversationListener` subscription is active. Each notification is dynamic: method name includes the `EventMsg` variant, and `params` merges the serialized event object plus `conversationId`.
- `authStatusChange` (`codex_message_processor.rs:87`) and `loginChatGptComplete` (`codex_message_processor.rs:137`): emitted via `OutgoingMessageSender::send_server_notification`. They match the `ServerNotification` enum in `codex-rs/protocol/src/mcp_protocol.rs` and keep the client informed about authentication state.
- The server currently does **not** emit MCP-standard notifications such as `tools/list_changed`, `prompts/list_changed`, or `resources/list_changed`; handlers for those notification kinds only log inbound messages from the client.

## Cancellation support
- When the client sends `notifications/cancelled`, `MessageProcessor::handle_cancelled_notification` (`message_processor.rs:548`) maps the MCP request id back to the Codex conversation and submits `Op::Interrupt`. The `TurnAborted` event delivery then unblocks any pending `interruptConversation` calls by replying with an `InterruptConversationResponse` (`codex_message_processor.rs:1238`).

## Codex-specific JSON-RPC surface (beyond MCP)
The server multiplexes additional Codex management RPCs on the same connection before it falls back to standard MCP handling. These methods are defined in `codex-rs/protocol/src/mcp_protocol.rs` and implemented in `codex-rs/mcp-server/src/codex_message_processor.rs`:

- `newConversation`: create a Codex session using the same parameter structure as the `codex` tool but without the initial prompt. Returns the conversation id, model, reasoning effort, and rollout path.
- `listConversations`: paginated listing of stored rollouts. `RolloutRecorder::list_conversations` backs this, and the response contains `ConversationSummary` records (id, timestamp, path, preview text).
- `resumeConversation`: load a saved rollout file and (optionally) override config. Sends a `SessionConfigured` notification immediately and returns the revived conversation id plus any plain user messages present in the rollout head.
- `archiveConversation`: moves a rollout JSONL into the archived directory after verifying the path matches the supplied conversation id. Active conversations are first shut down cleanly.
- `sendUserMessage` / `sendUserTurn`: push additional input into an active conversation. Both commands respond immediately with empty result structs.
- `interruptConversation`: request a graceful interrupt; the response is deferred until the underlying conversation raises `TurnAborted`.
- `addConversationListener`: start streaming all Codex events for a conversation to the client, using the `codex/event/<Variant>` notifications described above. Returns a subscription id; `removeConversationListener` cancels it.
- `gitDiffToRemote`: fetch Git information for a working directory by delegating to `codex_core::git_info::git_diff_to_remote`.
- Authentication helpers:
  - `loginApiKey`: store an API key and broadcast `authStatusChange`.
  - `loginChatGpt`, `cancelLoginChatGpt`, `logoutChatGpt`: manage the OAuth-style ChatGPT login flow and emit both `loginChatGptComplete` and `authStatusChange` events.
  - `getAuthStatus`: report the active auth mode, optionally refreshing tokens, and optionally returning the raw token.
  - `getUserSavedConfig`: return the parsed `config.toml` as `UserSavedConfig` objects.
  - `setDefaultModel`: persist CLI-level overrides for the default model and reasoning effort.
  - `getUserAgent`: expose the Codex user agent string (`codex_core::default_client`).
  - `userInfo`: best-effort return of the cached email from `auth.json`.
- `execOneOffCommand`: execute an arbitrary command under the configured sandbox policy by driving `codex_core::exec::process_exec_tool_call`. The response returns exit code, stdout, and stderr.

These Codex-specific methods are how the Codex TUI and other integrations orchestrate conversations; they are not part of the MCP specification but share the same JSON-RPC transport.

## Other behaviors worth noting
- `MessageProcessor::process_response` forwards any response originating from the MCP client to the `OutgoingMessageSender`, fulfilling outstanding Codex-issued requests (for example, the approval `elicitation` calls mentioned above).
- All outgoing requests generated by the server (approvals, patch requests, etc.) use numeric request ids allocated by an atomic counter in `OutgoingMessageSender`.
- The server accepts CLI overrides for the sandbox binary path. When generating configs for tool calls, the optional `codex_linux_sandbox_exe` path propagated from the CLI is included so Codex can run commands in Seatbelt when available.
- Snapshot and integration tests for the MCP server live under `codex-rs/mcp-server/tests`; they exercise end-to-end tool flows, including approvals and conversation management.

## Feature summary vs MCP surface
| Feature class | Supported items |
| --- | --- |
| Prompts | None; all prompt requests are logged and otherwise ignored. |
| Resources | None; resource requests and subscriptions are unimplemented. |
| Tools | `codex`, `codex-reply`; both stream Codex events, support approvals via MCP elicitation, and honor MCP cancellation. |
| Completions | None; capability not advertised, `completion/complete` is ignored. |
| Notifications | `codex/event`, `codex/event/<Variant>` (for listeners), `authStatusChange`, `loginChatGptComplete`. |
| Extra JSON-RPC | Codex management APIs such as `newConversation`, `listConversations`, auth helpers, Git diff retrieval, command execution, etc. |

