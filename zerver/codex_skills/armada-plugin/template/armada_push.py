"""push(): give Armada a widget's document, or new data for it, as the
plugin's own bot. Armada sends it on to whoever can see the widget, at
most once every two seconds."""

import json
import os
from typing import Any

import zulip

_client: zulip.Client | None = None


def client() -> zulip.Client:
    """The plugin's own bot, which pushes widgets and posts in its linked
    topics (client().send_message(...))."""
    global _client
    if _client is None:
        _client = zulip.Client(
            email=os.environ["ARMADA_EMAIL"],
            api_key=os.environ["ARMADA_API_KEY"],
            site=os.environ["ARMADA_SITE"],
        )
    return _client


def push(
    widget: str,
    document: dict[str, Any] | None = None,
    *,
    data: dict[str, Any] | None = None,
    link_id: int | None = None,
    item: str = "",
) -> int:
    """Push widget's whole document, or only new data for the document
    pushed before. link_id is for a widget in a linked place's column, item
    for a widget opened from a row. Returns the document's version."""
    request: dict[str, Any] = {"widget": widget}
    if document is not None:
        request["document"] = json.dumps(document)
    if data is not None:
        request["data"] = json.dumps(data)
    if link_id is not None:
        request["link_id"] = json.dumps(link_id)
    if item:
        request["item"] = item
    result = client().call_endpoint("armada/widgets", method="POST", request=request)
    if result.get("result") != "success":
        raise RuntimeError(result.get("msg", "Armada refused the push."))
    return result["version"]
