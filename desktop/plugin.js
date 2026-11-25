// Antigravity ACP — native Desktop page.
// Plain disk-plugin ESM: React and the UI kit come from `@hermes/plugin-sdk`.
//
// Compliance shape: this page is a thin interactive ACP client. Every prompt
// sent to the backend was typed into this composer by the user. There is no
// path for the Hermes agent, cron, or any automation to drive it.

import {
  atom, host, useValue, Button, Input, EmptyState, ErrorState, GlyphSpinner,
  ScrollArea, cn, ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA,
} from '@hermes/plugin-sdk';
import { useEffect, useRef, useState } from 'react';
import { jsx, jsxs, Fragment } from 'react/jsx-runtime';

const ID = 'antigravity-acp';

// ---------------------------------------------------------------------------
// Feed model: fold the backend event stream into renderable rows.
// ---------------------------------------------------------------------------

function foldEvents(rows, events) {
  const out = rows.slice();
  for (const event of events) {
    switch (event.kind) {
      case 'user':
        out.push({ key: event.seq, type: 'user', text: event.text });
        break;
      case 'text': {
        const last = out[out.length - 1];
        if (last && last.type === 'assistant') last.text += event.text;
        else out.push({ key: event.seq, type: 'assistant', text: event.text });
        break;
      }
      case 'thought': {
        const last = out[out.length - 1];
        if (last && last.type === 'thought') last.text += event.text;
        else out.push({ key: event.seq, type: 'thought', text: event.text });
        break;
      }
      case 'tool':
        out.push({ key: event.seq, type: 'tool', title: event.title, toolKind: event.toolKind, status: event.status });
        break;
      case 'permission_request':
        out.push({
          key: event.seq, type: 'permission', requestId: event.requestId,
          title: event.title, toolKind: event.toolKind, options: event.options || [], answered: false,
        });
        break;
      case 'permission_resolved': {
        const row = out.find(r => r.type === 'permission' && r.requestId === event.requestId);
        if (row) { row.answered = true; row.chosen = event.optionId; }
        break;
      }
      case 'mode':
        out.push({ key: event.seq, type: 'system', text: `Mode: ${event.modeId}` });
        break;
      case 'turn_end':
        if (event.stopReason && event.stopReason !== 'end_turn') {
          out.push({ key: event.seq, type: 'system', text: `Turn ended: ${event.stopReason}` });
        }
        break;
      case 'error':
        out.push({ key: event.seq, type: 'error', text: event.message });
        break;
      case 'ready':
      case 'plan':
        break;
      default:
        break;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function Page({ rest }) {
  const [, force] = useState(0);
  const state = useRef({
    status: null,
    statusError: null,
    session: null,
    rows: [],
    since: 0,
    polling: false,
    closed: false,
    busy: false,
    composer: '',
    cwd: '',
    starting: false,
    startError: null,
  }).current;
  const rerender = () => force(n => n + 1);
  const bottomRef = useRef(null);

  useEffect(() => {
    let disposed = false;
    (async () => {
      try {
        const status = await rest('/status');
        if (disposed) return;
        state.status = status;
        const list = await rest('/sessions');
        if (disposed) return;
        const live = (list.sessions || [])[0];
        if (live) {
          state.session = live;
          state.since = 0;
          startPolling();
        }
      } catch (error) {
        state.statusError = error;
      }
      rerender();
    })();
    return () => { disposed = true; state.closed = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: 'end' });
  });

  async function startPolling() {
    if (state.polling) return;
    state.polling = true;
    while (!state.closed && state.session) {
      try {
        const result = await rest(`/sessions/${state.session.handle}/events?since=${state.since}&wait=20`, { timeoutMs: 30000 });
        if (state.closed) return;
        if (result.events && result.events.length) {
          state.rows = foldEvents(state.rows, result.events);
          state.since = result.next;
          rerender();
        }
        if (result.closed) { state.session = null; state.rows = []; state.since = 0; rerender(); return; }
      } catch (error) {
        await new Promise(resolve => setTimeout(resolve, 1500));
      }
    }
  }

  async function startSession() {
    state.starting = true; state.startError = null; rerender();
    try {
      const created = await rest('/sessions', {
        method: 'POST',
        body: { cwd: state.cwd.trim() || undefined },
      });
      state.session = { handle: created.handle, agentSessionId: created.agentSessionId };
      state.rows = []; state.since = 0; state.closed = false;
      startPolling();
    } catch (error) {
      state.startError = error;
    } finally {
      state.starting = false;
      rerender();
    }
  }

  async function closeSession() {
    if (!state.session) return;
    const handle = state.session.handle;
    state.session = null; state.rows = []; state.since = 0; state.closed = true;
    try { await rest(`/sessions/${handle}`, { method: 'DELETE' }); } catch { /* already gone */ }
    state.closed = false;
    rerender();
  }

  async function send() {
    const text = state.composer.trim();
    if (!text || !state.session) return;
    state.composer = '';
    try {
      await rest(`/sessions/${state.session.handle}/messages`, { method: 'POST', body: { text } });
    } catch (error) {
      state.rows = [...state.rows, { key: `local-${Date.now()}`, type: 'error', text: String(error && error.message || error) }];
    }
    rerender();
  }

  async function answerPermission(row, optionId) {
    row.answered = true; row.chosen = optionId; rerender();
    try {
      await rest(`/sessions/${state.session.handle}/permissions`, {
        method: 'POST', body: { requestId: row.requestId, optionId },
      });
    } catch { /* expired */ }
  }

  async function cancelTurn() {
    if (!state.session) return;
    try { await rest(`/sessions/${state.session.handle}/cancel`, { method: 'POST' }); } catch { /* noop */ }
  }

  // -- render ----------------------------------------------------------------

  if (state.statusError) {
    return jsx('div', { className: 'p-4', children: jsx(ErrorState, { title: 'Backend unavailable', description: String(state.statusError && state.statusError.message || state.statusError) }) });
  }
  if (!state.status) {
    return jsx('div', { className: 'flex h-full items-center justify-center', children: jsx(GlyphSpinner, {}) });
  }
  if (state.status.binaryFound === false) {
    return jsx('div', {
      className: 'p-4',
      children: jsx(EmptyState, {
        title: 'Antigravity CLI not found on the server',
        description: state.status.hint || 'Install Google Antigravity on the machine running the Hermes backend.',
      }),
    });
  }

  if (!state.session) {
    return jsxs('div', {
      className: 'mx-auto flex h-full max-w-md flex-col items-center justify-center gap-4 p-6',
      children: [
        jsx('div', { className: 'text-lg font-medium', children: 'Antigravity' }),
        jsx('p', {
          className: 'text-center text-sm text-(--ui-text-secondary)',
          children: 'Interactive chat with the official Antigravity agent on your own Google sign-in. Every prompt here is typed by you; the agent owns its tools, history and quota.',
        }),
        jsx(Input, {
          value: state.cwd,
          onChange: event => { state.cwd = event.target.value; rerender(); },
          placeholder: 'Working directory on the server (optional)',
          className: 'w-full',
        }),
        state.startError ? jsx('div', { className: 'text-sm text-(--ui-danger-text, #c00)', children: String(state.startError.message || state.startError) }) : null,
        jsx(Button, {
          onClick: startSession,
          disabled: state.starting,
          children: state.starting ? 'Starting…' : 'Start session',
        }),
        jsx('p', {
          className: 'text-center text-xs text-(--ui-text-tertiary)',
          children: state.status.credentialFilesPresent
            ? 'A sign-in exists in this profile.'
            : 'Not signed in yet — sign in once on the server (hermes antigravity login) or the first session will walk you through it.',
        }),
      ],
    });
  }

  const rows = state.rows;
  return jsxs('div', {
    className: 'flex h-full flex-col',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-border) px-3 py-2',
        children: [
          jsx('span', { className: 'text-sm font-medium', children: 'Antigravity' }),
          jsx('span', { className: 'truncate text-xs text-(--ui-text-tertiary)', children: state.session.agentSessionId || state.session.handle }),
          jsx('div', { className: 'flex-1' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: cancelTurn, children: 'Cancel turn' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: closeSession, children: 'Close' }),
        ],
      }),
      jsx(ScrollArea, {
        className: 'flex-1 px-3 py-2',
        children: jsxs('div', {
          className: 'mx-auto flex max-w-2xl flex-col gap-2',
          children: [
            rows.length === 0
              ? jsx('div', { className: 'py-8 text-center text-sm text-(--ui-text-tertiary)', children: 'Type below to talk to Antigravity.' })
              : rows.map(row => jsx(Row, { row, answerPermission }, row.key)),
            jsx('div', { ref: bottomRef }),
          ],
        }),
      }),
      jsxs('div', {
        className: 'border-t border-(--ui-border) p-3',
        children: [
          jsxs('div', {
            className: 'mx-auto flex max-w-2xl items-end gap-2',
            children: [
              jsx('textarea', {
                value: state.composer,
                rows: 2,
                placeholder: 'Message Antigravity… (Enter to send, Shift+Enter for newline)',
                className: cn(
                  'flex-1 resize-none rounded-md border border-(--ui-border) bg-(--ui-bg) px-2 py-1.5 text-sm',
                  'outline-none focus:border-(--ui-accent)',
                ),
                onChange: event => { state.composer = event.target.value; rerender(); },
                onKeyDown: event => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                },
              }),
              jsx(Button, { onClick: send, disabled: !state.composer.trim(), children: 'Send' }),
            ],
          }),
        ],
      }),
    ],
  });
}

