from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0017_paid_run_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="turnledger",
            name="attempted_route",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="turnledger",
            name="attempted_source",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]
