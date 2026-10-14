// The kit as plain elements, for the feed: the same descriptions chrome
// renders through Preact (view.tsx), built with no framework. A row built
// here is a real element the feed keeps and writes into.

import * as blocks from "./blocks.ts";
import * as controls from "./controls.ts";
import * as conversation from "./conversation.ts";
import * as icons from "./icons.ts";
import {build_html} from "./tree.ts";
import type {Tree} from "./tree.ts";

export {build, build_html, n} from "./tree.ts";
export type {Child, Tree} from "./tree.ts";

function builder<Arguments extends unknown[]>(
    recipe: (...arguments_: Arguments) => Tree,
): (...arguments_: Arguments) => HTMLElement {
    return (...arguments_) => build_html(recipe(...arguments_));
}

export * as recipes from "./recipes.ts";

export const lane = builder(conversation.lane);
export const turn = builder(conversation.turn);
export const bubble = builder(conversation.bubble);
export const answer = builder(conversation.answer);
export const reactions = builder(conversation.reactions);
export const receipt = builder(conversation.receipt);
export const thread_line = builder(conversation.thread_line);
export const thread = builder(conversation.thread);
export const day = builder(conversation.day);
export const new_line = builder(conversation.new_line);
export const run_footer = builder(conversation.run_footer);
export const overlay = builder(conversation.overlay);
export const link_preview = builder(conversation.link_preview);
export const hello = builder(conversation.hello);
export const code_block = builder(blocks.code_block);
export const diff_block = builder(blocks.diff_block);
export const table_block = builder(blocks.table_block);
export const artifact = builder(blocks.artifact);
export const question = builder(blocks.question);
export const stream_head = builder(icons.stream_head);
export const face = builder(icons.face);
export const figure = builder(icons.figure);
export const text_control = builder(controls.text_control);
export const glyph_control = builder(controls.glyph_control);