function Row({ row, answerPermission }) {
  switch (row.type) {
    case 'user':
      return jsxs('div', {
        className: 'self-end rounded-lg bg-(--ui-accent-soft, rgba(80,120,255,0.12)) px-3 py-1.5 text-sm',
        children: [jsx('div', { className: 'mb-0.5 text-[0.65rem] uppercase tracking-wide text-(--ui-text-tertiary)', children: 'you' }), row.text],
      });
    case 'assistant':
      return jsxs('div', {
        className: 'self-start rounded-lg bg-(--ui-bg-secondary, rgba(128,128,128,0.08)) px-3 py-1.5 text-sm whitespace-pre-wrap',
        children: [jsx('div', { className: 'mb-0.5 text-[0.65rem] uppercase tracking-wide text-(--ui-text-tertiary)', children: 'antigravity' }), row.text],
      });
    case 'thought':
      return jsx('div', { className: 'self-start text-xs italic text-(--ui-text-tertiary) whitespace-pre-wrap', children: row.text });
    case 'tool':
      return jsxs('div', {
        className: 'self-start text-xs text-(--ui-text-secondary)',
        children: ['⚙ ', row.title || 'tool', row.status ? ` — ${row.status}` : ''],
      });
    case 'permission':
      return jsxs('div', {
        className: 'self-stretch rounded-md border border-(--ui-border) px-3 py-2',
        children: [
          jsxs('div', { className: 'mb-1 text-sm', children: ['Antigravity asks: ', row.title || 'permission'] }),
          row.answered
            ? jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: row.chosen ? `Answered: ${row.chosen}` : 'Dismissed' })
            : jsx('div', {
                className: 'flex flex-wrap gap-2',
                children: (row.options || []).map(option => jsx(Button, {
                  size: 'sm',
                  variant: option.kind && option.kind.startsWith('reject') ? 'ghost' : 'default',
                  onClick: () => answerPermission(row, option.optionId),
                  children: option.name || option.optionId,
                }, option.optionId)),
              }),
        ],
      });
    case 'error':
      return jsx('div', { className: 'self-start text-sm text-(--ui-danger-text, #c00)', children: row.text });
    case 'system':
    default:
      return jsx('div', { className: 'self-center text-xs text-(--ui-text-tertiary)', children: row.text || '' });
  }
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

export default {
  id: ID,
  name: 'Antigravity ACP',
  register(ctx) {
    const page = () => jsx(Page, { rest: ctx.rest });
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/antigravity' }, render: page },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/antigravity', label: 'Antigravity', codicon: 'comment-discussion' } },
      {
        id: 'open', area: PALETTE_AREA,
        data: {
          id: 'antigravity.open', label: 'Open Antigravity',
          keywords: ['antigravity', 'google', 'acp', 'gemini'],
          run: () => host.navigate('/antigravity'),
        },
      },
    ]);
  },
};
