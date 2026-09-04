import django.db.models.deletion
from django.db import migrations, models


def copy_identity(apps, schema_editor):
    alias = schema_editor.connection.alias
    Deploy = apps.get_model("releases", "Deploy")
    deploys = Deploy.objects.using(alias).select_related("release").iterator()
    for deploy in deploys:
        deploy.project_id = deploy.release.project_id
        deploy.identifier = f"legacy:{deploy.pk}"
        deploy.save(using=alias, update_fields=["project", "identifier"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_token_scopes"),
        ("releases", "0002_session_bucket"),
    ]

    operations = [
        migrations.AddField(
            model_name="deploy",
            name="identifier",
            field=models.CharField(blank=True, default="", max_length=128),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="deploy",
            name="project",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="deploys",
                to="core.project",
            ),
        ),
        migrations.RunPython(copy_identity, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="deploy",
            name="identifier",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="deploy",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="deploys",
                to="core.project",
            ),
        ),
        migrations.AddConstraint(
            model_name="deploy",
            constraint=models.UniqueConstraint(
                fields=("project", "identifier"),
                name="releases_deploy_identity_uq",
            ),
        ),
    ]
