"""One-time migration: legacy flat tables -> normalized project/work_day/design_point/shot_attempt.

Run from the project directory:
    python3 migrate_db.py

Old tables are kept (not dropped) so rollback is trivial; drop them manually after
verifying the new tables with the checklist at the bottom of this file.
"""
import sqlite3   # sqlite3：操作 SQLite 数据库
import sys       # sys：退出程序时使用

DB_FILE = "production.db"   # 数据库文件路径
OLD_TABLES = ("design_sps_db", "daily_sps_db", "daily_obs_db")   # 旧表名集合


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None   # 查 sqlite_master 判断表是否存在


def main():
    if table_exists(sqlite3.connect(DB_FILE), "design_point"):   # 新表已存在则说明迁移过
        print("design_point table already exists — migration appears done. Aborting.")
        sys.exit(1)   # 直接退出，防止重复迁移

    conn = sqlite3.connect(DB_FILE)   # 建立数据库连接
    conn.execute("PRAGMA foreign_keys = ON")   # 开启外键约束，级联删除生效
    conn.executescript(
        """
        BEGIN;

        CREATE TABLE project (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- 主键自增
            name       TEXT NOT NULL UNIQUE,                -- 工区名，唯一
            note       TEXT,                                -- 备注
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP   -- 创建时间
        );

        CREATE TABLE work_day (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,                         -- 主键自增
            project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE, -- 所属工区，级联删除
            work_date  DATE NOT NULL,                                            -- 作业日期
            note       TEXT,                                                     -- 备注
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,                       -- 创建时间
            UNIQUE (project_id, work_date)                                       -- 同工区同天唯一
        );

        CREATE TABLE design_point (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,                         -- 主键自增
            project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE, -- 所属工区，级联删除
            line       TEXT NOT NULL,                                            -- 线号
            point      TEXT NOT NULL,                                            -- 点号
            x          REAL,                                                     -- X 坐标
            y          REAL,                                                     -- Y 坐标
            batch_src  TEXT,                                                     -- 来自哪几次导入
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,                       -- 创建时间
            UNIQUE (project_id, line, point)                                     -- 按线号点号去重
        );

        CREATE TABLE shot_attempt (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,                              -- 主键自增
            work_day_id     INTEGER NOT NULL REFERENCES work_day(id)      ON DELETE CASCADE, -- 属于哪天，级联删除
            design_point_id INTEGER NOT NULL REFERENCES design_point(id)  ON DELETE CASCADE, -- 对应设计点，级联删除
            elevation       REAL,                                                           -- 高程
            gps_time        TEXT,                                                           -- GPS 时间
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP                              -- 创建时间
        );

        CREATE INDEX idx_design_point_proj  ON design_point (project_id, line, point);   -- 按工区查设计点
        CREATE INDEX idx_design_point_lp    ON design_point (line, point);               -- 按线号点号匹配
        CREATE INDEX idx_work_day_proj_date ON work_day (project_id, work_date);         -- 按工区日期查作业日
        CREATE INDEX idx_shot_attempt_workday ON shot_attempt (work_day_id);             -- 按作业日查生产
        CREATE INDEX idx_shot_attempt_designpt ON shot_attempt (design_point_id);        -- 按设计点查生产

        -- 1. One project for all legacy data
        INSERT INTO project (id, name, note)
        VALUES (1, '工区1', 'migrated from legacy flat tables');   -- 建一个工区装全部旧数据

        -- 2. Work days (distinct production dates)
        INSERT INTO work_day (id, project_id, work_date)
        SELECT ROW_NUMBER() OVER (ORDER BY work_date), 1, work_date
        FROM (SELECT DISTINCT work_date FROM daily_sps_db ORDER BY work_date);   -- 取所有去重日期生成作业日

        -- 3. Design points deduped by (line, point), batch provenance preserved
        INSERT INTO design_point (id, project_id, line, point, x, y, batch_src)
        SELECT ROW_NUMBER() OVER (ORDER BY MIN(ds.id)), 1,
               ds.line, ds.point,
               MIN(ds.x), MIN(ds.y),
               GROUP_CONCAT(ds.batch, ',')
        FROM design_sps_db ds
        WHERE ds.batch IS NOT NULL AND ds.batch <> ''
        GROUP BY ds.line, ds.point;   -- 按线号点号去重，批次号逗号拼接

        -- 4. Shot attempts joined to work_day + design_point
        INSERT INTO shot_attempt (id, work_day_id, design_point_id, elevation, gps_time)
        SELECT ROW_NUMBER() OVER (ORDER BY d.id),
               wd.id, dp.id, d.elevation, d.gps_Time
        FROM daily_sps_db d
        JOIN work_day     wd ON wd.work_date = d.work_date
        JOIN design_point dp ON dp.line = d.line AND dp.point = d.point;   -- 生产记录关联到作业日和设计点

        COMMIT;
        """
    )

    print("Migration complete. New tables created and populated.")   # 迁移完成提示
    print("Old tables kept in place: design_sps_db, daily_sps_db, daily_obs_db.")   # 旧表保留说明


if __name__ == "__main__":
    main()
