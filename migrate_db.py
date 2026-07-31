"""One-time migration: legacy flat tables -> normalized project/work_day/design_point/shot_attempt.

Run from the project directory:
    python3 migrate_db.py

Old tables are kept (not dropped) so rollback is trivial; drop them manually after
verifying the new tables with the checklist at the bottom of this file.
"""
import sqlite3
import sys

DB_FILE = "production.db"
OLD_TABLES = ("design_sps_db", "daily_sps_db", "daily_obs_db")


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    if table_exists(sqlite3.connect(DB_FILE), "design_point"):
        print("design_point table already exists — migration appears done. Aborting.")
        sys.exit(1)

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        BEGIN;

        CREATE TABLE project (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            note       TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE work_day (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            work_date  DATE NOT NULL,
            note       TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (project_id, work_date)
        );

        CREATE TABLE design_point (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            line       TEXT NOT NULL,
            point      TEXT NOT NULL,
            x          REAL,
            y          REAL,
            batch_src  TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (project_id, line, point)
        );

        CREATE TABLE shot_attempt (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            work_day_id     INTEGER NOT NULL REFERENCES work_day(id)      ON DELETE CASCADE,
            design_point_id INTEGER NOT NULL REFERENCES design_point(id)  ON DELETE CASCADE,
            elevation       REAL,
            gps_time        TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX idx_design_point_proj  ON design_point (project_id, line, point);
        CREATE INDEX idx_design_point_lp    ON design_point (line, point);
        CREATE INDEX idx_work_day_proj_date ON work_day (project_id, work_date);
        CREATE INDEX idx_shot_attempt_workday ON shot_attempt (work_day_id);
        CREATE INDEX idx_shot_attempt_designpt ON shot_attempt (design_point_id);

        -- 1. One project for all legacy data
        INSERT INTO project (id, name, note)
        VALUES (1, '工区1', 'migrated from legacy flat tables');

        -- 2. Work days (distinct production dates)
        INSERT INTO work_day (id, project_id, work_date)
        SELECT ROW_NUMBER() OVER (ORDER BY work_date), 1, work_date
        FROM (SELECT DISTINCT work_date FROM daily_sps_db ORDER BY work_date);

        -- 3. Design points deduped by (line, point), batch provenance preserved
        INSERT INTO design_point (id, project_id, line, point, x, y, batch_src)
        SELECT ROW_NUMBER() OVER (ORDER BY MIN(ds.id)), 1,
               ds.line, ds.point,
               MIN(ds.x), MIN(ds.y),
               GROUP_CONCAT(ds.batch, ',')
        FROM design_sps_db ds
        WHERE ds.batch IS NOT NULL AND ds.batch <> ''
        GROUP BY ds.line, ds.point;

        -- 4. Shot attempts joined to work_day + design_point
        INSERT INTO shot_attempt (id, work_day_id, design_point_id, elevation, gps_time)
        SELECT ROW_NUMBER() OVER (ORDER BY d.id),
               wd.id, dp.id, d.elevation, d.gps_Time
        FROM daily_sps_db d
        JOIN work_day     wd ON wd.work_date = d.work_date
        JOIN design_point dp ON dp.line = d.line AND dp.point = d.point;

        COMMIT;
        """
    )

    print("Migration complete. New tables created and populated.")
    print("Old tables kept in place: design_sps_db, daily_sps_db, daily_obs_db.")


if __name__ == "__main__":
    main()
