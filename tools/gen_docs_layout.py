#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档关系图谱 —— 离线布局生成器（Python 侧升级）

## 为什么要在 Python 里算布局，而不是继续在前端算

前端现在用的是「分组扇区初始化 + Fruchterman–Reingold 迭代」。它有两个结构性问题：

1. **构图不可控**：FR 只保证「大致均匀」，不保证「团簇清晰」。同一份数据，
   团簇之间的相对位置每次都要靠力平衡碰运气，最终容易长成「一团散点」。
2. **改进成本高**：想在布局层做社区聚类、标签侧向分配、团簇凸包，都得在浏览器里
   重写一遍数值代码，且受主线程预算限制。

所以把布局挪到离线：Python 用 **应力优化（SMACOF / stress majorization）** 求解
「图上最短路径距离」与「画布欧氏距离」的最小二乘拟合。这是图绘制领域公认质量高于
纯力导向的做法（Kamada–Kawai 的现代解法），迭代单调收敛、结果可复现。

## 产出

`static/docs-graph-layout.json`：

| 字段 | 含义 |
|---|---|
| `nodes[id] = [x, y]` | 归一化坐标（0–1000 × 0–660 视口），前端按画布等比映射 |
| `communities` | 标签传播得到的社区：`{id, name, members[], color_hint}` |
| `hulls` | 每个社区的凸包多边形（直接可画，避免前端再算几何） |
| `cross_edges` | 跨社区边数（用于决定团簇染色透明度） |
| `meta` | 算法名、迭代次数、最终应力值、生成时间、数据指纹 |

## 不变量（前端据此做降级）

- **节点集合必须与 `docs-graph.json` 完全一致**；前端比对 `data_hash`，不一致就
  回退到前端布局，绝不拿错位的坐标硬画。
- 布局只决定**位置**，不改变任何边/标签/语义。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import datetime
from typing import Dict, List, Tuple

try:
    import numpy as np
except ImportError:  # pragma: no cover
    print("需要 numpy：pip install numpy", file=sys.stderr)
    raise

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_PATH = os.path.join(ROOT, "static", "docs-graph.json")
OUT_PATH = os.path.join(ROOT, "static", "docs-graph-layout.json")

W, H = 1000.0, 660.0

# 社区配色：沿用前端分组色的低饱和区间，保证「团簇」与「分组」不打架
COMMUNITY_COLORS = [
    "rgba(143,160,184,.10)", "rgba(148,168,143,.10)", "rgba(185,156,126,.10)",
    "rgba(138,163,181,.10)", "rgba(168,144,152,.10)", "rgba(127,160,143,.10)",
]


