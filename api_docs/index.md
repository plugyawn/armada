# The Armada API

Armada's APIs allow you to integrate other services with Armada.  This
guide should help you find the API you need:

* First, check if the tool you'd like to integrate with Armada
  [already has a native integration](/integrations/).
* Next, check if [Zapier](https://zapier.com/apps) or
  [IFTTT](https://ifttt.com/search) has an integration.
  The [Zapier integration](/integrations/zapier) and
  [IFTTT integration](/integrations/ifttt) often allow
  integrating a new service with Armada without writing any code.
* If you'd like to send content into Armada, you can
  [write a native incoming webhook integration][incoming-webhooks-overview]
  or use [the API for sending messages](/api/send-message).
* If you're building an interactive bot that reacts to activity inside
  Armada, you'll want to look at the
  [Python framework for interactive bots](/help/running-bots) or
  [real-time events API](/api/get-events).

And if you still need to build your own integration with Armada, check out
the full [REST API](/api/rest), generally starting with
[installing the API client bindings](/api/installation-instructions).

In case you already know how you want to build your integration and you're
just looking for an API key, we've got you covered [here](/api/api-keys).

[incoming-webhooks-overview]: https://zulip.readthedocs.io/en/latest/webhooks/incoming-webhooks-overview.html
