from datetime import datetime, timedelta

import time_machine
from django.conf import settings
from django.contrib.sessions.models import Session
from django.utils.timezone import now as timezone_now

from zerver.lib.armada_sessions import SESSION_RENEWED_KEY
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import queries_captured

A_YEAR = timedelta(days=365)


class PersistentSessionTest(ZulipTestCase):
    def session_expiry(self) -> datetime:
        session_key = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        return Session.objects.get(session_key=session_key).expire_date

    def test_signing_in_keeps_the_session_for_a_year(self) -> None:
        self.assertEqual(settings.SESSION_COOKIE_AGE, A_YEAR.total_seconds())
        self.assertFalse(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)
        now = timezone_now().replace(microsecond=0)
        with time_machine.travel(now, tick=False):
            result = self.login_with_return(self.example_email("hamlet"))
        self.assertEqual(result.status_code, 302)
        cookie = result.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual(cookie["max-age"], int(A_YEAR.total_seconds()))
        self.assertEqual(self.session_expiry(), now + A_YEAR)

    def test_a_session_in_use_is_renewed_once_a_day(self) -> None:
        start = timezone_now().replace(microsecond=0)
        with time_machine.travel(start, tick=False):
            self.login_with_return(self.example_email("hamlet"))
        self.assertEqual(self.session_expiry(), start + A_YEAR)

        # Within a day of the last renewal, using the session writes
        # nothing: the expiry stays and no cookie is sent.
        with time_machine.travel(start + timedelta(hours=23), tick=False):
            result = self.client_get("/json/users/me")
        self.assert_json_success(result)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, result.cookies)
        self.assertEqual(self.session_expiry(), start + A_YEAR)

        # A day on, the first request moves the expiry a year on from
        # now and sends the cookie again with the whole lifetime.
        later = start + timedelta(days=200)
        with time_machine.travel(later, tick=False):
            result = self.client_get("/json/users/me")
        self.assert_json_success(result)
        cookie = result.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual(cookie["max-age"], int(A_YEAR.total_seconds()))
        self.assertEqual(self.session_expiry(), later + A_YEAR)
        session = Session.objects.get(session_key=cookie.value).get_decoded()
        self.assertEqual(session[SESSION_RENEWED_KEY], int(later.timestamp()))

        # A session that was in use stays signed in past the first year.
        with time_machine.travel(start + A_YEAR + timedelta(days=30), tick=False):
            result = self.client_get("/json/users/me")
        self.assert_json_success(result)

    def test_the_first_request_after_signing_in_writes_nothing(self) -> None:
        # Signing in stamps the session, so the request after it does not
        # save it a second time.
        self.login("hamlet")
        expiry = self.session_expiry()
        result = self.client_get("/json/users/me")
        self.assert_json_success(result)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, result.cookies)
        self.assertEqual(self.session_expiry(), expiry)

    def test_an_unused_session_still_expires(self) -> None:
        start = timezone_now().replace(microsecond=0)
        with time_machine.travel(start, tick=False):
            self.login_with_return(self.example_email("hamlet"))
        with time_machine.travel(start + A_YEAR + timedelta(days=1), tick=False):
            result = self.client_get("/json/users/me")
        self.assertEqual(result.status_code, 401)

    def test_an_api_request_does_not_read_the_session(self) -> None:
        # A request authenticated by an API key that still carries a
        # session cookie neither reads nor renews that session.
        start = timezone_now().replace(microsecond=0)
        with time_machine.travel(start, tick=False):
            self.login_with_return(self.example_email("hamlet"))
        hamlet = self.example_user("hamlet")
        with (
            time_machine.travel(start + timedelta(days=2), tick=False),
            queries_captured() as queries,
        ):
            result = self.api_get(hamlet, "/api/v1/users/me")
        self.assert_json_success(result)
        self.assertFalse([query for query in queries if "django_session" in query.sql])
        self.assertNotIn(settings.SESSION_COOKIE_NAME, result.cookies)
        self.assertEqual(self.session_expiry(), start + A_YEAR)

    def test_requests_without_a_signed_in_session_get_no_session(self) -> None:
        sessions = Session.objects.count()
        hamlet = self.example_user("hamlet")
        result = self.api_get(hamlet, "/api/v1/users/me")
        self.assert_json_success(result)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, result.cookies)

        result = self.client_get("/login/")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, result.cookies)
        self.assertEqual(Session.objects.count(), sessions)

    def test_logging_out_ends_the_session(self) -> None:
        start = timezone_now().replace(microsecond=0)
        with time_machine.travel(start, tick=False):
            self.login_with_return(self.example_email("hamlet"))
        with time_machine.travel(start + timedelta(days=2), tick=False):
            self.client_post("/accounts/logout/")
            result = self.client_get("/json/users/me")
        self.assertEqual(result.status_code, 401)
