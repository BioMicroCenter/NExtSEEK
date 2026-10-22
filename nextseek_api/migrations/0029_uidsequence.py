from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0028_seekpersonlink'),
    ]

    operations = [
        migrations.CreateModel(
            name='UidSequence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('prefix', models.CharField(max_length=191, unique=True)),
                ('last_index', models.PositiveBigIntegerField(default=0)),
            ],
        ),
    ]
