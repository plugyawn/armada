import secrets
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from unittest import mock

import orjson
import time_machine
from typing_extensions import override

from zerver.lib.rate_limiter import (
    RateLimitedIPAddr,
    RateLimitedObject,
    RateLimitedUser,
    RateLimiterBackend,
    RedisRateLimiterBackend,
    readable_expiry_string_for_html,
    readable_expiry_string_for_plaintext,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import ratelimit_rule

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse

RANDOM_KEY_PREFIX = secrets.token_hex(16)


class RateLimitedTestObject(RateLimitedObject):
    def __init__(
        self, name: str, rules: list[tuple[int, int]], backend: type[RateLimiterBackend]
    ) -> None:
        self.name = name
        self._rules = rules
        self._rules.sort(key=lambda x: x[0])
        super().__init__(backend)

    @override
    def key(self) -> str:
        return RANDOM_KEY_PREFIX + self.name

    @override
    def rules(self) -> list[tuple[int, int]]:
        return self._rules


class RateLimiterBackendBase(ZulipTestCase, ABC):
    backend: type[RateLimiterBackend]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.requests_record: dict[str, list[float]] = {}

    def create_object(self, name: str, rules: list[tuple[int, int]]) -> RateLimitedTestObject:
        obj = RateLimitedTestObject(name, rules, self.backend)
        obj.clear_history()

        return obj

    def make_request(
        self,
        obj: RateLimitedTestObject,
        expect_ratelimited: bool = False,
        verify_api_calls_left: bool = True,
    ) -> None:
        key = obj.key()
        if key not in self.requests_record:
            self.requests_record[key] = []

        ratelimited, _secs_to_freedom = obj.rate_limit()
        if not ratelimited:
            self.requests_record[key].append(time.time())

        self.assertEqual(ratelimited, expect_ratelimited)

        if verify_api_calls_left:
            self.verify_api_calls_left(obj)

    def verify_api_calls_left(self, obj: RateLimitedTestObject) -> None:
        now = time.time()
        with mock.patch("time.time", return_value=now):
            calls_remaining, time_till_reset = obj.api_calls_left()

        expected_calls_remaining, expected_time_till_reset = self.expected_api_calls_left(obj, now)
        self.assertEqual(expected_calls_remaining, calls_remaining)
        self.assertEqual(expected_time_till_reset, time_till_reset)

    def expected_api_calls_left(self, obj: RateLimitedTestObject, now: float) -> tuple[int, float]:
        longest_rule = obj.get_rules()[-1]
        max_window, max_calls = longest_rule
        history = self.requests_record.get(obj.key())
        if history is None:
            return max_calls, 0
        history.sort()

        return self.api_calls_left_from_history(history, max_window, max_calls, now)

    @abstractmethod
    def api_calls_left_from_history(
        self, history: list[float], max_window: int, max_calls: int, now: float
    ) -> tuple[int, float]:
        """
        This depends on the algorithm used in the backend, and should be defined by the test class.
        """
        raise NotImplementedError

    def test_hit_ratelimits(self) -> None:
        obj = self.create_object("test", [(2, 3)])

        start_time = time.time()
        for i in range(3):
            with mock.patch("time.time", return_value=start_time + i * 0.1):
                self.make_request(obj, expect_ratelimited=False)

        with mock.patch("time.time", return_value=start_time + 0.4):
            self.make_request(obj, expect_ratelimited=True)

        with mock.patch("time.time", return_value=start_time + 2.01):
            self.make_request(obj, expect_ratelimited=False)

    def test_clear_history(self) -> None:
        obj = self.create_object("test", [(2, 3)])
        start_time = time.time()
        for i in range(3):
            with mock.patch("time.time", return_value=start_time + i * 0.1):
                self.make_request(obj, expect_ratelimited=False)
        with mock.patch("time.time", return_value=start_time + 0.4):
            self.make_request(obj, expect_ratelimited=True)

        obj.clear_history()
        self.requests_record[obj.key()] = []
        for i in range(3):
            with mock.patch("time.time", return_value=start_time + i * 0.1):
                self.make_request(obj, expect_ratelimited=False)

    def test_block_unblock_access(self) -> None:
        obj = self.create_object("test", [(2, 5)])
        start_time = time.time()

        obj.block_access(1)
        with mock.patch("time.time", return_value=start_time):
            self.make_request(obj, expect_ratelimited=True, verify_api_calls_left=False)

        obj.unblock_access()
        with mock.patch("time.time", return_value=start_time):
            self.make_request(obj, expect_ratelimited=False, verify_api_calls_left=False)

    def test_api_calls_left(self) -> None:
        obj = self.create_object("test", [(2, 5), (3, 6)])
        start_time = time.time()

        # Check the edge case when no requests have been made yet.
        with mock.patch("time.time", return_value=start_time):
            self.verify_api_calls_left(obj)

        with mock.patch("time.time", return_value=start_time):
            self.make_request(obj)

        # Check the correct default values again, after the reset has happened on the first rule,
        # but not the other.
        with mock.patch("time.time", return_value=start_time + 2.1):
            self.make_request(obj)


class RedisRateLimiterBackendTest(RateLimiterBackendBase):
    backend = RedisRateLimiterBackend

    @override
    def api_calls_left_from_history(
        self, history: list[float], max_window: int, max_calls: int, now: float
    ) -> tuple[int, float]:
        reset_time = 0.0
        for timestamp in history:
            reset_time = max(reset_time, timestamp) + (max_window / max_calls)

        calls_left = (now + max_window - reset_time) * max_calls // max_window
        calls_left = int(calls_left)

        return calls_left, reset_time - now

    def test_block_access(self) -> None:
        """
        This test cannot verify that the user will get unblocked
        after the correct amount of time, because that event happens
        inside Redis, so we're not able to mock the timer. Making the test
        sleep for 1s is also too costly to be worth it.
        """
        obj = self.create_object("test", [(2, 5)])

        obj.block_access(1)
        self.make_request(obj, expect_ratelimited=True, verify_api_calls_left=False)


class RateLimitedObjectsTest(ZulipTestCase):
    def test_user_rate_limits(self) -> None:
        user_profile = self.example_user("hamlet")
        user_profile.rate_limits = "1:3,2:4"
        obj = RateLimitedUser(user_profile)

        self.assertEqual(obj.get_rules(), [(1, 3), (2, 4)])

    def test_ratelimit_rule(self) -> None:
        user_profile = self.example_user("hamlet")
        with ratelimit_rule(1, 2), ratelimit_rule(4, 5, domain="some_new_domain"):
            with ratelimit_rule(10, 100, domain="some_new_domain"):
                obj = RateLimitedUser(user_profile)

                self.assertEqual(obj.get_rules(), [(1, 2)])
                obj.domain = "some_new_domain"
                self.assertEqual(obj.get_rules(), [(4, 5), (10, 100)])

            self.assertEqual(obj.get_rules(), [(4, 5)])

    def test_empty_rules_edge_case(self) -> None:
        obj = RateLimitedTestObject("test", rules=[], backend=RedisRateLimiterBackend)
        self.assertEqual(obj.get_rules(), [(1, 9999)])

    def test_ip_bucket_key(self) -> None:
        ipv6 = "2001:0db8::ce1:12:8a2e:0370"
        ipv4 = "192.168.0.123"
        domain = "api_by_ip"

        self.assertEqual(
            RateLimitedIPAddr(ipv4, domain=domain).key(), f"RateLimitedIPAddr:<{ipv4}>:{domain}"
        )

        # Here we check that each bucket key, given a different network prefix,
        # is as expected.
        # Although /64 is the only used prefix, we still test other prefixes
        # to ensure correctness and also in case we decide to use smaller prefixes
        # in future.

        # Note that the leading zero in :0db8: is omitted
        self.assertEqual(
            RateLimitedIPAddr(ipv6, domain=domain, ipv6_network_prefix=64).key(),
            "RateLimitedIPAddr:<2001:db8::/64>:api_by_ip",
        )
        self.assertEqual(
            RateLimitedIPAddr(ipv6, domain=domain, ipv6_network_prefix=56).key(),
            "RateLimitedIPAddr:<2001:db8::/56>:api_by_ip",
        )
        self.assertEqual(
            RateLimitedIPAddr(ipv6, domain=domain, ipv6_network_prefix=48).key(),
            "RateLimitedIPAddr:<2001:db8::/48>:api_by_ip",
        )

        # Two IPv6 with the SAME network portion (identified by the prefix)
        # should belong to the SAME bucket.
        self.assertEqual(
            RateLimitedIPAddr(
                "2001:0db8:ce1:12::8a2e:0370", domain=domain, ipv6_network_prefix=64
            ).key(),
            RateLimitedIPAddr(
                "2001:0db8:ce1:12::8a2e:045f", domain=domain, ipv6_network_prefix=64
            ).key(),
        )
        self.assertEqual(
            RateLimitedIPAddr(
                "2001:0db8:7a2e:ccd1::0370", domain=domain, ipv6_network_prefix=56
            ).key(),
            RateLimitedIPAddr(
                "2001:0db8:7a2e:ccf2::045f", domain=domain, ipv6_network_prefix=56
            ).key(),
        )
        self.assertEqual(
            RateLimitedIPAddr(
                "2001:0db8:ce1:12::8a2e:0370", domain=domain, ipv6_network_prefix=48
            ).key(),
            RateLimitedIPAddr(
                "2001:0db8:ce1:13::8a2e:045f", domain=domain, ipv6_network_prefix=48
            ).key(),
        )

        # Two IPv6 with DIFFERENT network portions (identified by the prefix)
        # should belong to DIFFERENT buckets.
        self.assertNotEqual(
            RateLimitedIPAddr(
                "2001:0db8:ce1:12::8a2e:0370", domain=domain, ipv6_network_prefix=64
            ).key(),
            RateLimitedIPAddr(
                "2001:0db8:ce1:13::8a2e:045f", domain=domain, ipv6_network_prefix=64
            ).key(),
        )
        self.assertNotEqual(
            RateLimitedIPAddr(
                "2001:0db8:7a2e:ccd1::0370", domain=domain, ipv6_network_prefix=56
            ).key(),
            RateLimitedIPAddr(
                "2001:0db8:7a2e:c1f2::045f", domain=domain, ipv6_network_prefix=56
            ).key(),
        )
        self.assertNotEqual(
            RateLimitedIPAddr(
                "2001:0db8:12::8a2e:0370", domain=domain, ipv6_network_prefix=48
            ).key(),
            RateLimitedIPAddr(
                "2001:0db8:13::8a2e:045f", domain=domain, ipv6_network_prefix=48
            ).key(),
        )


class RateLimiterStringFormattingTest(ZulipTestCase):
    def test_formatting_for_html(self) -> None:
        self.assertEqual(readable_expiry_string_for_html(60), "60\xa0seconds")
        self.assertEqual(readable_expiry_string_for_html(61), "1\xa0minute")
        self.assertEqual(readable_expiry_string_for_html(90), "1\xa0minute")
        self.assertEqual(readable_expiry_string_for_html(121), "2\xa0minutes")
        self.assertEqual(readable_expiry_string_for_html(86400), "23\xa0hours, 59\xa0minutes")
        self.assertEqual(readable_expiry_string_for_html(90000), "1\xa0day")

    def test_formatting_for_plaintext(self) -> None:
        self.assertEqual(readable_expiry_string_for_plaintext(60), "60 seconds")
        self.assertEqual(readable_expiry_string_for_plaintext(61), "1 minute")
        self.assertEqual(readable_expiry_string_for_plaintext(90), "1 minute")
        self.assertEqual(readable_expiry_string_for_plaintext(121), "2 minutes")
        self.assertEqual(readable_expiry_string_for_plaintext(86400), "23 hours, 59 minutes")
        self.assertEqual(readable_expiry_string_for_plaintext(90000), "1 day")


class EventQueueBudgetTest(ZulipTestCase):
    """Long polls and registers count against their own per-user
    budgets, so that a client stuck in a loop of either cannot use up
    the budget its person needs for sending and everything else."""

    def send_message(self) -> "TestHttpResponse":
        return self.client_post(
            "/json/messages",
            {
                "type": "stream",
                "to": orjson.dumps("Verona").decode(),
                "topic": "budget",
                "content": "still sending",
            },
        )

    @ratelimit_rule(60, 2, domain="api_by_user")
    @ratelimit_rule(60, 3, domain="register_by_user")
    def test_register_storm_cannot_block_sends(self) -> None:
        hamlet = self.example_user("hamlet")
        for domain in ("api_by_user", "register_by_user"):
            RateLimitedUser(hamlet, domain=domain).clear_history()
        self.login_user(hamlet)

        with (
            time_machine.travel(time.time(), tick=False),
            mock.patch("zerver.views.events_register.do_events_register", return_value={}),
        ):
            for _ in range(3):
                self.assert_json_success(self.client_post("/json/register"))
            with self.assertLogs("zerver.lib.rate_limiter", level="WARNING") as logs:
                result = self.client_post("/json/register")
            self.assertEqual(result.status_code, 429)
            self.assertEqual(
                logs.output,
                [
                    "WARNING:zerver.lib.rate_limiter:429 to the web client:"
                    f" user {hamlet.id}, POST /json/register, register_by_user,"
                    " retry after 20.00s"
                ],
            )

            for _ in range(2):
                self.assert_json_success(self.send_message())
            with self.assertLogs("zerver.lib.rate_limiter", level="WARNING"):
                self.assertEqual(self.send_message().status_code, 429)

    @ratelimit_rule(60, 2, domain="api_by_user")
    @ratelimit_rule(60, 2, domain="events_by_user")
    def test_long_polls_have_their_own_budget(self) -> None:
        # A guest asking for every public channel's events is refused
        # before any queue is touched, after the rate limit is applied.
        polonius = self.example_user("polonius")
        for domain in ("api_by_user", "events_by_user"):
            RateLimitedUser(polonius, domain=domain).clear_history()
        self.login_user(polonius)

        def poll() -> "TestHttpResponse":
            return self.client_get("/json/events", {"all_public_streams": "true"})

        with time_machine.travel(time.time(), tick=False):
            for _ in range(2):
                self.assert_json_error(poll(), "User not authorized for this query")
            with self.assertLogs("zerver.lib.rate_limiter", level="WARNING") as logs:
                self.assertEqual(poll().status_code, 429)
            self.assertEqual(
                logs.output,
                [
                    "WARNING:zerver.lib.rate_limiter:429 to the web client:"
                    f" user {polonius.id}, GET /json/events, events_by_user,"
                    " retry after 30.00s"
                ],
            )

            for _ in range(2):
                self.assert_json_success(self.client_get("/json/users/me"))

    @ratelimit_rule(60, 1, domain="register_by_user")
    def test_429_to_an_api_client_is_not_logged(self) -> None:
        hamlet = self.example_user("hamlet")
        RateLimitedUser(hamlet, domain="register_by_user").clear_history()

        with (
            time_machine.travel(time.time(), tick=False),
            mock.patch("zerver.views.events_register.do_events_register", return_value={}),
            self.assertNoLogs("zerver.lib.rate_limiter", level="WARNING"),
        ):
            self.assert_json_success(self.api_post(hamlet, "/api/v1/register"))
            self.assertEqual(self.api_post(hamlet, "/api/v1/register").status_code, 429)


class RateLimitedPageLoadTest(ZulipTestCase):
    """A person whose budget is spent and who loads a page in the
    browser is shown a page that waits as long as the server asks and
    then loads again, not the JSON error meant for the web client."""

    @ratelimit_rule(60, 2, domain="api_by_user")
    def test_page_load_waits_and_reloads(self) -> None:
        hamlet = self.example_user("hamlet")
        RateLimitedUser(hamlet).clear_history()
        self.login_user(hamlet)

        # Whole seconds, so that the wait the limiter reports is exact.
        with time_machine.travel(int(time.time()), tick=False) as traveller:
            for _ in range(2):
                self.assert_json_success(self.client_get("/json/users/me"))
            traveller.shift(10.5)

            result = self.client_get(
                "/", HTTP_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            )
            self.assertEqual(result.status_code, 429)
            self.assertEqual(result["Content-Type"], "text/html; charset=utf-8")
            # 19.5 seconds, rounded up.
            self.assertEqual(result["Retry-After"], "20")
            html = result.content.decode()
            self.assertIn('<meta http-equiv="refresh" content="20" />', html)
            self.assertIn(
                "The server asked this page to wait. It will reload by itself in 20\xa0seconds.",
                html,
            )
            self.assertNotIn("RATE_LIMIT_HIT", html)

            # The web client's own requests keep the JSON error, even
            # when they say they prefer HTML.
            with self.assertLogs("zerver.lib.rate_limiter", level="WARNING") as logs:
                result = self.client_get("/json/users/me", HTTP_ACCEPT="text/html")
            self.assertEqual(
                logs.output,
                [
                    "WARNING:zerver.lib.rate_limiter:429 to the web client:"
                    f" user {hamlet.id}, GET /json/users/me, api_by_user, retry after 19.50s"
                ],
            )
            self.assertEqual(result.status_code, 429)
            self.assertEqual(result["Retry-After"], "19.5")
            self.assertEqual(
                orjson.loads(result.content),
                {
                    "result": "error",
                    "msg": "API usage exceeded rate limit; see https://zulip.com/api/http-headers#rate-limiting-response-headers",
                    "code": "RATE_LIMIT_HIT",
                    "retry-after": 19.5,
                },
            )
        RateLimitedUser(hamlet).clear_history()


# Don't load the base class as a test: https://bugs.python.org/issue17519.
del RateLimiterBackendBase
