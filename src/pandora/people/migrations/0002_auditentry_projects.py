from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_token_sources"),
        ("people", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditentry",
            name="projects",
            field=models.ManyToManyField(
                blank=True,
                related_name="audit_entries",
                to="core.project",
            ),
        ),
    ]
