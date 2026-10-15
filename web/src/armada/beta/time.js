/* INTERIM BETA (README.md): the server's clock and the ways the page writes a time. */

import {state} from "./state.js";

const now = () => Date.now() + state.skew;

/* ---------- time ---------- */
const p2 = (n) => String(n).padStart(2, "0");
const hhmm = (ts) => { const d = new Date(ts * 1000); return `${d.getHours()}:${p2(d.getMinutes())}`; };
const dayKey = (ts) => { const d = new Date(ts * 1000); return d.getFullYear() * 10000 + d.getMonth() * 100 + d.getDate(); };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const dayLabel = (ts) => { const d = new Date(ts * 1000), t = new Date(now()), days = Math.round((new Date(t.getFullYear(), t.getMonth(), t.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  return days === 0 ? "Today" : days === 1 ? "Yesterday" : days > 1 && days < 7 ? DOW[d.getDay()] : `${d.getDate()} ${MON[d.getMonth()]}${d.getFullYear() !== t.getFullYear() ? " " + d.getFullYear() : ""}`; };
const shortDay = (ts) => { const d = new Date(ts * 1000), days = (now() - d) / 864e5; return days < 6 ? DOW[d.getDay()].slice(0, 3) : `${d.getDate()} ${MON[d.getMonth()]}`; };
const age = (ts) => { const s = Math.max(0, now() / 1000 - ts); return s < 60 ? "now" : s < 3600 ? Math.floor(s / 60) + "m" : s < 172800 ? Math.floor(s / 3600) + "h" : Math.floor(s / 86400) + "d"; };
const dur = (s) => { s = Math.max(1, Math.round(s)); return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${p2(s % 60)}s` : `${Math.floor(s / 3600)}h ${p2(Math.floor(s % 3600 / 60))}m`; };
const mmss = (s) => (s < 600 ? `${Math.floor(s / 60)}:${p2(s % 60)}` : s < 6000 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h`);

export {age, dayKey, dayLabel, dur, hhmm, mmss, now, shortDay};
