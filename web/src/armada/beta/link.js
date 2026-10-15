/* INTERIM BETA (README.md): the connection's rules, with nothing of the page in them (resilience.md, R1).
   - One register in flight, at least 5 s apart, and at most one a minute after ten failures in a row.
   - Every failure waits min(cap, 1 s * 2^n) * (0.5 + 0.5 * random): the cap is 30 s while the page is visible, 120 s
     while it is hidden. A 429 holds every request until its Retry-After (plus up to 500 ms), and is a floor on that wait.
   - One retry layer: api() repeats a request only after a 429 (twice at most) or a refused CSRF token (once, with the
     token GET armada/session returns); the loop's own requests (register, the long poll) pass `once` and the loop alone
     waits and asks again.
   - The long poll is abandoned 65 s after it was sent (Tornado's heartbeat is 45 to 55 s) and asked again with dont_block.
   - Signed out (a 401) and signed in as someone else (another person's session in this browser) stop every request.
   - Nothing shows for the first 5 s of trouble: then "reconnecting", and after 30 s, or with the network gone, "offline".
   connection.js gives it the page (fetch, the clock, the page's state, what an event does); the node test
   web/tests/armada/beta_link.test.cjs gives it a fake clock and a fake server. */

const LONG_POLL_DEADLINE_MS = 65000;
const LONG_POLL_ANSWERING_MS = 3000;                       /* a long poll still open after 3 s is an answering server */
const CAP_VISIBLE_MS = 30000, CAP_HIDDEN_MS = 120000;
const REGISTER_GAP_MS = 5000, REGISTER_SLOW_GAP_MS = 60000, SLOW_AFTER_FAILURES = 10;
const GRACE_MS = 5000, OFFLINE_AFTER_MS = 30000;
const WAKE_EVERY_MS = 30000;                               /* each sign of the person (online, focus, ...) asks again at most this often */
const RATE_JITTER_MS = 500, RATE_TRIES = 2;

const backoffMs = (n, hidden, random) => Math.min(hidden ? CAP_HIDDEN_MS : CAP_VISIBLE_MS, 1000 * 2 ** n) * (0.5 + 0.5 * random());
/* the server no longer has my queue, or no longer has the events I have not seen: register again */
const queueLost = (e) => e.code === "BAD_EVENT_QUEUE_ID" || /already been pruned|not in this queue/.test(e.message);
/* worth asking again later: the network, the server, its rate limit, or a CSRF token the session endpoint could not mend */
const transient = (e) => !!e.net || e.status === 429 || e.status >= 500 || e.code === "CSRF_FAILED";
function retryAfterSeconds(r, j) { const h = parseFloat(r.headers.get("Retry-After")); if (h >= 0) return h; const b = j ? +j["retry-after"] : NaN; return b >= 0 ? b : 1; }

