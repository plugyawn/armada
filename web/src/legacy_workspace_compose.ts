import $ from "jquery";

import * as compose_actions from "./compose_actions.ts";
import * as compose_ui from "./compose_ui.ts";
import type {ArmadaWorkspaceEditorSelectionContext} from "./legacy_workspace_editor_contract.ts";

function inline_code(value: string): string {
    const longest_run = Math.max(0, ...[...value.matchAll(/`+/g)].map((match) => match[0].length));
    const fence = "`".repeat(Math.max(1, longest_run + 1));
    return `${fence}${value}${fence}`;
}

export function format_armada_workspace_compose_context(
    context: ArmadaWorkspaceEditorSelectionContext,
): string {
    const lines =
        context.selectedText !== "" &&
        context.startLine !== undefined &&
        context.endLine !== undefined
            ? `\nLines: ${context.startLine === context.endLine ? context.startLine : `${context.startLine}-${context.endLine}`}`
            : "";
    if (context.selectedText === "") {
        return `Ask Onix about this workspace file.\n\nPath: ${inline_code(context.path)}`;
    }
    const longest_run = Math.max(
        0,
        ...[...context.selectedText.matchAll(/`+/g)].map((match) => match[0].length),
    );
    const fence = "`".repeat(Math.max(3, longest_run + 1));
    return `Ask Onix about this workspace selection.\n\nPath: ${inline_code(context.path)}${lines}\n\n${fence}\n${context.selectedText}\n${fence}`;
}

export function fill_armada_compose_with_workspace_context(
    context: ArmadaWorkspaceEditorSelectionContext,
): void {
    compose_actions.start({message_type: "stream", trigger: "workspace file context"});
    compose_ui.insert_syntax_and_focus(
        format_armada_workspace_compose_context(context),
        $<HTMLTextAreaElement>("textarea#compose-textarea"),
        "block",
        2,
    );
}
