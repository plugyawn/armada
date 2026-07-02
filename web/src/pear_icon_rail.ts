// The Slack-style icon rail: a fixed 64px <nav> prepended to
// .app-main, owning the left edge of the full-bleed frame. Every
// button rebinds an existing surface — the realm icon and home view,
// the DM/mentions/drafts narrows, the other-views and add-streams
// popovers (both delegated on body, so their triggers work from the
// rail), the theme setting, and the relocated personal menu — so the
// rail introduces no new navigation.

import $ from "jquery";

import render_pear_icon_rail from "../templates/pear_icon_rail.hbs";

import * as browser_history from "./browser_history.ts";
import * as channel from "./channel.ts";
import * as hashchange from "./hashchange.ts";
import {page_params} from "./page_params.ts";
import * as settings_config from "./settings_config.ts";
import {current_user, realm} from "./state_data.ts";
import * as theme from "./theme.ts";

let initialized = false;

function is_effective_theme_dark(): boolean {
    const $root = $(":root");
    if ($root.hasClass("dark-theme")) {
        return true;
    }
    if ($root.hasClass("color-scheme-automatic")) {
        return window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
    return false;
}

function toggle_theme(): void {
    const new_scheme_code = is_effective_theme_dark()
        ? settings_config.color_scheme_values.light.code
        : settings_config.color_scheme_values.dark.code;
    if (page_params.is_spectator) {
        theme.set_theme_for_spectator(new_scheme_code);
        return;
    }
    // The same PATCH the personal-menu theme radios send; the
    // resulting settings event applies the theme.
    void channel.patch({
        url: "/json/settings",
        data: {color_scheme: new_scheme_code},
    });
}

export function update_selection(): void {
    const $rail = $("#pear-icon-rail");
    if ($rail.length === 0) {
        return;
    }
    const hash = window.location.hash;
    $rail
        .find(".pear-rail-home")
        .toggleClass("selected", browser_history.is_current_hash_home_view());
    $rail
        .find(".pear-rail-dms")
        .toggleClass(
            "selected",
            hash.startsWith("#narrow/is/dm") || hash.startsWith("#narrow/dm/"),
        );
    $rail
        .find(".pear-rail-activity")
        .toggleClass("selected", hash.startsWith("#narrow/is/mentioned"));
    $rail.find(".pear-rail-files").toggleClass("selected", hash.startsWith("#drafts"));
}

export function initialize(): void {
    if (initialized) {
        return;
    }
    const $app_main = $(".app-main");
    if ($app_main.length === 0) {
        // Guarded for the node-test environment, where the app grid
        // does not exist.
        return;
    }
    initialized = true;

    $app_main.prepend(
        $(
            render_pear_icon_rail({
                realm_name: realm.realm_name,
                realm_icon_url: realm.realm_icon_url,
                user_avatar: current_user.avatar_url_medium,
            }),
        ),
    );

    // Mirrors initialize_navbar's tracking of the avatar image load,
    // which drives the loading-background removal.
    $("#pear-icon-rail img.header-button-avatar-image").on("load", (e) => {
        e.currentTarget.classList.add("avatar-loaded");
    });

    $("#pear-icon-rail").on("click", ".pear-rail-workspace, .pear-rail-home", (e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey) {
            return;
        }
        e.preventDefault();
        e.stopPropagation();
        // The same navigation as the navbar brand logo this replaces.
        hashchange.set_hash_to_home_view();
    });

    $("#pear-icon-rail").on("click", ".pear-rail-theme-toggle", (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggle_theme();
    });

    $(window).on("hashchange", () => {
        update_selection();
    });
    update_selection();
}
