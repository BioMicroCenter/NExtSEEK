from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0003_chatsession_extra_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='title',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
