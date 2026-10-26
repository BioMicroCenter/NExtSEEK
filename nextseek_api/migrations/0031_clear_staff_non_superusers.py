from django.db import migrations


def clear_staff(apps, schema_editor):
    """Only superusers keep is_staff (the Django admin needs it); it admits nobody else to /admin/ any more."""
    User = apps.get_model("auth", "User")
    User.objects.filter(is_staff=True, is_superuser=False).update(is_staff=False)


class Migration(migrations.Migration):
    """SEC-H19. Reversible as a no-op: the cleared flags are not restored."""

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("nextseek_api", "0030_spill_json_fields"),
    ]

    operations = [migrations.RunPython(clear_staff, migrations.RunPython.noop)]
