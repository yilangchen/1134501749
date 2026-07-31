# @DATE：20/02/2026
# @TIME：
# @AUTHOR：YiLang CHEN
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def get_base_figure():
    """初始化图表并设置布局"""
    fig = go.Figure()
    fig.update_layout(
        title="设计 vs 实际 展点对比图",
        xaxis_title="X 坐标",
        yaxis_title="Y 坐标",
        yaxis=dict( scaleratio=1, showgrid=False), #  scaleanchor="x",
        xaxis=dict(showgrid=False, zeroline=False),
        plot_bgcolor='white',
        height=500,  # 建议稍微调高一点
        legend=dict(x=0, y=1.1, bgcolor='rgba(255,255,255,0.5)')
    )
    return fig


def _stride_sample(df, max_shown):
    """降采样：按均匀间隔取子集，保证全图范围仍有点显示"""
    if max_shown is None or max_shown <= 0 or len(df) <= max_shown:
        return df
    step = int(len(df) / max_shown) + 1
    return df.iloc[::step]


def add_design_trace(fig, df_sp, max_shown=None):
    """单独添加设计点层 (灰色)"""
    if df_sp is None or df_sp.empty:
        return fig

    # 预处理
    df = df_sp.copy()
    df['X'] = pd.to_numeric(df['X'], errors='coerce')
    df['Y'] = pd.to_numeric(df['Y'], errors='coerce')
    df = df.dropna(subset=['X', 'Y'])

    show_hover = max_shown is None or len(df) <= max_shown
    df = _stride_sample(df, max_shown)

    fig.add_trace(go.Scattergl(
        x=df['X'],
        y=df['Y'],
        mode='markers',
        name='设计 SPS',
        marker=dict(size=0.5, color='lightgray', symbol='circle'),
        hovertext="设计 线:" + df['Line'].astype(str) + " 点:" + df['Point'].astype(str) if show_hover else None,
        hoverinfo="text" if show_hover else "skip"
    ))
    return fig


def add_production_trace(fig, df_obs, max_shown=None):
    """单独添加实际生产层 (亮绿色)"""
    if df_obs is None or df_obs.empty:
        return fig

    # 预处理
    df = df_obs.copy()
    # 假设你的列名是 'Easting (m)' 和 'Northing (m)'
    df['Easting (m)'] = pd.to_numeric(df['Easting (m)'], errors='coerce')
    df['Northing (m)'] = pd.to_numeric(df['Northing (m)'], errors='coerce')
    df = df.dropna(subset=['Easting (m)', 'Northing (m)'])

    show_hover = max_shown is None or len(df) <= max_shown
    df = _stride_sample(df, max_shown)

    fig.add_trace(go.Scattergl(
        x=df['Easting (m)'],
        y=df['Northing (m)'],
        mode='markers',
        name='实际生产',
        marker=dict(
            size=1,
            color='#00CC96',
            symbol='circle',
            # line=dict(width=1, color='white')
        ),
        hovertext="实际 线:" + df['Line Name'].astype(str) + " 点:" + df['Point Number'].astype(str) if show_hover else None,
        hoverinfo="text+x+y" if show_hover else "skip"
    ))
    return fig

def add_production_sps(fig, df_obs, max_shown=None):
    """单独添加实际生产层 (亮绿色)"""
    if df_obs is None or df_obs.empty:
        return fig

    # 预处理
    df = df_obs.copy()
    # 假设你的列名是 'Easting (m)' 和 'Northing (m)'
    df['X'] = pd.to_numeric(df['X'], errors='coerce')
    df['Y'] = pd.to_numeric(df['Y'], errors='coerce')
    df = df.dropna(subset=['X', 'Y'])

    show_hover = max_shown is None or len(df) <= max_shown
    df = _stride_sample(df, max_shown)

    fig.add_trace(go.Scattergl(
        x=df['X'],
        y=df['Y'],
        mode='markers',
        name='实际生产每日炮数',
        marker=dict(
            size=1,
            # color='#00CC96',
            color=df['Elevation'] if len(df) else None,
            colorscale='Viridis',  # 推荐色阶：Viridis, Plasma, Terrain 等
            showscale=True,
            symbol='circle',
            # line=dict(width=1, color='white')
        ),
        hovertext="实际 线:" + df['Line'].astype(str) + " 点:" + df['Point'].astype(str) if show_hover else None,
        hoverinfo="text+x+y" if show_hover else "skip"
    ))
    return fig

