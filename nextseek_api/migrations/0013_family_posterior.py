import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nextseek_api", "0012_posterior_generation"),
    ]

    operations = [
        migrations.CreateModel(
            name="FamilyPosterior",
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
                ("task_family", models.CharField(max_length=128)),
                ("route", models.CharField(max_length=64)),
                ("posterior_mean", models.FloatField()),
                ("band", models.CharField(max_length=32)),
                ("n_total", models.IntegerField()),
                ("fitted_at", models.DateTimeField()),
                (
                    "generation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="posteriors",
                        to="nextseek_api.posteriorgeneration",
                    ),
                ),
            ],
            options={
                "db_table": "eval_family_posterior",
            },
        ),
        migrations.AddConstraint(
            model_name="familyposterior",
            constraint=models.UniqueConstraint(
                fields=("generation", "task_family", "route"),
                name="uniq_generation_family_route",
            ),
        ),
    ]
