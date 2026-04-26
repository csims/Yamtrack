import logging
from collections import defaultdict
from csv import DictReader
from dataclasses import dataclass

from django.conf import settings
from django.utils.dateparse import parse_datetime

from app import models as app_models
from app.models import MediaTypes, Sources
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError
from lists.models import CustomList, CustomListItem

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {
    "row_type",
    "list_key",
    "list_name",
    "list_description",
    "list_import_source",
    "list_import_source_id",
    "list_item_added_at",
    "item_source",
    "item_media_type",
    "item_media_id",
    "item_season_number",
    "item_episode_number",
    "item_title",
    "item_image",
}
LIST_ROW_TYPE = "list"
LIST_ITEM_ROW_TYPE = "list_item"


@dataclass
class ImportedListRecord:
    """Imported custom list metadata and resolved database target."""

    custom_list: CustomList
    created: bool


@dataclass
class MembershipCandidate:
    """Resolved list membership row."""

    item: app_models.Item
    date_added: object
    row_index: int


def importer(file, user, mode):
    """Import custom lists from a Yamtrack CSV file."""
    csv_importer = YamtrackListsImporter(file, user, mode)
    return csv_importer.import_data()


class YamtrackListsImporter:
    """Importer for Yamtrack custom list CSV backups."""

    def __init__(self, file, user, mode):
        """Initialize importer state."""
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []
        self.imported_counts = defaultdict(int)
        self.imported_lists = {}
        self.skipped_list_keys = set()
        self.memberships_by_list_key = defaultdict(list)

        logger.info(
            "Initialized Yamtrack custom list importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import custom lists from CSV."""
        rows = self._read_rows()

        for row_index, row in rows:
            try:
                self._process_list_row(row, row_index)
            except Exception as error:
                error_msg = f"Error processing custom list row: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        for row_index, row in rows:
            try:
                self._process_list_item_row(row, row_index)
            except Exception as error:
                error_msg = f"Error processing custom list item row: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        self._apply_memberships()

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return dict(self.imported_counts), deduplicated_messages

    def _read_rows(self):
        """Read and validate CSV rows."""
        try:
            decoded_file = self.file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from error

        reader = DictReader(decoded_file)
        if not reader.fieldnames:
            msg = "Invalid custom list CSV file."
            raise MediaImportError(msg)

        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing_columns:
            msg = (
                "Invalid custom list CSV file. Missing columns: "
                + ", ".join(sorted(missing_columns))
            )
            raise MediaImportError(msg)

        return list(enumerate(reader, start=2))

    def _process_list_row(self, row, row_index):
        """Process a `list` row."""
        row_type = row["row_type"]
        if row_type != LIST_ROW_TYPE:
            if row_type != LIST_ITEM_ROW_TYPE:
                self._warn(
                    row_index,
                    "Invalid row type for custom list CSV.",
                )
            return

        list_row_data = self._get_list_row_data(row, row_index)
        if list_row_data is None:
            return

        imported_record = self._resolve_custom_list(
            list_key=list_row_data["list_key"],
            list_name=list_row_data["list_name"],
            description=list_row_data["description"],
            import_source=list_row_data["import_source"],
            import_source_id=list_row_data["import_source_id"],
            row_index=row_index,
        )
        if imported_record is None:
            self.skipped_list_keys.add(list_row_data["list_key"])
            return

        self.imported_lists[list_row_data["list_key"]] = imported_record

    def _get_list_row_data(self, row, row_index):
        """Return validated list row data or `None` when the row should be skipped."""
        list_key = row["list_key"].strip()
        if not list_key:
            self._warn(row_index, "Missing list_key for list row.")
            return None
        if list_key in self.imported_lists or list_key in self.skipped_list_keys:
            self._warn(row_index, f"Duplicate list_key '{list_key}'.")
            self.skipped_list_keys.add(list_key)
            self.imported_lists.pop(list_key, None)
            return None

        import_source = row["list_import_source"].strip()
        import_source_id = row["list_import_source_id"].strip()
        if bool(import_source) != bool(import_source_id):
            self._warn(
                row_index,
                f"List '{list_key}' must include both import source fields or neither.",
            )
            self.skipped_list_keys.add(list_key)
            return None

        list_name = row["list_name"].strip()
        if not list_name:
            self._warn(row_index, f"List '{list_key}' is missing a name.")
            self.skipped_list_keys.add(list_key)
            return None

        return {
            "list_key": list_key,
            "list_name": list_name,
            "description": row["list_description"],
            "import_source": import_source,
            "import_source_id": import_source_id,
        }

    def _resolve_custom_list(
        self,
        *,
        list_key,
        list_name,
        description,
        import_source,
        import_source_id,
        row_index,
    ):
        """Return or create the target custom list for an imported row."""
        if import_source and import_source_id:
            custom_list, created = CustomList.objects.get_or_create(
                owner=self.user,
                import_source=import_source,
                import_source_id=import_source_id,
                defaults={
                    "name": list_name,
                    "description": description,
                },
            )
        else:
            matching_lists = list(
                CustomList.objects.filter(
                    owner=self.user,
                    name=list_name,
                ),
            )
            if len(matching_lists) > 1:
                self._warn(
                    row_index,
                    f"List '{list_name}' matches multiple owned lists; skipping.",
                )
                return None
            if matching_lists:
                custom_list = matching_lists[0]
                created = False
            else:
                custom_list = CustomList.objects.create(
                    owner=self.user,
                    name=list_name,
                    description=description,
                )
                created = True

        if created:
            self.imported_counts["list_created"] += 1
        else:
            self.imported_counts["list_updated"] += 1

        self._sync_list_metadata(
            custom_list,
            list_name=list_name,
            description=description,
            import_source=import_source,
            import_source_id=import_source_id,
        )
        logger.info("Imported custom list %s from key %s", custom_list, list_key)
        return ImportedListRecord(custom_list=custom_list, created=created)

    def _sync_list_metadata(
        self,
        custom_list,
        *,
        list_name,
        description,
        import_source,
        import_source_id,
    ):
        """Update imported list metadata in place."""
        fields_to_update = []
        if custom_list.name != list_name:
            custom_list.name = list_name
            fields_to_update.append("name")
        if custom_list.description != description:
            custom_list.description = description
            fields_to_update.append("description")
        if import_source and custom_list.import_source != import_source:
            custom_list.import_source = import_source
            fields_to_update.append("import_source")
        if import_source_id and custom_list.import_source_id != import_source_id:
            custom_list.import_source_id = import_source_id
            fields_to_update.append("import_source_id")

        if fields_to_update:
            custom_list.save(update_fields=fields_to_update)

    def _process_list_item_row(self, row, row_index):
        """Process a `list_item` row."""
        row_type = row["row_type"]
        if row_type == LIST_ROW_TYPE:
            return
        if row_type != LIST_ITEM_ROW_TYPE:
            return

        list_key = row["list_key"].strip()
        if list_key in self.skipped_list_keys:
            return

        imported_record = self.imported_lists.get(list_key)
        if imported_record is None:
            self._warn(
                row_index,
                f"List item references unknown list_key '{list_key}'.",
            )
            return

        item = self._resolve_item(row, row_index)
        if item is None:
            return

        date_added = parse_datetime(row["list_item_added_at"])
        if date_added is None:
            self._warn(
                row_index,
                f"List item for '{imported_record.custom_list.name}' has invalid date.",
            )
            return

        self.memberships_by_list_key[list_key].append(
            MembershipCandidate(
                item=item,
                date_added=date_added,
                row_index=row_index,
            ),
        )

    def _resolve_item(self, row, row_index):
        """Resolve or create an Item from list item identity columns."""
        item_kwargs = self._get_item_identity(row, row_index)
        if item_kwargs is None:
            return None

        item = app_models.Item.objects.filter(**item_kwargs).first()
        if item is not None:
            return item

        title = row["item_title"].strip()
        image = row["item_image"].strip() or settings.IMG_NONE
        if not title:
            self._warn(
                row_index,
                "Missing item title for list item row that needs a new Item.",
            )
            return None

        return app_models.Item.objects.create(
            **item_kwargs,
            title=title,
            image=image,
        )

    def _get_item_identity(self, row, row_index):
        """Return validated item identity kwargs or `None` for skipped rows."""
        source = row["item_source"].strip()
        media_type = row["item_media_type"].strip()
        media_id = row["item_media_id"].strip()
        season_number = self._parse_optional_int(row["item_season_number"])
        episode_number = self._parse_optional_int(row["item_episode_number"])

        if not source or not media_type or not media_id:
            self._warn(row_index, "List item row is missing item identity fields.")
            return None
        if source not in Sources.values:
            self._warn(row_index, f"Unsupported item source '{source}'.")
            return None
        if media_type not in MediaTypes.values:
            self._warn(row_index, f"Unsupported item media type '{media_type}'.")
            return None
        if not self._is_valid_item_identity(
            media_type,
            season_number,
            episode_number,
            row_index,
        ):
            return None

        return {
            "media_id": media_id,
            "source": source,
            "media_type": media_type,
            "season_number": season_number,
            "episode_number": episode_number,
        }

    def _is_valid_item_identity(
        self,
        media_type,
        season_number,
        episode_number,
        row_index,
    ):
        """Validate season/episode identity columns against media type."""
        if media_type == MediaTypes.SEASON.value:
            if season_number is None or episode_number is not None:
                self._warn(
                    row_index,
                    (
                        "Season list items must include season number and no "
                        "episode number."
                    ),
                )
                return False
            return True
        if media_type == MediaTypes.EPISODE.value:
            if season_number is None or episode_number is None:
                self._warn(
                    row_index,
                    "Episode list items must include season and episode numbers.",
                )
                return False
            return True
        if season_number is not None or episode_number is not None:
            self._warn(
                row_index,
                (
                    "Only season and episode list items may include season/"
                    "episode numbers."
                ),
            )
            return False
        return True

    def _apply_memberships(self):
        """Create or replace imported list memberships."""
        for list_key, imported_record in self.imported_lists.items():
            custom_list = imported_record.custom_list
            resolved_candidates = self._deduplicate_memberships(
                self.memberships_by_list_key.get(list_key, []),
            )

            if self.mode == "overwrite":
                custom_list.customlistitem_set.all().delete()
                if resolved_candidates:
                    self._create_memberships(custom_list, resolved_candidates)
                self.imported_counts["list_item"] += len(resolved_candidates)
                continue

            existing_item_ids = set(
                custom_list.items.values_list("id", flat=True),
            )
            new_candidates = [
                candidate
                for candidate in resolved_candidates
                if candidate.item.id not in existing_item_ids
            ]
            if not new_candidates:
                continue

            self._create_memberships(custom_list, new_candidates)
            self.imported_counts["list_item"] += len(new_candidates)

    def _create_memberships(self, custom_list, candidates):
        """Create memberships and restore imported `date_added` values."""
        memberships = CustomListItem.objects.bulk_create(
            [
                CustomListItem(
                    custom_list=custom_list,
                    item=candidate.item,
                )
                for candidate in candidates
            ],
        )

        for membership, candidate in zip(memberships, candidates, strict=True):
            membership.date_added = candidate.date_added

        CustomListItem.objects.bulk_update(memberships, ["date_added"])

    def _deduplicate_memberships(self, candidates):
        """Return one candidate per item, preferring newest timestamp."""
        deduplicated = {}
        for candidate in candidates:
            existing = deduplicated.get(candidate.item.id)
            if existing is None or (
                candidate.date_added,
                candidate.row_index,
            ) > (
                existing.date_added,
                existing.row_index,
            ):
                deduplicated[candidate.item.id] = candidate

        return sorted(
            deduplicated.values(),
            key=lambda candidate: (candidate.date_added, candidate.row_index),
        )

    def _parse_optional_int(self, value):
        """Parse an optional integer CSV field."""
        value = value.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _warn(self, row_index, message):
        """Append a formatted warning."""
        warning = f"Row {row_index}: {message}"
        self.warnings.append(warning)
        logger.warning(warning)
