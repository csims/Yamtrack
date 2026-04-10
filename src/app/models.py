import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from django.apps import apps
from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import (
    CheckConstraint,
    Count,
    F,
    IntegerField,
    Max,
    Prefetch,
    Q,
    UniqueConstraint,
    Window,
)
from django.db.models.functions import Cast, RowNumber
from django.utils import timezone
from model_utils import FieldTracker
from model_utils.fields import MonitorField
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import app
import events
import users
from app import providers
from app.mixins import CalendarTriggerMixin

logger = logging.getLogger(__name__)
UNKNOWN_RELEASE_DATETIME = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class TVHomeSeasonState:
    """Candidate season state for selecting a TV home-card episode."""

    season: models.Model | None
    season_item: models.Model
    unwatched_aired: list[int]


class Sources(models.TextChoices):
    """Choices for the source of the item."""

    TMDB = "tmdb", "The Movie Database"
    MAL = "mal", "MyAnimeList"
    MANGAUPDATES = "mangaupdates", "MangaUpdates"
    IGDB = "igdb", "Internet Game Database"
    OPENLIBRARY = "openlibrary", "Open Library"
    HARDCOVER = "hardcover", "Hardcover"
    COMICVINE = "comicvine", "Comic Vine"
    MANUAL = "manual", "Manual"


class MediaTypes(models.TextChoices):
    """Choices for the media type of the item."""

    TV = "tv", "TV Show"
    SEASON = "season", "TV Season"
    EPISODE = "episode", "Episode"
    MOVIE = "movie", "Movie"
    ANIME = "anime", "Anime"
    MANGA = "manga", "Manga"
    GAME = "game", "Game"
    BOOK = "book", "Book"
    COMIC = "comic", "Comic"


class Item(CalendarTriggerMixin, models.Model):
    """Model to store basic information about media items."""

    media_id = models.CharField(max_length=20)
    source = models.CharField(
        max_length=20,
        choices=Sources.choices,
    )
    media_type = models.CharField(
        max_length=10,
        choices=MediaTypes.choices,
        default=MediaTypes.MOVIE.value,
    )
    title = models.TextField()
    image = models.URLField()  # if add default, custom media entry will show the value
    season_number = models.PositiveIntegerField(null=True, blank=True)
    episode_number = models.PositiveIntegerField(null=True, blank=True)
    is_hidden_override = models.BooleanField(default=False)
    is_specials_override = models.BooleanField(default=False)

    class Meta:
        """Meta options for the model."""

        constraints = [
            # Ensures items without season/episode numbers are unique
            UniqueConstraint(
                fields=["media_id", "source", "media_type"],
                condition=Q(season_number__isnull=True, episode_number__isnull=True),
                name="unique_item_without_season_episode",
            ),
            # Ensures seasons are unique within a show
            UniqueConstraint(
                fields=["media_id", "source", "media_type", "season_number"],
                condition=Q(season_number__isnull=False, episode_number__isnull=True),
                name="unique_item_with_season",
            ),
            # Ensures episodes are unique within a season
            UniqueConstraint(
                fields=[
                    "media_id",
                    "source",
                    "media_type",
                    "season_number",
                    "episode_number",
                ],
                condition=Q(season_number__isnull=False, episode_number__isnull=False),
                name="unique_item_with_season_episode",
            ),
            # Enforces that season items must have a season number but no episode number
            CheckConstraint(
                condition=Q(
                    media_type=MediaTypes.SEASON.value,
                    season_number__isnull=False,
                    episode_number__isnull=True,
                )
                | ~Q(media_type=MediaTypes.SEASON.value),
                name="season_number_required_for_season",
            ),
            # Enforces that episode items must have both season and episode numbers
            CheckConstraint(
                condition=Q(
                    media_type=MediaTypes.EPISODE.value,
                    season_number__isnull=False,
                    episode_number__isnull=False,
                )
                | ~Q(media_type=MediaTypes.EPISODE.value),
                name="season_and_episode_required_for_episode",
            ),
            # Prevents season/episode numbers from being set on non-TV media types
            CheckConstraint(
                condition=Q(
                    ~Q(
                        media_type__in=[
                            MediaTypes.SEASON.value,
                            MediaTypes.EPISODE.value,
                        ],
                    ),
                    season_number__isnull=True,
                    episode_number__isnull=True,
                )
                | Q(media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value]),
                name="no_season_episode_for_other_types",
            ),
            # Validate source choices
            CheckConstraint(
                condition=Q(source__in=Sources.values),
                name="%(app_label)s_%(class)s_source_valid",
            ),
            # Validate media_type choices
            CheckConstraint(
                condition=Q(media_type__in=MediaTypes.values),
                name="%(app_label)s_%(class)s_media_type_valid",
            ),
        ]
        ordering = ["media_id"]

    def __str__(self):
        """Return the name of the item."""
        name = self.title
        if self.season_number is not None:
            name += f" S{self.season_number}"
            if self.episode_number is not None:
                name += f"E{self.episode_number}"
        return name

    @classmethod
    def generate_manual_id(cls, media_type):
        """Generate a new ID for manual items."""
        latest_item = (
            cls.objects.filter(source=Sources.MANUAL.value, media_type=media_type)
            .annotate(
                media_id_int=Cast("media_id", IntegerField()),
            )
            .order_by("-media_id_int")
            .first()
        )

        if latest_item is None:
            return "1"

        return str(int(latest_item.media_id) + 1)

    def fetch_releases(self, delay):
        """Fetch releases for the item."""
        if self._disable_calendar_triggers:
            return

        if self.media_type == MediaTypes.SEASON.value:
            # Get or create the TV item for this season
            try:
                tv_item = Item.objects.get(
                    media_id=self.media_id,
                    source=self.source,
                    media_type=MediaTypes.TV.value,
                )
            except Item.DoesNotExist:
                # Get metadata for the TV show
                tv_metadata = providers.services.get_media_metadata(
                    MediaTypes.TV.value,
                    self.media_id,
                    self.source,
                )
                tv_item = Item.objects.create(
                    media_id=self.media_id,
                    source=self.source,
                    media_type=MediaTypes.TV.value,
                    title=tv_metadata["title"],
                    image=tv_metadata["image"],
                )
                logger.info("Created TV item %s for season %s", tv_item, self)

            # Process the TV item instead of the season
            items_to_process = [tv_item]
        else:
            items_to_process = [self]

        if delay:
            events.tasks.reload_calendar.delay(items_to_process=items_to_process)
        else:
            events.tasks.reload_calendar(items_to_process=items_to_process)


