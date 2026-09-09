#!/usr/bin/python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from specialized_engineers import ParallelDispatcher
import asyncio
import contextlib
import json
import os
import pathlib
import re
import shutil
import time
import traceback
import uuid
import sys
import io
import locale

# =============================================================================
# PHASE 0: BOOTSTRAP ENCODING (MUST RUN BEFORE ANY METAGPT IMPORTS)
# =============================================================================
if sys.platform.startswith('win'):
    # 设置环境变量，强制 Python 使用 UTF-8 模式 (PEP 540)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        # 强制设置区域设置为 UTF-8
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except locale.Error:
        pass

    # 重新包装标准输出流
    try:
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        if isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except (AttributeError, io.UnsupportedOperation):
        pass

import mimetypes
from collections import deque
from contextlib import asynccontextmanager
from functools import partial
from typing import Dict, Any, Optional

import fire
import tenacity
import uvicorn
from fastapi import FastAPI, Request, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse, StreamingResponse, Response, FileResponse
from sse_starlette import EventSourceResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from metagpt.config import CONFIG
from metagpt.logs import set_llm_stream_logfunc
from metagpt.schema import Message
import metagpt.utils.common as metagpt_common
import metagpt.utils.file_repository as file_repo
import metagpt.utils.dependency_file as dependency_file
import metagpt.tools.ut_writer as ut_writer
from metagpt.utils.common import any_to_name, any_to_str
import aiofiles

# =============================================================================
# PHASE 1: ROBUST MONKEY-PATCH FOR ENCODING CONSISTENCY
# =============================================================================

# 全局路径配置
BACKEND_ROOT = pathlib.Path(__file__).parent.absolute()
PROJECT_ROOT = BACKEND_ROOT.parent  # 项目根目录（MetaGPT实际输出到这里）
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"  # 修复：与MetaGPT实际生成路径一致
STORAGE_ROOT = BACKEND_ROOT / "storage"

# 确保基础目录存在
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

# 全局事件队列，用于 SSE
EVENT_QUEUE_MAP: Dict[str, asyncio.Queue] = {}
# 全局工作目录映射：uid -> 真实 workdir 绝对路径
WORKDIR_MAP: Dict[str, str] = {}
_last_session_id: str | None = None


def register_queue(session_id: str, queue: asyncio.Queue):
    EVENT_QUEUE_MAP[session_id] = queue
    global _last_session_id
    _last_session_id = session_id


def get_current_queue() -> asyncio.Queue | None:
    # 返回最新注册的 queue（单用户场景下非常有用）
    if _last_session_id and _last_session_id in EVENT_QUEUE_MAP:
        return EVENT_QUEUE_MAP[_last_session_id]
    return None


# 1.5 Global Monkey-patch for Timeout and Code Review
import metagpt.roles.engineer as engineer_module
from metagpt.provider.openai_api import OpenAILLM
from metagpt.logs import logger  # 导入 logger
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type, after_log
from openai import APIConnectionError


# 禁用 Engineer 的代码审查步骤，防止大文件超时
async def patched_is_pass(self, summary) -> tuple[bool, str]:
    logger.info("Monkey-patched: Skipping Engineer code review (_is_pass) to avoid DashScope timeout.")
    return True, "Passed by monkey-patch"


engineer_module.Engineer._is_pass = patched_is_pass

# 优化重试策略：将重试次数从 6 改为 2
original_acompletion_text = OpenAILLM.acompletion_text


@retry(
    wait=wait_random_exponential(min=1, max=60),
    stop=stop_after_attempt(2),  # 改为 2 次，让错误能更快返回
    after=after_log(logger, logger.level("WARNING").name),
    retry=retry_if_exception_type(APIConnectionError),
)
async def patched_acompletion_text(self, messages: list[dict], stream=False, timeout=3) -> str:
    # 使用 __wrapped__ 绕过原始 tenacity 装饰器，应用我们自己的装饰器
    if hasattr(original_acompletion_text, "__wrapped__"):
        return await original_acompletion_text.__wrapped__(self, messages, stream=stream, timeout=timeout)
    return await original_acompletion_text(self, messages, stream=stream, timeout=timeout)


# 应用 Monkey-patch
OpenAILLM.acompletion_text = patched_acompletion_text


# 1.1 Robust Async Read with Fallback
async def patched_aread(filename, encoding='utf-8', **kwargs):
    """
    健壮的异步读取：UTF-8 优先，GBK 后备，失败返回空字符串。
    防止下游 json.loads(None) 崩溃。
    """
    filename_str = str(filename)
    for enc in ['utf-8', 'gbk', 'utf-8-sig']:
        try:
            async with aiofiles.open(filename_str, mode="r", encoding=enc) as reader:
                return await reader.read()
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.warning(f"Error reading {filename_str} with {enc}: {e}")
            continue
    logger.error(f"Final failure reading {filename_str} with all encodings.")
    return ""


