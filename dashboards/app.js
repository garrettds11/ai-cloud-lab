/*
 * AI Cloud Lab control panel: one page, four views (Instances, Logins, Logs, User management).
 * Plain JavaScript, no build step. All data comes from window.Api (api.js), which signs the
 * user in with Cognito and calls the Control API. This file holds no permissions of its own:
 * it shows what the API returns, and the API checks every rule again.
 */
(function () {
  'use strict';

  const API = window.Api;
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

  function roleLabel(roles) {
    const parts = [];
    if (roles.includes('admin')) parts.push('Administrator');
    if (roles.includes('operators')) parts.push('Operator');
    if (roles.includes('user_mgrs')) parts.push('User manager');
    return parts.length ? parts.join(', ') : 'No role';
  }

  const isAdmin = () => !!state.session && state.session.roles.includes('admin');
  const isManager = () => !!state.session && (state.session.roles.includes('user_mgrs') || isAdmin());

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
    timer: '<path d="M4.5 12a7.5 7.5 0 1 0 2.4-5.5M4.5 4.2v3.6h3.6" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 8v4.2l2.8 1.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    arrow: '<path d="M12 5v14m0 0-5-5m5 5 5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    spinner: '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2.4" opacity=".25"/><path d="M12 4a8 8 0 0 1 8 8" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>',
    dot: '<circle cx="12" cy="12" r="6" fill="currentColor"/>',
    info: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 11v6M12 7.5v.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
    cloud: '<path d="M7 18a4 4 0 0 1-.6-7.95A5.5 5.5 0 0 1 17 8.6 4.7 4.7 0 0 1 17.5 18H7Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    chat: '<path d="M4 5h16v11H9l-5 4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
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

  const freshUM = () => ({ users: null, instances: null, selectedId: null, saved: new Set(), staged: new Map(), stagedRoles: new Map(), changes: null, error: null });

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
  // Instances just started from this page. EC2 can still report "stopped" for a moment after
  // a start is accepted, so for 30 seconds a stopped answer is shown as starting.
  const pendingStarts = new Map();
  // Instances whose timer reset is being sent, so a double click sends one request.
  const resetting = new Set();

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
        h('span', { class: 'who-time', text: 'Signed in ' + fmt.time(s.signedInAt) })),
      h('button', { type: 'button', class: 'signout', onClick: () => API.signOut(), text: 'Sign out' }));
  }

  function renderNav() {
    const items = [
      ['instances', 'Instances', 'cube'],
      ['logins', 'Logins', 'key'],
      ['logs', 'Logs', 'list'],
      ['spend', 'Spend caps', 'timer'],
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
    if (isAdmin()) {
      if (!isManager()) nav.append(h('li', { class: 'nav-divider', 'aria-hidden': 'true' }));
      nav.append(link(['open-webui', 'Open WebUI', 'info']));
      nav.append(link(['chat-test', 'Chat API test', 'chat']));
      const grafana = typeof API.grafanaUrl === 'function' ? API.grafanaUrl() : null;
      if (grafana) {
        nav.append(h('li', null,
          h('a', { class: 'nav-item', href: grafana, target: '_blank', rel: 'noopener noreferrer' }, icon('external'), h('span', { text: 'Grafana' }),
            h('span', { class: 'sr-only', text: ' (opens in a new tab)' }))));
      }
      nav.append(link(['usage', 'Usage and cost', 'list']));
      nav.append(link(['timer-policy', 'Timer policy', 'timer']));
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
    // The absolute limit counts from launch and no reset moves it.
    const absStop = a.absoluteMaxMinutes > 0 ? a.launchedAt + a.absoluteMaxMinutes * MIN : null;
    if (a.maxUptimeMinutes > 0) {
      const resetAt = a.resetAt && a.resetAt > a.launchedAt ? a.resetAt : null;
      const softStop = (resetAt || a.launchedAt) + a.maxUptimeMinutes * MIN;
      const stopAt = absStop ? Math.min(softStop, absStop) : softStop;
      const left = (stopAt - t) / MIN;
      if (left <= 0) return [{ text: 'Stopping', tone: 'amber' }];
      const warn = left <= (a.maxUptimeMinutes >= 60 ? 30 : 10);
      lines.push({
        text: (warn ? 'Stopping soon. ' : '') + 'Stops at about ' + fmt.time(stopAt) + '. ' + duration(left) + ' left.',
        tone: warn ? 'amber' : 'muted',
      });
      if (absStop && absStop <= softStop) lines.push({ text: 'This is the lab\'s absolute limit. A reset cannot extend it.', tone: 'muted' });
      if (resetAt) lines.push({ text: 'Timer reset at ' + fmt.time(resetAt) + '.' + (absStop && absStop <= softStop ? '' : ' The lab got another ' + duration(a.maxUptimeMinutes) + '.'), tone: 'green' });
      if (a.resetsAllowed > 0) lines.push({ text: 'Resets used: ' + (a.resetsUsed === null || a.resetsUsed === undefined ? 'unknown' : a.resetsUsed) + ' of ' + a.resetsAllowed + ' this run.', tone: a.resetsUsed >= a.resetsAllowed ? 'amber' : 'muted' });
    } else if (absStop) {
      const left = (absStop - t) / MIN;
      if (left <= 0) return [{ text: 'Stopping', tone: 'amber' }];
      const warn = left <= (a.absoluteMaxMinutes >= 60 ? 30 : 10);
      lines.push({ text: (warn ? 'Stopping soon. ' : '') + 'Absolute limit: stops at about ' + fmt.time(absStop) + '. ' + duration(left) + ' left.', tone: warn ? 'amber' : 'muted' });
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
    const budget = can && !!inst.budgetReached;
    const ok = can && !budget;
    return h('button', {
      type: 'button',
      class: 'icon-btn start' + (budget ? ' has-note' : ''),
      'aria-label': (retry ? 'Retry start ' : 'Start ') + inst.name + (budget ? ' (monthly budget reached)' : ''),
      'aria-disabled': ok ? null : 'true',
      title: budget ? 'Monthly budget reached. An administrator can raise the cap, or it resets next month.' : can ? (retry ? 'Retry start' : 'Start') : 'Only a stopped instance can be started',
      'data-key': 'start:' + inst.id,
      onClick: ok ? () => onStart(inst) : (e) => e.preventDefault(),
    }, icon('play'), budget ? h('span', { class: 'budget-note', text: 'Monthly budget reached' }) : null);
  }

  // Restarts the hard-stop countdown without stopping or restarting the lab.
  function resetButton(inst) {
    const a = inst.autoStop;
    const running = inst.phase === 'ready' || inst.phase === 'initializing';
    const limited = !!(a && a.enabled && a.maxUptimeMinutes > 0);
    const busy = resetting.has(inst.id);
    const spent = !!(limited && a.resetsAllowed > 0 && a.resetsUsed >= a.resetsAllowed);
    const can = running && limited && !busy && !spent;
    const title = !running ? 'Only a running instance has an auto-stop timer to reset'
      : !limited ? 'No hard time limit is set, so there is nothing to reset'
      : spent ? 'All ' + a.resetsAllowed + ' timer resets of this run are used'
      : busy ? 'Resetting the timer' : 'Reset auto-stop timer' + (limited && a.resetsAllowed > 0 ? ' (' + Math.max(0, a.resetsAllowed - (a.resetsUsed || 0)) + ' left)' : '');
    return h('button', {
      type: 'button',
      class: 'icon-btn reset',
      'aria-label': 'Reset auto-stop timer for ' + inst.name,
      'aria-disabled': can ? null : 'true',
      title,
      'data-key': 'reset:' + inst.id,
      onClick: can ? () => onReset(inst) : (e) => e.preventDefault(),
    }, icon('timer'));
  }

  // Administrators stop an instance in the AWS console; the panel has no stop button on purpose (#62).
  function awsConsoleButton(inst) {
    const region = /^[a-z]{2}(-[a-z]+)+-\d$/.test(inst.region || '') ? inst.region : API.region();
    const href = 'https://console.aws.amazon.com/ec2/home?region=' + encodeURIComponent(region) + '#InstanceDetails:instanceId=' + encodeURIComponent(inst.id);
    const custom = API.awsIconUrl();
    const mark = custom ? h('img', { class: 'aws-icon', src: custom, alt: '', width: '16', height: '16' }) : icon('cloud');
    return h('a', {
      class: 'btn btn-secondary aws-console', href, target: '_blank', rel: 'noopener noreferrer',
      'aria-label': 'Open ' + inst.name + ' in the AWS console, where an instance can be stopped (opens in a new tab)',
      title: 'Open in the AWS console. This is where an instance is stopped.', 'data-key': 'console:' + inst.id,
    }, mark, 'AWS console');
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
    if (r.maxUptimeMinutes > 0) parts.push('Hard limit ' + r.maxUptimeMinutes + ' min after start or the last timer reset');
    if (r.absoluteMaxMinutes > 0) parts.push('Absolute limit ' + r.absoluteMaxMinutes + ' min after start');
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
      h('td', { class: 'cell-actions' }, h('div', { class: 'actions' }, startButton(inst), resetButton(inst), isAdmin() ? awsConsoleButton(inst) : null, accessButton(inst))))];
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
      const list = await API.listInstances();
      if (session !== state.session) return;
      list.forEach((i) => {
        const at = pendingStarts.get(i.id);
        if (at === undefined) return;
        if (i.phase === 'stopped' && Date.now() - at < 30000) i.phase = 'pending'; else pendingStarts.delete(i.id);
      });
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
      await API.startInstance(inst.id);
      pendingStarts.set(inst.id, Date.now());
      await loadInstances();
    } catch (err) {
      if (err.status === 409) { loadInstances(); return; } // already starting: the page was out of date
      toast(err.message, 'error');
      announce(err.message);
    }
  }

  async function onReset(inst) {
    const a = inst.autoStop;
    const max = a.maxUptimeMinutes;
    const currentStop = (a.resetAt && a.resetAt > a.launchedAt ? a.resetAt : a.launchedAt) + max * MIN;
    const absStop = a.absoluteMaxMinutes > 0 ? a.launchedAt + a.absoluteMaxMinutes * MIN : null;
    const newStop = absStop ? Math.min(API.now() + max * MIN, absStop) : API.now() + max * MIN;
    const ok = await confirmDialog({
      title: 'Reset the auto-stop timer?',
      body: h('div', null,
        h('p', { text: 'Extend this lab for another full session?' }),
        h('p', { class: 'muted', text: inst.name + ' will stop in ' + duration((newStop - API.now()) / MIN) + ' (at about ' + fmt.time(newStop) + (absStop && newStop === absStop ? ', the lab\'s absolute limit' : '') + '), instead of at about ' + fmt.time(currentStop) + '. Nothing is restarted, and the idle timer is not affected.' })),
      confirmLabel: 'Reset timer',
    });
    if (!ok || resetting.has(inst.id)) return;
    resetting.add(inst.id);
    paintInstances();
    try {
      announce('Resetting the auto-stop timer for ' + inst.name);
      const result = await API.resetTimer(inst.id);
      toast(result.message, 'ok');
      announce(result.message);
      if (inst.autoStop) inst.autoStop.resetAt = result.resetAt; // show it now; the next read confirms it
    } catch (err) {
      toast(err.message, 'error');
      announce(err.message);
    } finally {
      resetting.delete(inst.id);
    }
    await loadInstances();
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
    const list = state.logins.filter((l) => !q || ((l.userName || '') + ' ' + (l.userId || '') + ' ' + l.ip + ' ' + l.browser + ' ' + l.result + ' ' + l.detail).toLowerCase().includes(q));
    if (!list.length) {
      wrap.append(h('p', { class: 'empty-small', text: 'No logins match your search.' }));
      return;
    }
    wrap.append(h('table', { class: 'grid' },
      h('caption', { class: 'sr-only', text: 'Your recent logins, newest first' }),
      h('thead', null, h('tr', null,
        (everyone() ? ['Time', 'User'] : ['Time']).concat(['Result', 'Source IP', 'Browser or device', 'Session']).map((c) => h('th', { scope: 'col', text: c })))),
      h('tbody', null, list.map((l) => h('tr', null,
        h('td', { text: fmt.dateTime(l.t) }),
        everyone() ? h('td', null, l.userName, h('div', { class: 'sub', text: l.userId })) : null,
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
      const list = everyone() ? await API.listAllLogins() : await API.listLogins();
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
      h('p', { class: 'page-lead', text: everyone() ? 'Recent sign-ins to the control panel by everyone, newest first.' : 'Your own recent sign-ins to the control panel, newest first.' }),
      h('div', { class: 'toolbar' }, whoseSelect(), searchBox(), h('span', { class: 'spacer' }),
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
    const list = state.logs.filter((l) => !q || (l.event + ' ' + (l.userName || '') + ' ' + (l.userId || '')).toLowerCase().includes(q));
    if (!list.length) {
      wrap.append(h('p', { class: 'empty-small', text: 'No log events match these filters.' }));
      return;
    }
    wrap.append(h('table', { class: 'grid' },
      h('caption', { class: 'sr-only', text: 'Log events, newest first' }),
      h('thead', null, h('tr', null, (everyone() ? ['Time', 'User', 'Source', 'Severity', 'Event'] : ['Time', 'Source', 'Severity', 'Event']).map((c) => h('th', { scope: 'col', text: c })))),
      h('tbody', null, list.map((l) => {
        const sev = SEVERITY[l.severity];
        return h('tr', null,
          h('td', { text: fmt.dateTime(l.t) }),
          everyone() ? h('td', null, l.userName || h('span', { class: 'sub', text: 'Instance' })) : null,
          h('td', { text: l.source }),
          h('td', null, h('span', { class: 'chip tone-' + sev.tone }, icon(sev.ic), sev.label)),
          h('td', null, h('code', { text: l.event })));
      }))));
    $('#table-foot').textContent = 'Showing ' + list.length + (list.length === 1 ? ' event' : ' events');
  }

  async function loadLogs() {
    const session = state.session;
    try {
      const list = everyone() ? await API.listAllLogs(state.logFilters) : await API.listLogs(state.logFilters);
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

  // Administrators may switch Logins and Logs from their own activity to everyone's.
  const everyone = () => !!state.everyone && isAdmin();

  function whoseSelect() {
    if (!isAdmin()) return null;
    return h('label', { class: 'field' }, h('span', { text: 'Show' }),
      h('select', {
        onChange: (e) => {
          state.everyone = e.target.value === 'everyone';
          state.logins = null;
          state.logs = null;
          if (state.route === 'logins') viewLogins(); else viewLogs();
        },
      }, [['mine', 'My activity'], ['everyone', 'Everyone']].map(([value, text]) => h('option', { value, text, selected: (value === 'everyone') === !!state.everyone }))));
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
      h('p', { class: 'page-lead', text: everyone() ? 'Events for every managed instance, and everyone\'s connections to the control panel.' : 'Events for the instances you can use, and your own connections to the control panel.' }),
      h('div', { class: 'toolbar' },
        whoseSelect(),
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

  // ---- views: User management (user managers and administrators) -----------------------------------------

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

  const ROLES = [
    { key: 'operators', label: 'Operator', help: 'May start the instances they are granted.' },
    { key: 'user_mgrs', label: 'User manager', help: 'May open User management and change who can start what.' },
    { key: 'admin', label: 'Administrator', help: 'May also change roles, including who else is an administrator.' },
  ];

  const pendingCount = () => um().staged.size + um().stagedRoles.size;
  const hasRole = (user, key) => (um().stagedRoles.has(key) ? um().stagedRoles.get(key) : user.roles.includes(key));

  function umDirty() {
    return pendingCount() > 0;
  }

  async function confirmDiscard() {
    if (!umDirty()) return true;
    return confirmDialog({
      title: 'Discard unsaved changes?',
      body: h('p', { text: 'You have ' + pendingCount() + ' unsaved change' + (pendingCount() === 1 ? '' : 's') + '. They will be lost.' }),
      confirmLabel: 'Discard',
      cancelLabel: 'Keep editing',
    });
  }

  async function selectUser(id) {
    if (id === um().selectedId) return;
    if (!(await confirmDiscard())) { paintUM(); return; }
    um().selectedId = id;
    um().staged = new Map();
    um().stagedRoles = new Map();
    um().saved = new Set();
    paintUM();
    try {
      const granted = await API.getGrants(id);
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

  function toggleRole(key, checked) {
    const user = umUser();
    if (checked === user.roles.includes(key)) um().stagedRoles.delete(key);
    else um().stagedRoles.set(key, checked);
    paintUM();
  }

  function pendingChanges() {
    return Array.from(um().staged.entries()).map(([instanceId, grant]) => ({ instanceId, grant }));
  }

  async function saveChanges() {
    const user = umUser();
    const changes = pendingChanges();
    const roleChanges = Array.from(um().stagedRoles.entries()).map(([role, member]) => ({ role, member }));
    const total = changes.length + roleChanges.length;
    const nameOf = (id) => um().instances.find((i) => i.id === id).name;
    const labelOfRole = (key) => ROLES.find((g) => g.key === key).label;
    const losesOperator = roleChanges.some((c) => c.role === 'operators' && !c.member);
    const ok = await confirmDialog({
      title: 'Save changes for ' + user.name + '?',
      body: h('div', null,
        h('ul', null,
          roleChanges.map((c) => h('li', { text: (c.member ? 'Add role: ' : 'Remove role: ') + labelOfRole(c.role) })),
          changes.map((c) => h('li', { text: (c.grant ? 'Grant: ' : 'Revoke: ') + nameOf(c.instanceId) }))),
        losesOperator ? h('p', { text: 'Removing ' + user.name + ' from Operator also revokes every instance they were granted.' }) : null,
        h('p', { class: 'muted', text: 'This changes who may start each instance and which roles a person has. It does not start or stop any instance: a running instance keeps running.' }),
        null),
      confirmLabel: 'Save changes',
    });
    if (!ok) return;
    try {
      await API.saveUserChanges(user.id, { roles: roleChanges, grants: changes });
      um().users = await API.listUsers();
      const granted = await API.getGrants(user.id);
      um().saved = new Set(granted);
      um().staged = new Map();
      um().stagedRoles = new Map();
      um().changes = await API.listChanges();
      toast('Saved ' + total + ' change' + (total === 1 ? '' : 's') + ' for ' + user.name, 'ok');
      announce('Saved ' + total + ' change' + (total === 1 ? '' : 's') + ' for ' + user.name);
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
          options: (um().users || []).map((u) => ({ id: u.id, label: u.name + ', ' + roleLabel(u.roles) + (u.spendBlock ? ' (blocked: spend cap)' : '') })),
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
        body.append(h('p', { class: 'empty-small', text: 'Choose a user to see and change their roles and which instances they may start.' }));
        return;
      }
      const operator = user.roles.includes('operators');
      const userIsAdmin = user.roles.includes('admin');
      if (user.spendBlock) {
        body.append(h('div', { class: 'error-box', role: 'status' }, icon('error'),
          h('span', null, h('strong', { text: 'Blocked: spend cap. ' }), user.spendBlock.reason),
          isAdmin() ? h('a', { class: 'btn btn-secondary', href: '#/spend', text: 'Open Spend caps' }) : null));
      }
      if (userIsAdmin) {
        body.append(h('div', { class: 'notice' }, icon('info'),
          h('span', { text: user.name + ' is an administrator, who can see and start every instance without grants.' })));
      } else if (!operator) {
        body.append(h('div', { class: 'notice' }, icon('info'),
          h('span', { text: user.name + ' does not have the Operator role, so cannot be granted instances. ' + (isAdmin()
            ? 'Give them the Operator role in the Roles section below, save, then grant instances.'
            : 'Ask an administrator to give them the Operator role.') })));
      }
      const isSelf = state.session && user.id === state.session.userId;
      body.append(h('h2', { class: 'section-title', text: 'Roles' }),
        h('table', { class: 'grid' },
          h('caption', { class: 'sr-only', text: 'Roles ' + user.name + ' has' }),
          h('thead', null, h('tr', null,
            h('th', { scope: 'col', class: 'check-cell' }, h('span', { class: 'sr-only', text: 'Has role' })),
            h('th', { scope: 'col', text: 'Role' }),
            h('th', { scope: 'col', text: 'Status' }))),
          h('tbody', null, ROLES.map((g) => {
            const ownLock = isSelf && g.key === 'admin';
            const locked = !isAdmin() || ownLock;
            const checked = hasRole(user, g.key);
            const pending = um().stagedRoles.has(g.key);
            return h('tr', null,
              h('td', { class: 'check-cell' }, h('input', {
                type: 'checkbox', id: 'role-' + g.key, checked, disabled: locked, 'data-key': 'role:' + g.key,
                'aria-labelledby': 'role-name-' + g.key, 'aria-describedby': 'role-help-' + g.key,
                onChange: (e) => toggleRole(g.key, e.target.checked),
              })),
              h('td', null,
                h('label', { id: 'role-name-' + g.key, for: 'role-' + g.key, class: 'name', text: g.label }),
                h('div', { id: 'role-help-' + g.key, class: 'idrow' }, h('span', { text: g.help + (!isAdmin() ? ' Only an administrator can change roles.' : ownLock ? ' You cannot remove your own administrator role.' : '') }))),
              h('td', null, pending
                ? h('span', { class: 'chip tone-amber' }, icon('dot'), checked ? 'Pending: add' : 'Pending: remove')
                : (user.roles.includes(g.key)
                  ? h('span', { class: 'chip tone-green' }, icon('check'), 'Has role')
                  : h('span', { class: 'chip tone-grey' }, icon('dot'), 'No role'))));
          }))),
        h('p', { class: 'muted', text: 'Demo users are put back in their Terraform roles on every apply. Changes to anyone else stay.' }),
        h('h2', { class: 'section-title', text: 'Instance access' }));
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
      const n = pendingCount();
      body.append(h('div', { class: 'savebar', role: 'region', 'aria-label': 'Unsaved changes', hidden: n === 0 },
        h('span', null, h('strong', { text: String(n) }), ' unsaved change' + (n === 1 ? '' : 's')),
        h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: () => { um().staged = new Map(); um().stagedRoles = new Map(); paintUM(); }, text: 'Discard' }),
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
          h('thead', null, h('tr', null, ['Time', 'Changed by', 'User', 'Item', 'Change', 'Result'].map((c) => h('th', { scope: 'col', text: c })))),
          h('tbody', null, rows.map((r) => h('tr', null,
            h('td', { text: fmt.dateTime(r.t) }),
            h('td', { text: nameOfUser(r.adminId) }),
            h('td', { text: nameOfUser(r.userId) }),
            h('td', { text: r.role || nameOfInst(r.instanceId) }),
            h('td', null, h('span', { class: 'chip ' + (r.action === 'Granted' || r.action === 'Added' ? 'tone-green' : 'tone-grey') }, r.action)),
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
          h('p', { text: 'User management is for user managers.' })));
      return;
    }
    main.replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'User management' }),
      h('p', { class: 'page-lead', text: 'Choose which roles a person has and which instances they may start. Nothing changes until you save.' }),
      h('div', { id: 'um-picker', class: 'um-picker' }),
      h('div', { id: 'um-body' }),
      h('div', { id: 'um-changes' }));
    const session = state.session;
    try {
      const [users, instances, changes] = await Promise.all([API.listUsers(), API.listAllInstances(), API.listChanges()]);
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

  // ---- views: Open WebUI status and Usage and cost (administrators) -------------------------
  //
  // Both pages ask the lab instance one named question through the Control API (a Systems
  // Manager command that returns one JSON line), so an answer takes a few seconds and only
  // works while the instance is running. The page shows what came back and nothing else.

  const money = (v) => (v === null || v === undefined ? 'n/a'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) < 1 ? 4 : 2 }).format(v));
  const count = (v) => new Intl.NumberFormat().format(v);
  const bytesText = (v) => (v >= 1e9 ? (v / 1e9).toFixed(1) + ' GB' : (v / 1e6).toFixed(0) + ' MB');
  const WORKING = ['Pending', 'InProgress', 'Delayed', 'Cancelling'];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // Runs one action on the lab's instance and resolves with the finished answer, or null when
  // the person has signed out meanwhile.
  async function runAction(action, extra, onWaiting) {
    const session = state.session;
    const instances = await API.listAllInstances();
    if (session !== state.session) return null;
    if (!instances.length) throw new Error('No lab is deployed, so there is no instance to ask.');
    const started = await API.startWebuiAction(instances[0].id, action, extra);
    for (let i = 0; i < 75; i++) {
      await sleep(i === 0 ? 1500 : 3000);
      if (session !== state.session) return null;
      const answer = await API.getWebuiAction(started.commandId);
      if (!WORKING.includes(answer.status)) return answer;
      if (onWaiting) onWaiting(answer.status);
    }
    throw new Error('The instance did not answer in time. Try again.');
  }

  function adminGate(title) {
    if (isAdmin()) return false;
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: title }),
      h('div', { class: 'empty-state' }, h('h2', { text: 'You do not have access to this page.' }), h('p', { text: 'This page is for administrators.' })));
    return true;
  }

  function failedText(answer) {
    const r = answer.result;
    if (r && r.error) return r.error;
    if (answer.status === 'Expired') return 'The instance never reported back. Try again.';
    return 'The instance could not answer (' + answer.status + ').';
  }

  function errorBox(message, retry) {
    return h('div', { class: 'error-box', role: 'alert' }, icon('error'), h('span', { text: message }),
      h('button', { type: 'button', class: 'btn btn-secondary', onClick: retry, text: 'Try again' }));
  }

  function factCard(label, value, chip) {
    return h('div', { class: 'fact' },
      h('div', { class: 'stat-label', text: label }),
      h('div', { class: 'stat-value', text: value }),
      chip || null);
  }

  // ---- Open WebUI status

  function paintOpenWebui() {
    const wrap = $('#ow-body');
    if (!wrap || state.route !== 'open-webui') return;
    const s = state.openWebui;
    wrap.replaceChildren();
    if (s.error) { wrap.append(errorBox(s.error, checkOpenWebui)); return; }
    if (!s.answer) {
      wrap.append(h('p', { class: 'empty-small', text: s.busy ? (s.note || 'Asking the instance') + '. This takes a few seconds.' : 'Not checked yet.' }));
      return;
    }
    const r = s.answer.result;
    const chip = (ok, yes, no) => (ok ? h('span', { class: 'chip tone-green' }, icon('check'), yes) : h('span', { class: 'chip tone-red' }, icon('error'), no));
    const match = r.versionMatches === null ? h('span', { class: 'chip tone-grey' }, icon('info'), 'Not pinned to a version')
      : r.versionMatches ? h('span', { class: 'chip tone-green' }, icon('check'), 'Matches the lab')
        : h('span', { class: 'chip tone-amber' }, icon('warn'), 'Differs from the lab\'s ' + r.expectedVersion);
    wrap.append(
      h('div', { class: 'facts' },
        factCard('Open WebUI', r.healthy ? 'Healthy' : 'Not answering', chip(r.healthy, 'Health check passed', 'Health check failed')),
        factCard('Version', r.version || 'Unknown', match),
        factCard('Ollama', r.ollama ? 'Answering' : 'Not answering', chip(!!r.ollama, count(r.ollama ? r.ollama.loaded.length : 0) + ' loaded', 'No answer from Ollama'))));
    if (r.ollama) {
      const loaded = new Map(r.ollama.loaded.map((m) => [m.name, m]));
      wrap.append(
        h('h2', { class: 'section-title', text: 'Models in Ollama' }),
        r.ollama.installed.length === 0 ? h('p', { class: 'empty-small', text: 'No models are installed.' }) :
          h('table', { class: 'grid' },
            h('caption', { class: 'sr-only', text: 'Models installed in Ollama' }),
            h('thead', null, h('tr', null, ['Model', 'Size on disk', 'Loaded', 'Memory used', 'In GPU memory'].map((c) => h('th', { scope: 'col', text: c })))),
            h('tbody', null, r.ollama.installed.map((m) => {
              const on = loaded.get(m.name);
              return h('tr', null,
                h('td', null, h('code', { text: m.name })),
                h('td', { text: bytesText(m.sizeBytes) }),
                h('td', null, on ? h('span', { class: 'chip tone-green' }, icon('check'), 'Loaded') : h('span', { class: 'chip tone-grey' }, icon('dot'), 'Not loaded')),
                h('td', { text: on ? bytesText(on.sizeBytes) : '' }),
                h('td', null, on ? (on.vramBytes >= on.sizeBytes
                  ? h('span', { class: 'chip tone-green' }, icon('check'), 'All of it')
                  : h('span', { class: 'chip tone-amber' }, icon('warn'), Math.round(on.vramBytes / on.sizeBytes * 100) + '%, the rest runs on the CPU')) : ''));
            }))));
    }
    wrap.append(h('p', { class: 'table-foot', text: 'Checked at ' + fmt.clock(s.answer.requestedAt) + '. Names and sizes only; nothing from chats is read.' }));
  }

  async function checkOpenWebui() {
    const s = state.openWebui;
    if (s.busy) return;
    s.busy = true; s.error = null; s.note = '';
    paintOpenWebui();
    try {
      const answer = await runAction('status', {}, (status) => { s.note = status === 'Pending' ? 'Waiting for the instance' : 'Checking'; paintOpenWebui(); });
      if (answer === null) return;
      if (answer.status !== 'Success' || !answer.result || !answer.result.ok) s.error = failedText(answer);
      else { s.answer = answer; state.lastUpdated = API.now(); }
    } catch (err) {
      s.error = err.message;
    }
    s.busy = false;
    paintOpenWebui();
    paintControls();
    paintStatusBar();
  }

  function viewOpenWebui() {
    if (adminGate('Open WebUI')) return;
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Open WebUI' }),
      h('p', { class: 'page-lead', text: 'Whether Open WebUI and Ollama are answering on the lab instance, which version is running, and which models are loaded. Read only.' }),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: checkOpenWebui }, icon('refresh'), 'Check now')),
      h('div', { id: 'ow-body' }),
      h('h2', { class: 'section-title', text: 'Tool servers' }),
      h('p', { class: 'page-lead', text: 'The tool servers Open WebUI knows about. Test connection asks Open WebUI to connect to each enabled one and list its tools, the same check as the Verify button in Open WebUI. Keys and headers are never shown.' }),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: () => runSection('tools') }, icon('refresh'), 'Test connection')),
      h('div', { id: 'ow-tools' }),
      h('h2', { class: 'section-title', text: 'Settings in effect' }),
      h('p', { class: 'page-lead', text: 'A fixed list of Open WebUI settings. Global applies to everyone; User default is the starting point for new users.' }),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: () => runSection('settings') }, icon('refresh'), 'Read settings')),
      h('div', { id: 'ow-settings' }),
      h('h2', { class: 'section-title', text: 'Export configuration' }),
      h('p', { class: 'page-lead', text: 'Downloads the settings above, the tool servers (name, type and address only) and the models (numeric parameters only) as one file. Left out, always: passwords, API keys and tokens, tool server keys, headers and OAuth settings, system prompts, users, chats, files and memories.' }),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: exportConfiguration }, icon('copy'), 'Download configuration')),
      h('div', { id: 'ow-export' }),
      h('h2', { class: 'section-title', text: 'Change settings' }),
      h('p', { class: 'page-lead', text: 'Changes here are made on the running lab, read back from Open WebUI, and kept so they are put back at the next start. Settings fixed in Terraform (the lab\'s own model and tool server, sign-up and the version) are not changed here.' }),
      h('div', { id: 'ow-controls' }));
    paintSections();
    paintControls();
    if (!state.owx.ctl.desired && !state.owx.ctl.loading) loadControls();
    if (state.openWebui.answer) paintOpenWebui(); else checkOpenWebui();
  }

  const SETTING_LABELS = {
    ENABLE_SIGNUP: 'Anyone can sign up', DEFAULT_USER_ROLE: 'Role for new users', JWT_EXPIRES_IN: 'Sign-in lasts',
    ENABLE_API_KEY: 'API keys allowed', ENABLE_API_KEYS: 'API keys allowed', ENABLE_API_KEY_ENDPOINT_RESTRICTIONS: 'API keys limited to some routes',
    API_KEY_ALLOWED_ENDPOINTS: 'Routes API keys may call', ENABLE_COMMUNITY_SHARING: 'Community sharing', ENABLE_MESSAGE_RATING: 'Thumbs up and down on answers',
    ENABLE_CHANNELS: 'Channels', ENABLE_NOTES: 'Notes', ENABLE_USER_WEBHOOKS: 'User webhooks', SHOW_ADMIN_DETAILS: 'Show admin details',
    DEFAULT_MODELS: 'Default model', IMAGE_GENERATION_ENABLED: 'Image generation',
  };
  const settingLabel = (key) => SETTING_LABELS[key] || key.replace(/[._]/g, ' ');
  const settingValue = (v) => (v === true ? 'On' : v === false ? 'Off' : v === '' ? '(empty)' : String(v));

  // One of the two sections that ask for an answer and show it (tools, settings).
  async function runSection(key) {
    const s = state.owx[key];
    if (s.busy) return;
    s.busy = true; s.error = null; s.note = '';
    paintSections();
    try {
      const answer = await runAction(key === 'tools' ? 'tool-servers' : 'settings', {}, (status) => { s.note = status === 'Pending' ? 'Waiting for the instance' : 'Asking Open WebUI'; paintSections(); });
      if (answer === null) return;
      if (answer.status !== 'Success' || !answer.result || !answer.result.ok) s.error = failedText(answer);
      else s.answer = answer;
    } catch (err) {
      s.error = err.message;
    }
    s.busy = false;
    paintSections();
  }

  function toolServerChip(srv) {
    if (!srv.enabled) return h('span', { class: 'chip tone-grey' }, icon('dot'), 'Turned off in Open WebUI');
    if (srv.reachable === true) return h('span', { class: 'chip tone-green' }, icon('check'), 'Reachable, signed in, ' + count(srv.toolCount) + (srv.toolCount === 1 ? ' tool' : ' tools') + ' found');
    if (srv.reachable === false) return h('span', { class: 'chip tone-red' }, icon('error'), 'Could not connect or list tools');
    return h('span', { class: 'chip tone-amber' }, icon('warn'), 'Not checked this time');
  }

  function paintSections() {
    if (state.route !== 'open-webui') return;
    const tools = $('#ow-tools'), settings = $('#ow-settings'), exp = $('#ow-export');
    if (!tools || !settings || !exp) return;
    const t = state.owx.tools;
    tools.replaceChildren();
    if (t.error) tools.append(errorBox(t.error, () => runSection('tools')));
    else if (t.busy) tools.append(h('p', { class: 'empty-small', text: (t.note || 'Asking the instance') + '. Testing each server can take up to a minute.' }));
    else if (!t.answer) tools.append(h('p', { class: 'empty-small', text: 'Not tested yet.' }));
    else {
      const r = t.answer.result;
      tools.append(r.servers.length === 0 ? h('p', { class: 'empty-small', text: 'Open WebUI has no tool servers.' }) :
        h('table', { class: 'grid' },
          h('caption', { class: 'sr-only', text: 'Tool servers' }),
          h('thead', null, h('tr', null, ['Name', 'Type', 'Address', 'Sign-in', 'Result'].map((c) => h('th', { scope: 'col', text: c })))),
          h('tbody', null, r.servers.map((srv) => h('tr', null,
            h('td', { text: srv.name || srv.id || '(unnamed)' }),
            h('td', { text: srv.type === 'mcp' ? 'MCP' : srv.type === 'openapi' ? 'OpenAPI' : srv.type }),
            h('td', null, h('code', { text: srv.url + (srv.path ? ' ' + srv.path : '') })),
            h('td', { text: srv.authType === 'none' ? 'None' : srv.authType }),
            h('td', null, toolServerChip(srv),
              srv.error ? h('div', { class: 'cell-note', text: srv.error + (srv.reachable === false ? ' Check the address, the token and that the server is running.' : '') }) : null,
              srv.toolNames && srv.toolNames.length ? h('div', { class: 'cell-note', text: srv.toolNames.join(', ') }) : null))))),
        h('p', { class: 'table-foot', text: 'Tested at ' + fmt.clock(t.answer.requestedAt) + '. ' + count(r.total) + (r.total === 1 ? ' server' : ' servers') + ' in Open WebUI.' }));
    }
    const g = state.owx.settings;
    settings.replaceChildren();
    if (g.error) settings.append(errorBox(g.error, () => runSection('settings')));
    else if (g.busy) settings.append(h('p', { class: 'empty-small', text: (g.note || 'Asking the instance') + '. This takes a few seconds.' }));
    else if (!g.answer) settings.append(h('p', { class: 'empty-small', text: 'Not read yet.' }));
    else {
      const r = g.answer.result;
      settings.append(
        r.settings.length === 0 ? h('p', { class: 'empty-small', text: 'Open WebUI returned none of the settings on the list.' }) :
          h('table', { class: 'grid' },
            h('caption', { class: 'sr-only', text: 'Open WebUI settings in effect' }),
            h('thead', null, h('tr', null, ['Setting', 'Value', 'Applies to'].map((c) => h('th', { scope: 'col', text: c })))),
            h('tbody', null, r.settings.map((x) => h('tr', null,
              h('td', { text: settingLabel(x.key) }),
              h('td', { text: settingValue(x.value) }),
              h('td', null, x.scope === 'Global' ? h('span', { class: 'chip tone-info' }, 'Global') : h('span', { class: 'chip tone-grey' }, 'User default')))))),
        r.unavailable.length ? h('p', { class: 'table-foot', text: 'Open WebUI did not answer for: ' + r.unavailable.join(', ') + '. This version may not support them.' }) : null,
        h('p', { class: 'table-foot', text: 'Read at ' + fmt.clock(g.answer.requestedAt) + '.' }));
    }
    exp.replaceChildren();
    if (state.owx.exportError) exp.append(errorBox(state.owx.exportError, exportConfiguration));
    else if (state.owx.exportBusy) exp.append(h('p', { class: 'empty-small', text: 'Preparing the export. This takes a few seconds.' }));
    else if (state.owx.exportDone) exp.append(h('p', { class: 'table-foot', text: state.owx.exportDone }));
  }

  async function exportConfiguration() {
    const x = state.owx;
    if (x.exportBusy) return;
    x.exportBusy = true; x.exportError = null; x.exportDone = null;
    paintSections();
    try {
      const answer = await runAction('export-config', {}, () => {});
      if (answer === null) return;
      if (answer.status !== 'Success' || !answer.result || !answer.result.ok) x.exportError = failedText(answer);
      else {
        const { ok, action, ...doc } = answer.result;
        const stamp = new Date().toISOString().slice(0, 10);
        const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' }));
        const link = h('a', { href: url, download: 'open-webui-configuration-' + stamp + '.json' });
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
        x.exportDone = 'Downloaded at ' + fmt.clock(answer.requestedAt) + '.' + (doc.truncated ? ' The file was too large, so models list their IDs only.' : '')
          + (doc.unavailable.length ? ' Not available from this Open WebUI: ' + doc.unavailable.join(', ') + '.' : '');
      }
    } catch (err) {
      x.exportError = err.message;
    }
    x.exportBusy = false;
    paintSections();
  }

  // ---- Open WebUI changes
  //
  // Each control sends one action. The instance changes the setting, reads it back from Open WebUI,
  // and reports success only if it matches. The control panel then keeps the setting so it can be
  // put back when the lab starts again (see "Saved settings").

  const FEATURE_ROWS = [
    ['api_keys', 'API keys', 'Lets people create API keys for their own scripts.'],
    ['message_rating', 'Thumbs up and down on answers', 'Shows the rating buttons under each answer.'],
    ['image_generation', 'Image generation', 'Turns the image feature on or off. The engine itself is set in Open WebUI.'],
    ['memory', 'Memory', 'Lets people turn on Open WebUI memories for their own chats.'],
  ];

  async function loadControls() {
    const c = state.owx.ctl;
    c.loading = true;
    paintControls();
    const [tokens, desired] = await Promise.allSettled([API.getWebuiToolTokens(), API.getWebuiDesiredState()]);
    c.tokens = tokens.status === 'fulfilled' ? tokens.value : { prefix: '', names: [] };
    c.tokensError = tokens.status === 'rejected' ? tokens.reason.message : null;
    if (desired.status === 'fulfilled') { c.desired = desired.value; c.desiredError = null; } else c.desiredError = desired.reason.message;
    try {
      const list = await API.listAllInstances();
      const url = list[0] && list[0].url;
      c.adminUrl = typeof url === 'string' && /^https:\/\//.test(url) ? url.replace(/\/+$/, '') + '/admin/settings' : null;
    } catch (err) { c.adminUrl = null; }
    c.loading = false;
    paintControls();
  }

  async function refreshDesired() {
    const c = state.owx.ctl;
    try { c.desired = await API.getWebuiDesiredState(); c.desiredError = null; } catch (err) { c.desiredError = err.message; }
  }

  async function refreshAndPaint() { await refreshDesired(); paintControls(); }

  // Runs one change. `confirm` is { title, body, confirmLabel } for the changes that affect everyone.
  async function changeWebui(label, action, extra, confirm) {
    const c = state.owx.ctl;
    if (c.busy) return false;
    if (confirm) {
      const ok = await confirmDialog(Object.assign({ confirmLabel: 'Make the change' }, confirm));
      if (!ok) return false;
      extra = Object.assign({}, extra, { confirmed: true });
    }
    c.busy = label; c.message = null;
    paintControls();
    let done = false;
    try {
      const answer = await runAction(action, extra, (status) => { c.busy = label + (status === 'Pending' ? ' (waiting for the instance)' : ''); paintControls(); });
      if (answer === null) return false;
      const r = answer.result;
      if (answer.status === 'Success' && r && r.ok) {
        done = true;
        c.message = { ok: true, text: label + ': done, and read back from Open WebUI.' };
        toast(label + ' saved.', 'ok');
        state.owx.settings.answer = null;
        state.owx.tools.answer = null;
      } else {
        c.message = { ok: false, text: label + ' did not change anything. ' + failedText(answer) };
      }
    } catch (err) {
      c.message = { ok: false, text: label + ' did not change anything. ' + err.message };
    }
    c.busy = null;
    await refreshDesired();
    paintControls();
    paintSections();
    return done;
  }

  function installedModels() {
    const a = state.openWebui.answer;
    return a && a.result.ollama ? a.result.ollama.installed.map((m) => m.name) : [];
  }

  const numberValue = (id) => { const v = ($('#' + id).value || '').trim(); return v === '' ? undefined : Number(v); };

  function describeSaved(i) {
    if (i.kind === 'default-model') return 'Default model: ' + i.model;
    if (i.kind === 'model-params') return 'Model ' + i.model + ': ' + [i.temperature !== undefined ? 'temperature ' + i.temperature : null, i.numCtx !== undefined ? 'context ' + i.numCtx : null].filter(Boolean).join(', ');
    if (i.kind === 'feature') {
      const row = FEATURE_ROWS.find((f) => f[0] === i.feature);
      const name = row ? row[1] : i.feature === 'api_key_routes' ? 'Routes API keys may call' : i.feature;
      return name + ': ' + (i.value === true ? 'On' : i.value === false ? 'Off' : (i.value.length ? i.value.join(', ') : '(none)'));
    }
    return 'Tool server ' + (i.name || i.id) + ' (' + (i.type === 'mcp' ? 'MCP' : 'OpenAPI') + ') at ' + i.url + (i.path ? ' ' + i.path : '') + (i.enabled === false ? ', turned off' : '') + (i.tokenSecret ? ', key ' + i.tokenSecret.split('/').pop() : ', no key');
  }

  function fillServerForm(i) {
    $('#ts-id').value = i.id; $('#ts-name').value = i.name || ''; $('#ts-type').value = i.type; $('#ts-url').value = i.url;
    $('#ts-path').value = i.path || ''; $('#ts-token').value = i.tokenSecret || ''; $('#ts-enabled').checked = i.enabled !== false;
    $('#ts-id').focus();
  }

  function paintControls() {
    const wrap = $('#ow-controls');
    if (!wrap || state.route !== 'open-webui') return;
    const c = state.owx.ctl;
    const busy = !!c.busy;
    const models = installedModels();
    const keep = {};
    wrap.querySelectorAll('input[id], select[id], textarea[id]').forEach((el) => { keep[el.id] = el.type === 'checkbox' ? el.checked : el.value; });
    wrap.replaceChildren();
    const btn = (text, onClick, cls) => h('button', { type: 'button', class: 'btn ' + (cls || 'btn-secondary'), disabled: busy ? true : null, onClick, text });
    const complain = (text) => { c.message = { ok: false, text }; paintControls(); };
    const modelSelect = (id) => h('select', { id, class: 'cap-input', 'aria-label': 'Model' },
      models.length ? models.map((m) => h('option', { value: m, text: m })) : [h('option', { value: '', text: 'Check Open WebUI first' })]);
    const tokenNames = (c.tokens && c.tokens.names) || [];
    const tokenPrefix = (c.tokens && c.tokens.prefix) || '';

    put(wrap,
      c.busy ? h('div', { class: 'info-box', role: 'status' }, icon('info'), h('span', { text: 'Working on: ' + c.busy + '. Open WebUI is read back before this says it worked.' })) : null,
      c.message ? (c.message.ok ? h('div', { class: 'info-box', role: 'status' }, icon('check'), h('span', { text: c.message.text }))
        : h('div', { class: 'error-box', role: 'alert' }, icon('error'), h('span', { text: c.message.text }))) : null,

      h('h3', { class: 'section-title', text: 'Default model' }),
      h('p', { class: 'page-lead', text: 'The model new chats start with, for everyone.' }),
      h('div', { class: 'toolbar' },
        modelSelect('dm-model'), h('span', { class: 'spacer' }),
        btn('Set default model', () => {
          const model = $('#dm-model').value;
          if (!model) { complain('Choose a model first.'); return; }
          changeWebui('Default model', 'set-default-model', { model });
        })),

      h('h3', { class: 'section-title', text: 'Model settings' }),
      h('p', { class: 'page-lead', text: 'Temperature (0 to 2) and context length (512 to 131072 tokens) for one model. Leave a box empty to leave that value as it is. The lab\'s own model, security-analyst, is set up at every start and cannot be changed here.' }),
      h('div', { class: 'toolbar' },
        modelSelect('mp-model'),
        h('input', { id: 'mp-temp', type: 'number', min: '0', max: '2', step: '0.1', class: 'cap-input', placeholder: 'Temperature', 'aria-label': 'Temperature' }),
        h('input', { id: 'mp-ctx', type: 'number', min: '512', max: '131072', step: '1', class: 'cap-input', placeholder: 'Context length', 'aria-label': 'Context length' }),
        h('span', { class: 'spacer' }),
        btn('Save model settings', () => {
          const model = $('#mp-model').value, extra = { model }, t = numberValue('mp-temp'), n = numberValue('mp-ctx');
          if (!model) { complain('Choose a model first.'); return; }
          if (t === undefined && n === undefined) { complain('Give a temperature or a context length.'); return; }
          if (t !== undefined) extra.temperature = t;
          if (n !== undefined) extra.numCtx = n;
          changeWebui('Model settings', 'set-model-params', extra);
        })),

      h('h3', { class: 'section-title', text: 'Features' }),
      h('p', { class: 'page-lead', text: 'Each switch changes Open WebUI for every user and asks you to confirm first. Read settings above shows what is on now.' }),
      h('table', { class: 'grid' },
        h('caption', { class: 'sr-only', text: 'Open WebUI features' }),
        h('thead', null, h('tr', null, ['Feature', 'Change'].map((x) => h('th', { scope: 'col', text: x })))),
        h('tbody', null, FEATURE_ROWS.map(([key, name, help]) => {
          const set = (value) => changeWebui(name, 'set-feature', { feature: key, value }, {
            title: (value ? 'Turn on ' : 'Turn off ') + name.toLowerCase() + '?',
            body: h('p', { text: 'This changes Open WebUI for every user straight away, and the lab puts it back this way each time it starts.' }),
            confirmLabel: value ? 'Turn on' : 'Turn off',
          });
          return h('tr', null,
            h('td', null, h('div', { class: 'name', text: name }), h('div', { class: 'cell-note', text: help })),
            h('td', null, h('div', { class: 'toolbar' }, btn('Turn on', () => set(true)), btn('Turn off', () => set(false)))));
        }))),
      h('label', { for: 'routes', class: 'field-label', text: 'Routes API keys may call' }),
      h('p', { class: 'table-foot', text: 'One route per line, such as /api/chat/completions. Empty means none.' }),
      h('textarea', { id: 'routes', class: 'cap-input', rows: '3', style: 'width:100%;max-width:420px' }),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        btn('Save routes', () => {
          const value = $('#routes').value.split(/[\s,]+/).filter(Boolean);
          changeWebui('API key routes', 'set-feature', { feature: 'api_key_routes', value }, {
            title: 'Change the routes API keys may call?',
            body: h('p', { text: value.length ? 'API keys will be limited to: ' + value.join(', ') : 'No route will be allowed for API keys.' }),
            confirmLabel: 'Save routes',
          });
        })),

      h('h3', { class: 'section-title', text: 'Add or change a tool server' }),
      h('p', { class: 'page-lead', text: 'The address must be HTTPS. Open WebUI has to pass its own connection check before anything is saved. The key is chosen from the secrets the lab keeps for this; its value is never shown here. vuln-findings is set up by the lab and is not changed here.' }),
      c.tokensError ? h('p', { class: 'table-foot', text: 'Could not list the keys: ' + c.tokensError }) : null,
      h('div', { class: 'toolbar' },
        h('input', { id: 'ts-id', type: 'text', class: 'cap-input', maxlength: '40', placeholder: 'ID (letters, digits, - _)', 'aria-label': 'Server ID' }),
        h('input', { id: 'ts-name', type: 'text', class: 'cap-input', maxlength: '60', placeholder: 'Name', 'aria-label': 'Name' }),
        h('select', { id: 'ts-type', class: 'cap-input', 'aria-label': 'Type' }, h('option', { value: 'mcp', text: 'MCP' }), h('option', { value: 'openapi', text: 'OpenAPI' }))),
      h('div', { class: 'toolbar' },
        h('input', { id: 'ts-url', type: 'url', class: 'cap-input', style: 'width:320px', placeholder: 'https://tools.example.com', 'aria-label': 'Address' }),
        h('input', { id: 'ts-path', type: 'text', class: 'cap-input', maxlength: '100', placeholder: 'Path (optional)', 'aria-label': 'Path' }),
        h('select', { id: 'ts-token', class: 'cap-input', 'aria-label': 'Key' },
          h('option', { value: '', text: 'No key' }),
          tokenNames.map((n) => h('option', { value: n, text: n.slice(tokenPrefix.length) || n }))),
        h('label', { class: 'field' }, h('input', { id: 'ts-enabled', type: 'checkbox', checked: true }), 'Turned on')),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        btn('Remove this ID', () => {
          const id = $('#ts-id').value.trim();
          if (!id) { complain('Type the ID of the server to remove.'); return; }
          changeWebui('Remove tool server', 'remove-tool-server', { id }, {
            title: 'Remove tool server ' + id + '?', body: h('p', { text: 'Open WebUI stops offering its tools to everyone. Nothing on the server itself is touched.' }), confirmLabel: 'Remove',
          });
        }),
        btn('Save tool server', () => {
          const extra = { id: $('#ts-id').value.trim(), name: $('#ts-name').value.trim(), type: $('#ts-type').value, url: $('#ts-url').value.trim(), path: $('#ts-path').value.trim(), tokenSecret: $('#ts-token').value, enabled: $('#ts-enabled').checked };
          changeWebui('Tool server', 'upsert-tool-server', extra, {
            title: 'Save tool server ' + (extra.name || extra.id) + '?',
            body: h('p', { text: 'Open WebUI will connect to ' + extra.url + ' with ' + (extra.tokenSecret ? 'the chosen key' : 'no key') + ' and offer its tools to everyone. The change is kept only if the connection check passes.' }),
            confirmLabel: 'Save',
          });
        }, 'btn-live')),

      h('h3', { class: 'section-title', text: 'Import a skill' }),
      h('p', { class: 'page-lead', text: 'Paste the text of a SKILL.md (up to 1800 characters). It is stored in Open WebUI as plain text and never run. Choose the models that should have it, or none and attach it in Open WebUI. Skills are not kept for the next start. A skill with files, a ZIP or a folder is imported in Open WebUI itself (Workspace, Skills). Importing again with the same ID replaces the skill.' }),
      h('div', { class: 'toolbar' },
        h('input', { id: 'sk-id', type: 'text', class: 'cap-input', maxlength: '40', placeholder: 'ID (letters, digits, - _)', 'aria-label': 'Skill ID' }),
        h('input', { id: 'sk-name', type: 'text', class: 'cap-input', maxlength: '80', placeholder: 'Name', 'aria-label': 'Skill name' }),
        h('label', { class: 'field' }, h('input', { id: 'sk-enabled', type: 'checkbox', checked: true }), 'Turned on')),
      h('div', { class: 'toolbar' },
        h('input', { id: 'sk-desc', type: 'text', class: 'cap-input', style: 'width:420px', maxlength: '300', placeholder: 'Description (optional)', 'aria-label': 'Description' })),
      h('textarea', { id: 'sk-content', class: 'cap-input', rows: '8', maxlength: '1800', style: 'width:100%', placeholder: '# My skill\n\nWhen to use it and what to do...', 'aria-label': 'Skill text (SKILL.md)' }),
      models.length ? h('div', { class: 'toolbar', role: 'group', 'aria-label': 'Attach to models' },
        models.map((m, i) => h('label', { class: 'field' }, h('input', { id: 'sk-m-' + i, type: 'checkbox', value: m }), m))) : h('p', { class: 'table-foot', text: 'Check Open WebUI first to choose models to attach the skill to.' }),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        btn('Import skill', () => {
          const extra = { id: $('#sk-id').value.trim(), name: $('#sk-name').value.trim(), description: $('#sk-desc').value.trim(), content: $('#sk-content').value, enabled: $('#sk-enabled').checked,
            models: models.filter((m, i) => { const el = $('#sk-m-' + i); return el && el.checked; }) };
          if (!extra.id || !extra.name || !extra.content.trim()) { complain('Give the skill an ID, a name and its text.'); return; }
          changeWebui('Skill', 'import-skill', extra, {
            title: 'Import skill ' + extra.name + '?',
            body: h('p', { text: 'The text is saved in Open WebUI and ' + (extra.models.length ? 'attached to ' + extra.models.join(', ') + '. Everyone who can use those models gets it.' : 'not attached to any model yet.') + ' A skill with the same ID is replaced. It is not kept for the next start.' }),
            confirmLabel: 'Import',
          });
        }, 'btn-live')),

      h('h3', { class: 'section-title', text: 'Saved settings' }),
      h('p', { class: 'page-lead', text: 'Changes made here are kept so the lab can put them back after a restart. The lab does this by itself a few minutes after it starts, once per start. Reapply does the same now.' }),
      c.desiredError ? errorBox(c.desiredError, refreshAndPaint) : null,
      c.desired && !c.desired.configured ? h('p', { class: 'empty-small', text: 'Saved settings are not set up on this control panel.' }) : null,
      c.desired && c.desired.configured ? (c.desired.items.length === 0 ? h('p', { class: 'empty-small', text: 'Nothing is saved yet.' }) :
        h('table', { class: 'grid' },
          h('caption', { class: 'sr-only', text: 'Saved Open WebUI settings' }),
          h('thead', null, h('tr', null, [h('th', { scope: 'col', text: 'Setting' }), h('th', { scope: 'col', class: 'sr-only', text: 'Actions' })])),
          h('tbody', null, c.desired.items.map((i) => h('tr', null,
            h('td', { text: describeSaved(i) }),
            h('td', null, i.kind === 'tool-server' ? btn('Edit', () => fillServerForm(i)) : null)))))) : null,
      c.desired && c.desired.lastApplied ? h('p', { class: 'table-foot', text: c.desired.lastApplied.at
        ? 'Last put back on ' + fmt.time(c.desired.lastApplied.at) + (c.desired.lastApplied.failed ? ', and ' + c.desired.lastApplied.failed + ' could not be applied.' : ', all applied.') : 'Not put back since the lab last started.' }) : null,
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        btn('Reapply saved settings', () => changeWebui('Reapply saved settings', 'apply-desired', {}), 'btn-live')),

      h('p', { class: 'table-foot' },
        'Not done from this page, on purpose: Workspace tools, Functions and Pipelines (they run code), the code interpreter engine, importing a whole configuration, showing any secret, creating admin API keys, and Direct Connections. Use Open WebUI\'s own Admin Settings for those',
        c.adminUrl ? [': ', h('a', { href: c.adminUrl, target: '_blank', rel: 'noopener noreferrer', text: 'open Open WebUI admin settings' }), ' (opens in a new tab).'] : '.'));

    Object.entries(keep).forEach(([id, v]) => {
      const el = $('#' + id);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = v; else if (v !== '') el.value = v;
    });
  }

  // ---- Chat API test
  //
  // Sends one fixed prompt to one model through Open WebUI's chat API, from the instance, and shows
  // what came back and how long it took. The models offered are the ones Ollama has installed.
  // Nothing typed here reaches the model, and the reply is not stored.

  function paintChatTest() {
    const wrap = $('#chat-body');
    if (!wrap || state.route !== 'chat-test') return;
    const s = state.chatTest;
    wrap.replaceChildren();
    if (s.error) { wrap.append(errorBox(s.error, loadChatModels)); return; }
    if (!s.models) { wrap.append(h('p', { class: 'empty-small', text: (s.note || 'Asking the instance for its models') + '. This takes a few seconds.' })); return; }
    if (!s.models.length) { wrap.append(h('div', { class: 'empty-state' }, h('h2', { text: 'No models are installed in Ollama.' }), h('p', { text: 'Install a model on the instance first, then check again.' }))); return; }
    put(wrap,
      h('div', { class: 'toolbar' },
        h('label', { for: 'chat-model', class: 'stat-label', text: 'Model' }),
        h('select', { id: 'chat-model', class: 'cap-input', onChange: (e) => { s.model = e.target.value; } },
          s.models.map((m) => h('option', { value: m, selected: m === s.model ? 'selected' : null, text: m }))),
        h('span', { class: 'spacer' }),
        h('button', { type: 'button', id: 'chat-send', class: 'btn btn-live', disabled: s.busy ? true : null, onClick: sendChatTest, text: s.busy ? 'Waiting for the model' : 'Send test' })),
      s.busy ? h('p', { class: 'empty-small', text: (s.note || 'Sending') + '. A model that is not loaded yet can take a minute or two the first time.' }) : null,
      s.last && !s.busy ? chatResult(s.last) : null,
      s.history.length > 1 ? [
        h('h2', { class: 'section-title', text: 'Earlier tests this visit' }),
        h('table', { class: 'grid' },
          h('caption', { class: 'sr-only', text: 'Earlier chat API tests' }),
          h('thead', null, h('tr', null, ['Time', 'Model', 'Result', 'Time taken', 'Tokens in and out'].map((c) => h('th', { scope: 'col', text: c })))),
          h('tbody', null, s.history.slice(1).map((r) => h('tr', null,
            h('td', { text: fmt.clock(r.at) }), h('td', null, h('code', { text: r.model })),
            h('td', { text: r.ok ? 'Replied' : 'Failed' }),
            h('td', { text: r.ok ? (r.latencyMs / 1000).toFixed(1) + ' s' : '' }),
            h('td', { text: r.ok ? (r.inputTokens ?? '?') + ' / ' + (r.outputTokens ?? '?') : '' })))))] : null,
      h('p', { class: 'table-foot', text: 'The test prompt is fixed ("Reply with the single word: ready."). It goes through sign-in, Open WebUI, Ollama and the model, the same path people use.' }));
  }

  function chatResult(r) {
    if (!r.ok) {
      return h('div', { class: 'error-box', role: 'alert' }, icon('error'), h('span', { text: r.error }));
    }
    return h('div', { class: 'facts' },
      factCard('Reply', r.reply || '(empty)', h('span', { class: 'chip tone-green' }, icon('check'), 'The chat API answered')),
      factCard('Time taken', (r.latencyMs / 1000).toFixed(1) + ' s',
        r.latencyMs > 20000 ? h('span', { class: 'chip tone-amber' }, icon('warn'), 'Likely includes loading the model') : null),
      factCard('Tokens in and out', (r.inputTokens ?? '?') + ' / ' + (r.outputTokens ?? '?')));
  }

  async function loadChatModels() {
    const s = state.chatTest;
    if (s.loading) return;
    s.loading = true; s.error = null; s.note = '';
    paintChatTest();
    try {
      const answer = await runAction('status', {}, (status) => { s.note = status === 'Pending' ? 'Waiting for the instance' : 'Checking'; paintChatTest(); });
      if (answer === null) return;
      if (answer.status !== 'Success' || !answer.result || !answer.result.ok) s.error = failedText(answer);
      else if (!answer.result.ollama) s.error = 'Ollama is not answering on the instance, so there are no models to test.';
      else {
        s.models = answer.result.ollama.installed.map((m) => m.name);
        if (!s.models.includes(s.model)) s.model = s.models[0] || '';
      }
    } catch (err) {
      s.error = err.message;
    }
    s.loading = false;
    paintChatTest();
  }

  async function sendChatTest() {
    const s = state.chatTest;
    if (s.busy || !s.model) return;
    s.busy = true; s.note = '';
    paintChatTest();
    const model = s.model;
    let result;
    try {
      const answer = await runAction('chat-test', { model }, (status) => { s.note = status === 'Pending' ? 'Waiting for the instance' : 'Waiting for the model'; paintChatTest(); });
      if (answer === null) return;
      const r = answer.result;
      result = answer.status === 'Success' && r && r.ok ? Object.assign({}, r, { ok: true }) : { ok: false, error: (r && r.error) || failedText(answer) };
    } catch (err) {
      result = { ok: false, error: err.message };
    }
    result.model = model; result.at = API.now();
    s.last = result; s.history.unshift(result);
    s.busy = false;
    state.lastUpdated = API.now();
    paintChatTest();
    paintStatusBar();
  }

  function viewChatTest() {
    if (adminGate('Chat API test')) return;
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Chat API test' }),
      h('p', { class: 'page-lead', text: 'Checks that a model answers through Open WebUI\'s chat API, the way the lab\'s users and tools reach it, and how long it takes.' }),
      h('div', { id: 'chat-body' }));
    paintChatTest();
    if (!state.chatTest.models) loadChatModels();
  }

  // ---- Usage and cost

  const RANGES = [[7, 'Last 7 days'], [30, 'Last 30 days'], [0, 'All recorded']];

  function sessionsInRange(cost) {
    const days = state.usage.rangeDays;
    const nowS = API.now() / 1000;
    const since = days ? nowS - days * 86400 : 0;
    return cost.sessions.filter((x) => (x.stop === null ? nowS : x.stop) >= since);
  }

  function totalsFor(sessions) {
    const by = new Map();
    sessions.forEach((x) => x.users.forEach((u) => {
      const t = by.get(u.id) || { id: u.id, name: u.name, email: u.email, tokens: 0, charge: 0, sessions: 0 };
      t.tokens += u.tokens;
      t.charge += u.charge || 0;
      t.sessions += 1;
      by.set(u.id, t);
    }));
    return [...by.values()].sort((a, b) => b.charge - a.charge || b.tokens - a.tokens);
  }

  const personCell = (u) => h('td', null, u.name || u.email || u.id, u.email && u.name ? h('div', { class: 'sub', text: u.email }) : null);

  function sessionRow(x, priced) {
    const flags = [
      x.running ? h('span', { class: 'chip tone-info' }, icon('info'), 'Running') : null,
      x.provisional && !x.running ? h('span', { class: 'chip tone-amber' }, icon('warn'), 'Provisional') : null,
      x.incomplete ? h('span', { class: 'chip tone-amber' }, icon('warn'), 'Incomplete') : null,
      x.endedBy === 'lost' ? h('span', { class: 'chip tone-grey' }, icon('info'), 'Ended without a stop') : null,
    ].filter(Boolean);
    const people = x.users.length === 0
      ? (x.unallocatedCost ? 'Nobody (unallocated ' + money(x.unallocatedCost) + ')' : 'Nobody')
      : h('details', null, h('summary', { text: x.users.length + (x.users.length === 1 ? ' person' : ' people') }),
        h('ul', { class: 'plain' }, x.users.map((u) => h('li', {
          text: (u.name || u.email || u.id) + ': ' + count(u.tokens) + ' tokens, ' + Math.round(u.share * 100) + '% share' + (priced ? ', ' + money(u.charge) : ''),
        }))));
    return h('tr', null,
      h('td', null, fmt.dateTime(x.start * 1000), flags.length ? h('div', { class: 'sub' }, flags) : null),
      h('td', { text: duration(x.hours * 60) }),
      h('td', { text: priced ? money(x.cost) : 'n/a' }),
      h('td', { text: count(x.tokens) }),
      h('td', { text: x.pricePerMillionTokens === null ? 'n/a' : money(x.pricePerMillionTokens) }),
      h('td', null, people));
  }

  function paintUsage() {
    const wrap = $('#usage-body');
    if (!wrap || state.route !== 'usage') return;
    const s = state.usage;
    wrap.replaceChildren();
    if (s.error) { wrap.append(errorBox(s.error, loadUsage)); return; }
    if (!s.answer) {
      wrap.append(h('p', { class: 'empty-small', text: s.busy ? (s.note || 'Asking the instance') + '. This takes a few seconds.' : 'Not loaded yet.' }));
      return;
    }
    const cost = s.answer.cost;
    const notice = (text) => h('div', { class: 'error-box', role: 'status' }, icon('warn'), h('span', { text }));
    if (!cost.priced) wrap.append(notice('The lab has no hourly price, so tokens are counted but nothing is charged. Set instance_hourly_cost_usd in terraform.tfvars.'));
    if (cost.truncated) wrap.append(notice('The instance sent only the most recent hours, so older sessions may show less than they used.'));
    const sessions = sessionsInRange(cost);
    if (!sessions.length) {
      wrap.append(h('p', { class: 'empty-small', text: 'No sessions in this period.' }));
      return;
    }
    const totals = totalsFor(sessions);
    const spend = sessions.reduce((sum, x) => sum + (x.cost || 0), 0);
    const tokens = sessions.reduce((sum, x) => sum + x.tokens, 0);
    wrap.append(
      h('div', { class: 'facts' },
        factCard('Running cost', cost.priced ? money(spend) : 'n/a'),
        factCard('Tokens', count(tokens)),
        factCard('Price per 1M tokens', cost.priced && tokens ? money(spend / tokens * 1e6) : 'n/a'),
        factCard('Sessions', count(sessions.length))),
      h('h2', { class: 'section-title', text: 'Charge per person' }),
      totals.length === 0 ? h('p', { class: 'empty-small', text: 'Nobody used tokens in this period.' }) :
        h('table', { class: 'grid' },
          h('caption', { class: 'sr-only', text: 'Tokens and charge per person for the chosen period' }),
          h('thead', null, h('tr', null, ['Person', 'Sessions', 'Tokens', 'Charge'].map((c) => h('th', { scope: 'col', text: c })))),
          h('tbody', null, totals.map((u) => h('tr', null, personCell(u), h('td', { text: count(u.sessions) }), h('td', { text: count(u.tokens) }), h('td', { text: cost.priced ? money(u.charge) : 'n/a' }))))),
      h('h2', { class: 'section-title', text: 'Sessions' }),
      h('table', { class: 'grid' },
        h('caption', { class: 'sr-only', text: 'Lab sessions, newest first' }),
        h('thead', null, h('tr', null, ['Started', 'Length', 'Cost', 'Tokens', 'Per 1M tokens', 'People'].map((c) => h('th', { scope: 'col', text: c })))),
        h('tbody', null, sessions.map((x) => sessionRow(x, cost.priced)))),
      h('p', { class: 'table-foot', text: 'Each hour of a session is shared by the people who used tokens in it; start-up and idle-tail hours go to the nearest users. Charges are final when the session stops.' }));
  }

  async function loadUsage() {
    const s = state.usage;
    if (s.busy) return;
    s.busy = true; s.error = null; s.note = '';
    paintUsage();
    try {
      const answer = await runAction('usage', {}, (status) => { s.note = status === 'Pending' ? 'Waiting for the instance' : 'Collecting usage'; paintUsage(); });
      if (answer === null) return;
      if (answer.status !== 'Success' || !answer.result || !answer.result.ok || !answer.cost) s.error = failedText(answer);
      else { s.answer = answer; state.lastUpdated = API.now(); }
    } catch (err) {
      s.error = err.message;
    }
    s.busy = false;
    paintUsage();
    paintStatusBar();
  }

  function viewUsage() {
    if (adminGate('Usage and cost')) return;
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Usage and cost' }),
      h('p', { class: 'page-lead', text: 'What each run of the lab cost and each person\'s share of it, from the hours they used tokens. Administrators only.' }),
      h('div', { class: 'toolbar' },
        h('label', { class: 'field' }, h('span', { text: 'Period' }),
          h('select', { onChange: (e) => { state.usage.rangeDays = Number(e.target.value); paintUsage(); } },
            RANGES.map(([value, text]) => h('option', { value, text, selected: state.usage.rangeDays === value })))),
        h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: loadUsage }, icon('refresh'), 'Refresh')),
      h('div', { id: 'usage-body' }));
    if (state.usage.answer) paintUsage(); else loadUsage();
  }

  // ---- views: Spend caps -----------------------------------------------------------------
  //
  // Administrators set the caps; user managers see everyone's; everyone else sees their own.
  // The scheduled job enforces the caps (docs/monitoring-spec.md, "Spend caps"); this page only
  // shows its figures and writes the caps.

  const THRESHOLDS = [50, 80, 98, 100];

  // Append children, skipping empty ones (the DOM's own append would write "null").
  function put(parent, ...kids) {
    kids.flat(Infinity).forEach((kid) => { if (kid !== null && kid !== undefined && kid !== false) parent.append(kid); });
  }

  function pctTone(pct) {
    if (pct === null || pct === undefined) return 'grey';
    return pct >= 98 ? 'red' : pct >= 80 ? 'amber' : 'green';
  }

  function meter(pct, label) {
    return h('div', { class: 'meter-cell' },
      h('progress', { max: 100, value: Math.min(100, pct || 0), 'aria-label': label }),
      h('span', { class: 'meter-text', text: pct === null || pct === undefined ? 'No cap' : pct + '%' }));
  }

  function emailChips(sent) {
    return h('span', { class: 'chips' }, THRESHOLDS.map((t) => (sent.includes(t)
      ? h('span', { class: 'chip tone-info', title: 'The ' + t + '% email was sent' }, icon('check'), t + '%')
      : h('span', { class: 'chip tone-grey', title: 'The ' + t + '% email has not been sent' }, t + '%'))));
  }

  const ADDRESS = {
    confirmed: ['green', 'check', 'Confirmed'],
    pending: ['amber', 'warn', 'Waiting for the link to be clicked'],
    unconfirmed: ['grey', 'info', 'Not confirmed yet'],
    unverified: ['grey', 'info', 'Email not verified by the sign-in provider'],
    expired: ['amber', 'warn', 'Link expired'],
    unsubscribed: ['grey', 'info', 'Unsubscribed'],
    failed: ['red', 'error', 'Could not be confirmed'],
  };

  function addressChip(status) {
    const [tone, ic, text] = ADDRESS[status] || ADDRESS.unconfirmed;
    return h('span', { class: 'chip tone-' + tone }, icon(ic), text);
  }

  const blockedChip = (b) => h('span', { class: 'chip tone-red', title: b.reason }, icon('error'), 'Blocked: spend cap');

  const capText = (v) => (v === null || v === undefined || v === 0 ? 'No cap' : money(v));

  function paintSpend() {
    const wrap = $('#spend-body');
    if (!wrap || state.route !== 'spend') return;
    const s = state.spend;
    wrap.replaceChildren();
    if (s.error) { wrap.append(errorBox(s.error, loadSpend)); return; }
    if (!s.data) { wrap.append(h('p', { class: 'empty-small', text: 'Loading spend caps' })); return; }
    if (s.data.users) paintSpendOverview(wrap, s.data); else paintMySpend(wrap, s.data);
  }

  function paintMySpend(wrap, d) {
    put(wrap, 
      d.blocked ? h('div', { class: 'error-box', role: 'status' }, icon('error'), h('span', { text: d.blocked.reason })) : null,
      h('div', { class: 'facts' },
        factCard('Your monthly cap', capText(d.effectiveCapUsd)),
        factCard('Spent so far in ' + d.period, money(d.spendUsd)),
        h('div', { class: 'fact' }, h('div', { class: 'stat-label', text: 'Used' }), meter(d.percent, 'Share of your cap used'))),
      h('p', { class: 'page-lead', text: 'Cap emails sent this month: ' }, emailChips(d.emailsSent)),
      h('p', { class: 'table-foot', text: 'Your cap resets on ' + d.resetsOn + '. Your email address: ' }, addressChip(d.emailStatus)));
  }

  function numberInput(id, label, value, placeholder, disabled) {
    return h('input', {
      type: 'number', id, min: '0', max: '1000000', step: '0.01', class: 'cap-input', 'aria-label': label,
      value: value === null || value === undefined ? '' : String(value), placeholder: placeholder || '', disabled: disabled || null,
      onInput: () => { state.spend.dirty = true; const b = $('#spend-save'); if (b) b.disabled = false; },
    });
  }

  function paintSpendOverview(wrap, d) {
    const edit = d.canEdit;
    const lab = d.lab;
    put(wrap, 
      lab.override ? h('div', { class: 'info-box', role: 'status' }, icon('info'), h('span', { text: lab.override.by + ' let the lab run once past its monthly cap' + (lab.override.used ? '. It is running on that permission, which ends when the lab next stops.' : '. The next start is allowed, and the permission ends when the lab next stops.') }))
        : lab.stopped ? h('div', { class: 'error-box', role: 'status' }, icon('error'), h('span', { text: 'The lab reached its monthly budget and was stopped. It cannot be started until the cap is raised or the month resets on ' + d.resetsOn + '.' }),
          edit ? h('button', { type: 'button', class: 'btn btn-secondary small', onClick: allowLabOnce, text: 'Allow one run past the cap' }) : null) : null,
      h('div', { class: 'facts' },
        h('div', { class: 'fact' },
          h('div', { class: 'stat-label', text: 'Lab total cap, ' + d.period }),
          edit ? numberInput('lab-cap', 'Lab total monthly cap in dollars', lab.capUsd, 'No cap') : h('div', { class: 'stat-value', text: capText(lab.capUsd) }),
          h('div', { class: 'sub', text: 'Spent ' + money(lab.spendUsd) + (lab.projectedUsd ? ', on track for ' + money(lab.projectedUsd) : '') }),
          meter(lab.percent, 'Share of the lab cap used'), emailChips(lab.emailsSent)),
        h('div', { class: 'fact' },
          h('div', { class: 'stat-label', text: 'Default cap per person' }),
          edit ? numberInput('default-cap', 'Default monthly cap per person in dollars', d.defaultUserCapUsd, 'No cap') : h('div', { class: 'stat-value', text: capText(d.defaultUserCapUsd) }),
          h('div', { class: 'sub', text: 'Everyone\'s caps add up to ' + money(d.userCapsTotalUsd) + (lab.capUsd ? (d.userCapsTotalUsd > lab.capUsd ? ', more than the lab cap, so the lab cap stops people first' : ', within the lab cap') : '') }))),
      h('h2', { class: 'section-title', text: 'People' }),
      h('table', { class: 'grid' },
        h('caption', { class: 'sr-only', text: 'Monthly spend caps, spend and emails per person' }),
        h('thead', null, h('tr', null, ['Person', 'Cap', 'Spent', 'Used', 'Emails sent', 'Email address'].map((c) => h('th', { scope: 'col', text: c })))),
        h('tbody', null, d.users.map((u) => h('tr', null,
          h('td', null, u.name, h('div', { class: 'sub', text: u.email }), u.blocked ? blockedChip(u.blocked) : null,
            u.blocked ? h('div', { class: 'sub', text: u.blocked.reason }) : null),
          h('td', null, edit
            ? [numberInput('cap-' + u.id, 'Monthly cap for ' + u.name + ' in dollars', u.capUsd, d.defaultUserCapUsd ? 'Default (' + money(d.defaultUserCapUsd) + ')' : 'Default (no cap)'),
              u.roles.includes('admin') ? h('div', { class: 'sub', text: 'Administrators are never blocked' }) : null]
            : capText(u.effectiveCapUsd)),
          h('td', { text: money(u.spendUsd) }),
          h('td', null, meter(u.percent, 'Share of ' + u.name + '\'s cap used')),
          h('td', null, emailChips(u.emailsSent)),
          h('td', null, addressChip(u.emailStatus), edit && u.emailStatus !== 'confirmed'
            ? h('button', { type: 'button', class: 'btn btn-secondary small', onClick: () => resendConfirmation(u), text: 'Send again' }) : null))))),
      edit ? h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', id: 'spend-save', class: 'btn btn-live', disabled: true, onClick: saveSpend, text: 'Save caps' })) : null,
      h('p', { class: 'table-foot', text: 'Caps are dollars per calendar month (UTC) and reset on ' + d.resetsOn + '. Empty means the default; 0 means no cap. At 100% a person cannot sign in until the month resets or their cap is raised.' }));
  }

  async function loadSpend() {
    const session = state.session;
    state.spend.error = null;
    try {
      const data = isManager() ? await API.getSpendCaps() : await API.getMySpend();
      if (session !== state.session) return;
      state.spend.data = data;
      state.spend.dirty = false;
      state.lastUpdated = API.now();
    } catch (err) {
      if (session !== state.session) return;
      state.spend.error = 'Could not load spend caps. ' + err.message;
    }
    paintSpend();
    paintStatusBar();
  }

  function readCap(id) {
    const el = document.getElementById(id);
    if (!el) return 'missing';
    return el.value.trim() === '' ? null : Number(el.value);
  }

  async function saveSpend() {
    const d = state.spend.data;
    const body = { labCapUsd: readCap('lab-cap'), defaultUserCapUsd: readCap('default-cap'), userCaps: [] };
    d.users.forEach((u) => {
      const value = readCap('cap-' + u.id);
      if (value !== 'missing' && value !== u.capUsd) body.userCaps.push({ userId: u.id, capUsd: value });
    });
    if ([body.labCapUsd, body.defaultUserCapUsd].concat(body.userCaps.map((c) => c.capUsd)).some((v) => v !== null && (!Number.isFinite(v) || v < 0))) {
      toast('A cap must be a dollar amount of 0 or more.', 'error');
      return;
    }
    if (body.labCapUsd === d.lab.capUsd) delete body.labCapUsd;
    if (body.defaultUserCapUsd === d.defaultUserCapUsd) delete body.defaultUserCapUsd;
    if (!('labCapUsd' in body) && !('defaultUserCapUsd' in body) && !body.userCaps.length) { toast('Nothing has changed.'); return; }
    try {
      state.spend.data = await API.saveSpendCaps(body);
      state.spend.dirty = false;
      toast('Spend caps saved. People under a raised cap are let back in within a minute.');
      announce('Spend caps saved');
    } catch (err) {
      toast('Could not save. ' + err.message, 'error');
    }
    paintSpend();
  }

  async function allowLabOnce() {
    try {
      state.spend.data = await API.allowLabStartOnce();
      toast('Allowed. The lab can be started once past its cap; the cap itself is unchanged.');
      announce('One run past the lab cap allowed');
    } catch (err) {
      toast('Could not allow it. ' + err.message, 'error');
    }
    paintSpend();
  }

  async function resendConfirmation(u) {
    try {
      await API.confirmSpendEmail(u.id);
      toast('Asked for ' + u.email + ' to be confirmed. They get a link by email, once their sign-in email is verified.');
    } catch (err) {
      toast('Could not ask. ' + err.message, 'error');
    }
  }

  function viewSpend() {
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Spend caps' }),
      h('p', { class: 'page-lead', text: isAdmin() ? 'Set the lab\'s monthly budget and each person\'s monthly cap, and see who is close.'
        : isManager() ? 'The lab\'s monthly budget and everyone\'s monthly cap and spend. Only an administrator can change them.' : 'Your monthly spend cap and how much of it you have used.' }),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: loadSpend }, icon('refresh'), 'Refresh')),
      h('div', { id: 'spend-body' }));
    paintSpend();
    loadSpend();
  }

  // ---- views: Timer policy ---------------------------------------------------------------
  //
  // The lab's own auto-stop setting (Terraform) is the ceiling. This page sets shorter idle and
  // session-length limits inside it. 0 means "use the lab's value".

  const minutesText = (m) => (!m ? 'Off' : m % 60 === 0 ? (m / 60) + (m === 60 ? ' hour' : ' hours') : m + ' minutes');

  function paintTimer() {
    const wrap = $('#timer-body');
    if (!wrap || state.route !== 'timer-policy') return;
    const s = state.timer;
    wrap.replaceChildren();
    if (s.error) { wrap.append(errorBox(s.error, loadTimer)); return; }
    if (!s.data) { wrap.append(h('p', { class: 'empty-small', text: 'Loading the timer policy' })); return; }
    const d = s.data;
    if (!d.enabled) {
      wrap.append(h('div', { class: 'empty-state' }, h('h2', { text: 'Auto-stop is switched off for this lab.' }),
        h('p', { text: 'The lab\'s Terraform settings turn it on. Until they do, there is no timer to shorten.' })));
      return;
    }
    const field = (id, label, value, low, ceiling) => h('div', { class: 'fact' },
      h('label', { class: 'stat-label', for: id, text: label }),
      h('input', {
        type: 'number', id, class: 'cap-input', min: '0', max: String(ceiling || d.maximum), step: '1', value: value ? String(value) : '',
        placeholder: '0', onInput: () => { state.timer.dirty = true; const b = $('#timer-save'); if (b) b.disabled = false; },
      }),
      h('div', { class: 'sub', text: 'Between ' + low + ' and ' + (ceiling || d.maximum) + ' minutes. 0 uses the lab\'s value.' }));
    put(wrap,
      h('div', { class: 'facts' },
        factCard('Idle time in force', minutesText(d.effective.idleMinutes)),
        factCard('Session length in force', minutesText(d.effective.maxUptimeMinutes)),
        factCard('Resets per run in force', d.effective.maxResets ? String(d.effective.maxResets) : 'No limit')),
      h('div', { class: 'facts' },
        factCard('Lab limit for idle time', minutesText(d.ceiling.idleMinutes)),
        factCard('Lab limit for session length', minutesText(d.ceiling.maxUptimeMinutes)),
        factCard('Lab limit for resets per run', d.ceiling.maxResets ? String(d.ceiling.maxResets) : 'No limit')),
      d.ceiling.absoluteMaxMinutes ? h('p', { class: 'table-foot', text: 'The lab also stops ' + minutesText(d.ceiling.absoluteMaxMinutes) + ' after it starts, whatever is set here. A reset cannot extend that.' }) : null,
      h('h2', { class: 'section-title', text: 'Shorten the limits' }),
      h('div', { class: 'facts' },
        field('timer-idle', 'Stop after this many idle minutes', d.policy.idleMinutes, d.minimums.idleMinutes, d.ceiling.idleMinutes),
        field('timer-max', 'Stop this many minutes after the session starts', d.policy.maxUptimeMinutes, d.minimums.maxUptimeMinutes, d.ceiling.maxUptimeMinutes),
        h('div', { class: 'fact' },
          h('label', { class: 'stat-label', for: 'timer-resets', text: 'Timer resets allowed per run' }),
          h('input', {
            type: 'number', id: 'timer-resets', class: 'cap-input', min: '0', max: String(d.ceiling.maxResets || d.maximumResets), step: '1', value: d.policy.maxResets ? String(d.policy.maxResets) : '',
            placeholder: '0', onInput: () => { state.timer.dirty = true; const b = $('#timer-save'); if (b) b.disabled = false; },
          }),
          h('div', { class: 'sub', text: 'Between 1 and ' + (d.ceiling.maxResets || d.maximumResets) + '. 0 uses the lab\'s value.' }))),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', id: 'timer-save', class: 'btn btn-live', disabled: true, onClick: saveTimer, text: 'Save timer policy' })),
      h('p', { class: 'table-foot', text: 'A change reaches the running instance within a minute. The lab\'s own limit still applies as a backstop, so a limit can be shortened here but never made longer or switched off.' }));
  }

  async function loadTimer() {
    const session = state.session;
    state.timer.error = null;
    try {
      const data = await API.getTimerPolicy();
      if (session !== state.session) return;
      state.timer.data = data;
      state.timer.dirty = false;
      state.lastUpdated = API.now();
    } catch (err) {
      if (session !== state.session) return;
      state.timer.error = 'Could not load the timer policy. ' + err.message;
    }
    paintTimer();
    paintStatusBar();
  }

  async function saveTimer() {
    const read = (id) => { const el = document.getElementById(id); return el && el.value.trim() !== '' ? Number(el.value) : 0; };
    const body = { idleMinutes: read('timer-idle'), maxUptimeMinutes: read('timer-max'), maxResets: read('timer-resets') };
    if (![body.idleMinutes, body.maxUptimeMinutes, body.maxResets].every((v) => Number.isInteger(v) && v >= 0)) {
      toast('Enter whole minutes, or 0 to use the lab\'s value.', 'error');
      return;
    }
    try {
      state.timer.data = await API.saveTimerPolicy(body);
      state.timer.dirty = false;
      toast('Timer policy saved.');
      announce('Timer policy saved');
    } catch (err) {
      toast('Could not save. ' + err.message, 'error');
    }
    paintTimer();
  }

  function viewTimerPolicy() {
    if (adminGate('Timer policy')) return;
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Timer policy' }),
      h('p', { class: 'page-lead', text: 'How long the lab may sit idle and how long a session may last before the instance stops itself.' }),
      h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
        h('button', { type: 'button', class: 'btn btn-secondary', onClick: loadTimer }, icon('refresh'), 'Refresh')),
      h('div', { id: 'timer-body' }));
    paintTimer();
    loadTimer();
  }

  // ---- routing and sign-in --------------------------------------------------------------

  const TITLES = { instances: 'Instances', logins: 'Logins', logs: 'Logs', 'user-management': 'User management', 'open-webui': 'Open WebUI', usage: 'Usage and cost', spend: 'Spend caps', 'timer-policy': 'Timer policy', 'chat-test': 'Chat API test' };

  function route(moveFocus) {
    const wanted = location.hash.replace(/^#\/?/, '');
    state.route = TITLES[wanted] ? wanted : 'instances';
    clearTimeout(pollTimer);
    document.title = 'AI Cloud Lab · ' + TITLES[state.route];
    renderNav();
    if (state.route === 'instances') viewInstances();
    else if (state.route === 'logins') viewLogins();
    else if (state.route === 'logs') viewLogs();
    else if (state.route === 'open-webui') viewOpenWebui();
    else if (state.route === 'usage') viewUsage();
    else if (state.route === 'spend') viewSpend();
    else if (state.route === 'timer-policy') viewTimerPolicy();
    else if (state.route === 'chat-test') viewChatTest();
    else viewUserManagement();
    if (moveFocus) { const title = $('#page-title'); if (title) title.focus(); }
  }

  function openSession(session) {
    document.body.classList.remove('gate');
    clearTimeout(pollTimer);
    state.session = session;
    state.instances = null; state.instError = null;
    state.logins = null; state.logs = null;
    state.expanded = new Set();
    state.phases = new Map();
    state.um = freshUM();
    state.openWebui = { busy: false, error: null, answer: null, note: '' };
    state.owx = { tools: { busy: false, error: null, answer: null, note: '' }, settings: { busy: false, error: null, answer: null, note: '' }, exportBusy: false, exportError: null, exportDone: null, ctl: { loading: false, busy: null, message: null, tokens: null, tokensError: null, desired: null, desiredError: null } };
    state.usage = { busy: false, error: null, answer: null, note: '', rangeDays: 30 };
    state.spend = { data: null, error: null, dirty: false };
    state.timer = { data: null, error: null, dirty: false };
    state.chatTest = { models: null, model: '', loading: false, busy: false, error: null, note: '', last: null, history: [] };
    renderUserChip();
    announce('Signed in as ' + session.name);
    route(false);
  }

  // Shown instead of the page when it cannot sign in or has no settings.
  function showFatal(message, canRetry) {
    clearTimeout(pollTimer);
    if (document.body.classList.contains('gate')) {
      // Not signed in: no app shell, just the message.
      const box = $('#gate');
      box.replaceChildren(h('p', { role: 'alert', text: message }),
        canRetry ? h('button', { type: 'button', class: 'btn btn-secondary', onClick: () => location.reload(), text: 'Try again' }) : null);
      return;
    }
    $('#nav').replaceChildren();
    $('#main').replaceChildren(
      h('h1', { id: 'page-title', class: 'page-title', tabindex: '-1', text: 'Control panel' }),
      h('div', { class: 'error-box', role: 'alert' }, icon('error'), h('span', { text: message })),
      canRetry ? h('p', null, h('button', { type: 'button', class: 'btn btn-secondary', onClick: () => location.reload(), text: 'Try again' })) : null);
  }

  function paintApiStatus(ok) {
    const box = $('#api-status');
    box.className = ok ? 'status-ok' : 'status-bad';
    $('#api-status-text').textContent = ok ? 'Control API connected' : 'Control API unreachable';
  }

  async function boot() {
    $('#global-search').addEventListener('input', (e) => setSearch(e.target.value));
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#global-search').focus(); }
    });
    window.addEventListener('hashchange', () => route(true));
    window.addEventListener('api-status', (e) => paintApiStatus(e.detail.ok));
    $('#region').textContent = API.region() || 'unknown';
    const problem = API.configProblem();
    if (problem) { showFatal(problem, false); return; }
    try {
      openSession(await API.start());
    } catch (err) {
      showFatal(err.message, true);
    }
  }

  boot();
})();
