# History tracking and TV/Season/Episode flows

This document explains how historical records are created and how TV/Season/Episode flows behave.

## Historical tables (why they exist)

The app uses `django-simple-history`. Each model with `history = HistoricalRecords(...)` gets a corresponding `Historical*` table that stores snapshots of the model on every create/update/delete. These rows include `history_date`, `history_user`, and `history_type` so the UI can show a change timeline and the statistics page can aggregate activity by day.

History entries are used in:
- `src/app/views.py` (history/timeline rendering and history deletion).
- `src/app/history_processor.py` (diffing changes into user-visible entries).
- `src/app/statistics.py` (activity heatmap/streaks from `history_date`).

## Which fields are tracked

- **Episode**
  - Model fields: `end_date`, `item`, `related_season`, `created_at`.
  - History excludes `item`, `related_season`, `created_at`, so a historical episode mainly captures `end_date` (plus history metadata).
  - Source: `src/app/models.py` (`Episode.history = HistoricalRecords(...)`).

- **Season / TV**
  - Both inherit `Media`, which defines history once.
  - History excludes: `item`, `progressed_at`, `user`, `related_tv` (season-only), and `created_at`.
  - Fields still tracked in history include: `status`, `score`, `progress`, `start_date`, `end_date`, `notes` (plus history metadata).
  - `progress`, `start_date`, and `end_date` are exposed as computed properties for seasons/TV, but the history table still stores the underlying model field values when they are updated.
  - Source: `src/app/models.py` (`Media.history = HistoricalRecords(...)`).

## Flows

### Mark season completed

Entry point: `Season.save()` when `status` changes to `Completed`.

What happens:
- The app fetches season metadata and computes the highest watched episode number already stored for that season.
- It creates missing episodes **only for episode numbers greater than the highest watched** via `bulk_create_with_history`.
- Each created episode gets an `end_date` using `user.resolve_watch_date(...)`.

Important details:
- Gaps below the highest watched episode are **not** backfilled. Example: watched E1 and E3 only, then mark season completed -> E4..end are created, but E2 is not.
- `bulk_create_with_history` does not call `Episode.save()`, so the episode save hooks that update season/TV status do not run here.
- `Season.save()` does not set the related TV show to completed when the season is marked completed.

Relevant code:
- `Season.save()` and `Season.get_remaining_eps(...)` in `src/app/models.py`.

### Complete a season, rate it, rewatch, then change rating

1) **Complete the season**
- Same flow as above: missing episodes are created from the highest watched episode onward.
- History entries are written for the season status change (`HistoricalSeason`) and for each created episode (`HistoricalEpisode`).

2) **Rate the season**
- Updating `Season.score` creates another `HistoricalSeason` row.

3) **Rewatch the season**
- Rewatching is episode-driven (`Season.watch()` or `Season.increase_progress()`), which creates new `Episode` rows for repeats.
- Each new episode creates a `HistoricalEpisode` row.
- When a watched episode is the last episode of a season, `Episode.save()` marks the season as completed (again) via `bulk_update_with_history` and can update TV status if it is the last season.
- If you rewatch a mid-season episode and the season/TV is not already in progress, `Episode.save()` will set them to `In progress`.

4) **Change the season rating again**
- Another `HistoricalSeason` row is created reflecting the score change.

Relevant code:
- `Season.watch()`, `Season.increase_progress()`, `Episode.save()` in `src/app/models.py`.

### Same flow for a TV show

1) **Mark TV completed**
- `TV.save()` on status change to `Completed` calls `_completed()`.
- `_completed()` creates or updates all seasons to `Completed` and creates missing episode rows for each season (based on highest watched episode number per season).
- Episodes are created via `bulk_create_with_history` (no `Episode.save()` hooks).

2) **Rate the TV show**
- Updating `TV.score` creates a `HistoricalTV` row.

3) **Rewatch**
- There is no TV-level watch action; rewatches are still episode-driven via seasons.
- New episode rows create `HistoricalEpisode` records and can flip season/TV status via `Episode.save()` as described above.

4) **Change rating again**
- Another `HistoricalTV` row is created for the score change.

Relevant code:
- `TV.save()`, `TV._completed()`, and `Episode.save()` in `src/app/models.py`.

## Known quirks

- Completing a season only fills episodes **after** the highest watched episode; gaps below the max remain unwatched (no backfill).
- Bulk episode creation uses `bulk_create_with_history`, so `Episode.save()` hooks do not run for those rows (no automatic season/TV status flips).
- Marking a season completed does not automatically mark the parent TV completed; TV status is updated via episode saves or explicit TV status changes.

Details:
- Bulk episode creation avoids repeated `Episode.save()` logic (which would otherwise flip season/TV status multiple times and add extra writes), but it also means any `Episode.save()` side effects are skipped.
- Season completion is scoped to the season; the TV show only changes status when the last episode of the last season is saved through the normal episode flow or when the TV is explicitly set to completed.

### Examples

1) Mark a season completed with no episodes watched\n
- Missing episodes are created in bulk with `end_date` values, but `Episode.save()` is not called.\n
- The season stays `Completed` (as set by the user action), but the TV status does not change unless it is updated separately.\n

2) Mark a season completed when some episodes are already watched\n
- Only episodes after the highest watched episode are created; gaps remain.\n
- Because bulk creation skips `Episode.save()`, the usual “last episode watched” logic does not run, so TV status still does not auto-complete.\n

3) Mark a TV completed\n
- Seasons are created/updated to `Completed`, and missing episodes are created in bulk.\n
- Again, bulk episode creation does not trigger `Episode.save()` logic, so any status changes rely on the TV/season updates done explicitly in this flow.\n

### New season appears after completion (current behavior)

- Calendar refreshes only create/update `Event` rows and `Item` rows; they do not create user `Season`/`TV` media rows or change statuses.\n
- The home “In Progress” section only shows media with `status = In progress` and explicitly excludes TV shows. A new season won’t appear there until a Season media entry exists and is set to `In progress` or an episode is watched.\n
- If a user watches an episode in the new season, `Episode.save()` sets the season to `In progress` and flips the related TV to `In progress` if needed.\n
- Changing TV status from `Completed` to `In progress` creates a `HistoricalTV` entry, but it does not create rewatch entries (those only come from new `Episode` rows).\n
