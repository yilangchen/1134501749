# @DATE：20/02/2026
# @TIME：
# @AUTHOR：YiLang CHEN
import pandas as pd          # pandas：处理表格数据
import plotly.graph_objects as go   # plotly：生成交互式图表


def get_base_figure():
    """初始化图表并设置布局"""
    fig = go.Figure()        # 新建空图表对象
    fig.update_layout(
        title="设计 vs 实际 展点对比图",   # 图表标题
        xaxis_title="X 坐标",            # X 轴标题
        yaxis_title="Y 坐标",            # Y 轴标题
        yaxis=dict( scaleratio=1, showgrid=False), # 纵轴隐藏网格线，保持 1:1 比例
        xaxis=dict(showgrid=False, zeroline=False),  # 横轴隐藏网格线和零线
        plot_bgcolor='white',   # 白色绘图区背景
        height=500,  # 建议稍微调高一点
        legend=dict(x=0, y=1.1, bgcolor='rgba(255,255,255,0.5)')  # 图例放顶部，半透明白底
    )
    return fig


def _stride_sample(df, max_shown):
    """降采样：按均匀间隔取子集，保证全图范围仍有点显示"""
    if max_shown is None or max_shown <= 0 or len(df) <= max_shown:   # 不降采样或无必要
        return df
    step = int(len(df) / max_shown) + 1   # 每隔 step 行取一个
    return df.iloc[::step]                # 均匀取子集


def add_design_trace(fig, df_sp, max_shown=None):
    """单独添加设计点层 (灰色)"""
    if df_sp is None or df_sp.empty:
        return fig

    # 预处理
    df = df_sp.copy()      # 复制一份，避免污染原数据
    df['X'] = pd.to_numeric(df['X'], errors='coerce')   # X 列转数值，非法变 NaN
    df['Y'] = pd.to_numeric(df['Y'], errors='coerce')   # Y 列转数值，非法变 NaN
    df = df.dropna(subset=['X', 'Y'])   # 丢弃坐标缺失的行

    show_hover = max_shown is None or len(df) <= max_shown   # 是否允许显示悬浮信息
    df = _stride_sample(df, max_shown)   # 按需降采样

    fig.add_trace(go.Scattergl(          # WebGL 模式，适合几十万点
        x=df['X'],                       # X 坐标列
        y=df['Y'],                       # Y 坐标列
        mode='markers',                  # 只画散点
        name='设计 SPS',                 # 图层名
        marker=dict(size=1, color='gray', symbol='circle'),   # 灰色圆点
        hovertext="设计 线:" + df['Line'].astype(str) + " 点:" + df['Point'].astype(str) if show_hover else None,  # 悬浮显示线号点号
        hoverinfo="text" if show_hover else "skip"   # 降采样后禁用悬浮，节省渲染
    ))
    return fig


def add_production_trace(fig, df_obs, max_shown=None):
    """单独添加实际生产层 (亮绿色)"""
    if df_obs is None or df_obs.empty:
        return fig

    # 预处理
    df = df_obs.copy()     # 复制一份，避免污染原数据
    df['Easting (m)'] = pd.to_numeric(df['Easting (m)'], errors='coerce')   # Easting 列转数值
    df['Northing (m)'] = pd.to_numeric(df['Northing (m)'], errors='coerce')  # Northing 列转数值
    df = df.dropna(subset=['Easting (m)', 'Northing (m)'])   # 丢弃坐标缺失的行

    show_hover = max_shown is None or len(df) <= max_shown   # 是否允许显示悬浮信息
    df = _stride_sample(df, max_shown)   # 按需降采样

    fig.add_trace(go.Scattergl(
        x=df['Easting (m)'],     # X 坐标列
        y=df['Northing (m)'],    # Y 坐标列
        mode='markers',          # 只画散点
        name='实际生产',          # 图层名
        marker=dict(
            size=1,                # 点大小
            color='#00CC96',       # 亮绿色
            symbol='circle',       # 实心圆
        ),
        hovertext="实际 线:" + df['Line Name'].astype(str) + " 点:" + df['Point Number'].astype(str) if show_hover else None,  # 悬浮显示线号点号
        hoverinfo="text+x+y" if show_hover else "skip"   # 降采样后禁用悬浮
    ))
    return fig

def add_production_sps(fig, df_obs, max_shown=None):
    """单独添加实际生产层 (亮绿色)"""
    if df_obs is None or df_obs.empty:
        return fig

    # 预处理
    df = df_obs.copy()     # 复制一份，避免污染原数据
    df['X'] = pd.to_numeric(df['X'], errors='coerce')   # X 列转数值
    df['Y'] = pd.to_numeric(df['Y'], errors='coerce')   # Y 列转数值
    df = df.dropna(subset=['X', 'Y'])   # 丢弃坐标缺失的行

    show_hover = max_shown is None or len(df) <= max_shown   # 是否允许显示悬浮信息
    df = _stride_sample(df, max_shown)   # 按需降采样

    fig.add_trace(go.Scattergl(
        x=df['X'],             # X 坐标列
        y=df['Y'],             # Y 坐标列
        mode='markers',        # 只画散点
        name='实际生产每日炮数',  # 图层名
        marker=dict(
            size=1,                 # 点大小
            color=df['Elevation'] if len(df) else None,   # 按高程上色
            colorscale='Viridis',  # 色阶：Viridis
            showscale=True,        # 显示颜色条
            symbol='circle',       # 实心圆
        ),
        hovertext="实际 线:" + df['Line'].astype(str) + " 点:" + df['Point'].astype(str) if show_hover else None,  # 悬浮显示线号点号
        hoverinfo="text+x+y" if show_hover else "skip"   # 降采样后禁用悬浮
    ))
    return fig

def plot_progress_pie(completed, total):
    # 计算剩余点数
    remaining = max(0, total - completed)   # 剩余 = 总数 - 已完成，兜底不为负

    fig = go.Figure(data=[go.Pie(
        labels=['已完成', '剩余待办'],   # 两段标签
        values=[completed, remaining],   # 两段数值
        hole=.6,  # 环形设计，更现代
        marker_colors=["#00CC96", "#EEEEEE"],  # 绿色和浅灰色
        textinfo='percent',   # 扇形上显示百分比
        showlegend=False,  # 侧边栏较窄，隐藏图例以节省空间
        domain=dict(x=[0, 0.0])
    )])

    fig.update_layout(
        margin=dict(t=0, b=0, l=10, r=10),   # 四周留白最小化，适配窄栏
        height=150,                           # 小图高度
        annotations=[dict(text=f'进度', x=0.5, y=0.5, font_size=16, showarrow=False)]   # 圆环中心写"进度"
    )
    return fig