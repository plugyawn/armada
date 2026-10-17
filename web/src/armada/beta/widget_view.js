/* INTERIM BETA (README.md): a person's own view of a plugin's widget (design/r9/plugins/ROUND2.md, sections D and G).
   The widget's document is the team's, one for everyone; my preferences (armada_widget_preference) say how I want it
   drawn: in my nav or not, where in the group, under my label, with one of its declared figures at rest, only the rows
   its declared facets keep, and a mark when one of its declared alerts is met. The view is computed here, on my client,
   from the document and my preferences, and nothing else: pure, and tested by web/tests/armada/beta_plugin_view.test.cjs. */

const isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
function dataAt(data, path) { if (typeof path !== "string" || !path.startsWith("data.")) return undefined; let cur = data; for (const k of path.slice(5).split(".")) { if (!isObj(cur) || !(k in cur)) return undefined; cur = cur[k]; } return cur; }
const options = (declared, kind) => (isObj(declared) && isObj(declared.options) && Array.isArray(declared.options[kind]) ? declared.options[kind] : []);

/* a row stays when, for each facet I filter by, it has that value; a row that does not carry the facet is not one it is about */
const keeps = (row, filters) => Object.entries(filters).every(([k, v]) => !isObj(row.facets) || !(k in row.facets) || row.facets[k] === v);

/* the document as I see it: my figure at rest, only the rows my filters keep (each list's rows written into the list, so
   that whoever reads a list's rows reads mine), and the first of my alerts that is met */
function viewOf(declared, doc, pref) {
  const p = isObj(pref) ? pref : {}, label = p.label || (declared ? declared.label : "");
  if (!isObj(doc)) return {label, doc, alert: null};
  let rest = doc.rest; const figure = p.rest_figure && options(declared, "figures").find((f) => f.key === p.rest_figure);
  if (figure && isObj(rest)) rest = {...rest, figure: figure.figure, sizer: figure.sizer};
  const filters = isObj(p.filters) ? p.filters : {}, filtering = Object.keys(filters).length > 0;
  const blocks = !filtering || !Array.isArray(doc.blocks) ? doc.blocks : doc.blocks.map((b) => {
    if (!isObj(b) || b.type !== "list") return b;
    const bound = b.bind ? dataAt(doc.data || {}, b.bind) : undefined, items = Array.isArray(bound) ? bound : Array.isArray(b.items) ? b.items : [];
    const {bind, ...rest_} = b; return {...rest_, items: items.filter((row) => !isObj(row) || keeps(row, filters))}; });
  let alert = null;
  for (const [key, threshold] of Object.entries(isObj(p.alerts) ? p.alerts : {})) {
    const a = options(declared, "alerts").find((x) => x.key === key), v = a ? dataAt(doc.data || {}, a.value) : undefined;
    if (typeof v !== "number" || typeof threshold !== "number") continue;
    if (a.op === "<=" ? v <= threshold : v >= threshold) { alert = {key, title: a.title, threshold}; break; } }
  return {label, doc: rest === doc.rest && blocks === doc.blocks ? doc : {...doc, rest, blocks}, alert};
}

/* in my nav: my say, else the team's default ("shown"; "offered" waits in the "+") */
function inMyNav(declared, teamDefault, pref) {
  if (!declared || !(declared.in || []).includes("nav")) return false;
  if (isObj(pref) && typeof pref.shown === "boolean") return pref.shown;
  return (teamDefault ? teamDefault.nav : declared.team_default || "shown") === "shown";
}
/* in my column, where it is linked: my say, else the team's */
function inMyColumn(declared, teamDefault, pref) {
  if (!declared || !(declared.in || []).includes("column")) return false;
  if (isObj(pref) && typeof pref.in_column === "boolean") return pref.in_column;
  return !(teamDefault && teamDefault.column === false);
}
/* the group's order: the places I gave first, smallest first, then the rest in the order they came (newest install on
   top); a sort that keeps ties as they were */
const byMyOrder = (entries, prefOf) => entries.map((x, i) => [x, i]).sort(([a, i], [b, j]) => {
  const pa = prefOf(a), pb = prefOf(b), ka = pa && typeof pa.position === "number" ? pa.position : Infinity, kb = pb && typeof pb.position === "number" ? pb.position : Infinity;
  return ka - kb || i - j; }).map(([x]) => x);

export {byMyOrder, inMyColumn, inMyNav, viewOf};
