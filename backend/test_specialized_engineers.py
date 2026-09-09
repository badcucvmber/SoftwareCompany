#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
专精工程师测试脚本
测试智能工程师分派和功能
"""

import asyncio
from software_company import SoftwareCompany
from specialized_engineers import SmartEngineerDispatcher, EngineerRD, EngineerUI, EngineerPK
from metagpt.schema import Message


async def test_engineer_dispatcher():
    """测试工程师智能分派功能"""
    print("=== 测试工程师智能分派功能 ===")
    
    dispatcher = SmartEngineerDispatcher()
    
    # 测试不同类型的项目需求
    test_cases = [
        {
            "description": "创建一个响应式的电商网站，包含购物车和支付功能",
            "expected": "EngineerRD"
        },
        {
            "description": "设计一个现代化的移动应用界面，注重用户体验",
            "expected": "EngineerUI"
        },
        {
            "description": "开发一个数据分析工具，使用Python进行机器学习",
            "expected": "EngineerPK"
        },
        {
            "description": "创建一个简单的贪吃蛇游戏",
            "expected": "EngineerUI"
        }
    ]
    
    for i, case in enumerate(test_cases, 1):
        engineer = dispatcher.dispatch_engineer(case["description"])
        print(f"测试 {i}: {case['description'][:30]}...")
        print(f"  分派工程师: {engineer.profile}")
        print(f"  预期类型: {case['expected']}")
        print(f"  匹配: {'✓' if engineer.profile == case['expected'] else '✗'}")
        print()


async def test_software_company_with_specialized_engineers():
    """测试启用专精工程师的软件公司"""
    print("=== 测试专精工程师软件公司 ===")
    
    # 创建启用专精工程师的软件公司
    company = SoftwareCompany(enable_specialized_engineers=True)
    
    print("当前工程师信息:")
    current_info = company.get_current_engineer_info()
    print(f"  Profile: {current_info['profile']}")
    print(f"  Name: {current_info['name']}")
    print(f"  Specialization: {current_info['specialization']}")
    print()
    
    print("所有可用工程师:")
    all_engineers = company.get_available_engineers()
    for engineer in all_engineers:
        active_marker = " [当前]" if engineer['is_active'] else ""
        print(f"  - {engineer['name']} ({engineer['profile']}){active_marker}")
        print(f"    {engineer['specialization']}")
    print()
    
    # 测试智能切换工程师
    print("测试智能工程师切换:")
    
    # Web项目
    web_message = Message("开发一个RESTful API服务器，使用FastAPI框架")
    print(f"项目需求: {web_message.content}")
    company.recv(web_message)
    new_info = company.get_current_engineer_info()
    print(f"切换后工程师: {new_info['profile']} - {new_info['name']}")
    print()
    
    # UI项目
    ui_message = Message("设计一个现代化的用户界面，包含响应式布局和动画效果")
    print(f"项目需求: {ui_message.content}")
    company.recv(ui_message)
    new_info = company.get_current_engineer_info()
    print(f"切换后工程师: {new_info['profile']} - {new_info['name']}")
    print()
    
    # Python项目
    python_message = Message("开发一个机器学习模型，进行数据分析和预测")
    print(f"项目需求: {python_message.content}")
    company.recv(python_message)
    new_info = company.get_current_engineer_info()
    print(f"切换后工程师: {new_info['profile']} - {new_info['name']}")
    print()


async def test_traditional_vs_specialized():
    """对比传统工程师vs专精工程师"""
    print("=== 对比传统工程师 vs 专精工程师 ===")
    
    # 传统软件公司
    traditional_company = SoftwareCompany(enable_specialized_engineers=False)
    print("传统软件公司:")
    traditional_info = traditional_company.get_current_engineer_info()
    print(f"  工程师: {traditional_info['profile']} - {traditional_info['specialization']}")
    
    # 专精软件公司
    specialized_company = SoftwareCompany(enable_specialized_engineers=True)
    print("\n专精工程师软件公司:")
    specialized_engineers = specialized_company.get_available_engineers()
    for engineer in specialized_engineers:
        print(f"  工程师: {engineer['profile']} - {engineer['specialization']}")
    print()


def test_individual_engineers():
    """测试单个专精工程师"""
    print("=== 测试单个专精工程师 ===")
    
    engineers = [
        ("网站开发工程师", EngineerRD()),
        ("界面设计工程师", EngineerUI()),
        ("Python编程工程师", EngineerPK())
    ]
    
    for name, engineer in engineers:
        print(f"{name}:")
        print(f"  Name: {engineer.name}")
        print(f"  Profile: {engineer.profile}")
        print(f"  Actions: {[action.__class__.__name__ for action in engineer.actions]}")
        print()


async def main():
    """主测试函数"""
    print("开始测试专精工程师功能...\n")
    
    try:
        # 测试智能分派器
        await test_engineer_dispatcher()
        
        # 测试单个工程师
        test_individual_engineers()
        
        # 测试专精工程师软件公司
        await test_software_company_with_specialized_engineers()
        
        # 对比测试
        await test_traditional_vs_specialized()
        
        print("✓ 所有测试完成！")
        
    except Exception as e:
        print(f"✗ 测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
