# Generated manually to bring existing Pear dev databases up to the
# current 0805 PearWorkerRun state.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0805_pearworkerrun"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE zerver_pearworkerrun
                ADD COLUMN IF NOT EXISTS requesting_user_id integer NULL,
                ADD COLUMN IF NOT EXISTS session_kind varchar(16) NOT NULL DEFAULT 'shared',
                ADD COLUMN IF NOT EXISTS source_stream_name varchar(60) NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS source_topic_name varchar(60) NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS source_message_id integer NULL,
                ADD COLUMN IF NOT EXISTS summary text NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS current_activity text NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS runtime_label text NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS model text NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS sandbox varchar(64) NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS approval_policy varchar(64) NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS workdir text NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS codex_thread_id text NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS date_finished timestamp with time zone NULL;

            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'zerver_pearworkerrun_requesting_user_id_fkey'
                ) THEN
                    ALTER TABLE zerver_pearworkerrun
                        ADD CONSTRAINT zerver_pearworkerrun_requesting_user_id_fkey
                        FOREIGN KEY (requesting_user_id)
                        REFERENCES zerver_userprofile(id)
                        DEFERRABLE INITIALLY DEFERRED;
                END IF;
            END $$;

            CREATE TABLE IF NOT EXISTS zerver_pearworkerrunevent (
                id bigserial PRIMARY KEY,
                event_type varchar(64) NOT NULL,
                summary text NOT NULL DEFAULT '',
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                date_created timestamp with time zone NOT NULL,
                run_id bigint NOT NULL
            );

            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'zerver_pearworkerrunevent_run_id_fkey'
                ) THEN
                    ALTER TABLE zerver_pearworkerrunevent
                        ADD CONSTRAINT zerver_pearworkerrunevent_run_id_fkey
                        FOREIGN KEY (run_id)
                        REFERENCES zerver_pearworkerrun(id)
                        DEFERRABLE INITIALLY DEFERRED;
                END IF;
            END $$;

            CREATE INDEX IF NOT EXISTS zerver_pearworkerrun_requesting_user_id
                ON zerver_pearworkerrun(requesting_user_id);
            CREATE INDEX IF NOT EXISTS zerver_pear_run_realm_kind
                ON zerver_pearworkerrun(realm_id, session_kind, date_created);
            CREATE INDEX IF NOT EXISTS zerver_pearworkerrunevent_event_type
                ON zerver_pearworkerrunevent(event_type);
            CREATE INDEX IF NOT EXISTS zerver_pearworkerrunevent_date_created
                ON zerver_pearworkerrunevent(date_created);
            CREATE INDEX IF NOT EXISTS zerver_pearworkerrunevent_run_id
                ON zerver_pearworkerrunevent(run_id);
            CREATE INDEX IF NOT EXISTS zerver_pear_run_event_run_date
                ON zerver_pearworkerrunevent(run_id, date_created);
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
