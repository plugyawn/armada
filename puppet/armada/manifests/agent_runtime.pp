# @summary What the agent needs beyond Zulip: Codex, its sandbox, its browser, its directories
#
# The Codex login is a person's, by the device-code flow, and stays out of
# configuration (docs/armada/deploy.md, 6.3).
class armada::agent_runtime {
  $codex_version = zulipconf('armada', 'codex_version', '0.157.1')
  $worktree_root = zulipconf('armada', 'worktree_root', '/srv/armada/worktrees')
  # The version Codex's own desktop runtime bundles; its Chrome for Testing
  # is the version ARMADA_BROWSER_USER_AGENT names (zerver/lib/armada_agent.py).
  $playwright_version = zulipconf('armada', 'playwright_version', '1.62.1')
  $browser_dir = zulipconf('armada', 'browser_dir', '/opt/armada-browser')

  zulip::safepackage { ['bubblewrap', 'git']: ensure => installed }

  # Ubuntu 24.04 keeps unprivileged user namespaces from Bubblewrap, which
  # runs every command an agent issues (docs/armada/deploy.md, 6.1).
  zulip::sysctl { 'armada-codex-userns':
    key     => 'kernel.apparmor_restrict_unprivileged_userns',
    value   => '0',
    order   => 90,
    comment => 'Armada: Codex sandboxes agent commands with Bubblewrap',
  }
  zulip::sysctl { 'armada-codex-userns-clone':
    key   => 'kernel.unprivileged_userns_clone',
    value => '1',
    order => 90,
  }

  # In its own prefix: Zulip replaces /srv/zulip-node when it upgrades Node.
  exec { 'armada-install-codex':
    command     => "/usr/local/bin/npm install -g --prefix /opt/codex @openai/codex@${codex_version}",
    unless      => "test \"$(/opt/codex/bin/codex --version 2>/dev/null)\" = \"codex-cli ${codex_version}\"",
    provider    => shell,
    # Codex's launcher and npm run the node Zulip installed there.
    path        => ['/usr/local/bin', '/usr/bin', '/bin'],
    environment => ['HOME=/root'],
    timeout     => 600,
  }

  # The agent's browser (docs/armada/deploy.md, 6.9): Playwright, whose MCP
  # server the agent drives, and the Chrome for Testing it pins, in their own
  # prefix like Codex. The libraries and fonts are Playwright's own list for
  # its Chromium on Ubuntu 24.04 (playwright-core's nativeDeps), less Xvfb and
  # the X bitmap fonts, which a headless browser does not use; the fonts draw
  # pages in every script, emoji included.
  $browser_packages = [
    'libasound2t64',
    'libatk-bridge2.0-0t64',
    'libatk1.0-0t64',
    'libatspi2.0-0t64',
    'libcairo2',
    'libcups2t64',
    'libdbus-1-3',
    'libdrm2',
    'libgbm1',
    'libglib2.0-0t64',
    'libnspr4',
    'libnss3',
    'libpango-1.0-0',
    'libx11-6',
    'libxcb1',
    'libxcomposite1',
    'libxdamage1',
    'libxext6',
    'libxfixes3',
    'libxkbcommon0',
    'libxrandr2',
    'libfontconfig1',
    'libfreetype6',
    'fonts-freefont-ttf',
    'fonts-ipafont-gothic',
    'fonts-liberation',
    'fonts-noto-color-emoji',
    'fonts-tlwg-loma-otf',
    'fonts-unifont',
    'fonts-wqy-zenhei',
  ]
  zulip::safepackage { $browser_packages: ensure => installed }

  exec { 'armada-install-playwright':
    command     => "/usr/local/bin/npm install -g --prefix ${browser_dir} playwright@${playwright_version}",
    unless      => "test \"$(${browser_dir}/bin/playwright --version 2>/dev/null)\" = \"Version ${playwright_version}\"",
    provider    => shell,
    path        => ['/usr/local/bin', '/usr/bin', '/bin'],
    environment => ['HOME=/root'],
    timeout     => 600,
  }
  # The full browser, not the old headless shell: the agent runs it in the
  # new headless mode, which renders as desktop Chrome does. Installed when
  # the browser this Playwright expects is missing, so a new Playwright
  # brings its own; Playwright removes the browsers no install uses.
  $browsers_env = "PLAYWRIGHT_BROWSERS_PATH=${browser_dir}/browsers"
  $chromium = "require('${browser_dir}/lib/node_modules/playwright').chromium.executablePath()"
  exec { 'armada-install-chromium':
    command     => "${browsers_env} ${browser_dir}/bin/playwright install --no-shell chromium",
    unless      => "test -x \"$(${browsers_env} node -e \"console.log(${chromium})\")\"",
    provider    => shell,
    path        => ['/usr/local/bin', '/usr/bin', '/bin'],
    environment => ['HOME=/root'],
    timeout     => 900,
    require     => [Exec['armada-install-playwright'], Zulip::Safepackage[$browser_packages]],
  }

  file { ['/srv/armada', $worktree_root, "${worktree_root}/_scratch"]:
    ensure => directory,
    owner  => 'zulip',
    group  => 'zulip',
    mode   => '0750',
  }
}
