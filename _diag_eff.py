# -*- coding: utf-8 -*-
import sqlite3   # 连 SQLite 库

conn = sqlite3.connect('production.db')   # 打开生产库
conn.execute('PRAGMA foreign_keys = ON')   # 开外键

print('=== shot_attempt 总数 ===')
print(conn.execute('SELECT COUNT(*) FROM shot_attempt').fetchone()[0])   # 总炮数

print()
print('=== 前5行 ===')
for r in conn.execute('SELECT id, work_day_id, design_point_id, gps_time, swath FROM shot_attempt LIMIT 5'):
    print(r)   # 看每行字段

print()
print('=== gps_time 原文样本 ===')
for r in conn.execute('SELECT gps_time FROM shot_attempt LIMIT 10'):
    print(repr(r[0]))   # 看真实字符串

print()
print('=== gps_time 长度分布 ===')
for r in conn.execute('SELECT LENGTH(gps_time) L, COUNT(*) FROM shot_attempt GROUP BY L'):
    print(f'  长度={r[0]}: {r[1]}条')   # 看是8位还是9位

print()
print('=== 用工具的 SQL 取小时 ===')
for r in conn.execute('SELECT substr(gps_time, length(gps_time)-5, 2) h, COUNT(*) FROM shot_attempt GROUP BY h ORDER BY h'):
    print(f'  小时={r[0]}: {r[1]}条')   # 直接跑工具里的取小时SQL

print()
print('=== 每天炮数 ===')
for r in conn.execute('SELECT wd.work_date, COUNT(*) FROM work_day wd JOIN shot_attempt sa ON sa.work_day_id=wd.id GROUP BY wd.work_date ORDER BY wd.work_date'):
    print(f'  {r[0]}: {r[1]}炮')   # 每天总数

print()
print('=== 项目列表 ===')
for r in conn.execute('SELECT id, name FROM project'):
    print(f'  {r[0]}: {r[1]}')   # 看项目名，确认 agent 传的 project_name 对不对

print()
print('=== 模拟工具调用：12-18点 ===')
rows = conn.execute(
    "SELECT wd.work_date, COUNT(*) FROM shot_attempt sa "
    "JOIN work_day wd ON wd.id=sa.work_day_id "
    "JOIN project p ON p.id=wd.project_id "
    "WHERE CAST(substr(sa.gps_time, length(sa.gps_time)-5, 2) AS INT) >= 12 "
    "  AND CAST(substr(sa.gps_time, length(sa.gps_time)-5, 2) AS INT) < 18 "
    "GROUP BY wd.work_date ORDER BY wd.work_date"
).fetchall()
for r in rows:
    print(f'  {r[0]}: {r[1]}炮')   # 直接按12-18段统计

conn.close()
