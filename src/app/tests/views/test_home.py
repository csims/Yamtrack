from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Anime,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)
from events.models import Event
from users.models import HomeSortChoices


class HomeViewTests(TestCase):
    """Test the home view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
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

        for i in range(1, 6):  # Create 5 episodes
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Test TV Show",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=timezone.now() - timezone.timedelta(days=i),
            )

        for i in range(1, 9):
            Event.objects.create(
                item=season_item,
                content_number=i,
                datetime=timezone.now() - timezone.timedelta(days=9 - i),
            )

        anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=10,
        )

    def test_home_view(self):
        """Test the home view displays in-progress media."""
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/home.html")

        self.assertIn("list_by_type", response.context)
        self.assertIn(MediaTypes.TV.value, response.context["list_by_type"])
        self.assertIn(MediaTypes.ANIME.value, response.context["list_by_type"])

        self.assertIn("sort_choices", response.context)
        self.assertEqual(response.context["sort_choices"], HomeSortChoices.choices)

        tv_list = response.context["list_by_type"][MediaTypes.TV.value]
        self.assertEqual(len(tv_list["items"]), 1)
        self.assertEqual(tv_list["items"][0].progress, 5)
        self.assertEqual(tv_list["items"][0].home_remaining_count, 3)
        self.assertEqual(tv_list["items"][0].home_display_title, "Test TV Show S1 E6")
        self.assertIsNone(tv_list["items"][0].home_episode_badge)
        self.assertContains(response, "3 remaining")

        season_url = reverse(
            "season_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_id": "1668",
                "title": "test-tv-show",
                "season_number": 1,
            },
        )
        self.assertContains(response, season_url)

    def test_home_view_ignores_unknown_air_date_episodes(self):
        """Unknown-air-date placeholder events don't keep a finished show on home."""
        season1_item = Item.objects.create(
            media_id="273174",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Unknown Date Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        season2_item = Item.objects.create(
            media_id="273174",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Unknown Date Show",
            image="http://example.com/image.jpg",
            season_number=2,
        )
        tv_item = Item.objects.create(
            media_id="273174",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Unknown Date Show",
            image="http://example.com/image.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        season1 = Season.objects.create(
            item=season1_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.filter(pk=tv.pk).update(status=Status.IN_PROGRESS.value)
        tv.status = Status.IN_PROGRESS.value

        for episode_number in range(1, 13):
            episode_item = Item.objects.create(
                media_id="273174",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Unknown Date Show",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=episode_number,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season1,
                end_date=timezone.now() - timezone.timedelta(days=episode_number),
            )
            Event.objects.create(
                item=season1_item,
                content_number=episode_number,
                datetime=timezone.now() - timezone.timedelta(days=episode_number + 20),
            )

        Event.objects.create(
            item=season2_item,
            content_number=1,
            datetime=datetime.min.replace(tzinfo=UTC),
        )

        Season.objects.filter(pk=season1.pk).update(status=Status.COMPLETED.value)
        season1.status = Status.COMPLETED.value

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        tv_items = response.context["list_by_type"][MediaTypes.TV.value]["items"]
        self.assertFalse(any(item.item.media_id == "273174" for item in tv_items))

    def test_home_view_hides_show_when_remaining_episodes_are_unknown_date(self):
        """TV home should hide shows when only unknown-date episodes remain."""
        watched_episode_count = 20
        real_dated_episode_count = 20
        season_item = Item.objects.create(
            media_id="279388",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Chasing Jade",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        tv_item = Item.objects.create(
            media_id="279388",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Pursuit of Jade",
            image="http://example.com/image.jpg",
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
        tv.status = Status.IN_PROGRESS.value

        for episode_number in range(1, 41):
            episode_item = Item.objects.create(
                media_id="279388",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Chasing Jade",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=episode_number,
            )
            if episode_number <= watched_episode_count:
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=timezone.now() - timezone.timedelta(days=episode_number),
                )

            Event.objects.create(
                item=season_item,
                content_number=episode_number,
                datetime=(
                    timezone.now() - timezone.timedelta(days=episode_number)
                    if episode_number <= real_dated_episode_count
                    else datetime.min.replace(tzinfo=UTC)
                ),
            )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        tv_items = response.context["list_by_type"][MediaTypes.TV.value]["items"]
        self.assertFalse(any(item.item.media_id == "279388" for item in tv_items))

    def test_home_view_counts_only_past_aired_or_watched_episodes(self):
        """TV home should only count watched or past-aired episodes."""
        watched_episode_count = 17
        past_aired_episode_count = 19
        season1_item = Item.objects.create(
            media_id="949494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Only Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        season2_item = Item.objects.create(
            media_id="949494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Only Friends",
            image="http://example.com/image.jpg",
            season_number=2,
        )
        tv_item = Item.objects.create(
            media_id="949494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Only Friends",
            image="http://example.com/image.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        season1 = Season.objects.create(
            item=season1_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        season2 = Season.objects.create(
            item=season2_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.filter(pk=tv.pk).update(status=Status.IN_PROGRESS.value)
        tv.status = Status.IN_PROGRESS.value

        for episode_number in range(1, 13):
            episode_item = Item.objects.create(
                media_id="949494",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Only Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=episode_number,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season1,
                end_date=timezone.now() - timezone.timedelta(days=episode_number),
            )
            Event.objects.create(
                item=season1_item,
                content_number=episode_number,
                datetime=timezone.now() - timezone.timedelta(days=episode_number + 30),
            )

        for episode_number in range(1, 13):
            episode_item = Item.objects.create(
                media_id="949494",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Only Friends",
                image="http://example.com/image.jpg",
                season_number=2,
                episode_number=episode_number,
            )
            if episode_number <= watched_episode_count - 12:
                Episode.objects.create(
                    item=episode_item,
                    related_season=season2,
                    end_date=timezone.now() - timezone.timedelta(days=episode_number),
                )

            Event.objects.create(
                item=season2_item,
                content_number=episode_number,
                datetime=(
                    timezone.now() - timezone.timedelta(days=episode_number)
                    if episode_number <= past_aired_episode_count - 12
                    else timezone.now() + timezone.timedelta(days=episode_number)
                ),
            )

        Season.objects.filter(pk=season1.pk).update(status=Status.COMPLETED.value)
        season1.status = Status.COMPLETED.value

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        tv_items = response.context["list_by_type"][MediaTypes.TV.value]["items"]
        only_friends = next(item for item in tv_items if item.item.media_id == "949494")
        self.assertEqual(only_friends.progress, 17)
        self.assertEqual(only_friends.home_remaining_count, 2)
        self.assertContains(response, "2 remaining")

    def test_home_view_with_sort(self):
        """Test the home view with sorting parameter."""
        response = self.client.get(reverse("home") + "?sort=completion")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "completion")

        self.user.refresh_from_db()
        self.assertEqual(self.user.home_sort, "completion")

    def test_home_watch_next_episode_htmx(self):
        """Test watching next episode from TV home card without full reload."""
        headers = {"HTTP_HX_REQUEST": "true"}
        tv = self.user.tv_set.get(item__media_id="1668")
        response = self.client.post(
            reverse("home_watch_next_episode", kwargs={"instance_id": tv.id}),
            {"watch_on": "now"},
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/home_card.html")
        self.assertContains(response, "Test TV Show S1 E7")
        self.assertTrue(
            Episode.objects.filter(
                related_season__related_tv=tv,
                item__episode_number=6,
            ).exists(),
        )

    def test_home_view_out_of_order_fallback_to_earliest_unwatched(self):
        """Test fallback to earliest unwatched aired episode.

        Applies when next-after-max is unavailable.
        """
        season_item = Item.objects.create(
            media_id="9000",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Fallback Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        tv_item = Item.objects.create(
            media_id="9000",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Fallback Show",
            image="http://example.com/image.jpg",
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
        episode_item = Item.objects.create(
            media_id="9000",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Fallback Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=7,
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=timezone.now(),
        )
        for i in range(1, 8):
            Event.objects.create(
                item=season_item,
                content_number=i,
                datetime=timezone.now() - timezone.timedelta(days=10 - i),
            )

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

        tv_items = response.context["list_by_type"][MediaTypes.TV.value]["items"]
        fallback_tv = next(tv for tv in tv_items if tv.item.media_id == "9000")
        self.assertEqual(fallback_tv.home_display_title, "Fallback Show S1 E1")

    @patch("app.providers.services.get_media_metadata")
    def test_home_view_htmx_load_more(self, mock_get_media_metadata):
        """Test the HTMX load more functionality."""
        mock_get_media_metadata.return_value = {
            "title": "Test TV Show",
            "image": "http://example.com/image.jpg",
            "season/1": {
                "episodes": [{"id": 1}, {"id": 2}, {"id": 3}],  # 3 episodes
            },
            "related": {
                "seasons": [
                    {"season_number": 1, "image": "http://example.com/image.jpg"},
                ],  # Only one season
            },
        }

        for i in range(6, 20):  # Create 14 more TV shows (we already have 1)
            season_item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title=f"Test TV Show {i}",
                image="http://example.com/image.jpg",
                season_number=1,
            )
            tv_item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title=f"Test TV Show {i}",
                image="http://example.com/image.jpg",
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

            episode_item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Test TV Show {i}",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=1,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=timezone.now(),
            )
            Event.objects.create(
                item=season_item,
                content_number=2,
                datetime=timezone.now() - timezone.timedelta(days=1),
            )

        # Now test the load more functionality
        headers = {"HTTP_HX_REQUEST": "true"}
        response = self.client.get(
            reverse("home") + "?load_media_type=tv",
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/home_grid.html")

        self.assertIn("media_list", response.context)

        self.assertIn("items", response.context["media_list"])
        self.assertIn("total", response.context["media_list"])

        # Since we're loading more (items after the first 14),
        # we should have at least 1 item in the response
        self.assertEqual(len(response.context["media_list"]["items"]), 1)
        self.assertEqual(
            response.context["media_list"]["total"],
            15,
        )  # 15 TV shows total

    def test_home_view_moves_to_next_season_when_current_season_completed(self):
        """Test home card continues at next aired episode in a later season."""
        season1 = Season.objects.get(
            user=self.user,
            item__media_id="1668",
            item__season_number=1,
        )
        for episode_number in [6, 7, 8]:
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Test TV Show",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=episode_number,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season1,
                end_date=timezone.now(),
            )

        season2_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
            season_number=2,
        )
        for episode_number in [1, 2]:
            Event.objects.create(
                item=season2_item,
                content_number=episode_number,
                datetime=timezone.now() - timezone.timedelta(days=1),
            )

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

        tv_items = response.context["list_by_type"][MediaTypes.TV.value]["items"]
        tv = next(item for item in tv_items if item.item.media_id == "1668")
        self.assertEqual(tv.home_display_title, "Test TV Show S2 E1")
        self.assertEqual(tv.home_episode_badge, "Premiere")

        htmx_headers = {"HTTP_HX_REQUEST": "true"}
        watch_response = self.client.post(
            reverse("home_watch_next_episode", kwargs={"instance_id": tv.id}),
            {"watch_on": "now"},
            **htmx_headers,
        )
        self.assertEqual(watch_response.status_code, 200)
        self.assertTrue(
            Season.objects.filter(
                user=self.user,
                item=season2_item,
                related_tv=tv,
            ).exists(),
        )
        self.assertTrue(
            Episode.objects.filter(
                related_season__item=season2_item,
                item__episode_number=1,
            ).exists(),
        )

    def test_home_view_shows_finale_badge(self):
        """Test home card marks the selected last episode as Finale."""
        season1 = Season.objects.get(
            user=self.user,
            item__media_id="1668",
            item__season_number=1,
        )
        for episode_number in [6, 7]:
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Test TV Show",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=episode_number,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season1,
                end_date=timezone.now(),
            )

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

        tv_items = response.context["list_by_type"][MediaTypes.TV.value]["items"]
        tv = next(item for item in tv_items if item.item.media_id == "1668")
        self.assertEqual(tv.home_display_title, "Test TV Show S1 E8")
        self.assertEqual(tv.home_episode_badge, "Finale")

    def test_home_watch_marks_season_completed_when_all_non_hidden_aired_watched(self):
        """Test home watch auto-completes a season.

        Completes when all eligible aired episodes are watched.
        """
        season_item = Item.objects.create(
            media_id="9500",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Completion Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        tv_item = Item.objects.create(
            media_id="9500",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Completion Show",
            image="http://example.com/image.jpg",
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
        first_episode_item = Item.objects.create(
            media_id="9500",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Completion Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=first_episode_item,
            related_season=season,
            end_date=timezone.now(),
        )
        Item.objects.create(
            media_id="9500",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Completion Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        for episode_number in [1, 2]:
            Event.objects.create(
                item=season_item,
                content_number=episode_number,
                datetime=timezone.now() - timezone.timedelta(days=1),
            )

        headers = {"HTTP_HX_REQUEST": "true"}
        response = self.client.post(
            reverse("home_watch_next_episode", kwargs={"instance_id": tv.id}),
            {"watch_on": "now"},
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        season.refresh_from_db()
        self.assertEqual(season.status, Status.COMPLETED.value)

    def test_home_watch_does_not_complete_season_with_unaired_non_hidden_episodes(self):
        """Season should remain in progress if any non-hidden episode is unaired."""
        season_item = Item.objects.create(
            media_id="9600",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Unaired Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        tv_item = Item.objects.create(
            media_id="9600",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Unaired Show",
            image="http://example.com/image.jpg",
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
        Item.objects.create(
            media_id="9600",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Unaired Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        second_episode = Item.objects.create(
            media_id="9600",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Unaired Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        Episode.objects.create(
            item=Item.objects.get(
                media_id="9600",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=1,
            ),
            related_season=season,
            end_date=timezone.now(),
        )
        Event.objects.create(
            item=season_item,
            content_number=1,
            datetime=timezone.now() - timezone.timedelta(days=2),
        )
        Event.objects.create(
            item=season_item,
            content_number=2,
            datetime=timezone.now() - timezone.timedelta(days=1),
        )
        Event.objects.create(
            item=season_item,
            content_number=3,
            datetime=timezone.now() + timezone.timedelta(days=7),
        )

        headers = {"HTTP_HX_REQUEST": "true"}
        response = self.client.post(
            reverse("home_watch_next_episode", kwargs={"instance_id": tv.id}),
            {"watch_on": "now"},
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        season.refresh_from_db()
        self.assertEqual(season.status, Status.IN_PROGRESS.value)
        self.assertTrue(
            Episode.objects.filter(
                related_season=season,
                item=second_episode,
            ).exists(),
        )

    def test_home_view_premiere_finale_ignore_hidden_episodes(self):
        """Premiere/Finale badges should ignore hidden episodes."""
        season_item = Item.objects.create(
            media_id="9700",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Hidden Badge Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        tv_item = Item.objects.create(
            media_id="9700",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Hidden Badge Show",
            image="http://example.com/image.jpg",
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
        Item.objects.create(
            media_id="9700",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Hidden Badge Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
            is_hidden_override=True,
        )
        visible_episode_2 = Item.objects.create(
            media_id="9700",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Hidden Badge Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        visible_episode_4 = Item.objects.create(
            media_id="9700",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Hidden Badge Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=4,
        )
        Item.objects.create(
            media_id="9700",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Hidden Badge Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
            is_hidden_override=True,
        )
        for episode_number in [1, 2, 3]:
            Event.objects.create(
                item=season_item,
                content_number=episode_number,
                datetime=timezone.now() - timezone.timedelta(days=1),
            )
        Episode.objects.create(
            item=visible_episode_4,
            related_season=season,
            end_date=timezone.now(),
        )

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        tv_items = response.context["list_by_type"][MediaTypes.TV.value]["items"]
        picked_tv = next(item for item in tv_items if item.item.media_id == "9700")
        self.assertEqual(picked_tv.home_display_title, "Hidden Badge Show S1 E2")
        self.assertEqual(picked_tv.home_episode_badge, "Premiere • Finale")

        headers = {"HTTP_HX_REQUEST": "true"}
        self.client.post(
            reverse("home_watch_next_episode", kwargs={"instance_id": tv.id}),
            {"watch_on": "now"},
            **headers,
        )
        season.refresh_from_db()
        self.assertEqual(season.status, Status.COMPLETED.value)
        self.assertTrue(
            Episode.objects.filter(
                related_season=season,
                item=visible_episode_2,
            ).exists(),
        )

    def test_home_watch_can_complete_with_unaired_hidden_episode(self):
        """Hidden unaired episodes should not prevent season completion."""
        season_item = Item.objects.create(
            media_id="9800",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Hidden Unaired Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        tv_item = Item.objects.create(
            media_id="9800",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Hidden Unaired Show",
            image="http://example.com/image.jpg",
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
        first_episode_item = Item.objects.create(
            media_id="9800",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Hidden Unaired Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Item.objects.create(
            media_id="9800",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Hidden Unaired Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
            is_hidden_override=True,
        )
        third_episode_item = Item.objects.create(
            media_id="9800",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Hidden Unaired Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        Episode.objects.create(
            item=first_episode_item,
            related_season=season,
            end_date=timezone.now(),
        )
        Event.objects.create(
            item=season_item,
            content_number=1,
            datetime=timezone.now() - timezone.timedelta(days=2),
        )
        Event.objects.create(
            item=season_item,
            content_number=2,
            datetime=timezone.now() + timezone.timedelta(days=10),
        )
        Event.objects.create(
            item=season_item,
            content_number=3,
            datetime=timezone.now() - timezone.timedelta(days=1),
        )

        headers = {"HTTP_HX_REQUEST": "true"}
        response = self.client.post(
            reverse("home_watch_next_episode", kwargs={"instance_id": tv.id}),
            {"watch_on": "now"},
            **headers,
        )

        self.assertEqual(response.status_code, 200)
        season.refresh_from_db()
        self.assertEqual(season.status, Status.COMPLETED.value)
        self.assertTrue(
            Episode.objects.filter(
                related_season=season,
                item=third_episode_item,
            ).exists(),
        )
