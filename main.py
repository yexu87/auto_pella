import os
import sys
import time
import re
import platform
import requests
from datetime import datetime, timedelta, timezone
from seleniumbase import SB
from pyvirtualdisplay import Display

# ================= 配置区域 =================
# 环境变量格式: email,password,server_id,tg_token,tg_chat_id
ENV_VAR_NAME = "PELLA_BATCH"

LOGIN_URL = "https://www.pella.app/login"
SERVER_URL_TEMPLATE = "https://www.pella.app/server/{server_id}"

# ================= 辅助函数 =================

def setup_xvfb():
    """Linux下启动虚拟显示"""
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        return display
    return None

def mask_email(email):
    """脱敏邮箱"""
    if "@" not in email: return email
    name, domain = email.split("@")
    if len(name) > 3:
        return f"{name[:2]}***{name[-1]}@{domain}"
    return f"{name[:1]}***@{domain}"

def get_beijing_time():
    """获取北京时间字符串"""
    utc_now = datetime.now(timezone.utc)
    bj_now = utc_now + timedelta(hours=8)
    return bj_now.strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(token, chat_id, message):
    if not token or not chat_id: return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram 发送失败: {e}")

# ================= 核心逻辑 =================

def run_pella_task(account_line):
    parts = [p.strip() for p in account_line.split(",")]
    if len(parts) < 3:
        print(f"❌ 账号格式错误: {account_line}")
        return

    email, password, server_id = parts[0], parts[1], parts[2]
    tg_token = parts[3] if len(parts) > 3 else None
    tg_chat_id = parts[4] if len(parts) > 4 else None

    log_info = {
        "account": mask_email(email),
        "ip": "Unknown",
        "status": "Unknown",
        "expiry": "Unknown",
        "actions": [],
        "hint": ""
    }

    print(f"🚀 开始处理账号: {log_info['account']}")

    # 使用 UC 模式 (Undetected Chromedriver)
    with SB(uc=True, test=True, locale="en") as sb:
        try:
            # 1. 登录 (适配 Clerk 验证系统)
            print("👉 打开登录页面...")
            sb.uc_open_with_reconnect(LOGIN_URL, 6)
            
            # --- 步骤 1: 输入邮箱 ---
            print("👉 等待邮箱输入框 (Clerk)...")
            # Clerk 的邮箱框 name 属性通常是 'identifier'
            sb.wait_for_element('input[name="identifier"]', timeout=20)
            
            print(f"👉 输入邮箱: {email}")
            sb.type('input[name="identifier"]', email)
            sb.sleep(1) # 稍作停顿，模拟真人
            
            print("👉 点击 Continue...")
            sb.click('button:contains("Continue")')
            
            # --- 步骤 2: 输入密码 ---
            print("👉 等待密码输入框...")
            # 等待跳转到输入密码界面 (Clerk 的密码框 name 通常是 'password')
            sb.wait_for_element('input[name="password"]', timeout=20)
            
            print("👉 输入密码...")
            sb.type('input[name="password"]', password)
            sb.sleep(1)
            
            print("👉 点击 Continue 登录...")
            sb.click('button:contains("Continue")')
            
            # --- 步骤 3: 等待登录完成 ---
            # 等待跳转到 Dashboard 或出现服务器列表
            print("👉 等待跳转主页...")
            sb.wait_for_element('a[href*="/server/"]', timeout=30)
            print("✅ 登录成功")

            # 2. 直达服务器详情页
            target_url = SERVER_URL_TEMPLATE.format(server_id=server_id)
            print(f"👉 进入服务器页面: {target_url}")
            sb.open(target_url)
            sb.sleep(6) # 等待页面动态元素加载完毕

            # 3. 获取 IP (尝试在页面寻找 IP 格式文本)
            try:
                body_text = sb.get_text("body")
                ip_match = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', body_text)
                if ip_match:
                    log_info["ip"] = ip_match.group(0)
                else:
                    log_info["ip"] = f"ID: {server_id[:8]}..."
            except:
                pass

            # 4. 检查 Start/Stop 状态
            # 使用更宽泛的选择器防止找不到
            if sb.is_element_visible('button:contains("START")'):
                print("⚠️ 检测到服务器停止，正在启动...")
                sb.click('button:contains("START")')
                log_info["actions"].append("已执行启动")
                sb.sleep(5) # 等待启动请求发送
                log_info["status"] = "启动中 (Starting)"
            elif sb.is_element_visible('button:contains("STOP")'):
                print("✅ 服务器运行中")
                log_info["status"] = "运行中 (Running)"
            else:
                log_info["status"] = "未知状态 (未找到按钮)"

            # 5. 获取剩余时间
            try:
                # 获取页面所有文本进行匹配
                expiry_text_full = sb.get_text("body")
                # 匹配格式: expires in 1D 15H 30M.
                match = re.search(r"expires in\s+([\d\w\s]+)\.", expiry_text_full)
                if match:
                    log_info["expiry"] = match.group(1).strip()
                else:
                    # 尝试查找特定元素文本
                    log_info["expiry"] = "未匹配到时间"
            except Exception as e:
                print(f"时间获取错误: {e}")
                log_info["expiry"] = "获取失败"
            
            # 设置提示信息
            if "D" in log_info["expiry"] or "Day" in log_info["expiry"]:
                 log_info["hint"] = "剩余 > 24小时"
            else:
                 log_info["hint"] = "⚠️ 注意: 剩余时间不足 24 小时"

            # 6. 处理续期 (Claim)
            # 查找所有包含 "Claim" 的按钮
            print("👉 检查续期按钮...")
            claim_buttons = sb.find_elements('button:contains("Claim")')
            clicked_count = 0
            
            if not claim_buttons:
                print("未发现任何 Claim 按钮")
                log_info["actions"].append("无按钮/已满")
            
            for btn in claim_buttons:
                try:
                    txt = btn.text
                    if "Claimed" in txt:
                        continue # 已经领过了
                    
                    # 点击领取
                    print(f"👉 点击续期: {txt}")
                    btn.click()
                    clicked_count += 1
                    sb.sleep(3) # 等待点击反应
                except:
                    pass
            
            if clicked_count > 0:
                log_info["actions"].append(f"成功续期 {clicked_count} 次")
            
            # 如果没有进行启动，也没有续期
            if not log_info["actions"]:
                 log_info["actions"].append("无需操作")

        except Exception as e:
            print(f"❌ 发生错误: {e}")
            log_info["status"] = "脚本执行出错"
            log_info["actions"].append(f"错误: {str(e)[:50]}") # 只截取前50字符防止报错过长
            # 截图保存现场 (可选，方便调试)
            try:
                sb.save_screenshot("error_page.png")
                print("已保存错误截图: error_page.png")
            except:
                pass
        
        finally:
            # 发送 TG 通知
            send_report(log_info, tg_token, tg_chat_id)

