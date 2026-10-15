# Demo-user roster for reset_remote_dev_data.sh.
#
# Copy this file to armada_roster.local.py (gitignored) and fill in the real
# account names and emails for your deployment. The roster is deployment
# data, not source: keeping it untracked keeps personal identities out of
# the repository.
#
# Each row maps one of populate_db's canonical dev users onto a demo
# identity: (populate_db email, full name, new email, role, is_staff).
# Exactly one row must carry UserProfile.ROLE_REALM_OWNER; the reset
# script uses it to run the Pear config bootstrap.
users = [
    ("desdemona@zulip.com", "demo-owner", "demo-owner@example.com", UserProfile.ROLE_REALM_OWNER, False),
    ("iago@zulip.com", "demo-admin", "demo-admin@example.com", UserProfile.ROLE_REALM_ADMINISTRATOR, True),
    ("shiva@zulip.com", "demo-member-1", "demo-member-1@example.com", UserProfile.ROLE_MEMBER, False),
    ("ZOE@zulip.com", "demo-member-2", "demo-member-2@example.com", UserProfile.ROLE_MEMBER, False),
    ("AARON@zulip.com", "demo-member-3", "demo-member-3@example.com", UserProfile.ROLE_MEMBER, False),
    ("hamlet@zulip.com", "demo-member-4", "demo-member-4@example.com", UserProfile.ROLE_MEMBER, False),
]
