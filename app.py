import streamlit as st
import pandas as pd
import sqlite3
import Tool
import os
import re
import datetime

os.system('cls' if os.name == 'nt' else 'clear')

DB_FILE = "production.db"
# --- 1️⃣ 初始化数据库 ---
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.execute("PRAGMA foreign_keys = ON")
c = conn.cursor()
c.executescript("""
CREATE TABLE IF NOT EXISTS project (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    note       TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_day (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    work_date  DATE NOT NULL,
    note       TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, work_date)
);

CREATE TABLE IF NOT EXISTS design_point (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    line       TEXT NOT NULL,
    point      TEXT NOT NULL,
    x          REAL,
    y          REAL,
    batch_src  TEXT,
    swath      TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, line, point)
);

CREATE TABLE IF NOT EXISTS shot_attempt (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    work_day_id     INTEGER NOT NULL REFERENCES work_day(id)      ON DELETE CASCADE,
    design_point_id INTEGER NOT NULL REFERENCES design_point(id)  ON DELETE CASCADE,
    elevation       REAL,
    gps_time        TEXT,
    swath           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_design_point_proj  ON design_point (project_id, line, point);
CREATE INDEX IF NOT EXISTS idx_design_point_lp    ON design_point (line, point);
CREATE INDEX IF NOT EXISTS idx_work_day_proj_date ON work_day (project_id, work_date);
CREATE INDEX IF NOT EXISTS idx_shot_attempt_workday ON shot_attempt (work_day_id);
CREATE INDEX IF NOT EXISTS idx_shot_attempt_designpt ON shot_attempt (design_point_id);
""")
conn.commit()
# --- 1. 页面基本配置 (UI设置) ---
st.set_page_config(page_title="Crew S90", layout="wide")
st.title("设计 SPS 与 生产 SPS 查看")

