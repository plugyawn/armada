// Where the files a run writes for people to keep go. The suite runs from
// the VM's copy of the checkout, which a sync loop overwrites from the
// checkout people edit; ARMADA_HOST_CHECKOUT names that checkout, as the VM
// mounts it, and the run writes known.json, its reports and accepted
// pictures there too. Unset, or not mounted, they stay in this checkout.

import * as fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

export const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

// The host checkout, when it is set, mounted and not this checkout.
export function host_checkout({env = process.env, repo = REPO} = {}) {
    const host = env.ARMADA_HOST_CHECKOUT;
    if (!host || path.resolve(host) === repo) {
        return null;
    }
    return fs.existsSync(path.join(host, "tools/armada-checks")) ? path.resolve(host) : null;
}

// This checkout's copy of `relative`, then the host checkout's.
export function keep_targets(relative, options = {}) {
    const repo = options.repo ?? REPO;
    const host = host_checkout(options);
    return host === null
        ? [path.join(repo, relative)]
        : [path.join(repo, relative), path.join(host, relative)];
}
