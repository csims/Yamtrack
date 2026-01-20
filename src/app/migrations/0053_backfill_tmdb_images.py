from django.conf import settings
from django.db import migrations


def backfill_tmdb_images(apps, schema_editor):
    Item = apps.get_model("app", "Item")
    img_none = settings.IMG_NONE

    tv_items = Item.objects.filter(source="tmdb", media_type="tv").values(
        "media_id",
        "image",
    )
    tv_image_by_id = {
        row["media_id"]: row["image"]
        for row in tv_items
        if row["image"] != img_none
    }

    season_items = Item.objects.filter(source="tmdb", media_type="season").values(
        "id",
        "media_id",
        "season_number",
        "image",
    )
    season_updates = []
    season_image_by_key = {}
    for row in season_items:
        if row["image"] != img_none:
            season_image_by_key[(row["media_id"], row["season_number"])] = row["image"]
            continue

        fallback = tv_image_by_id.get(row["media_id"])
        if fallback:
            season_updates.append(Item(id=row["id"], image=fallback))
            season_image_by_key[(row["media_id"], row["season_number"])] = fallback

    if season_updates:
        Item.objects.bulk_update(season_updates, ["image"])

    episode_items = Item.objects.filter(source="tmdb", media_type="episode").values(
        "id",
        "media_id",
        "season_number",
        "image",
    )
    episode_updates = []
    for row in episode_items:
        if row["image"] != img_none:
            continue

        fallback = season_image_by_key.get((row["media_id"], row["season_number"]))
        if not fallback:
            fallback = tv_image_by_id.get(row["media_id"])

        if fallback:
            episode_updates.append(Item(id=row["id"], image=fallback))

    if episode_updates:
        Item.objects.bulk_update(episode_updates, ["image"])


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0052_alter_item_title"),
    ]

    operations = [
        migrations.RunPython(backfill_tmdb_images, migrations.RunPython.noop),
    ]
