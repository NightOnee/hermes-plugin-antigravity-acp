# Antigravity ACP — thin interactive client for Hermes Agent

A Hermes Agent plugin that adds a native **Desktop page** for chatting with
**Google Antigravity** through its official `agy acp` agent, on **your own
Google sign-in**, as a plain interactive
[Agent Client Protocol](https://agentclientprotocol.com) client.

```text
you ──type in the Desktop page──> backend plugin_api.py ──ACP/stdio──> agy acp (unmodified official binary)
        (Hermes Desktop)              (on your server)                  └─ owns auth, history, tools, quota
```

## What this plugin is — and what it deliberately is not

Google's Antigravity terms distinguish two ways of using a consumer
(`oauth-personal`) sign-in:

- **Interactive ACP clients are fine**: a client launches the unmodified
  `agy acp` binary and the user drives it interactively.
- **Agent harnesses are not**: a framework that wraps the binary inside its own
  agent loop, injecting system prompts, foreign conversation history or foreign
  tool schemas, or driving it autonomously. See Google's own clarification in
  [antigravity-cli#1542](https://github.com/google-antigravity/antigravity-cli/issues/1542).

This plugin is built to stay on the right side of that line:

| It does | It never does |
|---|---|
| Spawn the **unmodified** official binary | Patch, wrap or re-implement the agent |
| Send only prompts **you type yourself** in the Desktop page | Inject Hermes system prompts, personas or history |
| Let `agy` own history, tools, planning, quota | Serialize Hermes tool schemas into prompts |
| Forward permission requests for **you** to decide | Auto-answer approvals or fake permission modes |
| Use the native OAuth flow, tokens stay in `agy`'s own storage | Read, proxy or refresh access/refresh tokens |
| Identify itself honestly (`hermes-antigravity-acp`) | Impersonate another client |
| Run when you open the page and stop when you close it | Cron, subagents, background automation, account rotation |

Consequently the plugin registers **no model provider**, **no tools**, **no
hooks** and **no CLI automation surface** — a test enforces this invariant. If
you want Hermes' own agent loop backed by Google models, use a Gemini API key
or Vertex AI instead; that is the supported path for framework-style use.

## Architecture

- **`dashboard/plugin_api.py`** — FastAPI router mounted by the Hermes web
  server at `/api/plugins/antigravity-acp/`. Manages `agy acp` subprocesses on
  the machine running the backend (your VPS), one process per chat session,
  with a per-session seq-numbered event log. Streaming reaches the UI through
  long-polling (`GET /sessions/{h}/events?since=&wait=`), which works through
  every connection mode Hermes supports.
- **`dashboard/agy_backend.py`** — dependency-free ACP client (ndjson JSON-RPC
  over stdio), profile isolation, environment scrubbing.
- **`desktop/plugin.js`** — native Desktop disk plugin (plain ESM, no build):
  sidebar entry + `/antigravity` page with the chat UI, permission cards,
  cancel/close controls.

## Install

1. Install the backend plugin on the machine running your Hermes backend:

   ```sh
   hermes plugins install NightOnee/hermes-plugin-antigravity-acp
   ```

2. Enable the Desktop page. Hermes Desktop discovers
   `plugins/antigravity-acp/desktop/plugin.js` in the installed package and
   copies it into its local `desktop-plugins/` directory — open
   **Capabilities → Plugins** and use **Rescan**, then enable it. If discovery
   does not pick it up, copy it manually on the machine running Desktop:

   ```sh
   cp desktop/plugin.js "$HERMES_HOME/desktop-plugins/antigravity-acp/plugin.js"
   ```

   (`$HERMES_HOME` is `~/.hermes` by default.)

3. Make sure the **server** (where the backend runs) has the official
   Antigravity CLI installed (`agy` or `antigravity` on `PATH`, or set
   `ANTIGRAVITY_ACP_COMMAND`).

4. Sign in once with your Google account. The plugin's profile lives on the
   server under `~/.hermes/plugins-data/antigravity-acp/gemini-home`. On a
   headless server the native OAuth flow prints a sign-in URL; completing it is
   the one setup step that may need a shell on the server (or ask one of your
   CLI-capable agents to run it). **This auth flow is the least-tested part —
   see the caveats below.**

5. Open **Antigravity** in the Desktop sidebar and chat.

## Isolation model

- Per-install profile under `~/.hermes/plugins-data/antigravity-acp/gemini-home`
  on the server (override with `ANTIGRAVITY_ACP_HOME`), permissions `0700`.
- The agent process is spawned with ambient Google/Gemini credential variables
  stripped (`GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`,
  `GOOGLE_CLOUD_*`, …): the instance can only use the account you signed in
  with for this profile, never whatever happens to be in the environment.
- `AGY_CLI_DISABLE_AUTO_UPDATE=1` and `AGY_ACP_FORCE_FILE_STORAGE=1` keep the
  binary and credential layout stable and instance-scoped.
- Sessions live in the backend process memory: a gateway/plugin-host restart
  ends live chats (the agent's own history is preserved by `agy`; resume is on
  the roadmap).

## Status and caveats

**Tested** (8 tests, in-repo fake ACP server): environment scrubbing, spawn
argv, session lifecycle, streaming into the event log, long-poll draining,
permission allow/dismiss round-trips, session manager, and the
no-tools/no-hooks/no-CLI compliance invariant.

**Not yet verified against reality** — expect friction here on first use:

- The real `agy` binary's exact behaviors (auth URL printing, mode names,
  session update shapes) are implemented from the ACP spec and T3 Code's
  implementation, not from a live run.
- Headless OAuth on a VPS: the loopback redirect can't complete on a server
  without a browser. T3 solves this with a paste-the-callback flow and
  `agy acp --login`; this plugin does not implement that yet — for now, sign in
  on the server once by whatever method works (CLI login, or run `agy` login
  interactively there).
- Hermes plugin-host process semantics: if your deployment runs plugin code in
  an isolated host process, session state lives there — fine while the host
  stays up, gone when it restarts.
- Attachments, terminals and multi-session UI are future work.

## Development

```sh
python -m venv .venv && .venv/bin/pip install pytest agent-client-protocol
.venv/bin/python -m pytest
```

Tests run against `tests/fake_agent.py`, a small in-repo ACP server built on
the official `agent-client-protocol` Python SDK (test-only dependency) — no
real Antigravity binary or Google account needed.

## Prior art

[T3 Code](https://github.com/pingdotgg/t3code) ships the same core architecture
(official binary + ACP + native OAuth), which Google publicly confirmed as
permitted for single-user interactive use. This plugin applies that pattern to
Hermes while deliberately dropping the parts of T3 that sit closer to the line
(prompt-injected orchestration instructions, quota probing with stored tokens,
legacy client-name spoofing).

## License

MIT
