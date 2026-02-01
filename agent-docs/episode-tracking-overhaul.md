# Episode-driven TV tracking overhaul

This document captures the agreed decisions and a proposed implementation breakdown for moving TV/Season tracking to episode-level truth.

## Decisions captured

- **Completion %** counts only aired episodes (as of their air date). As new episodes air, completion % updates.
- **Specials** do not count toward completion and should not appear on the calendar or In Progress view.
- **In Progress (home)** is TV-level. A new season with zero watched episodes should still appear if the show is engaged, with the total unwatched aired episodes remaining.
- **Rewatches** are logged as additional watch events; no per-rewatch progress UX required yet.
- **Bulk mark watched on release dates** only applies to already-aired episodes; specials are excluded at TV-level.
- **Statuses** kept at TV level only: Planned, On hold, Dropped. These statuses hide a show from calendar and In Progress but are still filterable on the TV shows page.
- **Seasons** do not use statuses. A season can be manually ignored/unignored for In Progress (similar to Specials behavior) at the user level.

## Open questions (resolve before implementation)

Resolved:
- Specials detection: `season_number == 0` OR admin-only `Item.is_specials_override` flag.
- Episode air date: missing/future air dates are excluded from totals and from bulk “watch on air date”. Manual watches are allowed but do not count toward completion until a valid air date exists.
- Admin-only `is_hidden_override` on Item. If this flag is set, the item should be treated as non-existent (hidden from UI, excluded from totals and any bulk operations). Also applies to episodes.

## Proposed data model changes

### Canonical watch history

Adopt Option B: add a watch-event table (name TBD, e.g., `EpisodeWatch`) and migrate existing `Episode` rows into it.

- `EpisodeWatch`
  - `related_season` (FK to Season; keeps user scoping via season)
  - `item` (FK to Item for episode identity)
  - `watched_at` (timestamp; current `end_date` equivalent)
  - Optional: `source` (manual, bulk-now, bulk-airdate, import)
  - Optional: `notes` or `raw_air_date` (only if needed for imports)

Indexes to consider:
- `(related_season, item)` for distinct-episode counts.
- `(related_season, watched_at)` for last-watched queries.

Existing `Episode` rows will be migrated into `EpisodeWatch`, then `Episode` usage will be deprecated in watch flows.

Proposed model definition (draft):

```python
class EpisodeWatch(models.Model):
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
    )

    class Meta:
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
```

### Optional caches (for performance)

- `SeasonProgress` (or fields on Season)
  - `episodes_watched_count`
  - `episodes_total_count` (aired, excluding specials and ignored seasons)
  - `last_watched_at`
- `TVProgress` (or fields on TV)
  - same as above, plus `first_watched_at`

Caches are derived from `EpisodeWatch` and should be recomputed on writes or via signals/jobs.

### Admin overrides for metadata cleanup

- `Season.is_ignored` (user-facing)
- `Item.is_specials_override` (admin-only)
- `Item.is_hidden_override` for items, including episodes (admin-only; treated as non-existent)

## Core behavior rules

### Progress calculations

- **Season progress**: distinct aired episodes watched / aired episodes total, excluding any episodes flagged with `is_hidden_override`
- **TV progress**: distinct aired episodes watched / aired episodes total (excluding anything with `is_hidden_override` set, any episodes in specials seasons, and any seasons marked as ignored).
- **Jumping around**: progress based on distinct episodes watched, not max episode number.

Ignored season rule:
- Ignored seasons are excluded from the denominator for TV progress and remaining counts.
- Ignored seasons are filtered per-user when rendering calendar/events and In Progress lists.

Air date rule:
- Episodes with missing/future air dates are excluded from totals and from completion counts, even if manually watched, until a valid air date exists.

Hidden item rule:
- Items (including episodes) flagged with `is_hidden_override` are excluded from totals, progress counts, calendar, and bulk watch flows, and hidden from the UI completely. Any existing watch events for hidden episodes are ignored.

Source assignment rule:
- `source="manual"` for user-initiated watches (episode tracker UI).
- `source="bulk"` for auto-created watches when bulk-completing a season/TV.
- `source="import"` for importer-created watches.
- `source="webhook"` for media server webhooks.

### In Progress (home, filters)

- Include shows that are engaged and not 100% complete.
- Exclude shows with TV status = Planned/On hold/Dropped.
- Show total remaining aired episodes (excluding specials/ignored seasons).
- If a new season appears, show should surface even at 0 watched episodes if engaged.

Engaged rule:
- Any watch ever on the show (any season has a watched episode), as long as TV status is not On hold or Dropped.

### Bulk mark completed

- **Season**: mark any remaining unwatched aired episodes watched. Support “watched now” vs “watched on air date”.
- **TV**: same as above, across all non-ignored, non-special seasons.
- Only episodes with aired dates are eligible for “watched on air date”.

### Calendar

- Exclude any show with TV status On hold/Dropped.
- Exclude specials seasons and seasons flagged as ignored.
- Exclude episodes flagged with is_hidden_override.


## Implementation chunks

1) **Spec + ruleset validation**
   - (DONE) Confirm specials identification and air date source.

2) **Schema + migrations**
   - (DONE) Add `EpisodeWatch` model (or adapt existing `Episode`).
   - (DONE) Add season ignore flag on Season (per user, user-facing).
   - (DONE) Add season specials override flag on Item (sitewide, admin-only).
   - (DONE) Add hidden override on Item (sitewide, admin-only).
   - Add optional progress cache fields/tables.
   - (DONE) Backfill watch events from existing data.

