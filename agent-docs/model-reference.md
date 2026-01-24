# Model Reference (Items, MediaTypes, Episodes)

This doc summarizes how media identity, user state, and episode watches map to
models and database tables.

## Item (media identity)

- Model: `Item` in `src/app/models.py`
- Table: `app_item` (default Django naming)
- Purpose: canonical media identity and metadata per source.
- Key fields: `media_id`, `source`, `media_type`, `title`, `image`,
  `season_number`, `episode_number`, `is_hidden_override`.
- Episodes are stored as `Item` rows with:
  - `media_type="episode"`
  - `season_number` + `episode_number` set

## MediaTypes (type routing)

- Enum: `MediaTypes` in `src/app/models.py`
- Stored in: `Item.media_type` (string values like `tv`, `season`, `episode`).
- Used to route provider lookups, UI behavior, and import/export formats.
- `EpisodeWatch` is not a `MediaType`; it is a separate watch-event model.

## Media (user state)

- Abstract base: `Media` in `src/app/models.py`
- Tables: subclasses such as `app_tv`, `app_season`, `app_movie`, `app_anime`
- Purpose: user-specific state (status, score, progress, start/end dates).
- Each row points to an `Item` (identity) and a `user`.

## EpisodeWatch (watch events)

- Model: `EpisodeWatch` in `src/app/models.py`
- Table: `app_episodewatch`
- Purpose: per-watch events for episode items (supports rewatches).
- Key fields: `item` (episode `Item`), `related_season`, `watched_at`, `source`.
- Progress/stats are computed from EpisodeWatch rows + episode Items.

## Episode vs EpisodeWatch

- **Episode**: an episode *identity* stored as an `Item` with
  `media_type="episode"`.
- **EpisodeWatch**: a user *event* representing a watch of that episode.
- Legacy `Episode` and `HistoricalEpisode` models were removed in
  `src/app/migrations/0057_delete_episode_models.py`.
