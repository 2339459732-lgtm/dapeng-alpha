#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档关系图谱的「图算法层」——纯 stdlib，供 ``tools/gen_docs_graph.py`` 调用。

为什么把算法搬到 Python
----------------------
原先前端 ``static/js/docs-graph.js`` 在浏览器里做三件事：

1. **力导向布局**（Fruchterman–Reingold 变体，420 轮 × 31² 斥力对 ≈ 40 万次浮点
   运算），每次打开文档页都要重算一遍；
2. **节点尺寸/透明度**只用 ``degree`` 一个指标；
3. **没有任何聚类/中心性**，所以「哪几篇是一个主题」「哪几篇是桥」看不出来。

把这三件事移到 Python 后：

- 布局**一次算好写进 JSON**，浏览器只做 ``layout:'none'`` 渲染 → 首屏不再有
  40 万次运算，且**跨设备像素级一致**（不再依赖浏览器浮点实现差异）；
- 尺寸可以按 **PageRank** 而不是裸度数（枢纽节点的「被引用重要性」更准）；
- 新增 **社区发现**（CNM 贪心模块度）+ **介数中心性**，得到「群落光环」和
  「桥接节点」两种以前画不出来的视觉层次。

算法与复杂度（n≈31，全部毫秒级）
--------------------------------
========================  ==========================================  =========
函数                        算法                                         复杂度
========================  ==========================================  =========
``pagerank``              幂迭代（damping=0.85，含悬挂点处理）           O(iters·E)
``betweenness``           Brandes（无向无权，精确解）                    O(V·E)
``greedy_modularity``     Clauset–Newman–Moore 贪心合并                  O(V³)
``connected_components``   BFS                                            O(V+E)
``diameter``               全源 BFS（取最大连通分量）                     O(V·E)
``average_clustering``     三角形计数                                     O(V·d²)
``convex_hull``           Andrew monotone chain                          O(k log k)
``compute_layout``        FR + 分组凝聚力 + 碰撞松弛（与前端同参）        O(iters·V²)
========================  ==========================================  =========

确定性
------
布局初值用 **FNV-1a(节点 id)** 派生抖动，与前端 ``hash()`` 完全同构，
因此同一份数据在任何机器、任何次运行都得到相同坐标；不依赖 ``random``，
也就没有「每次刷新图都变形」的问题。
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 与前端共享的布局常量（改这里必须同步 static/js/docs-graph.js）
# ---------------------------------------------------------------------------

LAYOUT_W: int = 1000
LAYOUT_H: int = 660
LAYOUT_ALGORITHM: str = "fr-cohesion-v1"

# 分组凝聚力权重。太小看不出簇；太大会把组压成球、组间连线拉成长放射状。
COHESION: float = 0.26

# 斥力系数（area/n 的平方根再乘此值）
REPULSION: float = 1.26

# 碰撞松弛：节点之间的最小间隙（像素）
COLLISION_GAP: float = 10.0
COLLISION_PASSES: int = 80


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _hash01(s: str) -> float:
    """FNV-1a → [0,1]。与前端 ``docs-graph.js`` 的 ``hash()`` 同构。"""
    h = 2166136261
    for ch in str(s):
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h / 4294967295.0


def _r2(x: float) -> float:
    """保留两位小数（与前端 ``Math.round(x*100)/100`` 一致）。"""
    return math.floor(x * 100 + 0.5) / 100


def _r1(x: float) -> float:
    """保留一位小数（与前端 ``Math.round(x*10)/10`` 一致）。"""
    return math.floor(x * 10 + 0.5) / 10


