from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0002_querytask'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='title',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
