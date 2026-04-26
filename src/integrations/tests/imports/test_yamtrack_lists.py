from datetime import UTC, datetime
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.crypto import get_random_string

from app.models import Item, MediaTypes, Sources
from integrations.imports import yamtrack_lists
from lists.models import CustomList, CustomListItem, ImportedListSourceChoices


class ImportYamtrackLists(TestCase):
    """Test importing custom lists from Yamtrack CSV."""

    def setUp(self):
        """Create a user and some existing list/item state."""
        password = get_random_string(16)
        self.user = get_user_model().objects.create_user(
            username="test",
            password=password,
        )

    def import_csv(self, content, mode="new"):
        """Import in-memory CSV content."""
        return yamtrack_lists.importer(
            BytesIO(content.encode("utf-8")),
            self.user,
            mode,
        )

    def test_import_creates_lists_and_missing_items(self):
        """Import should create lists and missing Items for memberships."""
        csv_content = (
            "row_type,list_key,list_name,list_description,list_import_source,"
            "list_import_source_id,list_item_added_at,item_source,item_media_type,"
            "item_media_id,item_season_number,item_episode_number,item_title,item_image\n"
            "list,list-1,Favorites,Imported from CSV,trakt,55,,,,,,,,\n"
            "list,list-2,Books To Read,,,,,,,,,,,\n"
            "list_item,list-1,,,,,2024-01-02T10:00:00Z,tmdb,movie,10494,,,Perfect Blue,"
            "https://image.url/movie\n"
            "list_item,list-1,,,,,2024-01-03T10:00:00Z,igdb,game,19562,,,"
            "Resident Evil 7,https://image.url/game\n"
            "list_item,list-2,,,,,2024-01-04T10:00:00Z,openlibrary,book,OL21733390M,,,"
            "Fantastic Mr. Fox,https://image.url/book"
        )

        imported_counts, warnings = self.import_csv(csv_content)

        self.assertEqual(imported_counts["list_created"], 2)
        self.assertEqual(imported_counts["list_item"], 3)
        self.assertFalse(warnings)

        favorites = CustomList.objects.get(import_source_id="55")
        books = CustomList.objects.get(name="Books To Read")
        self.assertEqual(favorites.items.count(), 2)
        self.assertEqual(books.items.count(), 1)
        self.assertTrue(
            Item.objects.filter(
                media_id="19562",
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
            ).exists(),
        )

        favorite_movie_item = CustomListItem.objects.get(
            custom_list=favorites,
            item__media_id="10494",
        )
        self.assertEqual(
            favorite_movie_item.date_added,
            datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        )

    def test_import_overwrite_replaces_memberships_and_preserves_dates(self):
        """Overwrite mode should replace list memberships with imported ones."""
        existing_list = CustomList.objects.create(
            owner=self.user,
            name="Favorites",
            description="Old",
            import_source=ImportedListSourceChoices.TRAKT.value,
            import_source_id="55",
        )
        old_item = Item.objects.create(
            media_id="111",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Old Movie",
            image="https://image.url/old",
        )
        existing_list.items.add(old_item)

        csv_content = (
            "row_type,list_key,list_name,list_description,list_import_source,"
            "list_import_source_id,list_item_added_at,item_source,item_media_type,"
            "item_media_id,item_season_number,item_episode_number,item_title,item_image\n"
            "list,list-1,Favorites,Fresh,trakt,55,,,,,,,,\n"
            "list_item,list-1,,,,,2024-02-01T09:00:00Z,tmdb,episode,1668,1,2,"
            "The One with the Sonogram at the End,https://image.url/episode"
        )

        imported_counts, warnings = self.import_csv(csv_content, mode="overwrite")

        existing_list.refresh_from_db()
        self.assertEqual(imported_counts["list_updated"], 1)
        self.assertEqual(imported_counts["list_item"], 1)
        self.assertFalse(warnings)
        self.assertEqual(existing_list.description, "Fresh")
        self.assertEqual(existing_list.items.count(), 1)
        self.assertFalse(existing_list.items.filter(media_id="111").exists())
        self.assertTrue(
            existing_list.items.filter(
                media_id="1668",
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=2,
            ).exists(),
        )

        list_item = CustomListItem.objects.get(custom_list=existing_list)
        self.assertEqual(
            list_item.date_added,
            datetime(2024, 2, 1, 9, 0, tzinfo=UTC),
        )

    def test_import_new_mode_matches_unique_name_and_adds_missing_memberships(self):
        """New mode should match a uniquely named manual list and merge items."""
        existing_list = CustomList.objects.create(
            owner=self.user,
            name="Weekend Watch",
            description="Old description",
        )
        existing_item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url/movie",
        )
        existing_list.items.add(existing_item)

        csv_content = (
            "row_type,list_key,list_name,list_description,list_import_source,"
            "list_import_source_id,list_item_added_at,item_source,item_media_type,"
            "item_media_id,item_season_number,item_episode_number,item_title,item_image\n"
            "list,list-1,Weekend Watch,Updated description,,,,,,,,,,,\n"
            "list_item,list-1,,,,,2024-03-01T12:00:00Z,tmdb,movie,10494,,,Perfect Blue,"
            "https://image.url/movie\n"
            "list_item,list-1,,,,,2024-03-02T12:00:00Z,tmdb,tv,1668,,,Friends,"
            "https://image.url/tv"
        )

        imported_counts, warnings = self.import_csv(csv_content)

        existing_list.refresh_from_db()
        self.assertEqual(imported_counts["list_updated"], 1)
        self.assertEqual(imported_counts["list_item"], 1)
        self.assertFalse(warnings)
        self.assertEqual(existing_list.description, "Updated description")
        self.assertEqual(existing_list.items.count(), 2)

    def test_import_skips_ambiguous_manual_list_name(self):
        """Manual list rows should skip when multiple owned lists share the name."""
        CustomList.objects.create(owner=self.user, name="Favorites")
        CustomList.objects.create(owner=self.user, name="Favorites")

        csv_content = (
            "row_type,list_key,list_name,list_description,list_import_source,"
            "list_import_source_id,list_item_added_at,item_source,item_media_type,"
            "item_media_id,item_season_number,item_episode_number,item_title,item_image\n"
            "list,list-1,Favorites,Imported,,,,,,,,,,,\n"
            "list_item,list-1,,,,,2024-01-02T10:00:00Z,tmdb,movie,10494,,,Perfect Blue,"
            "https://image.url/movie"
        )

        imported_counts, warnings = self.import_csv(csv_content)

        self.assertFalse(imported_counts)
        self.assertIn("matches multiple owned lists", warnings)
        self.assertEqual(CustomListItem.objects.count(), 0)

    def test_import_warns_for_missing_list_key_reference(self):
        """List item rows should warn when their list key was never defined."""
        csv_content = (
            "row_type,list_key,list_name,list_description,list_import_source,"
            "list_import_source_id,list_item_added_at,item_source,item_media_type,"
            "item_media_id,item_season_number,item_episode_number,item_title,item_image\n"
            "list_item,list-404,,,,,2024-01-02T10:00:00Z,tmdb,movie,10494,,,"
            "Perfect Blue,"
            "https://image.url/movie"
        )

        imported_counts, warnings = self.import_csv(csv_content)

        self.assertFalse(imported_counts)
        self.assertIn("unknown list_key", warnings)

    def test_import_includes_empty_lists(self):
        """List rows without memberships should still create empty lists."""
        csv_content = (
            "row_type,list_key,list_name,list_description,list_import_source,"
            "list_import_source_id,list_item_added_at,item_source,item_media_type,"
            "item_media_id,item_season_number,item_episode_number,item_title,item_image\n"
            "list,list-1,Empty List,No memberships yet,,,,,,,,,,,"
        )

        imported_counts, warnings = self.import_csv(csv_content)

        self.assertEqual(imported_counts["list_created"], 1)
        self.assertEqual(CustomList.objects.get(name="Empty List").items.count(), 0)
        self.assertFalse(warnings)
