// Browser launch and dev login shared by every check.
import {launch as launch_chromium} from "puppeteer";

export const BASE = process.env.ARMADA_BASE ?? "http://localhost:9991";
// The client under test, as a path on the server. The checks pin the old
// interface, at /legacy/ since "/" serves the new one to signed-in people
// (ARMADA_NEW_INTERFACE_AT_ROOT); ARMADA_APP=/beta/ points every check at
// the beta. Its views are addressed by hash after it (`${APP}#inbox`).
export const APP = new URL(process.env.ARMADA_APP ?? "/legacy/", `${BASE}/`).href;
export const VIEWPORT = {width: 1440, height: 900, deviceScaleFactor: 1};
// The checks' own user, which nobody else signs in as: `./manage.py
// armada_checks_user` creates it in the development realm.
export const USER = process.env.ARMADA_USER ?? "armada-checks@zulip.com";

export async function sleep(ms) {
    await new Promise((resolve) => {
        setTimeout(resolve, ms);
    });
}

export async function launch() {
    return await launch_chromium({
        executablePath: process.env.CHROME,
        headless: true,
        args: [
            "--no-sandbox",
            "--force-color-profile=srgb",
            `--window-size=${VIEWPORT.width},${VIEWPORT.height}`,
        ],
    });
}

// Log in through the development login page. The session cookie lives in the
// browser's default context, so every page opened afterwards is logged in.
// Returns the user's id.
export async function login(browser, email = USER) {
    const page = await browser.newPage();
    await page.setViewport(VIEWPORT);
    await page.goto(`${BASE}/devlogin/`, {waitUntil: "networkidle2"});
    const pick = (address, click) => {
        /* global document */
        const button = [...document.querySelectorAll("button, input[type=submit]")].find(
            (candidate) => (candidate.value || candidate.textContent || "").includes(address),
        );
        if (button && click) {
            button.click();
        }
        return button !== undefined;
    };
    if (!(await page.evaluate(pick, email, false))) {
        await page.close();
        throw new Error(
            `${email} has no development login at ${BASE}/devlogin/: create the checks' user with ./manage.py armada_checks_user (ARMADA_USER names another user)`,
        );
    }
    await Promise.all([
        page.waitForNavigation({waitUntil: "networkidle2"}),
        page.evaluate(pick, email, true),
    ]);
    // Whichever client the login lands on, the session is what matters.
    const user_id = await page.evaluate(
        async () => (await (await fetch("/json/users/me")).json()).user_id,
    );
    if (typeof user_id !== "number") {
        await page.close();
        throw new Error(`the development login as ${email} at ${BASE} did not start a session`);
    }
    await page.close();
    return user_id;
}