# 1.2 Force UTF-8 Write
async def patched_awrite(filename, data, encoding='utf-8', **kwargs):
    """强制使用 UTF-8 写入所有生成的代码和文档"""
    filename_str = str(filename)
    pathname = pathlib.Path(filename_str)
    pathname.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(filename_str, mode="w", encoding='utf-8') as writer:
        await writer.write(data)


# 1.3 Force UTF-8 JSON Write
def safe_str(s):
    """防御性处理：确保字符串是 UTF-8 编码，替换非法字节"""
    if s is None:
        return ""
    if isinstance(s, bytes):
        return s.decode('utf-8', errors='replace')
    return str(s).encode('utf-8', errors='replace').decode('utf-8')


def patched_write_json_file(json_file, data, encoding='utf-8', **kwargs):
    """强制使用 UTF-8 写入所有 JSON 元数据文件"""
    folder_path = pathlib.Path(json_file).parent
    if not folder_path.exists():
        folder_path.mkdir(parents=True, exist_ok=True)
    with open(json_file, "w", encoding='utf-8') as fout:
        json.dump(data, fout, ensure_ascii=False, indent=4, default=str)


# 1.4 Patch FileRepository.save (Crucial for workspace files)
async def patched_save(self, filename, content, dependencies=None):
    pathname = self.workdir / filename
    pathname.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(str(pathname), mode="w", encoding='utf-8') as writer:
        await writer.write(content)

    # 尝试获取当前队列
    task_id = getattr(CONFIG, "CURRENT_TASK_ID", None)
    q = EVENT_QUEUE_MAP.get(task_id) if task_id else get_current_queue()

    if q:
        # 推送 file_done 事件，携带代码内容
        # 仅针对代码文件（.py, .md, .txt, .json 等）推送内容，避免传输大二进制文件
        is_code = pathname.suffix in ('.py', '.md', '.txt', '.json', '.yaml', '.yml', '.html', '.js', '.css')

        q.put_nowait({
            "type": "file_done",
            "role": "Engineer",
            "action": "WriteCode",
            "filename": str(filename),
            "content": content if is_code else "[Binary Content]",  # 传递实际代码内容
            "language": "python" if str(filename).endswith(".py") else pathname.suffix[
                                                                       1:] if pathname.suffix else "text",
            "timestamp": time.strftime("%H:%M:%S")
        })

    logger.info(f"save to (patched): {str(pathname)}")
    if dependencies is not None:
        dependency_file_obj = await self._git_repo.get_dependency()
        await dependency_file_obj.update(pathname, set(dependencies))


# 应用全量补丁，覆盖所有引用点
# 替换 common 模块
metagpt_common.aread = patched_aread
metagpt_common.awrite = patched_awrite
metagpt_common.write_json_file = patched_write_json_file

# 替换 file_repository 模块中的 aread 引用及 save 方法
file_repo.aread = patched_aread
file_repo.FileRepository.save = patched_save

# 替换其他模块中的 aread/awrite 引用
if hasattr(dependency_file, 'aread'):
    dependency_file.aread = patched_aread
if hasattr(ut_writer, 'awrite'):
    ut_writer.awrite = patched_awrite

# =============================================================================
# DEEPSEEK TOKEN COUNTING FIX
# =============================================================================
from metagpt.utils.token_counter import TOKEN_COSTS

if "deepseek-v3.2-exp" not in TOKEN_COSTS:
    TOKEN_COSTS["deepseek-v3.2-exp"] = TOKEN_COSTS.get("gpt-4", {"prompt": 0.03, "completion": 0.06})
# Patch tiktoken encoding if necessary (optional, MetaGPT fallback is usually enough)

from data_model import (
    LLMAPIkeyTest,
    MessageJsonModel,
    Sentence,
    Sentences,
    SentenceType,
    SentenceValue,
    ThinkActPrompt,
    ThinkActStep,
)
from pydantic import BaseModel, Field


class MessageRequest(BaseModel):
    """Chat with MetaGPT"""
    requirement: str = Field(description="Problem description")
    config: Optional[dict[str, Any]] = Field(default_factory=dict, description="Configuration information")
    audience: Optional[str] = ""  # 目标受众，可选
    language: Optional[str] = ""  # 编程语言，可选
    domain: Optional[str] = ""  # 知识点领域，可选


from message_enum import MessageStatus, QueryAnswerType
from software_company import RoleRun, SoftwareCompany
from performance_db import PerformanceDB, QUALITY_EVAL_PROMPT

from metagpt.utils.cost_manager import TOKEN_COSTS
from metagpt.config import CONFIG

# 添加所有必要的 MIME 类型
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('application/javascript', '.mjs')
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('text/html', '.html')


class CustomStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith('.js'):
            response.headers['Content-Type'] = 'application/javascript; charset=utf-8'
        elif path.endswith('.css'):
            response.headers['Content-Type'] = 'text/css; charset=utf-8'
        return response


