from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0056_add_historicalseason_overrides"),
    ]

    operations = [
        migrations.DeleteModel(name="HistoricalEpisode"),
        migrations.DeleteModel(name="Episode"),
    ]
