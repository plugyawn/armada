import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import path from "node:path";
import {test} from "node:test";

import {REPO, host_checkout, keep_targets} from "../lib/host.mjs";

test("REPO is the checkout this harness is in", () => {
    assert.ok(fs.existsSync(path.join(REPO, "tools/armada-checks/run.mjs")));
});

test("files go to this checkout, and to the host checkout when it is mounted", () => {
    const host = fs.mkdtempSync(path.join(os.tmpdir(), "armada-checks-host-"));
    try {
        const repo = "/srv/checkout";
        assert.equal(host_checkout({env: {}, repo}), null);
        assert.equal(host_checkout({env: {ARMADA_HOST_CHECKOUT: repo}, repo}), null);
        // Set but not mounted: nothing there to keep files in.
        assert.equal(host_checkout({env: {ARMADA_HOST_CHECKOUT: host}, repo}), null);
        assert.deepEqual(
            keep_targets("var/report.json", {env: {ARMADA_HOST_CHECKOUT: host}, repo}),
            ["/srv/checkout/var/report.json"],
        );
        fs.mkdirSync(path.join(host, "tools/armada-checks"), {recursive: true});
        assert.equal(host_checkout({env: {ARMADA_HOST_CHECKOUT: `${host}/`}, repo}), host);
        assert.deepEqual(
            keep_targets("var/report.json", {env: {ARMADA_HOST_CHECKOUT: host}, repo}),
            ["/srv/checkout/var/report.json", path.join(host, "var/report.json")],
        );
        assert.deepEqual(keep_targets("known.json", {env: {}}), [path.join(REPO, "known.json")]);
        // From the run's own environment: whatever it names, or nothing.
        const found = host_checkout();
        assert.ok(found === null || typeof found === "string");
    } finally {
        fs.rmSync(host, {recursive: true});
    }
});
