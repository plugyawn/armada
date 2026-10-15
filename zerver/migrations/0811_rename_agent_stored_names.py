from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

# Names the server stores and looks up, renamed with the code that reads
# them: (old, new).  What people read (the bot's name and email, mentions,
# the agent's own phrases) is renamed separately.
EMBEDDED_BOT_SERVICE = ("pear", "armada")
SELECTED_BOT_CONFIG_KEY = ("pear_selected", "armada_selected")
ARTIFACT_SUBMESSAGE_TYPE = ("pear_artifact", "artifact")
# An artifact's media sources are paths in the topic's worktree.
ARTIFACT_SOURCE_PREFIX = ('".pear/artifacts/', '".armada/artifacts/')

EMBEDDED_BOT = 4


def rename_stored_names(apps: StateApps, *, to_new_names: bool) -> None:
    Service = apps.get_model("zerver", "Service")
    BotConfigData = apps.get_model("zerver", "BotConfigData")
    # A restored message brings its archived submessages back, so the
    # archive carries the same type.
    submessage_models = [
        apps.get_model("zerver", "SubMessage"),
        apps.get_model("zerver", "ArchivedSubMessage"),
    ]

    def pick(pair: tuple[str, str]) -> tuple[str, str]:
        return pair if to_new_names else (pair[1], pair[0])

    old, new = pick(EMBEDDED_BOT_SERVICE)
    Service.objects.filter(name=old, user_profile__bot_type=EMBEDDED_BOT).update(name=new)

    old, new = pick(SELECTED_BOT_CONFIG_KEY)
    BotConfigData.objects.filter(key=old).exclude(
        bot_profile__in=BotConfigData.objects.filter(key=new).values("bot_profile")
    ).update(key=new)

    old, new = pick(ARTIFACT_SUBMESSAGE_TYPE)
    old_prefix, new_prefix = pick(ARTIFACT_SOURCE_PREFIX)
    for submessage_model in submessage_models:
        for submessage in submessage_model.objects.filter(msg_type=old):
            submessage.msg_type = new
            submessage.content = submessage.content.replace(old_prefix, new_prefix)
            submessage.save(update_fields=["msg_type", "content"])


def rename_stored_names_to_new_names(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    rename_stored_names(apps, to_new_names=True)


def rename_stored_names_to_old_names(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    rename_stored_names(apps, to_new_names=False)


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0810_rename_agent_models"),
    ]

    operations = [
        migrations.RunPython(
            rename_stored_names_to_new_names,
            reverse_code=rename_stored_names_to_old_names,
            elidable=True,
        ),
    ]
