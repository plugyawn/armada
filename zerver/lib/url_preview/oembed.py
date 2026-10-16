from collections import OrderedDict
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from pyoembed.data_types import get_data_type
from pyoembed.exceptions import PyOembedException
from pyoembed.parsers import get_parser
from pyoembed.providers import get_provider

from zerver.lib.url_preview.fetch import fetch
from zerver.lib.url_preview.types import UrlEmbedData, UrlOEmbedData


def get_oembed_endpoint(url: str, page: bytes | None = None) -> str | None:
    """Where url's oEmbed data is.  Without the page: the endpoint of
    a provider pyoembed knows.  With it: the endpoint the page names
    for itself (oEmbed discovery), for any other site.  pyoembed's own
    discovery fetched the page again, with no timeout, no size limit
    and outside the preview session; its providers list is used here,
    and every request is made by fetch()."""
    # BeautifulSoup, which pyoembed's discovery imports too, is
    # imported here, as in zerver/lib/url_preview/parsers/base.py.
    from bs4 import BeautifulSoup
    from bs4.element import Tag
    from pyoembed.providers.autodiscover import AutoDiscoverProvider

    provider = get_provider(url)
    known = provider is not None and not isinstance(provider, AutoDiscoverProvider)
    if page is None:
        return provider.oembed_url(url) if known else None
    if known:
        return None
    soup = BeautifulSoup(page, "lxml")
    # We prefer json over xml, as pyoembed does.
    link = soup.find("link", type="application/json+oembed", href=True) or soup.find(
        "link", type="text/xml+oembed", href=True
    )
    if not isinstance(link, Tag) or not isinstance(link["href"], str):
        return None
    return urljoin(url, link["href"])


def get_oembed_data(
    url: str,
    maxwidth: int = 640,
    maxheight: int = 480,
    page: bytes | None = None,
    until: float | None = None,
) -> UrlEmbedData | None:
    endpoint = get_oembed_endpoint(url, page)
    if endpoint is None:
        return None

    # The size we would show, in the endpoint's query, as pyoembed
    # asks for it.
    scheme, netloc, path, query_string, fragment = urlsplit(endpoint)
    query_params = OrderedDict(parse_qsl(query_string))
    query_params["maxwidth"] = str(maxwidth)
    query_params["maxheight"] = str(maxheight)
    endpoint = urlunsplit((scheme, netloc, path, urlencode(query_params, True), fragment))

    try:
        response = fetch(endpoint, until=until)
    except requests.exceptions.RequestException:
        return None
    if response is None or response.content_type is None:
        return None
    parser = get_parser(response.content_type)
    if parser is None:
        return None
    try:
        data = parser.content_parse(response.text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data_type = get_data_type(data)
    if data_type is None:
        return None
    try:
        data_type.validate_data(data)
    except PyOembedException:
        return None

    oembed_resource_type = data.get("type", "")
    image = data.get("url", data.get("image"))
    thumbnail = data.get("thumbnail_url")
    html = data.get("html", "")
    if oembed_resource_type == "photo" and image:
        return UrlOEmbedData(
            image=image,
            type="photo",
            title=data.get("title"),
            description=data.get("description"),
        )

    if oembed_resource_type == "video" and html and thumbnail:
        return UrlOEmbedData(
            image=thumbnail,
            type="video",
            html=strip_cdata(html),
            title=data.get("title"),
            description=data.get("description"),
        )

    # Otherwise, use the title/description from pyembed as the basis
    # for our other parsers
    return UrlEmbedData(
        title=data.get("title"),
        description=data.get("description"),
    )


def strip_cdata(html: str) -> str:
    # Work around a bug in SoundCloud's XML generation:
    # <html>&lt;![CDATA[&lt;iframe ...&gt;&lt;/iframe&gt;]]&gt;</html>
    if html.startswith("<![CDATA[") and html.endswith("]]>"):
        html = html[9:-3]
    return html
