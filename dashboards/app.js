/*
 * AI Cloud Lab dashboard: one page, four views (Instances, Logins, Logs, User management).
 * Plain JavaScript, no build step. All data comes from window.MockApi (mock-api.js).
 * To wire this to the real Control API, replace mock-api.js with a module that exposes the
 * same calls; nothing else in this file needs to change.
 */
(function () {
  'use strict';

  const API = window.MockApi;
  const MIN = 60 * 1000;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const $ = (selector, root) => (root || document).querySelector(selector);

  // ---- formatting --------------------------------------------------------------------

  const fmt = {
    time: (ms) => new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(ms),
    clock: (ms) => new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(ms),
    dateTime: (ms) => new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(ms),
  };

  function duration(minutes) {
    const m = Math.max(0, Math.ceil(minutes));
    if (m < 1) return 'under 1 min';
    if (m < 60) return m + ' min';
    const h = Math.floor(m / 60);
    const rest = m % 60;
    return rest ? h + ' h ' + rest + ' min' : h + ' h';
  }

  function roleLabel(groups) {
    const parts = [];
    if (groups.includes('operators')) parts.push('Operator');
    if (groups.includes('user_mgrs')) parts.push('User manager');
    return parts.length ? parts.join(', ') : 'No role';
  }

  const isManager = () => !!state.session && state.session.groups.includes('user_mgrs');

  // ---- tiny DOM helpers --------------------------------------------------------------

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (value === null || value === undefined || value === false) return;
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key === 'value') el.value = value;
      else if (key === 'checked') el.checked = !!value;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (value === true) el.setAttribute(key, '');
      else el.setAttribute(key, String(value));
    });
    kids.flat(Infinity).forEach((kid) => {
      if (kid === null || kid === undefined || kid === false) return;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    });
    return el;
  }

  const ICONS = {
    cube: '<path d="M12 2.5 3.5 7v10L12 21.5 20.5 17V7L12 2.5Z" fill="currentColor"/><path d="M3.5 7 12 11.5 20.5 7M12 11.5v10" fill="none" stroke="rgba(0,0,0,.4)" stroke-width="1.3" stroke-linejoin="round"/>',
    play: '<path d="M8 5.5v13l11-6.5-11-6.5Z" fill="currentColor"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M15.5 5.5V5A1.5 1.5 0 0 0 14 3.5H6A1.5 1.5 0 0 0 4.5 5v8A1.5 1.5 0 0 0 6 14.5h.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    search: '<circle cx="10.5" cy="10.5" r="6" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="m15.2 15.2 5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    key: '<circle cx="8" cy="12" r="3.8" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M11.8 12H21M17.5 12v3.5M20 12v2.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    list: '<path d="M5 6.5h14M5 12h14M5 17.5h9" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
    users: '<circle cx="9" cy="9" r="3.3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M3.5 19c.5-3.2 2.8-5 5.5-5s5 1.8 5.5 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M15.5 6.2a3 3 0 0 1 0 5.6M17.5 14.3c1.8.6 3 2.2 3.3 4.7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4.2h-4.2" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
    arrow: '<path d="M12 5v14m0 0-5-5m5 5 5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    spinner: '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2.4" opacity=".25"/><path d="M12 4a8 8 0 0 1 8 8" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>',
    dot: '<circle cx="12" cy="12" r="6" fill="currentColor"/>',
    info: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 11v6M12 7.5v.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
    warn: '<path d="M12 3.5 2.8 19.5h18.4L12 3.5Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 10v4.5M12 17v.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
    error: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="m9 9 6 6M15 9l-6 6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
    check: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="m8 12.5 3 3 5-6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
  };

  function icon(name, cls) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '16');
    svg.setAttribute('height', '16');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.setAttribute('class', 'icon' + (cls ? ' ' + cls : ''));
    svg.innerHTML = ICONS[name];
    return svg;
  }

  // ---- state ---------------------------------------------------------------------------

  const freshUM = () => ({ users: null, instances: null, selectedId: null, saved: new Set(), staged: new Map(), changes: null, error: null });

  const state = {
    session: null,
    route: 'instances',
    search: '',
    onlyRunning: false,
    sortDir: 1,
    instances: null,
    instError: null,
    expanded: new Set(),
    phases: new Map(),
    paintedSig: '',
    logins: null,
    loginsError: null,
    logs: null,
    logsError: null,
    logFilters: { source: 'all', severity: 'all', windowMinutes: 60 },
    lastUpdated: null,
    um: freshUM(),
  };

  let pollTimer = null;

  // ---- feedback: toasts, live region, dialog, focus -----------------------------------

  function announce(message) {
    const live = $('#live');
    live.textContent = '';
    setTimeout(() => { live.textContent = message; }, 50);
  }

  function toast(message, tone) {
    const el = h('div', { class: 'toast' + (tone ? ' ' + tone : ''), text: message });
    $('#toasts').append(el);
    setTimeout(() => el.remove(), 5000);
  }

  function withFocus(fn) {
    const active = document.activeElement;
    const key = active && active.dataset ? active.dataset.key : null;
    fn();
    if (key) {
      const el = document.querySelector('[data-key="' + CSS.escape(key) + '"]');
      if (el && el !== document.activeElement) el.focus({ preventScroll: true });
    }
  }

  function confirmDialog(opts) {
    return new Promise((resolve) => {
      const previous = document.activeElement;
      const dialog = h('dialog', { class: 'dialog', 'aria-labelledby': 'dialog-title' },
        h('form', { method: 'dialog', class: 'dialog-body' },
          h('h2', { id: 'dialog-title', text: opts.title }),
          opts.body,
          h('div', { class: 'dialog-actions' },
            h('button', { type: 'submit', value: 'cancel', class: 'btn btn-secondary', text: opts.cancelLabel || 'Cancel' }),
            h('button', { type: 'submit', value: 'ok', class: 'btn btn-live', text: opts.confirmLabel || 'Confirm' }))));
      dialog.addEventListener('close', () => {
        const ok = dialog.returnValue === 'ok';
        dialog.remove();
        if (previous && previous.focus) previous.focus();
        resolve(ok);
      });
      document.body.append(dialog);
      dialog.showModal();
    });
  }

  // ---- shell: user chip, navigation, status bar --------------------------------------

  function renderUserChip() {
    const s = state.session;
    const chip = $('#userchip');
    if (!s) return;
    const initials = s.name.split(' ').map((p) => p[0]).slice(0, 2).join('');
    chip.setAttribute('aria-label', 'Signed in as ' + s.name + ' at ' + fmt.time(s.signedInAt));
    chip.replaceChildren(
      h('span', { class: 'avatar', 'aria-hidden': 'true', text: initials }),
      h('span', { class: 'who' },
        h('span', { class: 'who-name', text: s.name }),
        h('span', { class: 'who-time', text: 'Signed in ' + fmt.time(s.signedInAt) })));
  }

  function renderNav() {
    const items = [
      ['instances', 'Instances', 'cube'],
      ['logins', 'Logins', 'key'],
      ['logs', 'Logs', 'list'],
    ];
    const nav = $('#nav');
    nav.replaceChildren();
    const link = ([id, label, ic]) => h('li', null,
      h('a', { class: 'nav-item', href: '#/' + id, 'aria-current': state.route === id ? 'page' : null }, icon(ic), h('span', { text: label })));
    items.forEach((item) => nav.append(link(item)));
    if (isManager()) {
      nav.append(h('li', { class: 'nav-divider', 'aria-hidden': 'true' }));
      nav.append(link(['user-management', 'User management', 'users']));
    }
  }

  function paintStatusBar() {
    $('#last-updated').textContent = state.lastUpdated ? 'Last updated ' + fmt.clock(state.lastUpdated) : 'Not loaded yet';
  }

  // ---- search (top bar and page search stay in step) ----------------------------------

  function setSearch(value) {
    state.search = value;
    const top = $('#global-search');
    if (top.value !== value) top.value = value;
    const page = $('#page-search');
    if (page && page.value !== value) page.value = value;
    if (state.route === 'instances') paintInstances();
    if (state.route === 'logins') paintLogins();
    if (state.route === 'logs') paintLogs();
  }

  function searchBox() {
    return h('label', { class: 'search' },
      icon('search'),
      h('span', { class: 'sr-only', text: 'Search this page' }),
      h('input', { id: 'page-search', type: 'search', placeholder: 'Search', value: state.search, autocomplete: 'off', onInput: (e) => setSearch(e.target.value) }));
  }

  // ---- views: Instances ----------------------------------------------------------------

  const ACTIVE = ['pending', 'initializing', 'ready', 'stopping'];
  const TRANSITIONAL = ['pending', 'initializing', 'stopping'];

  function phaseInfo(inst) {
    switch (inst.phase) {
      case 'stopped': return { label: 'Stopped', tone: 'grey', cube: 'grey' };
      case 'pending': return { label: 'Starting', tone: 'amber', cube: 'amber' };
      case 'initializing': return { label: 'Running, checking', tone: 'amber', cube: 'amber' };
      case 'ready': return { label: 'Ready', tone: 'green', cube: 'green' };
      case 'failed': return { label: 'Failed', tone: 'red', cube: 'red' };
      default: return { label: 'Stopping', tone: 'amber', cube: 'amber' };
    }
  }

  // The auto-stop line(s) for a running instance: see "Auto-stop display" in the PRD.
  function autoStopLines(inst) {
    const t = API.now();
    if (inst.phase === 'stopping') return []; // the status chip already says Stopping
    const a = inst.autoStop;
    if (!a) return [];
    if (!a.setupDone) return [{ text: 'Setting up. Auto-stop starts when ready.', tone: 'muted' }];
    if (!a.enabled) return [{ text: 'Auto-stop is off', tone: 'amber' }];
    const lines = [];
    const silent = a.heartbeatAgeMinutes >= 10;
    if (silent) lines.push({ text: 'Idle monitor not reporting', tone: 'amber' });
    if (a.maxUptimeMinutes > 0) {
      const stopAt = a.launchedAt + a.maxUptimeMinutes * MIN;
      const left = (stopAt - t) / MIN;
      if (left <= 0) return [{ text: 'Stopping', tone: 'amber' }];
      const warn = left <= (a.maxUptimeMinutes >= 60 ? 30 : 10);
      lines.push({
        text: (warn ? 'Stopping soon. ' : '') + 'Stops at about ' + fmt.time(stopAt) + '. ' + duration(left) + ' left.',
        tone: warn ? 'amber' : 'muted',
      });
    }
    if (a.idleLimitMinutes > 0 && !silent) {
      if (a.activeUsers === -1) {
        lines.push({ text: 'Activity unknown. Idle clock paused.', tone: 'amber' });
      } else {
        const idle = Math.min(a.idleMinutes, a.idleLimitMinutes);
        lines.push({
          text: 'Idle ' + idle + ' of ' + a.idleLimitMinutes + ' min. Earliest stop in ' + duration(a.idleLimitMinutes - idle) + '.',
          tone: 'muted',
          bar: { value: idle, max: a.idleLimitMinutes },
        });
      }
    }
    return lines;
  }

  function startButton(inst) {
    const can = inst.phase === 'stopped' || inst.phase === 'failed';
    const retry = inst.phase === 'failed';
    return h('button', {
      type: 'button',
      class: 'icon-btn start',
      'aria-label': (retry ? 'Retry start ' : 'Start ') + inst.name,
      'aria-disabled': can ? null : 'true',
      title: can ? (retry ? 'Retry start' : 'Start') : 'Only a stopped instance can be started',
      'data-key': 'start:' + inst.id,
      onClick: can ? () => onStart(inst) : (e) => e.preventDefault(),
    }, icon('play'));
  }

  function accessButton(inst) {
    if (inst.phase === 'ready') {
      return h('a', {
        class: 'btn btn-live', href: inst.url, target: '_blank', rel: 'noopener noreferrer',
        'aria-label': 'Access ' + inst.name + ' (opens in a new tab)', 'data-key': 'access:' + inst.id,
      }, 'Access', icon('external'));
    }
    const warming = inst.phase === 'pending' || inst.phase === 'initializing';
    return h('button', {
      type: 'button',
      class: 'btn btn-access' + (warming ? ' is-warming' : ''),
      'aria-disabled': 'true',
      title: warming ? 'Warming up: waiting for the status checks and the health check' : 'Service not ready',
      'data-key': 'access:' + inst.id,
      onClick: (e) => e.preventDefault(),
    }, warming ? (reduceMotion.matches ? null : icon('spinner', 'spin')) : null, warming ? 'Warming up' : 'Access');
  }

  function statusCell(inst) {
    const info = phaseInfo(inst);
    const cell = h('td', null,
      h('span', { class: 'chip tone-' + info.tone }, icon(info.tone === 'green' ? 'check' : info.tone === 'red' ? 'error' : info.tone === 'amber' ? 'dot' : 'dot'), info.label));
    if (inst.phase === 'failed' && inst.message) cell.append(h('div', { class: 'sub tone-red', text: inst.message }));
    if (inst.phase === 'initializing') {
      cell.append(h('div', { class: 'sub', text: 'Status checks ' + (inst.checks.ec2 ? 'passed' : 'pending') + '. Health check ' + (inst.checks.http ? 'passed' : 'pending') + '.' }));
    }
    if (inst.phase === 'pending') cell.append(h('div', { class: 'sub', text: 'Waiting for AWS to start the instance.' }));
    autoStopLines(inst).forEach((line) => {
      cell.append(h('div', { class: 'sub tone-' + line.tone, text: line.text }));
      if (line.bar) {
        cell.append(h('div', { class: 'bar', role: 'progressbar', 'aria-label': 'Idle minutes used', 'aria-valuemin': '0', 'aria-valuemax': String(line.bar.max), 'aria-valuenow': String(line.bar.value) },
          h('span', { style: 'width:' + Math.round((line.bar.value / line.bar.max) * 100) + '%' })));
      }
    });
    return cell;
  }

  function ruleText(inst) {
    const r = inst.rule;
    const parts = [];
    if (r.maxUptimeMinutes > 0) parts.push('Hard limit ' + r.maxUptimeMinutes + ' min after start');
    if (r.idleMinutes > 0) parts.push('Idle limit ' + r.idleMinutes + ' min');
    return r.enabled && parts.length ? parts.join('. ') : 'Off';
  }

  async function copyId(inst) {
    try {
      await navigator.clipboard.writeText(inst.id);
      toast('Copied instance ID of ' + inst.name, 'ok');
    } catch (err) {
      toast('Could not copy. Select the ID and copy it by hand.', 'error');
    }
  }

  function instanceRows(inst) {
    const info = phaseInfo(inst);
    const open = state.expanded.has(inst.id);
    const rows = [h('tr', null,
      h('td', { class: 'cell-icon' }, (() => { const c = icon('cube', 'cube tone-' + info.cube); c.setAttribute('width', '24'); c.setAttribute('height', '24'); return c; })()),
      h('td', null,
        h('button', {
          type: 'button', class: 'link-btn name', 'aria-expanded': String(open), 'data-key': 'name:' + inst.id,
          onClick: () => { if (open) state.expanded.delete(inst.id); else state.expanded.add(inst.id); paintInstances(); },
        }, inst.name),
        h('div', { class: 'idrow' },
          h('code', { text: inst.id }),
          h('button', { type: 'button', class: 'icon-btn small', 'aria-label': 'Copy instance ID of ' + inst.name, title: 'Copy instance ID', 'data-key': 'copy:' + inst.id, onClick: () => copyId(inst) }, icon('copy')))),
      statusCell(inst),
      h('td', { class: 'cell-actions' }, h('div', { class: 'actions' }, startButton(inst), accessButton(inst))))];
    if (open) {
      rows.push(h('tr', { class: 'details' }, h('td', { colspan: '4' }, h('dl', null,
        h('dt', { text: 'Instance ID' }), h('dd', { text: inst.id }),
        h('dt', { text: 'Type' }), h('dd', { text: inst.type }),
        h('dt', { text: 'Region' }), h('dd', { text: inst.region }),
        h('dt', { text: 'This run started' }), h('dd', { text: inst.launchedAt ? fmt.time(inst.launchedAt) : 'Not running' }),
        h('dt', { text: 'Auto-stop rule' }), h('dd', { text: ruleText(inst) })))));
    }
    return rows;
  }

  function filteredInstances() {
    const q = state.search.trim().toLowerCase();
    return (state.instances || [])
      .filter((i) => !state.onlyRunning || ACTIVE.includes(i.phase))
      .filter((i) => !q || i.name.toLowerCase().includes(q) || i.id.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name) * state.sortDir);
  }

  function paintSummary() {
    const box = $('#summary');
    if (!box) return;
    const list = state.instances || [];
    const running = list.filter((i) => ACTIVE.includes(i.phase)).length;
    const ready = list.filter((i) => i.phase === 'ready').length;
    box.replaceChildren(
      h('div', null, h('div', { class: 'stat-label', text: 'Instances running' }),
        h('div', { class: 'stat-value' }, String(running), h('span', { class: 'of', text: ' / ' + list.length }))),
      h('div', null, h('div', { class: 'stat-label', text: 'Ready to access' }),
        h('div', { class: 'stat-value' + (ready ? ' ok' : ''), text: String(ready) })));
  }

  function paintInstances() {
    const wrap = $('#table-wrap');
    if (!wrap) return;
    withFocus(() => {
      paintSummary();
      wrap.replaceChildren();
      const foot = $('#table-foot');
      foot.textContent = '';
      if (state.instError) {
        wrap.append(h('div', { class: 'error-box', role: 'alert' }, icon('error'), h('span', { text: state.instError }),
          h('button', { type: 'button', class: 'btn btn-secondary', onClick: () => loadInstances(), text: 'Try again' })));
        return;
      }
      if (state.instances === null) {
        wrap.append(h('p', { class: 'empty-small', text: 'Loading instances' }));
        return;
      }
      if (state.instances.length === 0) {
        wrap.append(h('div', { class: 'empty-state' },
          h('h2', { text: 'You have no instances available for edit.' }),
          h('p', { text: 'Contact your administrator if you expect to see instances here.' })));
        foot.textContent = 'Showing 0 items';
        return;
      }
      const list = filteredInstances();
      if (list.length === 0) {
        wrap.append(h('p', { class: 'empty-small', text: 'No instances match your search or filter.' }));
        foot.textContent = 'Showing 0 items';
        return;
      }
      wrap.append(h('table', { class: 'grid' },
        h('caption', { class: 'sr-only', text: 'Instances you can start' }),
        h('thead', null, h('tr', null,
          h('th', { scope: 'col', class: 'cell-icon' }, h('span', { class: 'sr-only', text: 'State icon' })),
          h('th', { scope: 'col', 'aria-sort': state.sortDir === 1 ? 'ascending' : 'descending' },
            h('button', { type: 'button', class: 'sort' + (state.sortDir === -1 ? ' desc' : ''), 'data-key': 'sort-name', onClick: () => { state.sortDir *= -1; paintInstances(); } }, 'Name', icon('arrow'))),
          h('th', { scope: 'col', text: 'Status' }),
          h('th', { scope: 'col', class: 'cell-actions', text: 'Actions' }))),
        h('tbody', null, list.map(instanceRows))));
      foot.textContent = 'Showing ' + list.length + (list.length === 1 ? ' item' : ' items');
      state.paintedSig = signature();
    });
  }

  function signature() {
    return JSON.stringify((state.instances || []).map((i) => autoStopLines(i)));
  }

  function viewInstances() {
    const main = $('#main');
    main.replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Instances' }),
      h('div', { id: 'summary', class: 'summary' }),
      h('div', { class: 'toolbar tight' },
        searchBox(),
        h('button', {
          id: 'only-running', type: 'button', role: 'switch', class: 'switch', 'aria-checked': String(state.onlyRunning),
          onClick: (e) => {
            state.onlyRunning = !state.onlyRunning;
            e.currentTarget.setAttribute('aria-checked', String(state.onlyRunning));
            paintInstances();
          },
        }, h('span', { class: 'switch-track' }, h('span', { class: 'switch-thumb' })), h('span', { text: 'Only show running instances' }))),
      h('div', { id: 'table-wrap', class: 'table-wrap' }),
      h('div', { id: 'table-foot', class: 'table-foot' }));
    paintInstances();
    loadInstances();
  }

  function detectTransitions(list) {
    list.forEach((inst) => {
      const before = state.phases.get(inst.id);
      if (before && before !== inst.phase) {
        if (inst.phase === 'ready') announce(inst.name + ' is ready. You can now access it.');
        else if (inst.phase === 'failed') announce(inst.name + ' failed to start.');
        else if (inst.phase === 'stopped') announce(inst.name + ' has stopped.');
        else if (inst.phase === 'stopping') announce(inst.name + ' is stopping.');
      }
      state.phases.set(inst.id, inst.phase);
    });
  }

  async function loadInstances() {
    const session = state.session;
    try {
      const list = await API.listInstances(session);
      if (session !== state.session) return;
      detectTransitions(list);
      state.instances = list;
      state.instError = null;
      state.lastUpdated = API.now();
    } catch (err) {
      if (session !== state.session) return;
      state.instError = 'Could not load your instances. ' + err.message;
    }
    paintInstances();
    paintStatusBar();
    schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (state.route !== 'instances') return;
    const fast = state.instances && state.instances.some((i) => TRANSITIONAL.includes(i.phase));
    pollTimer = setTimeout(loadInstances, fast ? 1500 : 30000);
  }

  async function onStart(inst) {
    try {
      announce('Starting ' + inst.name);
      await API.startInstance(state.session, inst.id);
      await loadInstances();
    } catch (err) {
      toast(err.message, 'error');
      announce(err.message);
    }
  }

  // The countdown ticks on the page between reads; repaint only when a line changes.
  setInterval(() => {
    if (state.route === 'instances' && state.instances && signature() !== state.paintedSig) paintInstances();
  }, 15000);

  // ---- views: Logins -------------------------------------------------------------------

  function paintLogins() {
    const wrap = $('#table-wrap');
    if (!wrap) return;
    wrap.replaceChildren();
    $('#table-foot').textContent = '';
    if (state.loginsError) {
      wrap.append(h('div', { class: 'error-box', role: 'alert' }, icon('error'), h('span', { text: state.loginsError }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: loadLogins, text: 'Try again' })));
      return;
    }
    if (state.logins === null) {
      wrap.append(h('p', { class: 'empty-small', text: 'Loading your logins' }));
      return;
    }
    const q = state.search.trim().toLowerCase();
    const list = state.logins.filter((l) => !q || (l.ip + ' ' + l.browser + ' ' + l.result + ' ' + l.detail).toLowerCase().includes(q));
    if (!list.length) {
      wrap.append(h('p', { class: 'empty-small', text: 'No logins match your search.' }));
      return;
    }
    wrap.append(h('table', { class: 'grid' },
      h('caption', { class: 'sr-only', text: 'Your recent logins, newest first' }),
      h('thead', null, h('tr', null,
        ['Time', 'Result', 'Source IP', 'Browser or device', 'Session'].map((c) => h('th', { scope: 'col', text: c })))),
      h('tbody', null, list.map((l) => h('tr', null,
        h('td', { text: fmt.dateTime(l.t) }),
        h('td', null, l.result === 'success'
          ? h('span', { class: 'chip tone-green' }, icon('check'), 'Success')
          : h('span', { class: 'chip tone-red' }, icon('error'), 'Failed'), l.detail ? h('div', { class: 'sub', text: l.detail }) : null),
        h('td', null, h('code', { text: l.ip })),
        h('td', { text: l.browser }),
        h('td', null, l.current ? h('span', { class: 'chip tone-info' }, icon('info'), 'Current session') : null))))));
    $('#table-foot').textContent = 'Showing ' + list.length + (list.length === 1 ? ' login' : ' logins');
  }

  async function loadLogins() {
    const session = state.session;
    try {
      const list = await API.listLogins(session);
      if (session !== state.session) return;
      state.logins = list;
      state.loginsError = null;
      state.lastUpdated = API.now();
    } catch (err) {
      if (session !== state.session) return;
      state.loginsError = 'Could not load your logins. ' + err.message;
    }
    paintLogins();
    paintStatusBar();
  }

  function viewLogins() {
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Logins' }),
      h('p', { class: 'page-lead', text: 'Your own recent sign-ins to the control panel, newest first.' }),
      h('div', { class: 'toolbar' }, searchBox(), h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: loadLogins }, icon('refresh'), 'Refresh')),
      h('div', { id: 'table-wrap', class: 'table-wrap' }),
      h('div', { id: 'table-foot', class: 'table-foot' }));
    paintLogins();
    loadLogins();
  }

  // ---- views: Logs ---------------------------------------------------------------------

  const SEVERITY = {
    info: { label: 'Info', tone: 'info', ic: 'info' },
    warning: { label: 'Warning', tone: 'amber', ic: 'warn' },
    error: { label: 'Error', tone: 'red', ic: 'error' },
  };

  function paintLogs() {
    const wrap = $('#table-wrap');
    if (!wrap) return;
    wrap.replaceChildren();
    $('#table-foot').textContent = '';
    if (state.logsError) {
      wrap.append(h('div', { class: 'error-box', role: 'alert' }, icon('error'), h('span', { text: state.logsError }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: loadLogs, text: 'Try again' })));
      return;
    }
    if (state.logs === null) {
      wrap.append(h('p', { class: 'empty-small', text: 'Loading logs' }));
      return;
    }
    const q = state.search.trim().toLowerCase();
    const list = state.logs.filter((l) => !q || l.event.toLowerCase().includes(q));
    if (!list.length) {
      wrap.append(h('p', { class: 'empty-small', text: 'No log events match these filters.' }));
      return;
    }
    wrap.append(h('table', { class: 'grid' },
      h('caption', { class: 'sr-only', text: 'Log events, newest first' }),
      h('thead', null, h('tr', null, ['Time', 'Source', 'Severity', 'Event'].map((c) => h('th', { scope: 'col', text: c })))),
      h('tbody', null, list.map((l) => {
        const sev = SEVERITY[l.severity];
        return h('tr', null,
          h('td', { text: fmt.dateTime(l.t) }),
          h('td', { text: l.source }),
          h('td', null, h('span', { class: 'chip tone-' + sev.tone }, icon(sev.ic), sev.label)),
          h('td', null, h('code', { text: l.event })));
      }))));
    $('#table-foot').textContent = 'Showing ' + list.length + (list.length === 1 ? ' event' : ' events');
  }

  async function loadLogs() {
    const session = state.session;
    try {
      const list = await API.listLogs(session, state.logFilters);
      if (session !== state.session) return;
      state.logs = list;
      state.logsError = null;
      state.lastUpdated = API.now();
    } catch (err) {
      if (session !== state.session) return;
      state.logsError = 'Could not load logs. ' + err.message;
    }
    paintLogs();
    paintStatusBar();
  }

  function select(label, key, options) {
    return h('label', { class: 'field' }, h('span', { text: label }),
      h('select', {
        onChange: (e) => {
          state.logFilters[key] = key === 'windowMinutes' ? Number(e.target.value) : e.target.value;
          state.logs = null;
          paintLogs();
          loadLogs();
        },
      }, options.map(([value, text]) => h('option', { value, text, selected: String(state.logFilters[key]) === String(value) }))));
  }

  function viewLogs() {
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Logs' }),
      h('p', { class: 'page-lead', text: 'Events for the instances you can use, and your own connections to the control panel.' }),
      h('div', { class: 'toolbar' },
        select('Source', 'source', [['all', 'All sources'], ['Control panel', 'Control panel'], ['EC2', 'EC2']]),
        select('Severity', 'severity', [['all', 'All'], ['info', 'Info'], ['warning', 'Warning'], ['error', 'Error']]),
        select('Time window', 'windowMinutes', [[60, 'Last hour'], [1440, 'Last 24 hours'], [10080, 'Last 7 days']]),
        searchBox(), h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: loadLogs }, icon('refresh'), 'Refresh')),
      h('div', { id: 'table-wrap', class: 'table-wrap' }),
      h('div', { id: 'table-foot', class: 'table-foot' }));
    paintLogs();
    loadLogs();
  }

  // ---- views: User management (user_mgrs only) -----------------------------------------

  function combobox(opts) {
    const wrap = h('div', { class: 'combo' });
    const input = h('input', {
      id: opts.id, type: 'text', class: 'combo-input', role: 'combobox', autocomplete: 'off', placeholder: 'Search users',
      'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': opts.id + '-list',
    });
    const list = h('ul', { id: opts.id + '-list', class: 'combo-list', role: 'listbox', 'aria-label': opts.label, hidden: true });
    let selectedId = opts.selectedId;
    let shown = [];
    let active = -1;
    const labelOf = () => { const o = opts.options.find((x) => x.id === selectedId); return o ? o.label : ''; };

    function paintActive() {
      Array.from(list.querySelectorAll('[role="option"]')).forEach((li, i) => {
        li.classList.toggle('active', i === active);
        if (i === active) {
          input.setAttribute('aria-activedescendant', li.id);
          li.scrollIntoView({ block: 'nearest' });
        }
      });
      if (active < 0) input.removeAttribute('aria-activedescendant');
    }
    function open(filter) {
      shown = opts.options.filter((o) => !filter || o.label.toLowerCase().includes(filter.toLowerCase()));
      list.replaceChildren(...shown.map((o, i) => h('li', {
        id: opts.id + '-opt-' + i, role: 'option', class: 'combo-opt', 'aria-selected': String(o.id === selectedId), text: o.label,
        onMousedown: (e) => { e.preventDefault(); choose(o); },
      })));
      if (!shown.length) list.append(h('li', { class: 'combo-empty', role: 'presentation', text: 'No users match' }));
      active = shown.length ? Math.max(0, shown.findIndex((o) => o.id === selectedId)) : -1;
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      paintActive();
    }
    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      input.value = labelOf();
    }
    function choose(o) {
      selectedId = o.id;
      close();
      opts.onSelect(o.id);
    }

    input.value = labelOf();
    input.addEventListener('focus', () => input.select());
    input.addEventListener('click', () => { if (list.hidden) open(''); });
    input.addEventListener('input', () => open(input.value));
    input.addEventListener('blur', close);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (list.hidden) open(''); else { active = Math.min(shown.length - 1, active + 1); paintActive(); }
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (list.hidden) open(''); else { active = Math.max(0, active - 1); paintActive(); }
      } else if (e.key === 'Enter') {
        if (!list.hidden && active >= 0 && shown[active]) { e.preventDefault(); choose(shown[active]); }
      } else if (e.key === 'Escape') {
        if (!list.hidden) { e.preventDefault(); close(); }
      }
    });
    wrap.append(h('label', { class: 'field-label', for: opts.id, text: opts.label }), input, list);
    return wrap;
  }

  const um = () => state.um;
  const umUser = () => (um().users || []).find((u) => u.id === um().selectedId);

  function umDirty() {
    return um().staged.size > 0;
  }

  async function confirmDiscard() {
    if (!umDirty()) return true;
    return confirmDialog({
      title: 'Discard unsaved changes?',
      body: h('p', { text: 'You have ' + um().staged.size + ' unsaved change' + (um().staged.size === 1 ? '' : 's') + '. They will be lost.' }),
      confirmLabel: 'Discard',
      cancelLabel: 'Keep editing',
    });
  }

  async function selectUser(id) {
    if (id === um().selectedId) return;
    if (!(await confirmDiscard())) { paintUM(); return; }
    um().selectedId = id;
    um().staged = new Map();
    um().saved = new Set();
    paintUM();
    try {
      const granted = await API.getGrants(state.session, id);
      if (um().selectedId !== id) return;
      um().saved = new Set(granted);
      um().error = null;
    } catch (err) {
      um().error = err.message;
    }
    paintUM();
  }

  function toggleGrant(instanceId, checked) {
    const wasSaved = um().saved.has(instanceId);
    if (checked === wasSaved) um().staged.delete(instanceId);
    else um().staged.set(instanceId, checked);
    paintUM();
  }

  function pendingChanges() {
    return Array.from(um().staged.entries()).map(([instanceId, grant]) => ({ instanceId, grant }));
  }

  async function saveChanges() {
    const user = umUser();
    const changes = pendingChanges();
    const nameOf = (id) => um().instances.find((i) => i.id === id).name;
    const ok = await confirmDialog({
      title: 'Save access changes for ' + user.name + '?',
      body: h('div', null,
        h('ul', null, changes.map((c) => h('li', { text: (c.grant ? 'Grant: ' : 'Revoke: ') + nameOf(c.instanceId) }))),
        h('p', { class: 'muted', text: 'This changes who may start each instance. It does not start or stop any instance: a running instance keeps running.' })),
      confirmLabel: 'Save changes',
    });
    if (!ok) return;
    try {
      await API.saveGrants(state.session, user.id, changes);
      const granted = await API.getGrants(state.session, user.id);
      um().saved = new Set(granted);
      um().staged = new Map();
      um().changes = await API.listChanges(state.session);
      toast('Saved ' + changes.length + ' change' + (changes.length === 1 ? '' : 's') + ' for ' + user.name, 'ok');
      announce('Saved ' + changes.length + ' access change' + (changes.length === 1 ? '' : 's') + ' for ' + user.name);
    } catch (err) {
      toast(err.message, 'error');
      announce(err.message);
    }
    paintUM();
  }

  function accessChip(instanceId) {
    if (um().staged.has(instanceId)) {
      return h('span', { class: 'chip tone-amber' }, icon('dot'), um().staged.get(instanceId) ? 'Pending: grant' : 'Pending: revoke');
    }
    return um().saved.has(instanceId)
      ? h('span', { class: 'chip tone-green' }, icon('check'), 'Granted')
      : h('span', { class: 'chip tone-grey' }, icon('dot'), 'Not granted');
  }

  function paintUM(keepPicker) {
    const body = $('#um-body');
    if (!body) return;
    withFocus(() => {
      if (!keepPicker) {
        $('#um-picker').replaceChildren(combobox({
          id: 'um-user',
          label: 'User',
          options: (um().users || []).map((u) => ({ id: u.id, label: u.name + ', ' + roleLabel(u.groups) })),
          selectedId: um().selectedId,
          onSelect: selectUser,
        }));
      }
      body.replaceChildren();
      if (um().error) {
        body.append(h('div', { class: 'error-box', role: 'alert' }, icon('error'), h('span', { text: um().error })));
        return;
      }
      const user = umUser();
      if (!user) {
        body.append(h('p', { class: 'empty-small', text: 'Choose a user to see and change which instances they may start.' }));
        return;
      }
      const operator = user.groups.includes('operators');
      if (!operator) {
        body.append(h('div', { class: 'notice' }, icon('info'),
          h('span', { text: user.name + ' is not in the operators group, so cannot be granted instances. Add them to the operators group in Cognito first.' })));
      }
      body.append(h('table', { class: 'grid' },
        h('caption', { class: 'sr-only', text: 'Instances and whether ' + user.name + ' may start them' }),
        h('thead', null, h('tr', null,
          h('th', { scope: 'col', class: 'check-cell' }, h('span', { class: 'sr-only', text: 'Allowed to start' })),
          h('th', { scope: 'col', text: 'Instance' }),
          h('th', { scope: 'col', text: 'Access' }))),
        h('tbody', null, um().instances.map((inst) => {
          const checked = um().staged.has(inst.id) ? um().staged.get(inst.id) : um().saved.has(inst.id);
          return h('tr', null,
            h('td', { class: 'check-cell' }, h('input', {
              type: 'checkbox', id: 'grant-' + inst.id, checked, disabled: !operator && !checked, 'data-key': 'grant:' + inst.id,
              'aria-labelledby': 'grant-name-' + inst.id,
              onChange: (e) => toggleGrant(inst.id, e.target.checked),
            })),
            h('td', null,
              h('label', { id: 'grant-name-' + inst.id, for: 'grant-' + inst.id, class: 'name', text: inst.name }),
              h('div', { class: 'idrow' }, h('code', { text: inst.id }), h('span', { text: ' ' + inst.type }))),
            h('td', null, accessChip(inst.id)));
        }))));
      const n = um().staged.size;
      body.append(h('div', { class: 'savebar', role: 'region', 'aria-label': 'Unsaved changes', hidden: n === 0 },
        h('span', null, h('strong', { text: String(n) }), ' unsaved change' + (n === 1 ? '' : 's')),
        h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: () => { um().staged = new Map(); paintUM(); }, text: 'Discard' }),
        h('button', { type: 'button', class: 'btn btn-live', onClick: saveChanges, text: 'Save changes' })));
    });
    paintChanges();
  }

  function paintChanges() {
    const box = $('#um-changes');
    if (!box) return;
    const rows = um().changes || [];
    const nameOfUser = (id) => { const u = (um().users || []).find((x) => x.id === id); return u ? u.name : id; };
    const nameOfInst = (id) => { const i = (um().instances || []).find((x) => x.id === id); return i ? i.name : id; };
    box.replaceChildren(
      h('h2', { class: 'section-title', text: 'Recent changes' }),
      rows.length
        ? h('table', { class: 'grid' },
          h('caption', { class: 'sr-only', text: 'Recent access changes, newest first' }),
          h('thead', null, h('tr', null, ['Time', 'Changed by', 'User', 'Instance', 'Change', 'Result'].map((c) => h('th', { scope: 'col', text: c })))),
          h('tbody', null, rows.map((r) => h('tr', null,
            h('td', { text: fmt.dateTime(r.t) }),
            h('td', { text: nameOfUser(r.adminId) }),
            h('td', { text: nameOfUser(r.userId) }),
            h('td', { text: nameOfInst(r.instanceId) }),
            h('td', null, h('span', { class: 'chip ' + (r.action === 'Granted' ? 'tone-green' : 'tone-grey') }, r.action)),
            h('td', null, h('span', { class: 'chip tone-green' }, icon('check'), r.result))))))
        : h('p', { class: 'empty-small', text: 'No changes yet.' }));
  }

  async function viewUserManagement() {
    const main = $('#main');
    if (!isManager()) {
      main.replaceChildren(
        h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'User management' }),
        h('div', { class: 'empty-state' },
          h('h2', { text: 'You do not have access to this page.' }),
          h('p', { text: 'User management is for members of the user_mgrs group.' })));
      return;
    }
    main.replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'User management' }),
      h('p', { class: 'page-lead', text: 'Choose which instances a user may start. Nothing changes until you save.' }),
      h('div', { id: 'um-picker', class: 'um-picker' }),
      h('div', { id: 'um-body' }),
      h('div', { id: 'um-changes' }));
    const session = state.session;
    try {
      const [users, instances, changes] = await Promise.all([API.listUsers(session), API.listAllInstances(session), API.listChanges(session)]);
      if (session !== state.session) return;
      um().users = users;
      um().instances = instances;
      um().changes = changes;
      um().error = null;
      state.lastUpdated = API.now();
      paintStatusBar();
    } catch (err) {
      um().error = 'Could not load users. ' + err.message;
    }
    paintUM();
  }

  // ---- routing and sign-in --------------------------------------------------------------

  const TITLES = { instances: 'Instances', logins: 'Logins', logs: 'Logs', 'user-management': 'User management' };

  function route(moveFocus) {
    const wanted = location.hash.replace(/^#\/?/, '');
    state.route = TITLES[wanted] ? wanted : 'instances';
    clearTimeout(pollTimer);
    document.title = 'AI Cloud Lab · ' + TITLES[state.route];
    renderNav();
    if (state.route === 'instances') viewInstances();
    else if (state.route === 'logins') viewLogins();
    else if (state.route === 'logs') viewLogs();
    else viewUserManagement();
    if (moveFocus) { const title = $('#page-title'); if (title) title.focus(); }
  }

  async function signInAs(userId) {
    clearTimeout(pollTimer);
    state.session = await API.signIn(userId);
    try { localStorage.setItem('dashboard.demoUser', userId); } catch (err) { /* private mode: fine */ }
    state.instances = null; state.instError = null;
    state.logins = null; state.logs = null;
    state.expanded = new Set();
    state.phases = new Map();
    state.um = freshUM();
    renderUserChip();
    $('#demo-user').value = userId;
    announce('Signed in as ' + state.session.name);
    route(false);
  }

  async function initDemoControls() {
    const people = API.listDemoUsers();
    const select = $('#demo-user');
    select.replaceChildren(...people.map((u) => h('option', { value: u.id, text: u.name + ', ' + roleLabel(u.groups) })));
    select.addEventListener('change', () => signInAs(select.value));
    $('#demo-scenario').addEventListener('change', (e) => {
      API.setScenario(e.target.value);
      if (state.route === 'instances') loadInstances();
    });
    $('#demo-advance').addEventListener('click', () => {
      API.advance(10);
      toast('Mock clock moved forward 10 minutes.');
      if (state.route === 'instances') loadInstances();
      if (state.route === 'logs') loadLogs();
    });
    $('#demo-reset').addEventListener('click', () => {
      API.reset();
      $('#demo-scenario').value = 'normal';
      signInAs(state.session.userId);
      toast('Demo reset.');
    });
  }

  async function boot() {
    $('#global-search').addEventListener('input', (e) => setSearch(e.target.value));
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#global-search').focus(); }
    });
    window.addEventListener('hashchange', () => route(true));
    await initDemoControls();
    let first = 'u1';
    try { first = localStorage.getItem('dashboard.demoUser') || first; } catch (err) { /* ignore */ }
    await signInAs(first);
    renderNav();
  }

  boot();
})();
