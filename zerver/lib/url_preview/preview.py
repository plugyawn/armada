import re
from collections.abc import Callable
from re import Match
from typing import Any
from urllib.parse import urljoin

import magic
import requests
from django.utils.encoding import smart_str

from zerver.lib.cache import cache_with_key, preview_url_cache_key
from zerver.lib.url_preview.fetch import FetchedResponse, deadline, fetch
from zerver.lib.url_preview.oembed import get_oembed_data
from zerver.lib.url_preview.parsers import GenericParser, OpenGraphParser
from zerver.lib.url_preview.types import UrlEmbedData, UrlOEmbedData

# Based on django.core.validators.URLValidator, with ftp support removed.
link_regex = re.compile(
    r"^(?:http)s?://"  # http:// or https://
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"  # domain...
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...or ip
    r"(?::\d+)?"  # optional port
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


def is_link(url: str) -> Match[str] | None:
    return link_regex.match(smart_str(url))


def may_be_html(content_type: str | None) -> bool:
    # Be accommodating of bad servers: assume content may be html if no content-type header
    return not content_type or content_type.startswith("text/html")


def is_html(page: FetchedResponse) -> bool:
    # Verify that the content is actually HTML if the server claims it is
    return magic.Magic(mime=True).from_buffer(page.body[:1000]).startswith("text/html")


def catch_network_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except requests.exceptions.RequestException:
            pass

    return wrapper


@catch_network_errors
@cache_with_key(preview_url_cache_key)
def get_link_embed_data(url: str, maxwidth: int = 640, maxheight: int = 480) -> UrlEmbedData | None:
    if not is_link(url):
        return None
    until = deadline()

    # A site pyoembed knows has an oEmbed endpoint of its own, asked
    # first: for a photo or a video, its answer is the whole preview.
    # Any other data it gives is a _base_ for the less sophisticated
    # techniques below, which we apply as successive fallbacks.
    data = get_oembed_data(url, maxwidth=maxwidth, maxheight=maxheight, until=until)
    if isinstance(data, UrlOEmbedData):
        return data

    # The page is read once, for all that follows.
    page = fetch(url, accept=may_be_html, until=until)
    if page is None or not is_html(page):
        return None

    if data is None:
        # Any other page may name its own oEmbed endpoint.
        data = get_oembed_data(
            url, maxwidth=maxwidth, maxheight=maxheight, page=page.body, until=until
        )
        if isinstance(data, UrlOEmbedData):
            return data
    if data is None:
        data = UrlEmbedData()

    for parser_class in (OpenGraphParser, GenericParser):
        parser = parser_class(page.body, page.content_type)
        data.merge(parser.extract_data())

    if data.image:
        data.image = urljoin(page.url, data.image)
    return data
