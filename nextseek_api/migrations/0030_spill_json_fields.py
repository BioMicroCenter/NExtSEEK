from django.db import migrations

import nextseek_api.assistant.spill


class Migration(migrations.Migration):
    """The chat JSON columns become SpillJSONField (big values go to files). Same column type: no SQL."""

    dependencies = [
        ('nextseek_api', '0029_uidsequence'),
    ]

    operations = [
        migrations.AlterField(
            model_name='chatsession',
            name='results_history',
            field=nextseek_api.assistant.spill.SpillJSONField(default=list),
        ),
        migrations.AlterField(
            model_name='chatsession',
            name='last_debug',
            field=nextseek_api.assistant.spill.SpillJSONField(default=dict),
        ),
        migrations.AlterField(
            model_name='chatsession',
            name='extra_state',
            field=nextseek_api.assistant.spill.SpillJSONField(default=dict),
        ),
        migrations.AlterField(
            model_name='querytask',
            name='result',
            field=nextseek_api.assistant.spill.SpillJSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='querytask',
            name='progress',
            field=nextseek_api.assistant.spill.SpillJSONField(default=list),
        ),
    ]
