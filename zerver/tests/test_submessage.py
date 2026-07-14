import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from zerver.actions.submessage import do_add_submessage
from zerver.lib.message_cache import MessageDict
from zerver.lib.pear import PEAR_EMBEDDED_BOT_SERVICE, attach_pear_artifact_to_message
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Attachment, Message, SubMessage, UserProfile


class TestBasics(ZulipTestCase):
    def test_get_raw_db_rows(self) -> None:
        cordelia = self.example_user("cordelia")
        hamlet = self.example_user("hamlet")
        stream_name = "Verona"

        message_id = self.send_stream_message(
            sender=cordelia,
            stream_name=stream_name,
        )

        def get_raw_rows() -> list[dict[str, Any]]:
            query = SubMessage.get_raw_db_rows([message_id])
            rows = list(query)
            return rows

        rows = get_raw_rows()
        self.assertEqual(rows, [])

        sm1 = SubMessage.objects.create(
            msg_type="whatever",
            content="stuff1",
            message_id=message_id,
            sender=cordelia,
        )

        sm2 = SubMessage.objects.create(
            msg_type="whatever",
            content="stuff2",
            message_id=message_id,
            sender=hamlet,
        )

        expected_data = [
            dict(
                id=sm1.id,
                message_id=message_id,
                sender_id=cordelia.id,
                msg_type="whatever",
                content="stuff1",
            ),
            dict(
                id=sm2.id,
                message_id=message_id,
                sender_id=hamlet.id,
                msg_type="whatever",
                content="stuff2",
            ),
        ]

        self.assertEqual(get_raw_rows(), expected_data)

        message = Message.objects.get(id=message_id)
        message_json = MessageDict.wide_dict(message)
        rows = message_json["submessages"]
        rows.sort(key=lambda r: r["id"])
        self.assertEqual(rows, expected_data)

        msg_rows = MessageDict.ids_to_dict([message_id])
        rows = msg_rows[0]["submessages"]
        rows.sort(key=lambda r: r["id"])
        self.assertEqual(rows, expected_data)

    def test_endpoint_errors(self) -> None:
        cordelia = self.example_user("cordelia")
        stream_name = "Verona"
        message_id = self.send_stream_message(
            sender=cordelia,
            stream_name=stream_name,
        )
        self.login_user(cordelia)

        payload = dict(
            message_id=message_id,
            msg_type="whatever",
            content="not json",
        )
        result = self.client_post("/json/submessage", payload)
        self.assert_json_error(result, "Invalid json for submessage")

        payload = dict(message_id=message_id, msg_type="pear_artifact", content="{}")
        result = self.client_post("/json/submessage", payload)
        self.assert_json_error(result, "Pear artifacts can only be attached by the Pear runtime.")

        hamlet = self.example_user("hamlet")
        bad_message_id = self.send_personal_message(
            from_user=hamlet,
            to_user=hamlet,
        )
        payload = dict(
            message_id=bad_message_id,
            msg_type="whatever",
            content="does not matter",
        )
        result = self.client_post("/json/submessage", payload)
        self.assert_json_error(result, "Invalid message(s)")

    def test_pear_runtime_artifact_is_durable_and_emits_event(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear",
            owner,
            full_name="Pear",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        message_id = self.send_stream_message(pear_bot, "Denmark", content="Here is the diagram.")
        document = '{"blocks":[{"body":"Force changes motion.","type":"copy"}],"version":1}'

        with self.capture_send_event_calls(expected_num_events=1) as events:
            attached = attach_pear_artifact_to_message(
                SimpleNamespace(user_profile=pear_bot),
                message_id=message_id,
                tool_results=[{"label": "Artifact", "value": document}],
            )
        self.assertTrue(attached)
        submessage = SubMessage.objects.get(message_id=message_id, msg_type="pear_artifact")
        self.assertEqual(submessage.content, document)
        self.assertEqual(events[0]["event"]["type"], "submessage")
        self.assertEqual(events[0]["event"]["msg_type"], "pear_artifact")

        self.login_user(owner)
        response = self.assert_json_success(self.client_get(f"/json/messages/{message_id}"))
        [reloaded] = response["message"]["submessages"]
        self.assertEqual(reloaded["content"], document)

        # Worker retries are idempotent for byte-identical canonical documents.
        attached = attach_pear_artifact_to_message(
            SimpleNamespace(user_profile=pear_bot),
            message_id=message_id,
            tool_results=[{"label": "Artifact", "value": document}],
        )
        self.assertTrue(attached)
        self.assertEqual(SubMessage.objects.filter(message_id=message_id).count(), 1)

    def test_pear_runtime_materializes_and_claims_generated_media(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear-media",
            owner,
            full_name="Pear Media",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        message_id = self.send_stream_message(pear_bot, "Denmark", content="Wave motion.")
        with tempfile.TemporaryDirectory() as workdir:
            media_dir = Path(workdir) / ".pear" / "artifacts"
            media_dir.mkdir(parents=True)
            (media_dir / "wave.webm").write_bytes(b"\x1a\x45\xdf\xa3bounded-webm")
            document = json.dumps(
                {
                    "version": 1,
                    "blocks": [
                        {
                            "type": "media",
                            "kind": "video",
                            "source": ".pear/artifacts/wave.webm",
                            "alt": "A wave moves from left to right.",
                            "motion": "manual",
                        }
                    ],
                }
            )
            attached = attach_pear_artifact_to_message(
                SimpleNamespace(user_profile=pear_bot),
                message_id=message_id,
                tool_results=[{"label": "Artifact", "value": document}],
                workdir=workdir,
            )
            self.assertTrue(attached)
            submessage = SubMessage.objects.get(message_id=message_id, msg_type="pear_artifact")
            media = json.loads(submessage.content)["blocks"][0]
            self.assertNotIn("source", media)
            self.assertRegex(media["src"], r"^/user_uploads/")
            attachment = Attachment.objects.get(path_id=media["src"].removeprefix("/user_uploads/"))
            self.assertEqual(list(attachment.messages.values_list("id", flat=True)), [message_id])

            attach_pear_artifact_to_message(
                SimpleNamespace(user_profile=pear_bot),
                message_id=message_id,
                tool_results=[{"label": "Artifact", "value": document}],
                workdir=workdir,
            )
            self.assertEqual(SubMessage.objects.filter(message_id=message_id).count(), 1)
            self.assertEqual(Attachment.objects.filter(owner=pear_bot).count(), 1)

    def test_pear_runtime_resolves_generated_media_as_the_topic_user(self) -> None:
        owner = self.example_user("hamlet")
        pear_bot = self.create_test_bot(
            "pear-isolated-media",
            owner,
            full_name="Pear Isolated Media",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=PEAR_EMBEDDED_BOT_SERVICE,
        )
        message_id = self.send_stream_message(pear_bot, "Denmark", content="Crystal lattice.")
        with tempfile.TemporaryDirectory() as workdir:
            media_path = Path(workdir) / ".pear" / "artifacts" / "lattice.png"
            media_path.parent.mkdir(parents=True)
            media_bytes = b"\x89PNG\r\n\x1a\nbounded-png"
            media_path.write_bytes(media_bytes)
            document = json.dumps(
                {
                    "version": 1,
                    "blocks": [
                        {
                            "type": "media",
                            "kind": "image",
                            "source": ".pear/artifacts/lattice.png",
                            "alt": "A hexagonal crystal lattice.",
                        }
                    ],
                }
            )
            with (
                mock.patch(
                    "zerver.lib.pear_worker.pear_resolve_file_path",
                    return_value=media_path.resolve(),
                ) as resolve_path,
                mock.patch(
                    "zerver.lib.pear_worker.pear_read_bytes_path",
                    return_value=media_bytes,
                ) as read_path,
            ):
                attached = attach_pear_artifact_to_message(
                    SimpleNamespace(user_profile=pear_bot),
                    message_id=message_id,
                    tool_results=[{"label": "Artifact", "value": document}],
                    workdir=workdir,
                    isolation_username="pear-topic-test",
                )
        self.assertTrue(attached)
        resolve_path.assert_called_once_with(
            media_path,
            isolation_username="pear-topic-test",
        )
        read_path.assert_called_once_with(
            media_path.resolve(),
            isolation_username="pear-topic-test",
            max_bytes=4_000_000,
        )
        self.assertEqual(
            SubMessage.objects.filter(message_id=message_id, msg_type="pear_artifact").count(),
            1,
        )

    def test_original_sender_enforced(self) -> None:
        cordelia = self.example_user("cordelia")
        hamlet = self.example_user("hamlet")
        stream_name = "Verona"

        message_id = self.send_stream_message(
            sender=cordelia,
            stream_name=stream_name,
        )
        self.login_user(hamlet)

        payload = dict(
            message_id=message_id,
            msg_type="whatever",
            content="{}",
        )

        # Hamlet can't just go attaching submessages to Cordelia's
        # message, even though he does have read access here to the
        # message itself.
        result = self.client_post("/json/submessage", payload)
        self.assert_json_error(result, "You cannot attach a submessage to this message.")

        # Since Hamlet is actually subscribed to the stream, he is welcome
        # to send submessages to Cordelia once she initiates the "subconversation".
        do_add_submessage(
            realm=cordelia.realm,
            sender_id=cordelia.id,
            message_id=message_id,
            msg_type="whatever",
            content="whatever",
        )

        result = self.client_post("/json/submessage", payload)
        self.assert_json_success(result)

    def test_endpoint_success(self) -> None:
        cordelia = self.example_user("cordelia")
        hamlet = self.example_user("hamlet")
        stream_name = "Verona"
        message_id = self.send_stream_message(
            sender=cordelia,
            stream_name=stream_name,
        )
        self.login_user(cordelia)

        payload = dict(
            message_id=message_id,
            msg_type="whatever",
            content='{"name": "alice", "salary": 20}',
        )
        with self.capture_send_event_calls(expected_num_events=1) as events:
            result = self.client_post("/json/submessage", payload)
        self.assert_json_success(result)

        submessage = SubMessage.objects.get(message_id=message_id)

        expected_data = dict(
            message_id=message_id,
            submessage_id=submessage.id,
            content=payload["content"],
            msg_type="whatever",
            sender_id=cordelia.id,
            type="submessage",
        )

        data = events[0]["event"]
        self.assertEqual(data, expected_data)
        users = events[0]["users"]
        self.assertIn(cordelia.id, users)
        self.assertIn(hamlet.id, users)

        rows = SubMessage.get_raw_db_rows([message_id])
        self.assert_length(rows, 1)
        row = rows[0]

        expected_data = dict(
            id=row["id"],
            message_id=message_id,
            content='{"name": "alice", "salary": 20}',
            msg_type="whatever",
            sender_id=cordelia.id,
        )
        self.assertEqual(row, expected_data)

    def test_submessage_event_sent_after_transaction_commits(self) -> None:
        """
        Tests that `send_event_rollback_unsafe` is hooked to `transaction.on_commit`.
        This is important, because we don't want to end up holding locks on message rows
        for too long if the event queue runs into a problem.
        """
        hamlet = self.example_user("hamlet")
        message_id = self.send_stream_message(hamlet, "Denmark")

        with (
            self.capture_send_event_calls(expected_num_events=1),
            mock.patch("zerver.tornado.django_api.queue_json_publish_rollback_unsafe") as m,
        ):
            m.side_effect = AssertionError(
                "Events should be sent only after the transaction commits."
            )
            do_add_submessage(hamlet.realm, hamlet.id, message_id, "whatever", "whatever")

    def test_fetch_message_containing_submessages(self) -> None:
        cordelia = self.example_user("cordelia")
        stream_name = "Verona"
        message_id = self.send_stream_message(
            sender=cordelia,
            stream_name=stream_name,
        )
        self.login_user(cordelia)

        payload = dict(
            message_id=message_id,
            msg_type="whatever",
            content='{"name": "alice", "salary": 20}',
        )
        self.assert_json_success(self.client_post("/json/submessage", payload))

        result = self.client_get(f"/json/messages/{message_id}")
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["message"]["submessages"], 1)

        submessage = response_dict["message"]["submessages"][0]
        expected_data = dict(
            id=submessage["id"],
            message_id=message_id,
            content='{"name": "alice", "salary": 20}',
            msg_type="whatever",
            sender_id=cordelia.id,
        )
        self.assertEqual(submessage, expected_data)
