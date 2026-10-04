/*
 * Client for the Control API. Signs the user in with any OpenID Connect provider
 * (authorization code with PKCE) and calls the API with the ID token.
 *
 * Settings come from config.js (window.PANEL_CONFIG):
 *   issuer       the provider's issuer URL; its endpoints are found from
 *                <issuer>/.well-known/openid-configuration (Okta, Entra ID, Cognito, ...)
 *   clientId     the single-page app client ID (also what the API's authorizer expects)
 *   redirectUri  where the provider sends the browser back (this page's address)
 *   apiUrl       the Control API address
 *   scope        optional, default "openid email profile" (Okta and Entra also want offline_access)
 * Cognito pools can still be given as userPoolId + region (the issuer is built from them),
 * and hostedLoginDomain keeps Cognito's own sign-in and sign-out addresses.
 * Terraform (control_panel_site.tf) publishes these for the lab's Cognito pool;
 * any other provider's file is written by hand.
 *
 * Tokens live in sessionStorage, so closing the tab signs the user out of this page.
 * The page keeps no permissions of its own: every rule is checked again by the API.
 */
(function () {
  'use strict';

  const cfg = window.PANEL_CONFIG || null;
  const clientId = cfg ? cfg.clientId || cfg.appClientId || '' : '';
  const issuer = cfg ? cfg.issuer || (cfg.userPoolId && cfg.region ? 'https://cognito-idp.' + cfg.region + '.amazonaws.com/' + cfg.userPoolId : '') : '';
  const TOKENS = 'panel.tokens';
  const FLOW = 'panel.flow';

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  const store = {
    get(key) { try { return JSON.parse(sessionStorage.getItem(key)); } catch (err) { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (err) { /* storage blocked */ } },
    remove(key) { try { sessionStorage.removeItem(key); } catch (err) { /* storage blocked */ } },
  };

  const announce = (ok) => window.dispatchEvent(new CustomEvent('api-status', { detail: { ok } }));

  function configProblem() {
    if (!cfg) return 'This page has no settings file. Apply the Terraform for the lab to publish config.js, or upload one by hand.';
    const missing = [];
    if (!clientId) missing.push('clientId');
    if (!cfg.redirectUri) missing.push('redirectUri');
    if (!issuer && !cfg.hostedLoginDomain && !(cfg.authorizationEndpoint && cfg.tokenEndpoint)) missing.push('issuer');
    if (missing.length) return 'The settings file is missing: ' + missing.join(', ') + '.';
    if (!cfg.apiUrl) return 'The Control API address is not set. Set control_panel_api_url in terraform.tfvars and apply again.';
    return null;
  }

  // ---- sign-in (OpenID Connect, authorization code with PKCE) -----------------------

  let found = null;

  // Where to send the browser and the token requests. Explicit addresses win, then
  // Cognito's hosted sign-in domain, then the provider's discovery document.
  function endpoints() {
    if (!found) {
      found = loadEndpoints().catch((err) => {
        found = null;
        throw err;
      });
    }
    return found;
  }

  async function loadEndpoints() {
    if (cfg.authorizationEndpoint && cfg.tokenEndpoint) {
      return { authorize: cfg.authorizationEndpoint, token: cfg.tokenEndpoint, logout: cfg.logoutEndpoint || null, style: 'oidc' };
    }
    if (cfg.hostedLoginDomain) {
      const base = 'https://' + cfg.hostedLoginDomain;
      return { authorize: base + '/oauth2/authorize', token: base + '/oauth2/token', logout: base + '/logout', style: 'cognito' };
    }
    let doc;
    try {
      const res = await fetch(issuer.replace(/\/+$/, '') + '/.well-known/openid-configuration');
      doc = await res.json();
    } catch (err) {
      throw new ApiError(0, 'Could not reach the sign-in service.');
    }
    if (!doc || !doc.authorization_endpoint || !doc.token_endpoint) throw new ApiError(0, 'The sign-in service did not describe itself correctly.');
    return { authorize: doc.authorization_endpoint, token: doc.token_endpoint, logout: doc.end_session_endpoint || null, style: 'oidc' };
  }

  const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const random = (n) => b64url(crypto.getRandomValues(new Uint8Array(n)));

  async function startLogin() {
    const verifier = random(48);
    const state = random(16);
    const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
    store.set(FLOW, { verifier, state, hash: location.hash });
    const url = new URL((await endpoints()).authorize);
    url.search = new URLSearchParams({
      response_type: 'code',
      client_id: clientId,
      redirect_uri: cfg.redirectUri,
      scope: cfg.scope || 'openid email profile',
      state,
      code_challenge: challenge,
      code_challenge_method: 'S256',
    }).toString();
    location.assign(url.toString());
    return new Promise(() => {}); // the page is leaving
  }

  async function tokenRequest(params) {
    let res;
    try {
      res = await fetch((await endpoints()).token, {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams(Object.assign({ client_id: clientId }, params)).toString(),
      });
    } catch (err) {
      throw err instanceof ApiError ? err : new ApiError(0, 'Could not reach the sign-in service.');
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.id_token) throw new ApiError(401, 'Sign-in did not complete. Please sign in again.');
    return data;
  }

  function keepTokens(data, previous) {
    const claims = JSON.parse(atob(data.id_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    store.set(TOKENS, {
      idToken: data.id_token,
      refreshToken: data.refresh_token || (previous && previous.refreshToken) || null,
      expiresAt: claims.exp * 1000,
    });
  }

  async function finishLogin(params) {
    const flow = store.get(FLOW);
    store.remove(FLOW);
    if (params.get('error')) throw new ApiError(401, params.get('error_description') || 'Sign-in was cancelled.');
    if (!flow || flow.state !== params.get('state')) throw new ApiError(401, 'Sign-in could not be checked. Please sign in again.');
    keepTokens(await tokenRequest({
      grant_type: 'authorization_code',
      code: params.get('code'),
      redirect_uri: cfg.redirectUri,
      code_verifier: flow.verifier,
    }));
    history.replaceState(null, '', location.pathname + (flow.hash || ''));
  }

  async function idToken() {
    let t = store.get(TOKENS);
    if (!t) return startLogin();
    if (t.expiresAt - Date.now() > 60000) return t.idToken;
    if (t.refreshToken) {
      try {
        keepTokens(await tokenRequest({ grant_type: 'refresh_token', refresh_token: t.refreshToken }), t);
        return store.get(TOKENS).idToken;
      } catch (err) {
        if (err.status === 0) throw err;
      }
    }
    store.remove(TOKENS);
    return startLogin();
  }

  // Clears this page's tokens, then ends the provider's session when it has a sign-out
  // address. Without one the page just reloads, and the next visit asks for sign-in again.
  async function signOut() {
    const previous = store.get(TOKENS);
    store.remove(TOKENS);
    store.remove(FLOW);
    let e = null;
    try {
      e = await endpoints();
    } catch (err) { /* provider unreachable: sign out locally */ }
    if (!e || !e.logout) return location.assign(cfg.redirectUri);
    const url = new URL(e.logout);
    url.search = new URLSearchParams(
      e.style === 'cognito'
        ? { client_id: clientId, logout_uri: cfg.redirectUri }
        : Object.assign({ post_logout_redirect_uri: cfg.redirectUri }, previous ? { id_token_hint: previous.idToken } : {})
    ).toString();
    location.assign(url.toString());
  }

  // ---- requests ---------------------------------------------------------------------

  async function request(method, path, body, retried) {
    const token = await idToken();
    let res;
    try {
      res = await fetch(cfg.apiUrl.replace(/\/+$/, '') + path, {
        method,
        headers: Object.assign({ authorization: 'Bearer ' + token }, body === undefined ? {} : { 'content-type': 'application/json' }),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      announce(false);
      throw new ApiError(0, 'Could not reach the Control API. Check your connection and try again.');
    }
    announce(true);
    if (res.status === 401 && !retried) {
      store.remove(TOKENS);
      return request(method, path, body, true);
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, (data && data.message) || 'The Control API returned an error (' + res.status + ').');
    return data;
  }

  const enc = encodeURIComponent;

  window.Api = {
    ApiError,
    now: () => Date.now(),
    configProblem,
    region: () => (cfg ? cfg.region || '' : ''),

    // Completes a sign-in in progress, or sends the browser to the sign-in page. Resolves
    // with the signed-in user, or never when the browser is leaving for the sign-in page.
    async start() {
      const params = new URLSearchParams(location.search);
      if (params.has('code') || params.has('error')) await finishLogin(params);
      await idToken();
      return request('POST', '/session');
    },
    signOut,

    listInstances: () => request('GET', '/instances'),
    startInstance: (instanceId) => request('POST', '/instances/' + enc(instanceId) + '/start'),
    listLogins: () => request('GET', '/logins'),
    listAllLogins: () => request('GET', '/admin/logins'),
    listAllLogs: (filter) => request('GET', '/admin/logs?' + new URLSearchParams(filter || {}).toString()),
    listLogs: (filter) => request('GET', '/logs?' + new URLSearchParams(filter || {}).toString()),

    // User management (user managers and administrators; the API checks again)
    listUsers: () => request('GET', '/admin/users'),
    listAllInstances: () => request('GET', '/admin/instances'),
    getGrants: (userId) => request('GET', '/admin/users/' + enc(userId) + '/grants'),
    listChanges: () => request('GET', '/admin/changes'),
    saveUserChanges: (userId, payload) => request('PUT', '/admin/users/' + enc(userId), payload),
  };
})();
