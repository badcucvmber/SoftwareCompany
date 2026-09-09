import asyncio
import datetime
import json
import os
import re
from functools import partial
from pathlib import Path
from typing import Any, Coroutine, Optional
import logging

# 第一步：配置日志（这一步很重要，否则可能看不到日志输出）
# 配置日志的输出格式、级别等
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为INFO，这样info级别的日志会被输出
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # 日志格式
    datefmt='%Y-%m-%d %H:%M:%S'  # 时间格式
)

# 第二步：获取logger实例（__name__表示当前模块名，是最佳实践）
logger = logging.getLogger(__name__)
import aiofiles
from mdutils.mdutils import MdUtils

try:
    from aiobotocore.session import get_session
except ImportError:
    # aiobotocore 不是必需的，只有在使用S3存储时才需要
    get_session = None
from metagpt.actions import Action
from metagpt.actions.action_output import ActionOutput
from metagpt.actions.design_api import WriteDesign
from metagpt.actions.prepare_documents import PrepareDocuments
from metagpt.actions.project_management import WriteTasks
from metagpt.actions.summarize_code import SummarizeCode
from metagpt.actions.write_code import WriteCode
from metagpt.actions.write_prd import WritePRD
from metagpt.config import CONFIG
from metagpt.const import (
    COMPETITIVE_ANALYSIS_FILE_REPO,
    DATA_API_DESIGN_FILE_REPO,
    SEQ_FLOW_FILE_REPO,
    SERDESER_PATH,
)
from metagpt.roles import Architect, Engineer, ProductManager, ProjectManager, Role
from specialized_engineers import EngineerRD, EngineerUI, EngineerPK, SmartEngineerDispatcher
from metagpt.schema import Message
from metagpt.team import Team
from metagpt.utils.common import any_to_str, read_json_file, write_json_file
from metagpt.utils.git_repository import GitRepository
from pydantic import BaseModel, Field
from zipstream import AioZipStream
from specialized_engineers import AHPEngineerDispatcher, ParallelDispatcher
from performance_db import PerformanceDB, QUALITY_EVAL_PROMPT
from metagpt.utils.common import OutputParser


_default_llm_stream_log = partial(print, end="")


class PackInfo(BaseModel):
    url: str


class RoleRun(Action):
    role: Role

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        action = self.role.rc.todo
        self.desc = f"{self.role.profile} {action.desc or str(action)}"


class PackProject(Action):
    role: Role

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.desc = "Pack the project with prd, design, code and more."

    async def upload(self, key: str):
        files = []
        workspace = Path(CONFIG.git_repo.workdir)  # 转为 Path 对象，自动处理跨平台路径

        # 递归遍历所有文件（包括子目录）
        for file_path in workspace.rglob("*"):  # rglob("*") 递归匹配所有文件/目录
            if file_path.is_file():  # 只处理文件（跳过目录本身）
                # 计算文件相对于 workspace 的相对路径（关键：保留子目录结构）
                relative_path = file_path.relative_to(workspace)
                # 添加到文件列表："file" 是本地绝对路径，"name" 是 zip 内的相对路径
                files.append({
                    "file": str(file_path),
                    "name": str(relative_path)  # 自动使用正斜杠 '/' 作为分隔符
                })

        # 打包为 zip 流
        chunks = []
        async for chunk in AioZipStream(files, chunksize=32768).stream():
            chunks.append(chunk)

        # 保存到 storage 并返回 URL
        return await get_download_url(b"".join(chunks), key)

    async def run(self, key: str):
        url = await self.upload(key)
        info = PackInfo(url=url)
        mdfile = MdUtils(None)
        mdfile.new_line(mdfile.new_inline_link(url, url.rsplit("/", 1)[-1]))
        return ActionOutput(mdfile.get_md_text(), info)


