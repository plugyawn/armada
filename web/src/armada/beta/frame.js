/* INTERIM BETA (README.md): the frame the kit draws, and the elements the page keeps of it. */

import {$, $$, el, esc} from "./util.js";
import {state} from "./state.js";
import {faceOf} from "./faces.js";
import {drawMeRow} from "./partner_card.js";

/* ---------- the frame ---------- */
let main, feed, lane, nav, navScroll, navFixed, side, outline, band, cz, tx, sh, go, goc, ph, toBtn, modeBtn, panes;
/* the frame the kit draws, found once at boot: its elements, the band's line for the picker, and the nav's rows */
function bindFrame() {
  frame({nav: "", path: "", title: "", dist: "", commit: {who: "", sha: "", subj: "", age: ""}, tab: "Chat", checklist: {title: "Checklist"}, band: {}, composer: {text: "", verb: "Send", mode: "", focus: false}});
  main = $("main"); feed = $("#pane"); lane = $("#col"); nav = $("nav"); side = $("#side"); outline = $("#outline"); state.hd = $("main > header.hd");
  band = {el: $("main > .band"), ty: $("#ty"), run: $("#run"), jump: $("#jump"), clock: $("#clock"), st: $("#run-st"), act: $("#act"), stop: $("#stop")};
  band.pk = el("p", "pkb"); band.pk.hidden = true; $(".lane", band.el).append(band.pk);   /* the picker's one sentence */
  cz = $("main > .dock .cz"); tx = $(".tx", cz); sh = $(".sh", cz); go = $(".disc", cz); goc = $(".disc .cell", cz); ph = $(".ph", cz); toBtn = $(".k.to", cz); modeBtn = $(".k.mode", cz);
  panes = {Chat: feed}; $$("main > .pane[data-pane]").forEach((x) => (panes[x.dataset.pane] = x));
  $$(".slot > i, .pc > i").forEach((i) => i.classList.add("sz"));
  [band.run, band.ty, band.jump].forEach((e) => e && (e.inert = e.classList.contains("off")));
  /* the nav: the kit's top row, a scroller of real places, and me */
  const top = $(".top", nav); nav.textContent = ""; navScroll = el("div", "scroll"); navFixed = el("div", "fixed", `<button class="row" data-k="jump" data-tip="Jump to" data-key="⌘K">${ic("search")}<span class="lab">Jump to</span></button><button class="row" data-k="home">${ic("home")}<span class="lab">Home</span></button><a class="row old" href="/" data-tip="The old interface, with everything the beta does not have yet">${ic("open")}<span class="lab">Old interface</span></a>`);
  nav.append(top, navFixed, navScroll); const meRow = el("button", "row me", `${faceOf(state.ME, "s20")}<span class="lab">${esc(state.meName)}</span>`); nav.append(meRow); host(nav);
  $(".wk-name", top).dataset.tip = "The workspace menu is in the old interface"; $(".new", top).dataset.tip = "New: in the old interface for now"; meRow.dataset.tip = "Signed in as " + state.meName; drawMeRow();
}

export {band, bindFrame, cz, feed, go, goc, lane, main, modeBtn, nav, navFixed, navScroll, outline, panes, ph, sh,
  side, toBtn, tx};
