import tempfile
from unittest.mock import patch

import ldap
import orjson
from django.core import mail
from django.core.mail.message import EmailMessage, EmailMultiAlternatives
from django.test import override_settings
from django.utils.timezone import now as timezone_now
from django_auth_ldap.config import LDAPSearch

from zerver.actions.create_user import do_create_user
from zerver.actions.realm_settings import do_set_realm_property
from zerver.lib.email_notifications import MissedMessageData, send_account_registered_email
from zerver.lib.send_email import (
    queue_scheduled_emails,
    send_custom_email,
    send_custom_server_email,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import mock_queue_publish
from zerver.models import ScheduledEmail, UserProfile
from zerver.models.realms import get_realm
from zerver.models.scheduled_jobs import NotificationTriggers
from zilencer.models import RemoteZulipServer


class TestCustomEmails(ZulipTestCase):
    def test_send_custom_email_argument(self) -> None:
        hamlet = self.example_user("hamlet")
        email_subject = "subject_test"
        reply_to = "reply_to_test"
        from_name = "from_name_test"
        campaign_name = "test_campaign"

        with tempfile.NamedTemporaryFile() as markdown_template:
            markdown_template.write(b"# Some heading\n\nSome content\n{{ realm_name }}")
            markdown_template.flush()
            send_custom_email(
                UserProfile.objects.filter(id=hamlet.id),
                dry_run=False,
                options={
                    "markdown_template_path": markdown_template.name,
                    "reply_to": reply_to,
                    "subject": email_subject,
                    "from_name": from_name,
                    "campaign_name": campaign_name,
                },
            )
        self.assert_length(mail.outbox, 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, email_subject)
        self.assert_length(msg.reply_to, 1)
        self.assertEqual(msg.reply_to[0], reply_to)
        self.assertNotIn("{% block content %}", msg.body)
        self.assertIn("# Some heading", msg.body)
        self.assertIn("Zulip Dev", msg.body)
        self.assertNotIn("{{ realm_name }}", msg.body)
        self.assertNotIn("</div>", msg.body)

        assert isinstance(msg, EmailMultiAlternatives)
        self.assertIn("Some heading</h1>", str(msg.alternatives[0][0]))
        self.assertNotIn("{{ realm_name }}", str(msg.alternatives[0][0]))

    def test_send_custom_email_remote_server(self) -> None:
        email_subject = "subject_test"
        reply_to = "reply_to_test"
        from_name = "from_name_test"
        campaign_name = "test_campaign"
        markdown_template_path = "templates/corporate/policies/index.md"
        send_custom_server_email(
            remote_servers=RemoteZulipServer.objects.all(),
            dry_run=False,
            options={
                "markdown_template_path": markdown_template_path,
                "reply_to": reply_to,
                "subject": email_subject,
                "from_name": from_name,
                "campaign_name": campaign_name,
            },
        )
        self.assert_length(mail.outbox, 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, email_subject)
        self.assertEqual(msg.to, ["remotezulipserver@zulip.com"])
        self.assert_length(msg.reply_to, 1)
        self.assertEqual(msg.reply_to[0], reply_to)
        self.assertNotIn("{% block content %}", msg.body)
        # Verify that the HTML version contains the footer.
        assert isinstance(msg, EmailMultiAlternatives)
        self.assertIn(
            "You are receiving this email to update you about important changes to Zulip",
            str(msg.alternatives[0][0]),
        )
        self.assertIn("Unsubscribe", str(msg.alternatives[0][0]))
        # Verify that the Text version contains the footer.
        self.assertIn(
            "You are receiving this email to update you about important changes to Zulip", msg.body
        )
        self.assertIn("Unsubscribe", msg.body)

    def test_send_custom_email_headers(self) -> None:
        hamlet = self.example_user("hamlet")
        campaign_name = "test_campaign_headers"
        markdown_template_path = (
            "zerver/tests/fixtures/email/custom_emails/email_base_headers_test.md"
        )
        send_custom_email(
            UserProfile.objects.filter(id=hamlet.id),
            dry_run=False,
            options={
                "markdown_template_path": markdown_template_path,
                "campaign_name": campaign_name,
            },
        )
        self.assert_length(mail.outbox, 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, "Test subject")
        self.assertFalse(msg.reply_to)
        self.assertIn("Test body", msg.body)

    def test_send_custom_email_context(self) -> None:
        hamlet = self.example_user("hamlet")
        markdown_template_path = (
            "zerver/tests/fixtures/email/custom_emails/email_base_headers_test.md"
        )
        send_custom_email(
            UserProfile.objects.filter(id=hamlet.id),
            dry_run=False,
            options={
                "markdown_template_path": markdown_template_path,
                "campaign_name": "test_campaign_context_1",
            },
        )
        self.assert_length(mail.outbox, 1)
        msg = mail.outbox[0]

        # We default to not including an unsubscribe link in the headers
        self.assertEqual(msg.extra_headers.get("X-Auto-Response-Suppress"), "All")
        self.assertIsNone(msg.extra_headers.get("List-Unsubscribe"))

        mail.outbox = []
        markdown_template_path = (
            "zerver/tests/fixtures/email/custom_emails/email_base_headers_custom_test.md"
        )

        def add_context(context: dict[str, object], user: UserProfile) -> None:
            context["unsubscribe_link"] = "some@email"
            context["custom"] = str(user.id)

        send_custom_email(
            UserProfile.objects.filter(id=hamlet.id),
            dry_run=False,
            options={
                "markdown_template_path": markdown_template_path,
                "campaign_name": "test_campaign_context_2",
            },
            add_context=add_context,
        )
        self.assert_length(mail.outbox, 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.extra_headers.get("X-Auto-Response-Suppress"), "All")
        self.assertEqual(msg.extra_headers.get("List-Unsubscribe"), "<some@email>")
        self.assertIn(f"Test body with {hamlet.id} value", msg.body)

    def test_send_custom_email_no_argument(self) -> None:
        hamlet = self.example_user("hamlet")
        campaign_name = "test_campaign"
        from_name = "from_name_test"
        email_subject = "subject_test"
        markdown_template_path = (
            "zerver/tests/fixtures/email/custom_emails/email_base_headers_no_headers_test.md"
        )

        from zerver.lib.send_email import NoEmailArgumentError

        self.assertRaises(
            NoEmailArgumentError,
            send_custom_email,
            UserProfile.objects.filter(id=hamlet.id),
            dry_run=False,
            options={
                "markdown_template_path": markdown_template_path,
                "from_name": from_name,
                "campaign_name": campaign_name,
            },
        )

        self.assertRaises(
            NoEmailArgumentError,
            send_custom_email,
            UserProfile.objects.filter(id=hamlet.id),
            dry_run=False,
            options={
                "markdown_template_path": markdown_template_path,
                "subject": email_subject,
                "campaign_name": campaign_name,
            },
        )

    def test_send_custom_email_doubled_arguments(self) -> None:
        hamlet = self.example_user("hamlet")
        from_name = "from_name_test"
        email_subject = "subject_test"
        markdown_template_path = (
            "zerver/tests/fixtures/email/custom_emails/email_base_headers_test.md"
        )
        campaign_name = "test_campaign"

        from zerver.lib.send_email import DoubledEmailArgumentError

        self.assertRaises(
            DoubledEmailArgumentError,
            send_custom_email,
            UserProfile.objects.filter(id=hamlet.id),
            dry_run=False,
            options={
                "markdown_template_path": markdown_template_path,
                "subject": email_subject,
                "campaign_name": campaign_name,
            },
        )

        self.assertRaises(
            DoubledEmailArgumentError,
            send_custom_email,
            UserProfile.objects.filter(id=hamlet.id),
            dry_run=False,
            options={
                "markdown_template_path": markdown_template_path,
                "from_name": from_name,
                "campaign_name": campaign_name,
            },
        )

    def test_send_custom_email_dry_run(self) -> None:
        hamlet = self.example_user("hamlet")
        email_subject = "subject_test"
        reply_to = "reply_to_test"
        from_name = "from_name_test"
        markdown_template_path = "templates/zerver/tests/markdown/test_nested_code_blocks.md"
        campaign_name = "test_campaign"
        with patch("builtins.print") as _:
            send_custom_email(
                UserProfile.objects.filter(id=hamlet.id),
                dry_run=True,
                options={
                    "markdown_template_path": markdown_template_path,
                    "reply_to": reply_to,
                    "subject": email_subject,
                    "from_name": from_name,
                    "campaign_name": campaign_name,
                },
            )
            self.assert_length(mail.outbox, 0)


class TestAccountRegisteredEmail(ZulipTestCase):
    def test_account_registered_email_context(self) -> None:
        hamlet = self.example_user("hamlet")
        with mock_queue_publish("zerver.lib.send_email.queue_event_on_commit") as m:
            send_account_registered_email(hamlet)
        m.assert_called_once()
        email_data = m.call_args[0][1]
        self.assertEqual(email_data["template_prefix"], "zerver/emails/account_registered")
        self.assertEqual(email_data["context"]["email"], self.example_email("hamlet"))
        self.assertFalse(email_data["context"]["realm_creation"])
        self.assertNotIn("ldap_username", email_data["context"])

        iago = self.example_user("iago")
        with mock_queue_publish("zerver.lib.send_email.queue_event_on_commit") as m:
            send_account_registered_email(iago, realm_creation=True)
        m.assert_called_once()
        email_data = m.call_args[0][1]
        self.assertEqual(email_data["context"]["email"], self.example_email("iago"))
        self.assertTrue(email_data["context"]["realm_creation"])
        self.assertNotIn("ldap_username", email_data["context"])

    def test_no_onboarding_emails_for_new_member(self) -> None:
        realm = get_realm("zulip")
        with self.captureOnCommitCallbacks(execute=True):
            user = do_create_user("newbie@zulip.com", "password", realm, "Newbie", acting_user=None)

        # The onboarding follow-up emails are cut: nothing is scheduled,
        # and the account details are sent at once.
        self.assertFalse(ScheduledEmail.objects.filter(users=user).exists())
        self.assert_length(mail.outbox, 1)
        self.assertEqual(mail.outbox[0].subject, "Zulip Dev on Armada: Your new account details")

    # See https://zulip.readthedocs.io/en/latest/production/authentication-methods.html#ldap-including-active-directory
    # for case details.
    @override_settings(
        AUTHENTICATION_BACKENDS=(
            "zproject.backends.ZulipLDAPAuthBackend",
            "zproject.backends.ZulipDummyBackend",
        ),
        # configure email search for email address in the uid attribute:
        AUTH_LDAP_REVERSE_EMAIL_SEARCH=LDAPSearch(
            "ou=users,dc=zulip,dc=com", ldap.SCOPE_ONELEVEL, "(uid=%(email)s)"
        ),
    )
    def test_account_registered_email_ldap_case_a_login_credentials(self) -> None:
        self.init_default_ldap_database()
        ldap_user_attr_map = {"full_name": "cn"}

        with (
            self.settings(AUTH_LDAP_USER_ATTR_MAP=ldap_user_attr_map),
            mock_queue_publish("zerver.lib.send_email.queue_event_on_commit") as m,
        ):
            self.login_with_return(
                "newuser_email_as_uid@zulip.com",
                self.ldap_password("newuser_email_as_uid@zulip.com"),
            )
            user = UserProfile.objects.get(delivery_email="newuser_email_as_uid@zulip.com")
            self.assertFalse(ScheduledEmail.objects.filter(users=user).exists())
            m.assert_called_once()
            email_data = m.call_args[0][1]
            self.assertEqual(email_data["context"]["ldap"], True)
            self.assertEqual(
                email_data["context"]["ldap_username"], "newuser_email_as_uid@zulip.com"
            )

    @override_settings(
        AUTHENTICATION_BACKENDS=(
            "zproject.backends.ZulipLDAPAuthBackend",
            "zproject.backends.ZulipDummyBackend",
        )
    )
    def test_account_registered_email_ldap_case_b_login_credentials(self) -> None:
        self.init_default_ldap_database()
        ldap_user_attr_map = {"full_name": "cn"}

        with (
            self.settings(
                LDAP_APPEND_DOMAIN="zulip.com", AUTH_LDAP_USER_ATTR_MAP=ldap_user_attr_map
            ),
            mock_queue_publish("zerver.lib.send_email.queue_event_on_commit") as m,
        ):
            self.login_with_return("newuser@zulip.com", self.ldap_password("newuser"))

            user = UserProfile.objects.get(delivery_email="newuser@zulip.com")
            self.assertFalse(ScheduledEmail.objects.filter(users=user).exists())
            m.assert_called_once()
            email_data = m.call_args[0][1]
            self.assertEqual(email_data["context"]["ldap"], True)
            self.assertEqual(email_data["context"]["ldap_username"], "newuser")

    @override_settings(
        AUTHENTICATION_BACKENDS=(
            "zproject.backends.ZulipLDAPAuthBackend",
            "zproject.backends.ZulipDummyBackend",
        )
    )
    def test_account_registered_email_ldap_case_c_login_credentials(self) -> None:
        self.init_default_ldap_database()
        ldap_user_attr_map = {"full_name": "cn"}

        with (
            self.settings(LDAP_EMAIL_ATTR="mail", AUTH_LDAP_USER_ATTR_MAP=ldap_user_attr_map),
            mock_queue_publish("zerver.lib.send_email.queue_event_on_commit") as m,
        ):
            self.login_with_return("newuser_with_email", self.ldap_password("newuser_with_email"))
            user = UserProfile.objects.get(delivery_email="newuser_email@zulip.com")
            self.assertFalse(ScheduledEmail.objects.filter(users=user).exists())
            m.assert_called_once()
            email_data = m.call_args[0][1]
            self.assertEqual(email_data["context"]["ldap"], True)
            self.assertEqual(email_data["context"]["ldap_username"], "newuser_with_email")


class TestCustomWelcomeEmailSender(ZulipTestCase):
    def test_custom_welcome_email_sender(self) -> None:
        name = "Nonreg Email"
        email = self.nonreg_email("test")
        with (
            override_settings(
                WELCOME_EMAIL_SENDER={
                    "name": name,
                    "email": email,
                }
            ),
            mock_queue_publish("zerver.lib.send_email.queue_event_on_commit") as m,
        ):
            send_account_registered_email(self.example_user("hamlet"))
        m.assert_called_once()
        email_data = m.call_args[0][1]
        self.assertEqual(email_data["from_name"], name)
        self.assertEqual(email_data["from_address"], email)


class TestScheduledEmailWithDeletedTemplate(ZulipTestCase):
    def test_scheduled_email_with_deleted_template_is_dropped(self) -> None:
        hamlet = self.example_user("hamlet")
        # A row scheduled before the onboarding follow-up emails were
        # cut, whose template is gone.
        email = ScheduledEmail.objects.create(
            type=ScheduledEmail.WELCOME,
            scheduled_timestamp=timezone_now(),
            realm=hamlet.realm,
            data=orjson.dumps(
                {
                    "template_prefix": "zerver/emails/onboarding_zulip_topics",
                    "from_name": None,
                    "from_address": None,
                    "language": None,
                    "context": {},
                }
            ).decode(),
        )
        email.users.add(hamlet)
        email_id = email.id
        scheduled_at = email.scheduled_timestamp

        with (
            self.assertLogs("zulip.send_email", level="WARNING") as warning_log,
            mock_queue_publish("zerver.lib.send_email.queue_event_on_commit") as m,
        ):
            queue_scheduled_emails(email)

        m.assert_not_called()
        self.assertFalse(ScheduledEmail.objects.filter(id=email_id).exists())
        self.assertEqual(
            warning_log.output,
            [
                f"WARNING:zulip.send_email:ScheduledEmail {email_id} at {scheduled_at} "
                "names a template that no longer exists: zerver/emails/onboarding_zulip_topics"
            ],
        )


class TestEmailsSayArmada(ZulipTestCase):
    def assert_email_says_armada(self, message: EmailMessage) -> None:
        assert isinstance(message, EmailMultiAlternatives)
        html = message.alternatives[0][0]
        assert isinstance(html, str)
        texts = [str(message.subject), str(message.body), html, self.email_display_from(message)]
        for text in texts:
            self.assertNotIn("Zulip", text)
        self.assertIn("Armada", "".join(texts))

    # The development settings' physical address names Zulip; it is
    # a server setting, not the emails' text.
    @override_settings(PHYSICAL_ADDRESS="")
    def test_emails_do_not_name_zulip(self) -> None:
        realm = get_realm("zulip")
        do_set_realm_property(realm, "name", "Elsinore", acting_user=None)
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")

        with self.captureOnCommitCallbacks(execute=True):
            send_account_registered_email(hamlet)
        self.assert_length(mail.outbox, 1)
        self.assertEqual(self.email_display_from(mail.outbox[0]).split(" <")[0], "Armada")
        self.assert_email_says_armada(mail.outbox[0])
        mail.outbox.clear()

        result = self.client_post("/accounts/password/reset/", {"email": hamlet.delivery_email})
        self.assertEqual(result.status_code, 302)
        self.assert_length(mail.outbox, 1)
        self.assert_email_says_armada(mail.outbox[0])
        mail.outbox.clear()

        result = self.client_post("/accounts/home/", {"email": "new-member@zulip.com"})
        self.assertEqual(result.status_code, 302)
        self.assert_length(mail.outbox, 1)
        self.assertEqual(self.email_display_from(mail.outbox[0]).split(" <")[0], "Armada")
        self.assert_email_says_armada(mail.outbox[0])
        mail.outbox.clear()

        self.login("iago")
        with self.captureOnCommitCallbacks(execute=True):
            result = self.client_post(
                "/json/invites",
                {
                    "invitee_emails": "invitee@zulip.com",
                    "stream_ids": orjson.dumps([self.get_stream_id("Denmark")]).decode(),
                    "invite_expires_in_minutes": 60,
                },
            )
        self.assert_json_success(result)
        self.assert_length(mail.outbox, 1)
        self.assertEqual(self.email_display_from(mail.outbox[0]).split(" <")[0], "Armada")
        self.assert_email_says_armada(mail.outbox[0])
        mail.outbox.clear()

        message_id = self.send_personal_message(othello, hamlet, "Are you there?")
        self.handle_missedmessage_emails(
            hamlet.id,
            {message_id: MissedMessageData(trigger=NotificationTriggers.DIRECT_MESSAGE)},
        )
        self.assert_length(mail.outbox, 1)
        self.assert_email_says_armada(mail.outbox[0])


class TestUtmParamsInEmailLinks(ZulipTestCase):
    def test_add_utm_paras_to_links(self) -> None:
        from zerver.lib.send_email import add_utm_params_to_links

        campaign_name = "test_campaign"

        html = '<a href="https://zulip.com/pricing">Pricing</a>'
        expected = '<a href="https://zulip.com/pricing?utm_source=newsletter&amp;utm_medium=email&amp;utm_campaign=test_campaign">Pricing</a>'
        self.assertEqual(add_utm_params_to_links(html, campaign_name), expected)

        html_frag = '<a href="https://zulip.com/help#topic">Help</a>'
        expected_frag = '<a href="https://zulip.com/help?utm_source=newsletter&amp;utm_medium=email&amp;utm_campaign=test_campaign#topic">Help</a>'
        self.assertEqual(add_utm_params_to_links(html_frag, campaign_name), expected_frag)

        html_ext = '<a href="https://github.com/zulip/zulip">GitHub</a>'
        self.assertEqual(add_utm_params_to_links(html_ext, campaign_name), html_ext)

        html_query = '<a href="https://blog.zulip.com/?page=2">Blog</a>'
        expected_query = '<a href="https://blog.zulip.com/?page=2&amp;utm_source=newsletter&amp;utm_medium=email&amp;utm_campaign=test_campaign">Blog</a>'
        self.assertEqual(add_utm_params_to_links(html_query, campaign_name), expected_query)
