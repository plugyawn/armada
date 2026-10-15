import re

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

# A link to an upload, alone or after the realm's address, as Zulip's
# renderer finds one (maybe_add_attachment_path_id in
# zerver/lib/markdown/__init__.py), up to the end of the link.
UPLOAD_LINK = re.compile(r"/user_uploads/([^\s()<>\[\]\"']+)")


def claim_uploads_of_existing_posts(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """A post written before posts claimed their uploads claims the ones
    it links to that its author owns. An author may always read their
    own uploads, so this claims nothing that writing the post now would
    not; an upload the author could read only through someone else's
    message or post waits for the post's next edit, which checks it."""
    Attachment = apps.get_model("zerver", "Attachment")
    Post = apps.get_model("zerver", "Post")
    posts = Post.objects.filter(content__contains="/user_uploads/").only(
        "id", "author_id", "content"
    )
    for post in posts:
        path_ids = set(UPLOAD_LINK.findall(post.content))
        post.attachments.add(
            *Attachment.objects.filter(path_id__in=path_ids, owner_id=post.author_id)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0816_post_attachments"),
    ]

    operations = [
        migrations.RunPython(
            claim_uploads_of_existing_posts,
            reverse_code=migrations.RunPython.noop,
            elidable=True,
        ),
    ]
