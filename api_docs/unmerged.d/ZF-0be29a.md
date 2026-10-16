**Feature level ZF-0be29a**

* [`GET /armada/plugins`](/api/get-armada-plugins),
  [`POST /armada/plugins`](/api/install-armada-plugin),
  [`PATCH /armada/plugins/{install_id}`](/api/update-armada-plugin),
  [`DELETE /armada/plugins/{install_id}`](/api/remove-armada-plugin),
  [`POST /armada/plugins/{install_id}/restore`](/api/restore-armada-plugin):
  New endpoints for Armada's plugins: list the organization's plugins,
  install one from a repository at a pinned commit, change its settings,
  secrets and where it shows, remove it, and undo a removal within a
  week. A plugin's secrets are never returned. A plugin comes from a
  repository on GitHub or on this server, at a commit and a directory of
  it, or is a remote service; it may be a draft, seen only by the
  person the agent makes it for; and each of its widgets has the team's
  default, shown in the nav or offered in its "+" menu.
* [`POST /armada/plugins/{install_id}/links`](/api/add-armada-plugin-link),
  [`DELETE /armada/plugins/{install_id}/links/{link_id}`](/api/remove-armada-plugin-link):
  New endpoints to link a plugin to a channel or to a topic, and to
  unlink it.
