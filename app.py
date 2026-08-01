import streamlit as st      # Streamlit：Web 界面框架，负责页面渲染和组件
import pandas as pd         # pandas：表格数据处理，读 SPS 文件、统计查询结果
import sqlite3              # sqlite3：连接 SQLite 数据库，执行 SQL
import Tool                 # 本地绘图工具模块，封装 Plotly 图表函数
import dbtool               # 本地 agent 工具，流式问答
import requests             # requests：直接调 /models 接口拉取模型列表
import os                   # os：执行清屏命令
import re                   # re：正则表达式，从文件名提取 swath 号
import datetime             # datetime：提供日期输入框的默认值

os.system('cls' if os.name == 'nt' else 'clear')  # 启动时清屏（Windows 用 cls，mac/Linux 用 clear）

DB_FILE = "production.db"   # 数据库文件路径，全项目统一使用
# --- 1️⃣ 初始化数据库 ---
conn = sqlite3.connect(DB_FILE, check_same_thread=False)  # 建立数据库连接，允许 Streamlit 多线程复用
conn.execute("PRAGMA foreign_keys = ON")                  # 开启外键约束，删工区时级联删除子表数据
c = conn.cursor()                                         # 创建游标，后续用它执行 SQL 语句
c.executescript("""
CREATE TABLE IF NOT EXISTS project (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- 主键自增
    name       TEXT NOT NULL UNIQUE,                -- 工区名，唯一约束（不允许重名）
    note       TEXT,                                -- 备注
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP   -- 创建时间
);

CREATE TABLE IF NOT EXISTS work_day (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,                         -- 主键自增
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE, -- 所属工区，删工区时级联删除
    work_date  DATE NOT NULL,                                            -- 作业日期
    note       TEXT,                                                     -- 备注
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,                       -- 创建时间
    UNIQUE (project_id, work_date)                                       -- 同一工区同一天只能有一行
);

CREATE TABLE IF NOT EXISTS design_point (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,                         -- 主键自增
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE, -- 所属工区，级联删除
    line       TEXT NOT NULL,                                            -- 线号
    point      TEXT NOT NULL,                                            -- 点号
    x          REAL,                                                     -- X 坐标（Easting）
    y          REAL,                                                     -- Y 坐标（Northing）
    batch_src  TEXT,                                                     -- 来自哪几次导入（逗号拼接）
    swath      TEXT,                                                     -- 束号，从文件名提取
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,                       -- 创建时间
    UNIQUE (project_id, line, point)                                     -- 同一工区按线号+点号去重
);

CREATE TABLE IF NOT EXISTS shot_attempt (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,                              -- 主键自增
    work_day_id     INTEGER NOT NULL REFERENCES work_day(id)      ON DELETE CASCADE, -- 属于哪天，删日期时级联删除
    design_point_id INTEGER NOT NULL REFERENCES design_point(id)  ON DELETE CASCADE, -- 对应的设计点，级联删除
    elevation       REAL,                                                           -- 高程
    gps_time        TEXT,                                                           -- GPS 时间
    swath           TEXT,                                                           -- 束号（冗余存储便于查询）
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP                              -- 创建时间
);

CREATE INDEX IF NOT EXISTS idx_design_point_proj  ON design_point (project_id, line, point);  -- 按工区查设计点用
CREATE INDEX IF NOT EXISTS idx_design_point_lp    ON design_point (line, point);              -- 按线号点号匹配用
CREATE INDEX IF NOT EXISTS idx_work_day_proj_date ON work_day (project_id, work_date);        -- 按工区日期查作业日用
CREATE INDEX IF NOT EXISTS idx_shot_attempt_workday ON shot_attempt (work_day_id);            -- 按作业日查生产记录用
CREATE INDEX IF NOT EXISTS idx_shot_attempt_designpt ON shot_attempt (design_point_id);       -- 按设计点查生产记录用

CREATE TABLE IF NOT EXISTS agent_session (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- 主键自增
    name       TEXT NOT NULL UNIQUE,                -- 会话显示名（唯一，如 "会话 20260801-1430"）
    thread_id  TEXT NOT NULL UNIQUE,                -- 对应 LangGraph 记忆的 thread_id
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP   -- 创建时间
);
""")
conn.commit()  # 提交建表事务，表结构立即生效
# --- 1. 页面基本配置 (UI设置) ---
st.set_page_config(page_title="Crew S90", layout="wide")  # 页面标题设为"Crew S90"，布局为宽屏
st.title("设计 SPS 与 生产 SPS 查看")                       # 页面顶部大标题

