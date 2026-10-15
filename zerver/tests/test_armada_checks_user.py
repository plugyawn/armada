from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import F

from zerver.actions.users import do_change_user_role
from zerver.lib.onboarding_steps import ALL_ONBOARDING_STEPS
from zerver.lib.rate_limiter import RateLimitedUser
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, OnboardingStep, Recipient, Subscription, UserMessage, UserProfile
from zerver.models.users import get_system_bot, get_user_by_delivery_email
from zilencer.lib.armada_checks_user import (
    ARMADA_CHECKS_EMAIL,
    ARMADA_CHECKS_RATE_LIMITS,
    direct_message_conversations,
    ensure_armada_checks_user,
)


class ArmadaChecksUserTest(ZulipTestCase):
    def channel_subscriptions(self, user: UserProfile) -> dict[int, tuple[str, bool, bool]]:
        return {
            subscription.recipient.type_id: (
                subscription.color,
                subscription.is_muted,
                subscription.pin_to_top,
            )
            for subscription in Subscription.objects.filter(
                user_profile=user, active=True, recipient__type=Recipient.STREAM
            ).select_related("recipient")
        }

    def give_conversations(self, reference: UserProfile) -> None:
        # As in the development realm: a group, someone else, and King
        # Hamlet's, the newest.
        self.send_group_direct_message(
            self.example_user("iago"), [reference, self.example_user("cordelia")]
        )
        self.send_personal_message(self.example_user("othello"), reference)
        self.send_personal_message(self.example_user("hamlet"), reference)

    def test_creates_the_checks_user_like_the_reference_user(self) -> None:
        desdemona = self.example_user("desdemona")
        hamlet = self.example_user("hamlet")
        self.give_conversations(desdemona)
        verona = Subscription.objects.get(
            user_profile=desdemona,
            recipient__type=Recipient.STREAM,
            recipient__type_id=self.get_stream_id("Verona"),
        )
        verona.color = "#123456"
        verona.is_muted = True
        verona.pin_to_top = True
        verona.save()

        stdout = StringIO()
        call_command("armada_checks_user", stdout=stdout)
        user = get_user_by_delivery_email(ARMADA_CHECKS_EMAIL, desdemona.realm)
        self.assertEqual(
            stdout.getvalue(), f"The checks log in as {ARMADA_CHECKS_EMAIL} (user {user.id}).\n"
        )

        self.assertTrue(user.is_active)
        self.assertEqual(user.role, UserProfile.ROLE_REALM_OWNER)
        self.assertEqual(user.rate_limits, ARMADA_CHECKS_RATE_LIMITS)
        self.assertEqual(RateLimitedUser(user).rules(), [(1, 1000), (60, 30000)])
        self.assertEqual(self.channel_subscriptions(user), self.channel_subscriptions(desdemona))
        self.assertEqual(
            self.channel_subscriptions(user)[verona.recipient.type_id], ("#123456", True, True)
        )
        self.assertEqual(
            set(OnboardingStep.objects.filter(user=user).values_list("onboarding_step", flat=True)),
            {step.name for step in ALL_ONBOARDING_STEPS},
        )

        # King Hamlet has written, and every conversation desdemona has is
        # there, with the same people, newest in the same order, all read;
        # the welcome bot's are gone.
        self.assertTrue(
            UserMessage.objects.filter(
                user_profile=user,
                message__sender=hamlet,
                message__recipient__type=Recipient.DIRECT_MESSAGE_GROUP,
            ).exists()
        )
        self.assertEqual(self.conversation_order(user), self.conversation_order(desdemona))
        self.assertFalse(
            UserMessage.objects.filter(user_profile=user)
            .exclude(message__recipient__type=Recipient.STREAM)
            .extra(where=[UserMessage.where_unread()])  # noqa: S610
            .exists()
        )
        welcome_bot = get_system_bot("welcome-bot@zulip.com", user.realm_id)
        self.assertFalse(
            UserMessage.objects.filter(user_profile=user, message__sender=welcome_bot).exists()
        )

    def conversation_order(self, user: UserProfile) -> list[frozenset[int]]:
        conversations = direct_message_conversations(user)
        return sorted(conversations, key=lambda people: conversations[people])

    def channel_flags(self, user: UserProfile) -> dict[int, int]:
        return {
            message_id: int(flags)
            for message_id, flags in UserMessage.objects.filter(
                user_profile=user, message__recipient__type=Recipient.STREAM
            ).values_list("message_id", "flags")
        }

    def test_sees_the_reference_users_unread_messages_and_time_zone(self) -> None:
        desdemona = self.example_user("desdemona")
        desdemona.timezone = "Asia/Kolkata"
        desdemona.save(update_fields=["timezone"])
        unread_id = self.send_stream_message(self.example_user("iago"), "Verona", "unread here")
        user = ensure_armada_checks_user(desdemona.realm)
        self.assertEqual(user.timezone, "Asia/Kolkata")
        flags = self.channel_flags(user)
        self.assertEqual(flags, self.channel_flags(desdemona))
        self.assertFalse(flags[unread_id] & UserMessage.flags.read)

        # A run of the checks reads and unreads; the next refresh puts
        # back what the reference has, and its own messages stay read.
        UserMessage.objects.filter(user_profile=user, message_id=unread_id).update(
            flags=F("flags").bitor(UserMessage.flags.read)
        )
        own_id = self.send_stream_message(user, "Verona", "mine")
        UserMessage.objects.filter(message_id=own_id).update(
            flags=F("flags").bitand(~UserMessage.flags.read)
        )
        ensure_armada_checks_user(desdemona.realm)
        flags = self.channel_flags(user)
        self.assertFalse(flags[unread_id] & UserMessage.flags.read)
        self.assertTrue(flags[own_id] & UserMessage.flags.read)
        self.assertEqual(
            {message_id: value for message_id, value in flags.items() if message_id != own_id},
            {
                message_id: value
                for message_id, value in self.channel_flags(desdemona).items()
                if message_id != own_id
            },
        )

    def test_keeps_the_reference_users_conversations_in_order(self) -> None:
        desdemona = self.example_user("desdemona")
        self.give_conversations(desdemona)
        user = ensure_armada_checks_user(desdemona.realm)
        self.assert_length(self.conversation_order(user), 3)
        messages = Message.objects.count()
        # The reference user's oldest conversation becomes the newest.
        oldest = self.conversation_order(desdemona)[0]
        others = [UserProfile.objects.get(id=person_id) for person_id in oldest]
        if len(others) == 1:
            self.send_personal_message(others[0], desdemona)
        else:
            self.send_group_direct_message(others[0], [desdemona, *others[1:]])
        ensure_armada_checks_user(desdemona.realm)
        self.assertEqual(self.conversation_order(user), self.conversation_order(desdemona))
        # One message in each conversation, oldest first.
        self.assertEqual(
            Message.objects.count(), messages + 1 + len(self.conversation_order(desdemona))
        )
        ensure_armada_checks_user(desdemona.realm)
        self.assertEqual(
            Message.objects.count(), messages + 1 + len(self.conversation_order(desdemona))
        )

    def test_is_idempotent_and_repairs_drift(self) -> None:
        realm = self.example_user("desdemona").realm
        user = ensure_armada_checks_user(realm)
        messages = Message.objects.count()
        subscriptions = Subscription.objects.filter(user_profile=user).count()

        user.rate_limits = ""
        user.save(update_fields=["rate_limits"])
        do_change_user_role(user, UserProfile.ROLE_MEMBER, acting_user=None, notify=False)
        again = ensure_armada_checks_user(realm)

        self.assertEqual(again.id, user.id)
        self.assertEqual(again.role, UserProfile.ROLE_REALM_OWNER)
        self.assertEqual(again.rate_limits, ARMADA_CHECKS_RATE_LIMITS)
        self.assertEqual(Message.objects.count(), messages)
        self.assertEqual(Subscription.objects.filter(user_profile=user).count(), subscriptions)

    def test_like_another_user(self) -> None:
        iago = self.example_user("iago")
        call_command("armada_checks_user", "--like", iago.delivery_email, stdout=StringIO())
        user = get_user_by_delivery_email(ARMADA_CHECKS_EMAIL, iago.realm)
        self.assertEqual(user.role, UserProfile.ROLE_REALM_ADMINISTRATOR)
        self.assertEqual(self.channel_subscriptions(user), self.channel_subscriptions(iago))

    def test_development_only(self) -> None:
        with (
            self.settings(DEVELOPMENT=False),
            self.assertRaisesRegex(CommandError, "only runs in a development environment"),
        ):
            call_command("armada_checks_user")
