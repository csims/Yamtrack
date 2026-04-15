from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Episode, Item, MediaTypes, Season, Sources, Status


class EpisodeTests(TestCase):
    """Test Episode-based season behavior."""

    def setUp(self):
        """Create a user, season, and episode items."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.item_ep1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        self.item_ep2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )

    def test_watch_creates_episode_watch(self):
        """Season.watch creates an Episode record."""
        watch_date = datetime(2023, 6, 1, 0, 0, tzinfo=UTC)
        self.season.watch(1, watch_date)

        watch = Episode.objects.get(
            related_season=self.season,
            item=self.item_ep1,
        )
        self.assertEqual(watch.end_date, watch_date)

    def test_progress_and_dates_from_episodes(self):
        """Season progress and dates use Episode timestamps."""
        Episode.objects.create(
            item=self.item_ep1,
            related_season=self.season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )
        Episode.objects.create(
            item=self.item_ep2,
            related_season=self.season,
            end_date=datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(self.season.progress, 2)
        self.assertEqual(
            self.season.start_date,
            datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            self.season.end_date,
            datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

    def test_unwatch_removes_latest_watch(self):
        """Season.unwatch removes the most recent watch."""
        Episode.objects.create(
            item=self.item_ep1,
            related_season=self.season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )
        Episode.objects.create(
            item=self.item_ep1,
            related_season=self.season,
            end_date=datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

        self.season.unwatch(1)

        remaining = Episode.objects.filter(
            related_season=self.season,
            item=self.item_ep1,
        )
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(
            remaining.first().end_date,
            datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )
