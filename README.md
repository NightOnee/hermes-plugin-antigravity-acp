# Antigravity ACP — thin interactive client for Hermes Agent

A Hermes Agent plugin that lets you chat with **Google Antigravity** through its
official `agy acp` agent, on **your own Google sign-in**, as a plain interactive
[Agent Client Protocol](https://agentclientprotocol.com) client.

```text
you ──type a prompt──> hermes antigravity chat ──ACP/stdio──> agy acp (unmodified official binary)
                                                              └─ owns auth, history, tools, quota
```

## What this plugin is — and what it deliberately is not

Google's Antigravity terms distinguish two ways of using a consumer
(`oauth-personal`) sign-in:

- **Interactive ACP clients are fine**: an editor or client launches the
  unmodified `agy acp` binary and the user drives it interactively.
- **Agent harnesses are not**: a framework that wraps the binary inside its own
  agent loop, injecting system prompts, foreign conversation history or foreign
  tool schemas, or driving it autonomously. See Google's own clarification in
  [antigravity-cli#1542](https://github.com/google-antigravity/antigravity-cli/issues/1542).

This plugin is built to stay on the right side of that line:

| It does | It never does |
|---|---|
| Spawn the **unmodified** official binary | Patch, wrap or re-implement the agent |
| Send only prompts **you type yourself** | Inject Hermes system prompts, personas or history |
| Let `agy` own history, tools, planning, quota | Serialize Hermes tool schemas into prompts |
| Forward permission requests for **you** to decide | Auto-answer approvals or fake permission modes |
| Use the native OAuth flow, tokens stay in `agy`'s own storage | Read, proxy or refresh access/refresh tokens |
| Identify itself honestly (`hermes-antigravity-acp`) | Impersonate another client |
| Run when you start it and stop when you leave | Cron, subagents, background automation, account rotation |

Consequently the plugin registers **no model provider** (Hermes must not use
Antigravity as a model backend on a consumer sign-in), **no tools** (Hermes must
not auto-invoke it) and **no hooks**. The only surface is the
`hermes antigravity` command, driven by you.

If you want Hermes' own agent loop backed by Google models, use a Gemini API key
or Vertex AI instead — that is the supported path for framework-style use.

## Requirements

- Hermes Agent ≥ 0.21.4
- Python ≥ 3.11
- The official Antigravity CLI (`agy` or `antigravity` on `PATH`), or set
  `ANTIGRAVITY_ACP_COMMAND` to the executable
- A Google account with Antigravity access, signed in through the native flow
  (or a Gemini Enterprise / API-key / Vertex auth method the agent offers)

## Install

```sh
hermes plugins install NightOnee/hermes-plugin-antigravity-acp
```

## Use

```sh
hermes antigravity login     # native Google sign-in (browser / printed URL)
hermes antigravity status    # binary + profile status
hermes antigravity chat      # interactive session — every prompt is typed by you
hermes antigravity logout    # drop the stored sign-in for this profile
```

Inside chat:

```text
hermes-agy> /help      commands (/new /cancel /mode <id> /quit)
hermes-agy> explain this repo's auth flow        <- sent verbatim to Antigravity
```

Attachments, multiple sessions and automation are out of scope on purpose:
an interactive terminal session is the compliance boundary.

## Isolation model

- Per-install profile under `~/.hermes/plugins-data/antigravity-acp/gemini-home`
  (override with `ANTIGRAVITY_ACP_HOME`), permissions `0700`.
- The agent process is spawned with ambient Google/Gemini credential variables
  stripped (`GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`,
  `GOOGLE_CLOUD_*`, …): the instance can only use the account you signed in
  with for this profile, never whatever happens to be in the environment.
- `AGY_CLI_DISABLE_AUTO_UPDATE=1` and `AGY_ACP_FORCE_FILE_STORAGE=1` keep the
  binary and credential layout stable and instance-scoped.
- Sessions are scoped to the working directory you start `chat` in.

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/python -m pytest
```

Tests run against `tests/fake_agent.py`, a small in-repo ACP server — no real
Antigravity binary or Google account needed.

## Prior art

[T3 Code](https://github.com/pingdotgg/t3code) ships the same core architecture
(official binary + ACP + native OAuth), which Google publicly confirmed as
permitted for single-user interactive use. This plugin applies that pattern to
Hermes while deliberately dropping the parts of T3 that sit closer to the line
(prompt-injected orchestration instructions, quota probing with stored tokens,
legacy client-name spoofing).

## License

MIT
