import asyncio
import json
import random
from typing import Dict, Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mock Data
ENGINEERS = {
    "RD": {
        "role_name": "代码示例工程师",
        "name": "Alice",
        "description": "负责生成带详细中文注释的教学示例代码",
        "avatar": "👩‍💻",
        "tech_stack": ["python", "java", "c++", "teaching"]
    },
    "UI": {
        "role_name": "可视化设计工程师",
        "name": "Bob",
        "description": "负责生成教学图解、算法流程图",
        "avatar": "🎨",
        "tech_stack": ["visualization", "diagram", "flowchart"]
    },
    "PK": {
        "role_name": "习题设计工程师",
        "name": "Charlie",
        "description": "负责生成配套练习题和测试用例",
        "avatar": "📝",
        "tech_stack": ["quiz", "test", "exercise"]
    }
}

PERFORMANCE_STATS = {
    "RD": {"avg_score": 8.9, "total_tasks": 15, "recent_trend": [8.5, 9.0, 9.2, 8.8, 9.0, 8.9, 9.1, 8.7, 9.3, 8.9]},
    "UI": {"avg_score": 8.5, "total_tasks": 10, "recent_trend": [8.0, 8.5, 8.8, 8.2, 9.0, 8.4, 8.6, 8.1, 8.9, 8.5]},
    "PK": {"avg_score": 9.1, "total_tasks": 12, "recent_trend": [9.0, 9.2, 9.0, 9.1, 9.3, 9.0, 9.4, 8.9, 9.2, 9.1]}
}

@app.get("/api/engineers")
async def get_engineers():
    return {
        "available_engineers": list(ENGINEERS.values()),
        "current_engineer": ENGINEERS["RD"],
        "total_count": 3
    }

@app.get("/api/performance")
async def get_performance():
    # Simulate slight changes
    # Only modify total_tasks for demonstration, not in place to keep stats somewhat stable
    return PERFORMANCE_STATS