def build_adjacency(
    nodes: Sequence[Dict[str, Any]],
    links: Sequence[Dict[str, Any]],
) -> Tuple[List[str], Dict[str, Tuple[str, ...]]]:
    """构建无向邻接表。自环与指向未知节点的边被丢弃（与前端一致）。

    **返回的邻居是排序后的元组，不是 set。**
    这不是风格问题：Python 对字符串的 hash 默认带随机种子
    （``PYTHONHASHSEED``），因此 ``set`` 的迭代顺序**每个进程都不同**。
    而本模块多处依赖迭代顺序做「同分时取先遇到的那个」的决策：

      · ``greedy_modularity`` 在 ΔQ 相同的候选对里取第一个 → 社区划分会变；
      · ``betweenness`` 的浮点累加顺序变化 → 末位数字抖动；
      · ``compute_layout`` 的边序变化 → 坐标微移。

    实测后果：同一份 docs-graph 连续生成两次，社区规模从 14/7/6/4 变成
    12/9/6/4，图谱外观跟着变 —— 直接违背「预计算坐标 = 稳定可复现」的初衷。
    改为排序元组后，任何机器、任何次运行结果完全一致。
    """
    ids: List[str] = [str(n["id"]) for n in nodes]
    known = set(ids)
    nbrs: Dict[str, set] = {i: set() for i in ids}
    for link in links:
        s, t = str(link.get("source", "")), str(link.get("target", ""))
        if s == t or s not in known or t not in known:
            continue
        nbrs[s].add(t)
        nbrs[t].add(s)
    adj: Dict[str, Tuple[str, ...]] = {i: tuple(sorted(v)) for i, v in nbrs.items()}
    return ids, adj


# ---------------------------------------------------------------------------
# 中心性
# ---------------------------------------------------------------------------

def pagerank(
    adj: Dict[str, Tuple[str, ...]],
    damping: float = 0.85,
    iterations: int = 100,
    tolerance: float = 1e-10,
) -> Dict[str, float]:
    """PageRank 幂迭代。

    无向图的 PageRank 与「特征向量中心性」高度相关，但收敛更稳，且天然处理
    悬挂点（度数为 0 的节点把权重均摊给全图，而不是凭空蒸发）。
    归一化后总和为 1，便于前端直接当作 0–1 的「重要度」使用。
    """
    ids = list(adj.keys())
    n = len(ids)
    if n == 0:
        return {}
    out_deg = {i: len(adj[i]) for i in ids}
    dangling = [i for i in ids if out_deg[i] == 0]
    rank = {i: 1.0 / n for i in ids}

    for _ in range(iterations):
        base = (1.0 - damping) / n
        # 悬挂点把自身权重均分给所有节点
        dangling_sum = sum(rank[i] for i in dangling)
        nxt = {i: base + damping * dangling_sum / n for i in ids}
        for i in ids:
            if out_deg[i] == 0:
                continue
            share = damping * rank[i] / out_deg[i]
            for j in adj[i]:
                nxt[j] += share
        delta = sum(abs(nxt[i] - rank[i]) for i in ids)
        rank = nxt
        if delta < tolerance:
            break

    total = sum(rank.values()) or 1.0
    return {i: rank[i] / total for i in ids}


def betweenness(adj: Dict[str, Tuple[str, ...]]) -> Dict[str, float]:
    """Brandes 介数中心性（无向无权，精确解）。

    介数衡量「多少条最短路径必须经过我」——即**桥接**程度。文档图谱里，
    介数高的往往不是引用最多的那篇，而是把「架构 / 运维 / 数据」几个群落
    缝在一起的那篇，视觉上值得单独标出来。
    """
    ids = list(adj.keys())
    cb = {i: 0.0 for i in ids}
    for s in ids:
        stack: List[str] = []
        pred: Dict[str, List[str]] = {i: [] for i in ids}
        sigma = {i: 0.0 for i in ids}
        sigma[s] = 1.0
        dist = {i: -1 for i in ids}
        dist[s] = 0
        queue = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = {i: 0.0 for i in ids}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                cb[w] += delta[w]

    # 无向图每条最短路径被正反各数一次
    for i in ids:
        cb[i] /= 2.0

    # 归一化到 [0,1]：无向图最大值为 (n-1)(n-2)/2
    n = len(ids)
    denom = ((n - 1) * (n - 2) / 2.0) if n > 2 else 1.0
    return {i: (cb[i] / denom if denom else 0.0) for i in ids}


# ---------------------------------------------------------------------------
# 连通性
# ---------------------------------------------------------------------------

def connected_components(adj: Dict[str, Tuple[str, ...]]) -> List[List[str]]:
    """BFS 求连通分量，按规模降序返回。"""
    seen = set()
    comps: List[List[str]] = []
    for start in adj:
        if start in seen:
            continue
        comp: List[str] = []
        queue = deque([start])
        seen.add(start)
        while queue:
            v = queue.popleft()
            comp.append(v)
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    queue.append(w)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    return comps