tab1, tab2 = st.tabs(["S90 生产进度", " S90 助手"])  # 分两个标签页：生产进度看板 + LLM 助手
with tab1:
    with st.sidebar:
        st.header("数据导入中心")
        # 获取现有工区列表
        try:
            project_df = pd.read_sql("SELECT id, name FROM project ORDER BY name", conn)  # 查所有工区
            project_list = project_df["name"].dropna().tolist()                          # 取工区名列表
        except:
            project_list = []
        project_options = ["➕ 新建工区"] + project_list        # 下拉选项 = 新建 + 已有工区
        selected_option = st.selectbox("选择工区", project_options)  # 工区下拉框

        # 如果选择新建，则显示输入框
        if selected_option == "➕ 新建工区":                     # 选了"新建工区"
            target_project_name = st.text_input("请输入工区名称 (如: 工区1)", "工区1")  # 弹出名称输入框
        else:
            target_project_name = selected_option              # 否则直接用选中的工区名
        sp_files = st.file_uploader("1. 上传to_recorder (SPS)", type=['sps', 's', 'S01'], accept_multiple_files=True)  # 设计 SPS：支持多文件
        # daily SPS 日期从文件名自动提取（格式 sw<线束号>-<mmdd>.sps，如 sw123-0731.sps）
        target_year = st.selectbox("作业年份", list(range(2020, 2031)), index=datetime.date.today().year - 2020)  # 作业年份下拉
        daily_sps_file = st.file_uploader("2. 上传生产daily SPS", type=['sps', 's'], accept_multiple_files=True)  # daily SPS：支持多文件
        # 日期逻辑：每个 daily 文件的日期在读取阶段已从文件名提取（见下方 file_date_map）
        st.markdown("---")   # 分隔线
        save_btn = st.button("💾 确认入库")  # 确认入库按钮，点下才写数据库

    col_chart, col_stats = st.columns([3, 1])
    df_sp = pd.DataFrame()
    daily_sps = pd.DataFrame()

    def resolve_project_id(name):
        """获取或创建工区，返回 (project_id, created)"""
        row = c.execute("SELECT id FROM project WHERE name = ?", (name,)).fetchone()  # 按名字查工区
        if row:
            return row[0], False   # 已存在：返回 id，created 标记为 False
        c.execute("INSERT INTO project (name) VALUES (?)", (name,))  # 不存在则插入新工区
        conn.commit()              # 提交插入
        return c.lastrowid, True   # 返回新工区 id，created 标记为 True

    def extract_swath(filename):
        """从文件名提取 swath 号，如 'sw123_sps_for_recorder.sps' -> 'sw123'"""
        if not filename:
            return None
        name = filename.split('/')[-1]  # 去路径，只留文件名部分
        m = re.search(r'(sw\d+)', name.lower())  # 匹配 "sw" + 数字，如 sw123
        return m.group(1) if m else None   # 有匹配返回 swath 号，否则返回 None

    if sp_files:
        # 读取 设计SPS（支持多文件，逐文件解析后合并）
        file_list = sp_files if isinstance(sp_files, list) else [sp_files]   # Streamlit 传1个文件时返回单对象不是列表

        def read_sps_file(f, column_names):
            """读取单个 SPS 文件为 DataFrame"""
            lines = f.read().decode("utf-8").splitlines()     # 解码为字符串列表

            start_line = 0
            for i, line in enumerate(lines):                  # 逐行找表头起始位置
                if line.strip().startswith('S'):              # 寻找第一个以 S 开头的行
                    start_line = i                            # 记下这一行的索引
                    break

            f.seek(0)                                         # 重置指针，让 pandas 重新读
            df = pd.read_csv(f, skiprows=start_line, names=column_names, sep=r'\s+',
                             header=None, engine='python')    # 按空格分隔解析
            return df

        custom_columns = ['S', 'Line', 'Point', 'X', 'Y']     # 设计 SPS 的列名
        parts = []                                            # 暂存每个文件的 DataFrame
        for f in file_list:                                    # 遍历每个设计 SPS 文件
            df_f = read_sps_file(f, custom_columns)           # 解析单个文件
            df_f['Swath'] = extract_swath(f.name)             # 从文件名提取 swath 号填到每行
            parts.append(df_f)                                # 收进列表
        df_sp = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()  # 合并所有文件为一个表

    if daily_sps_file:
        file_list_daily = daily_sps_file if isinstance(daily_sps_file, list) else [daily_sps_file]   # Streamlit 传1个文件时返回单对象不是列表

        def read_daily_file(f):
            """读取单个 daily SPS 文件为 DataFrame"""
            df = pd.read_csv(f, sep=r'\s+',                   # 按空格分隔解析
                             header=None,                     # 无表头
                             names=['S', 'Line', 'Point', 'index', 'X', 'Y', 'Elevation', 'GPS Time'],
                             engine='python')
            df['Swath'] = extract_swath(f.name)               # 从文件名提取 swath 号
            return df

        daily_parts = []             # 暂存每个 daily 文件的 DataFrame
        file_date_map = {}           # 文件名 -> 日期：记录每个文件对应的日期
        for f in file_list_daily:    # 遍历每个 daily 文件
            df_f = read_daily_file(f)            # 解析单个 daily 文件
            df_f['_src_file'] = f.name           # 标记数据来源文件名，入库时按文件分组
            daily_parts.append(df_f)             # 收进列表
            m = re.search(r'[-_](\d{2})(\d{2})\.\w+$', f.name)  # 从文件名提取 mmdd
            if m:
                mm = int(m.group(1))             # 月
                dd = int(m.group(2))             # 日
                try:
                    file_date_map[f.name] = datetime.date(target_year, mm, dd)  # 组装日期，用下拉选的年份
                except ValueError:
                    file_date_map[f.name] = None  # 日期不合法记为 None
            else:
                file_date_map[f.name] = None      # 匹配不到记为 None
        daily_sps = pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()  # 合并所有 daily 文件
        # 侧边栏展示识别到的日期
        for fn, dt in file_date_map.items():
            if dt:
                st.caption(f"📅 {fn} → {dt}")     # 提示每个文件的识别日期
            else:
                st.warning(f"⚠ {fn} 未识别到日期，将跳过")  # 提示未识别的文件

    # 数据确定入库
    if save_btn:
        project_id, _ = resolve_project_id(target_project_name)  # 解析工区 id
        target_batch = f"batch_{project_id}"                     # 本次导入的批次标签 = batch_工区id

        if df_sp is not None and not df_sp.empty:
            try:
                # 幂等导入：已存在的 (line,point) 更新坐标并追加批次标签，新点直接插入
                params = [(project_id, row['Line'], row['Point'], row['X'], row['Y'],
                           target_batch, row.get('Swath')) for _, row in df_sp.iterrows()]  # 每行转成元组
                c.executemany("""
                              INSERT INTO design_point (project_id, line, point, x, y, batch_src, swath)
                              VALUES (?, ?, ?, ?, ?, ?, ?)
                              ON CONFLICT (project_id, line, point) DO UPDATE SET
                                  x = excluded.x,                                            -- 冲突时更新坐标
                                  y = excluded.y,
                                  swath = COALESCE(excluded.swath, design_point.swath),      -- 已有 swath 不覆盖
                                  batch_src = CASE                                           -- 追加批次标签
                                      WHEN design_point.batch_src IS NULL OR design_point.batch_src = '' THEN excluded.batch_src   -- 原为空直接用
                                      WHEN instr(',' || design_point.batch_src || ',', ',' || excluded.batch_src || ',') > 0 THEN design_point.batch_src  -- 已含该批次则不动
                                      ELSE design_point.batch_src || ',' || excluded.batch_src   -- 否则逗号追加
                                  END
                              """, params)
                conn.commit()  # 提交设计点写入
                st.sidebar.success(f"上传并存储 {len(df_sp)} 个设计点")  # 侧边栏提示成功
                load_all_designs.clear()   # 清绘图缓存，新设计点立即生效
            except sqlite3.Error as e:
                st.sidebar.error(f"设计sps数据入库错误: {e}")  # 出错则提示错误信息

        if daily_sps is not None and not daily_sps.empty:
            # 按日期分组：每个日期一组独立入库（覆盖式），实现多文件多天一起导入
            date_groups = {}   # 日期 -> 该日期的数据行
            for _, row in daily_sps.iterrows():
                dt = file_date_map.get(row['_src_file'])   # 取该行来源文件的日期
                if dt is None:
                    continue   # 日期未识别的文件整组跳过
                date_groups.setdefault(dt, []).append(row)   # 按日期归组
            if not date_groups:
                st.sidebar.warning("daily SPS 无法入库：未能从文件名识别到日期")  # 全部无法识别时提示
            for target_date, day_rows in date_groups.items():
                try:
                    # 覆盖式导入：先删该工区该日期的作业记录，再入库
                    c.execute("""
                              DELETE FROM shot_attempt
                              WHERE work_day_id IN (
                                  SELECT id FROM work_day WHERE project_id = ? AND work_date = ?
                              )
                              """, (project_id, target_date))   # 删掉同工区同日期旧数据，实现重新导入=覆盖
                    # 确保 work_day 存在
                    c.execute("INSERT OR IGNORE INTO work_day (project_id, work_date) VALUES (?, ?)",
                              (project_id, target_date))        # 该日期不存在则插入，存在则忽略
                    conn.commit()
                    wd_id = c.execute("SELECT id FROM work_day WHERE project_id = ? AND work_date = ?",
                                      (project_id, target_date)).fetchone()[0]  # 拿到 work_day 的 id

                    rows = []         # 准备写入的生产记录列表
                    skip_swath = 0    # 记录 swath 不匹配但 (line,point) 匹配的炮数
                    for row in day_rows:
                        if row.get('Swath'):
                            dp = c.execute("""
                                           SELECT id FROM design_point
                                           WHERE project_id = ? AND line = ? AND point = ? AND swath = ?
                                           """, (project_id, row['Line'], row['Point'], row['Swath'])).fetchone()  # 优先按 (line,point,swath) 匹配
                            if dp is None:
                                # 退而求其次：该工区确实有这个设计点，只是 swath 标注不一致
                                alt = c.execute("""
                                                SELECT id FROM design_point
                                                WHERE project_id = ? AND line = ? AND point = ?
                                                """, (project_id, row['Line'], row['Point'])).fetchone()  # 退回按 (line,point) 匹配
                                if alt is not None:
                                    skip_swath += 1   # 记录一次 swath 不一致
                            dp = dp or alt            # 优先用 swath 匹配结果，否则用退回结果
                        else:
                            dp = c.execute("""
                                           SELECT id FROM design_point
                                           WHERE project_id = ? AND line = ? AND point = ?
                                           """, (project_id, row['Line'], row['Point'])).fetchone()  # 没 swath 直接按 (line,point) 匹配
                        if dp is None:
                            continue   # 匹配不到设计点，跳过这一炮
                        rows.append((wd_id, dp[0], row['Elevation'], row['GPS Time'], row.get('Swath')))  # 组装入库元组

                    if rows:
                        c.executemany("""
                                      INSERT INTO shot_attempt (work_day_id, design_point_id, elevation, gps_time, swath)
                                      VALUES (?, ?, ?, ?, ?)
                                      """, rows)       # 批量写入生产记录
                        conn.commit()
                        fnames = ", ".join(sorted({row['_src_file'] for row in day_rows}))  # 该日期的来源文件名
                        msg = f"✅ {target_date} 的 daily SPS 已入库！匹配 {len(rows)} 炮"  # 成功提示
                        if fnames:
                            msg += f"\n📁 文件: {fnames}"  # 附上文件名，方便核对
                        if skip_swath:
                            msg += f"（{skip_swath} 炮 swath 标注不一致，已按设计点归属）"  # 附上 swath 不一致数
                        st.sidebar.success(msg)
                    else:
                        st.sidebar.warning(f"{target_date} 没有匹配到设计点的生产记录")  # 一条都没匹配上
                    load_daily_sps.clear()   # 清绘图缓存，新生产点立即生效
                except Exception as e:
                    st.sidebar.error(f"daily sps入库失败: {e}")  # 入库过程出错

    # 画图
    with col_chart:
        quick_mode = st.checkbox("⚡ 快速模式（降采样，点太多时推荐）", value=False)  # 快速模式：最多画 2 万点 — 整行放置，全局模式
        # 第一行：工区 | 开始日期 | 结束日期 | Plot 按钮（swath 筛选单独放下一行）
        subcol_1, subcol_2, subcol_3, subcol_4 = st.columns([1, 1, 1, 1])  # 4 列等宽对齐
        with subcol_1:
            plot_project_options = project_list if project_list else ["工区1"]  # 绘图工区下拉选项
            selected_project = st.selectbox("绘图工区", plot_project_options)     # 选择要画哪个工区
        with subcol_2:
            selected_pro1 = st.date_input("开始日期")   # 生产数据的开始日期
        with subcol_3:
            selected_pro2 = st.date_input("结束日期")   # 生产数据的结束日期
        with subcol_4:
            plot_clicked = st.button("Plot", use_container_width=True)   # Plot 按钮，宽填满第 4 列
        # swath 筛选：单独一行占满整行，留空 = 全部束线
        try:
            swath_df = pd.read_sql("""
                                   SELECT DISTINCT swath FROM design_point dp
                                   JOIN project p ON p.id = dp.project_id
                                   WHERE p.name = ? AND dp.swath IS NOT NULL AND dp.swath <> ''
                                   ORDER BY dp.swath
                                   """, conn, params=(selected_project,))  # 查该工区所有 swath 号
            swath_list = swath_df["swath"].tolist()
        except:
            swath_list = []
        selected_swaths = st.multiselect("swath 筛选（不选 = 全部）", options=swath_list,
                                         placeholder="全部束线")  # 整行多选，默认空 = 不过滤，标签横向排列不换行

        if plot_clicked:   # 点 Plot 按钮才画图

            @st.cache_data
            def load_all_designs(project_name):
                # 设计点与时间、swath 均无关：始终查全量，不按 swath 过滤
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

            df_design = load_all_designs(selected_project)  # 加载全部设计点

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
                                   sa.swath     as Swath,
                                   wd.work_date
                            FROM shot_attempt sa
                            JOIN design_point dp ON dp.id = sa.design_point_id
                            JOIN work_day wd ON wd.id = sa.work_day_id
                            JOIN project p ON p.id = wd.project_id
                            WHERE p.name = ? AND wd.work_date BETWEEN ? AND ?
                              AND sa.swath IN ({placeholders})   -- 按生产记录的 swath 筛选
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
                               sa.swath     as Swath,
                               wd.work_date
                        FROM shot_attempt sa
                        JOIN design_point dp ON dp.id = sa.design_point_id
                        JOIN work_day wd ON wd.id = sa.work_day_id
                        JOIN project p ON p.id = wd.project_id
                        WHERE p.name = ? AND wd.work_date BETWEEN ? AND ?
                        ORDER BY wd.work_date, dp.line, dp.point
                        """   # 不筛选 swath，查全部生产点
                return pd.read_sql(
                    query,
                    conn,
                    params=(project_name, start_date, end_date)
                )
            df_daily = load_daily_sps(selected_project, selected_pro1, selected_pro2, tuple(selected_swaths))  # 加载生产点

            fig = Tool.get_base_figure()   # 建空白底图
            max_shown = 20_000 if quick_mode else None   # 快速模式最多画 2 万点，否则全量
            if 'df_design' in locals() and not df_design.empty:
                fig = Tool.add_design_trace(fig, df_design, max_shown=max_shown)  # 叠加设计层
            if 'df_daily' in locals() and not df_daily.empty:
                fig = Tool.add_production_sps(fig, df_daily, max_shown=max_shown)  # 叠加生产层（按高程上色）
            st.plotly_chart(fig)   # 渲染图表

            st.session_state.df_design = load_all_designs(selected_project)   # 存设计点供统计
            st.session_state.df_daily = load_daily_sps(selected_project, selected_pro1, selected_pro2, tuple(selected_swaths))  # 存生产点供统计

    with col_stats:
        st.markdown("### 📊 生产统计")
        if "df_design" in st.session_state and "df_daily" in st.session_state:   # 画过图才有统计
            # 统计数字独立于 swath 筛选：直接从 DB 查全量设计点数和全量生产炮数
            total_sp = pd.read_sql("""
                                   SELECT COUNT(*) FROM design_point dp
                                   JOIN project p ON p.id = dp.project_id
                                   WHERE p.name = ?
                                   """, conn, params=(selected_project,)).iloc[0, 0]  # 该工区所有设计点（不受 swath 筛选影响）
            total_sps = pd.read_sql("""
                                    SELECT COUNT(*) FROM shot_attempt sa
                                    JOIN work_day wd ON wd.id = sa.work_day_id
                                    JOIN project p ON p.id = wd.project_id
                                    WHERE p.name = ? AND wd.work_date BETWEEN ? AND ?
                                    """, conn, params=(selected_project, selected_pro1, selected_pro2)).iloc[0, 0]  # 该工区日期范围内所有生产炮
            if total_sp > 0 and total_sps > 0:
                progress_val = (total_sps / total_sp) if total_sp > 0 else 0   # 完成比例
                # 1. 饼图展示
                pie_fig = Tool.plot_progress_pie(total_sps, total_sp)   # 环形进度饼图
                st.plotly_chart(pie_fig)
                # 2. 核心指标卡片
                st.metric("设计总数", f"{total_sp}")             # 设计数指标卡（全量）
                st.metric("实际完成", f"{total_sps}", delta=f"{total_sps}")   # 完成数指标卡（全量 daily SPS）
            else:
                st.write('No production')   # 无数据显示占位

    col_1, col_3 = st.columns([1, 1])   # 两列等宽放管理区
    st.divider()                        # 分隔线
    with col_1:
        st.subheader("💾 库内工区管理")
        try:
            db_summary = pd.read_sql("""
                                     SELECT p.id, p.name, COUNT(dp.id) as count
                                     FROM project p
                                     LEFT JOIN design_point dp ON dp.project_id = p.id
                                     GROUP BY p.id
                                     ORDER BY p.name
                                     """, conn)   # 各工区及其设计点数

            if not db_summary.empty:
                with st.container(height=250, border=True):   # 可滚动容器，缩小高度
                    for _, row in db_summary.iterrows():      # 逐工区显示
                        col_d, col_b = st.columns([2, 1])     # 左名字右删除按钮
                        col_d.write(f"📅 {row['name']} ({row['count']} 炮)")   # 工区名+点数
                        if col_b.button("删除", key=f"btn_p_{row['id']}"):    # 删除按钮
                            c.execute("DELETE FROM project WHERE id = ?", (row['id'],))  # 删工区（级联删子表）
                            conn.commit()                                       # 提交删除
            else:
                st.write("数据库尚无记录")   # 空库提示
        except:
            st.write("等待初始化...")   # 表未建好时兜底

    with col_3:
        st.subheader("📅 库内daily sps 管理")
        # 查询数据库中已有的日期和炮数
        try:
            db_summary = pd.read_sql("""
                                     SELECT wd.id, wd.work_date, COUNT(sa.id) as count
                                     FROM work_day wd
                                     LEFT JOIN shot_attempt sa ON sa.work_day_id = wd.id
                                     GROUP BY wd.id
                                     ORDER BY wd.work_date DESC
                                     """, conn)   # 各作业日及其炮数

            if not db_summary.empty:
                with st.container(height=250, border=True):   # 可滚动容器，缩小高度
                    for _, row in db_summary.iterrows():      # 逐日显示
                        col_d, col_b = st.columns([2, 1])     # 左日期右删除按钮
                        col_d.write(f"📅 {row['work_date']} ({row['count']} 炮)")  # 日期+炮数
                        if col_b.button("删除", key=f"btn_wd_{row['id']}"):        # 删除按钮
                            c.execute("DELETE FROM work_day WHERE id = ?", (row['id'],))  # 删作业日（级联删当天炮）
                            conn.commit()   # 提交删除
            else:
                st.write("数据库尚无记录")   # 空库提示
        except Exception as e:
            st.write("读取数据时出错，请刷新页面")  # 异常提示

with tab2:
    st.subheader("🤖 S90 助手")   # 助手标题

    # --- 初始化 session_state ---
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []   # 聊天历史：[(role, msg), ...]
    if "agent" not in st.session_state:
        st.session_state.agent = None        # 当前 agent 实例，配置后才有
    if "model_list" not in st.session_state:
        st.session_state.model_list = []     # 可用模型列表
    if "current_thread_id" not in st.session_state:
        st.session_state.current_thread_id = None   # 当前会话的 thread_id（记忆键）
    if "session_name" not in st.session_state:
        st.session_state.session_name = None        # 当前会话显示名
    if "last_models_fetched" not in st.session_state:
        st.session_state.last_models_fetched = False   # 模型列表是否已自动拉取过

    # --- 会话管理：列出 agent_session 表中的会话 ---
    def load_sessions():
        """读 agent_session 表，返回 [(id, name, thread_id), ...]"""
        try:
            return pd.read_sql("SELECT id, name, thread_id FROM agent_session ORDER BY id DESC",
                               conn).to_dict("records")   # 按创建倒序，新的在前
        except Exception:
            return []   # 表不存在时返回空

    def make_new_session():
        """新建一个会话：插入 agent_session 表，返回 (name, thread_id)"""
        name = f"会话 {datetime.datetime.now():%m%d-%H%M}"   # 显示名：会话 + 月日时分
        thread_id = f"thread_{datetime.datetime.now():%Y%m%d%H%M%S}"   # 记忆键：thread_时间戳，避免重复
        c.execute("INSERT INTO agent_session (name, thread_id) VALUES (?, ?)", (name, thread_id))
        conn.commit()   # 提交插入
        return name, thread_id   # 返回新会话标识

    sessions = load_sessions()   # 当前会话列表
    if not sessions and st.session_state.agent:   # 表空但有 agent：自动补建一个默认会话
        n, t = make_new_session()                 # 建默认会话
        sessions = load_sessions()                # 重读列表
        st.session_state.current_thread_id = t    # 设为当前会话
        st.session_state.session_name = n

    # --- 会话切换区（放在配置区上方） ---
    if st.session_state.agent and sessions:
        session_options = {s["name"]: s for s in sessions}   # 显示名 -> 会话记录
        picked = st.selectbox("记忆会话", list(session_options.keys()),
                              index=max(0, [s["name"] for s in sessions].index(st.session_state.session_name)) if st.session_state.session_name in [s["name"] for s in sessions] else 0,
                              key="session_select")   # 会话下拉：当前会话置顶显示
        col_new, col_del = st.columns([1, 1])   # 新建/删除按钮两列
        with col_new:
            if st.button("➕ 新建会话", use_container_width=True):
                n, t = make_new_session()            # 新建会话
                st.session_state.current_thread_id = t   # 切换过去
                st.session_state.session_name = n
                st.session_state.chat_history = []   # 清空界面历史（新会话没历史）
                st.rerun()                            # 刷新界面
        with col_del:
            if st.button("🗑 删除当前会话", use_container_width=True):
                cur = session_options.get(st.session_state.session_name)   # 当前会话记录
                if cur:
                    st.session_state.agent.delete_thread(cur["thread_id"])   # 删记忆库里的检查点
                    c.execute("DELETE FROM agent_session WHERE id = ?", (cur["id"],))   # 删会话记录
                    conn.commit()
                    st.session_state.chat_history = []   # 清空界面历史
                    st.session_state.current_thread_id = None   # 无当前会话
                    st.session_state.session_name = None
                    st.rerun()                            # 刷新界面
        # 下拉里手动切换会话：同步当前 thread_id 并恢复该会话历史
        sel = session_options.get(picked)
        if sel and sel["thread_id"] != st.session_state.current_thread_id:
            st.session_state.current_thread_id = sel["thread_id"]   # 切到所选会话
            st.session_state.session_name = sel["name"]
            st.session_state.chat_history = []   # 先清空，下面从记忆恢复
            try:
                state = st.session_state.agent.agent.get_state(
                    {"configurable": {"thread_id": sel["thread_id"]}})   # 读该会话的记忆
                msgs = (state.values.get("messages") or []) if state else []   # 取消息列表
                for m in msgs:   # 遍历消息
                    if getattr(m, "type", "") in ("human", "ai"):   # 只显示人/助手的
                        role = "user" if m.type == "human" else "assistant"   # 角色映射
                        if m.content:   # 有内容才显示
                            st.session_state.chat_history.append({"role": role, "content": m.content})   # 恢复一条
            except Exception:
                pass   # 恢复失败不阻塞，就当新会话
        st.divider()   # 分隔线

    # --- 配置区：base_url + api_key → 拉模型列表 ---
    col1, col2, col3 = st.columns([2, 3, 2])   # 三列布局
    with col1:
        base_url = st.text_input("API 地址", "https://api.openai.com/v1")  # API 地址
    with col2:
        api_key = st.text_input("API Key", type="password")   # API 密钥，隐藏显示
    with col3:
        st.write("")   # 占位对齐
        fetch_models = st.button("获取模型", use_container_width=True)   # 拉模型按钮

    # 填好地址和 key 后自动拉取模型，或点按钮手动刷新
    if (fetch_models or (base_url and api_key)) and "last_models_fetched" not in st.session_state:
        st.session_state.last_models_fetched = True   # 标记：只自动拉一次，之后手动刷新
        with st.spinner("正在获取模型列表..."):   # 等待时显示加载动画
            try:
                # 拼接 /models 接口地址：base_url 去掉末尾 / 再补 /models
                models_url = base_url.rstrip("/") + "/models"
                resp = requests.get(models_url,    # 直接调用模型列表接口
                                    headers={"Authorization": f"Bearer {api_key}"},
                                    timeout=15)    # 15 秒超时，防止卡死
                if resp.status_code == 200:        # 请求成功
                    data = resp.json()             # 解析返回 JSON
                    model_ids = [m["id"] for m in data.get("data", [])]   # 提取模型 ID 列表
                    st.session_state.model_list = sorted(model_ids)       # 排序保存
                    st.success(f"获取到 {len(model_ids)} 个模型")
                else:
                    # 非 200：显示状态码和接口返回的错误信息，方便排查
                    st.error(f"获取模型失败 HTTP {resp.status_code}：{resp.text[:200]}")
                    st.session_state.model_list = []   # 清空列表
            except Exception as e:
                # 网络错误/地址不对等异常，提示用户检查地址
                st.error(f"获取模型失败：{e}")
                st.session_state.model_list = []   # 清空列表

    # --- 模型选择：自动获取列表 + 手动填写兜底 ---
    if st.session_state.model_list:
        selected_model = st.selectbox("选择模型", st.session_state.model_list)   # 自动获取成功：下拉选
    else:
        selected_model = st.text_input("手动输入模型名", "gpt-4o")   # 没拉到列表：手动填模型名

    # --- 初始化 agent ---
    if st.button("连接助手") and api_key:
        if base_url and selected_model:
            try:
                st.session_state.agent = dbtool.Productiontool(
                    db_path=DB_FILE,
                    api_key=api_key,
                    model_name=selected_model,
                    base_url=base_url
                )   # 创建 agent 实例，关掉 temperature 保证稳定
                # 连接后确保有一个记忆会话可用
                sessions = load_sessions()   # 重读会话列表
                if not sessions:             # 没有任何会话：自动建默认会话
                    n, t = make_new_session()
                    st.session_state.current_thread_id = t
                    st.session_state.session_name = n
                    st.session_state.chat_history = []   # 新会话无历史
                else:   # 已有会话：切到最新的一个并恢复历史
                    first = sessions[0]
                    st.session_state.current_thread_id = first["thread_id"]
                    st.session_state.session_name = first["name"]
                    st.session_state.chat_history = []   # 先清空，下面从记忆恢复
                    try:
                        state = st.session_state.agent.agent.get_state(
                            {"configurable": {"thread_id": first["thread_id"]}})   # 读该会话记忆
                        msgs = (state.values.get("messages") or []) if state else []   # 取消息列表
                        for m in msgs:   # 遍历消息
                            if getattr(m, "type", "") in ("human", "ai"):   # 只显示人/助手的
                                role = "user" if m.type == "human" else "assistant"   # 角色映射
                                if m.content:
                                    st.session_state.chat_history.append({"role": role, "content": m.content})   # 恢复一条
                    except Exception:
                        pass   # 恢复失败不阻塞
                st.success("Agent 已就绪")
            except Exception as e:
                st.error(f"初始化失败: {e}")

    st.divider()

    # --- 显示历史消息 ---
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])   # 逐条渲染历史

    # --- 快捷提问 ---
    with st.expander("💡 提示示例"):
        st.write("• 有哪些工区？")
        st.write("• 本月生产了多少炮？")
        st.write("• 最近 7 天每天的炮数")
        st.write("• 当前完成进度？")

    # --- 聊天输入 ---
    user_input = st.chat_input("输入问题...")

    if user_input:
        if not st.session_state.agent:
            st.warning("请先配置 API Key 并初始化 Agent")  # 未就绪时提示
        elif not st.session_state.current_thread_id:
            st.warning("请先新建或选择一个记忆会话")  # 没有会话时提示
        else:
            st.session_state.chat_history.append({"role": "user", "content": user_input})  # 保存用户消息
            with st.chat_message("user"):
                st.write(user_input)   # 回显用户消息

            with st.chat_message("assistant"):
                # 推理过程用浅灰小字折叠显示，正文实时更新
                reasoning_placeholder = st.empty()   # 推理过程占位
                placeholder = st.empty()             # 正文占位
                full_response = ""                   # 完整回答
                reasoning_text = ""                  # 推理过程
                with st.status("思考中...", expanded=False) as status:   # 折叠状态框
                    try:
                        for kind, payload in st.session_state.agent.ask_stream(
                                user_input, thread_id=st.session_state.current_thread_id):   # 遍历流式片段，带当前会话记忆
                            if kind == "reasoning":        # 推理过程片段
                                reasoning_text += payload  # 累加推理文本
                                status.update(label=f"🤔 思考中 {len(reasoning_text)} 字...", expanded=False)   # 更新字数提示
                            elif kind == "content":        # 正文片段
                                full_response += payload   # 累加正文
                                placeholder.markdown(full_response)   # 实时更新显示
                    except Exception as e:
                        placeholder.error(f"出错了: {e}")   # 错误提示
                        full_response = f"[错误] {e}"        # 记录错误到历史
                    finally:
                        status.update(label="✅ 回答完成", expanded=False)   # 完成后更新状态
                if reasoning_text and full_response:   # 有推理和正文
                    with st.expander("💭 思考过程"):   # 折叠显示思考过程
                        st.write(reasoning_text)       # 显示推理全文
                elif reasoning_text and not full_response:   # 只有推理没正文（异常情况）
                    st.write(reasoning_text)   # 直接显示推理
                st.session_state.chat_history.append({"role": "assistant", "content": full_response})   # 保存回复
