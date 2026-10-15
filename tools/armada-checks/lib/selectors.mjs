// Every selector the checks depend on, in one place.
//
// Each entry is a list of candidates, most-preferred first. The page helper
// resolves a name to the visible matches of the first candidate that has any.
// When the rebuild renames or moves an element, add the new selector at the
// front of its list; the checks themselves should not need editing.
export const SELECTORS = {
    // The frame (F5): every cell of the grid and the column, by the
    // data-armada hook the template and frame/ write. Later packages draw
    // inside these and never rename them.
    frame: ["[data-armada=frame]"],
    nav: ["[data-armada=nav]"],
    navTop: ["[data-armada=nav-top]"],
    plane: ["[data-armada=plane]"],
    header: ["[data-armada=header]"],
    headerLine1: ["[data-armada=header-line1]"],
    headerLine2: ["[data-armada=header-line2]"],
    // The first glyph of each line: the path or title, the first view or
    // the sentence (frame-stability reads the first text inside).
    headerTitle: [
        "[data-armada=header-title]",
        "#message_view_header .message-header-navbar-title",
    ],
    headerSentence: ["[data-armada=sentence]"],
    headerViews: ["[data-armada=views]", "[data-armada=header] .legacy-workspace-tabs"],
    // The worktree's facts once they have arrived (B2 fixes their lateness).
    headerValues: [
        "[data-armada=header-values]",
        "[data-armada=header] .legacy-commitline-sha:not(:empty)",
    ],
    more: ["[data-armada=more]"],
    pane: ["[data-armada=pane]"],
    lane: ["[data-armada=lane]"],
    // Home's lane while Inbox and Recent stand in for Home (B5).
    inboxPane: ["#inbox-view .inbox-container", "#recent_view .recent_view_container"],
    feedScroller: ["[data-armada=feed-scroller]"],
    band: ["[data-armada=band]"],
    composerTrack: ["[data-armada=composer]"],
    // The band and the field's track together: what the conversation is
    // read above.
    foot: ["[data-armada=foot]"],
    column: ["[data-armada=column]"],
    columnHeading: ["[data-armada=column] .armada-column-heading > .armada-heading"],
    columnSection: ["[data-armada=column] .armada-section"],
    // A content of the column's host shown as a sheet: today the old rail.
    sheet: ["[data-armada=sheet][data-state=open]", "[data-armada=sheet]"],

    // Application chrome, by the names the earlier checks use.
    // Until the application has loaded. There is no splash since F5 (the
    // frame is the first paint); ui_init.js still marks the end of loading
    // with this class.
    splash: ["body:not(:has(#app-loading.loaded))"],
    navbar: ["[data-armada=header]", "#navbar-fixed-container"],
    leftSidebar: ["[data-armada=nav]", "#left-sidebar"],
    // The column's heading: the part of the column that stands on every
    // view (Files and Changes fold the rest into it).
    rightSidebar: [
        "[data-armada=column] .armada-column-heading > .armada-heading",
        "#right-sidebar",
    ],
    middleColumn: ["[data-armada=pane]", ".app-main .column-middle"],
    compose: ["[data-armada=foot]", "#compose"],
    composeBox: ["#compose-content"],
    composeTextarea: ["textarea#compose-textarea"],
    // Buttons of the open composer that the console tour presses, and the
    // ones it must not: file choosers and call links leave the page.
    composeControls: [
        "#compose .compose_control_button, #compose .expand-composebox-button, #compose #send_later",
    ],
    composeControlsSkipped: [
        "#compose .compose_upload_file, #compose .video_link, #compose .audio_link, #compose .file_input",
    ],
    // The development build shows uncaught exceptions in an overlay that
    // covers the page; the tour clears it so that later steps can be driven.
    devErrorOverlay: [".blueslip-error-container > *"],
    alertBanner: ["#navbar_alerts_wrapper .banner-close-action"],

    // Topic bar and its tabs.
    // A worktree's bar is the header since F5: one element on every place.
    topicBar: ["[data-armada=header]", ".legacy-topic-bar"],
    tabStrip: ["[data-armada=views]", "[data-armada=header] .legacy-workspace-tabs"],
    tabChat: ["[data-armada=header] .legacy-workspace-mode-tab[data-legacy-tab=chat]"],
    tabReadme: ["[data-armada=header] .legacy-workspace-mode-tab[data-legacy-tab=readme]"],
    tabFiles: ["[data-armada=header] .legacy-workspace-mode-tab[data-legacy-tab=files]"],
    workspacePane: ["#legacy-workspace-pane"],
    runtimePicker: [".legacy-runtime-menu-trigger"],
    workDetails: [".legacy-topic-header-git-control", ".legacy-topic-header-commitline"],

    // Feed.
    // The header of a run of messages of one topic in a multi-topic view.
    topicGroup: [".focused-message-list .message_header"],
    // Anything that covers the application: settings, shortcuts, a modal.
    overlay: [".overlay.show", ".micromodal.modal--open"],
    messageExpander: [".focused-message-list .message_expander"],
    stickyHeader: ["#message-lists-container .sticky_header"],
    selectedRow: [".focused-message-list .message_row.selected_message"],
    messageRow: [".focused-message-list .message_row", "#message-lists-container .message_row"],
    messageContent: [
        ".focused-message-list .message_row .message_content",
        "#message-lists-container .message_row .message_content",
    ],
    messageActions: [".focused-message-list .message_row .message-actions-menu-button"],
    threadLaunch: [".message-thread-reply-launch", ".focused-message-list .message_thread_button"],
    threadPane: ["#message-thread-pane", ".message-thread-pane", ".message-thread-panel"],

    // Home views.
    inboxView: ["#inbox-view"],
    recentView: ["#recent_view"],

    // Left sidebar.
    channelRow: ["#stream_filters li.narrow-filter > .channel-header"],
    channelName: ["#stream_filters li.narrow-filter > .channel-header .stream-name"],
    topicRow: ["#stream_filters li.topic-list-item"],
    topicName: ["#stream_filters li.topic-list-item .sidebar-topic-name"],
    sidebarFilter: ["#left-sidebar .left-sidebar-search-input"],
    viewsToggle: ["#views-label-container .left-sidebar-title", "#views-label-container"],
    viewsMenu: ["#views-label-container .left-sidebar-navigation-menu-icon"],
    dmHeader: ["#direct-messages-section-header"],
    sectionHeader: ["#stream_filters .stream-list-subsection-header"],
    topicMenu: ["#stream_filters li.topic-list-item .topic-sidebar-menu-icon"],

    // A message that could not be sent, and its way to try again.
    failedRetry: [".focused-message-list .refresh-failed-message"],
    composeSend: ["#compose-send-button"],
    // The control that addresses the composer's message to the agent.
    composeAgent: ["#compose .compose-legacy-button"],
    // The picker `:` opens for the selected message: today the emoji
    // picker, in the rebuild the four reactions.
    reactionPicker: ["[data-armada=reactions]", ".emoji-picker-popover"],
    // What the rebuild adds; nothing matches today.
    commandPalette: ["[data-armada=palette]"],
    checklist: ["[data-armada=checklist]"],
    settingsView: ["[data-armada=settings]", "#settings_overlay_container.show"],
    findField: ["[data-armada=find] input"],

    // Header controls (the old navbar's, on the header's first line).
    helpMenu: ["#help-menu .header-button", "#help-menu"],
    gearMenu: ["#settings-dropdown", "#gear-menu"],
    personalMenu: ["#personal-menu .header-button", "#personal-menu"],

    // Anything that counts as an open menu or popover.
    popover: [
        "[data-tippy-root] .tippy-box",
        ".popover-menu",
        "#legacy-runtime-menu-popover",
        ".legacy-popover",
        "[popover]:popover-open",
    ],

    // An open menu, as opposed to a tooltip.
    menu: ["[data-tippy-root] .popover-menu", ".popover-menu", "#legacy-runtime-menu-popover"],

    // The list of rows of the view in front, and the agent's message
    // that is being written.
    feedList: [".focused-message-list"],
    agentLiveRow: [".focused-message-list .message_row.legacy-active-message"],
    // The mark that says a message to the agent was accepted; the rebuild
    // names it [data-run-ack].
    agentAck: [
        "[data-run-ack]",
        "[data-legacy-ack]",
        ".legacy-run-pending",
        ".legacy-working",
        ".legacy-liveness-active",
        ".legacy-active-message",
        ".legacy-live-progress",
    ],

    runtimeMenu: ["#legacy-runtime-menu-popover"],
    workDetailsPanel: [".legacy-workbench-popover-open"],
    typingNotice: ["#typing_notifications .typing_notification"],

    // A client-side acknowledgement that a message addressed to the agent
    // was accepted: any of these becoming visible before server data counts.
    armadaAck: [
        "[data-legacy-ack]",
        ".legacy-run-pending",
        ".legacy-working",
        ".legacy-liveness-active",
        ".legacy-active-message",
        ".legacy-live-progress",
    ],
};