tab1, tab2 = st.tabs(["S90 生产进度", " S90 助手"])
with tab1:
    with st.sidebar:
        st.header("数据导入中心")
        # 获取现有工区列表
        try:
            project_df = pd.read_sql("SELECT id, name FROM project ORDER BY name", conn)
            project_list = project_df["name"].dropna().tolist()
        except:
            project_list = []
        project_options = ["➕ 新建工区"] + project_list
        selected_option = st.selectbox("选择工区", project_options)

        # 如果选择新建，则显示输入框
        if selected_option == "➕ 新建工区":
            target_project_name = st.text_input("请输入工区名称 (如: 工区1)", "工区1")
        else:
            target_project_name = selected_option
        sp_files = st.file_uploader("1. 上传to_recorder (SPS)", type=['sps', 's', 'S01'], accept_multiple_files=True)
        sps_file = st.file_uploader("2. 上传生产obs (SPS)", type=['sps', 's', 'S01', 'csv'])
        target_date = st.date_input("选择作业日期", datetime.date.today())
        daily_sps_file = st.file_uploader("2. 上传生产daily SPS", type=['sps', 's'], accept_multiple_files=True)
        st.markdown("---")
        save_btn = st.button("💾 确认入库")

    col_chart, col_stats = st.columns([3, 1])
    df_sp = pd.DataFrame()
    df_sps = pd.DataFrame()
    daily_sps = pd.DataFrame()

    def resolve_project_id(name):
        """获取或创建工区，返回 (project_id, created)"""
        row = c.execute("SELECT id FROM project WHERE name = ?", (name,)).fetchone()
        if row:
            return row[0], False
        c.execute("INSERT INTO project (name) VALUES (?)", (name,))
        conn.commit()
        return c.lastrowid, True

    def extract_swath(filename):
        """从文件名提取 swath 号，如 'sw123_sps_for_recorder.sps' -> 'sw123'"""
        if not filename:
            return None
        name = filename.split('/')[-1]  # 去路径
        m = re.search(r'(sw\d+)', name.lower())
        return m.group(1) if m else None

    if sp_files:
        # 读取 设计SPS（支持多文件，逐文件解析后合并）
        @st.cache_data
        def smart_read_csv(uploaded_file, column_names=None):
            # 1. 先将文件内容读取为字符串列表，寻找起始行
            lines = uploaded_file.getvalue().decode("utf-8").splitlines()

            start_line = 0
            for i, line in enumerate(lines):
                if line.strip().startswith('S'):  # 寻找第一个以 S 开头的行
                    start_line = i
                    break

            # 2. 重新定位并从该行开始读取
            uploaded_file.seek(0)  # 重置文件指针
            df = pd.read_csv(uploaded_file, skiprows=start_line, names=column_names, sep=r'\s+',  # 匹配空格
                             header=None,  # 无表头
                             engine='python')
            return df

        custom_columns = ['S', 'Line', 'Point', 'X', 'Y']
        parts = []
        for f in sp_files:
            df_f = smart_read_csv(f, custom_columns)
            df_f['Swath'] = extract_swath(f.name)
            parts.append(df_f)
        df_sp = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    if sps_file:
        # 读取每日obs
        @st.cache_data
        def smart_read_spscsv(uploaded_file):
            df = pd.read_csv(uploaded_file, sep=',',
                             header=0,
                             engine='python')
            if 'Is Raw' in df.columns:
                # 筛选 False
                df_ = df[df['Is Raw'] == False].copy()
            else:
                st.error("数据中找不到 'Is Raw' 列，请检查 CSV 表头！")
                df_ = df.copy()
            df_['Easting (m)'] = pd.to_numeric(df_['Easting (m)'], errors='coerce')
            df_['Northing (m)'] = pd.to_numeric(df_['Northing (m)'], errors='coerce')
            # 如果实际数据也需要连线，建议也排序
            df_ = df_.sort_values(by=['Easting (m)', 'Northing (m)'])
            return df_
        df_sps = smart_read_spscsv(sps_file)

    if daily_sps_file:
        @st.cache_data
        def smart_read_dailysps(uploaded_file):
            df_ = []
            for file in uploaded_file:
                df = pd.read_csv(file, sep=r'\s+',  # 匹配空格
                                 header=None,  # 无表头
                                 names=['S', 'Line', 'Point', 'index', 'X', 'Y', 'Elevation', 'GPS Time'],
                                 engine='python')
                df['Swath'] = extract_swath(file.name)
                df_.append(df)
            if df_:
                return pd.concat(df_, ignore_index=True)
            return pd.DataFrame()
        daily_sps = smart_read_dailysps(daily_sps_file)

    # 数据确定入库
    if save_btn:
        project_id, _ = resolve_project_id(target_project_name)
        target_batch = f"batch_{project_id}"

        if df_sp is not None and not df_sp.empty:
            try:
                # 幂等导入：已存在的 (line,point) 更新坐标并追加批次标签，新点直接插入
                params = [(project_id, row['Line'], row['Point'], row['X'], row['Y'],
                           target_batch, row.get('Swath')) for _, row in df_sp.iterrows()]
                c.executemany("""
                              INSERT INTO design_point (project_id, line, point, x, y, batch_src, swath)
                              VALUES (?, ?, ?, ?, ?, ?, ?)
                              ON CONFLICT (project_id, line, point) DO UPDATE SET
                                  x = excluded.x,
                                  y = excluded.y,
                                  swath = COALESCE(excluded.swath, design_point.swath),
                                  batch_src = CASE
                                      WHEN design_point.batch_src IS NULL OR design_point.batch_src = '' THEN excluded.batch_src
                                      WHEN instr(',' || design_point.batch_src || ',', ',' || excluded.batch_src || ',') > 0 THEN design_point.batch_src
                                      ELSE design_point.batch_src || ',' || excluded.batch_src
                                  END
                              """, params)
                conn.commit()
                st.sidebar.success(f"上传并存储 {len(df_sp)} 个设计点")
            except sqlite3.Error as e:
                st.sidebar.error(f"设计sps数据入库错误: {e}")

        if df_sps is not None and not df_sps.empty:
            st.sidebar.warning("obs 数据不再单独入库：请使用 daily SPS 上传生产数据")

        if daily_sps is not None and not daily_sps.empty:
            try:
                # 覆盖式导入：先删该工区该日期的生产记录，再入库
                c.execute("""
                          DELETE FROM shot_attempt
                          WHERE work_day_id IN (
                              SELECT id FROM work_day WHERE project_id = ? AND work_date = ?
                          )
                          """, (project_id, target_date))
                # 确保 work_day 存在
                c.execute("INSERT OR IGNORE INTO work_day (project_id, work_date) VALUES (?, ?)",
                          (project_id, target_date))
                conn.commit()
                wd_id = c.execute("SELECT id FROM work_day WHERE project_id = ? AND work_date = ?",
                                  (project_id, target_date)).fetchone()[0]

                rows = []
                skip_swath = 0
                for _, row in daily_sps.iterrows():
                    if row.get('Swath'):
                        dp = c.execute("""
                                       SELECT id FROM design_point
                                       WHERE project_id = ? AND line = ? AND point = ? AND swath = ?
                                       """, (project_id, row['Line'], row['Point'], row['Swath'])).fetchone()
                        if dp is None:
                            # 退而求其次：该工区确实有这个设计点，只是 swath 标注不一致
                            alt = c.execute("""
                                            SELECT id FROM design_point
                                            WHERE project_id = ? AND line = ? AND point = ?
                                            """, (project_id, row['Line'], row['Point'])).fetchone()
                            if alt is not None:
                                skip_swath += 1
                        dp = dp or alt
                    else:
                        dp = c.execute("""
                                       SELECT id FROM design_point
                                       WHERE project_id = ? AND line = ? AND point = ?
                                       """, (project_id, row['Line'], row['Point'])).fetchone()
                    if dp is None:
                        continue
                    rows.append((wd_id, dp[0], row['Elevation'], row['GPS Time'], row.get('Swath')))

                if rows:
                    c.executemany("""
                                  INSERT INTO shot_attempt (work_day_id, design_point_id, elevation, gps_time, swath)
                                  VALUES (?, ?, ?, ?, ?)
                                  """, rows)
                    conn.commit()
                    msg = f"✅ {target_date} 的数据已入库！匹配 {len(rows)} 个设计点"
                    if skip_swath:
                        msg += f"（{skip_swath} 炮 swath 标注不一致，已按设计点归属）"
                    st.sidebar.success(msg)
                else:
                    st.sidebar.warning("没有匹配到设计点的生产记录")
            except Exception as e:
                st.sidebar.error(f"daily sps入库失败: {e}")

    # 画图
    with col_chart:
        quick_mode = st.checkbox("⚡ 快速模式（降采样，点太多时推荐）", value=False)
        subcol_1, subcol_2, subcol_3, subcol_4 = st.columns([1, 1, 1, 1])
        with subcol_1:
            plot_project_options = project_list if project_list else ["工区1"]
            selected_project = st.selectbox("绘图工区", plot_project_options)
        with subcol_2:
            selected_pro1 = st.date_input("开始日期")
        with subcol_3:
            selected_pro2 = st.date_input("结束日期")

        # 该工区已有的 swath 列表（供筛选）
        try:
            swath_df = pd.read_sql("""
                                   SELECT DISTINCT dp.swath FROM design_point dp
                                   JOIN project p ON p.id = dp.project_id
                                   WHERE p.name = ? AND dp.swath IS NOT NULL AND dp.swath <> ''
                                   ORDER BY dp.swath
                                   """, conn, params=(selected_project,))
            swath_list = swath_df["swath"].tolist()
        except:
            swath_list = []
        selected_swaths = st.multiselect("按 swath 筛选（留空 = 全部）", swath_list)

        if subcol_4.button("Plot"):

            @st.cache_data
            def load_all_designs(project_name, swaths=()):
                if not project_name:
                    return pd.DataFrame()
                if swaths:
                    placeholders = ', '.join(['?'] * len(swaths))
                    query = f"""
                            SELECT dp.line  as Line,
                                   dp.point as Point,
                                   dp.x     as X,
                                   dp.y     as Y
                            FROM design_point dp
                            JOIN project p ON p.id = dp.project_id
                            WHERE p.name = ? AND dp.swath IN ({placeholders})
                            ORDER BY dp.line, dp.point
                            """
                    return pd.read_sql(query, conn, params=[project_name] + list(swaths))
                query = """
                        SELECT dp.line  as Line,
                               dp.point as Point,
                               dp.x     as X,
                               dp.y     as Y
                        FROM design_point dp
                        JOIN project p ON p.id = dp.project_id
                        WHERE p.name = ?
                        ORDER BY dp.line, dp.point
                        """
                return pd.read_sql(query, conn, params=(project_name,))

            df_design = load_all_designs(selected_project, tuple(selected_swaths))

            @st.cache_data
            def load_daily_sps(project_name, start_date, end_date, swaths=()):
                if swaths:
                    placeholders = ', '.join(['?'] * len(swaths))
                    query = f"""
                            SELECT dp.line      as Line,
                                   dp.point     as Point,
                                   dp.x         as X,
                                   dp.y         as Y,
                                   sa.elevation as Elevation,
                                   wd.work_date
                            FROM shot_attempt sa
                            JOIN design_point dp ON dp.id = sa.design_point_id
                            JOIN work_day wd ON wd.id = sa.work_day_id
                            JOIN project p ON p.id = wd.project_id
                            WHERE p.name = ? AND wd.work_date BETWEEN ? AND ?
                              AND dp.swath IN ({placeholders})
                            ORDER BY wd.work_date, dp.line, dp.point
                            """
                    return pd.read_sql(query, conn,
                                      params=[project_name, start_date, end_date] + list(swaths))
                query = """
                        SELECT dp.line      as Line,
                               dp.point     as Point,
                               dp.x         as X,
                               dp.y         as Y,
                               sa.elevation as Elevation,
                               wd.work_date
                        FROM shot_attempt sa
                        JOIN design_point dp ON dp.id = sa.design_point_id
                        JOIN work_day wd ON wd.id = sa.work_day_id
                        JOIN project p ON p.id = wd.project_id
                        WHERE p.name = ? AND wd.work_date BETWEEN ? AND ?
                        ORDER BY wd.work_date, dp.line, dp.point
                        """
                return pd.read_sql(
                    query,
                    conn,
                    params=(project_name, start_date, end_date)
                )
            df_daily = load_daily_sps(selected_project, selected_pro1, selected_pro2, tuple(selected_swaths))

            fig = Tool.get_base_figure()
            max_shown = 20_000 if quick_mode else None
            if 'df_design' in locals() and not df_design.empty:
                fig = Tool.add_design_trace(fig, df_design, max_shown=max_shown)
            if 'df_daily' in locals() and not df_daily.empty:
                fig = Tool.add_production_sps(fig, df_daily, max_shown=max_shown)
            if 'df_sps' in locals() and not df_sps.empty:
                fig = Tool.add_production_trace(fig, df_sps, max_shown=max_shown)
            st.plotly_chart(fig)

            st.session_state.df_design = load_all_designs(selected_project, tuple(selected_swaths))
            st.session_state.df_daily = load_daily_sps(selected_project, selected_pro1, selected_pro2, tuple(selected_swaths))

    with col_stats:
        st.markdown("### 📊 生产统计")
        if "df_design" in st.session_state and "df_daily" in st.session_state:
            df_design = st.session_state.df_design
            df_daily = st.session_state.df_daily

            if not df_design.empty and not df_daily.empty:
                total_sp = len(df_design)
                total_sps = len(df_daily)
                progress_val = (total_sps / total_sp) if total_sp > 0 else 0

                # 1. 饼图展示
                pie_fig = Tool.plot_progress_pie(total_sps, total_sp)
                st.plotly_chart(pie_fig)

                # 2. 核心指标卡片
                st.metric("设计总数", f"{total_sp}")
                st.metric("实际完成", f"{total_sps}", delta=f"{total_sps}")

                # 如果有线号列，增加线号统计
                if 'Line' in df_daily.columns:
                    st.write(f"**涉及线数:** {df_daily['Line'].nunique()}")
            else:
                st.write('No production')

    col_1, col_2, col_3 = st.columns([1, 1, 1])
    st.divider()
    with col_1:
        st.subheader("💾 库内工区管理")
        try:
            db_summary = pd.read_sql("""
                                     SELECT p.id, p.name, COUNT(dp.id) as count
                                     FROM project p
                                     LEFT JOIN design_point dp ON dp.project_id = p.id
                                     GROUP BY p.id
                                     ORDER BY p.name
                                     """, conn)

            if not db_summary.empty:
                with st.container(height=400, border=True):
                    for _, row in db_summary.iterrows():
                        col_d, col_b = st.columns([2, 1])
                        col_d.write(f"📅 {row['name']} ({row['count']} 炮)")
                        if col_b.button("删除", key=f"btn_p_{row['id']}"):
                            c.execute("DELETE FROM project WHERE id = ?", (row['id'],))
                            conn.commit()
                            st.toast(f"已删除 {row['name']} 的数据")
                            st.rerun()
            else:
                st.write("数据库尚无记录")
        except:
            st.write("等待初始化...")
    with col_2:
        st.subheader("💾 库内数据管理")

    with col_3:
        st.subheader("💾 库内daily sps 管理")
        # 查询数据库中已有的日期和炮数
        try:
            db_summary = pd.read_sql("""
                                     SELECT wd.id, wd.work_date, COUNT(sa.id) as count
                                     FROM work_day wd
                                     LEFT JOIN shot_attempt sa ON sa.work_day_id = wd.id
                                     GROUP BY wd.id
                                     ORDER BY wd.work_date DESC
                                     """, conn)

            if not db_summary.empty:
                with st.container(height=400, border=True):
                    for _, row in db_summary.iterrows():
                        col_d, col_b = st.columns([2, 1])
                        col_d.write(f"📅 {row['work_date']} ({row['count']} 炮)")
                        if col_b.button("删除", key=f"btn_wd_{row['id']}"):
                            c.execute("DELETE FROM work_day WHERE id = ?", (row['id'],))
                            conn.commit()
                            st.toast(f"已删除 {row['work_date']} 的数据")
                            st.rerun()
            else:
                st.write("数据库尚无记录")
        except:
            st.write("等待初始化...")
