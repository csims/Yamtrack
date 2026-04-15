from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0053_backfill_tmdb_images"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="is_hidden_override",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="item",
            name="is_specials_override",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="season",
            name="is_ignored",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="historicalseason",
            name="is_ignored",
            field=models.BooleanField(default=False),
        ),
    ]
