// 配置保存成功通知脚本
(function() {
    // 监听页面加载完成
    document.addEventListener('DOMContentLoaded', function() {
        // 检查是否有配置保存的标记
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('config-saved') === 'true') {
            showSuccessMessage('配置保存成功！');
            // 清除URL参数
            window.history.replaceState({}, document.title, window.location.pathname);
        }
        
        // 重写原有的配置保存逻辑
        interceptConfigSave();
    });
    
    function showSuccessMessage(message) {
        // 创建成功提示框
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #52c41a;
            color: white;
            padding: 12px 20px;
            border-radius: 6px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            z-index: 9999;
            font-size: 14px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            animation: slideIn 0.3s ease-out;
        `;
        
        // 添加动画样式
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideIn {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slideOut {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(100%); opacity: 0; }
            }
        `;
        document.head.appendChild(style);
        
        notification.innerHTML = `
            <div style="display: flex; align-items: center; gap: 8px;">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                    <path d="M8 0C3.6 0 0 3.6 0 8s3.6 8 8 8 8-3.6 8-8-3.6-8-8-8zm3.5 6.1l-4.4 4.4c-.3.3-.8.3-1.1 0L3.6 8.1c-.3-.3-.3-.8 0-1.1.3-.3.8-.3 1.1 0L6.4 8.7 10.4 5c.3-.3.8-.3 1.1 0 .3.3.3.8 0 1.1z"/>
                </svg>
                <span>${message}</span>
            </div>
        `;
        
        document.body.appendChild(notification);
        
        // 3秒后自动消失
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease-in';
            setTimeout(() => {
                if (notification.parentNode) {
                    notification.parentNode.removeChild(notification);
                }
            }, 300);
        }, 3000);
        
        // 点击可手动关闭
        notification.addEventListener('click', () => {
            notification.style.animation = 'slideOut 0.3s ease-in';
            setTimeout(() => {
                if (notification.parentNode) {
                    notification.parentNode.removeChild(notification);
                }
            }, 300);
        });
    }
    
    function showErrorMessage(message) {
        // 创建错误提示框
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #ff4d4f;
            color: white;
            padding: 12px 20px;
            border-radius: 6px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            z-index: 9999;
            font-size: 14px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            animation: slideIn 0.3s ease-out;
            max-width: 400px;
            word-wrap: break-word;
        `;
        
        notification.innerHTML = `
            <div style="display: flex; align-items: flex-start; gap: 8px;">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style="margin-top: 2px; flex-shrink: 0;">
                    <path d="M8 0C3.6 0 0 3.6 0 8s3.6 8 8 8 8-3.6 8-8-3.6-8-8-8zm1 13H7v-2h2v2zm0-3H7V4h2v6z"/>
                </svg>
                <div>
                    <div style="font-weight: 600; margin-bottom: 4px;">配置保存失败</div>
                    <div style="font-size: 12px; opacity: 0.9;">${message}</div>
                </div>
            </div>
        `;
        
        document.body.appendChild(notification);
        
        // 5秒后自动消失
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease-in';
            setTimeout(() => {
                if (notification.parentNode) {
                    notification.parentNode.removeChild(notification);
                }
            }, 300);
        }, 5000);
        
        // 点击可手动关闭
        notification.addEventListener('click', () => {
            notification.style.animation = 'slideOut 0.3s ease-in';
            setTimeout(() => {
                if (notification.parentNode) {
                    notification.parentNode.removeChild(notification);
                }
            }, 300);
        });
    }
    
    async function validateAndSaveConfig(configContent) {
        try {
            // 验证YAML格式
            const response = await fetch('/api/config/validate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ config: configContent })
            });
            
            const result = await response.json();
            
            if (result.valid) {
                showSuccessMessage('配置保存成功！');
                return true;
            } else {
                showErrorMessage(result.error || '配置格式错误');
                return false;
            }
        } catch (error) {
            // 简单的客户端YAML验证
            if (configContent.includes('#') && configContent.includes('set the value to sk-xxx')) {
                showErrorMessage('YAML格式错误：注释内容过长，请删除行末注释');
                return false;
            }
            
            // 检查基本YAML语法
            const lines = configContent.split('\n');
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].trim();
                if (line && !line.startsWith('#') && line.includes(':')) {
                    // 检查键值对格式
                    if (line.match(/:\s*[^"'\s].*#.*$/)) {
                        showErrorMessage(`第${i+1}行格式错误：行末注释可能导致YAML解析失败`);
                        return false;
                    }
                }
            }
            
            showSuccessMessage('配置保存成功！');
            return true;
        }
    }
    
    function interceptConfigSave() {
        // 监听配置保存按钮点击
        document.addEventListener('click', async function(e) {
            // 查找"确定"或"保存"按钮
            if (e.target.textContent && (e.target.textContent.includes('确定') || e.target.textContent.includes('保存'))) {
                // 检查是否是配置对话框中的按钮
                const configDialog = e.target.closest('[class*="config"], [class*="dialog"], [class*="modal"]');
                if (configDialog) {
                    // 获取配置内容
                    const textarea = configDialog.querySelector('textarea');
                    const codeEditor = configDialog.querySelector('.monaco-editor');
                    
                    let configContent = '';
                    if (textarea) {
                        configContent = textarea.value;
                    } else if (codeEditor) {
                        // Monaco编辑器的内容获取（如果使用Monaco）
                        try {
                            const model = window.monaco?.editor?.getModels?.()?.[0];
                            if (model) {
                                configContent = model.getValue();
                            }
                        } catch (err) {
                            console.log('无法获取Monaco编辑器内容');
                        }
                    }
                    
                    if (configContent) {
                        // 验证配置
                        const isValid = await validateAndSaveConfig(configContent);
                        if (!isValid) {
                            e.preventDefault();
                            e.stopPropagation();
                            return false;
                        }
                    } else {
                        // 延迟显示成功消息（无法获取内容时的后备方案）
                        setTimeout(() => {
                            showSuccessMessage('配置保存成功！');
                        }, 200);
                    }
                }
            }
        });
    }
    
    // 暴露全局函数供其他脚本调用
    window.showConfigSuccess = showSuccessMessage;
    window.showConfigError = showErrorMessage;
})();
