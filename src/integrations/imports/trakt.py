import json
import logging
from collections import defaultdict

import requests
from django.conf import settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django_celery_beat.models import PeriodicTask

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError
from lists.models import CustomList, CustomListItem, ImportedListSourceChoices

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"
BULK_PAGE_SIZE = 1000


def handle_oauth_callback(request):
    """View for getting the Trakt OAuth2 token."""
    code = request.GET["code"]

    url = "https://api.trakt.tv/oauth/token"

    params = {
        "client_id": settings.TRAKT_API,
        "client_secret": settings.TRAKT_API_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": request.build_absolute_uri(reverse("import_trakt_private")),
    }

    try:
        token_response = app.providers.services.api_request(
            "TRAKT",
            "POST",
            url,
            params=params,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        raise

    return {
        "refresh_token": token_response["refresh_token"],
        "username": get_username_from_oauth(token_response["access_token"]),
    }


def get_username_from_oauth(access_token):
    """View for getting the Trakt OAuth2 username."""
    url = "https://api.trakt.tv/users/me"

    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": settings.TRAKT_API,
        "Authorization": f"Bearer {access_token}",
    }

    try:
        request = app.providers.services.api_request(
            "TRAKT",
            "GET",
            url,
            headers=headers,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        raise

    return request["username"]


def get_access_token(encrypted_refresh_token):
    """Get access token from encrypted refresh token."""
    url = "https://api.trakt.tv/oauth/token"

    decrypted_token = helpers.decrypt(encrypted_refresh_token)

    params = {
        "client_id": settings.TRAKT_API,
        "client_secret": settings.TRAKT_API_SECRET,
        "refresh_token": decrypted_token,
        "grant_type": "refresh_token",
        "redirect_uri": f"{settings.BASE_URL}/import/trakt/private",
    }

    try:
        request = app.providers.services.api_request(
            "TRAKT",
            "POST",
            url,
            params=params,
        )
    except services.ProviderAPIError as error:
        if error.status_code == requests.codes.unauthorized:
            msg = "Invalid Trakt secret key."
            raise MediaImportError(msg) from error
        raise

    # refresh tokens are one time use only
    update_refresh_token(encrypted_refresh_token, request["refresh_token"])
    return request["access_token"]


def update_refresh_token(old_token, new_token):
    """Update the refresh token in periodic tasks."""
    periodic_task = PeriodicTask.objects.filter(
        task="Import from Trakt",
        kwargs__contains=f'"token": "{old_token}"',
    ).first()

    if periodic_task:
        task_kwargs = json.loads(periodic_task.kwargs)
        task_kwargs["token"] = helpers.encrypt(new_token)
        periodic_task.kwargs = json.dumps(task_kwargs)
        periodic_task.save()


def importer(token, user, mode, username):
    """Import the user's data from Trakt.

    Can import using either OAuth (token provided) or public username.
    When using OAuth, username should be the authenticated user's username.
    When using public import, username is the Trakt username and token should be None.

    Args:
        token (str, optional): Encrypted OAuth2 refresh token if using OAuth else None
        user: Django user object to import data for
        mode (str): Import mode ("new" or "overwrite")
        username (str): Trakt username to import from
    """
    trakt_importer = TraktImporter(username, user, mode, refresh_token=token)
    return trakt_importer.import_data()


class TraktImporter:
    """Class to handle importing user data from Trakt."""

    def __init__(self, username, user, mode, refresh_token=None):
        """Initialize the importer with user details and mode.

        Args:
            username (str): Trakt username to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
            refresh_token (str, optional): Encrypted OAuth2 refresh token if
                using OAuth, None for public import
        """
        self.username = username
        self.user = user
        self.mode = mode
        self.refresh_token = refresh_token
        self.user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
        self.warnings = []

        # Track existing media to handle "new" mode correctly
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        # Track media instances being created
        self.media_instances = defaultdict(lambda: defaultdict(list))
        self.list_counts = defaultdict(int)

        logger.info(
            "Initialized Trakt importer for user %s with mode %s",
            username,
            mode,
        )

    def import_data(self):
        """Import all user data from Trakt."""
        self.process_history()
        self.process_watchlist()
        self.process_ratings()
        self.process_comments()

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)
        self.process_lists()

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }
        imported_counts.update(
            {
                count_type: count
                for count_type, count in self.list_counts.items()
                if count > 0
            },
        )
        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))

        return imported_counts, deduplicated_messages

    def _format_warning_prefix(self, warning_context):
        """Format a warning prefix when extra context is provided."""
        return f"{warning_context} - " if warning_context else ""

    def _make_api_request(self, url):
        """Make a request to the Trakt API with proper headers."""
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": settings.TRAKT_API,
        }
        if self.refresh_token:
            try:
                # already made api_request before, so access_token is set
                headers["Authorization"] = f"Bearer {self.access_token}"
            except AttributeError:
                self.access_token = get_access_token(self.refresh_token)
                headers["Authorization"] = f"Bearer {self.access_token}"
        return services.api_request(
            "TRAKT",
            "GET",
            url,
            headers=headers,
        )

    def _get_paginated_data(self, endpoint, item_type="items"):
        """Get paginated data from Trakt API."""
        page = 1
        all_data = []

        while True:
            url = f"{endpoint}?page={page}&limit={BULK_PAGE_SIZE}"

            try:
                page_data = self._make_api_request(url)
            except requests.exceptions.HTTPError as error:
                if error.response.status_code == requests.codes.not_found:
                    msg = (
                        f"User slug {self.username} not found. "
                        "User slug can be found in your Trakt profile URL."
                    )
                    raise MediaImportError(msg) from error

                if error.response.status_code == requests.codes.unauthorized:
                    msg = "This account is set to private, use OAuth import instead."
                    raise MediaImportError(msg) from error
                raise

            if not page_data:
                # We've reached the end of the data
                break

            all_data.extend(page_data)
            page += 1
            logger.info(
                "Retrieved page %s of %s for user %s (%s items)",
                page - 1,
                item_type,
                self.username,
                len(page_data),
            )

        logger.info(
            "Retrieved %s total %s for user %s",
            len(all_data),
            item_type,
            self.username,
        )
        return all_data

    def process_history(self):
        """Process watch history from Trakt."""
        logger.info("Importing watch history for user %s", self.username)
        history_endpoint = f"{self.user_base_url}/history"
        full_history = self._get_paginated_data(history_endpoint, "history entries")

        # Process in chronological order (oldest first)
        for entry in reversed(full_history):
            watched_at = entry["watched_at"]
            try:
                if entry["type"] == "movie":
                    logger.info(
                        "Processing movie %s watched at %s",
                        entry["movie"]["title"],
                        watched_at,
                    )
                    self.process_watched_movie(entry)
                elif entry["type"] == "episode":
                    logger.info(
                        "Processing episode %s S%sE%s watched at %s",
                        entry["show"]["title"],
                        entry["episode"]["season"],
                        entry["episode"]["number"],
                        watched_at,
                    )
                    self.process_watched_episode(entry)
            except Exception as e:
                msg = f"Error processing history entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def _get_tmdb_id(self, entry_data, warning_context=None, title=None):
        """Extract TMDB ID from entry data."""
        if (
            "ids" in entry_data
            and "tmdb" in entry_data["ids"]
            and entry_data["ids"]["tmdb"]
        ):
            return str(entry_data["ids"]["tmdb"])

        prefix = self._format_warning_prefix(warning_context)
        warning_title = title or entry_data.get("title") or "Unknown title"
        self.warnings.append(
            f"{prefix}{warning_title}: No {Sources.TMDB.label} ID found.",
        )
        return None

    def _get_metadata(
        self,
        media_type,
        tmdb_id,
        title,
        season_number=None,
        warning_context=None,
    ):
        """Get metadata for a media item."""
        try:
            kwargs = {}
            if season_number is not None:
                kwargs["season_numbers"] = [season_number]

            return services.get_media_metadata(
                media_type,
                tmdb_id,
                Sources.TMDB.value,
                **kwargs,
            )
        except services.ProviderAPIError as error:
            if error.status_code == requests.codes.not_found:
                if media_type == MediaTypes.SEASON.value:
                    title = f"{title} S{season_number}"
                prefix = self._format_warning_prefix(warning_context)
                self.warnings.append(
                    (
                        f"{prefix}{title}: not found in {Sources.TMDB.label} "
                        f"with ID {tmdb_id}."
                    ),
                )
                return None
            raise

    def _get_or_create_item(
        self,
        media_type,
        tmdb_id,
        metadata,
        season_number=None,
        episode_number=None,
    ):
        """Get or create an item in the database."""
        item_kwargs = {
            "media_id": tmdb_id,
            "source": Sources.TMDB.value,
            "media_type": media_type,
        }

        if season_number is not None:
            item_kwargs["season_number"] = season_number

        if episode_number is not None:
            item_kwargs["episode_number"] = episode_number

        defaults = {
            "title": metadata["title"],
            "image": metadata["image"],
        }

        item, _ = app.models.Item.objects.get_or_create(
            **item_kwargs,
            defaults=defaults,
        )

        return item

    def process_watched_movie(self, entry):
        """Process a single movie watch event."""
        movie = entry["movie"]
        tmdb_id = self._get_tmdb_id(movie)
        if not tmdb_id:
            return

        # Check if we should process this movie based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        metadata = self._get_metadata(MediaTypes.MOVIE.value, tmdb_id, movie["title"])
        if not metadata:
            return

        item = self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        watched_at = entry["watched_at"]

        key = f"{tmdb_id}"

        movie_obj = app.models.Movie(
            item=item,
            user=self.user,
            end_date=watched_at,
            status=Status.COMPLETED.value,
        )
        movie_obj._history_date = parse_datetime(watched_at)

        self.media_instances[MediaTypes.MOVIE.value][key].append(movie_obj)
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_obj)

    def _get_episode_image(self, episode_number, season_metadata):
        """Extract episode image URL from season metadata."""
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == episode_number:
                if episode.get("still_path"):
                    return f"https://image.tmdb.org/t/p/w500{episode['still_path']}"
                break
        return settings.IMG_NONE

    def process_watched_episode(self, entry):
        """Process a single episode watch event."""
        show = entry["show"]
        tmdb_id = self._get_tmdb_id(show)
        if not tmdb_id:
            return

        # Check if we should process this episode based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.TV.value,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        # Extract episode data
        season_number = entry["episode"]["season"]
        episode_number = entry["episode"]["number"]

        # Get TV metadata
        tv_metadata = self._get_metadata(MediaTypes.TV.value, tmdb_id, show["title"])
        if not tv_metadata:
            return

        # Get Season metadata
        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            show["title"],
            season_number,
        )
        if not season_metadata:
            return

        # Validate episode number exists in TMDB
        episode_exists = any(
            ep["episode_number"] == episode_number for ep in season_metadata["episodes"]
        )

        if not episode_exists:
            item_identifier = f"{show['title']} S{season_number}E{episode_number}"
            self.warnings.append(
                f"{item_identifier}: not found in {Sources.TMDB.label} "
                f"with ID {tmdb_id}.",
            )
            return

        episode_image = self._get_episode_image(episode_number, season_metadata)
        watched_at = entry["watched_at"]

        # Create or get TV show
        tv_item = self._get_or_create_item(MediaTypes.TV.value, tmdb_id, tv_metadata)
        tv_key = f"{tmdb_id}"

        if tv_key not in self.media_instances[MediaTypes.TV.value]:
            tv_obj = app.models.TV(
                item=tv_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
            tv_obj._history_date = parse_datetime(watched_at)
            self.bulk_media[MediaTypes.TV.value].append(tv_obj)
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        else:
            tv_obj = self.media_instances[MediaTypes.TV.value][tv_key][0]

        # Create or get Season
        season_item = self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
        )

        season_key = f"{tmdb_id}:{season_number}"
        if season_key not in self.media_instances[MediaTypes.SEASON.value]:
            season_obj = app.models.Season(
                item=season_item,
                user=self.user,
                related_tv=tv_obj,
                status=Status.IN_PROGRESS.value,
            )
            season_obj._history_date = parse_datetime(watched_at)
            self.bulk_media[MediaTypes.SEASON.value].append(season_obj)
            self.media_instances[MediaTypes.SEASON.value][season_key] = [season_obj]
        else:
            season_obj = self.media_instances[MediaTypes.SEASON.value][season_key][0]

        # Create Episode item and object
        episode_metadata = {
            "title": tv_metadata["title"],
            "image": episode_image,
        }
        episode_item = self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
        )

        ep_key = f"{tmdb_id}:{season_number}:{episode_number}"

        episode_obj = app.models.Episode(
            item=episode_item,
            related_season=season_obj,
            end_date=watched_at,
        )
        episode_obj._history_date = parse_datetime(watched_at)
        self.media_instances[MediaTypes.EPISODE.value][ep_key].append(episode_obj)
        self.bulk_media[MediaTypes.EPISODE.value].append(episode_obj)

        # Update status if this is the last episode
        self._update_completion_status(
            season_obj,
            tv_obj,
            season_number,
            episode_number,
            season_metadata,
            tv_metadata,
        )

    def _update_completion_status(
        self,
        season_obj,
        tv_obj,
        season_number,
        episode_number,
        season_metadata,
        tv_metadata,
    ):
        """Update completion status for season and TV show if applicable."""
        if episode_number == season_metadata["max_progress"]:
            season_obj.status = Status.COMPLETED.value

            last_season = tv_metadata.get("last_episode_season")
            if last_season and last_season == season_number:
                tv_obj.status = Status.COMPLETED.value

    def process_watchlist(self):
        """Process watchlist from Trakt."""
        logger.info("Importing watchlist for user %s", self.username)
        watchlist_endpoint = f"{self.user_base_url}/watchlist"
        watchlist_data = self._make_api_request(watchlist_endpoint)

        for entry in watchlist_data:
            try:
                self._process_generic_entry(
                    entry,
                    "watchlist",
                    {"status": Status.PLANNING.value},
                )
            except Exception as e:
                msg = f"Error processing watchlist entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_ratings(self):
        """Process ratings from Trakt."""
        logger.info("Importing ratings for user %s", self.username)
        ratings_endpoint = f"{self.user_base_url}/ratings"
        ratings_data = self._make_api_request(ratings_endpoint)

        for entry in ratings_data:
            try:
                self._process_generic_entry(
                    entry,
                    "rating",
                    {"score": entry["rating"]},
                )
            except Exception as e:
                msg = f"Error processing rating entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_comments(self):
        """Process comments from Trakt."""
        logger.info("Importing comments for user %s", self.username)
        comments_endpoint = f"{self.user_base_url}/comments"
        full_comments = self._get_paginated_data(comments_endpoint, "comments")

        for entry in full_comments:
            try:
                self._process_generic_entry(
                    entry,
                    "comment",
                    {"notes": entry["comment"]["comment"]},
                )
            except Exception as e:
                msg = f"Error processing comment entry: {entry}"
                raise MediaImportUnexpectedError(msg) from e

    def process_lists(self):
        """Process custom lists from Trakt."""
        logger.info("Importing custom lists for user %s", self.username)
        lists_endpoint = f"{self.user_base_url}/lists"
        trakt_lists = self._make_api_request(lists_endpoint)

        for trakt_list in trakt_lists:
            try:
                self._process_list(trakt_list)
            except Exception:
                list_name = trakt_list.get("name", "Unnamed list")
                self.warnings.append(
                    f"List '{list_name}': unexpected error while importing list.",
                )
                logger.exception(
                    "Unexpected error importing Trakt list %s for user %s",
                    list_name,
                    self.username,
                )

    def _process_list(self, trakt_list):
        """Create or update a custom list from Trakt."""
        list_name = trakt_list["name"]
        list_identifier = self._get_trakt_list_identifier(trakt_list)
        if not list_identifier:
            self.warnings.append(
                f"List '{list_name}': missing Trakt list identifier.",
            )
            return

        list_items = self._get_list_items(list_identifier, list_name)
        if list_items is None:
            return

        resolved_item_ids = []
        for entry in list_items:
            item = self._resolve_list_item(list_name, entry)
            if item is not None:
                resolved_item_ids.append(item.id)

        custom_list, created = CustomList.objects.get_or_create(
            owner=self.user,
            import_source=ImportedListSourceChoices.TRAKT.value,
            import_source_id=list_identifier,
            defaults={
                "name": list_name,
                "description": trakt_list.get("description") or "",
            },
        )

        if created:
            self.list_counts["list_created"] += 1
        else:
            self.list_counts["list_updated"] += 1

        self._sync_list_metadata(custom_list, trakt_list)
        self._sync_list_items(custom_list, resolved_item_ids)

    def _get_trakt_list_identifier(self, trakt_list):
        """Return the stable Trakt identifier for a list."""
        list_ids = trakt_list.get("ids", {})
        trakt_id = list_ids.get("trakt")
        if trakt_id:
            return str(trakt_id)
        return list_ids.get("slug")

    def _get_list_items(self, list_identifier, list_name):
        """Fetch all items for a Trakt list."""
        page = 1
        all_items = []

        while True:
            url = (
                f"{self.user_base_url}/lists/{list_identifier}/items"
                f"?page={page}&limit={BULK_PAGE_SIZE}"
            )

            try:
                page_items = self._make_api_request(url)
            except requests.exceptions.HTTPError as error:
                if error.response.status_code == requests.codes.not_found:
                    self.warnings.append(
                        f"List '{list_name}': unable to fetch list items from Trakt.",
                    )
                    return None
                raise

            if not page_items:
                break

            all_items.extend(page_items)
            page += 1

        return all_items

    def _sync_list_metadata(self, custom_list, trakt_list):
        """Keep linked custom-list metadata in sync with Trakt."""
        fields_to_update = []
        description = trakt_list.get("description") or ""

        if custom_list.name != trakt_list["name"]:
            custom_list.name = trakt_list["name"]
            fields_to_update.append("name")

        if custom_list.description != description:
            custom_list.description = description
            fields_to_update.append("description")

        if fields_to_update:
            custom_list.save(update_fields=fields_to_update)

    def _sync_list_items(self, custom_list, item_ids):
        """Sync the contents of a linked custom list."""
        ordered_item_ids = list(dict.fromkeys(item_ids))

        if self.mode == "overwrite":
            custom_list.items.set(ordered_item_ids)
            self.list_counts["list_item"] += len(ordered_item_ids)
            return

        existing_item_ids = set(custom_list.items.values_list("id", flat=True))
        new_item_ids = [
            item_id for item_id in ordered_item_ids if item_id not in existing_item_ids
        ]

        if not new_item_ids:
            return

        CustomListItem.objects.bulk_create(
            [
                CustomListItem(custom_list=custom_list, item_id=item_id)
                for item_id in new_item_ids
            ],
            ignore_conflicts=True,
        )
        self.list_counts["list_item"] += len(new_item_ids)

    def _resolve_list_item(self, list_name, entry):
        """Resolve a Trakt list entry to a Yamtrack item."""
        entry_type = entry["type"]

        if entry_type == "person":
            return None
        if entry_type == "movie":
            return self._resolve_movie_list_item(list_name, entry)
        if entry_type == "show":
            return self._resolve_show_list_item(list_name, entry)
        if entry_type == "season":
            return self._resolve_season_list_item(list_name, entry)
        if entry_type == "episode":
            return self._resolve_episode_list_item(list_name, entry)

        self.warnings.append(
            (
                f"List '{list_name}' - {self._get_list_item_display_name(entry)}: "
                "unsupported list item type."
            ),
        )
        return None

    def _resolve_movie_list_item(self, list_name, entry):
        """Resolve a movie list item."""
        movie = entry["movie"]
        item_name = self._get_list_item_display_name(entry)
        tmdb_id = self._get_tmdb_id(
            movie,
            warning_context=f"List '{list_name}'",
            title=item_name,
        )
        if not tmdb_id:
            return None

        metadata = self._get_metadata(
            MediaTypes.MOVIE.value,
            tmdb_id,
            movie["title"],
            warning_context=f"List '{list_name}'",
        )
        if not metadata:
            return None

        return self._get_or_create_item(MediaTypes.MOVIE.value, tmdb_id, metadata)

    def _resolve_show_list_item(self, list_name, entry):
        """Resolve a show list item."""
        show = entry["show"]
        item_name = self._get_list_item_display_name(entry)
        tmdb_id = self._get_tmdb_id(
            show,
            warning_context=f"List '{list_name}'",
            title=item_name,
        )
        if not tmdb_id:
            return None

        metadata = self._get_metadata(
            MediaTypes.TV.value,
            tmdb_id,
            show["title"],
            warning_context=f"List '{list_name}'",
        )
        if not metadata:
            return None

        return self._get_or_create_item(MediaTypes.TV.value, tmdb_id, metadata)

    def _resolve_season_list_item(self, list_name, entry):
        """Resolve a season list item."""
        show = entry["show"]
        season_number = entry["season"]["number"]
        item_name = self._get_list_item_display_name(entry)
        tmdb_id = self._get_tmdb_id(
            show,
            warning_context=f"List '{list_name}'",
            title=item_name,
        )
        if not tmdb_id:
            return None

        metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            show["title"],
            season_number,
            warning_context=f"List '{list_name}'",
        )
        if not metadata:
            return None

        return self._get_or_create_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            metadata,
            season_number,
        )

    def _resolve_episode_list_item(self, list_name, entry):
        """Resolve an episode list item."""
        show = entry["show"]
        season_number = entry["episode"]["season"]
        episode_number = entry["episode"]["number"]
        item_name = self._get_list_item_display_name(entry)
        tmdb_id = self._get_tmdb_id(
            show,
            warning_context=f"List '{list_name}'",
            title=item_name,
        )
        if not tmdb_id:
            return None

        tv_metadata = self._get_metadata(
            MediaTypes.TV.value,
            tmdb_id,
            show["title"],
            warning_context=f"List '{list_name}'",
        )
        if not tv_metadata:
            return None

        season_metadata = self._get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            show["title"],
            season_number,
            warning_context=f"List '{list_name}'",
        )
        if not season_metadata:
            return None

        episode_exists = any(
            episode["episode_number"] == episode_number
            for episode in season_metadata["episodes"]
        )
        if not episode_exists:
            self.warnings.append(
                (
                    f"List '{list_name}' - {item_name}: not found in "
                    f"{Sources.TMDB.label} with ID {tmdb_id}."
                ),
            )
            return None

        episode_metadata = {
            "title": tv_metadata["title"],
            "image": self._get_episode_image(episode_number, season_metadata),
        }
        return self._get_or_create_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
        )

    def _get_list_item_display_name(self, entry):
        """Return a human-readable label for a Trakt list entry."""
        if entry["type"] == "movie":
            return entry["movie"]["title"]
        if entry["type"] == "show":
            return entry["show"]["title"]
        if entry["type"] == "season":
            return f"{entry['show']['title']} S{entry['season']['number']}"
        if entry["type"] == "episode":
            return (
                f"{entry['show']['title']} "
                f"S{entry['episode']['season']}E{entry['episode']['number']}"
            )
        if entry["type"] == "person":
            return entry["person"]["name"]
        return "Unknown item"

    def _process_generic_entry(self, entry, entry_type, attribute_updates=None):
        """Process a generic entry (watchlist, rating, or comment)."""
        if entry["type"] == "movie":
            logger.info(
                "Processing movie %s for %s",
                entry["movie"]["title"],
                entry_type,
            )
            self._process_media_item(
                entry,
                entry["movie"],
                MediaTypes.MOVIE.value,
                app.models.Movie,
                attribute_updates or {},
            )
        elif entry["type"] == "show":
            logger.info(
                "Processing show %s for %s",
                entry["show"]["title"],
                entry_type,
            )
            self._process_media_item(
                entry,
                entry["show"],
                MediaTypes.TV.value,
                app.models.TV,
                attribute_updates or {},
            )
        elif entry["type"] == "season":
            logger.info(
                "Processing season %s S%s for %s",
                entry["show"]["title"],
                entry["season"]["number"],
                entry_type,
            )
            self._process_media_item(
                entry,
                entry["show"],
                MediaTypes.SEASON.value,
                app.models.Season,
                attribute_updates or {},
                entry["season"]["number"],
            )
        elif entry["type"] == "episode":
            logger.info(
                "Processing episode %s S%sE%s for %s",
                entry["show"]["title"],
                entry["episode"]["season"],
                entry["episode"]["number"],
                entry_type,
            )
            self._process_episode_generic_entry(
                entry,
                attribute_updates or {},
            )

    def _process_episode_generic_entry(self, entry, defaults):
        """Update an already-imported episode with rating/comment data."""
        tmdb_id = self._get_tmdb_id(entry["show"])
        if not tmdb_id:
            return

        season_number = entry["episode"]["season"]
        episode_number = entry["episode"]["number"]
        key = f"{tmdb_id}:{season_number}:{episode_number}"

        if key not in self.media_instances[MediaTypes.EPISODE.value]:
            logger.info(
                "Skipping ep %s S%sE%s for generic import; no watched entry exists",
                entry["show"]["title"],
                season_number,
                episode_number,
            )
            return

        self._update_instance(MediaTypes.EPISODE.value, key, defaults)

    def _process_media_item(
        self,
        entry,
        media_data,
        media_type,
        model_class,
        defaults=None,
        season_number=None,
    ):
        """Process media items for watchlist, ratings, and comments."""
        tmdb_id = self._get_tmdb_id(media_data)
        if not tmdb_id:
            return

        parent_type = (
            MediaTypes.TV.value if media_type == MediaTypes.SEASON.value else media_type
        )
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            parent_type,
            Sources.TMDB.value,
            tmdb_id,
            self.mode,
        ):
            return

        metadata = self._get_metadata(
            media_type,
            tmdb_id,
            media_data["title"],
            season_number,
        )
        if not metadata:
            return

        updated_at = parse_datetime(
            entry.get("listed_at")
            or entry.get("rated_at")
            or entry["comment"].get("updated_at"),
        )

        if media_type == MediaTypes.SEASON.value:
            tv_obj = self._get_tv_obj(tmdb_id, media_data, updated_at)
            if not tv_obj:
                return
            defaults["related_tv"] = tv_obj

        key = f"{tmdb_id}"
        if media_type == MediaTypes.SEASON.value:
            key = f"{key}:{season_number}"

        item = self._get_or_create_item(media_type, tmdb_id, metadata, season_number)

        if key in self.media_instances[media_type]:
            self._update_instance(media_type, key, defaults)
        else:
            media_obj = model_class(
                item=item,
                user=self.user,
                **defaults,
            )
            media_obj._history_date = updated_at
            self.bulk_media[media_type].append(media_obj)
            self.media_instances[media_type][key] = [media_obj]

    def _get_tv_obj(self, tmdb_id, media_data, updated_at):
        """Get or create a TV object for the given season."""
        tv_metadata = self._get_metadata(
            MediaTypes.TV.value,
            tmdb_id,
            media_data["title"],
        )
        if not tv_metadata:
            return None

        tv_item = self._get_or_create_item(
            MediaTypes.TV.value,
            tmdb_id,
            tv_metadata,
        )

        tv_key = f"{tmdb_id}"

        # Create or get the TV object
        if tv_key in self.media_instances[MediaTypes.TV.value]:
            tv_obj = self.media_instances[MediaTypes.TV.value][tv_key][0]
        else:
            tv_obj = app.models.TV(
                item=tv_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )
            tv_obj._history_date = updated_at
            self.bulk_media[MediaTypes.TV.value].append(tv_obj)
            self.media_instances[MediaTypes.TV.value][tv_key] = [tv_obj]
        return tv_obj

    def _update_instance(self, media_type, key, defaults):
        """Update the instance with new attributes."""
        for media_obj in self.media_instances[media_type][key]:
            for attr, value in defaults.items():
                setattr(media_obj, attr, value)