class MediaManager(models.Manager):
    """Custom manager for media models."""

    def get_historical_models(self):
        """Return list of historical model names."""
        return [
            f"historical{media_type}"
            for media_type in MediaTypes.values
            if media_type != MediaTypes.EPISODE.value
        ]

    def get_media_list(self, user, media_type, status_filter, sort_filter, search=None):
        """Get media list based on filters, sorting and search."""
        if media_type == MediaTypes.EPISODE.value:
            model = EpisodeWatch
        else:
            model = apps.get_model(app_label="app", model_name=media_type)
        if media_type == MediaTypes.EPISODE.value:
            queryset = model.objects.filter(related_season__user=user)
        else:
            queryset = model.objects.filter(user=user.id)

        if status_filter != users.models.MediaStatusChoices.ALL:
            queryset = queryset.filter(status=status_filter)

        if search:
            queryset = queryset.filter(item__title__icontains=search)

        queryset = queryset.annotate(
            repeats=Window(
                expression=Count("id"),
                partition_by=[F("item")],
            ),
            row_number=Window(
                expression=RowNumber(),
                partition_by=[F("item")],
                order_by=F("created_at").desc(),
            ),
        ).filter(row_number=1)

        queryset = queryset.select_related("item")
        queryset = self._apply_prefetch_related(queryset, media_type)

        if sort_filter:
            return self._sort_media_list(queryset, sort_filter, media_type)
        return queryset

    def _apply_prefetch_related(self, queryset, media_type):
        """Apply appropriate prefetch_related based on media type."""
        # Apply media-specific prefetches
        if media_type == MediaTypes.TV.value:
            return queryset.prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.select_related("item"),
                ),
                Prefetch(
                    "seasons__item__event_set",
                    queryset=events.models.Event.objects.all(),
                    to_attr="prefetched_events",
                ),
                Prefetch(
                    "seasons__episode_watches",
                    queryset=EpisodeWatch.objects.select_related("item"),
                ),
            )

        base_queryset = queryset.prefetch_related(
            Prefetch(
                "item__event_set",
                queryset=events.models.Event.objects.all(),
                to_attr="prefetched_events",
            ),
        )

        if media_type == MediaTypes.SEASON.value:
            return base_queryset.prefetch_related(
                Prefetch(
                    "episode_watches",
                    queryset=EpisodeWatch.objects.select_related("item"),
                ),
            )

        return base_queryset

    def _sort_media_list(self, queryset, sort_filter, media_type=None):
        """Sort media list using SQL sorting with annotations for calculated fields."""
        if media_type == MediaTypes.TV.value:
            return self._sort_tv_media_list(queryset, sort_filter)
        if media_type == MediaTypes.SEASON.value:
            return self._sort_season_media_list(queryset, sort_filter)

        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_tv_media_list(self, queryset, sort_filter):
        """Sort TV media list based on the sort criteria."""
        if sort_filter == "start_date":
            # Annotate with the minimum start_date from related seasons/episodes
            queryset = queryset.annotate(
                calculated_start_date=models.Min(
                    "seasons__episode_watches__watched_at",
                    filter=models.Q(
                        seasons__item__season_number__gt=0,
                        seasons__item__is_specials_override=False,
                        seasons__is_ignored=False,
                    ),
                ),
            )
            return queryset.order_by(
                models.F("calculated_start_date").asc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "end_date":
            # Annotate with the maximum end_date from related seasons/episodes
            queryset = queryset.annotate(
                calculated_end_date=models.Max(
                    "seasons__episode_watches__watched_at",
                    filter=models.Q(
                        seasons__item__season_number__gt=0,
                        seasons__item__is_specials_override=False,
                        seasons__is_ignored=False,
                    ),
                ),
            )
            return queryset.order_by(
                models.F("calculated_end_date").desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "progress":
            # Annotate with the sum of episodes watched (excluding
            # season 0 and specials overrides)
            queryset = queryset.annotate(
                # Count episodes in non-specials seasons
                calculated_progress=models.Count(
                    "seasons__episode_watches",
                    filter=models.Q(
                        seasons__item__season_number__gt=0,
                        seasons__item__is_specials_override=False,
                        seasons__is_ignored=False,
                    ),
                ),
            )
            return queryset.order_by(
                "-calculated_progress",
                models.functions.Lower("item__title"),
            )

        # Default to generic sorting
        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_season_media_list(self, queryset, sort_filter):
        """Sort Season media list based on the sort criteria."""
        if sort_filter == "start_date":
            # Annotate with the minimum end_date from related episodes
            queryset = queryset.annotate(
                calculated_start_date=models.Min("episode_watches__watched_at"),
            )
            return queryset.order_by(
                models.F("calculated_start_date").asc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "end_date":
            # Annotate with the maximum end_date from related episodes
            queryset = queryset.annotate(
                calculated_end_date=models.Max("episode_watches__watched_at"),
            )
            return queryset.order_by(
                models.F("calculated_end_date").desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "progress":
            # Annotate with the maximum episode number
            queryset = queryset.annotate(
                calculated_progress=models.Max("episode_watches__item__episode_number"),
            )
            return queryset.order_by(
                "-calculated_progress",
                models.functions.Lower("item__title"),
            )

        # Default to generic sorting
        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_generic_media_list(self, queryset, sort_filter):
        """Apply generic sorting logic for all media types."""
        # Handle sorting by date fields with special null handling
        if sort_filter in ("start_date", "end_date"):
            # For start_date, sort ascending (earliest first)
            if sort_filter == "start_date":
                return queryset.order_by(
                    models.F(sort_filter).asc(nulls_last=True),
                    models.functions.Lower("item__title"),
                )
            # For other date fields, sort descending (latest first)
            return queryset.order_by(
                models.F(sort_filter).desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        # Handle sorting by Item fields
        item_fields = [f.name for f in Item._meta.fields]
        if sort_filter in item_fields:
            if sort_filter == "title":
                # Case-insensitive title sorting
                return queryset.order_by(models.functions.Lower("item__title"))
            # Default sorting for other Item fields
            return queryset.order_by(
                f"-item__{sort_filter}",
                models.functions.Lower("item__title"),
            )

        # Default sorting by media field
        return queryset.order_by(
            models.F(sort_filter).desc(nulls_last=True),
            models.functions.Lower("item__title"),
        )

    def get_in_progress(self, user, sort_by, items_limit, specific_media_type=None):
        """Get a media list of in progress media by type."""
        list_by_type = {}
        media_types = self._get_media_types_to_process(user, specific_media_type)

        for media_type in media_types:
            if media_type == MediaTypes.TV.value:
                media_list = self._get_tv_in_progress_media(user)
            else:
                # Get base media list for in-progress media
                media_list = self.get_media_list(
                    user=user,
                    media_type=media_type,
                    status_filter=Status.IN_PROGRESS.value,
                    sort_filter=None,
                )

            if not media_list:
                continue

            if media_type != MediaTypes.TV.value:
                # Annotate with max_progress and next_event
                self.annotate_max_progress(media_list, media_type)
                self._annotate_next_event(media_list)

            # Sort the media list
            sorted_list = self._sort_in_progress_media(media_list, sort_by)

            # Apply pagination
            total_count = len(sorted_list)
            if specific_media_type:
                paginated_list = sorted_list[items_limit:]
            else:
                paginated_list = sorted_list[:items_limit]

            list_by_type[media_type] = {
                "items": paginated_list,
                "total": total_count,
            }

        return list_by_type

    def _get_media_types_to_process(self, user, specific_media_type):
        """Determine which media types to process based on user settings."""
        if specific_media_type:
            return [specific_media_type]

        # Get active types excluding seasons.
        # Home renders TV-level tracking instead of season-level.
        return [
            media_type
            for media_type in user.get_active_media_types()
            if media_type != MediaTypes.SEASON.value
        ]

    def _get_tv_in_progress_media(self, user):
        """Get TV media list for home in-progress cards."""
        media_list = list(
            self.get_media_list(
                user=user,
                media_type=MediaTypes.TV.value,
                status_filter=users.models.MediaStatusChoices.ALL,
                sort_filter=None,
            ).exclude(
                status__in=[
                    Status.PLANNING.value,
                    Status.PAUSED.value,
                    Status.DROPPED.value,
                ],
            ),
        )

        if not media_list:
            return media_list

        self.annotate_home_tv_entries(media_list)

        return [
            tv
            for tv in media_list
            if getattr(tv, "is_engaged_home", False) and tv.progress < tv.max_progress
        ]

    def annotate_home_tv_entries(self, tv_list, current_time=None):
        """Annotate TV entries with home-card fields and progress metadata."""
        if not tv_list:
            return

        if current_time is None:
            current_time = timezone.now()

        hidden_episode_map = self._build_hidden_episode_map(tv_list)
        aired_episode_map = self._get_tv_aired_episode_map(
            tv_list,
            current_time,
            hidden_episode_map,
        )
        all_episode_map = self._get_tv_all_episode_map(
            tv_list,
            hidden_episode_map,
        )
        self._annotate_tv_released_episodes(
            tv_list,
            current_time,
            hidden_episode_map=hidden_episode_map,
            aired_episode_map=aired_episode_map,
            include_home_progress=True,
        )
        self._annotate_next_event(tv_list, hidden_episode_map=hidden_episode_map)
        self._annotate_tv_home_next_episode(
            tv_list,
            current_time,
            hidden_episode_map=hidden_episode_map,
            aired_episode_map=aired_episode_map,
            all_episode_map=all_episode_map,
        )

    def maybe_mark_season_completed(self, season, current_time=None):
        """Mark season completed when all non-hidden episodes are aired and watched."""
        if current_time is None:
            current_time = timezone.now()

        hidden_numbers = set(
            Item.objects.filter(
                media_id=season.item.media_id,
                source=season.item.source,
                media_type=MediaTypes.EPISODE.value,
                season_number=season.item.season_number,
                is_hidden_override=True,
            ).values_list("episode_number", flat=True),
        )

        all_episode_numbers = set()
        has_unaired_non_hidden = False
        season_events = events.models.Event.objects.filter(
            item=season.item,
            content_number__isnull=False,
        ).values_list("content_number", "datetime")

        for content_number, event_datetime in season_events:
            if content_number in hidden_numbers:
                continue

            all_episode_numbers.add(content_number)
            if (
                not event_datetime
                or event_datetime <= UNKNOWN_RELEASE_DATETIME
                or event_datetime > current_time
            ):
                has_unaired_non_hidden = True

        if not all_episode_numbers or has_unaired_non_hidden:
            return

        watched_numbers = set(
            season.episode_watches.filter(item__is_hidden_override=False).values_list(
                "item__episode_number",
                flat=True,
            ),
        )
        if all_episode_numbers.issubset(watched_numbers):
            Season.objects.filter(pk=season.pk).update(status=Status.COMPLETED.value)
            season.status = Status.COMPLETED.value

    def _build_hidden_episode_map(self, tv_list):
        """Return hidden episode numbers keyed by (media_id, source, season_number)."""
        hidden_episode_items = Item.objects.filter(
            media_type=MediaTypes.EPISODE.value,
            is_hidden_override=True,
            media_id__in=[tv.item.media_id for tv in tv_list],
            source__in=[tv.item.source for tv in tv_list],
        ).values_list("media_id", "source", "season_number", "episode_number")

        hidden_map = {}
        for media_id, source, season_number, episode_number in hidden_episode_items:
            hidden_map.setdefault((media_id, source, season_number), set()).add(
                episode_number,
            )
        return hidden_map

    def _get_tv_aired_episode_map(self, tv_list, current_time, hidden_episode_map):
        """Return aired episode numbers keyed by TV id and season number."""
        if not tv_list:
            return {}

        tv_by_media_key = {
            (tv.item.media_id, tv.item.source): tv for tv in tv_list
        }
        aired_episode_map = {}
        aired_events = events.models.Event.objects.filter(
            item__media_id__in=[tv.item.media_id for tv in tv_list],
            item__source__in=[tv.item.source for tv in tv_list],
            item__media_type=MediaTypes.SEASON.value,
            item__season_number__gt=0,
            item__is_specials_override=False,
            item__is_hidden_override=False,
            datetime__gt=UNKNOWN_RELEASE_DATETIME,
            datetime__lte=current_time,
            content_number__isnull=False,
        ).select_related("item")

        for event in aired_events:
            tv = tv_by_media_key.get((event.item.media_id, event.item.source))
            if tv is None:
                continue

            season_number = event.item.season_number
            if event.content_number in hidden_episode_map.get(
                (tv.item.media_id, tv.item.source, season_number),
                set(),
            ):
                continue

            tv_seasons = aired_episode_map.setdefault(tv.id, {})
            season_data = tv_seasons.setdefault(
                season_number,
                {"item": event.item, "episodes": set()},
            )
            season_data["episodes"].add(event.content_number)

        return aired_episode_map

    def _get_tv_all_episode_map(self, tv_list, hidden_episode_map):
        """Return all known episode numbers keyed by TV id and season number."""
        if not tv_list:
            return {}

        tv_by_media_key = {
            (tv.item.media_id, tv.item.source): tv for tv in tv_list
        }
        all_episode_map = {}
        all_events = events.models.Event.objects.filter(
            item__media_id__in=[tv.item.media_id for tv in tv_list],
            item__source__in=[tv.item.source for tv in tv_list],
            item__media_type=MediaTypes.SEASON.value,
            item__season_number__gt=0,
            item__is_specials_override=False,
            item__is_hidden_override=False,
            content_number__isnull=False,
        ).select_related("item")

        for event in all_events:
            tv = tv_by_media_key.get((event.item.media_id, event.item.source))
            if tv is None:
                continue

            season_number = event.item.season_number
            if event.content_number in hidden_episode_map.get(
                (tv.item.media_id, tv.item.source, season_number),
                set(),
            ):
                continue

            tv_seasons = all_episode_map.setdefault(tv.id, {})
            season_data = tv_seasons.setdefault(
                season_number,
                {"item": event.item, "episodes": set()},
            )
            season_data["episodes"].add(event.content_number)

        return all_episode_map

    def _annotate_next_event(self, media_list, hidden_episode_map=None):
        """Annotate next_event for media items."""
        current_time = timezone.now()
        hidden_episode_map = hidden_episode_map or {}

        for media in media_list:
            if media.item.media_type == MediaTypes.TV.value:
                future_events = sorted(
                    [
                        event
                        for season in media.seasons.all()
                        if (
                            season.item.season_number != 0
                            and not season.item.is_specials_override
                            and not season.is_ignored
                        )
                        for event in getattr(season.item, "prefetched_events", [])
                        if (
                            event.datetime > current_time
                            and event.content_number is not None
                            and event.content_number
                            not in hidden_episode_map.get(
                                (
                                    media.item.media_id,
                                    media.item.source,
                                    season.item.season_number,
                                ),
                                set(),
                            )
                        )
                    ],
                    key=lambda e: e.datetime,
                )
                media.next_event = future_events[0] if future_events else None
                continue

            # Get future events sorted by datetime
            future_events = sorted(
                [
                    event
                    for event in getattr(media.item, "prefetched_events", [])
                    if event.datetime > current_time
                ],
                key=lambda e: e.datetime,
            )

            media.next_event = future_events[0] if future_events else None

    def _sort_in_progress_media(self, media_list, sort_by):
        """Sort in-progress media based on the sort criteria."""
        # Define primary sort functions based on sort_by
        primary_sort_functions = {
            users.models.HomeSortChoices.UPCOMING: lambda x: (
                x.next_event is None,
                x.next_event.datetime if x.next_event else None,
            ),
            users.models.HomeSortChoices.RECENT: lambda x: -timezone.datetime.timestamp(
                x.progressed_at if x.progressed_at is not None else x.created_at,
            ),
            users.models.HomeSortChoices.COMPLETION: lambda x: (
                x.max_progress is None,
                -(
                    x.progress / x.max_progress * 100
                    if x.max_progress and x.max_progress > 0
                    else 0
                ),
            ),
            users.models.HomeSortChoices.EPISODES_LEFT: lambda x: (
                x.max_progress is None,
                (x.max_progress - x.progress if x.max_progress else 0),
            ),
            users.models.HomeSortChoices.TITLE: lambda x: x.item.title.lower(),
        }

        primary_sort_function = primary_sort_functions[sort_by]

        return sorted(
            media_list,
            key=lambda x: (
                primary_sort_function(x),
                -timezone.datetime.timestamp(
                    x.progressed_at if x.progressed_at is not None else x.created_at,
                ),
                x.item.title.lower(),
            ),
        )

    def _annotate_tv_home_next_episode(
        self,
        tv_list,
        current_time,
        hidden_episode_map,
        aired_episode_map=None,
        all_episode_map=None,
    ):
        """Annotate TV entries with the next home episode to show/watch."""
        aired_episode_map = aired_episode_map or {}
        all_episode_map = all_episode_map or {}
        for tv in tv_list:
            seasons = list(tv.seasons.all())
            tracked_states, is_engaged = self._get_tracked_tv_home_season_states(
                tv,
                seasons,
                hidden_episode_map,
                aired_episode_map,
            )
            tracked_season_numbers, ignored_season_numbers = (
                self._get_tv_tracked_and_ignored_season_numbers(seasons)
            )
            untracked_states = self._get_untracked_tv_home_season_states(
                tv,
                aired_episode_map,
                tracked_season_numbers,
                ignored_season_numbers,
            )
            season_states = tracked_states + untracked_states
            self._reset_tv_home_annotations(tv, is_engaged)

            if not season_states:
                continue

            picked_state = self._pick_tv_home_episode_state(season_states)
            self._apply_tv_home_episode_state(
                tv,
                picked_state,
                all_episode_map,
                current_time,
            )

    def _get_tv_tracked_and_ignored_season_numbers(self, seasons):
        """Return tracked and ignored season-number sets for a TV entry."""
        tracked = {season.item.season_number for season in seasons}
        ignored = {season.item.season_number for season in seasons if season.is_ignored}
        return tracked, ignored

    def _get_tracked_tv_home_season_states(
        self,
        tv,
        seasons,
        hidden_episode_map,
        aired_episode_map,
    ):
        """Build candidate home-episode states from tracked seasons."""
        season_states = []
        is_engaged = False
        for season in sorted(
            seasons,
            key=lambda item: item.item.season_number,
        ):
            if self._is_skipped_home_season(season):
                continue

            hidden_numbers = self._get_hidden_episode_numbers(
                tv.item.media_id,
                tv.item.source,
                season.item.season_number,
                hidden_episode_map,
            )
            aired_numbers = self._get_aired_numbers_for_season(
                aired_episode_map,
                tv.id,
                season.item.season_number,
                season.item,
                hidden_numbers,
            )
            watched_numbers = self._get_visible_watched_numbers_for_season(season)
            if watched_numbers:
                is_engaged = True

            unwatched_aired = sorted(aired_numbers - watched_numbers)
            if not unwatched_aired:
                continue

            season_states.append(
                TVHomeSeasonState(
                    season=season,
                    season_item=season.item,
                    unwatched_aired=unwatched_aired,
                ),
            )
        return season_states, is_engaged

    def _is_skipped_home_season(self, season):
        """Return whether this season should be skipped on the home TV card."""
        return (
            season.item.season_number == 0
            or season.item.is_specials_override
            or season.is_ignored
        )

    def _get_aired_numbers_for_season(
        self,
        aired_episode_map,
        tv_id,
        season_number,
        season_item,
        hidden_numbers,
    ):
        """Return visible aired episode numbers for a season."""
        aired_numbers = set(
            aired_episode_map.get(tv_id, {})
            .get(season_number, {"item": season_item, "episodes": set()})
            .get("episodes", set()),
        )
        return {number for number in aired_numbers if number not in hidden_numbers}

    def _get_visible_watched_numbers_for_season(self, season):
        """Return visible watched episode numbers for a season."""
        return {
            watch.item.episode_number
            for watch in season.episode_watches.all()
            if not watch.item.is_hidden_override
        }

    def _get_hidden_episode_numbers(
        self,
        media_id,
        source,
        season_number,
        hidden_episode_map,
    ):
        """Return hidden episode numbers for a given media/season key."""
        return hidden_episode_map.get((media_id, source, season_number), set())

    def _get_untracked_tv_home_season_states(
        self,
        tv,
        aired_episode_map,
        tracked_season_numbers,
        ignored_season_numbers,
    ):
        """Build candidate home-episode states from untracked seasons."""
        season_states = []
        for season_number, season_data in aired_episode_map.get(tv.id, {}).items():
            if season_number in ignored_season_numbers:
                continue
            if season_number in tracked_season_numbers:
                continue

            aired_numbers = set(season_data["episodes"])
            if not aired_numbers:
                continue

            season_states.append(
                TVHomeSeasonState(
                    season=None,
                    season_item=season_data["item"],
                    unwatched_aired=sorted(aired_numbers),
                ),
            )
        return season_states

    def _reset_tv_home_annotations(self, tv, is_engaged):
        """Reset home-card fields to default values for a TV entry."""
        tv.is_engaged_home = is_engaged
        tv.home_display_title = tv.item.title
        tv.home_season_item = None
        tv.home_episode_number = None
        tv.home_episode_air_datetime = None
        tv.home_episode_badge = None

    def _pick_tv_home_episode_state(self, season_states):
        """Pick the earliest-season unwatched aired episode state."""
        first_state = min(
            season_states,
            key=lambda state: state.season_item.season_number,
        )
        return first_state, first_state.unwatched_aired[0]

    def _apply_tv_home_episode_state(
        self,
        tv,
        picked_state,
        all_episode_map,
        current_time,
    ):
        """Apply the selected home-card episode state to a TV entry."""
        state, episode_number = picked_state
        season_number = state.season_item.season_number
        tv.home_display_title = f"{tv.item.title} S{season_number} E{episode_number}"
        tv.home_season_item = state.season_item
        tv.home_episode_number = episode_number
        tv.home_episode_air_datetime = self._get_episode_air_datetime(
            tv.home_season_item,
            episode_number,
            current_time,
        )
        self._set_tv_home_episode_badge(
            tv,
            episode_number,
            season_number,
            all_episode_map,
        )

    def _get_episode_air_datetime(self, season_item, episode_number, current_time):
        """Return latest aired datetime for a season episode."""
        matching_event = (
            events.models.Event.objects.filter(
                item=season_item,
                content_number=episode_number,
                datetime__gt=UNKNOWN_RELEASE_DATETIME,
                datetime__lte=current_time,
            )
            .order_by("-datetime")
            .first()
        )
        return matching_event.datetime if matching_event else None

    def _annotate_season_released_episodes(self, season_list, current_datetime):
        """Annotate seasons with the number of distinctly released episodes."""
        hidden_episode_map = self._build_hidden_episode_map(season_list)
        released_numbers_by_season = {}
        season_by_item_id = {season.item_id: season for season in season_list}

        released_events = events.models.Event.objects.filter(
            item_id__in=season_by_item_id,
            content_number__isnull=False,
            datetime__gt=UNKNOWN_RELEASE_DATETIME,
            datetime__lte=current_datetime,
        )

        for event in released_events:
            season = season_by_item_id.get(event.item_id)
            if season is None:
                continue

            hidden_numbers = self._get_hidden_episode_numbers(
                season.item.media_id,
                season.item.source,
                season.item.season_number,
                hidden_episode_map,
            )
            if event.content_number in hidden_numbers:
                continue

            released_numbers_by_season.setdefault(season.id, set()).add(
                event.content_number,
            )

        for season in season_list:
            season.max_progress = len(released_numbers_by_season.get(season.id, set()))

    def _set_tv_home_episode_badge(
        self,
        tv,
        episode_number,
        season_number,
        all_episode_map,
    ):
        """Set home badge to premiere/finale when applicable."""
        all_numbers = sorted(
            all_episode_map.get(tv.id, {})
            .get(season_number, {"episodes": set()})
            .get("episodes", set()),
        )
        if not all_numbers:
            return

        is_premiere = episode_number == all_numbers[0]
        is_finale = episode_number == all_numbers[-1]
        if is_premiere and is_finale:
            tv.home_episode_badge = "Premiere • Finale"
        elif is_premiere:
            tv.home_episode_badge = "Premiere"
        elif is_finale:
            tv.home_episode_badge = "Finale"

    def annotate_max_progress(self, media_list, media_type):
        """Annotate max_progress for all media items."""
        current_datetime = timezone.now()

        if media_type == MediaTypes.MOVIE.value:
            for media in media_list:
                media.max_progress = 1
            return

        if media_type == MediaTypes.SEASON.value:
            self._annotate_season_released_episodes(media_list, current_datetime)
            return

        if media_type == MediaTypes.TV.value:
            self._annotate_tv_released_episodes(media_list, current_datetime)
            return

        # For other media types, calculate max_progress from events
        # Create a dictionary mapping item_id to max content_number
        max_progress_dict = {}

        item_ids = [media.item.id for media in media_list]

        # Fetch all relevant events in a single query
        events_data = events.models.Event.objects.filter(
            item_id__in=item_ids,
            datetime__lte=current_datetime,
        ).values("item_id", "content_number")

        # Process events to find max content number per item
        for event in events_data:
            item_id = event["item_id"]
            content_number = event["content_number"]
            if content_number is not None:
                current_max = max_progress_dict.get(item_id, 0)
                max_progress_dict[item_id] = max(current_max, content_number)

        for media in media_list:
            media.max_progress = max_progress_dict.get(media.item.id)

    def _annotate_tv_released_episodes(
        self,
        tv_list,
        current_datetime,
        hidden_episode_map=None,
        aired_episode_map=None,
        *,
        include_home_progress=False,
    ):
        """Annotate TV shows with the number of released episodes."""
        hidden_episode_map = hidden_episode_map or {}
        if aired_episode_map is None:
            aired_episode_map = self._get_tv_aired_episode_map(
                tv_list,
                current_datetime,
                hidden_episode_map,
            )

        for tv in tv_list:
            seasons = list(tv.seasons.all())
            _, ignored_season_numbers = self._get_tv_tracked_and_ignored_season_numbers(
                seasons,
            )
            aired_episodes = self._get_tv_aired_episode_keys(
                tv.id,
                aired_episode_map,
                ignored_season_numbers,
            )
            watched_episodes = self._get_tv_watched_aired_episode_keys(
                tv,
                seasons,
                aired_episodes,
                hidden_episode_map,
            )
            tv.max_progress = len(aired_episodes)
            if include_home_progress:
                tv._home_progress = len(watched_episodes)

    def _get_tv_aired_episode_keys(
        self,
        tv_id,
        aired_episode_map,
        ignored_season_numbers,
    ):
        """Return aired episode keys for non-ignored seasons."""
        aired_episodes = set()
        for season_number, season_data in aired_episode_map.get(tv_id, {}).items():
            if season_number in ignored_season_numbers:
                continue
            aired_episodes.update(
                (season_number, episode_number)
                for episode_number in season_data["episodes"]
            )
        return aired_episodes

    def _get_tv_watched_aired_episode_keys(
        self,
        tv,
        seasons,
        aired_episodes,
        hidden_episode_map,
    ):
        """Return watched aired episode keys excluding hidden episodes."""
        watched_episodes = set()
        for season in seasons:
            hidden_numbers = self._get_hidden_episode_numbers(
                tv.item.media_id,
                tv.item.source,
                season.item.season_number,
                hidden_episode_map,
            )
            for watch in season.episode_watches.all():
                if watch.item.is_hidden_override:
                    continue
                if watch.item.episode_number in hidden_numbers:
                    continue
                episode_key = (
                    season.item.season_number,
                    watch.item.episode_number,
                )
                if episode_key in aired_episodes:
                    watched_episodes.add(episode_key)
        return watched_episodes

    def fetch_media_for_items(self, media_types, item_ids, user, status_filter=None):
        """Fetch media objects for given items, optionally filtering by status.

        Args:
            media_types: Iterable of media type strings to query
            item_ids: QuerySet or list of item IDs to fetch media for
            user: User to filter media by
            status_filter: Optional status value to filter by

        Returns:
            dict mapping item_id to media object
        """
        media_by_item_id = {}

        for media_type in media_types:
            model = apps.get_model("app", media_type)

            if media_type == MediaTypes.EPISODE.value:
                filter_kwargs = {
                    "item__in": item_ids,
                    "related_season__user": user,
                }
                if status_filter:
                    filter_kwargs["related_season__status"] = status_filter
            else:
                filter_kwargs = {
                    "item__in": item_ids,
                    "user": user,
                }
                if status_filter:
                    filter_kwargs["status"] = status_filter

            queryset = model.objects.filter(**filter_kwargs).select_related("item")
            queryset = self._apply_prefetch_related(queryset, media_type)
            self.annotate_max_progress(queryset, media_type)

            for entry in queryset:
                media_by_item_id.setdefault(entry.item_id, entry)

        return media_by_item_id

    def get_media(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get user media object given the media type and item."""
        if media_type == MediaTypes.EPISODE.value:
            model = EpisodeWatch
        else:
            model = apps.get_model(app_label="app", model_name=media_type)
        params = self._get_media_params(
            user,
            media_type,
            instance_id,
        )

        return model.objects.get(**params)

    def get_media_prefetch(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get user media object with prefetch_related applied."""
        if media_type == MediaTypes.EPISODE.value:
            model = EpisodeWatch
        else:
            model = apps.get_model(app_label="app", model_name=media_type)
        params = self._get_media_params(
            user,
            media_type,
            instance_id,
        )

        queryset = model.objects.filter(**params)

        queryset = self._apply_prefetch_related(queryset, media_type)
        self.annotate_max_progress(queryset, media_type)

        return queryset[0]

    def _get_media_params(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get the common filter parameters for media queries."""
        params = {"id": instance_id}

        if media_type == MediaTypes.EPISODE.value:
            params["related_season__user"] = user
        else:
            params["user"] = user

        return params

    def filter_media(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
    ):
        """Filter media objects based on parameters."""
        if media_type == MediaTypes.EPISODE.value:
            model = EpisodeWatch
        else:
            model = apps.get_model(app_label="app", model_name=media_type)
        params = self._filter_media_params(
            media_type,
            media_id,
            source,
            user,
            season_number,
            episode_number,
        )

        return model.objects.filter(**params)

    def filter_media_prefetch(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
    ):
        """Filter user media object with prefetch_related applied."""
        queryset = self.filter_media(
            user,
            media_id,
            media_type,
            source,
            season_number,
            episode_number,
        )
        queryset = self._apply_prefetch_related(queryset, media_type)
        self.annotate_max_progress(queryset, media_type)

        return queryset

    def _filter_media_params(
        self,
        media_type,
        media_id,
        source,
        user,
        season_number=None,
        episode_number=None,
    ):
        """Get the common filter parameters for media queries."""
        params = {
            "item__media_type": media_type,
            "item__source": source,
            "item__media_id": media_id,
        }

        if media_type == MediaTypes.SEASON.value:
            params["item__season_number"] = season_number
            params["user"] = user
        elif media_type == MediaTypes.EPISODE.value:
            params["item__season_number"] = season_number
            params["item__episode_number"] = episode_number
            params["related_season__user"] = user
        else:
            params["user"] = user

        return params


class Status(models.TextChoices):
    """Choices for item status."""

    COMPLETED = "Completed", "Completed"
    IN_PROGRESS = "In progress", "In Progress"
    PLANNING = "Planning", "Planning"
    PAUSED = "Paused", "Paused"
    DROPPED = "Dropped", "Dropped"


class Media(models.Model):
    """Abstract model for all media types."""

    history = HistoricalRecords(
        cascade_delete_history=True,
        inherit=True,
        excluded_fields=[
            "item",
            "progressed_at",
            "user",
            "related_tv",
            "created_at",
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    score = models.DecimalField(
        null=True,
        blank=True,
        max_digits=3,
        decimal_places=1,
        validators=[
            DecimalValidator(3, 1),
            MinValueValidator(0),
            MaxValueValidator(10),
        ],
    )
    progress = models.PositiveIntegerField(default=0)
    progressed_at = MonitorField(monitor="progress")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED.value,
    )
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        """Meta options for the model."""

        abstract = True
        ordering = ["user", "item", "-created_at"]

    def __str__(self):
        """Return the title of the media."""
        return self.item.__str__()

    def save(self, *args, **kwargs):
        """Save the media instance."""
        if self.tracker.has_changed("progress"):
            self.process_progress()

        if self.tracker.has_changed("status"):
            self.process_status()

        super().save(*args, **kwargs)

    def process_progress(self):
        """Update fields depending on the progress of the media."""
        if self.progress < 0:
            self.progress = 0
        else:
            max_progress = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )["max_progress"]

            if max_progress:
                self.progress = min(self.progress, max_progress)

                if self.progress == max_progress:
                    self.status = Status.COMPLETED.value

                    now = timezone.now().replace(second=0, microsecond=0)
                    self.end_date = now

    def process_status(self):
        """Update fields depending on the status of the media."""
        if self.status == Status.COMPLETED.value:
            max_progress = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )["max_progress"]

            if max_progress:
                self.progress = max_progress

        self.item.fetch_releases(delay=True)

    @property
    def formatted_score(self):
        """Return as int if score is 10.0 or 0.0, otherwise show decimal."""
        if self.score is not None:
            max_score = 10
            min_score = 0
            if self.score in (max_score, min_score):
                return int(self.score)
            return self.score
        return None

    @property
    def formatted_progress(self):
        """Return the progress of the media in a formatted string."""
        return str(self.progress)

    def increase_progress(self):
        """Increase the progress of the media by one."""
        self.progress += 1
        self.save()
        logger.info("Incresed progress of %s to %s", self, self.progress)

    def decrease_progress(self):
        """Decrease the progress of the media by one."""
        self.progress -= 1
        self.save()
        logger.info("Decreased progress of %s to %s", self, self.progress)


class BasicMedia(Media):
    """Model for basic media types."""

    objects = MediaManager()


class TV(Media):
    """Model for TV shows."""

    tracker = FieldTracker()

    class Meta:
        """Meta options for the model."""

        ordering = ["user", "item"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "item"],
                name="%(app_label)s_%(class)s_unique_item_user",
            ),
        ]

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == Status.COMPLETED.value:
                self._completed()

            elif self.status == Status.DROPPED.value:
                self._mark_in_progress_seasons_as_dropped()

            elif (
                self.status == Status.IN_PROGRESS.value
                and not self.seasons.filter(status=Status.IN_PROGRESS.value).exists()
            ):
                self._start_next_available_season()

            self.item.fetch_releases(delay=True)

    @property
    def progress(self):
        """Return the total episodes watched for the TV show."""
        cached_progress = getattr(self, "_home_progress", None)
        if cached_progress is not None:
            return cached_progress

        return sum(
            season.progress
            for season in self.seasons.all()
            if (
                season.item.season_number != 0
                and not season.item.is_specials_override
                and not season.is_ignored
            )
        )

    @property
    def last_watched(self):
        """Return the latest watched episode in SxxExx format."""
        watched_episodes = [
            {
                "season": season.item.season_number,
                "episode": episode.item.episode_number,
                "end_date": episode.watched_at,
            }
            for season in self.seasons.all()
            if (
                hasattr(season, "episode_watches")
                and season.item.season_number != 0
                and not season.item.is_specials_override
                and not season.is_ignored
            )
            for episode in season.episode_watches.all()
            if episode.watched_at is not None
        ]

        if not watched_episodes:
            return ""

        latest_episode = max(
            watched_episodes,
            key=lambda x: (x["end_date"], x["season"], x["episode"]),
        )

        return f"S{latest_episode['season']:02d}E{latest_episode['episode']:02d}"

    @property
    def progressed_at(self):
        """Return the date when the last episode was watched."""
        dates = [
            season.progressed_at
            for season in self.seasons.all()
            if (
                season.progressed_at
                and season.item.season_number != 0
                and not season.item.is_specials_override
                and not season.is_ignored
            )
        ]
        return max(dates) if dates else None

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            season.start_date
            for season in self.seasons.all()
            if (
                season.start_date
                and season.item.season_number != 0
                and not season.item.is_specials_override
                and not season.is_ignored
            )
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [
            season.end_date
            for season in self.seasons.all()
            if (
                season.end_date
                and season.item.season_number != 0
                and not season.item.is_specials_override
                and not season.is_ignored
            )
        ]
        return max(dates) if dates else None

    def _completed(self):
        """Create remaining seasons and episodes for a TV show."""
        tv_metadata = providers.services.get_media_metadata(
            self.item.media_type,
            self.item.media_id,
            self.item.source,
        )
        max_progress = tv_metadata["max_progress"]

        if not max_progress or self.progress > max_progress:
            return

        seasons_to_create = []
        seasons_to_update = []
        episodes_to_create = []

        specials_override_numbers = set(
            Item.objects.filter(
                media_id=self.item.media_id,
                source=self.item.source,
                media_type=MediaTypes.SEASON.value,
                is_specials_override=True,
            ).values_list("season_number", flat=True),
        )
        ignored_numbers = set(
            self.seasons.filter(is_ignored=True).values_list(
                "item__season_number",
                flat=True,
            ),
        )
        season_numbers = [
            season["season_number"]
            for season in tv_metadata["related"]["seasons"]
            if season["season_number"] != 0
            and season["season_number"] not in specials_override_numbers
            and season["season_number"] not in ignored_numbers
        ]
        tv_with_seasons_metadata = providers.services.get_media_metadata(
            "tv_with_seasons",
            self.item.media_id,
            self.item.source,
            season_numbers,
        )
        for season_number in season_numbers:
            season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=self.item.source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                defaults={
                    "title": self.item.title,
                    "image": season_metadata["image"],
                },
            )
            try:
                season_instance = Season.objects.get(
                    item=item,
                    user=self.user,
                )

                if season_instance.status != Status.COMPLETED.value:
                    season_instance.status = Status.COMPLETED.value
                    seasons_to_update.append(season_instance)

            except Season.DoesNotExist:
                seasons_to_create.append(
                    Season(
                        item=item,
                        score=None,
                        status=Status.COMPLETED.value,
                        notes="",
                        related_tv=self,
                        user=self.user,
                    ),
                )

        bulk_create_with_history(seasons_to_create, Season)
        bulk_update_with_history(seasons_to_update, Season, ["status"])

        for season_instance in seasons_to_create + seasons_to_update:
            season_metadata = tv_with_seasons_metadata[
                f"season/{season_instance.item.season_number}"
            ]
            episodes_to_create.extend(
                season_instance.get_remaining_eps(season_metadata),
            )
        if episodes_to_create:
            EpisodeWatch.objects.bulk_create(episodes_to_create, batch_size=1000)

    def _mark_in_progress_seasons_as_dropped(self):
        """Mark all in-progress seasons as dropped."""
        in_progress_seasons = list(
            self.seasons.filter(status=Status.IN_PROGRESS.value),
        )

        for season in in_progress_seasons:
            season.status = Status.DROPPED.value

        if in_progress_seasons:
            bulk_update_with_history(
                in_progress_seasons,
                Season,
                fields=["status"],
            )

    def _start_next_available_season(self):
        """Find the next available season to watch and set it to in-progress."""
        all_seasons = self.seasons.filter(
            item__season_number__gt=0,
            item__is_specials_override=False,
            is_ignored=False,
        ).order_by("item__season_number")

        next_unwatched_season = all_seasons.exclude(
            status__in=[Status.COMPLETED.value],
        ).first()

        if not next_unwatched_season:
            # If all existing seasons are watched, get the next available season
            tv_metadata = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )
            specials_override_numbers = set(
                Item.objects.filter(
                    media_id=self.item.media_id,
                    source=self.item.source,
                    media_type=MediaTypes.SEASON.value,
                    is_specials_override=True,
                ).values_list("season_number", flat=True),
            )
            ignored_numbers = set(
                self.seasons.filter(is_ignored=True).values_list(
                    "item__season_number",
                    flat=True,
                ),
            )

            existing_season_numbers = set(
                all_seasons.values_list("item__season_number", flat=True),
            )

            for season_data in tv_metadata["related"]["seasons"]:
                season_number = season_data["season_number"]
                if (
                    season_number > 0
                    and season_number not in existing_season_numbers
                    and season_number not in specials_override_numbers
                    and season_number not in ignored_numbers
                ):
                    item, _ = Item.objects.get_or_create(
                        media_id=self.item.media_id,
                        source=self.item.source,
                        media_type=MediaTypes.SEASON.value,
                        season_number=season_data["season_number"],
                        defaults={
                            "title": self.item.title,
                            "image": season_data["image"],
                        },
                    )

                    next_unwatched_season = Season(
                        item=item,
                        user=self.user,
                        related_tv=self,
                        status=Status.IN_PROGRESS.value,
                    )
                    bulk_create_with_history([next_unwatched_season], Season)
                    break

        elif next_unwatched_season.status != Status.IN_PROGRESS.value:
            next_unwatched_season.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [next_unwatched_season],
                Season,
                fields=["status"],
            )


class Season(Media):
    """Model for seasons of TV shows."""

    related_tv = models.ForeignKey(
        TV,
        on_delete=models.CASCADE,
        related_name="seasons",
    )
    is_ignored = models.BooleanField(default=False)

    tracker = FieldTracker()

    class Meta:
        """Limit the uniqueness of seasons.

        Only one season per media can have the same season number.
        """

        constraints = [
            models.UniqueConstraint(
                fields=["related_tv", "item"],
                name="%(app_label)s_season_unique_tv_item",
            ),
        ]

    def __str__(self):
        """Return the title of the media and season number."""
        return f"{self.item.title} S{self.item.season_number}"

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        # if related_tv is not set
        if self.related_tv_id is None:
            self.related_tv = self.get_tv()

        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == Status.COMPLETED.value:
                season_metadata = providers.services.get_media_metadata(
                    MediaTypes.SEASON.value,
                    self.item.media_id,
                    self.item.source,
                    [self.item.season_number],
                )
                episodes_to_create = self.get_remaining_eps(season_metadata)
                if episodes_to_create:
                    EpisodeWatch.objects.bulk_create(
                        episodes_to_create,
                        batch_size=1000,
                    )

            elif (
                self.status == Status.DROPPED.value
                and self.related_tv.status != Status.DROPPED.value
            ):
                self.related_tv.status = Status.DROPPED.value
                bulk_update_with_history(
                    [self.related_tv],
                    TV,
                    fields=["status"],
                )

            elif (
                self.status == Status.IN_PROGRESS.value
                and self.related_tv.status != Status.IN_PROGRESS.value
            ):
                self.related_tv.status = Status.IN_PROGRESS.value
                bulk_update_with_history(
                    [self.related_tv],
                    TV,
                    fields=["status"],
                )

            self.item.fetch_releases(delay=True)

    @property
    def progress(self):
        """Return number of distinct watched episodes in the season."""
        return len(
            {
                watch.item.episode_number
                for watch in self.episode_watches.all()
                if not watch.item.is_hidden_override
            },
        )

    def _current_episode_number(self):
        """Return watched episode number used for next/unwatch actions."""
        episodes = self.episode_watches.all()
        if not episodes:
            return 0

        if self.status == Status.IN_PROGRESS.value:
            episode_counts = {}
            for watch in episodes:
                episode_number = watch.item.episode_number
                episode_counts[episode_number] = (
                    episode_counts.get(episode_number, 0) + 1
                )

            sorted_episodes = sorted(
                episodes,
                key=lambda watch: (
                    -episode_counts[watch.item.episode_number],
                    -watch.item.episode_number,
                ),
            )
            return sorted_episodes[0].item.episode_number

        return max(watch.item.episode_number for watch in episodes)

    @property
    def progressed_at(self):
        """Return the date when the last episode was watched."""
        dates = [
            episode.watched_at
            for episode in self.episode_watches.all()
            if episode.watched_at is not None
        ]
        return max(dates) if dates else None

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            episode.watched_at
            for episode in self.episode_watches.all()
            if episode.watched_at is not None
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [
            episode.watched_at
            for episode in self.episode_watches.all()
            if episode.watched_at is not None
        ]
        return max(dates) if dates else None

    def increase_progress(self):
        """Watch the next episode of the season.

        Legacy support for progress_edit +/- season controls. Primary season
        tracking is episode-driven (episode_save and home_watch_next_episode).
        """
        season_metadata = providers.services.get_media_metadata(
            MediaTypes.SEASON.value,
            self.item.media_id,
            self.item.source,
            [self.item.season_number],
        )
        episodes = season_metadata["episodes"]

        current_episode_number = self._current_episode_number()
        if current_episode_number == 0:
            # start watching from the first episode
            next_episode_number = episodes[0]["episode_number"]
        else:
            next_episode_number = providers.tmdb.find_next_episode(
                current_episode_number,
                episodes,
            )

        now = timezone.now().replace(second=0, microsecond=0)

        if next_episode_number:
            self.watch(next_episode_number, now)
        else:
            logger.info("No more episodes to watch.")

    def watch(self, episode_number, end_date, source="manual"):
        """Create or add a repeat to an episode of the season."""
        item = self.get_episode_item(episode_number)

        episode = EpisodeWatch.objects.create(
            related_season=self,
            item=item,
            watched_at=end_date,
            source=source,
        )
        logger.info(
            "%s created successfully.",
            episode,
        )

    def decrease_progress(self):
        """Unwatch the current episode of the season.

        Legacy support for progress_edit +/- season controls. Primary season
        tracking is episode-driven (episode_save and home_watch_next_episode).
        """
        current_episode_number = self._current_episode_number()
        if current_episode_number == 0:
            return
        self.unwatch(current_episode_number)

    def unwatch(self, episode_number):
        """Unwatch the episode instance."""
        item = self.get_episode_item(episode_number)

        episodes = EpisodeWatch.objects.filter(
            related_season=self,
            item=item,
        ).order_by("-watched_at")

        episode = episodes.first()

        if episode is None:
            logger.warning(
                "Episode %s does not exist.",
                self.item,
            )
            return

        # Get count before deletion for logging
        remaining_count = episodes.count() - 1

        episode.delete()
        logger.info(
            "Deleted %s S%02dE%02d (%d remaining instances)",
            self.item.title,
            self.item.season_number,
            episode_number,
            remaining_count,
        )

    def get_tv(self):
        """Get related TV instance for a season and create it if it doesn't exist."""
        try:
            tv = TV.objects.get(
                item__media_id=self.item.media_id,
                item__media_type=MediaTypes.TV.value,
                item__season_number=None,
                item__source=self.item.source,
                user=self.user,
            )
        except TV.DoesNotExist:
            tv_metadata = providers.services.get_media_metadata(
                MediaTypes.TV.value,
                self.item.media_id,
                self.item.source,
            )

            # creating tv with multiple seasons from a completed season
            if (
                self.status == Status.COMPLETED.value
                and tv_metadata["details"]["seasons"] > 1
            ):
                status = Status.IN_PROGRESS.value
            else:
                status = self.status

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                defaults={
                    "title": tv_metadata["title"],
                    "image": tv_metadata["image"],
                },
            )

            tv = TV(
                item=item,
                score=None,
                status=status,
                notes="",
                user=self.user,
            )

            # save_base to avoid custom save method
            TV.save_base(tv)

            logger.info("%s did not exist, it was created successfully.", tv)

        return tv

    def get_remaining_eps(self, season_metadata):
        """Return episodes needed to complete a season."""
        latest_watched_ep_num = EpisodeWatch.objects.filter(
            related_season=self,
        ).aggregate(latest_watched_ep_num=Max("item__episode_number"))[
            "latest_watched_ep_num"
        ]

        if latest_watched_ep_num is None:
            latest_watched_ep_num = 0

        episodes_to_create = []

        # Calculate current time once before the loop
        now = timezone.now().replace(second=0, microsecond=0)

        # Create EpisodeWatch objects for the remaining episodes
        for episode in reversed(season_metadata["episodes"]):
            if episode["episode_number"] <= latest_watched_ep_num:
                break

            item = self.get_episode_item(episode["episode_number"], season_metadata)

            # Resolve end_date based on user preference
            end_date = self.user.resolve_watch_date(now, episode.get("air_date"))

            episode_db = EpisodeWatch(
                related_season=self,
                item=item,
                watched_at=end_date,
                source="bulk",
            )
            episodes_to_create.append(episode_db)

        return episodes_to_create

    def get_episode_item(self, episode_number, season_metadata=None):
        """Get the episode item instance, create it if it doesn't exist."""
        existing_item = Item.objects.filter(
            media_id=self.item.media_id,
            source=self.item.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=self.item.season_number,
            episode_number=episode_number,
        ).first()
        if existing_item is not None:
            return existing_item

        if not season_metadata:
            season_metadata = providers.services.get_media_metadata(
                MediaTypes.SEASON.value,
                self.item.media_id,
                self.item.source,
                [self.item.season_number],
            )

        image = settings.IMG_NONE
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == int(episode_number):
                if episode.get("still_path"):
                    image = (
                        f"https://image.tmdb.org/t/p/original{episode['still_path']}"
                    )
                elif "image" in episode:
                    # for manual seasons
                    image = episode["image"]
                else:
                    image = season_metadata.get("image", settings.IMG_NONE)
                break

        item, _ = Item.objects.get_or_create(
            media_id=self.item.media_id,
            source=self.item.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=self.item.season_number,
            episode_number=episode_number,
            defaults={
                "title": self.item.title,
                "image": image,
            },
        )

        return item


class EpisodeWatch(models.Model):
    """Model for episode watch events."""

    created_at = models.DateTimeField(auto_now_add=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    related_season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="episode_watches",
    )
    watched_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text="Origin of the watch event (manual, bulk, import, webhook).",
    )

    class Meta:
        """Meta options for the model."""

        ordering = [
            "related_season",
            "item__episode_number",
            "-watched_at",
            "-created_at",
        ]
        indexes = [
            models.Index(fields=["related_season", "item"]),
            models.Index(fields=["related_season", "watched_at"]),
        ]

    def __str__(self):
        """Return a readable identifier for the watch event."""
        return f"{self.item} (watch #{self.id})"


class Manga(Media):
    """Model for manga."""

    tracker = FieldTracker()


class Anime(Media):
    """Model for anime."""

    tracker = FieldTracker()


class Movie(Media):
    """Model for movies."""

    tracker = FieldTracker()


class Game(Media):
    """Model for games."""

    tracker = FieldTracker()

    @property
    def formatted_progress(self):
        """Return progress in hours:minutes format."""
        return app.helpers.minutes_to_hhmm(self.progress)

    def increase_progress(self):
        """Increase the progress of the media by 30 minutes."""
        self.progress += 30
        self.save()
        logger.info("Changed playtime of %s to %s", self, self.formatted_progress)

    def decrease_progress(self):
        """Decrease the progress of the media by 30 minutes."""
        self.progress -= 30
        self.save()
        logger.info("Changed playtime of %s to %s", self, self.formatted_progress)


class Book(Media):
    """Model for books."""

    tracker = FieldTracker()


class Comic(Media):
    """Model for comics."""

    tracker = FieldTracker()
