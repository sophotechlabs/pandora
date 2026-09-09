from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("issues", "0018_issue_priority"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="escalated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