// Where the checks' real sends go: a topic of this run's own. A topic the
// suite sent to on every run grew past the old view's render window
// (about 400 messages), and from then on every send re-rendered the view
// and showed "Scroll down to view your message"; a topic that holds one
// run's sends never grows. The harness writes its first message the first
// time a check opens it (CheckContext.ensure_probe).
const RUN_STAMP = new Date().toISOString().replaceAll(/\D/g, "").slice(0, 14);
export const PROBE = {
    stream_id: 2,
    topic: `armada-checks-${RUN_STAMP}-${process.pid}`,
};

// Fixture locations (var/armada-overhaul/SEED.md).
export const TOPICS = {
    short: "#narrow/channel/22-click/topic/option-groups",
    other: "#narrow/channel/22-click/topic/shell-completion",
    long: "#narrow/channel/22-click/topic/demo-long-topic",
    live: "#narrow/channel/22-click/topic/demo-live-run",
    wall: "#narrow/channel/22-click/topic/demo-wall-monitors",
    completed: "#narrow/channel/22-click/topic/demo-completed-run",
    threads: "#narrow/channel/22-click/topic/demo-threads",
    // Messages the checks send go here and nowhere else.
    probe: `#narrow/channel/${PROBE.stream_id}-sandbox/topic/${PROBE.topic}`,
    plainChannel: "#narrow/channel/3-Verona",
    // The semantics checks' own topic (lib/fixtures.mjs) and its channel,
    // which holds several topics.
    pins: "#narrow/channel/2-sandbox/topic/armada-pins",
    pinsChannel: "#narrow/channel/2-sandbox",
    dm: "#narrow/dm/10-King-Hamlet",
    inbox: "#inbox",
    recent: "#recent",
};