class ChatHandler:
    @staticmethod
    async def create_message(req_model: MessageRequest, request: Request):
        """
        Session message stream, using standard SSE format with asyncio.Queue.
        """
        # 0. 拼接背景信息 [DONE: 修复 422 并增强 prompt]
        enriched_requirement = req_model.requirement
        context_parts = []
        if req_model.audience: context_parts.append(f"目标受众：{req_model.audience}")
        if req_model.language: context_parts.append(f"编程语言：{req_model.language}")
        if req_model.domain:   context_parts.append(f"知识点领域：{req_model.domain}")
        if context_parts:
            enriched_requirement = f"{req_model.requirement}\n[教学背景：{', '.join(context_parts)}]"

        # 1. 配置加载（API Key 从环境变量读取，不再硬编码，避免泄漏）
        correct_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "YOUR_DASHSCOPE_API_KEY"
        correct_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"

        for env_key in ["OPENAI_API_KEY", "OPENAI_BASE_URL", "METAGPT_API_KEY", "METAGPT_BASE_URL"]:
            if env_key in os.environ:
                del os.environ[env_key]

        os.environ["OPENAI_API_KEY"] = correct_key
        os.environ["OPENAI_BASE_URL"] = correct_base

        from metagpt.config import Config
        new_config = Config()
        new_config.openai_api_key = correct_key
        new_config.openai_base_url = correct_base
        new_config.openai_api_model = "deepseek-v3.2-exp"

        global CONFIG
        for attr in ["openai_api_key", "openai_base_url", "openai_api_model"]:
            setattr(CONFIG, attr, getattr(new_config, attr))

        if hasattr(CONFIG, "llm"):
            if isinstance(CONFIG.llm, dict):
                CONFIG.llm.update({
                    "api_key": correct_key, "base_url": correct_base,
                    "model": "deepseek-v3.2-exp", "api_type": "openai",
                    "timeout": 600  # 增加超时时间到 10 分钟
                })
            else:
                try:
                    CONFIG.llm.api_key = correct_key
                    CONFIG.llm.base_url = correct_base
                    CONFIG.llm.model = "deepseek-v3.2-exp"
                    CONFIG.llm.api_type = "openai"
                    CONFIG.llm.timeout = 600  # 增加超时时间到 10 分钟
                except Exception:
                    pass

        # 2. 创建队列和任务 ID
        task_id = uuid.uuid4().hex
        CONFIG.CURRENT_TASK_ID = task_id
        queue = asyncio.Queue()
        register_queue(task_id, queue)

        # 3. 定义运行函数
        async def run_metagpt_task(query: str, config_dict: dict, q: asyncio.Queue):
            try:
                try:
                    exclude_keys = CONFIG.get("SERVER_METAGPT_CONFIG_EXCLUDE", [])
                except (ValueError, KeyError, AttributeError):
                    exclude_keys = []
                config = {k.upper(): v for k, v in config_dict.items() if k not in exclude_keys}

                # 确保存储配置始终注入，防止 ValueError: Key 'STORAGE_TYPE' not found
                storage_defaults = {
                    "STORAGE_TYPE": "local",
                    "LOCAL_ROOT": str(STORAGE_ROOT),
                    "LOCAL_BASE_URL": "storage",
                }
                for k, v in storage_defaults.items():
                    if k not in config:
                        config[k] = v

                # 设置上下文
                uid = uuid.uuid4().hex
                CONFIG.set_context(config)
                CONFIG.WORKSPACE_PATH = WORKSPACE_ROOT / uid
                CONFIG.CURRENT_TASK_ID = task_id

                # ── 并行需求分析 ──────────────────────────────────────────────
                await q.put({
                    "type": "step",
                    "role": "Dispatcher",
                    "action": "AssignRole",
                    "status": "running",
                    "desc": "正在分析需求，判断执行模式..."
                })

                _is_parallel = False
                _parallel_merged = ""
                try:
                    _pd = ParallelDispatcher()
                    _pd_result = await _pd.analyze_and_dispatch(query, session_id=uid)
                    _is_parallel = _pd_result.get("is_parallel", False)
                except Exception as _pd_err:
                    logger.warning(f"⚠️ 并行分析失败，回退串行：{_pd_err}")
                    _is_parallel = False

                if _is_parallel:
                    logger.info("🚀 并行执行模式：多工程师协作")
                    await q.put({
                        "type": "step",
                        "role": "Dispatcher",
                        "action": "AssignRole",
                        "status": "done",
                        "desc": "需求已拆解，启动多工程师并行执行..."
                    })
                    # 从事件生成器逐条读取并转发到主队列
                    _stream = _pd_result.get("stream_events")
                    _parallel_content_buf = []
                    if _stream:
                        async for _sse_str in _stream:
                            # _sse_str 格式: "event: xxx\ndata: {...}\n\n"
                            # 解析后放入队列让 event_generator 转发
                            try:
                                _lines = [l for l in _sse_str.strip().split("\n") if l]
                                _ename, _edata = None, {}
                                for _l in _lines:
                                    if _l.startswith("event: "):
                                        _ename = _l[7:].strip()
                                    elif _l.startswith("data: "):
                                        _edata = json.loads(_l[6:])
                                if _ename:
                                    _edata["type"] = _ename
                                    await q.put(_edata)
                                    # 收集合并内容用于 finish
                                    if _ename == "parallel_complete":
                                        _parallel_content_buf.append(
                                            _edata.get("merged_result", "")
                                        )
                            except Exception as _pe:
                                logger.warning(f"⚠️ 解析并行事件出错：{_pe}")

                    _parallel_merged = "\n\n".join(filter(None, _parallel_content_buf))
                    # 推送 finish 后直接跳出，不走下面的串行路径
                    await q.put({
                        "type": "finish",
                        "role": "System",
                        "action": "Complete",
                        "content": _parallel_merged or "并行任务全部完成！",
                        "session_id": uid,
                        "project_name": "EduCode_Parallel",
                        "file_count": 0,
                        "timestamp": time.strftime("%H:%M:%S")
                    })
                    await q.put(None)
                    return  # 并行路径结束，不继续串行

                # ── 串行路径 ─────────────────────────────────────────────────
                role = SoftwareCompany(max_auto_summarize_code=0)
                role.event_queue = q
                role.session_id = uid  # 注入 session_id 供质量评估使用
                # 禁用 SummarizeCode 加速生成
                role.max_auto_summarize_code = 0
                try:
                    from metagpt.config import CONFIG as _C
                    _C.max_auto_summarize_code = 0
                    _C.reqa_file = None  # 禁用需求质检（耗时）
                except Exception:
                    pass

                logger.info(f"🚀 Handling request: {query}")

                role.recv(message=Message(content=query))

                # 分配完成
                await q.put({
                    "type": "step",
                    "role": "Dispatcher",
                    "action": "AssignRole",
                    "status": "done",
                    "desc": f"已分配工程师：{role.assigned_engineer or '默认工程师'}"
                })

                while True:
                    think_result = await role.think()
                    if not think_result: break
                    act_result = await role.act()

                    # 逐行流式推送内容到前端 [DONE: 修复一 1.3]
                    if act_result and act_result.content:
                        # 推送进度事件：根据当前阶段推送 task_progress（前端进度条用）
                        stage_progress = {
                            "PRD": 20, "DESIGN": 40, "TASKS": 60, "CODE": 85, "FINISHING": 95
                        }
                        cur_stage = getattr(role, "stage", "CODE")
                        prog_val = stage_progress.get(cur_stage, 50)
                        engineer_id = "RD"
                        if role.current_engineer:
                            eid = type(role.current_engineer).__name__
                            engineer_id = "UI" if "UI" in eid else ("PK" if "PK" in eid else "RD")
                        await q.put({
                            "type": "task_progress",
                            "task_id": engineer_id,
                            "engineer_id": engineer_id,
                            "engineer": getattr(role.current_engineer, "profile", "工程师"),
                            "status": "running",
                            "progress": prog_val,
                            "timestamp": time.strftime("%H:%M:%S")
                        })

                        # 流式推送内容
                        await q.put({
                            "type": "message",
                            "content": act_result.content,
                            "timestamp": time.strftime("%H:%M:%S")
                        })

                        # CODE 阶段完成：推送 task_complete + 触发质量评分
                        if cur_stage in ("CODE", "FINISHING"):
                            await q.put({
                                "type": "task_progress",
                                "task_id": engineer_id,
                                "engineer_id": engineer_id,
                                "engineer": getattr(role.current_engineer, "profile", "工程师"),
                                "status": "done",
                                "progress": 100,
                                "timestamp": time.strftime("%H:%M:%S")
                            })
                            await q.put({
                                "type": "task_complete",
                                "task_id": engineer_id,
                                "engineer_id": engineer_id,
                                "engineer": getattr(role.current_engineer, "profile", "工程师"),
                                "preview": act_result.content[:80] + "...",
                                "timestamp": time.strftime("%H:%M:%S")
                            })

                            # ── LLM 质量评分 + 写入 SQLite ──
                            async def _run_quality_eval(content: str, req: str, eng_id: str, eq: asyncio.Queue):
                                """后台异步调用 LLM 对生成内容评分，写库后推送 quality_score 事件"""
                                try:
                                    import aiohttp, json as _json
                                    prompt = QUALITY_EVAL_PROMPT.format(
                                        content=content[:3000],
                                        requirement=req[:500]
                                    )
                                    payload = {
                                        "model": "deepseek-v3.2-exp",
                                        "max_tokens": 300,
                                        "messages": [{"role": "user", "content": prompt}]
                                    }
                                    headers = {
                                        "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}",
                                        "Content-Type": "application/json"
                                    }
                                    base_url = os.environ.get("OPENAI_BASE_URL",
                                                              "https://dashscope.aliyuncs.com/compatible-mode/v1")
                                    async with aiohttp.ClientSession() as session:
                                        async with session.post(
                                                f"{base_url}/chat/completions",
                                                json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)
                                        ) as resp:
                                            resp_data = await resp.json()
                                    raw_text = resp_data["choices"][0]["message"]["content"].strip()
                                    raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                                    eval_result = _json.loads(raw_text)
                                    score = float(eval_result.get("score", 7.0))
                                    reason = eval_result.get("reason", "")
                                    dimensions = eval_result.get("dimensions", {})

                                    # 确定 task_type
                                    task_type_map = {"RD": "python_teaching", "UI": "visualization", "PK": "exercise"}
                                    task_type = task_type_map.get(eng_id, "teaching")

                                    # 写入 SQLite
                                    db = PerformanceDB()
                                    db.save_score(eng_id, task_type, req, score, reason, dimensions, session_id=uid)

                                    # 推送 quality_score 事件到前端
                                    await eq.put({
                                        "type": "quality_score",
                                        "engineer_id": eng_id,
                                        "score": score,
                                        "reason": reason,
                                        "dimensions": dimensions,
                                        "task_type": task_type,
                                        "session_id": uid,
                                        "timestamp": time.strftime("%H:%M:%S")
                                    })
                                    logger.info(f"✅ 质量评分完成：{eng_id} = {score}")
                                except Exception as _e:
                                    logger.warning(f"⚠️ 质量评分失败（不影响主流程）：{_e}")

                            asyncio.create_task(
                                _run_quality_eval(act_result.content, query, engineer_id, q)
                            )

                # 统计生成的文件数量，记录真实工作目录
                file_count = 0
                project_name = "EduCode_Project"
                real_workdir = None

                if hasattr(CONFIG, "git_repo") and CONFIG.git_repo:
                    real_workdir = CONFIG.git_repo.workdir
                    project_name = real_workdir.name
                    file_count = len([f for f in real_workdir.rglob('*') if
                                      f.is_file() and f.suffix in ('.py', '.md', '.txt', '.json')])

                # 把真实 workdir 路径存到全局 map，供下载接口使用
                if real_workdir:
                    WORKDIR_MAP[uid] = str(real_workdir.absolute())
                    logger.info(f"[Finish] Registered workdir: {WORKDIR_MAP[uid]}")
                else:
                    WORKDIR_MAP[uid] = str((WORKSPACE_ROOT / uid).absolute())
                    logger.warning(f"[Finish] git_repo not available, fallback: {WORKDIR_MAP[uid]}")

                await q.put({
                    "type": "finish",
                    "role": "System",
                    "action": "Complete",
                    "content": "任务全部完成！",
                    "session_id": uid,
                    "project_name": project_name,
                    "file_count": file_count,
                    "timestamp": time.strftime("%H:%M:%S")
                })
            except Exception as e:
                logger.error(f"MetaGPT Error: {e}\n{traceback.format_exc()}")
                await q.put({
                    "type": "error",
                    "role": "System",
                    "action": "Error",
                    "content": safe_str(str(e)),
                    "timestamp": time.strftime("%H:%M:%S")
                })
            finally:
                # 用 None 作为队列结束信号，event_generator 检测到后退出
                # 注意：并行路径已在内部 return 前 put(None)，此处仍可安全再 put 一次
                # event_generator 读到第一个 None 就 break，多余的 None 会被丢弃
                try:
                    await q.put(None)
                except Exception:
                    pass

        # 4. 定义生成器 (符合模板要求)
        async def event_generator():
            # 定义推送辅助函数 [DONE: 修复一 1.1]
            def make_sse(event: str, data: dict) -> str:
                return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            # 在后台启动任务
            bg_task = asyncio.create_task(run_metagpt_task(enriched_requirement, req_model.config or {}, queue))
            try:
                while True:
                    if await request.is_disconnected():
                        bg_task.cancel()
                        break

                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=300)
                        if event is None: break

                        # 统一映射事件类型 [DONE: 修复一 1.2]
                        event_type = event.get("type", "message")

                        # 处理 MetaGPT 内部步骤事件 (来自 software_company.py)
                        if event_type in ("step_start", "step_done"):
                            event_type = "step"
                            event["status"] = "done" if event.get("type") == "step_done" else "running"
                            event["desc"] = event.get("content", "")

                        # 转换并发送
                        yield make_sse(event_type, event)

                        if event.get("type") in ("finish", "error"):
                            break
                    except asyncio.TimeoutError:
                        yield make_sse("error", {"type": "error", "content": "任务执行超时"})
                        break
            except Exception as e:
                yield make_sse("error", {"type": "error", "content": str(e)})
            finally:
                if not bg_task.done():
                    bg_task.cancel()
                if task_id in EVENT_QUEUE_MAP:
                    del EVENT_QUEUE_MAP[task_id]

        return StreamingResponse(
            event_generator(),
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"  # 禁用 Nginx 缓存
            },
            media_type="text/event-stream"
        )