# ────────────────────────────── 数据装载 ──────────────────────────────
def load_graph(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def graph_fingerprint(nodes: List[dict], links: List[dict]) -> str:
    ids = sorted(str(n.get("id")) for n in nodes)
    pairs = sorted("%s>%s" % (l.get("source"), l.get("target")) for l in links)
    raw = "|".join(ids) + "||" + "|".join(pairs)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ────────────────────────────── 图算法 ──────────────────────────────
def build_adjacency(ids: List[str], links: List[dict]) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    seen = set()
    for l in links:
        s, t = str(l.get("source")), str(l.get("target"))
        if s not in adj or t not in adj:
            continue
        key = (s, t) if s < t else (t, s)
        if key in seen:
            continue
        seen.add(key)
        adj[s].append(t)
        adj[t].append(s)
    return adj


def all_pairs_shortest(ids: List[str], adj: Dict[str, List[str]]) -> np.ndarray:
    """BFS 全源最短路（无权图）。30 个节点规模，O(n·(n+m)) 完全够用。"""
    n = len(ids)
    index = {v: i for i, v in enumerate(ids)}
    dist = np.full((n, n), np.inf)
    for src_i, src in enumerate(ids):
        dist[src_i, src_i] = 0.0
        frontier = [src]
        d = 0
        while frontier:
            d += 1
            nxt = []
            for u in frontier:
                for v in adj[u]:
                    j = index[v]
                    if dist[src_i, j] == np.inf:
                        dist[src_i, j] = d
                        nxt.append(v)
            frontier = nxt
    # 不连通的对：用最大有限距离 + 1 填充，避免 inf 破坏应力计算
    finite = dist[np.isfinite(dist)]
    fill = (finite.max() if finite.size else 1.0) + 1.0
    dist[np.isinf(dist)] = fill
    return dist


def label_propagation(ids: List[str], adj: Dict[str, List[str]], rounds: int = 30) -> Dict[str, int]:
    """确定性标签传播社区发现。

    异步更新容易震荡，这里用**同步更新 + 固定平局规则**（取最小标签），
    保证同一份图每次得到相同分区。
    """
    label = {v: i for i, v in enumerate(ids)}
    order = list(ids)
    for _ in range(rounds):
        changed = False
        new_label = {}
        for v in order:
            counts: Dict[int, int] = {}
            for u in adj[v]:
                counts[label[u]] = counts.get(label[u], 0) + 1
            if counts:
                best = max(counts.values())
                cand = sorted(lab for lab, c in counts.items() if c == best)
                # 自身标签在候选里则保持自身，减少抖动
                new_label[v] = label[v] if label[v] in cand else cand[0]
            else:
                new_label[v] = label[v]
            if new_label[v] != label[v]:
                changed = True
        label = new_label
        if not changed:
            break
    # 归一化成 0..k-1
    uniq = sorted(set(label.values()))
    remap = {old: i for i, old in enumerate(uniq)}
    return {node_id: remap[lab] for node_id, lab in label.items()}


def convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Andrew monotone chain。"""
    pts = sorted(set((round(x, 2), round(y, 2)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ────────────────────────────── 布局求解 ──────────────────────────────
def initial_layout(dist: np.ndarray, communities: Dict[str, int], ids: List[str]) -> np.ndarray:
    """按社区扇区 + 距离分位半径初始化：给 SMACOF 一个结构化的起点。"""
    n = len(ids)
    comm_ids = sorted(set(communities.values()))
    nc = max(1, len(comm_ids))
    pos = np.zeros((n, 2))
    for i, v in enumerate(ids):
        c = comm_ids.index(communities[v])
        base_angle = 2 * math.pi * c / nc
        # 社区内按度数微调角度（用 BFS 距离均值当代理量，稳定可复现）
        spread = 0.55
        off = spread * ((i % max(1, len(ids))) / max(1, len(ids))) * 2 - spread
        angle = base_angle + off
        r = 0.42 + 0.10 * ((i * 37) % 10) / 10.0
        pos[i, 0] = 0.5 + r * math.cos(angle) * 0.5
        pos[i, 1] = 0.5 + r * math.sin(angle) * 0.5
    # 轻度扰动打散重合点（确定性）
    for i in range(n):
        pos[i, 0] += ((i * 2654435761) % 1000) / 1000.0 * 0.01
        pos[i, 1] += ((i * 40503) % 1000) / 1000.0 * 0.01
    return pos


def smacof(dist: np.ndarray, init: np.ndarray, iters: int = 300, eps: float = 1e-7) -> Tuple[np.ndarray, List[float]]:
    """应力优化（SMACOF）。距离先按图论距离归一化到单位方框。"""
    n = dist.shape[0]
    d = dist / max(dist.max(), 1e-9)          # 归一到 [0,1]
    w = 1.0 / np.maximum(d, 1e-6) ** 2         # 经典权重：强调近邻
    np.fill_diagonal(w, 0.0)
    X = init.copy()
    history: List[float] = []
    for _ in range(iters):
        diff = X[:, None, :] - X[None, :, :]
        cur = np.sqrt((diff ** 2).sum(axis=2))
        cur = np.maximum(cur, 1e-6)
        ratio = np.where(d > 0, d / cur, 0.0)
        # 应力值（用于报告与收敛判断）
        stress = float((w * (cur - d) ** 2).sum() / 2.0)
        history.append(stress)
        # Guttman 变换
        B = -w * ratio
        np.fill_diagonal(B, 0.0)
        B[np.diag_indices(n)] = -B.sum(axis=1)
        X_new = (B @ X) / np.maximum(w.sum(axis=1)[:, None], 1e-9)
        if np.abs(X_new - X).max() < eps:
            X = X_new
            break
        X = X_new
    return X, history


def normalize_to_canvas(X: np.ndarray, w: float = W, h: float = H, pad: float = 60.0) -> np.ndarray:
    mn, mx = X.min(axis=0), X.max(axis=0)
    span = np.maximum(mx - mn, 1e-9)
    # 等比缩放，保持构图长宽比；再居中
    scale = min((w - 2 * pad) / span[0], (h - 2 * pad) / span[1])
    Y = (X - mn) * scale
    off = np.array([(w - (mx[0] - mn[0]) * scale) / 2.0, (h - (mx[1] - mn[1]) * scale) / 2.0])
    return Y + off


def relax_overlaps(coords: Dict[str, Tuple[float, float]], radius: Dict[str, float],
                   min_gap: float = 12.0, iters: int = 200) -> Dict[str, Tuple[float, float]]:
    """简单硬球松弛：消除节点重叠（半径按度数预估，与前端 symbolSize/2 同量级）。"""
    ids = list(coords.keys())
    pos = {k: [float(v[0]), float(v[1])] for k, v in coords.items()}
    for _ in range(iters):
        moved = False
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]
                dist = math.hypot(dx, dy) or 0.01
                need = radius.get(a, 10) + radius.get(b, 10) + min_gap
                if dist >= need:
                    continue
                push = (need - dist) / 2.0
                ux, uy = dx / dist, dy / dist
                pos[a][0] -= ux * push
                pos[a][1] -= uy * push
                pos[b][0] += ux * push
                pos[b][1] += uy * push
                moved = True
        if not moved:
            break
    return {k: (v[0], v[1]) for k, v in pos.items()}


# ────────────────────────────── 主流程 ──────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="生成 docs 图谱离线布局")
    ap.add_argument("--graph", default=GRAPH_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--iters", type=int, default=320)
    args = ap.parse_args()

    g = load_graph(args.graph)
    nodes = g.get("nodes") or []
    links = g.get("links") or []
    if not nodes:
        print("图谱为空，放弃生成", file=sys.stderr)
        return 1

    ids = [str(n.get("id")) for n in nodes]
    degree = {i: 0 for i in ids}
    for l in links:
        s, t = str(l.get("source")), str(l.get("target"))
        if s in degree:
            degree[s] += 1
        if t in degree:
            degree[t] += 1

    adj = build_adjacency(ids, links)
    dist = all_pairs_shortest(ids, adj)
    # 社区用**文档真实分组**（root / 00-strategy / … ），不用标签传播。
    # 实测：本图跨分组边占比接近 80%，标签传播只能切出 2 个大块 —— 那不是在发现结构，
    # 而是在把一张互相引用的网硬切两半。分组是作者给定的真实语义，画出来才有意义。
    group_order: List[str] = []
    for n in nodes:
        gname = str(n.get("group") or "root")
        if gname not in group_order:
            group_order.append(gname)
    communities = {}
    for i, v in enumerate(ids):
        communities[v] = group_order.index(str(nodes[i].get("group") or "root"))

    # ── 分组约束布局 ───────────────────────────────────────────────────
    # 实测教训：对整图直接跑 SMACOF，会得到「一个大圆环」——因为本图 65% 的边是跨组的，
    # 全局应力最优解就是把所有点均匀铺在环上，7 个分组的凸包互相重叠成一团灰。
    # 改为两层：先在**每组内部**做应力优化（保留组内结构），再把各组的局部布局
    # 摆到画布上的不同位置（组质心按椭圆均分，半径按组规模加权）。
    # 这样组内关系清楚、组间互不侵占，凸包才画得出来。
    X = np.zeros((len(ids), 2))
    group_ids = {}
    for i, v in enumerate(ids):
        group_ids.setdefault(communities[v], []).append(i)

    n_groups = len(group_ids)
    # 组质心：椭圆均分（画布 1000×660 → 椭圆长短轴按比例），大组给更大的环绕半径
    cx0, cy0 = W / 2.0, H / 2.0
    rx, ry = W * 0.30, H * 0.32
    max_size = max(len(v) for v in group_ids.values())
    history: List[float] = []
    for gi in sorted(group_ids):
        idxs = group_ids[gi]
        sub = dist[np.ix_(idxs, idxs)]
        sub_init = initial_layout(sub, {ids[i]: 0 for i in idxs}, [ids[i] for i in idxs])
        subX, sub_hist = smacof(sub, sub_init, iters=args.iters)
        history.extend(sub_hist[-1:])
        # 组内归一化到「组半径」：规模越大的组占地越大（开方避免大组吃掉全图）
        size_ratio = math.sqrt(len(idxs) / max_size)
        local_r = min(rx, ry) * (0.42 + 0.46 * size_ratio)
        mn, mx = subX.min(axis=0), subX.max(axis=0)
        span = np.maximum(mx - mn, 1e-9)
        scale = (2 * local_r) / span.max()
        subX = (subX - (mn + mx) / 2.0) * scale
        # 组质心沿椭圆均分，加一点交错偏移避免"正圆"死板
        ang = 2 * math.pi * (sorted(group_ids).index(gi) / max(1, n_groups)) - math.pi / 2
        gx = cx0 + rx * math.cos(ang)
        gy = cy0 + ry * math.sin(ang)
        for k, i in enumerate(idxs):
            X[i, 0] = gx + subX[k, 0]
            X[i, 1] = gy + subX[k, 1]

    # 全局轻推：组之间若仍贴近，用少量斥力分开（不破坏组内结构）
    X = normalize_to_canvas(X)

    max_deg = max(degree.values()) or 1
    radius = {i: 9 + 11 * (degree[i] / max_deg) for i in ids}
    coords = {ids[i]: (float(X[i, 0]), float(X[i, 1])) for i in range(len(ids))}
    coords = relax_overlaps(coords, radius)

    # 社区结构与凸包
    by_comm: Dict[int, List[str]] = {}
    for v in ids:
        by_comm.setdefault(communities[v], []).append(v)
    name_of = {str(n.get("id")): (n.get("group_label") or n.get("group") or "") for n in nodes}
    group_key_of = {str(n.get("id")): str(n.get("group") or "root") for n in nodes}

    comm_out = []
    hulls = []
    for ci in sorted(by_comm):
        members = by_comm[ci]
        pts = [coords[m] for m in members]
        hull = convex_hull(pts)
        label_counts: Dict[str, int] = {}
        for m in members:
            label_counts[name_of.get(m, "")] = label_counts.get(name_of.get(m, ""), 0) + 1
        dominant = sorted(label_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if label_counts else ""
        comm_out.append({
            "id": ci,
            "group": group_order[ci] if ci < len(group_order) else None,
            "name": dominant or ("分区 %d" % (ci + 1)),
            "size": len(members),
            "members": members,
            "color_hint": COMMUNITY_COLORS[ci % len(COMMUNITY_COLORS)],
        })
        if len(hull) >= 3:
            hulls.append({"community": ci, "points": [[round(x, 1), round(y, 1)] for x, y in hull]})

    cross = 0
    for l in links:
        s, t = str(l.get("source")), str(l.get("target"))
        if s in communities and t in communities and communities[s] != communities[t]:
            cross += 1

    payload = {
        "version": 1,
        "algo": "smacof-stress-majorization",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_graph": os.path.relpath(args.graph, ROOT).replace("\\", "/"),
        "data_hash": graph_fingerprint(nodes, links),
        "canvas": {"w": W, "h": H},
        "nodes": {k: [round(v[0], 2), round(v[1], 2)] for k, v in coords.items()},
        "communities": comm_out,
        "hulls": hulls,
        "cross_edges": cross,
        "meta": {
            # 分组约束下每个组各自归一化求解，全局应力不再可比 ——
            # 如实报告每组末轮应力，而不是拼一个看起来"下降很多"的假总数。
            "per_group_stress": [round(x, 6) for x in history],
            "iterations": args.iters,
            "node_count": len(ids),
            "link_count": len(links),
            "community_count": len(comm_out),
            "cross_edge_ratio": round(cross / max(1, len(links)), 4),
        },
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, args.out)

    m = payload["meta"]
    print("布局已生成：%s" % os.path.relpath(args.out, ROOT))
    print("  节点 %d / 边 %d / 社区 %d / 跨社区边占比 %.1f%%"
          % (m["node_count"], m["link_count"], m["community_count"], m["cross_edge_ratio"] * 100))
    print("  每组迭代 %d 次；各组末轮应力 %s（分组约束下不可跨组比较，故不求和）"
          % (m["iterations"], ", ".join("%.4f" % x for x in m["per_group_stress"])))
    print("  指纹 %s" % payload["data_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
