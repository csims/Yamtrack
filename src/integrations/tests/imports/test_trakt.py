from pathlib import Path
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Episode,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from integrations.imports import (
    helpers,
)
from integrations.imports.trakt import TraktImporter, importer
from lists.models import CustomList, ImportedListSourceChoices

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportTrakt(TestCase):
    """Test importing media from Trakt."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    def _episode_metadata_side_effect(self, media_type, _, __, ___=None):
        """Return TV and season metadata for episode import tests."""
        if media_type == MediaTypes.TV.value:
            return {
                "title": "Test Show",
                "image": "tv_image.jpg",
                "last_episode_season": 1,
                "max_progress": 1,
            }
        if media_type == MediaTypes.SEASON.value:
            return {
                "title": "Season 1",
                "image": "season_image.jpg",
                "episodes": [{"episode_number": 1, "still_path": "/still.jpg"}],
                "max_progress": 1,
            }
        return None

    def _episode_history_entry(self, date="2023-01-01"):
        """Return a watched episode entry."""
        return {
            "type": "episode",
            "episode": {"season": 1, "number": 1, "title": "Pilot"},
            "show": {"title": "Test Show", "ids": {"tmdb": 12345}},
            "watched_at": f"{date}T00:00:00.000Z",
        }

    def _episode_rating_entry(self):
        """Return an episode rating entry."""
        return {
            "rated_at": "2023-01-02T00:00:00.000Z",
            "type": "episode",
            "episode": {"season": 1, "number": 1, "title": "Pilot"},
            "show": {"title": "Test Show", "ids": {"tmdb": 12345}},
            "rating": 8,
        }

    def _episode_comment_entry(self):
        """Return an episode comment entry."""
        return {
            "type": "episode",
            "episode": {"season": 1, "number": 1, "title": "Pilot"},
            "show": {"title": "Test Show", "ids": {"tmdb": 12345}},
            "comment": {
                "comment": "Great pilot!",
                "updated_at": "2023-01-03T00:00:00.000Z",
            },
        }

    def _list_metadata_side_effect(
        self,
        media_type,
        _,
        title,
        season_number=None,
        warning_context=None,  # noqa: ARG002
    ):
        """Return metadata for Trakt list item resolution."""
        if media_type == MediaTypes.MOVIE.value:
            return {
                "title": title,
                "image": "movie_image.jpg",
            }
        if media_type == MediaTypes.TV.value:
            return {
                "title": title,
                "image": "tv_image.jpg",
                "last_episode_season": 3,
                "max_progress": 10,
            }
        if media_type == MediaTypes.SEASON.value:
            return {
                "title": f"{title} Season {season_number}",
                "image": "season_image.jpg",
                "episodes": [{"episode_number": 2, "still_path": "/still.jpg"}],
                "max_progress": 10,
            }
        return None

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_watched_movie(self, mock_get_metadata):
        """Test processing a movie entry."""
        movie_entry = {
            "type": "movie",
            "movie": {"title": "Test Movie", "ids": {"tmdb": 67890}},
            "watched_at": "2023-01-02T00:00:00.000Z",
        }

        mock_get_metadata.return_value = {
            "title": "Test Movie",
            "image": "movie_image.jpg",
        }

        trakt_importer = TraktImporter("test", self.user, "new")
        trakt_importer.process_watched_movie(movie_entry)

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        self.assertEqual(len(trakt_importer.media_instances[MediaTypes.MOVIE.value]), 1)

        # Process the same movie again to test repeat handling
        trakt_importer.process_watched_movie(movie_entry)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 2)

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_watched_episode(self, mock_get_metadata):
        """Test processing an episode entry."""
        episode_entry = self._episode_history_entry()
        mock_get_metadata.side_effect = self._episode_metadata_side_effect

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_watched_episode(episode_entry)

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.TV.value]), 1)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.SEASON.value]), 1)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 1)

        # Process the same episode again to test repeat handling
        trakt_importer.process_watched_episode(episode_entry)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 2)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_watchlist(self, mock_get_metadata, mock_make_request):
        """Test processing a watchlist entry."""
        watchlist_entry = {
            "listed_at": "2023-01-01T00:00:00.000Z",
            "type": "show",
            "show": {"title": "Watchlist Show", "ids": {"tmdb": 54321}},
        }

        mock_make_request.return_value = [watchlist_entry]
        mock_get_metadata.return_value = {
            "title": "Watchlist Show",
            "image": "show_image.jpg",
        }

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_watchlist()

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.TV.value]), 1)
        tv_obj = trakt_importer.bulk_media[MediaTypes.TV.value][0]
        self.assertEqual(tv_obj.status, Status.PLANNING.value)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_ratings(self, mock_get_metadata, mock_make_request):
        """Test processing a rating entry."""
        rating_entry = {
            "rated_at": "2023-01-01T00:00:00.000Z",
            "type": "movie",
            "movie": {"title": "Rated Movie", "ids": {"tmdb": 238}},
            "rating": 8,
        }

        mock_make_request.return_value = [rating_entry]
        mock_get_metadata.return_value = {
            "title": "Rated Movie",
            "image": "movie_image.jpg",
        }

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_ratings()

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        movie_obj = trakt_importer.bulk_media[MediaTypes.MOVIE.value][0]
        self.assertEqual(movie_obj.score, 8)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_episode_rating_updates_existing_watched_episode(
        self,
        mock_get_metadata,
        mock_make_request,
    ):
        """Test processing an episode rating after watched history import."""
        mock_get_metadata.side_effect = self._episode_metadata_side_effect
        mock_make_request.return_value = [self._episode_rating_entry()]

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_watched_episode(
            self._episode_history_entry("2023-01-01"),
        )
        trakt_importer.process_watched_episode(
            self._episode_history_entry("2025-01-01"),
        )

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 2)

        trakt_importer.process_ratings()

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.TV.value]), 1)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.SEASON.value]), 1)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 2)

        episode_obj = trakt_importer.bulk_media[MediaTypes.EPISODE.value][0]
        self.assertIsInstance(episode_obj, Episode)
        self.assertEqual(episode_obj.end_date, "2023-01-01T00:00:00.000Z")
        self.assertEqual(episode_obj.score, 8)

        episode_obj2 = trakt_importer.bulk_media[MediaTypes.EPISODE.value][1]
        self.assertIsInstance(episode_obj2, Episode)
        self.assertEqual(episode_obj2.end_date, "2025-01-01T00:00:00.000Z")
        self.assertEqual(episode_obj2.score, 8)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_comments(self, mock_get_metadata, mock_make_request):
        """Test processing paginated comments from Trakt."""
        # First page with one comment
        first_page = [
            {
                "type": "movie",
                "movie": {"title": "Commented Movie", "ids": {"tmdb": 123}},
                "comment": {
                    "comment": "Great movie!",
                    "updated_at": "2023-01-01T00:00:00.000Z",
                },
            },
        ]

        # Second empty page to stop pagination
        second_page = []

        mock_make_request.side_effect = [first_page, second_page]
        mock_get_metadata.return_value = {
            "title": "Commented Movie",
            "image": "movie_image.jpg",
        }

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_comments()

        calls = mock_make_request.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("?page=1&limit=1000", calls[0].args[0])  # First page
        self.assertIn("?page=2&limit=1000", calls[1].args[0])  # Second page

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        movie_obj = trakt_importer.bulk_media[MediaTypes.MOVIE.value][0]
        self.assertEqual(movie_obj.notes, "Great movie!")

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_episode_comment_updates_existing_watched_episode(
        self,
        mock_get_metadata,
        mock_make_request,
    ):
        """Test processing an episode comment after watched history import."""
        mock_get_metadata.side_effect = self._episode_metadata_side_effect
        mock_make_request.side_effect = [[self._episode_comment_entry()], []]

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_watched_episode(
            self._episode_history_entry("2023-01-01"),
        )
        trakt_importer.process_watched_episode(
            self._episode_history_entry("2025-01-01"),
        )

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 2)

        trakt_importer.process_comments()

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.TV.value]), 1)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.SEASON.value]), 1)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 2)

        episode_obj = trakt_importer.bulk_media[MediaTypes.EPISODE.value][0]
        self.assertIsInstance(episode_obj, Episode)
        self.assertEqual(episode_obj.end_date, "2023-01-01T00:00:00.000Z")
        self.assertEqual(episode_obj.notes, "Great pilot!")

        episode_obj2 = trakt_importer.bulk_media[MediaTypes.EPISODE.value][1]
        self.assertIsInstance(episode_obj2, Episode)
        self.assertEqual(episode_obj2.end_date, "2025-01-01T00:00:00.000Z")
        self.assertEqual(episode_obj2.notes, "Great pilot!")

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_process_episode_rating_skips_without_watched_history(
        self,
        mock_make_request,
    ):
        """Test skipping an episode rating without matching watched history."""
        mock_make_request.return_value = [self._episode_rating_entry()]

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_ratings()

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.TV.value]), 0)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.SEASON.value]), 0)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 0)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_process_episode_comment_skips_without_watched_history(
        self,
        mock_make_request,
    ):
        """Test skipping an episode comment without matching watched history."""
        mock_make_request.side_effect = [[self._episode_comment_entry()], []]

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_comments()

        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.TV.value]), 0)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.SEASON.value]), 0)
        self.assertEqual(len(trakt_importer.bulk_media[MediaTypes.EPISODE.value]), 0)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_lists_creates_linked_custom_list_and_items(
        self,
        mock_get_metadata,
        mock_make_request,
    ):
        """Test Trakt lists import into Yamtrack custom lists."""
        mock_get_metadata.side_effect = self._list_metadata_side_effect
        mock_make_request.side_effect = [
            [
                {
                    "name": "Favorites",
                    "description": "Imported from Trakt",
                    "ids": {"trakt": 55, "slug": "favorites"},
                },
            ],
            [
                {
                    "type": "movie",
                    "movie": {"title": "Movie One", "ids": {"tmdb": 101}},
                },
                {
                    "type": "show",
                    "show": {"title": "Show One", "ids": {"tmdb": 202}},
                },
                {
                    "type": "season",
                    "show": {"title": "Show One", "ids": {"tmdb": 202}},
                    "season": {"number": 1, "ids": {"tmdb": 303}},
                },
                {
                    "type": "episode",
                    "show": {"title": "Show One", "ids": {"tmdb": 202}},
                    "episode": {
                        "season": 1,
                        "number": 2,
                        "title": "Episode Two",
                        "ids": {"tmdb": 404},
                    },
                },
                {
                    "type": "person",
                    "person": {"name": "Ignored Person", "ids": {"tmdb": 505}},
                },
            ],
            [],
        ]

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_lists()

        custom_list = CustomList.objects.get()
        self.assertEqual(custom_list.name, "Favorites")
        self.assertEqual(custom_list.description, "Imported from Trakt")
        self.assertEqual(
            custom_list.import_source,
            ImportedListSourceChoices.TRAKT.value,
        )
        self.assertEqual(custom_list.import_source_id, "55")
        self.assertEqual(custom_list.items.count(), 4)
        self.assertEqual(trakt_importer.list_counts["list_created"], 1)
        self.assertEqual(trakt_importer.list_counts["list_item"], 4)
        self.assertFalse(trakt_importer.warnings)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_lists_new_mode_updates_existing_linked_list(
        self,
        mock_get_metadata,
        mock_make_request,
    ):
        """Test rerunning list import in new mode reuses linked lists."""
        existing_item = Item.objects.create(
            title="Existing Movie",
            media_id="101",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            image="https://example.com/existing.jpg",
        )
        custom_list = CustomList.objects.create(
            name="Old Name",
            description="Old Description",
            owner=self.user,
            import_source=ImportedListSourceChoices.TRAKT.value,
            import_source_id="55",
        )
        custom_list.items.add(existing_item)

        mock_get_metadata.side_effect = self._list_metadata_side_effect
        mock_make_request.side_effect = [
            [
                {
                    "name": "Favorites",
                    "description": "Fresh Description",
                    "ids": {"trakt": 55, "slug": "favorites"},
                },
            ],
            [
                {
                    "type": "movie",
                    "movie": {"title": "Existing Movie", "ids": {"tmdb": 101}},
                },
                {
                    "type": "show",
                    "show": {"title": "New Show", "ids": {"tmdb": 202}},
                },
            ],
            [],
        ]

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_lists()

        custom_list.refresh_from_db()
        self.assertEqual(CustomList.objects.count(), 1)
        self.assertEqual(custom_list.name, "Favorites")
        self.assertEqual(custom_list.description, "Fresh Description")
        self.assertEqual(custom_list.items.count(), 2)
        self.assertEqual(trakt_importer.list_counts["list_updated"], 1)
        self.assertEqual(trakt_importer.list_counts["list_item"], 1)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_process_lists_overwrite_mode_replaces_linked_list_items(
        self,
        mock_get_metadata,
        mock_make_request,
    ):
        """Test overwrite mode replaces the contents of linked lists."""
        old_item = Item.objects.create(
            title="Old Movie",
            media_id="111",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            image="https://example.com/old.jpg",
        )
        custom_list = CustomList.objects.create(
            name="Favorites",
            owner=self.user,
            import_source=ImportedListSourceChoices.TRAKT.value,
            import_source_id="55",
        )
        custom_list.items.add(old_item)

        mock_get_metadata.side_effect = self._list_metadata_side_effect
        mock_make_request.side_effect = [
            [
                {
                    "name": "Favorites",
                    "description": "",
                    "ids": {"trakt": 55, "slug": "favorites"},
                },
            ],
            [
                {
                    "type": "movie",
                    "movie": {"title": "New Movie", "ids": {"tmdb": 222}},
                },
            ],
            [],
        ]

        trakt_importer = TraktImporter("testuser", self.user, "overwrite")
        trakt_importer.process_lists()

        custom_list.refresh_from_db()
        self.assertEqual(custom_list.items.count(), 1)
        self.assertFalse(custom_list.items.filter(id=old_item.id).exists())
        self.assertTrue(custom_list.items.filter(media_id="222").exists())
        self.assertEqual(trakt_importer.list_counts["list_updated"], 1)
        self.assertEqual(trakt_importer.list_counts["list_item"], 1)

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_process_lists_continues_after_list_fetch_failure(
        self,
        mock_make_request,
        mock_get_metadata,
    ):
        """Test list fetch failures become warnings and do not abort import."""
        error_response = type("Response", (), {"status_code": 404})()
        list_error = requests.exceptions.HTTPError(response=error_response)

        mock_get_metadata.side_effect = self._list_metadata_side_effect
        mock_make_request.side_effect = [
            [
                {
                    "name": "Broken List",
                    "description": "",
                    "ids": {"trakt": 55, "slug": "broken-list"},
                },
                {
                    "name": "Working List",
                    "description": "",
                    "ids": {"trakt": 56, "slug": "working-list"},
                },
            ],
            list_error,
            [
                {
                    "type": "movie",
                    "movie": {"title": "Movie One", "ids": {"tmdb": 101}},
                },
            ],
            [],
        ]

        trakt_importer = TraktImporter("testuser", self.user, "new")
        trakt_importer.process_lists()

        self.assertEqual(CustomList.objects.count(), 1)
        self.assertEqual(CustomList.objects.get().name, "Working List")
        self.assertEqual(trakt_importer.list_counts["list_created"], 1)
        self.assertIn(
            "List 'Broken List': unable to fetch list items from Trakt.",
            trakt_importer.warnings,
        )

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_public_import_full_flow(
        self,
        mock_get_metadata,
        mock_make_request,
        mock_get_paginated,
    ):
        """Test full import flow with public username (no OAuth)."""
        mock_get_paginated.side_effect = [
            [
                {
                    "type": "movie",
                    "movie": {"title": "Public Movie", "ids": {"tmdb": 999}},
                    "watched_at": "2023-01-01T00:00:00.000Z",
                },
            ],
            [],  # Empty comments
        ]

        mock_make_request.return_value = []

        mock_get_metadata.return_value = {
            "title": "Public Movie",
            "image": "movie.jpg",
        }

        imported_counts, _ = importer(None, self.user, "new", "public_user")

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_oauth_import_full_flow(
        self,
        mock_get_metadata,
        mock_make_request,
        mock_get_paginated,
    ):
        """Test full import flow with OAuth token."""
        mock_get_paginated.side_effect = [
            [
                {
                    "type": "movie",
                    "movie": {"title": "OAuth Movie", "ids": {"tmdb": 888}},
                    "watched_at": "2023-01-01T00:00:00.000Z",
                },
            ],
            [],  # Empty comments
        ]

        mock_make_request.return_value = []

        mock_get_metadata.return_value = {
            "title": "OAuth Movie",
            "image": "movie.jpg",
        }

        encrypted_token = helpers.encrypt("test_refresh_token")
        imported_counts, _ = importer(
            encrypted_token,
            self.user,
            "new",
            "oauth_user",
        )

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

    def test_trakt_importer_with_refresh_token(self):
        """Test TraktImporter initialization with refresh token."""
        encrypted_token = helpers.encrypt("test_token")
        importer = TraktImporter(
            "testuser",
            self.user,
            "new",
            refresh_token=encrypted_token,
        )

        self.assertEqual(importer.username, "testuser")
        self.assertEqual(importer.refresh_token, encrypted_token)
        self.assertEqual(importer.mode, "new")

    def test_trakt_importer_without_refresh_token(self):
        """Test TraktImporter initialization without refresh token (public)."""
        importer = TraktImporter("testuser", self.user, "new", refresh_token=None)

        self.assertEqual(importer.username, "testuser")
        self.assertIsNone(importer.refresh_token)
        self.assertEqual(importer.mode, "new")
