# INTERIM BETA: deleted with zerver/views/armada_beta.py and
# web/src/armada/beta/ when the rebuild's conversation lands.
from datetime import timedelta
from unittest import mock

import orjson
from django.utils.timezone import now as timezone_now

from zerver.lib.pear import PEAR_QUESTION_TIMEOUT_SECONDS
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.templates import font_preloads
from zerver.models import PearWorkerRun, UserProfile
from zerver.views.armada_beta import live_run_ids_for_user


class ArmadaBetaTest(ZulipTestCase):
    def test_signed_out_is_sent_to_sign_in(self) -> None:
        result = self.client_get("/beta/")
        self.assertEqual(result.status_code, 302)
        self.assertIn("/login/", result["Location"])

    def test_signed_in_gets_the_page_with_session_csrf_and_no_key(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)
        result = self.client_get("/beta/")
        self.assertEqual(result.status_code, 200)
        html = result.content.decode()
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn("armada-beta-stubentry.js", html)
        self.assertIn('id="col"', html)
        # The page is told how long a run waits for an answer, so that a
        # question the server has given up on stops reading as waiting.
        self.assertIn(f'data-question-timeout="{PEAR_QUESTION_TIMEOUT_SECONDS}"', html)
        self.assertIn('data-live-run-ids="[]"', html)
        # The page signs in by the session alone: no API key is ever
        # written into it.
        self.assertNotIn(hamlet.api_key, html)

    def make_run(
        self,
        requester: UserProfile,
        stream_name: str,
        *,
        status: str = PearWorkerRun.RUNNING,
        session_kind: str = PearWorkerRun.SHARED,
        minutes_ago: int = 0,
    ) -> PearWorkerRun:
        trigger_message_id = self.send_stream_message(requester, stream_name, "a request")
        return PearWorkerRun.objects.create(
            realm=requester.realm,
            bot_profile=self.example_user("default_bot"),
            requesting_user=requester,
            trigger_message_id=trigger_message_id,
            stream_name=stream_name,
            topic_name="test",
            session_kind=session_kind,
            status=status,
            date_created=timezone_now() - timedelta(minutes=minutes_ago),
        )

    def test_live_runs_are_the_live_shared_runs_in_my_channels(self) -> None:
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        self.subscribe(hamlet, "Denmark")
        self.subscribe(othello, "Denmark")
        self.make_stream("hamlet-not-here")
        self.subscribe(othello, "hamlet-not-here")

        older = self.make_run(othello, "Denmark", minutes_ago=5)
        queued = self.make_run(othello, "Denmark", status=PearWorkerRun.QUEUED)
        self.make_run(othello, "Denmark", status=PearWorkerRun.DONE)
        self.make_run(othello, "Denmark", status=PearWorkerRun.FAILED)
        self.make_run(othello, "Denmark", session_kind=PearWorkerRun.SIDE)
        self.make_run(othello, "hamlet-not-here")

        self.assertEqual(live_run_ids_for_user(hamlet), [queued.id, older.id])

        # What the page is given is what pear/runs then lets this person
        # read, in one request.
        self.login_user(hamlet)
        html = self.client_get("/beta/").content.decode()
        self.assertIn(f'data-live-run-ids="[{queued.id},{older.id}]"', html)
        result = self.client_get(
            "/json/pear/runs", {"run_ids": orjson.dumps([queued.id, older.id]).decode()}
        )
        runs = self.assert_json_success(result)["pear_runs"]
        self.assertEqual({run["id"] for run in runs}, {queued.id, older.id})

        with mock.patch("zerver.views.armada_beta.ARMADA_BETA_LIVE_RUN_LIMIT", 1):
            self.assertEqual(live_run_ids_for_user(hamlet), [queued.id])

    def test_the_beta_preloads_the_faces_the_app_does(self) -> None:
        # The beta's page is drawn in Söhne, like the app's, so a first
        # paint never waits on it.
        stats = {
            "status": "done",
            "assets": {
                f"files/{name}": {"name": f"files/{name}", "sourceFilename": source}
                for name, source in [
                    ("soehne-buch.woff2", "../fonts/licensed/soehne-buch.woff2"),
                    ("GeistMonoVF.woff2", "fonts/armada/GeistMonoVF.woff2"),
                    ("HankenGrotesk-VF.woff2", "fonts/armada/HankenGrotesk-VF.woff2"),
                ]
            },
        }
        with mock.patch("zerver.lib.templates.webpack_stats", return_value=stats):
            beta = font_preloads("armada-beta")
            self.assertEqual(beta, font_preloads("app"))
        self.assertEqual(
            [url.rsplit("/", 1)[-1] for url in beta], ["soehne-buch.woff2", "GeistMonoVF.woff2"]
        )
