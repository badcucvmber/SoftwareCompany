import re
import yaml
import json
import math
from typing import List, Dict, Any, Tuple, Optional
import logging

# 导入 metagpt 框架的 Role 基类
from metagpt.roles import Role
from metagpt.actions import Action
from metagpt.config import CONFIG
from metagpt.utils.git_repository import GitRepository
from metagpt.roles import Engineer as MetaEngineer
from performance_db import PerformanceDB

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ===================== 配置类 =====================
class RTSMConfig:
    _instance: Optional['RTSMConfig'] = None
    _config: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            try:
                with open("rtsm_config.yaml", "r", encoding="utf-8") as f:
                    cls._config = yaml.safe_load(f)
                logger.info("✅ 成功加载配置文件：rtsm_config.yaml")
            except FileNotFoundError:
                logger.warning("⚠️ 未找到配置文件，使用默认配置")
                cls._config = cls._get_default_config()
            except yaml.YAMLError as e:
                logger.error(f"❌ 配置文件解析错误：{e}，使用默认配置")
                cls._config = cls._get_default_config()
        return cls._instance

    @staticmethod
    def _get_default_config() -> Dict[str, Any]:
        return {
            "weight": {
                "base_score": 0.6,
                "symbiosis_score": 0.4,
                "base_detail": {
                    "tech_stack": 0.2,
                    "complexity": 0.1,
                    "history_score": 0.1
                }
            }
        }

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

