
import asyncio
from software_company import SoftwareCompany
from metagpt.schema import Message

async def test_requirement_1():
    print("\n--- Testing Requirement 1 (Parallel Dispatch) ---")
    sc = SoftwareCompany()
    query = "请帮我生成 Python 冒泡排序的代码示例，并且画出它的算法流程图，最后出一道相关的练习题"
    async for event in sc.run(Message(query)):
        if "event:" in str(event):
            print(event)

async def test_requirement_2():
    print("\n--- Testing Requirement 2 (Adaptive Performance) ---")
    sc = SoftwareCompany()
    # This query should trigger serial mode and scoring
    query = "请生成一个 Python 的列表推导式教学示例"
    async for event in sc.run(Message(query)):
        if "event: quality_score" in str(event):
            print("Quality score generated!")
            print(event)

if __name__ == "__main__":
    asyncio.run(test_requirement_1())
    asyncio.run(test_requirement_2())
