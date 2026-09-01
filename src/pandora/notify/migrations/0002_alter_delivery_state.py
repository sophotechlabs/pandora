from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notify", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="delivery",
            name="state",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("sending", "Sending"),
                    ("sent", "Sent"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=8,
            ),
        ),
    ]