# ===================== 共生计算器（简化实现） =====================
class SymbiosisCalculator:
    _skill_graph: Dict[str, Dict[str, float]] = {}

    @staticmethod
    def load_skill_graph(path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                SymbiosisCalculator._skill_graph = json.load(f)
            logger.info("🌐 技能图谱加载成功")
        except Exception as e:
            logger.warning(f"⚠️ 技能图谱加载失败，使用空图谱：{e}")
            SymbiosisCalculator._skill_graph = {}

    @staticmethod
    def calculate_eep(engineer, project_skills: List[str]) -> float:
        """
        EEP = 工程师技能熟练度 × 项目技能覆盖率
        """
        if not project_skills:
            return 0.3

        score = 0.0
        for skill in project_skills:
            score += engineer.skill_proficiency.get(skill, 0.2)

        return min(1.0, score / len(project_skills))

    @staticmethod
    def calculate_tqg(engineer, project_core_tech: str, project_complexity: int) -> float:
        """
        TQG = 核心技术成功率 × 复杂度匹配度
        """
        tech_score = engineer.similar_project_success_rate.get(project_core_tech, 0.6)
        complexity_gap = max(0, project_complexity - engineer.complexity_capability)
        complexity_penalty = max(0.0, 1.0 - complexity_gap * 0.15)
        return min(1.0, tech_score * complexity_penalty)

    @staticmethod
    def calculate_ss(eep: float, tqg: float, project_type: str) -> float:
        """
        SS = α·EEP + β·TQG （不同需求类型动态调整）
        """
        if project_type == "ui":
            alpha, beta = 0.6, 0.4
        elif project_type == "python":
            alpha, beta = 0.4, 0.6
        elif project_type == "pk":
            alpha, beta = 0.45, 0.55  # PK 工程师：EEP 权重稍低，TQG 稍高（习题需要准确性和匹配度）
        else:
            alpha, beta = 0.5, 0.5
        return min(1.0, alpha * eep + beta * tqg)

# ===================== TF-IDF分析器（真实实现） =====================
class TFIDFAnalyzer:
    def __init__(self, corpus: Dict[str, str]):
        self.corpus = corpus
        self.corpus_texts = {k: v.lower().split() for k, v in corpus.items()}
        # 预计算词频（TF）：每个文档中每个词出现的次数
        self.corpus_tf = {}
        for eng_name, words in self.corpus_texts.items():
            tf = {}
            for word in words:
                tf[word] = tf.get(word, 0) + 1
            # 归一化TF
            total = len(words)
            for word in tf:
                tf[word] = tf[word] / total if total > 0 else 0
            self.corpus_tf[eng_name] = tf

        # IDF：log(文档数 / 包含该词的文档数)
        self.idf = {}
        all_words = set()
        for words in self.corpus_texts.values():
            all_words.update(words)
        for word in all_words:
            doc_count = sum(1 for words in self.corpus_texts.values() if word in words)
            self.idf[word] = math.log(len(self.corpus_texts) / doc_count) if doc_count > 0 else 0

    def calculate_tfidf_score(self, text: str, extracted_keywords: List[str] = None) -> Dict[str, float]:
        """计算文本与每个工程师语料的TF-IDF相似度"""
        # 如果有提取出的关键词，直接使用；否则进行简单分割
        words = extracted_keywords if extracted_keywords else text.lower().split()
        scores = {}

        for eng_name, tf_dict in self.corpus_tf.items():
            score = 0.0
            for word in words:
                if word in tf_dict and word in self.idf:
                    # TF * IDF
                    score += tf_dict[word] * self.idf[word]
            scores[eng_name] = score

        # 归一化到0-1范围
        max_score = max(scores.values()) if max(scores.values()) > 0 else 1.0
        min_score = min(scores.values())
        range_score = max_score - min_score if max_score != min_score else 1.0

        for eng_name in scores:
            if range_score > 0:
                scores[eng_name] = (scores[eng_name] - min_score) / range_score
            else:
                scores[eng_name] = 0.5

        return scores

# ===================== 代码编写Action类 =====================
class WriteCode(Action):
    name: str = "WriteCode"
    desc: str = "编写代码"

    async def run(self, context: str = "", filename: str = "main.py", *args, **kwargs) -> Any:
        """
        统一的写入逻辑：生成代码并保存到工作空间
        """
        prompt = f"针对以下需求编写完整的代码文件 {filename}：\n{context or self.desc}\n请只输出代码，不要解释。"
        response = await self._aask(prompt)

        # 提取并清洗代码（去除 markdown 标签）
        code_text = response.replace("```python", "").replace("```javascript", "").replace("```html", "").replace(
            "```css", "").replace("```", "").strip()

        repo: GitRepository = CONFIG.git_repo
        if repo:
            repo.write_file(path=filename, content=code_text)
            logger.info(f"✅ 已成功写入文件: {filename}")

        return response


class WriteWebCode(WriteCode):
    name: str = "WriteWebCode"
    desc: str = "编写Web相关代码"

    async def run(self, context: str = "", *args, **kwargs) -> Any:
        # 默认生成 index.html 或根据需求判断，这里为了通用性直接调用父类
        return await super().run(context=context, filename="index.html", **kwargs)


class WriteUICode(WriteCode):
    name: str = "WriteUICode"
    desc: str = "编写UI相关代码"

    async def run(self, context: str = "", *args, **kwargs) -> Any:
        return await super().run(context=context, filename="styles.css", **kwargs)

class WritePythonCode(WriteCode):
    name: str = "WritePythonCode"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.desc = "为指定 Python 文件生成高质量源码"

    async def run(
        self,
        file_path: str,
        file_purpose: str,
        project_context: str
    ) -> str:

        prompt = f"""
你是一个专业的软件工程师。

现在你正在一个 Python 项目中编写【单个文件】。

【文件路径】
{file_path}

【文件职责】
{file_purpose}

【项目整体背景】
{project_context}

请你：
1. 只编写该文件的完整 Python 源代码
2. 不要解释
3. 不要写 README
4. 不要输出 Markdown
"""

        code_text = await self._aask(prompt)

        repo: GitRepository = CONFIG.git_repo
        if repo is None:
            raise RuntimeError("CONFIG.git_repo 未初始化，无法写入代码")

        repo.write_file(
            path=file_path,
            content=code_text
        )

        return file_path


    def _is_python_project(self, context: str) -> bool:
        python_keywords = [
            'python', '.py', 'django', 'flask', 'fastapi', 'pandas', 'numpy',
            'scikit-learn', 'tensorflow', 'pytorch', 'jupyter', 'pip', 'conda',
            'virtual environment', 'requirements.txt', 'setup.py'
        ]
        return any(keyword in context.lower() for keyword in python_keywords)

    def _inject_python_constraints(self):
        pass

    def _post_process_python_code(self, result: Any) -> Any:
        return result

# ===================== 基础工程师类（继承自metagpt.Role） =====================
class Engineer(MetaEngineer): # 继承标准工程师
    # 保留您的自定义属性用于分派逻辑
    tech_stack: List[str] = []
    complexity_capability: int = 5
    history_scores: Dict[str, float] = {}
    similar_project_success_rate: Dict[str, float] = {}
    skill_proficiency: Dict[str, float] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reset()

    def _reset(self):
        # 初始化角色状态（继承自Role的通用状态）
        pass

    def _init_actions(self, actions: List[Action]):
        # 初始化角色可执行的动作
        self.actions = [action() for action in actions]

    def match_keywords(self, keywords: List[str]) -> float:
        return 5.0

    def match_tech_stack(self, project_tech: List[str]) -> float:
        return 5.0

    def match_complexity(self, project_complexity: int) -> float:
        return 5.0

    def get_history_score(self, domain: str) -> float:
        return 5.0

# ===================== 具体工程师类 =====================
class EngineerRD(Engineer):
    name: str = "Alice"
    profile: str = "代码示例工程师"
    desc: str = "负责生成带详细中文注释的教学示例代码，覆盖初级/中级/高级三个难度层次"
    tech_stack: List[str] = [
        "python", "java", "javascript", "c++", "code_example", 
        "annotation", "tutorial", "beginner", "intermediate", "advanced"
    ]
    complexity_capability: int = 8
    history_scores: Dict[str, float] = {
        "python_teaching": 9.0, "java_teaching": 8.5, 
        "algorithm": 8.8, "web_teaching": 8.2,
        "python": 9.0  # Added to match generic 'python' domain
    }
    similar_project_success_rate: Dict[str, float] = {
        "fastapi": 0.92, "django": 0.88, "flask": 0.85, "restful api": 0.9,
        "postgresql": 0.87, "docker": 0.83, "react": 0.89, "vue": 0.86,
        "node.js": 0.84, "express": 0.82
    }
    skill_proficiency: Dict[str, float] = {
        "fastapi": 0.95, "django": 0.88, "react": 0.90, "vue": 0.75,
        "html": 0.98, "css": 0.96, "javascript": 0.92, "flask": 0.89,
        "node.js": 0.80, "express": 0.78, "restful api": 0.91,
        "postgresql": 0.82, "docker": 0.78
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_actions([WriteWebCode])

    def match_keywords(self, keywords: List[str]) -> float:
        match_keywords_set = {
            "代码", "源码", "注释", "教学", "示例", "example", "code", "annotation", "tutorial", "walkthrough"
        }
        match_count = sum(1 for kw in keywords if kw in match_keywords_set)
        return min(10.0, (match_count / max(1, len(keywords))) * 10)

    def match_tech_stack(self, project_tech: List[str]) -> float:
        match_count = sum(1 for tech in project_tech if tech in self.tech_stack)
        return min(10.0, (match_count / max(1, len(project_tech))) * 10)

    def match_complexity(self, project_complexity: int) -> float:
        if project_complexity <= self.complexity_capability:
            return 10.0
        return max(0.0, 10.0 - (project_complexity - self.complexity_capability) * 2)

    def get_history_score(self, domain: str) -> float:
        return self.history_scores.get(domain.lower(), 5.0)

class EngineerUI(Engineer):
    name: str = "Bob"
    profile: str = "可视化设计工程师"
    desc: str = "负责生成教学图解、算法流程图、数据结构示意图等可视化内容"
    tech_stack: List[str] = [
        "visualization", "diagram", "flowchart", "matplotlib", 
        "graphviz", "ui", "illustration", "animation"
    ]
    complexity_capability: int = 7
    history_scores: Dict[str, float] = {
        'ui': 9.2, 'ux': 8.8, 'frontend': 8.5, 'mobile': 8.0
    }
    similar_project_success_rate: Dict[str, float] = {
        "figma": 0.94, "sketch": 0.91, "ui": 0.93, "ux": 0.90,
        "responsive design": 0.88, "animation": 0.86, "react": 0.82,
        "vue": 0.80, "accessibility": 0.85,
        "流程图": 0.95, "图解": 0.92, "可视化": 0.94, "flowchart": 0.95, "diagram": 0.93
    }
    skill_proficiency: Dict[str, float] = {
        "figma": 0.70, "sketch": 0.95, "ui design": 0.75, "ux": 0.93,
        "html": 0.88, "css": 0.92, "javascript": 0.75, "react": 0.82,
        "vue": 0.80, "responsive design": 0.91, "animation": 0.88,
        "accessibility": 0.85, "angular": 0.70,
        "流程图": 0.98, "图解": 0.95, "可视化": 0.96, "flowchart": 0.98, "diagram": 0.95
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_actions([WriteUICode])

    def match_keywords(self, keywords: List[str]) -> float:
        match_keywords_set = {
            "图解", "可视化", "流程图", "diagram", "visualization", 
            "动画", "示意图", "图示"
        }
        match_count = sum(1 for kw in keywords if kw in match_keywords_set)
        return min(10.0, (match_count / max(1, len(keywords))) * 10)

    def match_tech_stack(self, project_tech: List[str]) -> float:
        match_count = sum(1 for tech in project_tech if tech in self.tech_stack)
        return min(10.0, (match_count / max(1, len(project_tech))) * 10)

    def match_complexity(self, project_complexity: int) -> float:
        if project_complexity <= self.complexity_capability:
            return 10.0
        return max(0.0, 10.0 - (project_complexity - self.complexity_capability) * 2)

    def get_history_score(self, domain: str) -> float:
        return self.history_scores.get(domain.lower(), 5.0)

class EngineerPK(Engineer):
    name: str = "Charlie"
    profile: str = "习题设计工程师"
    desc: str = "负责生成配套练习题、测试用例、标准答案及常见错误提示"
    tech_stack: List[str] = [
        "exercise", "quiz", "test", "problem", "leetcode", 
        "practice", "assessment", "unittest"
    ]
    complexity_capability: int = 9
    history_scores: Dict[str, float] = {
        "python": 9.0, "data analysis": 8.7, "machine learning": 8.5, "automation": 9.2,
        "pk": 9.5, "exercise": 9.3, "practice": 9.2, "quiz": 9.1, "test": 9.0
    }
    similar_project_success_rate: Dict[str, float] = {
        "python": 0.95, "pandas": 0.92, "numpy": 0.91, "scikit-learn": 0.89,
        "tensorflow": 0.87, "pytorch": 0.86, "flask": 0.88, "fastapi": 0.90,
        "automation": 0.93, "jupyter": 0.85,
        "exercise": 0.96, "练习": 0.95, "习题": 0.96, "quiz": 0.94, "test": 0.93,
        "标准答案": 0.95, "答案": 0.94
    }
    skill_proficiency: Dict[str, float] = {
        "python": 0.98, "pandas": 0.95, "numpy": 0.94, "scikit-learn": 0.90,
        "tensorflow": 0.87, "pytorch": 0.85, "flask": 0.88, "fastapi": 0.90,
        "django": 0.82, "jupyter": 0.91, "automation": 0.93,
        "data analysis": 0.92, "machine learning": 0.88,
        "exercise": 0.97, "练习": 0.96, "习题": 0.97, "quiz": 0.95, "test": 0.94,
        "标准答案": 0.96, "答案": 0.95, "leetcode": 0.98
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_actions([WritePythonCode])

    def match_keywords(self, keywords: List[str]) -> float:
        match_keywords_set = {
            "练习", "习题", "题目", "exercise", "quiz", 
            "测试", "作业", "考试", "训练"
        }
        match_count = sum(1 for kw in keywords if kw in match_keywords_set)
        return min(10.0, (match_count / max(1, len(keywords))) * 10)

    def match_tech_stack(self, project_tech: List[str]) -> float:
        match_count = sum(1 for tech in project_tech if tech in self.tech_stack)
        return min(10.0, (match_count / max(1, len(project_tech))) * 10)

    def match_complexity(self, project_complexity: int) -> float:
        if project_complexity <= self.complexity_capability:
            return 10.0
        return max(0.0, 10.0 - (project_complexity - self.complexity_capability) * 2)

    def get_history_score(self, domain: str) -> float:
        return self.history_scores.get(domain.lower(), 5.0)

# ===================== 传统加权分派器 =====================
class SmartEngineerDispatcher:
    def __init__(self):
        self.engineers = [EngineerRD(), EngineerUI(), EngineerPK()]
        self.config = RTSMConfig()
        self.weights = {
            "keyword_match": 0.4,
            "tech_stack": 0.3,
            "complexity": 0.2,
            "history_score": 0.1
        }
        logger.info("🔧 初始化分派器，加载工程师列表：%s",
                    [e.profile for e in self.engineers])

    def _extract_project_features(self, project_context: str) -> Dict[str, Any]:
        logger.info("📝 开始提取项目特征，上下文：%s",
                    project_context[:50] + "..." if len(project_context) > 50 else project_context)
        context_lower = project_context.lower()
        all_keywords = re.findall(r'\b\w+\b', context_lower)
        filtered_keywords = [kw for kw in all_keywords if len(kw) > 2]

        # 技术栈提取
        tech_patterns = {'html', 'css', 'javascript', 'react', 'vue', 'angular',
                         'django', 'flask', 'fastapi', 'node.js', 'python',
                         'pandas', 'numpy', 'scikit-learn', 'tensorflow'}
        tech_stack = [tech for tech in tech_patterns if tech in context_lower]

        # 复杂度判断
        complexity_keywords = {
            '简单': 3, '基础': 3, '入门': 2, '中等': 5, '一般': 5, '常规': 4,
            '复杂': 7, '高级': 8, '分布式': 9, '高并发': 9, '大规模': 10
        }
        complexity = next((score for kw, score in complexity_keywords.items() if kw in context_lower), 5)

        # 领域判断
        domain_keywords = {
            'web': ['website', 'web', 'api', 'frontend', 'backend'],
            'ui': ['ui', 'ux', 'design', 'interface', '可视化', '图解', '流程图', '示意图', '动画', 'visualization', 'diagram', 'flowchart'],
            'python': ['python', 'data', 'machine learning', 'ai', 'script']
        }
        domain = 'general'
        for dom, kws in domain_keywords.items():
            if any(kw in context_lower for kw in kws):
                domain = dom
                break

        features = {
            "keywords": filtered_keywords,
            "tech_stack": tech_stack,
            "complexity": complexity,
            "domain": domain
        }
        logger.debug("🔍 提取的项目特征：%s", features)
        logger.info("✅ 项目特征提取完成，技术栈：%s，复杂度：%d，领域：%s",
                    tech_stack, complexity, domain)
        return features

    def calculate_score(self, engineer: Engineer, project_features: Dict[str, Any]) -> float:
        logger.debug("🧮 计算[%s]得分，项目特征：%s", engineer.profile, project_features)
        keyword_score = engineer.match_keywords(project_features["keywords"])
        tech_score = engineer.match_tech_stack(project_features["tech_stack"])
        complexity_score = engineer.match_complexity(project_features["complexity"])
        history_score = engineer.get_history_score(project_features["domain"])

        total_score = (keyword_score * self.weights["keyword_match"] +
                       tech_score * self.weights["tech_stack"] +
                       complexity_score * self.weights["complexity"] +
                       history_score * self.weights["history_score"])

        logger.info("📊 [%s]得分明细 - 关键词匹配：%.1f, 技术栈匹配：%.1f, 复杂度匹配：%.1f, 历史表现：%.1f → 总分：%.1f",
                    engineer.profile, keyword_score, tech_score, complexity_score, history_score, total_score)
        return total_score

    def dispatch_engineer(self, project_context: str) -> Engineer:
        logger.info("🎯 开始分派工程师，项目上下文：%s",
                    project_context[:50] + "..." if len(project_context) > 50 else project_context)
        project_features = self._extract_project_features(project_context)
        engineer_scores = [(eng, self.calculate_score(eng, project_features)) for eng in self.engineers]
        best_engineer = max(engineer_scores, key=lambda x: x[1])[0]

        logger.info("🏆 最终分派结果：%s（得分：%.1f）", best_engineer.profile, max(engineer_scores, key=lambda x: x[1])[1])
        return best_engineer

    def get_all_engineers(self) -> list[Engineer]:
        return self.engineers

# ===================== AHP分析器 =====================
class AHPAnalyzer:
    def __init__(self, req_type: str = "hybrid"):
        self.req_type = req_type
        logger.info("🔧 初始化AHP分析器，需求类型：%s", req_type)
        self.criteria_matrix = self._build_dynamic_matrix(req_type)
        self.criteria_weights = self._calculate_weights(self.criteria_matrix)

        if not self._check_consistency(self.criteria_matrix, self.criteria_weights):
            logger.error("❌ 准则层判断矩阵一致性不满足要求，请求类型：%s", req_type)
            raise ValueError("准则层判断矩阵一致性不满足要求，请调整矩阵")

        logger.info("✅ AHP分析器初始化完成，准则层权重：%s", self.criteria_weights)

    def _build_dynamic_matrix(self, req_type: str) -> List[List[float]]:
        # 简化实现：返回预设的判断矩阵
        return [
            [1.0, 2.0, 3.0, 4.0],
            [0.5, 1.0, 2.0, 3.0],
            [1/3, 0.5, 1.0, 2.0],
            [0.25, 1/3, 0.5, 1.0]
        ]

    def _calculate_weights(self, matrix: List[List[float]]) -> List[float]:
        # 简化实现：返回平均权重
        n = len(matrix)
        return [sum(row) / n for row in matrix]

    def _check_consistency(self, matrix: List[List[float]], weights: List[float]) -> bool:
        # 简化实现：默认返回True
        return True

# ===================== 融合RTSM模型的AHP分派器 =====================
class AHPEngineerDispatcher:
    def __init__(self):
        self.engineer_corpus = {
            "代码示例工程师": """
            代码 示例 注释 教学 演示 入门 教程 源码 python java javascript c++ 
            code example annotation tutorial teaching demonstration commented code walkthrough
            """,
            "可视化设计工程师": """
            图解 可视化 流程图 示意图 图示 动画 界面 设计 
            visualization diagram flowchart data structure animation illustration 
            graphviz matplotlib teaching visual explanation chart graph
            """,
            "习题设计工程师": """
            练习 习题 题目 测试 作业 考试 训练 标准答案 
            exercise quiz practice problem assessment unittest test cases answer 
            hint common mistakes leetcode training homework examination
            """
        }
        logger.info("🔧 初始化AHP+RTSM分派器，加载工程师语料库：%s", list(self.engineer_corpus.keys()))
        self.tfidf_analyzer = TFIDFAnalyzer(self.engineer_corpus)
        self.engineers = [EngineerRD(), EngineerUI(), EngineerPK()]
        self.criteria_index = {
            "tfidf_match": 0,
            "tech_stack": 1,
            "complexity": 2,
            "history_score": 3
        }
        self.engineer_load = {
            "代码示例工程师": 3,
            "可视化设计工程师": 1,
            "习题设计工程师": 5
        }
        self.config = RTSMConfig()
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        skill_graph_path = os.path.join(current_dir, "skill_graph.json")
        SymbiosisCalculator.load_skill_graph(skill_graph_path)
        logger.info(
            "✅ AHP+RTSM分派器初始化完成，加载工程师列表：%s",
            [e.profile for e in self.engineers]
        )

        self.performance_db = PerformanceDB()

    def _identify_req_type(self, req_text: str) -> str:
        logger.debug("🔍 识别需求类型，文本：%s", req_text[:50] + "..." if len(req_text) > 50 else req_text)
        if not isinstance(req_text, str) or not req_text.strip():
            logger.warning("⚠️ 需求文本无效，使用hybrid类型")
            return "hybrid"

        text_lower = req_text.lower()

        def count_keywords(keywords):
            return sum(1 for kw in keywords if kw in text_lower)

        ui_count = count_keywords(["ui", "ux", "界面", "设计", "figma", "sketch", "动画", "响应式", "图解", "可视化", "流程图", "示意图", "图示"])
        # 移除 "示例"、"代码"、"注释"，这些是通用词会导致误判
        python_count = count_keywords(["python", "pandas", "numpy", "机器学习", "数据分析", "tensorflow"])
        pk_count = count_keywords(["练习", "习题", "题目", "exercise", "quiz", "测试", "作业", "考试", "训练", "标准答案"])
        web_count = count_keywords(["web", "api", "fastapi", "flask", "django", "后端", "前端"])

        # 习题检测优先，因为习题关键词更具体，应该优先于通用 python 关键词
        if pk_count > 0:
            req_type = "pk"
        elif ui_count > 0:
            req_type = "ui"
        elif python_count > 0:
            req_type = "python"
        elif web_count > 0:
            req_type = "web"
        else:
            req_type = "hybrid"

        logger.info("📋 需求类型识别完成：%s（UI:%d, Python:%d, PK:%d, Web:%d）",
                    req_type, ui_count, python_count, pk_count, web_count)
        return req_type

    def _build_context_key(self, features: Dict[str, Any]) -> str:
        """
        构建 RTSM 使用的任务上下文 Key
        """
        complexity_bucket = features["complexity"] // 3
        return f"{features['req_type']}|{features['project_core_tech']}|C{complexity_bucket}"

    def _extract_project_features(self, project_context: str) -> Dict[str, Any]:
        logger.info("📝 开始提取项目特征，上下文：%s",
                    project_context[:50] + "..." if len(project_context) > 50 else project_context)
        if not isinstance(project_context, str) or not project_context.strip():
            logger.warning("⚠️ 项目上下文无效，返回默认特征")
            return {
                "keywords": [], "tech_stack": [], "complexity": 5, "domain": "general",
                "tfidf_scores": {"代码示例工程师": 0.0, "可视化设计工程师": 0.0, "习题设计工程师": 0.0},
                "req_type": "hybrid", "project_skills": [], "project_core_tech": "general"
            }

        context_lower = project_context.lower()
        
        # 改进的关键词提取：基于预定义关键词库的匹配（解决中文分词问题）
        # 收集所有工程师的关键词库
        all_engineer_keywords = set()
        for eng in self.engineers:
            # 临时实例化或访问类属性来获取关键词集合
            # 这里我们手动聚合，或者更好的方式是让每个工程师自己检查
            if isinstance(eng, EngineerRD):
                all_engineer_keywords.update({
                    "示例", "example", "代码", "演示", "demo", "python", "java", 
                    "教学", "注释", "tutorial", "入门", "初级", "中级", "高级"
                })
            elif isinstance(eng, EngineerUI):
                all_engineer_keywords.update({
                    "图解", "可视化", "流程图", "diagram", "visualization", 
                    "动画", "示意图", "图示"
                })
            elif isinstance(eng, EngineerPK):
                all_engineer_keywords.update({
                    "练习", "习题", "题目", "exercise", "quiz", 
                    "测试", "作业", "考试", "训练", "标准答案"
                })
        
        # 在文本中查找这些关键词
        filtered_keywords = [kw for kw in all_engineer_keywords if kw in context_lower]

        # 只保留与工程师关键词库匹配的英文词（避免英文单词稀释匹配率）
        english_matching = [kw for kw in re.findall(r'\b[a-z]{2,}\b', context_lower)
                          if kw in all_engineer_keywords]
        filtered_keywords.extend(english_matching)

        # 去重
        filtered_keywords = list(set(filtered_keywords))

        # 技术栈提取
        tech_patterns = {'html', 'css', 'javascript', 'react', 'vue', 'angular',
                         'django', 'flask', 'fastapi', 'node.js', 'python',
                         'pandas', 'numpy', 'scikit-learn', 'tensorflow', 'figma', 'sketch',
                         'java', 'c++', 'algorithm', 'data structure', 'bfs', 'dfs',
                         '流程图', '图解', '可视化', '示意图', 'diagram', 'flowchart', 'visualization'}
        tech_stack = [tech for tech in tech_patterns if tech in context_lower]

        # 复杂度判断
        complexity_keywords = {
            '简单': 3, '基础': 3, '入门': 2, '中等': 5, '一般': 5, '常规': 4,
            '复杂': 7, '高级': 8, '分布式': 9, '高并发': 9, '大规模': 10
        }
        complexity = next((score for kw, score in complexity_keywords.items() if kw in context_lower), 5)

        # 领域判断
        domain_keywords = {
            'web': ['website', 'web', 'api', 'frontend', 'backend'],
            'ui': ['ui', 'ux', 'design', 'interface', '可视化', '图解', '流程图', '示意图', '动画', 'visualization', 'diagram', 'flowchart'],
            'python': ['python', 'data', 'machine learning', 'ai', 'script'],
            'pk': ['练习', '习题', '题目', 'exercise', 'quiz', '测试', '作业', '考试', '训练', '标准答案']
        }
        domain = next((dom for dom, kws in domain_keywords.items() if any(kw in context_lower for kw in kws)),
                      'general')

        project_skills = tech_stack
        core_tech_priority = [
            "fastapi", "django", "flask", "react", "vue", "python",
            "pandas", "numpy", "scikit-learn", "tensorflow", "figma", "sketch",
            "流程图", "可视化", "图解", "示意图", "flowchart", "diagram", "visualization"
        ]
        project_core_tech = next((tech for tech in core_tech_priority if tech in tech_stack), "general")
        tfidf_scores = self.tfidf_analyzer.calculate_tfidf_score(project_context, filtered_keywords)
        req_type = self._identify_req_type(project_context)

        features = {
            "keywords": filtered_keywords,
            "tech_stack": tech_stack,
            "complexity": complexity,
            "domain": domain,
            "tfidf_scores": tfidf_scores,
            "req_type": req_type,
            "project_skills": project_skills,
            "project_core_tech": project_core_tech
        }
        logger.info("✅ 项目特征提取完成 - 技术栈：%s，复杂度：%d，领域：%s，核心技术：%s，类型：%s",
                    tech_stack, complexity, domain, project_core_tech, req_type)
        logger.debug("🔍 完整项目特征：%s", features)
        return features

    def _get_engineer_id(self, profile: str) -> str:
        if "代码示例" in profile:
            return "RD"
        elif "可视化" in profile:
            return "UI"
        elif "习题" in profile:
            return "PK"
        return "UNKNOWN"

    def _calculate_scores_and_metrics(self, project_context: str, project_features: Dict[str, Any], selected_engineer: Engineer) -> Tuple[Dict[str, float], Dict[str, float]]:
        """
        计算所有工程师的得分和选中工程师的RTSM指标（供优先级路径使用）
        返回 (ahp_scores, rtsm_metrics)
        """
        config = self.config
        ahp_scores = {}
        rtsm_metrics = {}

        try:
            # 加载权重配置
            base_weight = max(0.0, min(1.0, config.get("weight.base_score", 0.6)))
            symbiosis_weight = max(0.0, min(1.0, config.get("weight.symbiosis_score", 0.4)))

            # 权重归一化
            total_weight = base_weight + symbiosis_weight
            if abs(total_weight - 1.0) > 0.001:
                base_weight /= total_weight
                symbiosis_weight /= total_weight

            tech_weight = config.get("weight.base_detail.tech_stack", 0.2)
            comp_weight = config.get("weight.base_detail.complexity", 0.1)
            hist_weight = config.get("weight.base_detail.history_score", 0.1)
            keyword_weight = max(0.0, 1.0 - (tech_weight + comp_weight + hist_weight))

            # 计算每个工程师的得分
            for engineer in self.engineers:
                keyword_score = min(1.0, max(0.0, engineer.match_keywords(project_features["keywords"]) / 10))
                tech_score = min(1.0, max(0.0, engineer.match_tech_stack(project_features["tech_stack"]) / 10))
                comp_score = min(1.0, max(0.0, engineer.match_complexity(project_features["complexity"]) / 10))
                hist_score = min(1.0, max(0.0, engineer.get_history_score(project_features["domain"]) / 10))

                base_score = (keyword_weight * keyword_score +
                              tech_weight * tech_score +
                              comp_weight * comp_score +
                              hist_weight * hist_score) * base_weight

                eep = SymbiosisCalculator.calculate_eep(
                    engineer=engineer,
                    project_skills=project_features["project_skills"]
                )
                tqg = SymbiosisCalculator.calculate_tqg(
                    engineer=engineer,
                    project_core_tech=project_features["project_core_tech"],
                    project_complexity=project_features["complexity"]
                )
                ss = SymbiosisCalculator.calculate_ss(
                    eep=eep,
                    tqg=tqg,
                    project_type=project_features["req_type"]
                )
                symbiosis_score = ss * symbiosis_weight

                # 获取历史均分计算绩效因子
                eng_id = self._get_engineer_id(engineer.profile)
                hist_avg_score = self.performance_db.get_avg_score(eng_id)
                performance_factor = 1.0 + (hist_avg_score - 5.0) * 0.05
                performance_factor = max(0.8, min(1.2, performance_factor))

                total_score = (base_score + symbiosis_score) * performance_factor
                ahp_scores[engineer.profile] = total_score

                # 为选中的工程师记录RTSM指标
                if engineer.profile == selected_engineer.profile:
                    rtsm_metrics = {
                        "EEP": eep,
                        "TQG": tqg,
                        "SS": ss,
                        "performance_factor": performance_factor
                    }

            logger.info(f"✅ 优先级路径分数计算完成：ahp_scores={ahp_scores}, rtsm_metrics={rtsm_metrics}")

        except Exception as e:
            logger.error(f"❌ 优先级路径分数计算失败：{str(e)}", exc_info=True)
            # 降级：返回默认分数
            for eng in self.engineers:
                ahp_scores[eng.profile] = 0.5
            rtsm_metrics = {"EEP": 0.5, "TQG": 0.5, "SS": 0.5, "performance_factor": 1.0}

        return ahp_scores, rtsm_metrics

    def dispatch_engineer(self, project_context: str) -> Dict[str, Any]:
        logger.info("🚀 开始工程师分派流程，项目上下文：%s",
                    project_context[:50] + "..." if len(project_context) > 50 else project_context)
        project_features = self._extract_project_features(project_context)
        context_key = self._build_context_key(project_features)
        config = self.config

        # ── 优先级判断：如果明确要求习题/练习相关，直接选择 PK ──
        pk_priority_keywords = ["习题", "练习题", "练习", "题目", "exercise", "quiz",
                                "测试题", "考试题", "作业题", "上机练习", "编程题"]
        context_lower = project_context.lower()
        pk_keyword_count = sum(1 for kw in pk_priority_keywords if kw in context_lower)
        if pk_keyword_count > 0:
            logger.info("🎯 优先级判断：检测到 %d 个习题关键词，直接分派给 PK 工程师", pk_keyword_count)
            pk_engineer = next((e for e in self.engineers if "习题" in e.profile), self.engineers[2])
            # 计算实际分数和指标（与其他工程师对比）
            _ahp_scores, _rtsm_metrics = self._calculate_scores_and_metrics(project_context, project_features, pk_engineer)
            return {
                'engineer': pk_engineer.profile,
                'engineer_id': 'PK',
                'ahp_scores': _ahp_scores,
                'rtsm_metrics': _rtsm_metrics,
                'dispatch_confidence': 'high',
                'confidence_note': '习题关键词优先级判断命中',
                'is_parallel': False,
                'sub_tasks': [],
                '_engineer_instance': pk_engineer
            }

        # ── 优先级判断：如果明确要求可视化/图解相关，直接选择 UI ──
        ui_priority_keywords = ["图解", "可视化", "流程图", "示意图", "图示", "diagram",
                                "flowchart", "visualization", "动画", "画图"]
        ui_keyword_count = sum(1 for kw in ui_priority_keywords if kw in context_lower)
        if ui_keyword_count > 0:
            logger.info("🎯 优先级判断：检测到 %d 个可视化关键词，直接分派给 UI 工程师", ui_keyword_count)
            ui_engineer = next((e for e in self.engineers if "可视化" in e.profile), self.engineers[1])
            # 计算实际分数和指标（与其他工程师对比）
            _ahp_scores, _rtsm_metrics = self._calculate_scores_and_metrics(project_context, project_features, ui_engineer)
            return {
                'engineer': ui_engineer.profile,
                'engineer_id': 'UI',
                'ahp_scores': _ahp_scores,
                'rtsm_metrics': _rtsm_metrics,
                'dispatch_confidence': 'high',
                'confidence_note': '可视化关键词优先级判断命中',
                'is_parallel': False,
                'sub_tasks': [],
                '_engineer_instance': ui_engineer
            }

        # ── 优先级判断：如果明确要求代码示例/教学代码，直接选择 RD ──
        rd_priority_keywords = ["代码", "示例", "源码", "注释", "教学代码", "演示代码",
                                "example code", "注释代码", "带注释"]
        rd_keyword_count = sum(1 for kw in rd_priority_keywords if kw in context_lower)
        if rd_keyword_count > 0:
            logger.info("🎯 优先级判断：检测到 %d 个代码示例关键词，直接分派给 RD 工程师", rd_keyword_count)
            rd_engineer = next((e for e in self.engineers if "代码示例" in e.profile), self.engineers[0])
            # 计算实际分数和指标（与其他工程师对比）
            _ahp_scores, _rtsm_metrics = self._calculate_scores_and_metrics(project_context, project_features, rd_engineer)
            return {
                'engineer': rd_engineer.profile,
                'engineer_id': 'RD',
                'ahp_scores': _ahp_scores,
                'rtsm_metrics': _rtsm_metrics,
                'dispatch_confidence': 'high',
                'confidence_note': '代码示例关键词优先级判断命中',
                'is_parallel': False,
                'sub_tasks': [],
                '_engineer_instance': rd_engineer
            }

        # 加载权重配置
        base_weight = max(0.0, min(1.0, config.get("weight.base_score", 0.6)))
        symbiosis_weight = max(0.0, min(1.0, config.get("weight.symbiosis_score", 0.4)))

        # 权重归一化
        total_weight = base_weight + symbiosis_weight
        if abs(total_weight - 1.0) > 0.001:
            logger.warning(f"⚠️ 基础分+共生分权重={total_weight}≠1，已归一化")
            base_weight /= total_weight
            symbiosis_weight /= total_weight

        tech_weight = config.get("weight.base_detail.tech_stack", 0.2)
        comp_weight = config.get("weight.base_detail.complexity", 0.1)
        hist_weight = config.get("weight.base_detail.history_score", 0.1)
        keyword_weight = max(0.0, 1.0 - (tech_weight + comp_weight + hist_weight))
        
        logger.info("⚖️ 分派权重配置 - 基础分：%.2f，共生分：%.2f", base_weight, symbiosis_weight)
        logger.debug("🔧 基础分内部权重 - 关键词：%.2f，技术栈：%.2f，复杂度：%.2f，历史表现：%.2f",
                     keyword_weight, tech_weight, comp_weight, hist_weight)

        engineer_scores = {}
        expected_scores = {}
        engineer_metrics = {}

        for engineer in self.engineers:
            try:
                logger.info("🧮 计算[%s]的综合得分", engineer.profile)
                # 1. 基础特征分（归一化到0-1）
                keyword_score = min(1.0, max(0.0, engineer.match_keywords(project_features["keywords"]) / 10))
                tech_score = min(1.0, max(0.0, engineer.match_tech_stack(project_features["tech_stack"]) / 10))
                comp_score = min(1.0, max(0.0, engineer.match_complexity(project_features["complexity"]) / 10))
                hist_score = min(1.0, max(0.0, engineer.get_history_score(project_features["domain"]) / 10))

                base_score = (keyword_weight * keyword_score +
                              tech_weight * tech_score +
                              comp_weight * comp_score +
                              hist_weight * hist_score) * base_weight

                logger.debug("📊 [%s]基础分明细 - 关键词：%.3f，技术栈：%.3f，复杂度：%.3f，历史表现：%.3f → 加权分：%.3f",
                             engineer.profile, keyword_score, tech_score, comp_score, hist_score, base_score)

                # 2. 共生特征分
                eep = SymbiosisCalculator.calculate_eep(
                    engineer=engineer,
                    project_skills=project_features["project_skills"]
                )
                tqg = SymbiosisCalculator.calculate_tqg(
                    engineer=engineer,
                    project_core_tech=project_features["project_core_tech"],
                    project_complexity=project_features["complexity"]
                )
                ss = SymbiosisCalculator.calculate_ss(
                    eep=eep,
                    tqg=tqg,
                    project_type=project_features["req_type"]
                )
                
                symbiosis_score = ss * symbiosis_weight
                
                # 3. 综合得分与持久化绩效学习调整 (Adaptive Performance Learning)
                expected_score = base_score + symbiosis_score
                
                # 从 SQLite 获取持久化历史均分
                eng_id = self._get_engineer_id(engineer.profile)
                hist_avg_score = self.performance_db.get_avg_score(eng_id)
                
                # 性能校正因子：均分 5.0 为基准 (1.0)，每高 1 分提升 5%，每低 1 分降低 5%
                # 这样实现了基于真实质量评分的持久化自适应学习
                performance_factor = 1.0 + (hist_avg_score - 5.0) * 0.05
                performance_factor = max(0.8, min(1.2, performance_factor))
                
                total_score = expected_score * performance_factor
                engineer_scores[engineer.profile] = total_score
                
                logger.info(
                    "🏆 [%s]综合得分计算完成 - (基础分：%.3f + 共生分：%.3f) × 绩效因子：%.3f (均分:%.1f) = %.3f",
                    engineer.profile, base_score, symbiosis_score, performance_factor, hist_avg_score, total_score
                )

                engineer_metrics[engineer.profile] = {
                    "EEP": eep,
                    "TQG": tqg,
                    "SS": ss,
                    "performance_factor": performance_factor
                }

            except Exception as e:
                logger.error(f"❌ 计算[{engineer.profile}]得分时出错：{str(e)}", exc_info=True)
                engineer_scores[engineer.profile] = 0.0

        # 选择最优工程师
        best_profile = max(engineer_scores, key=engineer_scores.get)
        best_engineer = next(e for e in self.engineers if e.profile == best_profile)
        best_score = engineer_scores[best_profile]
        
        # 计算置信度
        sorted_scores = sorted(engineer_scores.values(), reverse=True)
        score_diff = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 1.0
        
        if score_diff < 0.1:
            dispatch_confidence = "low"
            confidence_note = f"最高分与次高分差值仅为 {score_diff:.3f}，需人工关注"
        elif score_diff < 0.3:
            dispatch_confidence = "medium"
            confidence_note = f"得分优势适中，差值为 {score_diff:.3f}"
        else:
            dispatch_confidence = "high"
            confidence_note = f"得分优势明显，差值为 {score_diff:.3f}"

        logger.info(
            "🏁 分派完成 - 最优工程师：%s，综合得分：%.3f，置信度：%s",
            best_profile, best_score, dispatch_confidence
        )
        
        return {
            'engineer': best_profile,
            'engineer_id': self._get_engineer_id(best_profile),
            'ahp_scores': engineer_scores,
            'rtsm_metrics': engineer_metrics[best_profile],
            'dispatch_confidence': dispatch_confidence,
            'confidence_note': confidence_note,
            'is_parallel': False,
            'sub_tasks': [],
            '_engineer_instance': best_engineer  # 内部保留实例供调用方使用
        }

    def generate_assignment_report(self, best_engineer: Engineer, project_context: str) -> str:
        """生成工程师分派报告（用于测试输出）"""
        project_features = self._extract_project_features(project_context)
        eep = SymbiosisCalculator.calculate_eep(
            engineer=best_engineer,
            project_skills=project_features["project_skills"]
        )
        tqg = SymbiosisCalculator.calculate_tqg(
            engineer=best_engineer,
            project_core_tech=project_features["project_core_tech"],
            project_complexity=project_features["complexity"]
        )
        ss = SymbiosisCalculator.calculate_ss(
            eep=eep,
            tqg=tqg,
            project_type=project_features["req_type"]
        )
        report = f"""
===== 工程师分派报告 =====
项目描述：{project_context[:50]}...
选中工程师：{best_engineer.profile}
核心指标：
  - EEP（技能拓展潜力）：{eep:.3f}
  - TQG（任务质量保障）：{tqg:.3f}
  - SS（共生度）：{ss:.3f}
项目特征：
  - 技术栈：{', '.join(project_features['tech_stack']) or '无'}
  - 复杂度：{project_features['complexity']}
  - 核心技术：{project_features['project_core_tech']}
  - 需求类型：{project_features['req_type']}
配置参数：
  - 基础分权重：{self.config.get('weight.base_score', 0.6):.2f}
  - 共生分权重：{self.config.get('weight.symbiosis_score', 0.4):.2f}
=========================
        """.strip()
        return report

import asyncio
from metagpt.provider.llm_provider_registry import LLMProviderRegistry
from metagpt.utils.common import OutputParser

# ===================== 并行分派器 =====================
class ParallelDispatcher:
    """
    复杂需求自动拆解与并行分派控制器
    核心方法：analyze_and_dispatch(requirement: str) -> dict
    """
    
    DECOMPOSE_PROMPT = '''
    你是一个教学需求分析师。分析以下教学需求，判断是否包含多个独立子任务。
    独立子任务的判断标准：
    - 代码示例任务（生成代码、注释、演示）
    - 可视化任务（流程图、图解、示意图）
    - 习题任务（练习题、测试题、作业）
    
    需求：{requirement}
    
    只返回 JSON，格式：
    {{"sub_tasks": [
      {{"task_id": "t1", "description": "子任务描述", 
       "engineer_type": "RD|UI|PK", "priority": 1}}
    ]}}
    如果只有 1 个子任务，返回只含 1 个元素的列表。
    '''

    def __init__(self):
        # 在 MetaGPT 中，OpenAILLM 会默认加载 CONFIG
        from metagpt.provider import OpenAILLM
        self.llm = OpenAILLM()
        self.engineer_dispatcher = AHPEngineerDispatcher()

    async def analyze_and_dispatch(self, requirement: str, session_id: str = None) -> dict:
        """
        1. 调用 LLM 拆解需求
        2. 若子任务数 == 1，返回 is_parallel=False，走原路径
        3. 若子任务数 >= 2，返回 is_parallel=True 和 事件流生成器
        """
        logger.info(f"🔄 开始分析需求并行性 (Session: {session_id})：{requirement[:50]}...")
        
        try:
            # 1. 调用 LLM 拆解需求
            prompt = self.DECOMPOSE_PROMPT.format(requirement=requirement)
            response = await self.llm.aask(prompt)
            
            # 解析 JSON
            try:
                # 尝试从 markdown 代码块中提取 json
                json_str = OutputParser.extract_struct(response, "json")
                tasks_data = json.loads(json_str)
            except Exception:
                # 如果失败，尝试直接解析
                json_str = response.strip().replace("```json", "").replace("```", "")
                tasks_data = json.loads(json_str)
                
            sub_tasks = tasks_data.get("sub_tasks", [])
            
            if len(sub_tasks) <= 1:
                logger.info("ℹ️ 需求判定为单一任务，使用串行模式")
                return {"is_parallel": False}
            
            logger.info(f"✅ 需求判定为并行任务，共 {len(sub_tasks)} 个子任务")
            
            # 2. 准备并行执行
            # 创建一个队列用于收集事件
            event_queue = asyncio.Queue()
            
            # 启动后台任务执行并行处理
            asyncio.create_task(self._execute_parallel_tasks(sub_tasks, event_queue, session_id))
            
            # 返回事件流生成器
            return {
                "is_parallel": True,
                "stream_events": self._event_generator(event_queue),
                "merged_result": None # 结果将通过事件流的最后一个事件传递，或者这里预留
            }
            
        except Exception as e:
            logger.error(f"❌ 需求分析失败：{e}，回退到串行模式")
            return {"is_parallel": False}

    async def _execute_parallel_tasks(self, sub_tasks: list, event_queue: asyncio.Queue, session_id: str = None):
        """执行并行任务并将事件放入队列"""
        import time as _time
        start_ts = _time.time()

        # 提前映射 engineer_id（前端用 RD/UI/PK 匹配工程师卡片）
        eng_type_map = {"RD": "RD", "UI": "UI", "PK": "PK"}
        task_ids = []
        for t in sub_tasks:
            eid = t.get("engineer_type", "RD").upper()
            task_ids.append(eng_type_map.get(eid, "RD"))

        try:
            # 推送开始事件
            await event_queue.put({
                "event": "parallel_start",
                "data": {
                    "total_tasks": len(sub_tasks),
                    "task_ids": task_ids,
                    "session_id": session_id
                }
            })

            # 并行执行所有子任务
            results = await asyncio.gather(
                *[self._run_sub_task(task_info, event_queue, session_id) for task_info in sub_tasks]
            )

            # 合并结果，推送为 message 事件供前端显示
            merged_result = self._merge_results(results)
            await event_queue.put({
                "event": "message",
                "data": {"type": "message", "content": merged_result}
            })

            total_ms = int((_time.time() - start_ts) * 1000)

            # 推送完成事件
            await event_queue.put({
                "event": "parallel_complete",
                "data": {
                    "all_tasks_done": True,
                    "total_time_ms": total_ms,
                    "merged_result": merged_result
                }
            })

        except Exception as e:
            logger.error(f"❌ 并行执行出错：{e}")
            await event_queue.put({
                "event": "error",
                "data": {"type": "error", "message": str(e), "content": str(e)}
            })
        finally:
            await event_queue.put(None)

    async def _event_generator(self, event_queue: asyncio.Queue):
        """从队列中读取事件并生成 SSE 格式数据"""
        while True:
            event = await event_queue.get()
            if event is None:
                break
            
            # 格式化为 SSE 字符串 (event: ... \n data: ... \n\n)
            # 或者直接返回字典，让上层处理格式。根据 software_company.py 的用法，这里直接 yield 字典或 formatted string
            # app.py 里的 Service.create_message yield 的是 json 字符串或 event 字符串
            # 用户提示中的格式是：
            # event: parallel_start
            # data: {...}
            
            event_name = event["event"]
            data = json.dumps(event["data"], ensure_ascii=False)
            sse_message = f"event: {event_name}\ndata: {data}\n\n"
            yield sse_message

    async def _run_sub_task(self, sub_task: dict, event_queue: asyncio.Queue, session_id: str = None) -> dict:
        """执行单个子任务（直接调用 LLM，并保存到本地文件）"""
        task_id = sub_task["task_id"]
        description = sub_task["description"]
        engineer_type_hint = sub_task.get("engineer_type", "RD")

        # 1. 根据 engineer_type_hint 选择工程师实例获取 profile
        eng_map = {"RD": EngineerRD(), "UI": EngineerUI(), "PK": EngineerPK()}
        engineer_instance = eng_map.get(engineer_type_hint.upper(), EngineerRD())
        engineer_id = engineer_type_hint.upper() if engineer_type_hint.upper() in eng_map else "RD"

        # 系统提示根据工程师类型定制
        system_prompts = {
            "RD": "你是一位编程教学专家，负责生成带详细中文注释的教学示例代码，覆盖初级到高级难度层次。",
            "UI": "你是一位教学可视化设计师，负责生成教学图解、算法流程图和数据结构示意图（使用 Mermaid 或文字描述）。",
            "PK": "你是一位习题设计专家，负责生成配套练习题、测试用例、标准答案及常见错误提示。"
        }
        system_prompt = system_prompts.get(engineer_id, system_prompts["RD"])

        # 推送任务开始进度
        await event_queue.put({
            "event": "task_progress",
            "data": {
                "task_id": engineer_id,
                "engineer_id": engineer_id,
                "engineer": engineer_instance.profile,
                "status": "running",
                "progress": 10,
                "session_id": session_id
            }
        })

        try:
            import aiohttp as _aiohttp
            import os as _os
            from pathlib import Path

            api_key = _os.environ.get("OPENAI_API_KEY", "")
            base_url = _os.environ.get("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

            payload = {
                "model": "deepseek-v3.2-exp",
                "max_tokens": 4096,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": description}
                ]
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            # 推送中间进度
            await event_queue.put({
                "event": "task_progress",
                "data": {
                    "task_id": engineer_id,
                    "engineer_id": engineer_id,
                    "engineer": engineer_instance.profile,
                    "status": "running",
                    "progress": 50,
                    "session_id": session_id
                }
            })

            async with _aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/chat/completions",
                    json=payload, headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=120)
                ) as resp:
                    resp_data = await resp.json()

            result_content = resp_data["choices"][0]["message"]["content"]

            # ── 持久化：保存到 workspace ──
            if session_id:
                try:
                    # 确定保存路径
                    from metagpt.config import CONFIG as _CONFIG
                    ws_root = getattr(_CONFIG, "WORKSPACE_PATH", Path("workspace") / session_id)
                    ws_root.mkdir(parents=True, exist_ok=True)
                    
                    # 确定文件名
                    ext = ".py" if engineer_id == "RD" else ".md"
                    filename = f"task_{engineer_id}{ext}"
                    filepath = ws_root / filename
                    
                    # 写入文件
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(result_content)
                    logger.info(f"💾 已保存并行子任务文件: {filepath}")
                    
                    # 同时保存一份需求描述
                    with open(ws_root / "requirement.txt", "w", encoding="utf-8") as f:
                        f.write(description)
                except Exception as _se:
                    logger.warning(f"⚠️ 保存并行子任务文件失败: {_se}")

            # 推送进度完成
            await event_queue.put({
                "event": "task_progress",
                "data": {
                    "task_id": engineer_id,
                    "engineer_id": engineer_id,
                    "engineer": engineer_instance.profile,
                    "status": "running",
                    "progress": 100,
                    "session_id": session_id
                }
            })

            # 推送任务完成
            await event_queue.put({
                "event": "task_complete",
                "data": {
                    "task_id": engineer_id,
                    "engineer_id": engineer_id,
                    "engineer": engineer_instance.profile,
                    "status": "done",
                    "preview": result_content[:80] + "..." if len(result_content) > 80 else result_content,
                    "session_id": session_id
                }
            })

            # ── LLM 质量评分并写入 SQLite ──
            try:
                from performance_db import PerformanceDB, QUALITY_EVAL_PROMPT as _QPROMPT
                import json as _json2
                eval_prompt = _QPROMPT.format(
                    content=result_content[:3000],
                    requirement=description[:500]
                )
                eval_payload = {
                    "model": "deepseek-v3.2-exp",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": eval_prompt}]
                }
                async with _aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{base_url}/chat/completions",
                        json=eval_payload, headers=headers,
                        timeout=_aiohttp.ClientTimeout(total=60)
                    ) as eresp:
                        edata = await eresp.json()
                raw_eval = edata["choices"][0]["message"]["content"].strip()
                raw_eval = raw_eval.replace("```json", "").replace("```", "").strip()
                eval_result = _json2.loads(raw_eval)
                score = float(eval_result.get("score", 7.0))
                reason = eval_result.get("reason", "")
                dimensions = eval_result.get("dimensions", {})

                task_type_map = {"RD": "python_teaching", "UI": "visualization", "PK": "exercise"}
                task_type = task_type_map.get(engineer_id, "teaching")

                db = PerformanceDB()
                db.save_score(engineer_id, task_type, description, score, reason, dimensions, session_id=session_id)

                # 推送 quality_score 事件供前端展示
                await event_queue.put({
                    "event": "quality_score",
                    "data": {
                        "engineer_id": engineer_id,
                        "score": score,
                        "reason": reason,
                        "dimensions": dimensions,
                        "task_type": task_type,
                        "type": "quality_score",
                        "session_id": session_id
                    }
                })
                logger.info(f"✅ 并行子任务评分完成：{engineer_id} = {score} (Session: {session_id})")
            except Exception as _qe:
                logger.warning(f"⚠️ 并行子任务质量评分失败：{_qe}")

            return {
                "task_id": task_id,
                "engineer_id": engineer_id,
                "description": description,
                "engineer": engineer_instance.profile,
                "result": result_content
            }

        except Exception as e:
            logger.error(f"❌ 子任务 {task_id} 执行失败：{e}")
            await event_queue.put({
                "event": "task_progress",
                "data": {
                    "task_id": engineer_id,
                    "engineer_id": engineer_id,
                    "engineer": engineer_instance.profile,
                    "status": "error",
                    "progress": 0
                }
            })
            return {
                "task_id": task_id,
                "engineer_id": engineer_id,
                "description": description,
                "engineer": engineer_instance.profile,
                "result": f"执行失败: {str(e)}"
            }

    def _merge_results(self, sub_results: list) -> str:
        """合并子任务结果"""
        md_lines = ["# 并行任务执行报告\n"]
        for res in sub_results:
            md_lines.append(f"## 任务 {res['task_id']}: {res['description']}")
            md_lines.append(f"**执行工程师**: {res['engineer']}")
            md_lines.append(f"\n{res['result']}\n")
            md_lines.append("-" * 30)
        return "\n".join(md_lines)

if __name__ == '__main__':
    dispatcher = AHPEngineerDispatcher()
    
    test_cases = [
        ("请生成Python冒泡排序的教学代码，加详细中文注释", "代码示例工程师"),
        ("画一个二叉树BFS算法的流程图", "可视化设计工程师"),
        ("出三道关于链表的练习题，附标准答案", "习题设计工程师")
    ]
    
    print("\n===== 单元测试验证分派准确性 =====")
    for query, expected in test_cases:
        result = dispatcher.dispatch_engineer(query)
        engineer_name = result['engineer']
        is_pass = engineer_name == expected
        status = "✅ PASS" if is_pass else f"❌ FAIL (Expected: {expected}, Got: {engineer_name})"
        print(f"输入: {query}")
        print(f"预期: {expected}")
        print(f"结果: {engineer_name}")
        print(f"状态: {status}")
        print("-" * 30)
