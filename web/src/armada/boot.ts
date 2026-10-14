// The one module ui_init.js calls (SPEC-ENGINEERING section 10). It mounts
// the frame, registers every surface's commands and starts the bridges that
// keep the old interface alive inside the frame.
//
// Every surface is imported here once, in the order its stylesheet loads,
// with its commands: a surface package fills its own directory and edits no
// shared file (PLAN.md, rule 3).

import "./bridge/legacy_feed.ts";
import "./bridge/legacy_header.ts";
import "./bridge/legacy_composer.ts";
import "./bridge/legacy_home.ts";
import "./bridge/legacy_nav.ts";
import "./surfaces/nav/index.tsx";
import "./surfaces/header/index.tsx";
import "./surfaces/band/index.tsx";
import "./surfaces/composer/index.tsx";
import "./surfaces/column/index.tsx";
import "./surfaces/checklist/index.tsx";
import "./surfaces/lists/index.tsx";
import "./surfaces/trace/index.tsx";
import "./surfaces/home/index.tsx";
import "./surfaces/readme/index.tsx";
import "./surfaces/files/index.tsx";
import "./surfaces/changes/index.tsx";
import "./surfaces/artifacts/index.tsx";
import "./surfaces/emoji/index.tsx";
import "./surfaces/palette/index.tsx";
import "./surfaces/dialogs/index.tsx";
import "./surfaces/settings/index.tsx";

import * as legacy_column from "./bridge/legacy_column.ts";
import * as registry from "./commands/registry.ts";
import * as frame from "./frame/frame.tsx";
import {commands as artifacts_commands} from "./surfaces/artifacts/commands.ts";
import {commands as band_commands} from "./surfaces/band/commands.ts";
import {commands as changes_commands} from "./surfaces/changes/commands.ts";
import {commands as checklist_commands} from "./surfaces/checklist/commands.ts";
import {commands as column_commands} from "./surfaces/column/commands.ts";
import {commands as composer_commands} from "./surfaces/composer/commands.ts";
import {commands as dialogs_commands} from "./surfaces/dialogs/commands.ts";
import {commands as emoji_commands} from "./surfaces/emoji/commands.ts";
import {commands as files_commands} from "./surfaces/files/commands.ts";
import {commands as header_commands} from "./surfaces/header/commands.ts";
import {commands as home_commands} from "./surfaces/home/commands.ts";
import {commands as lists_commands} from "./surfaces/lists/commands.ts";
import {commands as nav_commands} from "./surfaces/nav/commands.ts";
import {commands as palette_commands} from "./surfaces/palette/commands.ts";
import {commands as readme_commands} from "./surfaces/readme/commands.ts";
import {commands as settings_commands} from "./surfaces/settings/commands.ts";
import {commands as trace_commands} from "./surfaces/trace/commands.ts";

export function initialize(): void {
    registry.register([
        ...nav_commands,
        ...header_commands,
        ...band_commands,
        ...composer_commands,
        ...column_commands,
        ...checklist_commands,
        ...lists_commands,
        ...trace_commands,
        ...home_commands,
        ...readme_commands,
        ...files_commands,
        ...changes_commands,
        ...artifacts_commands,
        ...emoji_commands,
        ...palette_commands,
        ...dialogs_commands,
        ...settings_commands,
    ]);
    legacy_column.initialize();
    frame.mount();
}
