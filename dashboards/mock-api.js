/*
 * Mock Control API for the dashboard mock.
 *
 * Nothing here talks to AWS. It stands in for the real Control API so the pages can be
 * built and judged first. The rules the real API must enforce live here, not in the
 * pages, so the pages can be wired to the real API later by replacing this file:
 *
 *   - only members of the Cognito group "operators" can see or start instances
 *   - an operator sees and starts only instances with an active entitlement
 *   - only members of the Cognito group "user_mgrs" can use the User management calls
 *   - every call is answered for the signed-in user only (own logins, own logs)
 *   - Save on the User management page changes entitlements only, never the
 *     instance itself or any AWS permission
 *
 * Group names and the odd/even demo users match cognito.tf and terraform.tfvars.
 */
(function () {
  'use strict';

  const MIN = 60 * 1000;
  const HOUR = 60 * MIN;
  const DAY = 24 * HOUR;
  const REGION = 'us-east-1';
  const GROUP_OPERATORS = 'operators';
  const GROUP_USER_MGRS = 'user_mgrs';

  let skew = 0; // virtual clock offset, moved by the demo "+10 min" button
  const now = () => Date.now() + skew;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const latency = () => sleep(120 + Math.random() * 140);
  const clone = (value) => JSON.parse(JSON.stringify(value));

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  // Each instance carries its own auto-stop rule so every display state can be seen.
  // In the lab today the rule is one SSM parameter per project (see auto_stop.tf).
  const INSTANCE_DEFS = [
    {
      key: 'demo',
      id: 'i-0a3f9c21d84e5b607',
      name: 'ai-lab-demo',
      type: 't3.xlarge',
      url: 'https://aiwebdemo.click',
      rule: { enabled: true, idle: 0, max: 20 },
      initial: 'stopped',
    },
    {
      key: 'staging',
      id: 'i-07be41c9a2d6f8153',
      name: 'ai-lab-staging',
      type: 't3.xlarge',
      url: 'https://aiwebdemo.click',
      rule: { enabled: true, idle: 60, max: 90 },
      initial: 'running',
    },
    {
      key: 'gpu',
      id: 'i-0d52e8a7b1c394f60',
      name: 'ai-lab-gpu',
      type: 'g6.xlarge',
      url: 'https://aiwebdemo.click',
      rule: { enabled: true, idle: 30, max: 0 },
      initial: 'stopped',
      failFirstStart: true,
    },
  ];

  // Who has been granted what at the start of the demo.
  const SEED_GRANTS = {
    u1: ['demo', 'staging', 'gpu'],
    u3: ['demo', 'staging'],
    u5: ['demo'],
    u7: ['demo'],
    u9: ['demo'],
  };

  const BROWSERS = ['Chrome 129 on Windows 11', 'Edge 129 on Windows 11', 'Firefox 131 on macOS'];

  let users;
  let instances;
  let entitlements;
  let history;
  let logs;
  let logins;
  let scenario;
  let logSeq;

  function addLog(t, source, severity, event, extra) {
    logs.push(Object.assign({ id: 'l' + ++logSeq, t, source, severity, event }, extra || {}));
  }

  function init() {
    const t = now();
    scenario = 'normal';
    logSeq = 0;
    logs = [];
    history = [];
    entitlements = [];
    logins = {};

    users = [{ id: 'u0', name: 'Admin User', email: 'admin@example.local', groups: [GROUP_USER_MGRS] }];
    for (let n = 1; n <= 10; n++) {
      users.push({
        id: 'u' + n,
        name: 'Demo User ' + n,
        email: 'demo' + n + '@example.local',
        // Odd demo users are operators; even demo users have no role (see terraform.tfvars).
        groups: n % 2 === 1 ? [GROUP_OPERATORS] : [],
      });
    }

    instances = INSTANCE_DEFS.map((d) => ({
      key: d.key,
      id: d.id,
      name: d.name,
      type: d.type,
      url: d.url,
      rule: d.rule,
      state: 'stopped',
      launchedAt: null,
      plan: null,
      setupDoneAt: null,
      failMsg: null,
      stopAt: null,
      warned: false,
      readyLogged: true,
      failsLeft: d.failFirstStart ? 1 : 0,
    }));

    const staging = instances.find((i) => i.key === 'staging');
    staging.state = 'running';
    staging.launchedAt = t - 35 * MIN;
    staging.plan = { runningAt: t - 35 * MIN + 3000, ec2At: t - 35 * MIN + 6500, httpAt: t - 35 * MIN + 9500, failAt: 0 };
    staging.setupDoneAt = t - 30 * MIN;

    const byKey = (key) => instances.find((i) => i.key === key);
    let grantNo = 0;
    Object.keys(SEED_GRANTS).forEach((userId) => {
      SEED_GRANTS[userId].forEach((key) => {
        const at = t - 2 * DAY - grantNo++ * 7 * MIN;
        entitlements.push({ userId, instanceId: byKey(key).id, status: 'applied', by: 'u0', at });
        history.push({ t: at, adminId: 'u0', userId, instanceId: byKey(key).id, action: 'Granted', result: 'Applied' });
      });
    });
    history.sort((a, b) => b.t - a.t);

    users.forEach((u) => {
      const n = Number(u.id.slice(1));
      logins[u.id] = [];
      for (let k = 0; k < 7; k++) {
        const failed = (n + k) % 5 === 0;
        const at = t - (k + 1) * (31 * HOUR + n * 37 * MIN);
        logins[u.id].push({
          id: 'g-' + u.id + '-' + k,
          t: at,
          result: failed ? 'failed' : 'success',
          detail: failed ? 'Incorrect password' : '',
          ip: '203.0.113.' + (10 + n * 7 + k),
          browser: BROWSERS[(n + k) % 3],
          current: false,
        });
        if (!failed && k < 3) addLog(at, 'Control panel', 'info', 'Signed in', { userId: u.id });
        if (failed) addLog(at, 'Control panel', 'warning', 'Sign-in failed: incorrect password', { userId: u.id });
      }
    });

    const demo = byKey('demo');
    const gpu = byKey('gpu');
    const stg = byKey('staging');
    const d0 = t - 26 * HOUR;
    addLog(d0, 'Control panel', 'info', 'Start requested for ai-lab-demo', { userId: 'u3', instanceId: demo.id });
    addLog(d0 + 1000, 'EC2', 'info', 'ai-lab-demo: state stopped to pending', { instanceId: demo.id });
    addLog(d0 + 4000, 'EC2', 'info', 'ai-lab-demo: state pending to running', { instanceId: demo.id });
    addLog(d0 + 7000, 'EC2', 'info', 'ai-lab-demo: status checks passed (2 of 2)', { instanceId: demo.id });
    addLog(d0 + 10000, 'EC2', 'info', 'ai-lab-demo: health check passed (HTTP 200)', { instanceId: demo.id });
    addLog(d0 + 10 * MIN, 'EC2', 'warning', 'ai-lab-demo: warning email sent, stops in about 10 minutes', { instanceId: demo.id });
    addLog(d0 + 20 * MIN, 'EC2', 'info', 'ai-lab-demo: hard limit of 20 minutes reached, powering off', { instanceId: demo.id });
    addLog(d0 + 20 * MIN + 8000, 'EC2', 'info', 'ai-lab-demo: state running to stopped', { instanceId: demo.id });
    const g0 = t - 3 * HOUR;
    addLog(g0, 'Control panel', 'info', 'Start requested for ai-lab-gpu', { userId: 'u1', instanceId: gpu.id });
    addLog(g0 + 2000, 'EC2', 'error', 'ai-lab-gpu: start failed, InsufficientInstanceCapacity', { instanceId: gpu.id });
    addLog(g0 + 2500, 'Control panel', 'error', 'Start failed for ai-lab-gpu: not enough capacity', { userId: 'u1', instanceId: gpu.id });
    const s0 = stg.launchedAt;
    addLog(s0, 'Control panel', 'info', 'Start requested for ai-lab-staging', { userId: 'u1', instanceId: stg.id });
    addLog(s0 + 1000, 'EC2', 'info', 'ai-lab-staging: state stopped to pending', { instanceId: stg.id });
    addLog(s0 + 3000, 'EC2', 'info', 'ai-lab-staging: state pending to running', { instanceId: stg.id });
    addLog(s0 + 6500, 'EC2', 'info', 'ai-lab-staging: status checks passed (2 of 2)', { instanceId: stg.id });
    addLog(s0 + 9500, 'EC2', 'info', 'ai-lab-staging: health check passed (HTTP 200)', { instanceId: stg.id });
    addLog(s0 + 5 * MIN, 'EC2', 'info', 'ai-lab-staging: idle monitor reporting, 0 active users', { instanceId: stg.id });
  }

  // ---- instance state machine ------------------------------------------------------

  function warnMinutes(rule) {
    return rule.max >= 60 ? 30 : 10;
  }

  function tick() {
    const t = now();
    instances.forEach((i) => {
      if (i.state === 'pending') {
        if (i.plan.failAt && t >= i.plan.failAt) {
          i.state = 'failed';
          i.failMsg = 'Not enough capacity right now. Try again in a few minutes.';
          addLog(i.plan.failAt, 'EC2', 'error', i.name + ': start failed, InsufficientInstanceCapacity', { instanceId: i.id });
          return;
        }
        if (t >= i.plan.runningAt) {
          i.state = 'running';
          addLog(i.plan.runningAt, 'EC2', 'info', i.name + ': state pending to running', { instanceId: i.id });
        }
      }
      if (i.state === 'running') {
        if (!i.readyLogged && t >= i.plan.httpAt) {
          i.readyLogged = true;
          addLog(i.plan.ec2At, 'EC2', 'info', i.name + ': status checks passed (2 of 2)', { instanceId: i.id });
          addLog(i.plan.httpAt, 'EC2', 'info', i.name + ': health check passed (HTTP 200)', { instanceId: i.id });
        }
        if (scenario !== 'off') {
          if (i.rule.max > 0 && !i.warned && t >= i.launchedAt + (i.rule.max - warnMinutes(i.rule)) * MIN) {
            i.warned = true;
            addLog(i.launchedAt + (i.rule.max - warnMinutes(i.rule)) * MIN, 'EC2', 'warning',
              i.name + ': warning email sent, stops in about ' + warnMinutes(i.rule) + ' minutes', { instanceId: i.id });
          }
          const hardHit = i.rule.max > 0 && t >= i.launchedAt + i.rule.max * MIN;
          const idleHit = i.rule.idle > 0 && scenario !== 'unknown' && i.setupDoneAt != null && t >= i.setupDoneAt + i.rule.idle * MIN;
          if (hardHit || idleHit) {
            i.state = 'stopping';
            i.stopAt = t + 6000;
            addLog(t, 'EC2', 'info', hardHit
              ? i.name + ': hard limit of ' + i.rule.max + ' minutes reached, powering off'
              : i.name + ': idle for ' + i.rule.idle + ' minutes, powering off', { instanceId: i.id });
          }
        }
      }
      if (i.state === 'stopping' && t >= i.stopAt) {
        i.state = 'stopped';
        i.launchedAt = null;
        i.plan = null;
        i.setupDoneAt = null;
        i.warned = false;
        addLog(i.stopAt, 'EC2', 'info', i.name + ': state running to stopped', { instanceId: i.id });
      }
    });
  }

  function phaseOf(i, t) {
    switch (i.state) {
      case 'stopped': return 'stopped';
      case 'failed': return 'failed';
      case 'stopping': return 'stopping';
      case 'pending': return 'pending';
      default: return t >= i.plan.ec2At && t >= i.plan.httpAt ? 'ready' : 'initializing';
    }
  }

  // What the real API would assemble from the auto-stop SSM parameter, EC2 LaunchTime
  // and the AILab CloudWatch metrics.
  function autoStopInfo(i, t) {
    const configured = i.rule.enabled && (i.rule.idle > 0 || i.rule.max > 0);
    const setupDone = i.setupDoneAt != null && t >= i.setupDoneAt;
    return {
      enabled: scenario === 'off' ? false : configured,
      idleLimitMinutes: i.rule.idle,
      maxUptimeMinutes: i.rule.max,
      launchedAt: i.launchedAt,
      setupDone,
      idleMinutes: setupDone ? Math.floor((t - i.setupDoneAt) / MIN) : null,
      activeUsers: scenario === 'unknown' ? -1 : 0,
      heartbeatAgeMinutes: scenario === 'silent' ? 12 : 0.5,
    };
  }

  function view(i, t) {
    const phase = phaseOf(i, t);
    const running = phase === 'ready' || phase === 'initializing' || phase === 'stopping';
    return {
      id: i.id,
      name: i.name,
      type: i.type,
      region: REGION,
      url: i.url,
      phase,
      message: i.failMsg,
      launchedAt: i.launchedAt,
      checks: { ec2: !!i.plan && t >= i.plan.ec2At, http: !!i.plan && t >= i.plan.httpAt },
      rule: { enabled: i.rule.enabled, idleMinutes: i.rule.idle, maxUptimeMinutes: i.rule.max },
      autoStop: running ? autoStopInfo(i, t) : null,
    };
  }

  // ---- rules ------------------------------------------------------------------------

  function requireUser(session) {
    const user = session && users.find((u) => u.id === session.userId);
    if (!user) throw new ApiError(401, 'Please sign in again.');
    return user;
  }

  function requireGroup(user, group) {
    if (!user.groups.includes(group)) throw new ApiError(403, 'You do not have permission to do that.');
  }

  const isOperator = (user) => user.groups.includes(GROUP_OPERATORS);
  const isEntitled = (userId, instanceId) =>
    entitlements.some((e) => e.userId === userId && e.instanceId === instanceId && e.status === 'applied');

  function visibleLog(user, l) {
    if (l.userId === user.id) return true;
    // Instance logs carry no user names. They are shown only for instances this user may use.
    return !!l.instanceId && !l.userId && isOperator(user) && isEntitled(user.id, l.instanceId);
  }

  // ---- the API ----------------------------------------------------------------------

  const api = {
    now,
    ApiError,

    // Demo helpers (not part of the real API)
    listDemoUsers() {
      return clone(users.map((u) => ({ id: u.id, name: u.name, email: u.email, groups: u.groups })));
    },
    advance(minutes) {
      skew += minutes * MIN;
      tick();
    },
    setScenario(name) {
      scenario = name;
    },
    getScenario() {
      return scenario;
    },
    reset() {
      skew = 0;
      init();
    },

    async signIn(userId) {
      await latency();
      const user = users.find((u) => u.id === userId);
      if (!user) throw new ApiError(401, 'Unknown user.');
      const t = now();
      logins[user.id].forEach((l) => { l.current = false; });
      logins[user.id].unshift({
        id: 'g-' + user.id + '-now-' + t,
        t,
        result: 'success',
        detail: '',
        ip: '203.0.113.' + (10 + Number(user.id.slice(1)) * 7 + 50),
        browser: BROWSERS[Number(user.id.slice(1)) % 3],
        current: true,
      });
      addLog(t, 'Control panel', 'info', 'Signed in', { userId: user.id });
      return { userId: user.id, name: user.name, email: user.email, groups: user.groups.slice(), signedInAt: t };
    },

    // Customer calls ---------------------------------------------------------------
    async listInstances(session) {
      await latency();
      const user = requireUser(session);
      tick();
      if (!isOperator(user)) return [];
      const t = now();
      return clone(instances.filter((i) => isEntitled(user.id, i.id)).map((i) => view(i, t)));
    },

    async startInstance(session, instanceId) {
      await latency();
      const user = requireUser(session);
      requireGroup(user, GROUP_OPERATORS);
      const inst = instances.find((i) => i.id === instanceId);
      if (!inst) throw new ApiError(404, 'Instance not found.');
      if (!isEntitled(user.id, instanceId)) {
        addLog(now(), 'Control panel', 'warning', 'Start refused for ' + inst.name + ': no active grant', { userId: user.id });
        throw new ApiError(403, 'You do not have access to this instance.');
      }
      tick();
      if (inst.state !== 'stopped' && inst.state !== 'failed') throw new ApiError(409, 'This instance is not stopped.');
      const t = now();
      const failing = inst.failsLeft > 0;
      if (failing) inst.failsLeft -= 1;
      inst.failMsg = null;
      inst.state = 'pending';
      inst.launchedAt = t;
      inst.warned = false;
      inst.readyLogged = false;
      inst.plan = { runningAt: t + 3000, ec2At: t + 6500, httpAt: t + 9500, failAt: failing ? t + 2500 : 0 };
      inst.setupDoneAt = t + 9500;
      addLog(t, 'Control panel', 'info', 'Start requested for ' + inst.name, { userId: user.id, instanceId: inst.id });
      addLog(t + 500, 'EC2', 'info', inst.name + ': state stopped to pending', { instanceId: inst.id });
      return { ok: true };
    },

    async listLogins(session) {
      await latency();
      const user = requireUser(session);
      return clone(logins[user.id].slice(0, 10));
    },

    async listLogs(session, filter) {
      await latency();
      const user = requireUser(session);
      tick();
      const t = now();
      const f = Object.assign({ source: 'all', severity: 'all', windowMinutes: 60 }, filter || {});
      const list = logs
        .filter((l) => l.t <= t && l.t >= t - f.windowMinutes * MIN)
        .filter((l) => visibleLog(user, l))
        .filter((l) => f.source === 'all' || l.source === f.source)
        .filter((l) => f.severity === 'all' || l.severity === f.severity)
        .sort((a, b) => b.t - a.t)
        .slice(0, 200);
      return clone(list);
    },

    // User management calls (user_mgrs only) --------------------------------------
    async listUsers(session) {
      await latency();
      requireGroup(requireUser(session), GROUP_USER_MGRS);
      return clone(users.map((u) => ({ id: u.id, name: u.name, email: u.email, groups: u.groups })));
    },

    async listAllInstances(session) {
      await latency();
      requireGroup(requireUser(session), GROUP_USER_MGRS);
      return clone(instances.map((i) => ({ id: i.id, name: i.name, type: i.type })));
    },

    async getGrants(session, userId) {
      await latency();
      requireGroup(requireUser(session), GROUP_USER_MGRS);
      return entitlements.filter((e) => e.userId === userId && e.status === 'applied').map((e) => e.instanceId);
    },

    async listChanges(session) {
      await latency();
      requireGroup(requireUser(session), GROUP_USER_MGRS);
      return clone(history.slice(0, 12));
    },

    async saveGrants(session, userId, changes) {
      await latency();
      const admin = requireUser(session);
      requireGroup(admin, GROUP_USER_MGRS);
      const target = users.find((u) => u.id === userId);
      if (!target) throw new ApiError(404, 'User not found.');
      const t = now();
      changes.forEach((c) => {
        const inst = instances.find((i) => i.id === c.instanceId);
        if (!inst) throw new ApiError(404, 'Instance not found.');
        if (c.grant && !isOperator(target)) {
          throw new ApiError(409, target.name + ' is not in the operators group, so cannot be granted instances.');
        }
      });
      changes.forEach((c) => {
        const inst = instances.find((i) => i.id === c.instanceId);
        const existing = entitlements.find((e) => e.userId === userId && e.instanceId === c.instanceId);
        if (c.grant) {
          if (existing) {
            existing.status = 'applied';
            existing.by = admin.id;
            existing.at = t;
          } else {
            entitlements.push({ userId, instanceId: c.instanceId, status: 'applied', by: admin.id, at: t });
          }
        } else if (existing) {
          existing.status = 'revoked'; // kept for the history, never deleted
          existing.by = admin.id;
          existing.at = t;
        }
        history.unshift({ t, adminId: admin.id, userId, instanceId: c.instanceId, action: c.grant ? 'Granted' : 'Revoked', result: 'Applied' });
        addLog(t, 'Control panel', 'info', (c.grant ? 'Granted ' : 'Revoked ') + target.name + ' access to ' + inst.name, { userId: admin.id });
      });
      return { applied: changes.length };
    },
  };

  init();
  window.MockApi = api;
})();
