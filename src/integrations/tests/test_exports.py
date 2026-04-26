import csv
from datetime import UTC, datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse
from django.utils.crypto import get_random_string

from app.models import (
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem, ImportedListSourceChoices


class ExportCSVTest(TestCase):
    """Test exporting media to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        item_movie = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )
        Movie.objects.create(
            item=item_movie,
            user=self.user,
            score=9,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
        )

        season = Season.objects.create(
            item=item_season,
            user=self.user,
            score=9,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
        )

        item_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_episode,
            related_season=season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://image.url",
        )
        Anime.objects.create(
            item=item_anime,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_manga = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="https://image.url",
        )
        Manga.objects.create(
            item=item_manga,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_game = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Witcher 3: Wild Hunt",
            image="https://image.url",
        )
        Game.objects.create(
            item=item_game,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_book = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Fantastic Mr. Fox",
            image="https://image.url",
        )
        Book.objects.create(
            item=item_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_export_csv(self):
        """Basic test exporting media to CSV."""
        # Generate the CSV file by accessing the export view
        response = self.client.get(reverse("export_csv"))

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "text/csv")

        # Read the streaming content and decode it
        content = b"".join(response.streaming_content).decode("utf-8")

        # Create a CSV reader from the CSV content
        reader = csv.DictReader(StringIO(content))

        db_media_ids = set(
            Item.objects.filter(
                Q(tv__user=self.user)
                | Q(movie__user=self.user)
                | Q(season__user=self.user)
                | Q(episode__related_season__user=self.user)
                | Q(anime__user=self.user)
                | Q(manga__user=self.user)
                | Q(game__user=self.user)
                | Q(book__user=self.user),
            ).values_list("media_id", flat=True),
        )

        # Verify each row in the CSV exists in the database
        for row in reader:
            media_id = row["media_id"]
            self.assertIn(media_id, db_media_ids)


class ExportListCSVTest(TestCase):
    """Test exporting custom lists to CSV."""

    def setUp(self):
        """Create custom list data for export tests."""
        password = get_random_string(16)
        self.credentials = {"username": "test", "password": password}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.other_user = get_user_model().objects.create_user(
            username="other",
            password=password,
        )
        self.client.login(**self.credentials)

        self.movie_item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url/movie",
        )
        self.book_item = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Fantastic Mr. Fox",
            image="https://image.url/book",
        )

        self.favorite_list = CustomList.objects.create(
            name="Favorites",
            description="Imported list",
            owner=self.user,
            import_source=ImportedListSourceChoices.TRAKT.value,
            import_source_id="55",
        )
        self.empty_list = CustomList.objects.create(
            name="Empty List",
            description="No items yet",
            owner=self.user,
        )
        other_list = CustomList.objects.create(
            name="Other User List",
            owner=self.other_user,
        )

        self.favorite_movie = CustomListItem.objects.create(
            custom_list=self.favorite_list,
            item=self.movie_item,
        )
        self.favorite_book = CustomListItem.objects.create(
            custom_list=self.favorite_list,
            item=self.book_item,
        )
        other_list.items.add(self.movie_item)

    def test_export_lists_csv(self):
        """Exported custom lists CSV should contain list and list_item rows."""
        response = self.client.get(reverse("export_lists_csv"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")

        content = b"".join(response.streaming_content).decode("utf-8")
        reader = list(csv.DictReader(StringIO(content)))

        list_rows = [row for row in reader if row["row_type"] == "list"]
        item_rows = [row for row in reader if row["row_type"] == "list_item"]

        self.assertEqual(len(list_rows), 2)
        self.assertEqual(len(item_rows), 2)

        favorite_row = next(row for row in list_rows if row["list_name"] == "Favorites")
        empty_row = next(row for row in list_rows if row["list_name"] == "Empty List")
        self.assertEqual(
            favorite_row["list_import_source"],
            ImportedListSourceChoices.TRAKT.value,
        )
        self.assertEqual(favorite_row["list_import_source_id"], "55")
        self.assertEqual(empty_row["list_description"], "No items yet")

        exported_media_ids = {row["item_media_id"] for row in item_rows}
        self.assertEqual(exported_media_ids, {"10494", "OL21733390M"})

        book_row = next(
            row for row in item_rows if row["item_media_id"] == "OL21733390M"
        )
        self.assertEqual(book_row["item_media_type"], MediaTypes.BOOK.value)
        self.assertEqual(book_row["item_title"], "Fantastic Mr. Fox")
        self.assertEqual(book_row["item_image"], "https://image.url/book")
        self.assertEqual(
            book_row["list_item_added_at"],
            self.favorite_book.date_added.isoformat(),
        )