def plot_production_comparison(designsps, obs_sps):
    """
    designsps: 设计数据 (使用列索引 3 和 4)
    obs_sps: 实际数据 (使用列索引 6 和 7)
    """
    fig = go.Figure()

    # --- 1. 处理设计数据 (SPs) ---
    sp_plot = designsps.copy()
    sp_plot['X'] = pd.to_numeric(sp_plot['X'], errors='coerce')
    sp_plot['X'] = pd.to_numeric(sp_plot['X'], errors='coerce')
    sp_plot = sp_plot.sort_values(by=['Line', 'Point'])

    # 绘制设计点（背景层：灰色、更小）
    fig.add_trace(go.Scatter(
        x=sp_plot['X'],
        y=sp_plot['Y'],
        mode='markers',
        name='设计 SPS',
        marker=dict(
            size=0.5,           # 点的大小设小一点
            color='lightgray', # 设为灰色
            symbol='circle-open'
        ),
        hovertext="设计 线:" + sp_plot['Line'].astype(str) + " 点:" + sp_plot['Point'].astype(str),
        hoverinfo="text"
    ))

    # --- 2. 处理实际数据 (SPS) ---
    # if 'Is Raw' in obs_sps.columns:
    #     # 筛选 False
    #     sps_plot = obs_sps[obs_sps['Is Raw'] == False].copy()
    # else:
    #     st.error("数据中找不到 'Is Raw' 列，请检查 CSV 表头！")
    #     sps_plot = obs_sps.copy()
    # sps_plot['Easting (m)'] = pd.to_numeric(sps_plot['Easting (m)'], errors='coerce')
    # sps_plot['Northing (m)'] = pd.to_numeric(sps_plot['Northing (m)'], errors='coerce')
    # # 如果实际数据也需要连线，建议也排序
    # sps_plot = sps_plot.sort_values(by=['Easting (m)', 'Northing (m)'])
    sps_plot = obs_sps.copy()
    # 绘制实际进度点（前景层：高亮颜色、更大）
    fig.add_trace(go.Scatter(
        x=sps_plot['Easting (m)'],
        y=sps_plot['Northing (m)'],
        mode='markers', # 实际进度可以用线连起来，方便看轨迹
        name='实际 SPS',
        marker=dict(
            size=1,            # 点的大小大一点
            color='#00CC96',   # 亮绿色
            symbol='circle-open',
            line=dict(width=1, color='white') # 加个白边更好看
        ),
        line=dict(width=2, color='#00CC96'),
        hovertext="实际 线:" + sps_plot['Line Name'].astype(str) + " 点:" + sps_plot['Point Number'].astype(str),
        hoverinfo="text+x+y"
    ))

    # --- 3. 设置布局 ---
    fig.update_layout(
        title="设计 vs 实际 展点对比图",
        xaxis_title="X 坐标",
        yaxis_title="Y 坐标",
        # 保持 1:1 比例，防止地图变形
        yaxis=dict(scaleanchor="x", scaleratio=1, gridcolor='rgba(240,240,240,1)'),
        xaxis=dict(gridcolor='rgba(240,240,240,1)'),
        plot_bgcolor='white',
        height=400,
        legend=dict(x=0, y=1, bgcolor='rgba(255,255,255,0.5)') # 图例放在左上角
    )

    return fig


def plot_progress_pie(completed, total):
    # 计算剩余点数
    remaining = max(0, total - completed)

    fig = go.Figure(data=[go.Pie(
        labels=['已完成', '剩余待办'],
        values=[completed, remaining],
        hole=.6,  # 环形设计，更现代
        marker_colors=["#00CC96", "#EEEEEE"],  # 绿色和浅灰色
        textinfo='percent',
        showlegend=False,  # 侧边栏较窄，隐藏图例以节省空间
        domain=dict(x=[0, 0.0])
    )])

    fig.update_layout(
        margin=dict(t=0, b=0, l=10, r=10),
        height=150,
        annotations=[dict(text=f'进度', x=0.5, y=0.5, font_size=16, showarrow=False)]
    )
    return fig