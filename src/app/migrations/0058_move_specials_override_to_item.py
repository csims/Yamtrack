from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0057_delete_episode_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="is_specials_override",
            field=models.BooleanField(default=False),
        ),
        migrations.RemoveField(
            model_name="season",
            name="is_specials_override",
        ),
        migrations.RemoveField(
            model_name="historicalseason",
            name="is_specials_override",
        ),
    ]
