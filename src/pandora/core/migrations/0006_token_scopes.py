import django.db.models.deletion
from django.db import migrations, models


def copy_scopes(apps, schema_editor):
    alias = schema_editor.connection.alias
    IngestToken = apps.get_model("core", "IngestToken")
    TokenScopeGrant = apps.get_model("core", "TokenScopeGrant")
    grants = []
    for token in IngestToken.objects.using(alias).all().iterator():
        scopes = [token.scope]
        if token.scope == "ingest":
            scopes = ["ingest", "artifacts"]
        if token.scope == "payload":
            scopes = ["read", "payload"]
        for scope in scopes:
            grants.append(TokenScopeGrant(token_id=token.pk, scope=scope))
    TokenScopeGrant.objects.using(alias).bulk_create(grants)


def restore_scopes(apps, schema_editor):
    alias = schema_editor.connection.alias
    IngestToken = apps.get_model("core", "IngestToken")
    tokens = (
        IngestToken.objects.using(alias)
        .prefetch_related("scope_grants")
        .iterator(chunk_size=1000)
    )
    for token in tokens:
        scopes = {grant.scope for grant in token.scope_grants.all()}
        scope = "read"
        if "read" in scopes and "payload" in scopes:
            scope = "payload"
        elif "read" in scopes:
            scope = "read"
        elif "ingest" in scopes:
            scope = "ingest"
        else:
            token.active = False
        token.scope = scope
        token.save(using=alias, update_fields=["scope", "active"])


class Migration(migrations.Migration):
    dependencies = [("core", "0005_token_sources")]

    operations = [
        migrations.CreateModel(
            name="TokenScopeGrant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("ingest", "Ingest"),
                            ("artifacts", "Artifacts"),
                            ("deploy", "Deploy"),
                            ("read", "Read"),
                            ("payload", "Payload"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "token",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scope_grants",
                        to="core.ingesttoken",
                    ),
                ),
            ],
        ),
        migrations.RunPython(copy_scopes, restore_scopes),
        migrations.RemoveIndex(
            model_name="ingesttoken",
            name="core_token_src_scope",
        ),
        migrations.RemoveField(model_name="ingesttoken", name="scope"),
        migrations.AddIndex(
            model_name="ingesttoken",
            index=models.Index(
                fields=["source", "active"], name="core_token_src_active"
            ),
        ),
        migrations.AddConstraint(
            model_name="tokenscopegrant",
            constraint=models.UniqueConstraint(
                fields=("token", "scope"), name="core_token_scope_uq"
            ),
        ),
        migrations.AddIndex(
            model_name="tokenscopegrant",
            index=models.Index(
                fields=["scope", "token"], name="core_scope_token"
            ),
        ),
    ]