3) **Progress engine**
   - Helpers to compute aired episode totals (excluding specials, ignored seasons).
   - Helpers to compute distinct watched counts.
   - Update caches on watch event creation and on season ignore toggle.

4) **Watch flows**
   - (DONE) Single episode watch creates EpisodeWatch.
   - Bulk mark season/TV watched with “now” or “air date” options.
   - Ensure bulk operations skip specials and unaired episodes.

5) **UI and queries**
   - Update In Progress home query to TV-level.
   - Update TV shows filtering: Completed (100%), In progress (started but <100%).
      - This applies across any pages with status filters such as the /medialist/tv page and lists.
   - Ensure calendar hides specials + ignored seasons + On hold/Dropped.

6) **History/statistics alignment**
   - Decide which models should still use django-simple-history.
   - Update timeline/statistics to use watch events rather than Season/TV status changes.
   - Update `agent-docs/history-tracking.md` for the new flows.

7) **Backfill + maintenance tasks**
   - (DONE) One-time job to backfill watch events (if new table).
   - One-time job to backfill progress caches (if stored).
   - Optional management command to recalc progress and verify counts.

## Progress log (implemented)

- Added EpisodeWatch model and schema/data migrations: `src/app/migrations/0054_add_episodewatch_and_overrides.py`, `src/app/migrations/0055_backfill_episodewatch.py`.
- Added admin-only overrides and user-facing ignore fields in `src/app/models.py`.
- Moved `is_specials_override` to Item and updated calendar/progress logic to respect it.
- Added season ignore toggle endpoint and UI on season details page.
- Switched watch creation and progress/date calculations to EpisodeWatch in `src/app/models.py`.
- Updated watch form + episode save flow in `src/app/forms.py` and `src/app/views.py`.
- Updated episode history display to use `watched_at` in `src/templates/app/components/fill_track_episode.html` and `src/templates/app/media_details.html`.
- Updated statistics queries to use EpisodeWatch in `src/app/statistics.py`.
- Registered EpisodeWatch in admin and excluded it from auto-registering with MediaAdmin in `src/app/admin.py`.
- Updated history modal to render EpisodeWatch events and delete EpisodeWatch records via `src/app/views.py` and `src/templates/app/components/fill_history.html`.
- Updated import/export/webhook paths to use EpisodeWatch, and adjusted related integration tests in `src/integrations/`.
- Removed legacy Episode model/admin and added migration to drop Episode and HistoricalEpisode tables.
- Updated export CSV schema to use `watched_at` and `watch_source` for EpisodeWatch, plus Yamtrack fixtures and importer alignment in `src/integrations/exports.py` and `src/integrations/imports/yamtrack.py`.
- Updated history deletion to support EpisodeWatch and updated EpisodeWatch admin display (user + source) and help text in `src/app/views.py`, `src/app/admin.py`, `src/app/models.py`.
- Migrated Episode-based tests to EpisodeWatch across models/views/providers in `src/app/tests/`.

## Backfill plan (if introducing EpisodeWatch) (DONE)

1) Schema migration:
   - Create `EpisodeWatch` model + indexes.
   - Add admin-only overrides: `Item.is_specials_override`, `Item.is_hidden_override`.
   - Add user-facing `Season.is_ignored`.
2) Data migration:
   - For each existing `Episode`, create `EpisodeWatch` with:
     - `related_season = episode.related_season`
     - `item = episode.item`
     - `watched_at = episode.end_date`
     - `source = "legacy"`
   - Skip rows whose `item` is flagged `is_hidden_override`.
3) Code migration:
   - Update all read/write paths to use EpisodeWatch.
   - Remove side effects in `Episode.save()` and disable new writes to `Episode`.
4) Stabilization:
   - Keep `Episode` table for at least one release to allow rollback.
   - Optional cleanup migration to drop `Episode` once stable.

Migration details to implement:
- `000X_create_episodewatch.py` (schema)
- `000Y_backfill_episodewatch.py` (data)
- `000Z_deprecate_episode.py` (optional cleanup later)

## Affected code paths (Episode -> EpisodeWatch)

Core app:
- `src/app/models.py` (Season.watch/increase_progress, Episode.save side effects, progress properties)
- `src/app/views.py` (episode watch endpoints, bulk watch flows)
- `src/app/statistics.py` (uses `Episode` for activity/streaks)
- `src/app/history_processor.py` (Episode history entries)
- `src/events/` (calendar sources for TV episodes)

Imports/exports:
- `src/integrations/imports/trakt.py`, `src/integrations/imports/simkl.py`
- `src/integrations/imports/yamtrack.py` + `src/integrations/imports/helpers.py`
- `src/integrations/exports.py` (episode rows)
- `src/users/views.py` import/export pages (wiring)

Webhooks:
- `src/integrations/webhooks/base.py` (episode played events)
- `src/integrations/webhooks/plex.py`, `src/integrations/webhooks/jellyfin.py`, `src/integrations/webhooks/emby.py`

## Suggested acceptance tests (high value)

- Watch E1 and E11 of a 12-ep season -> 2/12 and TV total updates.
- Bulk mark season completed -> all aired episodes become watched, including gaps below max watched.
- New season starts -> TV appears on In Progress with remaining aired count.
- TV status = Dropped -> hidden from In Progress and calendar.
- Specials season -> excluded from progress, calendar, In Progress; still can bulk-mark at season level.
