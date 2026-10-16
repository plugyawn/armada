import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from django.conf import settings
from typing_extensions import override
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from version import ZULIP_VERSION
from zerver.lib.outgoing_http import OutgoingSession
from zerver.lib.pysa import mark_sanitized

# Use Chrome User-Agent, since some sites refuse to work on old browsers
ZULIP_URL_PREVIEW_USER_AGENT = (
    f"Mozilla/5.0 (compatible; ZulipURLPreview/{ZULIP_VERSION}; +{settings.ROOT_DOMAIN_URI})"
)
HEADERS = {"User-Agent": ZULIP_URL_PREVIEW_USER_AGENT}

# Every request made for a link's preview -- the page, and its oEmbed
# endpoint -- goes through PreviewSession, so through the outgoing
# proxy (Smokescreen in production, which refuses private and
# internal addresses), and is bounded: each wait on the network
# (connecting, or the next bytes) by TIMEOUT, the reading of all of a
# link's requests together by FETCH_SECONDS (see deadline()), the
# redirects a request follows by MAX_REDIRECTS, and what is read of a
# body by MAX_BYTES.  One queue worker fetches a realm's previews one
# after another, so a slow or endless page must not hold all the
# others; and the Open Graph tags a preview is made from are in a
# page's <head>, well inside its first megabyte.
TIMEOUT = 5
FETCH_SECONDS = 10
MAX_BYTES = 1024 * 1024
CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 5


class PreviewSession(OutgoingSession):
    def __init__(self) -> None:
        super().__init__(role="preview", timeout=TIMEOUT, headers=HEADERS)

    @override
    def get_redirect_target(self, resp: requests.Response) -> str | None:
        # Within one request, requests would follow up to 30 redirects,
        # reading each one's body whole and waiting TIMEOUT afresh at
        # each.  fetch() follows them itself, within its limits.
        return None

    def redirect_of(self, response: requests.Response) -> str | None:
        """Where response redirects to, or None if it is not a redirect."""
        location = super().get_redirect_target(response)
        return None if location is None else urljoin(response.url, location)


def deadline() -> float:
    """The time by which a link's requests are read, to share among them."""
    return time.monotonic() + FETCH_SECONDS


@dataclass
class FetchedResponse:
    # Where the request ended, after any redirects.
    url: str
    content_type: str | None
    encoding: str | None
    # At most MAX_BYTES of the body, decoded from its transfer encoding.
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding or "utf-8", errors="replace")


def fetch(
    url: str,
    accept: Callable[[str | None], bool] = lambda content_type: True,
    until: float | None = None,
) -> FetchedResponse | None:
    """GETs url for a preview, reading its body until the time given
    (by default, FETCH_SECONDS from now), and following MAX_REDIRECTS
    redirects at most, each begun before that time.  Returns None when
    that time has passed already, when the server answers with an
    error or a redirect not followed, or when accept() refuses the
    response's content type, which is decided before any of the body
    is read.  Network errors are raised, as requests.RequestException."""
    if until is None:
        until = deadline()
    if time.monotonic() >= until:
        return None
    session = PreviewSession()
    response = session.get(mark_sanitized(url), stream=True)
    for _ in range(MAX_REDIRECTS):
        location = session.redirect_of(response)
        if location is None or time.monotonic() >= until:
            break
        response.close()
        response = session.get(mark_sanitized(location), stream=True)
    with response:
        if not response.ok or response.is_redirect:
            return None
        content_type = response.headers.get("Content-Type")
        if not accept(content_type):
            return None
        body = bytearray()
        try:
            while len(body) < MAX_BYTES and time.monotonic() < until:
                # read1 returns what one read from the network gives,
                # so a page that trickles in still meets the deadline.
                chunk = response.raw.read1(CHUNK_BYTES, decode_content=True)
                if not chunk:
                    break
                body += chunk
        except Urllib3HTTPError:
            # The read timed out or broke off: what arrived is what
            # there is.
            pass
        return FetchedResponse(
            url=response.url,
            content_type=content_type,
            encoding=response.encoding,
            body=bytes(body[:MAX_BYTES]),
        )
