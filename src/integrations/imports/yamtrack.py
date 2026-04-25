import logging
from collections import defaultdict
from csv import DictReader
from datetime import UTC, datetime

from django.apps import apps
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app import config
from app import forms as app_forms
from app import models as app_models
from app.models import MediaTypes, Sources
from app.providers import services
from app.templatetags import app_tags
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from CSV file using the class-based importer."""
    csv_importer = YamtrackImporter(file, user, mode)
    return csv_importer.import_data()


class YamtrackImporter:
    """Class to handle importing user data from CSV files."""

    def __init__(self, file, user, mode):
        """Initialize the importer with file, user, and mode.

        Args:
            file: Uploaded CSV file object
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)
        self.episode_shared_candidates = {}
        self.episode_instances_by_key = defaultdict(list)

        logger.info(
            "Initialized Yamtrack CSV importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all user data from the CSV file."""
        try:
            decoded_file = self.file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        reader = DictReader(decoded_file)

        for row_index, row in enumerate(reader, start=1):
            try:
                self._process_row(row, row_index)
            except services.ProviderAPIError as error:
                error_msg = (
                    f"Error processing entry with ID {row['media_id']} "
                    f"({app_tags.media_type_readable(row['media_type'])}): {error}"
                )
                self.warnings.append(error_msg)
                continue
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        self._normalize_episode_shared_fields()
        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _process_row(self, row, row_index):
        """Process a single row from the CSV file."""
        media_type = row["media_type"]

        season_number = (
            int(row["season_number"]) if row["season_number"] != "" else None
        )
        episode_number = (
            int(row["episode_number"]) if row["episode_number"] != "" else None
        )

        if row["progress"] == "":
            row["progress"] = 0

        parent_type = (
            MediaTypes.TV.value
            if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
            else media_type
        )

        # Check if we should process this movie based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            parent_type,
            row["source"],
            row["media_id"],
            self.mode,
        ):
            return

        if row["title"] == "" or row["image"] == "":
            self._handle_missing_metadata(
                row,
                media_type,
                season_number,
                episode_number,
            )

        item, _ = app_models.Item.objects.update_or_create(
            media_id=row["media_id"],
            source=row["source"],
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": row["title"],
                "image": row["image"],
            },
        )

        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item)
        if media_type != MediaTypes.EPISODE.value:  # episode has no user field
            instance.user = self.user

        row["item"] = item
        form = app_forms.get_form_class(media_type)(
            row,
            instance=instance,
        )

        if form.is_valid():
            if (
                media_type == MediaTypes.EPISODE.value
                and not self._validate_and_track_episode_shared_fields(
                    row,
                    row_index,
                    form.instance,
                )
            ):
                return
            progressed_at = row.get("progressed_at")
            if progressed_at:
                form.instance._history_date = parse_datetime(progressed_at)
            self.bulk_media[media_type].append(form.instance)
        else:
            error_msg = f"{row['title']} ({media_type}): {form.errors.as_json()}"
            self.warnings.append(error_msg)
            logger.error(error_msg)

    def _validate_and_track_episode_shared_fields(self, row, row_index, instance):
        """Validate and stage episode shared fields for later normalization."""
        shared_form = app_forms.EpisodeSharedFieldsForm(
            data={
                "score": row.get("score") or "",
                "notes": row.get("notes") or "",
            },
        )
        if not shared_form.is_valid():
            error_msg = f"{row['title']} (episode): {shared_form.errors.as_json()}"
            self.warnings.append(error_msg)
            logger.error(error_msg)
            return False

        episode_key = (
            row["source"],
            row["media_id"],
            instance.item.season_number,
            instance.item.episode_number,
        )
        rank = self._episode_shared_rank(
            instance.end_date,
            parse_datetime(row.get("created_at")) if row.get("created_at") else None,
            row_index,
        )
        candidate = {
            "rank": rank,
            "score": shared_form.cleaned_data["score"],
            "notes": shared_form.cleaned_data["notes"],
        }

        current = self.episode_shared_candidates.get(episode_key)
        if current is None or candidate["rank"] > current["rank"]:
            self.episode_shared_candidates[episode_key] = candidate

        self.episode_instances_by_key[episode_key].append(instance)
        return True

    def _normalize_episode_shared_fields(self):
        """Apply canonical shared episode fields across imported watches."""
        for episode_key, instances in self.episode_instances_by_key.items():
            candidate = self.episode_shared_candidates.get(episode_key)
            if candidate is None:
                continue

            for instance in instances:
                instance.score = candidate["score"]
                instance.notes = candidate["notes"]

    def _episode_shared_rank(self, end_date, created_at, row_index):
        """Return comparable rank tuple for episode shared-field conflicts."""
        return (
            end_date is not None,
            self._normalize_datetime(end_date),
            created_at is not None,
            self._normalize_datetime(created_at),
            row_index,
        )

    def _normalize_datetime(self, value):
        """Return an aware datetime for ranking, or the minimum sentinel."""
        if value is None:
            return datetime.min.replace(tzinfo=UTC)
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_default_timezone())
        return value

    def _handle_missing_metadata(self, row, media_type, season_number, episode_number):
        """Handle missing metadata by fetching from provider."""
        if row["source"] == Sources.MANUAL.value and row["image"] == "":
            row["image"] = settings.IMG_NONE
            return

        if row.get("media_id", "") != "":
            metadata = services.get_media_metadata(
                media_type,
                row["media_id"],
                row["source"],
                [season_number],
                episode_number,
            )
            row["title"] = metadata["title"]
            row["image"] = metadata["image"]
            return

        if row.get("title", "") != "":
            source = row.get("source", "")
            if source == "":
                source = config.get_default_source_name(media_type).value

            metadata = services.search(
                media_type,
                row["title"],
                1,
                source,
            )

            first_result = metadata["results"][0]
            row["title"] = first_result["title"]
            row["source"] = first_result["source"]
            row["media_id"] = first_result["media_id"]
            row["media_type"] = media_type
            row["image"] = first_result["image"]

            logger.info("Added title from %s: %s", source, row["title"])
            logger.info("Obtained media id: %s", row["media_id"])
            return

        msg = f"Missing metadata for: {row}"
        raise MediaImportError(msg)
