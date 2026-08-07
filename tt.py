import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pandas as pd
from datetime import datetime
import os
from collections import defaultdict


class TimeEfficiencyAnalyzer:
    def __init__(self, root):
        self.root = root
        self.root.title("时效分析工具 - 每天12-18点平均时效计算")
        self.root.geometry("800x600")

        # 存储选中的文件
        self.selected_files = []

        # 创建界面
        self.create_widgets()

    def create_widgets(self):
        # 标题
        title_label = tk.Label(self.root, text="时效分析工具", font=("Arial", 16, "bold"))
        title_label.pack(pady=10)

        # 文件选择区域
        file_frame = tk.Frame(self.root)
        file_frame.pack(pady=10, padx=20, fill="x")

        self.file_label = tk.Label(file_frame, text="未选择任何文件", wraplength=600)
        self.file_label.pack(side="left", fill="x", expand=True)

        select_btn = tk.Button(file_frame, text="选择文件", command=self.select_files,
                               bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
        select_btn.pack(side="right", padx=5)

        clear_btn = tk.Button(file_frame, text="清空选择", command=self.clear_files,
                              bg="#f44336", fg="white", font=("Arial", 10, "bold"))
        clear_btn.pack(side="right", padx=5)

        # 进度条和状态
        self.status_var = tk.StringVar(value="就绪")
        status_label = tk.Label(self.root, textvariable=self.status_var,
                                font=("Arial", 10), fg="blue")
        status_label.pack(pady=5)

        # 分析按钮
        analyze_btn = tk.Button(self.root, text="开始分析", command=self.analyze_data,
                                bg="#2196F3", fg="white", font=("Arial", 12, "bold"),
                                height=2, width=20)
        analyze_btn.pack(pady=20)

        # 结果显示区域
        result_frame = tk.Frame(self.root)
        result_frame.pack(pady=10, padx=20, fill="both", expand=True)

        # 创建Treeview显示结果
        columns = ("日期", "12-18点平均时效", "样本数量")
        self.tree = ttk.Treeview(result_frame, columns=columns, show="headings", height=15)

        # 设置列标题
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=200, anchor="center")

        # 添加滚动条
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 导出按钮
        export_btn = tk.Button(self.root, text="导出结果到CSV", command=self.export_results,
                               bg="#FF9800", fg="white", font=("Arial", 10, "bold"))
        export_btn.pack(pady=10)

        # 存储结果
        self.results = {}

    def select_files(self):
        """选择多个数据文件"""
        files = filedialog.askopenfilenames(
            title="选择时效数据文件",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )

        if files:
            self.selected_files = list(files)
            self.update_file_label()
            self.status_var.set(f"已选择 {len(self.selected_files)} 个文件")

    def clear_files(self):
        """清空选中的文件"""
        self.selected_files = []
        self.update_file_label()
        self.status_var.set("已清空文件选择")
        # 清空结果
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.results = {}

    def update_file_label(self):
        """更新文件标签显示"""
        if self.selected_files:
            # 只显示文件名，用换行分隔
            filenames = [os.path.basename(f) for f in self.selected_files[:5]]
            if len(self.selected_files) > 5:
                filenames.append(f"... 等{len(self.selected_files)}个文件")
            self.file_label.config(text="\n".join(filenames))
        else:
            self.file_label.config(text="未选择任何文件")

    def parse_time_from_line(self, line):
        """从行中提取时间（最后6位数字）"""
        try:
            # 分割行
            parts = line.strip().split()
            if parts:
                # 获取最后一个字段
                last_field = parts[-1]
                # 提取最后6位数字
                time_str = last_field[-6:]
                if len(time_str) == 6 and time_str.isdigit():
                    # 解析为 HHMMSS 格式
                    hour = int(time_str[:2])
                    minute = int(time_str[2:4])
                    second = int(time_str[4:6])
                    return hour, minute, second
        except:
            pass
        return None, None, None

    def extract_date_from_filename(self, filename):
        """从文件名中提取日期（如 swxxxx-0501 表示5月1日）"""
        basename = os.path.basename(filename)
        try:
            # 查找 - 后面的4位数字
            if '-' in basename:
                date_part = basename.split('-')[-1]
                # 提取前4位数字（MMDD）
                import re
                match = re.search(r'(\d{4})', date_part)
                if match:
                    mmdd = match.group(1)
                    month = int(mmdd[:2])
                    day = int(mmdd[2:])
                    # 假设是2026年（可根据实际情况调整）
                    return datetime(2026, month, day).date()
        except:
            pass
        return None

    def read_file_data(self, filepath):
        """读取文件并提取时效数据"""
        data = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):  # 跳过空行和注释行
                        # 提取时效值（第5列）
                        parts = line.split()
                        if len(parts) >= 5:
                            try:
                                efficiency = float(parts[4])
                                hour, minute, second = self.parse_time_from_line(line)
                                if hour is not None:
                                    # 只在12-18点之间
                                    if 12 <= hour <= 18:
                                        data.append({
                                            'efficiency': efficiency,
                                            'hour': hour,
                                            'minute': minute,
                                            'second': second,
                                            'time': f"{hour:02d}:{minute:02d}:{second:02d}"
                                        })
                            except (ValueError, IndexError):
                                continue
            return data
        except Exception as e:
            print(f"读取文件 {filepath} 失败: {e}")
            return []

    def analyze_data(self):
        """执行数据分析"""
        if not self.selected_files:
            messagebox.showwarning("警告", "请先选择数据文件！")
            return

        # 清空之前的结果
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.status_var.set("正在分析数据，请稍候...")
        self.root.update()

        # 按日期分组收集数据
        daily_data = defaultdict(list)
        file_count = len(self.selected_files)

        for i, filepath in enumerate(self.selected_files, 1):
            self.status_var.set(f"正在处理文件 {i}/{file_count}: {os.path.basename(filepath)}")
            self.root.update()

            # 从文件名提取日期
            date = self.extract_date_from_filename(filepath)
            if date is None:
                print(f"无法从文件名提取日期: {filepath}")
                continue

            # 读取数据
            data = self.read_file_data(filepath)
            if data:
                daily_data[date].extend(data)

        # 计算每天12-18点的平均时效
        results = []
        for date in sorted(daily_data.keys()):
            data_list = daily_data[date]
            if data_list:
                efficiencies = [d['efficiency'] for d in data_list]
                avg_efficiency = sum(efficiencies) / len(efficiencies)
                results.append({
                    'date': date,
                    'avg_efficiency': avg_efficiency,
                    'count': len(efficiencies)
                })
                self.tree.insert("", "end", values=(
                    date.strftime("%Y-%m-%d"),
                    f"{avg_efficiency:.2f}",
                    len(efficiencies)
                ))

        self.results = results

        if results:
            self.status_var.set(f"分析完成！共处理 {len(results)} 天的数据")
            messagebox.showinfo("完成",
                                f"分析完成！\n共处理 {len(results)} 天的数据\n总样本数: {sum(r['count'] for r in results)}")
        else:
            self.status_var.set("未找到有效数据，请检查文件格式")
            messagebox.showwarning("无数据", "未找到符合条件的数据（12-18点），请检查文件格式。")

    def export_results(self):
        """导出结果到CSV文件"""
        if not self.results:
            messagebox.showwarning("警告", "没有可导出的结果，请先运行分析！")
            return

        # 选择保存路径
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="保存结果"
        )

        if filepath:
            try:
                # 创建DataFrame
                df = pd.DataFrame(self.results)
                df['date'] = df['date'].dt.strftime("%Y-%m-%d")
                df.to_csv(filepath, index=False, encoding='utf-8-sig')
                messagebox.showinfo("成功", f"结果已导出到：\n{filepath}")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败：{str(e)}")


# 启动程序
if __name__ == "__main__":
    root = tk.Tk()
    app = TimeEfficiencyAnalyzer(root)
    root.mainloop()