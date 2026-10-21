-- Running coach database. All upserts key on natural ids so syncs are idempotent.

CREATE TABLE IF NOT EXISTS raw_cache (
    source      TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    key         TEXT NOT NULL,          -- date, activity id, or 'latest'
    fetched_at  TEXT NOT NULL,
    payload     BLOB NOT NULL,          -- zlib-compressed JSON
    PRIMARY KEY (source, endpoint, key)
);

CREATE TABLE IF NOT EXISTS activities (
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    start_local   TEXT NOT NULL,
    date          TEXT NOT NULL,
    sport         TEXT NOT NULL,
    name          TEXT,
    distance_m    REAL, duration_s REAL, moving_s REAL, elev_gain_m REAL,
    avg_hr        REAL, max_hr REAL, avg_speed REAL, cadence REAL, avg_power REAL,
    training_load REAL, aerobic_te REAL, anaerobic_te REAL, te_label TEXT,
    vo2max        REAL, calories REAL,
    zone_secs     TEXT,                 -- JSON list
    zone_floors   TEXT,                 -- JSON list
    extra         TEXT,                 -- JSON object
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS ix_activities_date ON activities(date);

CREATE TABLE IF NOT EXISTS laps (
    source TEXT NOT NULL, activity_id TEXT NOT NULL, idx INTEGER NOT NULL,
    distance_m REAL, duration_s REAL, avg_speed REAL, avg_hr REAL, max_hr REAL,
    cadence REAL, avg_power REAL, elev_gain_m REAL, intensity TEXT,
    PRIMARY KEY (source, activity_id, idx)
);

CREATE TABLE IF NOT EXISTS strength_sets (
    source TEXT NOT NULL, activity_id TEXT NOT NULL, idx INTEGER NOT NULL,
    exercise TEXT, category TEXT, reps INTEGER, weight_kg REAL, duration_s REAL,
    PRIMARY KEY (source, activity_id, idx)
);

CREATE TABLE IF NOT EXISTS activity_streams (
    source TEXT NOT NULL, activity_id TEXT NOT NULL,
    payload BLOB NOT NULL,              -- zlib JSON {t_s, hr, speed}
    PRIMARY KEY (source, activity_id)
);

CREATE TABLE IF NOT EXISTS daily_wellness (
    date TEXT PRIMARY KEY,
    sleep_s REAL, deep_s REAL, light_s REAL, rem_s REAL, awake_s REAL, sleep_score REAL,
    hrv_last_night REAL, hrv_weekly_avg REAL, hrv_status TEXT,
    hrv_baseline_low REAL, hrv_baseline_high REAL,
    rhr REAL, bb_charged REAL, bb_drained REAL, bb_high REAL, bb_low REAL,
    stress_avg REAL, readiness_score REAL, readiness_level TEXT,
    steps INTEGER, respiration_avg REAL, weight_kg REAL
);

CREATE TABLE IF NOT EXISTS fitness_daily (
    date TEXT PRIMARY KEY,
    vo2max REAL, training_status TEXT, acute_load REAL, chronic_load REAL, acwr REAL,
    lt_hr REAL, lt_speed REAL,
    race_pred_5k_s REAL, race_pred_10k_s REAL, race_pred_half_s REAL, race_pred_marathon_s REAL,
    endurance_score REAL
);

CREATE TABLE IF NOT EXISTS planned_workouts (
    source TEXT NOT NULL, source_id TEXT NOT NULL,
    date TEXT NOT NULL, title TEXT, sport TEXT, workout_id TEXT,
    steps TEXT,                         -- JSON list
    est_duration_s REAL, est_distance_m REAL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS ix_planned_date ON planned_workouts(date);

CREATE TABLE IF NOT EXISTS races (
    name TEXT NOT NULL, date TEXT NOT NULL,
    priority TEXT, distance_km REAL, goal_time TEXT, source TEXT,
    PRIMARY KEY (name, date)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                   -- ISO datetime, athlete timezone
    date TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    week TEXT PRIMARY KEY,              -- e.g. 2026-W43
    generated_at TEXT NOT NULL,
    md_path TEXT, html_path TEXT,
    metrics TEXT,                       -- JSON
    narrative TEXT                      -- JSON (LLM output) or NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
