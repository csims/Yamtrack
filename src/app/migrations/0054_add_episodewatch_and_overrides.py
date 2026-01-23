from django.db import migrations, models
import django.db.models.deletion


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
            model_name="season",
            name="is_ignored",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="season",
            name="is_specials_override",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="EpisodeWatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("watched_at", models.DateTimeField(blank=True, null=True)),
                ("source", models.CharField(blank=True, default="", max_length=30)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="app.item")),
                (
                    "related_season",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="episode_watches", to="app.season"),
                ),
            ],
            options={
                "ordering": [
                    "related_season",
                    "item__episode_number",
                    "-watched_at",
                    "-created_at",
                ],
            },
        ),
        migrations.AddIndex(
            model_name="episodewatch",
            index=models.Index(fields=["related_season", "item"], name="app_episode_related_521822_idx"),
        ),
        migrations.AddIndex(
            model_name="episodewatch",
            index=models.Index(fields=["related_season", "watched_at"], name="app_episode_related_b6b5fd_idx"),
        ),
    ]
