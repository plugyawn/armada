**Feature level ZF-1f71ef**

* [`PATCH /armada/widgets/{install_id}/{widget}/preference`](/api/update-armada-widget-preference),
  [`DELETE /armada/widgets/{install_id}/{widget}/preference`](/api/remove-armada-widget-preference):
  New endpoints to change how one of a plugin's widgets is drawn for the
  user alone (in their nav or not and where, their label, the figure at
  rest, the rows its facets keep, the alerts that mark their nav, its list
  in their column), and to go back to the team's view. `seen` records
  that the user has opened the widget, so that their clients agree on
  which widgets are new to them.
* [`POST /register`](/api/register-queue), [`GET /events`](/api/get-events):
  New `armada_widget_preferences` in the register response, and new
  `armada_widget_preference` events, for the user's own preferences.
