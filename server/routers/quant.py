# -*- coding: utf-8 -*-
"""量化回测路由模块 — 集成进 DAPENG Alpha 服务。

提供回测任务管理、SSE 实时日志推送和结果查询接口。
所有路由以 /api/quant 为前缀，避免与现有路由冲突。
"""

import asyncio
import json
import re
import threading
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from server import quant_core

router = APIRouter(prefix="/api/quant", tags=["quant"])

# ──────────────────────────────────────────────────────────
# 全局状态存储
# ──────────────────────────────────────────────────────────
tasks: Dict[str, Dict[str, Any]] = {}

current_config: Dict[str, Any] = {
    "TUSHARE_TOKEN": "",
    "PRICE_LOW": 5.0,
    "PRICE_HIGH": 36.0,
    "INDEX_CODES": ["000300.SH", "000905.SH"],
    "MAX_STOCKS": 80,
    "START": "2018-01-01",
    "END": None,
    "HORIZON": 10,
    "BUY_TH": 0.02,
    "SELL_TH": -0.02,
    "MIN_PROBA": 0.55,
    "THETA_THRESHOLD": 0.10,
    "BETA_SIZE": 0.30,
    "TRAIN_MIN_YEARS": 3,
    "STEP_DAYS": 20,
    "TOPK": 5,
    "TCOST": 0.001,
    "SEED": 42,
}

_PROGRESS_RE = re.compile(r"\[PROGRESS:(\d+)\]")


def _parse_progress(msg: str) -> Optional[int]:
    m = _PROGRESS_RE.search(msg)
    return int(m.group(1)) if m else None


def _run_backtest_thread(task_id: str, config: dict) -> None:
    task = tasks[task_id]
    task["status"] = "running"
    task["progress"] = 0

    def log_callback(msg: str) -> None:
        task["logs"].append(str(msg))
        progress = _parse_progress(str(msg))
        if progress is not None:
            task["progress"] = progress

    try:
        result = quant_core.run_backtest(config=config, log_callback=log_callback)
        task["result"] = result
        task["status"] = "completed"
        task["progress"] = 100
        task["logs"].append("[DONE] 回测任务完成")
    except Exception as exc:
        import traceback
        task["status"] = "error"
        task["error"] = str(exc)
        task["logs"].append(f"[ERROR] 回测失败: {exc}")
        task["logs"].append(traceback.format_exc())


# ──────────────────────────────────────────────────────────
# API 路由
# ──────────────────────────────────────────────────────────

@router.get("/config")
async def get_config() -> JSONResponse:
    """读取回测默认参数（注意：该组接口为早期直返 JSON，未使用三键信封）"""
    return JSONResponse(content=current_config)


@router.post("/config")
async def update_config(request: Request) -> JSONResponse:
    """更新回测默认参数并落内存（同样为直返 JSON）"""
    body = await request.json()
    current_config.update(body)
    return JSONResponse(content={"ok": True, "config": current_config})


@router.post("/run")
async def run_backtest(request: Request) -> JSONResponse:
    """提交一次回测任务，返回 task_id；后台线程执行，进度与结果另行轮询"""
    body = await request.json()
    merged_config = {**current_config, **body}
    current_config.update(body)

    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "logs": [],
        "result": None,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_backtest_thread,
        args=(task_id, merged_config),
        daemon=True,
    )
    thread.start()

    return JSONResponse(content={"task_id": task_id, "status": "pending"})


@router.get("/status/{task_id}")
async def get_status(task_id: str) -> JSONResponse:
    """查询回测任务进度（pending/running/done/failed + progress）"""
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return JSONResponse(content={
        "task_id": task_id,
        "status": task["status"],
        "progress": task["progress"],
        "log_count": len(task["logs"]),
        "error": task.get("error"),
    })


@router.get("/result/{task_id}")
async def get_result(task_id: str) -> JSONResponse:
    """取回回测结果（指标 + 净值曲线 + 交易明细）"""
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if task["status"] == "error":
        raise HTTPException(status_code=500, detail=task.get("error", "unknown error"))
    if task["status"] != "completed":
        return JSONResponse(
            status_code=202,
            content={"message": "task not completed yet", "status": task["status"]},
        )
    return JSONResponse(content=task["result"])


@router.get("/logs/{task_id}")
async def stream_logs(task_id: str) -> StreamingResponse:
    """回测任务日志流（SSE，边跑边推，避免长轮询）"""
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    async def event_generator():
        sent_index = 0
        while True:
            logs = task["logs"]
            while sent_index < len(logs):
                msg = logs[sent_index]
                payload = json.dumps({"index": sent_index, "msg": msg}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                sent_index += 1

            if task["status"] in ("completed", "error"):
                logs = task["logs"]
                while sent_index < len(logs):
                    msg = logs[sent_index]
                    payload = json.dumps({"index": sent_index, "msg": msg}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    sent_index += 1
                done_payload = json.dumps({"done": True, "status": task["status"]}, ensure_ascii=False)
                yield f"event: done\ndata: {done_payload}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
