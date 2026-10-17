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
* [`POST /armada/plugins/{install_id}/retry`](/api/retry-armada-plugin):
  New endpoint to start a plugin that failed five times in a row again
  at once. A plugin's state, in its `state` and `detail` fields, is now
  reported by the plugin host, which runs it.
* [`GET /armada/plugins/{install_id}/recent`](/api/get-armada-plugin-recent):
  New endpoint for a plugin's recent activity: who installed, changed,
  linked, removed or updated it, the tool actions people ran, and when
  its process started.
* [`POST /armada/plugins/{install_id}/links`](/api/add-armada-plugin-link),
  [`DELETE /armada/plugins/{install_id}/links/{link_id}`](/api/remove-armada-plugin-link):
  New endpoints to link a plugin to a channel or to a topic, and to
  unlink it.
* `POST /armada/runs`: New endpoint with which a plugin's own bot asks
  the agent: it starts a run, for the user who installed the plugin, in
  a place the plugin is linked to. Like Armada's other run endpoints, it
  is not yet in the API documentation.
* [`POST /armada/widgets`](/api/push-armada-widget): New endpoint with
  which a plugin's own bot pushes a document, or data, for one of its
  widgets.
* [`POST /armada/plugins/{install_id}/actions`](/api/run-armada-plugin-action):
  New endpoint to run a widget's action that calls one of the plugin's
  tools, for the user who pressed it.
* [`GET /armada/plugins/{install_id}/tools`](/api/get-armada-plugin-tools):
  New endpoint for the tools a running plugin offers the agent, and
  which of them the organization turned off.
* `POST /armada/plugins/{install_id}/mcp`, `DELETE /armada/plugins/{install_id}/mcp`:
  New endpoint with which an agent off the server, with an API key,
  uses a plugin's tools: the plugin's Model Context Protocol endpoint
  (Streamable HTTP transport), which speaks JSON-RPC as that protocol's
  specification documents. The tools the organization turned off are
  neither listed nor callable.
* [`POST /register`](/api/register-queue), [`GET /events`](/api/get-events):
  New `armada_plugins` and `armada_widgets` in the register response, and
  new `armada_plugin` and `armada_widget` events, for the organization's
  plugins and the widget documents the user may see.