def _bfs_far(adj: Dict[str, Tuple[str, ...]], start: str) -> Tuple[str, int]:
    """从 start 出发的 BFS，返回 (最远节点, 距离)。"""
    dist = {start: 0}
    queue = deque([start])
    far, far_d = start, 0
    while queue:
        v = queue.popleft()
        for w in adj[v]:
            if w not in dist:
                dist[w] = dist[v] + 1
                if dist[w] > far_d:
                    far, far_d = w, dist[w]
                queue.append(w)
    return far, far_d


def diameter(adj: Dict[str, Tuple[str, ...]]) -> Tuple[int, str, str]:
    """最大连通分量的直径（全源 BFS，n 很小直接精确求）。

    先做两次 BFS（找近似直径端点）再对端点做一次全源 BFS 校正，
    避免对每个节点都跑一遍。
    """
    comps = connected_components(adj)
    if not comps:
        return 0, "", ""
    main = set(comps[0])
    if len(main) == 1:
        node = next(iter(main))
        return 0, node, node
    sub = {i: tuple(sorted(j for j in adj[i] if j in main)) for i in main}
    a, _ = _bfs_far(sub, next(iter(main)))
    b, _ = _bfs_far(sub, a)
    best, pa, pb = 0, a, b
    for s in main:
        far, d = _bfs_far(sub, s)
        if d > best:
            best, pa, pb = d, s, far
    return best, pa, pb


def average_clustering(adj: Dict[str, Tuple[str, ...]]) -> float:
    """平均聚类系数（局部系数取均值）。衡量「邻居之间也互相认识」的程度。"""
    if not adj:
        return 0.0
    total = 0.0
    counted = 0
    for v, nbrs in adj.items():
        d = len(nbrs)
        if d < 2:
            continue
        links = 0
        nbr_list = list(nbrs)
        for i in range(d):
            for j in range(i + 1, d):
                if nbr_list[j] in adj[nbr_list[i]]:
                    links += 1
        total += 2.0 * links / (d * (d - 1))
        counted += 1
    return (total / counted) if counted else 0.0


# ---------------------------------------------------------------------------
# 社区发现（CNM 贪心模块度）
# ---------------------------------------------------------------------------

def modularity(adj: Dict[str, Tuple[str, ...]], membership: Dict[str, int]) -> float:
    """给定划分的模块度 Q。"""
    m = sum(len(v) for v in adj.values()) / 2.0
    if m <= 0:
        return 0.0
    internal: Dict[int, float] = defaultdict(float)
    degree_sum: Dict[int, float] = defaultdict(float)
    for v, nbrs in adj.items():
        c = membership.get(v, -1)
        degree_sum[c] += len(nbrs)
        for w in nbrs:
            if v < w and membership.get(w, -2) == c:
                internal[c] += 1.0
    q = 0.0
    for c, dsum in degree_sum.items():
        q += internal[c] / m - (dsum / (2.0 * m)) ** 2
    return q


def greedy_modularity(adj: Dict[str, Tuple[str, ...]]) -> Dict[str, int]:
    """Clauset–Newman–Moore 贪心社区合并。

    每轮选出使模块度增量 ΔQ 最大的两个社区合并，直到不存在 ΔQ>0 的合并。
    ΔQ(i,j) = e_ij/m − a_i·a_j/(2m²)，其中 e_ij 为两社区间边数、
    a_i 为社区 i 的度数总和、m 为总边数。

    n=31 时 O(V³) 也在毫秒级，因此不做堆优化——代码可读性优先。
    返回 ``{node_id: community_index}``，社区索引按规模降序重编号（0 最大）。
    """
    ids = list(adj.keys())
    m = sum(len(v) for v in adj.values()) / 2.0
    if m <= 0:
        return {i: 0 for i in ids}

    comm: Dict[str, int] = {i: idx for idx, i in enumerate(ids)}
    a: Dict[int, float] = {idx: float(len(adj[i])) for idx, i in enumerate(ids)}

    # 社区对之间的边数（键统一为 (小, 大)）
    between: Dict[Tuple[int, int], float] = defaultdict(float)
    for i in ids:
        ci = comm[i]
        for j in adj[i]:
            cj = comm[j]
            if ci < cj:
                between[(ci, cj)] += 1.0

    while True:
        best_pair: Optional[Tuple[int, int]] = None
        best_dq = 1e-12
        for (ci, cj), eij in between.items():
            dq = eij / m - (a[ci] * a[cj]) / (2.0 * m * m)
            if dq > best_dq:
                best_dq, best_pair = dq, (ci, cj)
        if best_pair is None:
            break

        ci, cj = best_pair
        a[ci] += a[cj]
        a.pop(cj, None)

        merged: Dict[Tuple[int, int], float] = defaultdict(float)
        for (x, y), v in between.items():
            nx = ci if x in (ci, cj) else x
            ny = ci if y in (ci, cj) else y
            if nx == ny:
                continue                      # 合并后成为社区内部边，不再参与 ΔQ
            key = (nx, ny) if nx < ny else (ny, nx)
            merged[key] += v
        between = merged

        for node, c in comm.items():
            if c == cj:
                comm[node] = ci

    # 按规模降序重编号，让「0 号社区」稳定是最大的那个
    sizes: Dict[int, int] = defaultdict(int)
    for c in comm.values():
        sizes[c] += 1
    order = sorted(sizes, key=lambda c: (-sizes[c], c))
    remap = {c: idx for idx, c in enumerate(order)}
    return {node: remap[c] for node, c in comm.items()}


