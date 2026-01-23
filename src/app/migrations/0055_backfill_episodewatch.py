from django.db import migrations


def backfill_episodewatch(apps, schema_editor):
    Episode = apps.get_model("app", "Episode")
    EpisodeWatch = apps.get_model("app", "EpisodeWatch")

    batch_size = 1000
    buffer = []

    episodes = Episode.objects.filter(
        item_id__isnull=False,
        item__is_hidden_override=False,
    ).values_list("related_season_id", "item_id", "end_date")

    for related_season_id, item_id, end_date in episodes.iterator(chunk_size=batch_size):
        buffer.append(
            EpisodeWatch(
                related_season_id=related_season_id,
                item_id=item_id,
                watched_at=end_date,
                source="legacy",
            ),
        )
        if len(buffer) >= batch_size:
            EpisodeWatch.objects.bulk_create(buffer, batch_size=batch_size)
            buffer = []

    if buffer:
        EpisodeWatch.objects.bulk_create(buffer, batch_size=batch_size)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0054_add_episodewatch_and_overrides"),
    ]

    operations = [
        migrations.RunPython(backfill_episodewatch, migrations.RunPython.noop),
    ]
