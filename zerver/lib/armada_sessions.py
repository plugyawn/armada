"""A signed-in session lasts until the person logs out (BRIEF statement 47).

A session expires settings.SESSION_COOKIE_AGE (a year by default) after it
was last saved, and Django saves a session only when its data changes: on
its own, a session signed in a year ago expires however much it is used.
Saving it on every request (SESSION_SAVE_EVERY_REQUEST) would cost a write
per request. Instead a signed-in session carries the time it was last
renewed, set when the person signs in, and the first request a day after
that stamps it again, which saves it: its expiry moves a year on, and the
browser gets the cookie again with the new lifetime. So a person who uses
Armada at least once a year is never logged out.
"""

import time
from typing import Any

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.signals import user_logged_in
from django.contrib.sessions.backends.base import SessionBase
from django.dispatch import receiver
from django.http import HttpRequest, HttpResponseBase
from django.utils.deprecation import MiddlewareMixin

SESSION_RENEWED_KEY = "armada_session_renewed"
SESSION_RENEWAL_INTERVAL_SECONDS = 24 * 60 * 60


def stamp_session(session: SessionBase) -> None:
    session[SESSION_RENEWED_KEY] = int(time.time())


@receiver(user_logged_in, dispatch_uid="armada_stamp_session_on_login")
def stamp_session_on_login(sender: Any, request: HttpRequest, **kwargs: Any) -> None:
    # Signing in saves the session anyway; stamping it then spares the
    # first request after it a second write.
    stamp_session(request.session)


def renew_session_if_due(request: HttpRequest) -> None:
    session = request.session
    # A request without a session cookie (an API key's, or one from a
    # person who is not signed in) has no session to renew, and must not
    # be given one.
    if session.session_key is None or SESSION_KEY not in session:
        return
    renewed = session.get(SESSION_RENEWED_KEY)
    if isinstance(renewed, int) and time.time() - renewed < SESSION_RENEWAL_INTERVAL_SECONDS:
        return
    stamp_session(session)


class RenewSessionMiddleware(MiddlewareMixin):
    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        # Tornado's long polls would renew from the event loop, where a
        # database write blocks every other client; the web app's own
        # requests to Django renew the session often enough.
        if not settings.RUNNING_INSIDE_TORNADO and hasattr(request, "session"):
            renew_session_if_due(request)
        return response