function createLink(env) {
  const {state} = env;
  const stats = {requests: 0, registers: 0, csrfRefreshes: 0, rateLimited: 0, deadlines: 0, queuesLost: 0, failures: 0, wakes: 0};
  const link = {csrf: env.csrf, status: "live", stopped: null, otherUserId: null, leaving: false, build: null, stats};
  let heldUntil = 0, registering = null, sessionAsked = null, lastRegisterAt = -Infinity, failures = 0, downSince = null;
  let napDone = null, poller = null, indicatorTimer = 0, generation = 0;
  const lastWake = new Map();

  const sleep = (ms) => new Promise((resolve) => env.setTimeout(resolve, ms));
  /* a wait that a sign of the person (wake) may end early */
  const nap = (ms) => new Promise((resolve) => { const t = env.setTimeout(done, ms); function done() { env.clearTimeout(t); napDone = null; resolve(); } napDone = done; });
  const stoppedError = () => Object.assign(new Error(link.stopped === "other_user" ? "Signed in as someone else" : "Signed out"), {status: 401, stopped: true});
  function setStatus(s) { if (link.status === s) return; link.status = s; env.onStatus(s); }

  async function api(method, path, params = {}, o = {}) {
    if (link.stopped) throw stoppedError();
    for (let held = heldUntil - env.now(); held > 0; held = heldUntil - env.now()) { await sleep(held); if (link.stopped) throw stoppedError(); }   /* a 429 that lands meanwhile holds it longer */
    const sp = new URLSearchParams(); for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) sp.set(k, typeof v === "string" ? v : JSON.stringify(v));
    let url = "/json/" + path, body = o.body; if (method === "GET" || method === "DELETE") { const s = sp.toString(); if (s) url += "?" + s; } else if (!body) body = sp;
    stats.requests++;
    const headers = method === "GET" ? {} : {"X-CSRFToken": link.csrf};
    let r; try { r = await env.fetch(url, {method, body, headers, credentials: "same-origin", signal: o.signal}); }
    catch (e) { throw Object.assign(new Error("The server cannot be reached"), {net: true, aborted: !!(o.signal && o.signal.aborted)}); }
    if (r.status === 401) { signedOut(); throw stoppedError(); }
    let j = null; try { j = await r.json(); } catch (e) { /* a proxy's page, or nothing: below */ }
    if (r.status === 429) { heldUntil = Math.max(heldUntil, env.now() + retryAfterSeconds(r, j) * 1000 + env.random() * RATE_JITTER_MS); stats.rateLimited++;
      if (!o.once && (o.tries || 0) < RATE_TRIES) return api(method, path, params, {...o, tries: (o.tries || 0) + 1});
      throw Object.assign(new Error((j && j.msg) || "Too many requests"), {status: 429}); }
    if (!j) throw Object.assign(new Error(`The server answered ${r.status}`), {status: r.status, net: r.status >= 500});
    if (r.status === 403 && j.code === "CSRF_FAILED" && !o.csrfMended) { const mended = await mendCsrf(); if (link.stopped) throw stoppedError(); if (mended) return api(method, path, params, {...o, csrfMended: true}); }
    if (j.result !== "success") throw Object.assign(new Error(j.msg || "The request failed"), {code: j.code, status: r.status, net: r.status >= 500});
    return j;
  }
  /* a write refused for its CSRF token: signing in again elsewhere rotated it, and the cookie that holds it is HttpOnly.
     The session endpoint (contract C1) says the current one; the request is repeated only if it is a new one. */
  async function mendCsrf() { const old = link.csrf; stats.csrfRefreshes++; try { await session(); } catch (e) { return false; } return link.csrf !== old; }
  /* GET armada/session: whose session this browser holds now, and its CSRF token; one at a time */
  function session() {
    if (!sessionAsked) sessionAsked = (async () => { const r = await api("GET", "armada/session", {}, {once: true});
      if (typeof r.user_id === "number" && state.ME && r.user_id !== state.ME) { otherUser(r.user_id); throw stoppedError(); }
      if (r.csrf_token && r.csrf_token !== link.csrf) { link.csrf = r.csrf_token; env.onToken(link.csrf); }
      return r; })().finally(() => { sessionAsked = null; });
    return sessionAsked; }

  function register() { if (!registering) registering = registerOnce().finally(() => { registering = null; }); return registering; }
  async function registerOnce() { lastRegisterAt = env.now(); stats.registers++;
    const r = await api("POST", "register", env.registerParams(), {once: true});
    if (!r.user_id) { signedOut(); throw stoppedError(); }   /* no session: the server registered a spectator */
    if (state.ME && r.user_id !== state.ME) { otherUser(r.user_id); throw stoppedError(); }
    state.queue = r.queue_id; state.lastEvent = r.last_event_id; link.build = buildOf(r);
    env.onRegister(r); return r; }
  /* register until it works. At load (boot) a refusal that waiting cannot mend is thrown; later every failure waits. */
  async function connect(alive, boot) {
    for (;;) {
      const gap = (failures >= SLOW_AFTER_FAILURES ? REGISTER_SLOW_GAP_MS : REGISTER_GAP_MS) - (env.now() - lastRegisterAt);
      if (gap > 0) await sleep(gap);
      if (!alive()) throw stoppedError();
      try { const r = await register(); answered(); return r; }
      catch (e) { if (link.stopped || (boot && !transient(e))) throw e; await failed(e); if (!alive()) throw stoppedError(); }
    }
  }

  /* the event queue: one long poll at a time, for as long as the session stands */
  async function run() { const mine = ++generation, alive = () => mine === generation && !link.stopped && !link.leaving;
    let dontBlock = false, resync = false;
    while (alive()) {
      if (!state.queue) { try { await connect(alive, false); } catch (e) { return; } if (resync) { resync = false; env.onResync(); } continue; }
      const ctl = new AbortController(), me = {ctl, at: env.now(), woken: false};
      const deadline = env.setTimeout(() => { stats.deadlines++; ctl.abort(); }, LONG_POLL_DEADLINE_MS), open = env.setTimeout(answered, LONG_POLL_ANSWERING_MS);
      poller = me;
      let r = null, err = null;
      try { r = await api("GET", "events", {queue_id: state.queue, last_event_id: state.lastEvent, dont_block: dontBlock || undefined}, {signal: ctl.signal, once: true}); }
      catch (e) { err = e; }
      env.clearTimeout(deadline); env.clearTimeout(open); if (poller === me) poller = null;
      if (!alive()) return;
      if (!err) { dontBlock = false; answered(); const at = env.now(); for (const ev of r.events) { state.lastEvent = Math.max(state.lastEvent, ev.id); env.onEvent(ev, at); } }
      else if (queueLost(err)) { stats.queuesLost++; failures++; state.queue = null; resync = true; }
      else if (me.woken) dontBlock = true;   /* asked again at once, and the answer must not wait for news */
      else { if (err.aborted) dontBlock = true; await failed(err); }
    } }
  async function failed(e) { failures++; stats.failures++; if (downSince === null) { downSince = env.now(); indicator(); }
    await nap(Math.max(heldUntil - env.now(), backoffMs(failures - 1, env.hidden(), env.random))); }
  function answered() { failures = 0; if (downSince === null) return; downSince = null; indicator(); }
  function indicator() { env.clearTimeout(indicatorTimer); if (link.stopped) return; if (downSince === null) { setStatus("live"); return; }
    const down = env.now() - downSince;
    if (down >= OFFLINE_AFTER_MS || (down >= GRACE_MS && !env.online())) setStatus("offline");
    else { if (down >= GRACE_MS) setStatus("reconnecting"); indicatorTimer = env.setTimeout(indicator, (down >= GRACE_MS ? OFFLINE_AFTER_MS : GRACE_MS) - down); } }
  /* a sign that the network or the person is back: a wait ends now; a long poll that may be dead (after the network came
     back, a page restored, a machine woken) is asked again */
  function wake(reason) { const t = env.now(); if (link.stopped || t - (lastWake.get(reason) ?? -Infinity) < WAKE_EVERY_MS) return; lastWake.set(reason, t); stats.wakes++;
    if (reason === "online") indicator();
    if (napDone) { napDone(); return; }
    if (poller && reason !== "focus" && reason !== "visible" && t - poller.at > GRACE_MS) { poller.woken = true; poller.ctl.abort(); } }

  function stop(kind) { if (link.stopped) return; link.stopped = kind; if (poller) poller.ctl.abort(); if (napDone) napDone(); env.clearTimeout(indicatorTimer); setStatus(kind); }
  function signedOut() { stop("signed_out"); }
  function otherUser(id) { link.otherUserId = id; stop("other_user"); }
  /* another tab of this browser said whose session it holds (on its load) or that it signed out */
  function heard(msg) {
    if (msg.type === "signed_out") { signedOut(); return; }
    if (msg.type !== "session" || !state.ME) return;
    if (msg.user_id !== state.ME) { otherUser(msg.user_id); return; }
    if (msg.csrf_token) link.csrf = msg.csrf_token;
    if (link.stopped) { link.stopped = null; link.otherUserId = null; failures = 0; downSince = null; setStatus("live"); run(); } }
  /* the page is going away for good: its queue goes with it, rather than waiting ten idle minutes for the server to drop it */
  function leave() { link.leaving = true; if (poller) poller.ctl.abort(); if (!state.queue || link.stopped) return;
    env.fetch("/json/events?" + new URLSearchParams({queue_id: state.queue}), {method: "DELETE", headers: {"X-CSRFToken": link.csrf}, credentials: "same-origin", keepalive: true}).catch(() => {}); }
  const buildOf = (x) => `${x.zulip_version}|${x.zulip_merge_base}`;
  /* a restart event from a server running other code than the one this page registered with */
  const isNewBuild = (ev) => link.build !== null && buildOf(ev) !== link.build;

  return Object.assign(link, {api, session, register, connect: () => connect(() => !link.stopped, true), run, wake, heard, leave, isNewBuild, signedOut, otherUser});
}

export {backoffMs, createLink, queueLost, CAP_HIDDEN_MS, CAP_VISIBLE_MS, GRACE_MS, LONG_POLL_DEADLINE_MS, OFFLINE_AFTER_MS,
  REGISTER_GAP_MS, REGISTER_SLOW_GAP_MS, WAKE_EVERY_MS};