"""封装软件公司成角色，以快速接入agent store。"""
class SoftwareCompany(Role):
    """封装软件公司成角色，以快速接入agent store。"""

    finish: bool = False
    stage: str = "INIT"
    company: Team = Field(default_factory=Team)
    active_role: Optional[Role] = None
    git_repo: Optional[GitRepository] = None
    max_auto_summarize_code: int = 0
    engineer_dispatcher: AHPEngineerDispatcher = Field(default_factory=AHPEngineerDispatcher)
    specialized_engineers: list[Engineer] = Field(default_factory=list)
    current_engineer: Optional[Engineer] = None
    assigned_engineer: Optional[str] = None
    performance_db: Optional[PerformanceDB] = None
    event_queue: Optional[asyncio.Queue] = None
    requirement: str = ""
    session_id: str = ""

    def __init__(self, use_code_review=False, enable_specialized_engineers=True, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.assigned_engineer = None
        self.performance_db = PerformanceDB()
        
        if enable_specialized_engineers:
            # 使用专精工程师团队
            self.specialized_engineers = [
                EngineerRD(n_borg=1, use_code_review=use_code_review),
                EngineerUI(n_borg=1, use_code_review=use_code_review),
                EngineerPK(n_borg=1, use_code_review=use_code_review)
            ]
            # 默认使用第一个工程师（RD）
            primary_engineer = self.specialized_engineers[0]
        else:
            # 使用传统的通用工程师
            primary_engineer = Engineer(n_borg=1, use_code_review=use_code_review)

        self.current_engineer = primary_engineer
        self.company.hire([ProductManager(), Architect(), ProjectManager(), primary_engineer])
        self._init_actions([PackProject(role=primary_engineer)])

    async def _evaluate_and_save(self, content, requirement, engineer_id, task_type, session_id=None):
        """
        代码生成完成后自动评分并保存到数据库
        """
        try:
            # 优先使用实例保存的 session_id
            target_session_id = session_id or self.session_id
            logger.info(f"📊 开始对工程师 {engineer_id} 的工作成果进行质量评估 (Session: {target_session_id})...")
            
            # 1. 调用 LLM 执行 QUALITY_EVAL_PROMPT
            prompt = QUALITY_EVAL_PROMPT.format(content=content[:2000], requirement=requirement)
            # 这里的 active_role 通常是 Engineer，可以使用其 _aask 方法
            # 或者使用 self._aask 如果 SoftwareCompany 本身是一个 Role
            if self.active_role:
                response = await self.active_role._aask(prompt)
            elif self.current_engineer:
                response = await self.current_engineer._aask(prompt)
            else:
                # Fallback to current_engineer or self
                response = await self._aask(prompt)
            
            # 2. 解析 JSON 分数
            try:
                # 尝试使用 OutputParser (MetaGPT 内置)
                json_str = OutputParser.extract_struct(response, "json")
                if not json_str:
                    # 兜底：手动正则提取
                    match = re.search(r'\{.*\}', response, re.DOTALL)
                    json_str = match.group(0) if match else response
                
                eval_data = json.loads(json_str)
            except Exception as parse_e:
                logger.warning(f"JSON 解析初次失败，尝试清洗：{parse_e}")
                json_str = response.strip().replace("```json", "").replace("```", "")
                try:
                    eval_data = json.loads(json_str)
                except Exception:
                    eval_data = {"score": 5.0, "reason": f"评分解析失败: {response[:50]}..."}
            
            score = eval_data.get("score", 5.0)
            reason = eval_data.get("reason", "无评分理由")
            dimensions = eval_data.get("dimensions", {})
            
            # 3. 写入 PerformanceDB
            if self.performance_db:
                self.performance_db.save_score(
                    engineer_id=engineer_id,
                    task_type=task_type,
                    requirement=requirement,
                    quality_score=score,
                    eval_reason=reason,
                    dimensions=dimensions,
                    session_id=target_session_id
                )
            
            # 4. 通过 SSE 推送评分结果给前端
            if self.event_queue:
                self.event_queue.put_nowait({
                    "type": "quality_score",
                    "engineer_id": engineer_id,
                    "score": score,
                    "reason": reason,
                    "task_type": task_type,
                    "dimensions": dimensions,
                    "session_id": target_session_id
                })
            
            logger.info(f"✅ 质量评估推送完成：{engineer_id} - {score}")
            return score
            
        except Exception as e:
            logger.error(f"❌ 自动评分失败: {e}")
            return None

    def recv(self, message: Message) -> None:
        self.requirement = message.content
        # 如果启用了专精工程师，根据项目内容智能选择工程师
        if self.specialized_engineers:
            # 获取分派结果
            dispatch_result = self.engineer_dispatcher.dispatch_engineer(message.content)
            
            # 兼容处理：检查返回类型
            optimal_engineer = None
            if isinstance(dispatch_result, dict):
                # 优先使用返回的实例
                optimal_engineer = dispatch_result.get('_engineer_instance')
                if not optimal_engineer:
                    # 如果没有实例，尝试根据 ID 查找
                    engineer_id = dispatch_result.get('engineer_id')
                    for eng in self.specialized_engineers:
                        # 简单映射：RD -> Alice, UI -> Bob, PK -> Charlie
                        if (engineer_id == 'RD' and '代码' in eng.profile) or \
                           (engineer_id == 'UI' and '可视化' in eng.profile) or \
                           (engineer_id == 'PK' and '习题' in eng.profile):
                            optimal_engineer = eng
                            break
            elif isinstance(dispatch_result, tuple):
                # 旧逻辑兼容
                optimal_engineer = dispatch_result[0]
            else:
                # 假设是 Engineer 实例
                optimal_engineer = dispatch_result

            if optimal_engineer:
                self._switch_engineer_if_needed(optimal_engineer)
                # 记录分配的角色名称
                self.assigned_engineer = optimal_engineer.name + '-' + optimal_engineer.profile
                
                # 推送分派决策详情（供右侧 AHP 面板展示）
                if self.event_queue and isinstance(dispatch_result, dict):
                    self.event_queue.put_nowait({
                        "type": "dispatch_result",
                        "engineer_id": dispatch_result.get("engineer_id", "RD"),
                        "ahp_scores": dispatch_result.get("ahp_scores", {}),
                        "rtsm_metrics": dispatch_result.get("rtsm_metrics", {"EEP": 0, "TQG": 0, "SS": 0}),
                        "dispatch_confidence": dispatch_result.get("dispatch_confidence", "high"),
                        "confidence_note": dispatch_result.get("confidence_note", ""),
                        "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
                    })

                # 推送角色分配完成步骤卡片
                if self.event_queue:
                    self.event_queue.put_nowait({
                        "type": "step_done",
                        "role": "System",
                        "action": "AssignRole",
                        "content": f"已分配工程师：{optimal_engineer.name} · {optimal_engineer.profile}",
                        "desc": f"已分配工程师：{optimal_engineer.name} · {optimal_engineer.profile}",
                        "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
                    })
        else:
            # 未启用专精工程师时，可记录默认角色
            self.assigned_engineer = "默认工程师"

        self.company.run_project(message.content)

    def _switch_engineer_if_needed(self, new_engineer: Engineer) -> None:
        """如果需要，切换到更合适的工程师"""
        if type(new_engineer) != type(self.current_engineer):
            # 需要切换工程师
            old_engineer = self.current_engineer

            # 从团队中移除旧工程师
            if old_engineer in self.company.env.roles.values():
                self.company.env.roles = {
                    k: v for k, v in self.company.env.roles.items()
                    if v != old_engineer
                }

            # 添加新工程师到团队
            self.company.hire([new_engineer])
            self.current_engineer = new_engineer

            # 更新PackProject action的工程师引用
            for action in self.actions:
                if isinstance(action, PackProject):
                    action.role = new_engineer

    def get_current_engineer_info(self) -> dict:
        """获取当前工程师的详细信息"""
        if not self.current_engineer:
            return {"profile": "Engineer", "name": "Unknown", "specialization": "General"}

        engineer_info = {
            "profile": self.current_engineer.profile,
            "name": getattr(self.current_engineer, 'name', 'Unknown'),
            "specialization": self._get_engineer_specialization(self.current_engineer)
        }
        return engineer_info

    def _get_engineer_specialization(self, engineer: Engineer) -> str:
        """获取工程师的专业领域描述"""
        specializations = {
            "EngineerRD": "代码示例专家 - 负责生成带详细中文注释的教学示例代码",
            "EngineerUI": "可视化设计专家 - 负责生成教学图解、算法流程图等可视化内容",
            "EngineerPK": "习题设计专家 - 负责生成配套练习题、测试用例及答案",
            "Engineer": "全栈工程师 - 通用软件开发能力"
        }
        return specializations.get(engineer.profile, "编程教学辅助工程师")

    def get_available_engineers(self) -> list[dict]:
        """获取所有可用工程师的信息"""
        if not self.specialized_engineers:
            return [{"profile": "Engineer", "name": "General", "specialization": "全栈工程师"}]

        engineers_info = []
        for engineer in self.specialized_engineers:
            info = {
                "profile": engineer.profile,
                "name": getattr(engineer, 'name', 'Unknown'),
                "specialization": self._get_engineer_specialization(engineer),
                "is_active": engineer == self.current_engineer
            }
            engineers_info.append(info)

        return engineers_info

    async def run(self, message: Message = None):
        """
        运行入口（并行感知版）：
        - 先用 ParallelDispatcher 分析需求是否包含多个独立子任务
        - 若子任务 >= 2：走并行路径，各工程师并发执行，合并结果后 yield
        - 若子任务 == 1：回退串行路径，保持向下兼容
        """
        if not message:
            return

        requirement = message.content if hasattr(message, "content") else str(message)

        # ── 并行分析 ──
        try:
            dispatcher = ParallelDispatcher()
            dispatch_result = await dispatcher.analyze_and_dispatch(requirement)
        except Exception as e:
            logger.warning(f"⚠️ ParallelDispatcher 初始化/分析失败，回退串行：{e}")
            dispatch_result = {"is_parallel": False}

        if dispatch_result.get("is_parallel"):
            logger.info("🚀 并行执行模式：多工程师协作")
            # 从 _event_generator 异步生成器中 yield SSE 字符串
            stream = dispatch_result.get("stream_events")
            if stream:
                async for sse_chunk in stream:
                    yield sse_chunk
            return

        # ── 串行回退路径 ──
        logger.info("➡️ 串行执行模式（单任务需求）")
        self.recv(message)

        while await self.think():
            act_result = await self.act()
            yield act_result.content
    async def _think(self) -> bool:
        """
        优化后的思考逻辑：识别流程阶段并强制驱动到打包环节
        """
        if self.finish:
            self.rc.todo = None
            return False

        if self.git_repo is not None:
            CONFIG.git_repo = self.git_repo

        environment = self.company.env

        # 1. 正常角色轮转逻辑
        any_role_active = False
        for role in environment.roles.values():
            # 观察是否有新消息
            if await role._observe():
                any_role_active = True
                await role._think()

                # 状态同步
                if isinstance(role.rc.todo, WritePRD):
                    self.stage = "PRD"
                elif isinstance(role.rc.todo, WriteDesign):
                    self.stage = "DESIGN"
                elif isinstance(role.rc.todo, WriteTasks):
                    self.stage = "TASKS"
                elif isinstance(role.rc.todo, WriteCode):
                    self.stage = "CODE"

                # 特殊动作处理
                if isinstance(role.rc.todo, (PrepareDocuments, SummarizeCode)):
                    self.active_role = role
                    await self.act()
                    if isinstance(role.rc.todo, PrepareDocuments):
                        self.git_repo = CONFIG.git_repo
                        # 推送角色分配完成事件
                        if self.event_queue:
                            self.event_queue.put_nowait({
                                "type": "step_done",
                                "role": "System",
                                "action": "PrepareDocuments",
                                "content": "角色分配完成",
                                "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
                            })
                    # 继续思考下一轮
                    return await self._think()

                self.rc.todo = RoleRun(role=role)
                self.active_role = role
                return True

        # 2. 强制收尾逻辑：如果 CODE 阶段已开始过，且当前没有活跃任务（或报错中断后重试）
        # 只要检测到 workspace 下有了代码文件，而流程停滞了，就触发打包
        if self.stage in ["CODE", "PACKING"]:
            # 检查是否已经生成了 ZIP，避免重复触发
            has_zip = False
            if self.git_repo:
                ws_path = self.git_repo.workdir
                has_zip = any(f.endswith('.zip') for f in os.listdir(ws_path)) if os.path.exists(ws_path) else False

            if not has_zip:
                for action in self.actions:
                    if isinstance(action, PackProject):
                        self.rc.todo = action
                        self.stage = "FINISHING"  # 防止无限循环
                        logger.info("📢 检测到开发阶段完成或停滞，正在强制执行打包任务...")
                        return True

        self._set_state(0)
        return False

    async def _act(self) -> Message:
        if self.git_repo is not None:
            CONFIG.git_repo = self.git_repo
            CONFIG.src_workspace = CONFIG.git_repo.workdir / CONFIG.git_repo.workdir.name
            CONFIG.max_auto_summarize_code = self.max_auto_summarize_code

        if isinstance(self.rc.todo, PackProject):
            workdir = CONFIG.git_repo.workdir
            name = workdir.name
            uid = workdir.parent.name
            now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            key = f"{uid}/metagpt-{name}-{now}.zip"
            output = await self.rc.todo.run(key)
            self.finish = True
            return Message(output.content, role=self.profile, cause_by=type(self.rc.todo))

        default_log_stream = CONFIG.get("LLM_STREAM_LOG", _default_llm_stream_log)

        start = False
        insert_code = False

        def log_stream(msg):
            nonlocal start, insert_code
            if not start:
                if msg.startswith("["):
                    msg = "```json\n" + msg
                    insert_code = True
                start = True
            return default_log_stream(msg)

        CONFIG.LLM_STREAM_LOG = log_stream

        # 推送步骤开始事件
        if self.event_queue:
            from metagpt.utils.common import any_to_name
            action_name = any_to_name(self.active_role.rc.todo)
            
            # 根据 action_name 定制开始消息 [DONE: 修复一 1.2]
            display_desc = f"正在执行 {action_name}..."
            if "WritePRD" in action_name:
                display_desc = "正在分析需求，编写教学目标..."
            elif "WriteDesign" in action_name:
                display_desc = "正在设计教学结构与知识框架..."
            elif "WriteTasks" in action_name:
                display_desc = "正在规划教学任务与代码模块..."
            elif "WriteCode" in action_name:
                display_desc = "正在生成教学代码与注释..."
            
            self.event_queue.put_nowait({
                "type": "step_start",
                "role": self.active_role.profile,
                "action": action_name,
                "content": display_desc,
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
            })

        output = await self.active_role._act()
        
        # 推送步骤完成事件
        if self.event_queue:
            from metagpt.utils.common import any_to_name
            action_name = any_to_name(self.active_role.rc.todo)
            
            # 根据 action_name 定制完成消息
            display_content = f"{action_name} 执行完成"
            if "WritePRD" in action_name:
                display_content = "产品需求文档与教学大纲已完成 ✓"
            elif "WriteDesign" in action_name:
                display_content = "教学结构与知识框架设计已完成 ✓"
            elif "WriteTasks" in action_name:
                display_content = "教学任务与代码模块规划已完成 ✓"
            elif "WriteCode" in action_name:
                display_content = "教学代码与注释生成完毕 ✓"
            
            self.event_queue.put_nowait({
                "type": "step_done",
                "role": self.active_role.profile,
                "action": action_name,
                "content": display_content,
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
            })

        self.active_role._set_state(state=-1)
        self.active_role.publish_message(output)

        if insert_code:
            default_log_stream("\n```\n")

        cause_by = output.cause_by

        if cause_by == any_to_str(WritePRD):
            output = await self.format_prd(output)
        elif cause_by == any_to_str(WriteDesign):
            output = await self.format_system_design(output)
        elif cause_by == any_to_str(WriteTasks):
            output = await self.format_tasks(output)
        elif cause_by == any_to_str(WriteCode):
            output = await self.format_code(output)
        elif cause_by == any_to_str(SummarizeCode):
            output = await self.format_code_summary(output)
        return output

    async def format_prd(self, msg: Message):
        docs = [(k, v) for k, v in msg.instruct_content.docs.items()]
        prd_doc = docs[0][1]
        data = json.loads(prd_doc.content)

        mdfile = MdUtils(None)
        title = "Original Requirements"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_paragraph(data[title])

        title = "Product Goals"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_list(data[title], marked_with="1")

        title = "User Stories"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_list(data[title], marked_with="1")

        title = "Competitive Analysis"
        mdfile.new_header(2, title, add_table_of_contents=False)
        if all(i.count(":") == 1 for i in data[title]):
            mdfile.new_table(
                2, len(data[title]) + 1, ["Competitor", "Description", *(i for j in data[title] for i in j.split(":"))]
            )
        else:
            mdfile.new_list(data[title], marked_with="1")

        title = "Competitive Quadrant Chart"
        mdfile.new_header(2, title, add_table_of_contents=False)
        competitive_analysis_path = (
                CONFIG.git_repo.workdir / Path(COMPETITIVE_ANALYSIS_FILE_REPO) / Path(prd_doc.filename).with_suffix(
            ".png")
        )

        if competitive_analysis_path.exists():
            key = str(competitive_analysis_path.relative_to(CONFIG.git_repo.workdir.parent.parent))
            url = await upload_file_to_s3(competitive_analysis_path, key)
            mdfile.new_line(mdfile.new_inline_image(title, url))
        else:
            mdfile.insert_code(data[title], "mermaid")

        title = "Requirement Analysis"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_paragraph(data[title])

        title = "Requirement Pool"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_table(
            2, len(data[title]) + 1, ["Task Description", "Priority", *(i for j in data[title] for i in j)]
        )

        title = "UI Design draft"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_paragraph(data[title])

        title = "Anything UNCLEAR"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_paragraph(data[title])
        content = mdfile.get_md_text()
        return Message(content, cause_by=msg.cause_by, role=msg.role)

    async def format_system_design(self, msg: Message):
        system_designs = [(k, v) for k, v in msg.instruct_content.docs.items()]
        system_design_doc = system_designs[0][1]
        data = json.loads(system_design_doc.content)

        mdfile = MdUtils(None)

        title = "Implementation approach"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_paragraph(data[title])

        title = "File list"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_list(data[title], marked_with="1")

        title = "Data structures and interfaces"
        mdfile.new_header(2, title, add_table_of_contents=False)

        data_api_design_path = (
                CONFIG.git_repo.workdir
                / Path(DATA_API_DESIGN_FILE_REPO)
                / Path(system_design_doc.filename).with_suffix(".png")
        )
        if data_api_design_path.exists():
            key = str(data_api_design_path.relative_to(CONFIG.git_repo.workdir.parent.parent))
            url = await upload_file_to_s3(data_api_design_path, key)
            mdfile.new_line(mdfile.new_inline_image(title, url))
        else:
            mdfile.insert_code(data[title], "mermaid")

        title = "Program call flow"
        mdfile.new_header(2, title, add_table_of_contents=False)
        seq_flow_path = (
                CONFIG.git_repo.workdir / SEQ_FLOW_FILE_REPO / Path(system_design_doc.filename).with_suffix(".png")
        )
        if seq_flow_path.exists():
            key = str(seq_flow_path.relative_to(CONFIG.git_repo.workdir.parent.parent))
            url = await upload_file_to_s3(seq_flow_path, key)
            mdfile.new_line(mdfile.new_inline_image(title, url))
        else:
            mdfile.insert_code(data[title], "mermaid")

        title = "Anything UNCLEAR"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_paragraph(data[title])
        content = mdfile.get_md_text()
        return Message(content, cause_by=msg.cause_by, role=msg.role)

    async def format_tasks(self, msg: Message):
        tasks = [(k, v) for k, v in msg.instruct_content.docs.items()]
        task_doc = tasks[0][1]
        data = json.loads(task_doc.content)

        mdfile = MdUtils(None)
        title = "Required Python packages"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.insert_code("\n".join(data[title]), "txt")

        title = "Required Other language third-party packages"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.insert_code("\n".join(data[title]), "txt")

        title = "Logic Analysis"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_table(
            2, len(data[title]) + 1, ["Filename", "Class/Function Name", *(i for j in data[title] for i in j)]
        )

        title = "Task list"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.new_list(data[title])

        title = "Full API spec"
        mdfile.new_header(2, title, add_table_of_contents=False)
        if data[title]:
            mdfile.insert_code(data[title], "json")

        title = "Shared Knowledge"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.insert_code(data[title], "python")

        title = "Anything UNCLEAR"
        mdfile.new_header(2, title, add_table_of_contents=False)
        mdfile.insert_code(data[title], "python")
        content = mdfile.get_md_text()
        return Message(content, cause_by=msg.cause_by, role=msg.role)

    async def format_code(self, msg: Message):
        data = msg.content.splitlines()
        workdir = CONFIG.git_repo.workdir
        code_root = workdir / workdir.name

        mdfile = MdUtils(None)

        for filename in data:
            mdfile.new_header(2, filename, add_table_of_contents=False)
            filepath = code_root / filename
            try:
                async with aiofiles.open(filepath, encoding='utf-8', errors='replace') as f:
                    content = await f.read()
            except UnicodeDecodeError:
                # 再次尝试，使用更加宽松的错误处理
                async with aiofiles.open(filepath, encoding='utf-8', errors='replace') as f:
                    content = await f.read()
            except Exception as e:
                logger.error(f"Error reading file {filepath}: {e}")
                content = f"Error reading file: {e}"
                
            suffix = filename.rsplit(".", maxsplit=1)[-1]
            mdfile.insert_code(content, "python" if suffix == "py" else suffix)
        
        # 推送代码格式化完成事件
        if self.event_queue:
            self.event_queue.put_nowait({
                "type": "step_done",
                "role": "System",
                "action": "FormatCode",
                "content": "代码格式化完成 ✓",
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
            })
        
        # 🟢 新增：在代码生成并格式化完成后，自动触发质量评估
        try:
            # 确定工程师 ID
            eid = type(self.current_engineer).__name__
            engineer_id = "UI" if "UI" in eid else ("PK" if "PK" in eid else "RD")
            
            # 异步执行评估（不阻塞主响应，但在 SoftwareCompany 内部是顺序执行的）
            # 使用聚合后的 mdfile 内容进行评估
            await self._evaluate_and_save(
                content=mdfile.get_md_text(),
                requirement=self.requirement,
                engineer_id=engineer_id,
                task_type="教学代码",
                session_id=self.session_id
            )
        except Exception as eval_e:
            logger.error(f"❌ 自动触发质量评估失败: {eval_e}")
            
        return Message(mdfile.get_md_text(), cause_by=msg.cause_by, role=msg.role)

    async def format_code_summary(self, msg: Message):
        # TODO
        return msg

    async def think(self):
        await self._think()
        return self.rc.todo

    async def act(self):
        return await self._act()

    def serialize(self, stg_path: Path = None):
        stg_path = SERDESER_PATH.joinpath("software_company") if stg_path is None else stg_path

        team_info_path = stg_path.joinpath("software_company_info.json")
        write_json_file(team_info_path, self.model_dump(exclude={"company": True}))

        self.company.serialize(stg_path.joinpath("company"))  # save company alone

    @classmethod
    def deserialize(cls, stg_path: Path) -> "Team":
        """stg_path = ./storage/team"""
        # recover team_info
        software_company_info_path = stg_path.joinpath("software_company_info.json")
        if not software_company_info_path.exists():
            raise FileNotFoundError(
                "recover storage meta file `team_info.json` not exist, "
                "not to recover and please start a new project."
            )

        software_company_info: dict = read_json_file(software_company_info_path)

        # recover environment
        company = Team.deserialize(stg_path=stg_path.joinpath("company"))
        software_company_info.update({"company": company})

        return cls(** software_company_info)


async def upload_file_to_s3(filepath: str, key: str):
    async with aiofiles.open(filepath, "rb") as f:
        content = await f.read()
        return await get_download_url(content, key)


async def get_download_url(content: bytes, key: str) -> str:
    # 安全读取 STORAGE_TYPE，缺失时默认本地存储，避免 ValueError 崩溃
    try:
        storage_type = CONFIG.get("STORAGE_TYPE")
    except (ValueError, KeyError, AttributeError):
        storage_type = "local"

    if storage_type == "S3" and get_session is not None:
        try:
            session = get_session()
            async with session.create_client(
                    "s3",
                    aws_secret_access_key=CONFIG.get("S3_SECRET_KEY"),
                    aws_access_key_id=CONFIG.get("S3_ACCESS_KEY"),
                    endpoint_url=CONFIG.get("S3_ENDPOINT_URL"),
                    use_ssl=CONFIG.get("S3_SECURE"),
            ) as client:
                # upload object to amazon s3
                bucket = CONFIG.get("S3_BUCKET")
                await client.put_object(Bucket=bucket, Key=key, Body=content)
                return f"{CONFIG.get('S3_ENDPOINT_URL')}/{bucket}/{key}"
        except Exception as e:
            logger.warning(f"S3 上传失败，降级到本地存储: {e}")
    # 本地存储（默认路径）
    try:
        storage = CONFIG.get("LOCAL_ROOT", "storage")
    except (ValueError, KeyError, AttributeError):
        storage = "storage"
    try:
        base_url = CONFIG.get("LOCAL_BASE_URL", "storage")
    except (ValueError, KeyError, AttributeError):
        base_url = "storage"
    filepath = Path(storage) / key
    filepath.parent.mkdir(exist_ok=True, parents=True)
    async with aiofiles.open(filepath, "wb") as f:
        await f.write(content)
    return f"{base_url}/{key}"


async def main(idea, **kwargs):
    sc = SoftwareCompany(** kwargs)
    sc.recv(Message(idea))
    while await sc.think():
        print(await sc.act())


if __name__ == "__main__":
    asyncio.run(main())
