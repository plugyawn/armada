// Browser launch and dev login shared by every check.
import {launch as launch_chromium} from "puppeteer";

export const BASE = process.env.ARMADA_BASE ?? "http://localhost:9991";
export const VIEWPORT = {width: 1440, height: 900, deviceScaleFactor: 1};
export const USER = process.env.ARMADA_USER ?? "desdemona@zulip.com";

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
    await Promise.all([
        page.waitForNavigation({waitUntil: "networkidle2"}),
        page.evaluate((address) => {
            /* global document */
            const button = [...document.querySelectorAll("button, input[type=submit]")].find(
                (candidate) => (candidate.value || candidate.textContent || "").includes(address),
            );
            button.click();
        }, email),
    ]);
    await page.waitForSelector("#left-sidebar", {visible: true});
    const user_id = await page.evaluate(
        async () => (await (await fetch("/json/users/me")).json()).user_id,
    );
    await page.close();
    return user_id;
}
