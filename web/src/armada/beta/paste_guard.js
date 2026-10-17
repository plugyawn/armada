/* INTERIM BETA (README.md): whether words hold something shaped like a key (design/r9/plugins/ROUND2.md, decision 4).
   A secret is asked for through a plugin's settings, never in a message: a key in the transcript is in everyone's
   history, in every export and in the next model's context. So a field that is about to hold one offers first to put it
   where only the plugin reads it (plugin_view.js draws that offer). This only says what is shaped like a key; it is pure,
   and tested by web/tests/armada/beta_paste_guard.test.cjs. */

/* the prefixes services give their keys: OpenAI and Anthropic, Stripe, GitHub, GitLab, Slack, Hugging Face, npm,
   DigitalOcean, AWS and Google */
const PREFIXED = /^(sk-|(sk|rk|pk)_(live|test)_|gh[pousr]_|github_pat_|glpat-|xox[abprs]-|hf_|npm_|dop_v1_|AKIA|ASIA|AIza)[A-Za-z0-9_-]{8,}$/;
const JWT = /^eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$/;
const URL_WITH_PASSWORD = /^[a-z][a-z0-9+.-]*:\/\/[^\s/@:]*:[^\s/@]+@/i;
const PRIVATE_KEY = /-----BEGIN [A-Z ]*PRIVATE KEY-----/;
const HEX = /^[0-9a-f]+$/i;
/* a long run with upper case, lower case and digits mixed: the shape of a random token. A hex digest is not taken for
   one in words to Onix, where it is far more often a commit or a checksum than a key. */
const randomish = (t) => t.length >= 24 && /^[A-Za-z0-9_\-+/=.]+$/.test(t) && /[a-z]/.test(t) && /[A-Z]/.test(t) && /\d/.test(t) && !HEX.test(t);
/* what a word is wrapped in when it is quoted or ends a sentence */
const bare = (word) => word.replace(/^["'`([<]+/, "").replace(/["'`)\]>,;:.!?]+$/, "");

/* the first thing in the words that is shaped like a key, or null. In a setting, where words are not a message, a long
   hex run counts too (`hex`): a W&B key is forty hex digits. */
function findKey(text, {hex = false} = {}) {
  if (typeof text !== "string") return null;
  const block = PRIVATE_KEY.exec(text); if (block) return block[0];
  for (const word of text.split(/\s+/)) {
    const t = bare(word); if (t.length < 16) continue;
    if (URL_WITH_PASSWORD.test(t) || JWT.test(t)) return t;
    if (PREFIXED.test(t) && t.length >= 20 && /\d/.test(t)) return t;
    if (randomish(t) || (hex && t.length >= 32 && HEX.test(t))) return t;
  }
  return null;
}

export {findKey};
