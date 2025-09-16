# Codex MCP Server Documentation

## Overview

The Codex MCP (Model Context Protocol) server is an implementation that allows Codex to be used as an MCP server, enabling integration with MCP-compatible clients. The server is implemented in Rust and located in `codex-rs/mcp-server/`.

## 1. MCP Server Architecture

**Yes, it runs an MCP server** that communicates over **stdio** (standard input/output). The server:
- Reads JSON-RPC messages from stdin
- Processes them asynchronously
- Writes responses to stdout
- Uses line-delimited JSON for message framing

## 2. Communication Protocol

The MCP server uses **stdio-based communication**:
- Input: Reads from stdin using a `BufReader` with line-by-line processing
- Output: Writes to stdout with newline-delimited JSON
- Protocol: JSON-RPC 2.0 over stdio
- Message format: Each message is a single line of JSON followed by a newline

## 3. Library Usage

The Codex MCP implementation:
- **Uses a custom mcp-types library** (`codex-rs/mcp-types/`) that is auto-generated from MCP schema definitions
- The types are generated from JSON Schema files (versions 2025-03-26 and 2025-06-18) using a Python script
- **Does NOT use a standard MCP framework** - it implements the protocol handling directly
- Uses `serde_json` for JSON serialization/deserialization
- Uses `ts-rs` for TypeScript type generation
- The JSON-RPC handling is implemented directly in the message processor

## 4. MCP Standard Features

### 4.1 Prompts
**Currently NOT implemented**. The handlers exist but only log the requests:
- `prompts/list` - Handler exists but not implemented
- `prompts/get` - Handler exists but not implemented

### 4.2 Resources
**Currently NOT implemented**. The handlers exist but only log the requests:
- `resources/list` - Handler exists but not implemented
- `resources/templates/list` - Handler exists but not implemented
- `resources/read` - Handler exists but not implemented
- `resources/subscribe` - Handler exists but not implemented
- `resources/unsubscribe` - Handler exists but not implemented

### 4.3 Tools
**Partially implemented**. The server supports:

#### Standard MCP Tools:
- `tools/list` - Returns the list of available tools (currently "codex" and "codex-reply")
- `tools/call` - Executes tool calls

#### Codex-Specific Tools:

**"codex" Tool:**
Creates a new Codex conversation session. Parameters:
- `prompt` (required): The initial user prompt to start the conversation
- `model`: Optional model override (e.g., "o3", "o4-mini")
- `profile`: Configuration profile from config.toml
- `cwd`: Working directory for the session
- `approval-policy`: Shell command approval policy ("untrusted", "on-failure", "on-request", "never")
- `sandbox`: Sandbox mode ("read-only", "workspace-write", "danger-full-access")
- `config`: Individual config settings override
- `base-instructions`: Custom instructions to use
- `include-plan-tool`: Whether to include the plan tool

**"codex-reply" Tool:**
Continues an existing Codex conversation. Parameters:
- `conversation_id` (required): The conversation ID to continue
- `prompt` (required): The next user prompt

### 4.4 Completion
**Currently NOT implemented**. The handler exists but only logs the requests:
- `completion/complete` - Handler exists but not implemented

## 5. Notifications

The MCP server sends the following notifications:

### Standard MCP Notifications (received but not fully implemented):
- `notifications/cancelled` - Handles cancellation requests
- `notifications/progress` - Progress updates (logged only)
- `notifications/resourceListChanged` - Resource list changes (logged only)
- `notifications/resourceUpdated` - Resource updates (logged only)
- `notifications/promptListChanged` - Prompt list changes (logged only)
- `notifications/toolListChanged` - Tool list changes (logged only)
- `notifications/message` - Logging messages (logged only)

### Codex-Specific Notifications (sent by server):
- `authStatusChange` - Notifies when authentication status changes
  - Parameters: `auth_mode` (optional, "ApiKey" or "ChatGPT"), `email` (optional)
- `loginChatGptComplete` - Notifies when ChatGPT login flow completes
  - Parameters: `login_id`, `success` (boolean)

## 6. Additional Codex-Specific Features

Beyond standard MCP, the Codex server implements extensive custom functionality via additional JSON-RPC methods:

### Conversation Management:
- `newConversation` - Create a new Codex conversation with extensive configuration options
- `listConversations` - List recorded conversations with pagination
- `resumeConversation` - Resume from a rollout file
- `archiveConversation` - Archive a conversation
- `interruptConversation` - Interrupt an active conversation

### Message Handling:
- `sendUserMessage` - Send a user message to a conversation
- `sendUserTurn` - Send a complete user turn with multiple items

### Authentication:
- `loginApiKey` - Authenticate with API key
- `loginChatGpt` - Initiate ChatGPT login flow
- `cancelLoginChatGpt` - Cancel ChatGPT login
- `logoutChatGpt` - Logout from ChatGPT
- `getAuthStatus` - Get current auth status

### Configuration:
- `getUserSavedConfig` - Get saved user configuration
- `setDefaultModel` - Set the default model

### Utilities:
- `getUserAgent` - Get the Codex user agent string
- `userInfo` - Get user information
- `gitDiffToRemote` - Get git diff to remote
- `execOneOffCommand` - Execute a command under the server's sandbox

### Event Streaming:
- `addConversationListener` - Subscribe to conversation events
- `removeConversationListener` - Unsubscribe from events

### Approval Flows:
The server implements elicitation-based approval flows for:
- **Exec Approval** - Approve/reject shell command execution
  - Uses MCP's elicitation protocol for human-in-the-loop approval
  - Sends exec details: command, working directory, context
- **Patch Approval** - Approve/reject file patches
  - Also uses elicitation for reviewing file changes
  - Includes file paths, patch content, and context

## 7. Initialization

When initialized via the `initialize` method, the server:
- Returns server info with name "codex-mcp-server" and version
- Declares capabilities: Currently only `tools` with `list_changed: true`
- Sets up the user agent based on client info
- Protocol version is echoed back from the client request

## 8. Implementation Details

### Message Processing Flow:
1. JSON-RPC messages are received via stdin
2. Messages are parsed into `JSONRPCMessage` types
3. Standard MCP requests are handled by `MessageProcessor`
4. Codex-specific requests are delegated to `CodexMessageProcessor`
5. Responses are sent back via stdout

### Channel Architecture:
- Uses tokio channels for async message passing
- Bounded channel (128 capacity) for incoming messages
- Unbounded channel for outgoing messages
- Three main async tasks: stdin reader, message processor, stdout writer

### Error Handling:
- Standard JSON-RPC error responses
- Custom error codes for invalid requests and internal errors
- Graceful handling of malformed messages

## Summary

The Codex MCP server is a hybrid implementation that:
1. **Implements the MCP protocol** over stdio with JSON-RPC 2.0
2. **Partially supports standard MCP features** (mainly tools, with stubs for resources/prompts/completion)
3. **Extensively extends MCP** with Codex-specific functionality for conversation management, authentication, and approval flows
4. **Rolls its own implementation** using generated types rather than a standard MCP framework
5. **Focuses on tool-based interaction** where Codex conversations are managed as MCP tools

The implementation prioritizes Codex-specific features while maintaining MCP compatibility for tool operations, making it usable with MCP clients that primarily need tool functionality.