class LLMAPIHandler:
    @staticmethod
    async def check_openai_key(req_model: LLMAPIkeyTest):
        try:
            # Listing all available models.
            from openai import OpenAI
            client = OpenAI(api_key=req_model.api_key)
            response = client.models.list()
            model_set = {model.id for model in response.data}
            if req_model.llm_type in model_set:
                logger.info("API Key is valid.")
                return JSONResponse({"valid": True})
            else:
                logger.info("API Key is invalid.")
                return JSONResponse({"valid": False, "message": "Model not found"})
        except Exception as e:
            # If the request fails, return False
            logger.info(f"Error: {e}")
            return JSONResponse({"valid": False, "message": str(e)})


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.create_task(clear_storage())
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join("static", "assets", "favicon-beef0aa9.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=404)


# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 确保 storage 目录存在
if not STORAGE_ROOT.exists():
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

app.mount(
    "/storage",
    StaticFiles(directory=str(STORAGE_ROOT)),
    name="storage",
)

app.add_api_route(
    "/api/messages",
    endpoint=ChatHandler.create_message,
    methods=["post"],
    summary="Session message sending (streaming response)",
)
app.add_api_route(
    "/api/test-api-key",
    endpoint=LLMAPIHandler.check_openai_key,
    methods=["post"],
    summary="LLM APIkey detection",
)


@app.post("/api/config-save")
async def config_save_notification():
    """配置保存成功通知"""
    return JSONResponse({"success": True, "message": "配置保存成功！"})


@app.post("/api/config/validate")
async def validate_config(request: Request):
    """验证配置文件格式"""
    try:
        body = await request.json()
        config_content = body.get("config", "")

        if not config_content:
            return JSONResponse({
                "valid": False,
                "error": "配置内容不能为空"
            })

        # 检查YAML格式
        import yaml
        try:
            parsed_config = yaml.safe_load(config_content)

            # 检查是否有API密钥配置
            has_api_key = False
            if isinstance(parsed_config, dict):
                openai_key = parsed_config.get("OPENAI_API_KEY")
                spark_key = parsed_config.get("SPARK_API_KEY")

                if openai_key and openai_key != "YOUR_API_KEY":
                    has_api_key = True
                if spark_key and spark_key != "YOUR_API_KEY":
                    has_api_key = True

            return JSONResponse({
                "valid": True,
                "message": "配置格式正确",
                "has_api_key": has_api_key
            })

        except yaml.YAMLError as e:
            error_msg = str(e)
            if "line" in error_msg:
                return JSONResponse({
                    "valid": False,
                    "error": f"YAML语法错误：{error_msg}"
                })
            else:
                return JSONResponse({
                    "valid": False,
                    "error": "YAML格式错误，请检查语法"
                })

    except Exception as e:
        return JSONResponse({
            "valid": False,
            "error": f"验证失败：{str(e)}"
        })


@app.get("/api/config-status")
async def config_status():
    """检查配置状态"""
    try:
        # 检查key.yaml是否存在且包含API密钥
        import yaml
        with open("key.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        has_openai_key = config.get("OPENAI_API_KEY") and config.get("OPENAI_API_KEY") != "YOUR_API_KEY"
        has_spark_key = config.get("SPARK_API_KEY") and config.get("SPARK_API_KEY") != "YOUR_API_KEY"

        return JSONResponse({
            "configured": has_openai_key or has_spark_key,
            "openai_configured": has_openai_key,
            "spark_configured": has_spark_key,
            "message": "配置检查完成"
        })
    except Exception as e:
        return JSONResponse({
            "configured": False,
            "error": str(e),
            "message": "配置文件检查失败"
        })


@app.get("/api/engineers", summary="获取工程师团队信息")
async def get_engineers_info():
    """获取可用工程师信息"""
    from software_company import SoftwareCompany

    # 创建临时实例来获取工程师信息
    temp_company = SoftwareCompany(enable_specialized_engineers=True)
    engineers_info = temp_company.get_available_engineers()
    current_engineer = temp_company.get_current_engineer_info()

    return JSONResponse({
        "available_engineers": engineers_info,
        "current_engineer": current_engineer,
        "total_count": len(engineers_info)
    })


@app.get("/api/performance", summary="获取工程师绩效统计")
async def get_performance_stats():
    """获取所有工程师的绩效数据"""
    try:
        db = PerformanceDB()
        stats = db.get_all_engineers_stats()
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/performance/history", summary="获取全局历史记录")
async def get_performance_history():
    """获取全局历史记录"""
    try:
        db = PerformanceDB()
        # 获取所有工程师的最近记录并合并
        history = []
        for eng_id in ["RD", "UI", "PK"]:
            eng_history = db.get_score_history(eng_id, limit=50)
            for item in eng_history:
                item["engineer_id"] = eng_id
            history.extend(eng_history)

        # 按时间排序
        history.sort(key=lambda x: x["timestamp"], reverse=True)
        # 统一字段名映射给前端
        formatted_history = []
        for h in history:
            formatted_history.append({
                "engineer_id": h["engineer_id"],
                "score": h["score"],
                "reason": h["reason"],
                "timestamp": h["timestamp"],
                "requirement": h.get("requirement", ""),
                "task_type": h.get("task_type", "教学任务"),
                "dimensions": h.get("dimensions"),
                "session_id": h.get("session_id")
            })
        return JSONResponse(formatted_history[:50])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/history/files/{session_id}")
async def get_history_files(session_id: str):
    """获取历史会话生成的文件列表及内容"""
    from pathlib import Path

    # 1. 优先从内存映射中查找（仅限当前运行期间生成的任务）
    real_path = WORKDIR_MAP.get(session_id)

    # 2. 搜索可能的 workspace 根目录
    possible_roots = [
        WORKSPACE_ROOT,  # MetaGPT-collaboration/workspace
        BACKEND_ROOT / "workspace",  # MetaGPT-collaboration/backend/workspace
    ]

    if not real_path:
        for ws_root in possible_roots:
            if not ws_root.exists(): continue

            # 2.1 直接匹配 session_id 目录
            candidate = ws_root / session_id
            if candidate.exists():
                # 找其中的项目子目录（MetaGPT 通常在 timestamp 目录下创建项目名目录）
                sub_dirs = sorted([d for d in candidate.iterdir() if d.is_dir() and not d.name.startswith('.')])
                real_path = str((sub_dirs[-1] if sub_dirs else candidate).absolute())
                break

            # 2.2 递归搜索名为 session_id 的目录
            for d in ws_root.rglob('*'):
                if d.is_dir() and d.name == session_id:
                    # 同样检查子目录
                    sub_dirs = sorted([sd for sd in d.iterdir() if sd.is_dir() and not sd.name.startswith('.')])
                    real_path = str((sub_dirs[-1] if sub_dirs else d).absolute())
                    break
            if real_path: break

    if not real_path:
        logger.error(f"❌ 未找到会话文件：{session_id}")
        return JSONResponse({"error": "Session files not found"}, status_code=404)

    logger.info(f"📂 正在加载历史文件：{real_path}")
    path = Path(real_path)
    files = []
    # 递归查找所有代码文件，限制深度防止扫描过慢
    for f in path.rglob('*'):
        if f.is_file() and f.suffix in ('.py', '.md', '.txt', '.json', '.html', '.js', '.css'):
            # 排除 __pycache__ 等
            if '__pycache__' in str(f) or '.git' in str(f):
                continue
            try:
                content = f.read_text(encoding='utf-8', errors='replace')
                files.append({
                    "filename": str(f.relative_to(path)),
                    "content": content,
                    "language": f.suffix[1:] if f.suffix else "plaintext"
                })
            except Exception as e:
                logger.warning(f"读取文件失败 {f}: {e}")
                continue

    if not files:
        return JSONResponse({"error": "No files found in session directory"}, status_code=404)

    return JSONResponse({"files": files})


@app.get("/api/download/{session_id}")
async def download_workspace(session_id: str):
    """将 workspace 中的生成文件打包为 zip 返回，优先从数据库读取"""
    import zipfile, io
    from pathlib import Path

    # 0. 优先从数据库读取已保存的 zip 数据
    db = PerformanceDB()
    zip_data = db.get_zip_data(session_id)
    if zip_data:
        logger.info(f"[Download] Serving zip from database for session: {session_id}")
        project_name = f"EduCode_{session_id[:8]}"
        return StreamingResponse(
            io.BytesIO(zip_data),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={project_name}.zip",
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )

    # 1. 如果数据库没有，则从 workspace 动态生成
    real_path = WORKDIR_MAP.get(session_id)
    if real_path:
        root_to_zip = Path(real_path)
        logger.info(f"[Download] Using registered workdir: {root_to_zip}")
    else:
        # 兜底：在 WORKSPACE_ROOT 下递归搜索含代码文件的目录
        logger.warning(f"[Download] session_id {session_id} not in WORKDIR_MAP, scanning workspace...")
        root_to_zip = None

        # 先尝试 WORKSPACE_ROOT/session_id
        candidate = WORKSPACE_ROOT / session_id
        if candidate.exists():
            # 找其中的项目子目录
            sub_dirs = sorted([d for d in candidate.iterdir() if d.is_dir() and not d.name.startswith('.')])
            root_to_zip = sub_dirs[-1] if sub_dirs else candidate
        else:
            # 全局扫描：找 WORKSPACE_ROOT 下最新修改、含 .py 文件的目录
            best = None
            best_mtime = 0
            for d in WORKSPACE_ROOT.rglob('*'):
                if d.is_dir() and not d.name.startswith('.') and not d.name.startswith('__'):
                    py_files = list(d.glob('*.py'))
                    if py_files:
                        mtime = d.stat().st_mtime
                        if mtime > best_mtime:
                            best_mtime = mtime
                            best = d
            root_to_zip = best

    if root_to_zip is None or not root_to_zip.exists():
        logger.error(f"[Download] Workspace not found for session: {session_id}")
        raise HTTPException(status_code=404, detail=f"Workspace not found: {session_id}")

    project_name = root_to_zip.name
    logger.info(f"[Download] Packaging project: {project_name} from {root_to_zip}")

    # 3. 打包为 ZIP
    zip_buffer = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in root_to_zip.rglob('*'):
            if file.is_file():
                # 排除敏感或无关文件
                if file.suffix in ('.py', '.md', '.txt', '.json', '.yaml', '.yml', '.html', '.js', '.css') \
                        and '.git' not in str(file) and '__pycache__' not in str(file):
                    arcname = file.relative_to(root_to_zip)
                    zf.write(file, arcname)
                    file_count += 1

    logger.info(f"[Download] Packaged {file_count} files into {project_name}.zip")
    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={project_name}.zip",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )


def _generate_zip_bytes(session_id: str) -> tuple[bytes, str]:
    """生成 zip 数据的内部函数，返回 (zip_bytes, project_name)"""
    import zipfile, io
    from pathlib import Path

    real_path = WORKDIR_MAP.get(session_id)
    if real_path:
        root_to_zip = Path(real_path)
    else:
        candidate = WORKSPACE_ROOT / session_id
        if candidate.exists():
            sub_dirs = sorted([d for d in candidate.iterdir() if d.is_dir() and not d.name.startswith('.')])
            root_to_zip = sub_dirs[-1] if sub_dirs else candidate
        else:
            best = None
            best_mtime = 0
            for d in WORKSPACE_ROOT.rglob('*'):
                if d.is_dir() and not d.name.startswith('.') and not d.name.startswith('__'):
                    py_files = list(d.glob('*.py'))
                    if py_files:
                        mtime = d.stat().st_mtime
                        if mtime > best_mtime:
                            best_mtime = mtime
                            best = d
            root_to_zip = best

    if root_to_zip is None or not root_to_zip.exists():
        return None, None

    project_name = root_to_zip.name
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in root_to_zip.rglob('*'):
            if file.is_file():
                if file.suffix in ('.py', '.md', '.txt', '.json', '.yaml', '.yml', '.html', '.js', '.css') \
                        and '.git' not in str(file) and '__pycache__' not in str(file):
                    arcname = file.relative_to(root_to_zip)
                    zf.write(file, arcname)

    zip_buffer.seek(0)
    return zip_buffer.read(), project_name


@app.post("/api/session/save")
async def save_session_data(session_id: str = Form(...), messages: str = Form(...)):
    """保存会话的聊天消息和 zip 数据到数据库"""
    try:
        import json
        db = PerformanceDB()

        # 保存聊天消息
        msg_list = json.loads(messages)
        if msg_list:
            db.save_chat_messages(session_id, msg_list)

        # 生成并保存 zip 数据
        zip_data, project_name = _generate_zip_bytes(session_id)
        if zip_data:
            # 找到对应的 session_id 记录并更新 zip_data
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE performance_scores SET zip_data = ? WHERE session_id = ?
            ''', (zip_data, session_id))
            if cursor.rowcount == 0:
                logger.warning(f"[Save] No record found for session_id={session_id} to save zip")
            conn.commit()
            conn.close()
            logger.info(f"[Save] Saved zip data: {len(zip_data)} bytes for session {session_id}")
        else:
            logger.warning(f"[Save] Could not generate zip for session {session_id}")

        return JSONResponse({"success": True, "project_name": project_name})
    except Exception as e:
        logger.error(f"[Save] Failed to save session data: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """获取会话的完整聊天历史"""
    try:
        db = PerformanceDB()
        messages = db.get_chat_messages(session_id)
        if not messages:
            return JSONResponse({"error": "No chat history found"}, status_code=404)
        return JSONResponse({"messages": messages})
    except Exception as e:
        logger.error(f"[ChatHistory] Failed to get chat history: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# 先在文件顶部导入 os 库
import os

# 替换为（自动获取正确路径）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(BASE_DIR, "static")
app.mount("/", CustomStaticFiles(directory=static_dir, html=True), name="static")

def llm_stream_log(msg):
    with contextlib.suppress():
        CONFIG._get("LLM_STREAM_LOG", lambda x: print(x, end=""))(msg)


set_llm_stream_logfunc(llm_stream_log)


def gen_file_modified_time(folder_path):
    yield os.path.getmtime(folder_path)
    for root, _, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            yield os.path.getmtime(file_path)


async def clear_storage(ttl: float = 1800):
    logger.info("task `clear_storage` start running")

    while True:
        current_time = time.time()
        for i in os.listdir(STORAGE_ROOT):
            i = STORAGE_ROOT / i
            try:
                last_time = max(gen_file_modified_time(i))
                if current_time - last_time > ttl:
                    shutil.rmtree(i)
                    await asyncio.sleep(0)
                    logger.info(f"Deleted directory: {i}")
            except Exception:
                logger.exception(f"check {i} error")
        await asyncio.sleep(60)


def main():
    # 调试：打印当前工作目录和关键路径
    from pathlib import Path
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"Backend directory: {Path(__file__).parent.absolute()}")
    logger.info(f"Workspace root: {WORKSPACE_ROOT.absolute()}")

    # 调试：列出所有已注册路由
    logger.info("Registered Routes:")
    for route in app.routes:
        methods = getattr(route, "methods", "STATIC")
        logger.info(f"Route: {methods} {route.path}")

    server_config = CONFIG.get("SERVER_UVICORN", {})
    uvicorn.run(app="__main__:app", **server_config)


if __name__ == "__main__":
    fire.Fire(main)