def convex_hull(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Andrew monotone chain 凸包（逆时针）。少于 3 点直接返回去重后的点。"""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------------------
# 布局
# ---------------------------------------------------------------------------

def _size_of(deg: int, max_deg: int) -> float:
    """节点半径：与前端 ``sizeOf()`` 完全一致（幂函数连续映射）。"""
    if deg <= 0:
        return 13.0
    m = max_deg if max_deg > 0 else 1
    r = max(0.0, min(1.0, deg / m))
    return _r1(16 + 20 * (r ** 0.62))


def _collision_relax(pos: Dict[str, List[float]], ids: Sequence[str], adj: Dict[str, Tuple[str, ...]],
                     w: int, h: int, passes: int = COLLISION_PASSES) -> None:
    """碰撞松弛：只消除重叠，不改整体结构。就地修改 ``pos``。"""
    n = len(ids)
    max_deg = max((len(adj[i]) for i in ids), default=1)
    for _ in range(passes):
        moved = False
        for a_idx in range(n):
            ia = ids[a_idx]
            pa = pos[ia]
            for b_idx in range(a_idx + 1, n):
                ib = ids[b_idx]
                pb = pos[ib]
                need = (_size_of(len(adj[ia]), max_deg) + _size_of(len(adj[ib]), max_deg)) / 2 + COLLISION_GAP
                ddx = pb[0] - pa[0]
                ddy = pb[1] - pa[1]
                dd = math.hypot(ddx, ddy) or 0.01
                if dd >= need:
                    continue
                push = (need - dd) / 2
                ux, uy = ddx / dd, ddy / dd
                pa[0] -= ux * push
                pa[1] -= uy * push
                pb[0] += ux * push
                pb[1] += uy * push
                moved = True
        if not moved:
            break
    for i in ids:
        pos[i][0] = max(24.0, min(w - 24.0, pos[i][0]))
        pos[i][1] = max(24.0, min(h - 24.0, pos[i][1]))


def _relax_in_disc(
    pos: Dict[str, List[float]],
    mem: Sequence[str],
    adj: Dict[str, Tuple[str, ...]],
    cx: float,
    cy: float,
    R: float,
    iters: int = 90,
) -> None:
    """在一个圆盘内做受限力导向（只算社区内部）。

    为什么需要：把全局 FR 的某个社区等比缩进圆盘时，会**原样保留它的长宽比**。
    实测「架构与设计」在全局布局里是一条 713×78 的横带，缩进半径 106 的圆盘后
    仍是「一排点」，既不像团块也不好看。这里改为：

      1. 先用向日葵（Vogel）螺旋把成员铺成均匀圆盘，按度数降序 → 枢纽居中；
      2. 再在圆盘内跑短程力导向（社区内斥力 + 社区内弹簧 + 向心），
         每步把节点夹回圆盘内。

    结果：团块是圆的、密度均匀，且社区内部的连边结构仍然决定相对位置
    （连得多的靠近），比单纯几何排布有信息量。
    """
    n = len(mem)
    if n <= 1:
        return
    GOLDEN = 2.399963229728653          # 黄金角
    # 1) 向日葵初始化：度数高的排前面 → 落在中心
    ordered = sorted(mem, key=lambda i: (-len(adj[i]), i))
    for idx, i in enumerate(ordered):
        rr = R * 0.62 * math.sqrt((idx + 0.5) / n)
        th = idx * GOLDEN
        pos[i] = [cx + math.cos(th) * rr, cy + math.sin(th) * rr]

    inner = set(mem)
    edges = [(s, t) for s in mem for t in adj[s] if t in inner and s < t]
    k = R * 0.9                          # 该尺度下的理想边长
    disp: Dict[str, List[float]] = {i: [0.0, 0.0] for i in mem}

    for it in range(iters):
        t = (1 - it / iters) * (k * 0.35) + 0.3
        for i in mem:
            disp[i][0] = 0.0
            disp[i][1] = 0.0
        # 社区内斥力
        for a_idx in range(n):
            a = mem[a_idx]
            pa = pos[a]
            for b_idx in range(a_idx + 1, n):
                b = mem[b_idx]
                pb = pos[b]
                dx = pa[0] - pb[0]
                dy = pa[1] - pb[1]
                d2 = dx * dx + dy * dy
                if d2 < 0.01:
                    dx = (_hash01(a + b) - 0.5) * 2
                    dy = (_hash01(b + a) - 0.5) * 2
                    d2 = dx * dx + dy * dy or 0.01
                d = math.sqrt(d2)
                f = (k * k) / d
                ux, uy = dx / d, dy / d
                disp[a][0] += ux * f
                disp[a][1] += uy * f
                disp[b][0] -= ux * f
                disp[b][1] -= uy * f
        # 社区内弹簧
        for s, tt in edges:
            ps, pt = pos[s], pos[tt]
            dx = ps[0] - pt[0]
            dy = ps[1] - pt[1]
            d = math.sqrt(dx * dx + dy * dy) or 0.01
            f = (d * d) / k
            ux, uy = dx / d, dy / d
            disp[s][0] -= ux * f
            disp[s][1] -= uy * f
            disp[tt][0] += ux * f
            disp[tt][1] += uy * f
        # 向心 + 夹回圆盘
        for i in mem:
            p0 = pos[i]
            disp[i][0] += (cx - p0[0]) * 0.05
            disp[i][1] += (cy - p0[1]) * 0.05
            dd = math.hypot(disp[i][0], disp[i][1]) or 1.0
            step = min(dd, t)
            p0[0] += (disp[i][0] / dd) * step
            p0[1] += (disp[i][1] / dd) * step
            ox = p0[0] - cx
            oy = p0[1] - cy
            od = math.hypot(ox, oy)
            if od > R:
                p0[0] = cx + ox / od * R
                p0[1] = cy + oy / od * R


def compact_by_community(
    pos: Dict[str, List[float]],
    ids: Sequence[str],
    comm: Dict[str, int],
    adj: Dict[str, Tuple[str, ...]],
    w: int = LAYOUT_W,
    h: int = LAYOUT_H,
    radius_unit: float = 40.0,
) -> None:
    """把「全局力导向结果」压成「若干紧致社区圆盘」（就地修改 pos）。

    为什么需要这一步
    ----------------
    单纯给 FR 加「向社区质心收拢」的力，实测几乎无效：把 COHESION 从 0.26 拉到
    1.10，社区内平均两两距离只从 320 降到 293，而节点仍被斥力顶到画布边界
    （横向跨度 952 / 1000）。原因是斥力 ``k²/d`` 的量级远大于线性凝聚力，
    想靠调权重压紧，只能把斥力调到点与点贴在一起。

    因此改成**两段式**：
      1. 全局 FR 先决定「社区之间的相对方位」（谁在左上、谁在右下）；
      2. 每个社区的质心按原方位重排到一条椭圆上，成员按原构型缩放进
         半径 ``radius_unit·√size`` 的圆盘里；
      3. 再跑碰撞松弛消除重叠（允许轻微溢出圆盘，但不破坏团块）。

    半径取 ``34·√size``：14 个节点 → 127px，7 个 → 90px，4 个 → 68px，
    四个圆盘加起来约占画布 27% 面积，既不拥挤也不散开。
    """
    members: Dict[int, List[str]] = defaultdict(list)
    for i in ids:
        members[comm[i]].append(i)
    comms = sorted(members, key=lambda c: (-len(members[c]), c))
    k = len(comms)
    if k == 0:
        return

    # 1) 新椭圆上的目标质心
    #    按**可用空间**分配方位，而不是沿用全局 FR 的原始角度：
    #    画布是 1000×660 的宽幅，横轴可容半径 500、纵轴只有 330，
    #    所以让成员最多的社区优先占横轴两端（|cos| 大的槽位），
    #    否则最大的社区会被挤在上下两侧、被画布边界裁掉（实测 R=150 的盘
    #    放在底部时纵向只剩 330-150=180，节点被迫压成一条）。
    ex, ey = w * 0.30, h * 0.32
    slots = []
    for idx in range(k):
        a = (idx / k) * math.pi * 2 - math.pi / 2
        slots.append((abs(math.cos(a)), a))     # |cos| 越大 → 越靠横轴 → 空间越足
    slots.sort(key=lambda s: -s[0])
    # 社区按规模降序（comms 已排序），依次占用空间最足的槽位
    target: Dict[int, Tuple[float, float]] = {}
    for idx, c in enumerate(comms):
        a = slots[idx][1]
        target[c] = (w / 2.0 + math.cos(a) * ex, h / 2.0 + math.sin(a) * ey)

    # 3) 各社区成员：向日葵初始化 + 圆盘内受限力导向
    #    不再「等比缩放全局 FR 的构型」—— 那会把扁平形状原样保留
    #    （实测「架构与设计」全局是 713×78 的横带，缩进圆盘后仍是一排点）。
    for c, mem in members.items():
        if not mem:
            continue
        tx, ty = target[c]
        if len(mem) == 1:
            pos[mem[0]] = [tx, ty]
            continue
        R = radius_unit * math.sqrt(len(mem))
        _relax_in_disc(pos, mem, adj, tx, ty, R)

    # 4) 消除重叠（允许轻微溢出圆盘，但不破坏团块）
    _collision_relax(pos, ids, adj, w, h, passes=COLLISION_PASSES)


def compute_layout(
    ids: Sequence[str],
    groups: Dict[str, str],
    adj: Dict[str, Tuple[str, ...]],
    w: int = LAYOUT_W,
    h: int = LAYOUT_H,
    cohesion_of: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Tuple[float, float]], int]:
    """Fruchterman–Reingold + 分组凝聚力 + 碰撞松弛。

    与前端 ``computeLayout()`` 同参同序，因此结果可直接替换前端计算，
    视觉风格不变、但跨设备一致。返回 ``(坐标表, 实际迭代轮数)``。

    :param groups:      ``{node_id: group}``，只用于**初始扇区分布**（保留目录直觉）。
    :param cohesion_of: ``{node_id: 凝聚力键}``，决定迭代中向哪个质心收拢。
                        默认等于 ``groups``（按目录聚拢）；v26 起传入**社区 id**，
                        这样「同一社区」的节点在空间上真的挨在一起，
                        凸包光环才是紧致的团块，而不是横跨半个画布的大多边形。
    """
    n = len(ids)
    if n == 0:
        return {}, 0
    coh = cohesion_of or groups

    cx, cy = w / 2.0, h / 2.0
    # 画布是宽幅，正圆半径只能按短边取 → 横向会缩在中间。横竖分别给半径。
    rx = w * 0.30
    ry = min(h * 0.40, rx * 0.92)

    # 分组顺序 = 首次出现顺序（与前端 indexOf 语义一致）
    group_order: List[str] = []
    for i in ids:
        g = coh.get(i, "")
        if g not in group_order:
            group_order.append(g)

    pos: Dict[str, List[float]] = {}
    for i in ids:
        g = coh.get(i, "")
        gi = max(0, group_order.index(g))
        sector = (gi / max(1, len(group_order))) * math.pi * 2
        j1, j2 = _hash01(i + "a"), _hash01(i + "b")
        ang = sector + (j1 - 0.5) * 0.9
        jr = 0.45 + 0.60 * j2
        pos[i] = [cx + math.cos(ang) * rx * jr, cy + math.sin(ang) * ry * jr]

    edges = [(s, t) for s in adj for t in adj[s] if s < t]
    area = float(w * h)
    k = math.sqrt(area / n) * REPULSION
    iters = min(420, 220 + n * 4)
    disp: Dict[str, List[float]] = {i: [0.0, 0.0] for i in ids}

    for it in range(iters):
        t = (1 - it / iters) * (k * 0.55) + 0.4
        for i in ids:
            disp[i][0] = 0.0
            disp[i][1] = 0.0

        # 分组质心（本轮迭代开始时的位置）
        g_sum: Dict[str, List[float]] = {}
        g_cnt: Dict[str, int] = {}
        for i in ids:
            g = coh.get(i, "")
            acc = g_sum.setdefault(g, [0.0, 0.0])
            acc[0] += pos[i][0]
            acc[1] += pos[i][1]
            g_cnt[g] = g_cnt.get(g, 0) + 1

        # 斥力 k²/d
        for a_idx in range(n):
            a = ids[a_idx]
            pa = pos[a]
            for b_idx in range(a_idx + 1, n):
                b = ids[b_idx]
                pb = pos[b]
                dx = pa[0] - pb[0]
                dy = pa[1] - pb[1]
                d2 = dx * dx + dy * dy
                if d2 < 0.01:
                    dx = (_hash01(a + b) - 0.5) * 2
                    dy = (_hash01(b + a) - 0.5) * 2
                    d2 = dx * dx + dy * dy or 0.01
                d = math.sqrt(d2)
                f = (k * k) / d
                ux, uy = dx / d, dy / d
                disp[a][0] += ux * f
                disp[a][1] += uy * f
                disp[b][0] -= ux * f
                disp[b][1] -= uy * f

        # 弹簧 d²/k
        for s, tt in edges:
            ps, pt = pos[s], pos[tt]
            dx = ps[0] - pt[0]
            dy = ps[1] - pt[1]
            d = math.sqrt(dx * dx + dy * dy) or 0.01
            f = (d * d) / k
            ux, uy = dx / d, dy / d
            disp[s][0] -= ux * f
            disp[s][1] -= uy * f
            disp[tt][0] += ux * f
            disp[tt][1] += uy * f

        # 向心 + 分组凝聚力 + 退火步长
        for i in ids:
            p0 = pos[i]
            disp[i][0] += (cx - p0[0]) * 0.06
            disp[i][1] += (cy - p0[1]) * 0.06
            g = coh.get(i, "")
            if g_cnt.get(g, 0) > 1:
                gx = g_sum[g][0] / g_cnt[g]
                gy = g_sum[g][1] / g_cnt[g]
                disp[i][0] += (gx - p0[0]) * COHESION
                disp[i][1] += (gy - p0[1]) * COHESION
            dd = math.hypot(disp[i][0], disp[i][1]) or 1.0
            step = min(dd, t)
            p0[0] += (disp[i][0] / dd) * step
            p0[1] += (disp[i][1] / dd) * step
            p0[0] = max(24.0, min(w - 24.0, p0[0]))
            p0[1] = max(24.0, min(h - 24.0, p0[1]))

    # 碰撞松弛：只消除重叠，不改结构
    _collision_relax(pos, ids, adj, w, h)

    out = {i: (_r2(pos[i][0]), _r2(pos[i][1])) for i in ids}
    return out, iters


# ---------------------------------------------------------------------------
# 总装：把算法结果写回 payload
# ---------------------------------------------------------------------------

def enrich(
    payload: Dict[str, Any],
    group_labels: Optional[Dict[str, str]] = None,
    bridge_top_ratio: float = 0.18,
) -> Dict[str, Any]:
    """把中心性 / 社区 / 坐标写入 ``payload``（就地修改并返回）。

    只**追加**字段，不改动既有字段语义 —— 老前端忽略新字段也能正常跑。
    """
    nodes: List[Dict[str, Any]] = payload.get("nodes") or []
    links: List[Dict[str, Any]] = payload.get("links") or []
    labels = group_labels or {}
    if not nodes:
        payload["layout"] = {"w": LAYOUT_W, "h": LAYOUT_H, "algorithm": LAYOUT_ALGORITHM,
                             "iterations": 0, "node_count": 0}
        payload["metrics"] = {}
        payload["communities"] = []
        return payload

    ids, adj = build_adjacency(nodes, links)
    degree = {i: len(adj[i]) for i in ids}
    pr = pagerank(adj)
    bc = betweenness(adj)
    comm = greedy_modularity(adj)
    comps = connected_components(adj)
    diam, diam_a, diam_b = diameter(adj)
    q = modularity(adj, comm)
    clustering = average_clustering(adj)

    groups = {str(n["id"]): str(n.get("group") or "root") for n in nodes}
    # v26：凝聚力键改用**社区 id** 而不是目录。
    # 原因：社区是跨目录的（实测最大社区 14 个成员横跨 4 个目录），若按目录聚拢，
    # 同一社区的节点会散布全图，凸包光环退化成横跨半个画布的大多边形，
    # 既不好看也没有信息量。目录信息由节点颜色继续承载。
    cohesion = {i: "c%d" % comm[i] for i in ids}
    pos, iters = compute_layout(ids, groups, adj, cohesion_of=cohesion)

    # 两段式布局第二段：把全局结果压成紧致社区圆盘。
    # 只靠调凝聚力权重压不紧（实测 COHESION 0.26→1.10 只把社区内平均距离
    # 从 320 降到 293，节点仍顶到画布边界），所以用「社区质心重排 + 成员缩放进盘」。
    pos_list = {i: [pos[i][0], pos[i][1]] for i in ids}
    compact_by_community(pos_list, ids, comm, adj)
    pos = {i: (_r2(pos_list[i][0]), _r2(pos_list[i][1])) for i in ids}

    # 桥接节点：介数进入前 N% 且绝对值不为 0
    positives = sorted((v for v in bc.values() if v > 0), reverse=True)
    cut = 0.0
    if positives:
        take = max(1, int(round(len(positives) * bridge_top_ratio)))
        cut = positives[min(take, len(positives)) - 1]

    n = len(ids)
    max_deg = max(degree.values(), default=1)

    # 社区 → 成员 / 主导分组 / 凸包
    by_comm: Dict[int, List[str]] = defaultdict(list)
    for i in ids:
        by_comm[comm[i]].append(i)

    communities: List[Dict[str, Any]] = []
    for cid in sorted(by_comm):
        members = by_comm[cid]
        # 主导分组：成员数最多的 group
        gcount: Dict[str, int] = defaultdict(int)
        for m in members:
            gcount[groups.get(m, "root")] += 1
        top_group = max(gcount, key=lambda g: (gcount[g], g))
        top_count = gcount[top_group]
        # 「主导分组占比」低于 70% 说明这个社区是跨目录的，用单一分组名会误导，
        # 标记 mixed 让前端在标签上体现「混合」而不是硬套一个目录名。
        mixed = (top_count / len(members)) < 0.7
        # 社区锚点：PageRank 最高的成员 —— 比目录名更能说明「这一簇围绕什么」
        anchor = max(members, key=lambda m: (pr.get(m, 0.0), m))
        hull_pts = [pos[m] for m in members]
        hull = convex_hull(hull_pts) if len(members) >= 3 else []
        communities.append({
            "id": cid,
            "size": len(members),
            "top_group": top_group,
            "label": labels.get(top_group, top_group),
            "mixed": mixed,
            "group_mix": {g: c for g, c in sorted(gcount.items(), key=lambda kv: -kv[1])},
            "anchor": anchor,
            "members": members,
            # 凸包用于前端画「群落光环」；<3 个成员时不画（退化成一个点/一条线）
            "hull": [[_r2(x), _r2(y)] for x, y in hull],
        })

    for node in nodes:
        nid = str(node["id"])
        d = degree[nid]
        node["x"], node["y"] = pos[nid]
        node["degree"] = d
        node["pagerank"] = round(pr[nid], 6)
        node["betweenness"] = round(bc[nid], 6)
        node["community"] = comm[nid]
        node["bridge"] = bool(bc[nid] > 0 and bc[nid] >= cut)
        node["size"] = _size_of(d, max_deg)

    payload["layout"] = {
        "w": LAYOUT_W,
        "h": LAYOUT_H,
        "algorithm": LAYOUT_ALGORITHM,
        "iterations": iters,
        "node_count": n,
        "cohesion": COHESION,
    }
    payload["metrics"] = {
        "nodes": n,
        "links": len(links),
        "density": round((2.0 * len(links)) / (n * (n - 1)), 4) if n > 1 else 0.0,
        "avg_degree": round(sum(degree.values()) / n, 2) if n else 0.0,
        "max_degree": max_deg,
        "components": len(comps),
        "largest_component": len(comps[0]) if comps else 0,
        "isolated": sum(1 for v in degree.values() if v == 0),
        "diameter": diam,
        "diameter_from": diam_a,
        "diameter_to": diam_b,
        "modularity": round(q, 4),
        "communities": len(communities),
        "avg_clustering": round(clustering, 4),
    }
    payload["communities"] = communities
    return payload