@app.post("/api/messages")
async def create_message(request: Request):
    data = await request.json()
    query = data.get("query", "")
    
    async def event_generator():
        # 1. Dispatching Phase
        await asyncio.sleep(0.5)
        
        # Scenario 1: Parallel Dispatch (2 tasks)
        if "需求 1" in query or "需求1" in query:
            print("Mocking Scenario 1: Parallel Dispatch (2 tasks)")
            # Dispatch result
            dispatch_data = {
                "engineer": "System",
                "engineer_id": "PM",
                "ahp_scores": {},
                "rtsm_metrics": {},
                "dispatch_confidence": "high",
                "confidence_note": "检测到复杂需求，自动拆解为 2 个并行子任务",
                "is_parallel": True
            }
            yield f"event: dispatch_result\ndata: {json.dumps(dispatch_data)}\n\n"
            
            # Parallel Start
            sub_tasks = ["t1_code", "t2_doc"]
            yield f"event: parallel_start\ndata: {json.dumps({'total_tasks': 2, 'task_ids': sub_tasks})}\n\n"
            
            # Task Progress
            for i in range(1, 11):
                await asyncio.sleep(0.3)
                # Task 1: RD
                yield f"event: task_progress\ndata: {json.dumps({'task_id': 't1_code', 'engineer': 'Alice', 'status': 'running', 'progress': i * 10})}\n\n"
                # Task 2: PM/Doc (Simulated)
                yield f"event: task_progress\ndata: {json.dumps({'task_id': 't2_doc', 'engineer': 'Bob', 'status': 'running', 'progress': i * 10})}\n\n"

            # Complete
            yield f"event: task_complete\ndata: {json.dumps({'task_id': 't1_code', 'engineer': 'Alice', 'status': 'done', 'preview': 'Python Code Generated...'})}\n\n"
            yield f"event: task_complete\ndata: {json.dumps({'task_id': 't2_doc', 'engineer': 'Bob', 'status': 'done', 'preview': 'Documentation Generated...'})}\n\n"
            
            yield f"event: parallel_complete\ndata: {json.dumps({'all_tasks_done': True, 'total_time_ms': 3500})}\n\n"
            
            # Quality Score (One for main result)
            await asyncio.sleep(1)
            score_msg = {
                'engineer_id': 'RD', 'score': 9.2, 'reason': '代码结构清晰，并行任务执行完美',
                'dimensions': {'comment': 9, 'structure': 9, 'difficulty': 9, 'completeness': 10}
            }
            yield f"event: quality_score\ndata: {json.dumps(score_msg)}\n\n"
            
            # Update Mock DB stats
            PERFORMANCE_STATS["RD"]["total_tasks"] += 1
            return

        # Scenario 3: Full Process (3 tasks)
        elif "需求 3" in query or "需求3" in query:
            print("Mocking Scenario 3: Full Process (3 tasks)")
            dispatch_data = {
                "engineer": "System",
                "engineer_id": "PM",
                "ahp_scores": {},
                "rtsm_metrics": {},
                "dispatch_confidence": "high",
                "confidence_note": "全栈开发需求，拆解为代码、UI、习题三个子任务",
                "is_parallel": True
            }
            yield f"event: dispatch_result\ndata: {json.dumps(dispatch_data)}\n\n"
            
            sub_tasks = ["t_rd", "t_ui", "t_pk"]
            yield f"event: parallel_start\ndata: {json.dumps({'total_tasks': 3, 'task_ids': sub_tasks})}\n\n"
            
            engineers = [("t_rd", "RD", "Alice"), ("t_ui", "UI", "Bob"), ("t_pk", "PK", "Charlie")]
            
            for i in range(1, 11):
                await asyncio.sleep(0.3)
                for tid, eid, name in engineers:
                    yield f"event: task_progress\ndata: {json.dumps({'task_id': tid, 'engineer': name, 'status': 'running', 'progress': i * 10})}\n\n"

            for tid, eid, name in engineers:
                yield f"event: task_complete\ndata: {json.dumps({'task_id': tid, 'engineer': name, 'status': 'done', 'preview': f'{eid} Task Result...'})}\n\n"
            
            yield f"event: parallel_complete\ndata: {json.dumps({'all_tasks_done': True, 'total_time_ms': 4000})}\n\n"
            
            # 3 Quality Scores
            for tid, eid, name in engineers:
                await asyncio.sleep(0.5)
                score = round(random.uniform(8.5, 9.8), 1)
                score_msg = {
                    'engineer_id': eid, 'score': score, 'reason': f'{name} 的任务完成度很高',
                    'dimensions': {'comment': 9, 'structure': 9, 'difficulty': 8, 'completeness': 9}
                }
                yield f"event: quality_score\ndata: {json.dumps(score_msg)}\n\n"
                PERFORMANCE_STATS[eid]["total_tasks"] += 1
            return

        # Default / Scenario 2 (History Bonus check)
        # Determine intent based on query
        if "画" in query or "图" in query:
            selected_id = "UI"
        elif "题" in query or "练习" in query:
            selected_id = "PK"
        else:
            selected_id = "RD"
        
        selected_eng = ENGINEERS[selected_id]
        eng_name = selected_eng["name"]

        # Mock AHP scores with history bonus context if "需求 2"
        hist_bonus_note = ""
        if "需求 2" in query or "需求2" in query:
             hist_bonus_note = " (历史绩效加成 +0.15)"
        
        dispatch_data = {
            "engineer": eng_name,
            "engineer_id": selected_id,
            "ahp_scores": {"RD": 0.85, "UI": 0.4, "PK": 0.3}, 
            "rtsm_metrics": {"EEP": 0.92, "TQG": 0.88, "SS": 0.95},
            "dispatch_confidence": "high",
            "confidence_note": f"根据关键词匹配度选择{hist_bonus_note}"
        }
        yield f"event: dispatch_result\ndata: {json.dumps(dispatch_data)}\n\n"
        
        # Normal Flow
        yield f"data: {json.dumps({'step': {'title': 'Thinking', 'description': 'Analyzing requirement...'}})}\n\n"
        await asyncio.sleep(1)
        yield f"data: {json.dumps({'step': {'title': 'Coding', 'description': f'{eng_name} is working...'}})}\n\n"
        await asyncio.sleep(1.5)
        
        response_text = f"这里是 {eng_name} 生成的结果：\n\n```python\n# 示例代码\ndef hello():\n    print('Hello from {eng_name}')\n```"
        
        msg = {
            'step': {'title': 'Done', 'description': 'Finished'},
            'content': response_text
        }
        yield f"data: {json.dumps(msg)}\n\n"

        # Quality Score
        await asyncio.sleep(1)
        score = round(random.uniform(8.0, 9.5), 1)
        score_msg = {
            'engineer_id': selected_id, 
            'score': score, 
            'reason': 'Mock evaluation: Good job!',
            'dimensions': {
                'comment': round(random.uniform(8, 10), 1),
                'structure': round(random.uniform(8, 10), 1),
                'difficulty': round(random.uniform(8, 10), 1),
                'completeness': round(random.uniform(8, 10), 1)
            }
        }
        yield f"event: quality_score\ndata: {json.dumps(score_msg)}\n\n"
        PERFORMANCE_STATS[selected_id]["total_tasks"] += 1

    return StreamingResponse(event_generator(), media_type="text/event-stream")

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
