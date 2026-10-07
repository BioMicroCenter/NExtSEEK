from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0026_ccturn_ops_cost_partial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ccturn',
            name='ops_cost_estimated',
            field=models.BooleanField(default=False),
        ),
    ]
