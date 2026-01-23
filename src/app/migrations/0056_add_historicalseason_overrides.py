from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0055_backfill_episodewatch"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalseason",
            name="is_ignored",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="historicalseason",
            name="is_specials_override",
            field=models.BooleanField(default=False),
        ),
    ]
