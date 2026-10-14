// This loader puts every stylesheet that is not part of the rebuilt
// frontend (web/src/armada/) into the cascade layer `zulip`: our old
// sheets, the generated icon font's sheet, and the sheets of libraries.
//
// Rules in a layer lose to rules in a later layer and to rules in no
// layer, whatever their specificity.  The rebuild's sheets declare
// `@layer zulip, armada.tokens, armada.base, armada.kit, armada.surfaces`,
// so a new rule wins over an old one without an id selector or
// `!important`.  Among themselves the old rules cascade exactly as
// before, because they all sit in the one layer in their old order.
//
// That only holds if nothing old is left outside the layer.  Two libraries
// write their styles into a <style> element at run time: CodeMirror
// (through the style-mod package) and the altcha widget of the sign-up
// pages.  Unlayered, those would now beat every override of them in our
// sheets, so the second half of this loader edits the one statement in
// each that writes the text, to write it as an `@layer zulip` block.  It
// fails the build when the statement is no longer there, which is what
// an upgrade of either package may do.
//
// Two more libraries write rules at run time and are left alone, because
// no old rule overrides what they write: flatpickr inserts one rule (the
// arrow of an open date picker, `.flatpickr-calendar.arrowTop` and its
// siblings) at the top level of the first stylesheet it can write to,
// with `insertRule`; and Plotly writes its own <style> on /stats.  A new
// override of either in an old sheet would lose to them.
//
// To find a new source of unlayered styles, list `document.styleSheets`
// on the running page: every sheet should start with `@layer zulip`,
// except the inline <style> of the loading splash in the app template,
// Plotly's on /stats, and the one rule flatpickr adds to an existing
// sheet once a date picker has been opened.

import postcss from "postcss";
import type {LoaderContext} from "webpack";

const LAYER = "zulip";

// These must stay at the top of a sheet, outside any block.  css-loader
// resolves each @import into a module of its own, which this loader
// wraps when its turn comes (see `importLoaders` in webpack.config.ts).
const TOP_LEVEL_ONLY = new Set(["charset", "import", "namespace"]);

type Options = {
    // Set for a script: the statement that assigns the stylesheet's text to
    // a <style> element, as `[target, value]`.
    injectedStyle?: [string, string];
};

type SourceMap = Parameters<LoaderContext<Options>["callback"]>[2];

// Without `injectedStyle` the source is a stylesheet, whatever the
// resource is called (the icon font's is a .cjs file).
export default function zulipLayerLoader(
    this: LoaderContext<Options>,
    source: string,
    map?: SourceMap,
): void {
    const {injectedStyle} = this.getOptions();
    if (injectedStyle !== undefined) {
        const [target, value] = injectedStyle;
        const statement = `${target} = ${value}`;
        if (source.split(statement).length !== 2) {
            throw new Error(
                `zulip-layer-loader: expected "${statement}" exactly once in ${this.resourcePath}; ` +
                    "update webpack.config.ts so that what this package injects is wrapped in @layer zulip.",
            );
        }
        this.callback(
            null,
            source.replace(statement, `${target} = "@layer ${LAYER} {\\n" + ${value} + "}"`),
            map,
        );
        return;
    }

    const root = postcss.parse(source, {
        from: this.resourcePath,
        map: map ? {prev: map, inline: false, annotation: false} : false,
    });
    // A sheet that has been here already (one imported both through a rule
    // and with inline loaders, say) is returned as it is: wrapped again it
    // would be a sub-layer and lose to every other old rule.
    const rules = root.nodes.filter(
        (node) =>
            node.type !== "comment" &&
            !(node.type === "atrule" && TOP_LEVEL_ONLY.has(node.name.toLowerCase())),
    );
    if (
        rules.length > 0 &&
        rules.every(
            (node) =>
                node.type === "atrule" &&
                node.name.toLowerCase() === "layer" &&
                node.params.trim() === LAYER &&
                node.nodes !== undefined,
        )
    ) {
        this.callback(null, source, map);
        return;
    }
    const layer = postcss.atRule({
        name: "layer",
        params: LAYER,
        raws: {afterName: " ", between: " ", before: "\n"},
    });
    // `each` tolerates the nodes being moved while it walks them.
    root.each((node) => {
        if (node.type === "atrule" && TOP_LEVEL_ONLY.has(node.name.toLowerCase())) {
            return;
        }
        // Comments above an @import stay with it.
        if (node.type === "comment" && layer.nodes === undefined) {
            return;
        }
        layer.append(node);
    });
    if (layer.nodes !== undefined) {
        root.append(layer);
    }
    const result = root.toResult({
        to: this.resourcePath,
        map: map ? {prev: map, inline: false, annotation: false} : false,
    });
    if (!map) {
        this.callback(null, result.css);
        return;
    }
    const wrapped_map = result.map.toJSON();
    this.callback(null, result.css, {
        ...wrapped_map,
        version: Number(wrapped_map.version),
        file: wrapped_map.file ?? this.resourcePath,
    });
}
