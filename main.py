import sqlite3

def init_db():
    # 连接数据库（如果文件不存在会自动创建）
    conn = sqlite3.connect('seismic_project.db')
    cursor = conn.cursor()

    # 1. 创建点位仓库表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS points_repo (
            line_id TEXT,
            point_id TEXT,
            x REAL,
            y REAL,
            status INTEGER DEFAULT 0, -- 0:未生产, 1:已生产, 2:空炮
            update_time TEXT,
            PRIMARY KEY (line_id, point_id) -- 联合主键防止重复
        )
    ''')

    # 2. 创建每日统计表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,    -- 格式: YYYY-MM-DD
            daily_prod INTEGER,       -- 当日生产数
            daily_void INTEGER,       -- 当日空炮数
            total_accum INTEGER       -- 累计完成数
        )
    ''')

    conn.commit()
    conn.close()
    print("数据库初始化成功！已准备好存储SPS数据。")

if __name__ == "__main__":
    init_db()