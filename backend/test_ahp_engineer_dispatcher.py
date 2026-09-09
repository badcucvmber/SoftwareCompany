import pytest
import math
import numpy as np
from specialized_engineers import (
    AHPEngineerDispatcher,
    EngineerRD,
    EngineerUI,
    EngineerPK,
    SymbiosisCalculator,
    RTSMConfig
)


def test_config_loading():
    """验证配置加载功能是否正常"""
    print("=== 验证配置加载功能 ===")
    config = RTSMConfig()
    # 测试多级路径读取
    assert config.get("weight.base_score") == 0.6
    assert config.get("ss.gamma_threshold.ui") == 0.4
    assert config.get("tqg.alpha_beta.high_complexity.alpha") == 0.4
    # 测试默认值返回
    assert config.get("nonexistent.key", "default") == "default"
    print("✅ 配置加载功能测试通过")


def test_parameter_configurability():
    """验证核心参数可配置性（修改配置后计算结果应变化）"""
    print("=== 验证核心参数可配置性 ===")
    # 1. 测试EEP标准化系数
    config = RTSMConfig()
    original_coeff = config.get("eep.normalize_coeff")
    # 临时修改配置（模拟修改配置文件）
    config._config["eep"]["normalize_coeff"] = 5
    # 计算EEP（系数改为5，结果应与原系数10时有两倍关系）
    engineer = EngineerUI()
    project_skills = ["figma", "ui design"]
    eep_with_coeff5 = SymbiosisCalculator.calculate_eep(engineer, project_skills)
    # 恢复原始系数
    config._config["eep"]["normalize_coeff"] = original_coeff
    eep_with_coeff10 = SymbiosisCalculator.calculate_eep(engineer, project_skills)
    print(f"EEP（系数5）：{eep_with_coeff5:.3f}，EEP（系数10）：{eep_with_coeff10:.3f}（预期约2倍关系）")
    assert abs(eep_with_coeff5 - 2 * eep_with_coeff10) < 0.01, "EEP标准化系数配置未生效"

    # 2. 测试γ阈值
    original_threshold = config.get("ss.gamma_threshold.ui")
    config._config["ss"]["gamma_threshold"]["ui"] = 0.3
    eep_val = 0.35
    tqg_val = 0.35
    ss_with_threshold03 = SymbiosisCalculator.calculate_ss(eep_val, tqg_val, "ui")
    config._config["ss"]["gamma_threshold"]["ui"] = original_threshold
    ss_with_threshold04 = SymbiosisCalculator.calculate_ss(eep_val, tqg_val, "ui")
    print(f"SS（UI阈值0.3）：{ss_with_threshold03:.3f}，SS（UI阈值0.4）：{ss_with_threshold04:.3f}（预期前者更大）")
    assert ss_with_threshold03 > ss_with_threshold04, "γ阈值配置未生效"

    # 3. 测试权重配置
    original_base_weight = config.get("weight.base_score")
    config._config["weight"]["base_score"] = 0.8
    config._config["weight"]["symbiosis_score"] = 0.2
    dispatcher = AHPEngineerDispatcher()
    project_context = "用Figma设计UI界面"
    best_engineer, weights_dict = dispatcher.dispatch_engineer(project_context)
    # 恢复原始权重
    config._config["weight"]["base_score"] = original_base_weight
    config._config["weight"]["symbiosis_score"] = 0.4
    print(f"权重0.8/0.2时EngineerUI得分：{weights_dict['EngineerUI']:.3f}")
    assert weights_dict["EngineerUI"] > 0.2, "权重配置未生效"
    print("✅ 核心参数可配置性测试通过")


def test_gamma_adaptation():
    """验证γ自适应功能（从配置读取参数）"""
    print("=== 验证γ自适应（配置驱动） ===")
    test_scenarios = [
        ("ui", 0.41, 0.41, 1.2),
        ("ui", 0.39, 0.41, 1.0),
        ("python", 0.56, 0.56, 1.2),
    ]
    for project_type, eep, tqg, expected_gamma in test_scenarios:
        ss_value = SymbiosisCalculator.calculate_ss(eep, tqg, project_type)
        gamma_calculated = ss_value / math.sqrt(eep * tqg) if (eep * tqg) > 0 else 1.0
        gamma_calculated = round(gamma_calculated, 1)
        print(f"项目类型：{project_type:<8} EEP={eep:.2f} TQG={tqg:.2f} → 计算γ={gamma_calculated}（预期={expected_gamma}）")
        assert gamma_calculated == expected_gamma


def test_dispatcher_config_integration():
    """测试分派器与配置参数的集成使用"""
    print("=== 测试分派器（配置驱动） ===")
    dispatcher = AHPEngineerDispatcher()
    test_scenarios = [
        {
            "name": "UI项目",
            "description": "用Figma设计移动端UI界面，简单复杂度",
            "expected_eng": "EngineerUI"
        },
        {
            "name": "Python项目",
            "description": "基于Python和pandas做数据分析，复杂项目",
            "expected_eng": "EngineerPK"
        }
    ]
    for scenario in test_scenarios:
        print(f"\n--- {scenario['name']} ---")
        best_engineer, weights_dict = dispatcher.dispatch_engineer(scenario["description"])
        print(f"最佳工程师：{best_engineer.profile}（预期：{scenario['expected_eng']}）")
        print(f"得分：{weights_dict[best_engineer.profile]:.3f}")
        assert best_engineer.profile == scenario["expected_eng"]
        # 打印配置化报告
        print(dispatcher.generate_assignment_report(best_engineer, scenario["description"]))


if __name__ == "__main__":
    test_config_loading()
    test_parameter_configurability()
    test_gamma_adaptation()
    test_dispatcher_config_integration()

    # 交互式模式
    print("\n=== 交互式分派（配置驱动） ===")
    dispatcher = AHPEngineerDispatcher()
    user_input = input("输入项目需求：").strip()
    best_engineer, weights_dict = dispatcher.dispatch_engineer(user_input)
    print(f"\n最佳匹配工程师：{best_engineer.name}（{best_engineer.profile}）")
    print("所有工程师得分：", weights_dict)
    print(dispatcher.generate_assignment_report(best_engineer, user_input))