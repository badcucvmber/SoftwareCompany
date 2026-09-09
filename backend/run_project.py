import asyncio
from specialized_engineers import AHPEngineerDispatcher

async def main():
    # ① 用户需求（你之后可以换成任何需求）
    user_requirement = (
        "请你用 Python（Pygame）实现一个界面简洁、操作直观的五子棋小游戏，"
        "并给出完整可运行代码"
    )

    # ② 创建分派器
    dispatcher = AHPEngineerDispatcher()

    # ③ 分派工程师
    engineer, scores = dispatcher.dispatch_engineer(user_requirement)

    print("✅ 分派到工程师：", engineer.profile)

    # ④ 🔥 这一句是“点火开关”
    await engineer.run(user_requirement)

    print("🎉 代码生成流程已执行完成")

if __name__ == "__main__":
    asyncio.run(main())
