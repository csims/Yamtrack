from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    TV,
    EpisodeWatch,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)


class MediaDetailsViewTests(TestCase):
    """Test the media details views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_view(self, mock_get_metadata):
        """Test the media details view."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Test Movie")

        mock_get_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "238",
            Sources.TMDB.value,
        )

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_view(self, mock_process_episodes, mock_get_metadata):
        """Test the season details view."""
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "season_number": 1,
                "episodes": [],
            },
        }

        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "name": "Episode 1",
                "air_date": "2023-01-01",
                "watched": False,
            },
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Season 1")
        self.assertEqual(len(response.context["media"]["episodes"]), 1)

        mock_get_metadata.assert_called_once_with(
            "tv_with_seasons",
            "1668",
            Sources.TMDB.value,
            [1],
        )

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_uses_known_episode_total_for_progress(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Season detail progress should use known episode totals, not aired counts."""
        tv_item = Item.objects.create(
            media_id="279388",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Pursuit of Jade",
            image="http://example.com/tv.jpg",
        )
        season_item = Item.objects.create(
            media_id="279388",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Chasing Jade",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.filter(pk=tv.pk).update(status=Status.IN_PROGRESS.value)

        for episode_number in range(1, 41):
            episode_item = Item.objects.create(
                media_id="279388",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Chasing Jade",
                image="http://example.com/episode.jpg",
                season_number=1,
                episode_number=episode_number,
            )
            EpisodeWatch.objects.create(
                item=episode_item,
                related_season=season,
            )

        mock_get_metadata.return_value = {
            "title": "Pursuit of Jade",
            "media_id": "279388",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/tv.jpg",
            "season/1": {
                "title": "Chasing Jade",
                "media_id": "279388",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "season_number": 1,
                "episodes": [{"episode_number": episode} for episode in range(1, 41)],
            },
        }
        mock_process_episodes.return_value = [
            {
                "media_id": "279388",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": episode_number,
                "name": f"Episode {episode_number}",
                "air_date": "2026-03-26",
                "watched": True,
            }
            for episode_number in range(1, 41)
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "279388",
                    "title": "pursuit-of-jade",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_instance"].progress, 40)
        self.assertEqual(response.context["current_instance"].max_progress, 40)

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_ignores_unknown_unwatched_episodes_for_progress(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Season detail progress should ignore unknown-date unwatched episodes."""
        tv_item = Item.objects.create(
            media_id="273174",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="The Bangkok Boy",
            image="http://example.com/tv.jpg",
        )
        season_item = Item.objects.create(
            media_id="273174",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="The Bangkok Boy",
            image="http://example.com/season.jpg",
            season_number=2,
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.filter(pk=tv.pk).update(status=Status.IN_PROGRESS.value)

        for episode_number in range(1, 13):
            episode_item = Item.objects.create(
                media_id="273174",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="The Bangkok Boy",
                image="http://example.com/episode.jpg",
                season_number=2,
                episode_number=episode_number,
            )
            EpisodeWatch.objects.create(
                item=episode_item,
                related_season=season,
            )

        mock_get_metadata.return_value = {
            "title": "The Bangkok Boy",
            "media_id": "273174",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/tv.jpg",
            "season/2": {
                "title": "The Bangkok Boy",
                "media_id": "273174",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "season_number": 2,
                "episodes": [{"episode_number": episode} for episode in range(1, 14)],
            },
        }
        aired_episode_count = 12
        mock_process_episodes.return_value = [
            {
                "media_id": "273174",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 2,
                "episode_number": episode_number,
                "name": f"Episode {episode_number}",
                "air_date": "2026-03-26"
                if episode_number <= aired_episode_count
                else None,
                "history": season.episode_watches.filter(
                    item__episode_number=episode_number,
                ),
            }
            for episode_number in range(1, 14)
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "273174",
                    "title": "the-bangkok-boy",
                    "season_number": 2,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_instance"].progress, 12)
        self.assertEqual(response.context["current_instance"].max_progress, 12)

    @patch("app.views.Item.fetch_releases")
    @patch("app.providers.tmdb.process_episodes")
    @patch("app.providers.services.get_media_metadata")
    def test_sync_metadata_for_season_creates_missing_episode_items_and_reconciles(
        self,
        mock_get_metadata,
        mock_process_episodes,
        mock_fetch_releases,
    ):
        """Season sync should create missing episodes and reconcile events."""
        mock_get_metadata.return_value = {
            "title": "Chasing Jade",
            "media_id": "279388",
            "media_type": MediaTypes.SEASON.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/season.jpg",
            "season_number": 1,
            "episodes": [],
        }
        mock_process_episodes.return_value = [
            {
                "episode_number": episode_number,
                "image": f"http://example.com/episode{episode_number}.jpg",
            }
            for episode_number in range(1, 4)
        ]

        response = self.client.post(
            reverse(
                "sync_metadata",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.SEASON.value,
                    "media_id": "279388",
                    "season_number": 1,
                },
            ),
            HTTP_REFERER="/",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Item.objects.filter(
                media_id="279388",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
            ).count(),
            3,
        )
        mock_fetch_releases.assert_called_once()
        self.assertEqual(
            mock_fetch_releases.call_args.kwargs,
            {
                "delay": False,
                "authoritative_reconcile": True,
            },
        )

    @patch("app.views.Item.fetch_releases")
    @patch("app.providers.services.get_media_metadata")
    def test_sync_metadata_for_movie_keeps_authoritative_reconcile_disabled(
        self,
        mock_get_metadata,
        mock_fetch_releases,
    ):
        """Non-TV sync should keep authoritative reconcile disabled."""
        mock_get_metadata.return_value = {
            "title": "Test Movie",
            "media_id": "238",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/movie.jpg",
        }

        response = self.client.post(
            reverse(
                "sync_metadata",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            ),
            HTTP_REFERER="/",
        )

        self.assertEqual(response.status_code, 302)
        mock_fetch_releases.assert_called_once()
        self.assertEqual(
            mock_fetch_releases.call_args.kwargs,
            {
                "delay": False,
                "authoritative_reconcile": False,
            },
        )

    def test_toggle_season_ignore(self):
        """Test toggling the season ignore flag."""
        item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/season.jpg",
            season_number=1,
        )

        response = self.client.post(
            reverse(
                "toggle_season_ignore",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "season_number": 1,
                },
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        season = Season.objects.get(user=self.user, item=item)
        self.assertTrue(season.is_ignored)

        response = self.client.post(
            reverse(
                "toggle_season_ignore",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "season_number": 1,
                },
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        season.refresh_from_db()
        self.assertFalse(season.is_ignored)
