import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = 'performance.db'

QUALITY_EVAL_PROMPT = '''
你是一位编程教育专家。请对以下教学代码/内容进行质量评分（0-10分）。
评分维度：
1. 注释质量（中文注释是否清晰易懂）
2. 代码结构（是否适合教学，层次分明）
3. 难度匹配（是否符合目标学生水平）
4. 完整性（是否包含示例输出/说明）

生成内容：
{content}

原始需求：
{requirement}

请严格只返回 JSON 格式，不要包含 Markdown 标记（如 ```json ... ```）：
{{"score": 8.5, "reason": "简短评分理由", "dimensions": {{"comment": 9, "structure": 8, "difficulty": 8, "completeness": 9}}}}
'''

class PerformanceDB:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        """初始化数据库表结构"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS performance_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                engineer_id TEXT NOT NULL,
                task_type TEXT,
                requirement TEXT,
                quality_score REAL,
                eval_reason TEXT,
                dimensions JSON,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT
            )
        ''')
        # 检查是否需要迁移（添加 session_id 列）
        try:
            cursor.execute("SELECT session_id FROM performance_scores LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("正在为 performance_scores 表添加 session_id 列...")
            cursor.execute("ALTER TABLE performance_scores ADD COLUMN session_id TEXT")

        # 创建聊天消息表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                steps JSON,
                files JSON,
                finish_data JSON,
                time TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # 创建 session_id 索引加速查询
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id)")
        except sqlite3.OperationalError:
            pass

        # 检查 zip_data 列是否存在
        try:
            cursor.execute("SELECT zip_data FROM performance_scores LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("正在为 performance_scores 表添加 zip_data 列...")
            cursor.execute("ALTER TABLE performance_scores ADD COLUMN zip_data BLOB")
            
        conn.commit()
        conn.close()

    def save_score(self, engineer_id: str, task_type: str, requirement: str,
                   quality_score: float, eval_reason: str, dimensions: Dict[str, int],
                   session_id: str = None):
        """保存评分记录"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO performance_scores
                (engineer_id, task_type, requirement, quality_score, eval_reason, dimensions, timestamp, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (engineer_id, task_type, requirement, quality_score, eval_reason,
                  json.dumps(dimensions), datetime.now(), session_id))
            conn.commit()
            conn.close()
            logger.info(f"✅ 已保存评分：工程师={engineer_id}, 分数={quality_score}, Session={session_id}")
        except Exception as e:
            logger.error(f"❌ 保存评分失败：{e}")

    def save_zip_data(self, session_id: str, zip_data: bytes):
        """保存 ZIP 数据到数据库（覆盖式更新）"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE performance_scores SET zip_data = ? WHERE session_id = ?
            ''', (zip_data, session_id))
            if cursor.rowcount == 0:
                logger.warning(f"⚠️ 未找到 session_id={session_id} 的记录，ZIP 数据未保存")
            conn.commit()
            conn.close()
            logger.info(f"✅ 已保存 ZIP 数据：Session={session_id}, 大小={len(zip_data) if zip_data else 0} bytes")
        except Exception as e:
            logger.error(f"❌ 保存 ZIP 数据失败：{e}")

    def get_zip_data(self, session_id: str) -> Optional[bytes]:
        """获取 ZIP 数据"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT zip_data FROM performance_scores WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            conn.close()
            return row[0] if row and row[0] else None
        except Exception as e:
            logger.error(f"❌ 获取 ZIP 数据失败：{e}")
            return None

    def save_chat_messages(self, session_id: str, messages: List[Dict[str, Any]]):
        """批量保存聊天消息"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            for msg in messages:
                cursor.execute('''
                    INSERT INTO chat_messages (session_id, role, content, steps, files, finish_data, time, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    session_id,
                    msg.get('role', ''),
                    msg.get('content', ''),
                    json.dumps(msg.get('steps', [])),
                    json.dumps(msg.get('files', [])),
                    json.dumps(msg.get('finishData', {})),
                    msg.get('time', ''),
                    datetime.now()
                ))
            conn.commit()
            conn.close()
            logger.info(f"✅ 已保存 {len(messages)} 条聊天消息：Session={session_id}")
        except Exception as e:
            logger.error(f"❌ 保存聊天消息失败：{e}")

    def get_chat_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """获取聊天消息"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT role, content, steps, files, finish_data, time, timestamp
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY id ASC
            ''', (session_id,))
            rows = cursor.fetchall()
            conn.close()

            messages = []
            for row in rows:
                try:
                    steps = json.loads(row[2]) if row[2] else []
                except:
                    steps = []
                try:
                    files = json.loads(row[3]) if row[3] else []
                except:
                    files = []
                try:
                    finish_data = json.loads(row[4]) if row[4] else {}
                except:
                    finish_data = {}
                messages.append({
                    "role": row[0],
                    "content": row[1] or '',
                    "steps": steps,
                    "files": files,
                    "finishData": finish_data,
                    "time": row[5] or '',
                    "timestamp": row[6]
                })
            return messages
        except Exception as e:
            logger.error(f"❌ 获取聊天消息失败：{e}")
            return []

    def get_session_ids(self, session_id: str) -> List[str]:
        """检查 session_id 是否在数据库中"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT session_id FROM performance_scores WHERE session_id = ?", (session_id,))
            rows = cursor.fetchall()
            conn.close()
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error(f"❌ 查询 session_id 失败：{e}")
            return []

    def get_avg_score(self, engineer_id: str, task_type: Optional[str] = None, last_n: int = 10) -> float:
        """获取最近 N 次的平均分"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if task_type:
                query = '''
                    SELECT AVG(quality_score) FROM (
                        SELECT quality_score FROM performance_scores
                        WHERE engineer_id = ? AND task_type = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    )
                '''
                params = (engineer_id, task_type, last_n)
            else:
                query = '''
                    SELECT AVG(quality_score) FROM (
                        SELECT quality_score FROM performance_scores
                        WHERE engineer_id = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    )
                '''
                params = (engineer_id, last_n)
                
            cursor.execute(query, params)
            result = cursor.fetchone()
            conn.close()
            
            return result[0] if result and result[0] is not None else 0.0
        except Exception as e:
            logger.error(f"❌ 获取平均分失败：{e}")
            return 0.0

    def get_score_history(self, engineer_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """获取历史评分记录"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT quality_score, timestamp, eval_reason, requirement, task_type, dimensions, session_id 
                FROM performance_scores
                WHERE engineer_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (engineer_id, limit))
            
            rows = cursor.fetchall()
            conn.close()
            
            history = []
            for row in rows:
                try:
                    dims = json.loads(row[5]) if row[5] else None
                except:
                    dims = None
                history.append({
                    "score": row[0], 
                    "timestamp": row[1], 
                    "reason": row[2],
                    "requirement": row[3],
                    "task_type": row[4],
                    "dimensions": dims,
                    "session_id": row[6]
                })
            return history
        except Exception as e:
            logger.error(f"❌ 获取历史记录失败：{e}")
            return []

    def get_all_engineers_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有工程师的统计数据"""
        stats = {}
        engineer_ids = ["RD", "UI", "PK"] # 默认关注的工程师ID列表
        
        # 也可以从数据库中动态获取所有 engineer_id
        # conn = self.get_connection()
        # cursor = conn.cursor()
        # cursor.execute("SELECT DISTINCT engineer_id FROM performance_scores")
        # db_ids = [row[0] for row in cursor.fetchall()]
        # conn.close()
        # engineer_ids = list(set(engineer_ids + db_ids))

        for eng_id in engineer_ids:
            avg = self.get_avg_score(eng_id, last_n=100) # 获取整体平均分
            history = self.get_score_history(eng_id, limit=10)
            recent_trend = [h["score"] for h in reversed(history)] # 按时间正序排列
            
            # 获取总任务数
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM performance_scores WHERE engineer_id = ?", (eng_id,))
            total_tasks = cursor.fetchone()[0]
            conn.close()
            
            stats[eng_id] = {
                "avg_score": round(avg, 1),
                "total_tasks": total_tasks,
                "recent_trend": recent_trend
            }
            
        return stats
