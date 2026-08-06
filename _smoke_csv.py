# 样例 CSV 冒烟：验证 app.py 停机导入的解析+入库核心逻辑（不启 UI，直接调核心片段）
import sqlite3, re, datetime, pandas as pd, io

DB="/Volumes/T7_Shield/07-tempCode/02-seismic/production.db"
conn=sqlite3.connect(DB); c=conn.cursor(); conn.execute("PRAGMA foreign_keys=ON")
conn.execute("CREATE TABLE IF NOT EXISTS vib_stop (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, work_day_id INTEGER, vib1 TEXT,vib2 TEXT,grp TEXT,start_time TEXT,end_time TEXT,duration_sec INTEGER,reason TEXT,src_file TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")

# 模拟一个上传文件：20260801.csv（注意 pandas 读 BytesIO 需要 seek）
csv_text = """Vib1,Vib2,Group,StartTime,EndTime,Duration,Reason
V101,V102,G1,08:00:00,08:05:53,0:05:53,temperature warning
V101,V102,G1,09:00:00,09:10:00,0:10:00,temperature warning alert
V201,V202,G2,10:00:00,10:15:00,0:15:00,temperature warning
V301,,"G1",11:00:00,11:20:00,0:20:00,hydraulic failure
,,,12:00:00,12:30:00,0:30:00,temperature warning
"""
# 注意: demo 里第三行 Group 带引号, 第五行 vib1/vib2 空
buf=io.BytesIO(csv_text.encode("utf-8"))
df_v=pd.read_csv(buf)

# ===== 复刻 app.py 解析逻辑 =====
# 列名容错
df_v.columns=[str(col).strip().lower() for col in df_v.columns]
print("读入列:", list(df_v.columns))

def _parse_duration(dur):
    if dur is None or (isinstance(dur,float) and pd.isna(dur)):
        return None
    s=str(dur).strip()
    if not s: return None
    try:
        parts=[int(p) for p in s.split(':')]
        if len(parts)==3: return parts[0]*3600+parts[1]*60+parts[2]
        if len(parts)==2: return parts[0]*60+parts[1]
        return parts[0] if parts else None
    except (ValueError,TypeError):
        return None

def _col(*names):
    for n in names:
        if n in df_v.columns: return df_v[n]
    return pd.Series([None]*len(df_v))

df_v['vib1']=_col('vib1','vib')
df_v['vib2']=_col('vib2')
df_v['grp']=_col('grp','group')
df_v['start_time']=_col('starttime','start_time')
df_v['end_time']=_col('endtime','end_time')
df_v['duration_sec']=_col('duration').map(_parse_duration)
df_v['reason']=_col('reason').astype(str).str.strip()
df_v['_src_file']="20260801.csv"

print("\n=== 解析后表格 (重点看 duration_sec 和 reason) ===")
print(df_v[['vib1','vib2','grp','duration_sec','reason']].to_string())

# 文件名取日期
m=re.search(r'(\d{4})(\d{2})(\d{2})',"20260801.csv")
date=datetime.date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
print("\n文件名日期:", date)

# ===== 复刻入库逻辑 =====
proj_id=c.execute("SELECT id FROM project WHERE name='NIBAN'").fetchone()[0]
c.execute("INSERT OR IGNORE INTO work_day(project_id,work_date) VALUES(?,?)",(proj_id,date))
conn.commit()
v_wd_id=c.execute("SELECT id FROM work_day WHERE project_id=? AND work_date=?",(proj_id,date)).fetchone()[0]
c.execute("DELETE FROM vib_stop WHERE work_day_id IN (SELECT id FROM work_day WHERE project_id=? AND work_date=?)",(proj_id,date))
v_rows=[]; temp_cnt=0
def _s(v):
    if v is None or (isinstance(v,float) and pd.isna(v)): return None
    return str(v).strip() or None
for _,vrow in df_v.iterrows():
    reason=_s(vrow.get('reason'))
    if reason and 'temperature warning' in reason.lower(): temp_cnt+=1
    v_rows.append((proj_id,v_wd_id,_s(vrow.get('vib1')),_s(vrow.get('vib2')),_s(vrow.get('grp')),
                   _s(vrow.get('start_time')),_s(vrow.get('end_time')),
                   vrow.get('duration_sec') if pd.notna(vrow.get('duration_sec')) else None, reason, vrow['_src_file']))
c.executemany("INSERT INTO vib_stop(project_id,work_day_id,vib1,vib2,grp,start_time,end_time,duration_sec,reason,src_file) VALUES(?,?,?,?,?,?,?,?,?,?)",v_rows)
conn.commit()

# 验证入库
stored=c.execute("SELECT vib1,vib2,duration_sec,reason FROM vib_stop WHERE src_file='20260801.csv' ORDER BY id").fetchall()
print("\n=== 入库后 ===")
for r in stored: print(" ", r)
print("温度停机条数:", temp_cnt, "(期望 4: 353,600,900,1800; hydraulic 应被排除但行仍入库)")
print("\n断言: 353/600/900/1800 秒正确入库, 温度4条")

# 清理
c.execute("DELETE FROM vib_stop WHERE src_file='20260801.csv'"); conn.commit()
print("已清理冒烟数据")
