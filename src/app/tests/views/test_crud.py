import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import (
    TV,
    Anime,
    EpisodeWatch,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class CreateMedia(TestCase):
    """Test the creation of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_anime(self):
        """Test the creation of a TV object."""
        Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1",
                "source": Sources.MAL.value,
                "media_type": MediaTypes.ANIME.value,
                "status": Status.PLANNING.value,
                "progress": 0,
                "repeats": 0,
            },
        )
        self.assertEqual(
            Anime.objects.filter(item__media_id="1", user=self.user).exists(),
            True,
        )

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_tv(self):
        """Test the creation of a TV object through views."""
        Item.objects.create(
            media_id="5895",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "5895",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            TV.objects.filter(item__media_id="5895", user=self.user).exists(),
            True,
        )

    def test_create_season(self):
        """Test the creation of a Season through views."""
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            Season.objects.filter(item__media_id="1668", user=self.user).exists(),
            True,
        )

    def test_create_episodes(self):
        """Test the creation of Episode through views."""
        self.client.post(
            reverse("episode_save"),
            {
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "watched_at": "2023-06-01T00:00",
            },
        )
        self.assertEqual(
            EpisodeWatch.objects.filter(
                item__media_id="1668",
                related_season__user=self.user,
                item__episode_number=1,
            ).exists(),
            True,
        )


class EditMedia(TestCase):
    """Test the editing of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_edit_movie_score(self):
        """Test the editing of a movie score."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            score=9,
            progress=1,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

        self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 1,
                "status": Status.COMPLETED.value,
                "notes": "Nice",
            },
        )
        self.assertEqual(Movie.objects.get(item__media_id="10494").score, 10)


class DeleteMedia(TestCase):
    """Test the deletion of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=self.item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.item_ep = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        self.episode = EpisodeWatch.objects.create(
            item=self.item_ep,
            related_season=self.season,
            watched_at=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

    def test_delete_tv(self):
        """Test the deletion of a tv through views."""
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        tv_obj = TV.objects.get(user=self.user)

        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": tv_obj.id,
                "media_type": MediaTypes.TV.value,
            },
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_delete_season(self):
        """Test the deletion of a season through views."""
        self.client.post(
            reverse(
                "media_delete",
            ),
            data={"instance_id": self.season.id, "media_type": MediaTypes.SEASON.value},
        )

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            EpisodeWatch.objects.filter(related_season__user=self.user).count(),
            0,
        )


class EpisodeHtmxCrudTests(TestCase):
    """Test HTMX episode tracking updates on the season details page."""

    def setUp(self):
        """Create a user, log in, and create a season."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=self.season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="The Pilot",
            image="http://example.com/episode.jpg",
            season_number=1,
            episode_number=1,
        )
        self.episode = EpisodeWatch.objects.create(
            item=self.episode_item,
            related_season=self.season,
            watched_at=datetime.datetime(2023, 5, 1, 0, 0, tzinfo=datetime.UTC),
        )

    @staticmethod
    def _mock_season_metadata():
        return {
            "title": "Friends",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/tv.jpg",
            "season/1": {
                "title": "Friends Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "season_number": 1,
                "episodes": [],
            },
        }

    @staticmethod
    def _mock_processed_episodes():
        watched_at = datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC)
        return [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "title": "The Pilot",
                "image": "http://example.com/episode.jpg",
                "air_date": "2023-01-01",
                "runtime": "22m",
                "overview": "Pilot episode overview.",
                "history": [
                    {
                        "id": 1,
                        "watched_at": watched_at,
                    },
                ],
            },
        ]

    @patch("app.models.Item.fetch_releases")
    @patch("app.views.tmdb.process_episodes")
    @patch("app.views.services.get_media_metadata")
    def test_episode_save_htmx_returns_updated_fragments(
        self,
        mock_get_metadata,
        mock_process_episodes,
        mock_fetch_releases,
    ):
        """HTMX episode save should return row and season fragment updates."""
        mock_get_metadata.return_value = self._mock_season_metadata()
        mock_process_episodes.return_value = self._mock_processed_episodes()
        mock_fetch_releases.return_value = None

        response = self.client.post(
            reverse("episode_save") + "?next=/season",
            data={
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "watched_at": "2023-06-01T00:00",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="episode-row-episode-1668-1-1"')
        self.assertContains(response, 'id="season-track-status-season-1668-1"')
        self.assertContains(response, 'hx-swap-oob="outerHTML"', count=2)
        self.assertContains(response, 'hx-post="/episode_save?next=/season"')
        self.assertTrue(
            EpisodeWatch.objects.filter(
                related_season__user=self.user,
                item__episode_number=1,
            ).exists(),
        )

    @patch("app.views.tmdb.process_episodes")
    @patch("app.views.services.get_media_metadata")
    def test_episode_delete_htmx_returns_updated_fragments(
        self,
        mock_get_metadata,
        mock_process_episodes,
    ):
        """HTMX episode delete should return row and season fragment updates."""
        mock_get_metadata.return_value = self._mock_season_metadata()
        mock_process_episodes.return_value = self._mock_processed_episodes()

        watch = EpisodeWatch.objects.create(
            item=self.episode_item,
            related_season=self.season,
            watched_at=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

        response = self.client.post(
            reverse("media_delete") + "?next=/season",
            data={
                "instance_id": watch.id,
                "media_type": "episodewatch",
                "media_id": "1668",
                "season_number": 1,
                "source": Sources.TMDB.value,
                "episode_number": 1,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="episode-row-episode-1668-1-1"')
        self.assertContains(response, 'id="season-tracking-sidebar-season-1668-1"')
        self.assertContains(response, 'hx-swap-oob="outerHTML"', count=2)
        self.assertFalse(EpisodeWatch.objects.filter(pk=watch.pk).exists())

    def test_unwatch_episode(self):
        """Test unwatching of an episode through views."""
        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": self.episode.id,
                "media_type": "episodewatch",
            },
        )

        self.assertEqual(
            EpisodeWatch.objects.filter(related_season__user=self.user).count(),
            0,
        )
