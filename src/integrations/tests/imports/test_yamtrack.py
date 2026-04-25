from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    TV,
    Anime,
    Book,
    Episode,
    Manga,
    Movie,
    Season,
)
from integrations.imports import yamtrack

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
app_mock_path = (
    Path(__file__).resolve().parent.parent.parent.parent / "app" / "tests" / "mock_data"
)


class ImportYamtrack(TestCase):
    """Test importing media from Yamtrack CSV."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_yamtrack.csv").open("rb") as file:
            self.import_results = yamtrack.importer(file, self.user, "new")

    def test_import_counts(self):
        """Test basic counts of imported media."""
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 1)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            24,
        )

    def test_historical_records(self):
        """Test historical records creation during import."""
        anime = Anime.objects.filter(user=self.user).first()
        self.assertEqual(anime.history.count(), 1)
        self.assertEqual(
            anime.history.first().history_date,
            datetime(2024, 2, 9, 10, 0, 0, tzinfo=UTC),
        )

        movie = Movie.objects.filter(user=self.user).first()
        self.assertEqual(movie.history.count(), 1)
        self.assertEqual(
            movie.history.first().history_date,
            datetime(2024, 2, 9, 15, 30, 0, tzinfo=UTC),
        )

        tv = TV.objects.filter(user=self.user).first()
        self.assertEqual(tv.history.count(), 1)
        self.assertEqual(
            tv.history.first().history_date,
            datetime(2024, 2, 9, 12, 0, 0, tzinfo=UTC),
        )

    def test_missing_metadata_handling(self):
        """Test _handle_missing_metadata method directly."""
        test_rows = [
            # TV Show
            {
                "media_id": "1668",
                "source": "tmdb",
                "media_type": "tv",
                "title": "",
                "image": "",
                "season_number": "",
                "episode_number": "",
            },
            {
                "media_id": "1668",
                "source": "tmdb",
                "media_type": "season",
                "title": "",
                "image": "",
                "season_number": "2",
                "episode_number": "",
            },
            # Episode
            {
                "media_id": "1668",
                "source": "tmdb",
                "media_type": "episode",
                "title": "",
                "image": "",
                "season_number": "2",
                "episode_number": "5",
            },
        ]

        importer = yamtrack.YamtrackImporter(None, self.user, "new")

        for row in test_rows:
            # Make copies of original rows to verify they're modified
            original_row = row.copy()

            # Call the method directly
            importer._handle_missing_metadata(
                row,
                row["media_type"],
                row["season_number"],
                row["episode_number"],
            )

            self.assertNotEqual(row["title"], original_row["title"])
            self.assertNotEqual(row["image"], original_row["image"])


class ImportYamtrackPartials(TestCase):
    """Test importing yamtrack media with no ID."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_yamtrack_partials.csv").open("rb") as file:
            self.import_results = yamtrack.importer(file, self.user, "new")

    def test_import_counts(self):
        """Test basic counts of imported media."""
        self.assertEqual(Book.objects.filter(user=self.user).count(), 3)
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

    def test_end_dates(self):
        """Test end dates during import."""
        book = Book.objects.filter(user=self.user).first()
        self.assertEqual(book.history.count(), 1)
        bookqs = Book.objects.filter(
            user=self.user,
            item__title="Warlock",
        ).order_by("-end_date")
        books = list(bookqs)

        self.assertEqual(len(books), 3)
        self.assertEqual(
            books[0].end_date,
            datetime(2024, 5, 9, 0, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            books[1].end_date,
            datetime(2024, 4, 9, 0, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            books[2].end_date,
            datetime(2024, 3, 9, 0, 0, 0, tzinfo=UTC),
        )


class ImportYamtrackEpisodeSharedFields(TestCase):
    """Test importing shared episode score and notes from Yamtrack CSV."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def import_csv(self, content):
        """Import CSV content for the test user."""
        return yamtrack.importer(BytesIO(content.encode("utf-8")), self.user, "new")

    def test_import_normalizes_episode_shared_fields_to_most_recent_watch(self):
        """Repeated watches should share the newest imported score and notes."""
        expected_score = Decimal("9.0")
        csv_content = (
            "media_id,source,media_type,title,image,season_number,episode_number,"
            "is_hidden_override,is_specials_override,score,status,notes,"
            "start_date,end_date,progress,created_at,progressed_at\n"
            "1668,tmdb,tv,Friends,https://image.tmdb.org/t/p/w500/show.jpg,,,"
            "False,False,,Completed,,,,0,2024-01-01T00:00:00Z,\n"
            "1668,tmdb,season,Friends,https://image.tmdb.org/t/p/w500/season1.jpg,"
            "1,,False,False,,Completed,,,,0,2024-01-01T00:00:00Z,\n"
            "1668,tmdb,episode,Friends,https://image.tmdb.org/t/p/w500/"
            "episode1.jpg,1,1,False,False,9.0,,Newest shared notes,,"
            "2024-03-01T00:00:00Z,0,2024-03-01T00:00:00Z,2024-03-01T00:00:00Z\n"
            "1668,tmdb,episode,Friends,https://image.tmdb.org/t/p/w500/"
            "episode1.jpg,1,1,False,False,2.0,,Older shared notes,,"
            "2024-02-01T00:00:00Z,0,2024-02-01T00:00:00Z,2024-02-01T00:00:00Z\n"
            "1668,tmdb,episode,Friends,https://image.tmdb.org/t/p/w500/"
            "episode2.jpg,1,2,False,False,7.5,,Carry forward notes,,"
            "2024-02-10T00:00:00Z,0,2024-02-10T00:00:00Z,2024-02-10T00:00:00Z\n"
            "1668,tmdb,episode,Friends,https://image.tmdb.org/t/p/w500/"
            "episode2.jpg,1,2,False,False,,Completed,,,2024-03-10T00:00:00Z,"
            "0,2024-03-10T00:00:00Z,2024-03-10T00:00:00Z"
        )

        self.import_csv(csv_content)

        episode_one_watches = list(
            Episode.objects.filter(
                related_season__user=self.user,
                item__media_id="1668",
                item__season_number=1,
                item__episode_number=1,
            ).order_by("end_date"),
        )
        self.assertEqual(len(episode_one_watches), 2)
        self.assertTrue(
            all(watch.score == expected_score for watch in episode_one_watches),
        )
        self.assertTrue(
            all(watch.notes == "Newest shared notes" for watch in episode_one_watches),
        )

        episode_two_watches = list(
            Episode.objects.filter(
                related_season__user=self.user,
                item__media_id="1668",
                item__season_number=1,
                item__episode_number=2,
            ).order_by("end_date"),
        )
        self.assertEqual(len(episode_two_watches), 2)
        self.assertTrue(all(watch.score is None for watch in episode_two_watches))
        self.assertTrue(all(watch.notes == "" for watch in episode_two_watches))

    def test_invalid_episode_shared_fields_skip_row_with_warning(self):
        """Invalid shared episode score should warn and skip the episode row."""
        csv_content = (
            "media_id,source,media_type,title,image,season_number,episode_number,"
            "is_hidden_override,is_specials_override,score,status,notes,"
            "start_date,end_date,progress,created_at,progressed_at\n"
            "1668,tmdb,tv,Friends,https://image.tmdb.org/t/p/w500/show.jpg,,,"
            "False,False,,Completed,,,,0,2024-01-01T00:00:00Z,\n"
            "1668,tmdb,season,Friends,https://image.tmdb.org/t/p/w500/season1.jpg,"
            "1,,False,False,,Completed,,,,0,2024-01-01T00:00:00Z,\n"
            "1668,tmdb,episode,Friends,https://image.tmdb.org/t/p/w500/"
            "episode1.jpg,1,1,False,False,11.0,,Too high,,2024-03-01T00:00:00Z,"
            "0,2024-03-01T00:00:00Z,2024-03-01T00:00:00Z"
        )

        import_results = self.import_csv(csv_content)

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )
        self.assertIn(
            "Ensure this value is less than or equal to 10",
            import_results[1],
        )