def send_report(info, token, chat_id):
    """发送 TG 通知"""
    
    action_str = " | ".join(info["actions"])
    
    # 动态 Emoji
    if "启动" in action_str:
        header_emoji = "⚠️"
        action_summary = "执行了启动操作"
    elif "成功续期" in action_str:
        header_emoji = "🎉"
        action_summary = "成功续期时长"
    elif "错误" in action_str:
        header_emoji = "❌"
        action_summary = "脚本执行出错"
    else:
        header_emoji = "ℹ️"
        action_summary = "无需续期/保活"

    msg = f"""
<b>🎮 Pella 续期通知</b>
🆔 账号: <code>{info['account']}</code>
🖥 IP: <code>{info['ip']}</code>
⏰ 时间: {get_beijing_time()}

{header_emoji} <b>{action_summary}</b>
📊 状态: {info['status']}
⏳ 剩余: <b>{info['expiry']}</b>
💡 提示: {info['hint']}
📝 详情: {action_str}
"""
    print("📤 发送通知中...")
    send_telegram(token, chat_id, msg)

# ================= 主程序入口 =================
if __name__ == "__main__":
    batch_data = os.getenv(ENV_VAR_NAME)
    if not batch_data:
        print(f"❌ 未找到环境变量 {ENV_VAR_NAME}")
        # 本地测试用 (如果环境变量不存在)
        # batch_data = "你的邮箱,密码,ID,Token,ChatID"
        sys.exit(1)
    
    display = setup_xvfb()
    
    lines = batch_data.strip().splitlines()
    for line in lines:
        if not line.strip() or line.startswith("#"): continue
        run_pella_task(line)
        time.sleep(5) # 账号间缓冲
        
    if display:
        display.stop()
