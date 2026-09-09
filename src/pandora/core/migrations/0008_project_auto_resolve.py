from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_alter_ingesttoken_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="auto_resolve_days",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
