import requests  # 这个是Anaconda默认就有的，不用额外装


def test_deepseek_sync():
    """同步测试DeepSeek API连通性，无需异步插件"""
    # ========== 替换成你的配置 ==========
    api_key = "YOUR_DASHSCOPE_API_KEY"  # 你的DashScope API Key（示例脚本，请从环境变量注入）
    model = "deepseek-v3.2-exp"  # 用稳定版，别用exp实验版

    # 请求配置
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": "hello，请回复一个简单的测试消息"}],
        "temperature": 0.1
    }

    # 发送请求（设置60秒超时）
    try:
        print("开始测试DeepSeek API连接...")
        response = requests.post(
            url=url,
            headers=headers,
            json=data,
            timeout=60  # 延长超时时间到60秒
        )

        # 打印结果
        print(f"✅ 请求状态码: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print(f"✅ API响应成功: {result['choices'][0]['message']['content']}")
        else:
            print(f"❌ API返回错误: {response.status_code} - {response.text}")

    except requests.exceptions.Timeout:
        print("❌ 超时错误：无法在60秒内收到响应（网络/代理问题）")
    except requests.exceptions.ConnectionError:
        print("❌ 连接错误：无法连接到API服务器（网络/代理/防火墙问题）")
    except requests.exceptions.InvalidHeader:
        print("❌ 头部错误：API Key格式错误，请检查")
    except Exception as e:
        print(f"❌ 其他错误：{type(e).__name__} - {str(e)}")


# 直接运行测试
if __name__ == "__main__":
    test_deepseek_sync()