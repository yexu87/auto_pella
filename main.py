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
ENV_VAR_NAME = "PELLA_BATCH"
LOGIN_URL = "https://www.pella.app/login"
SERVER_URL_TEMPLATE = "https://www.pella.app/server/{server_id}"

# ================= 辅助函数 =================
def setup_xvfb():
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        return display
    return None

def mask_email(email):
    if "@" not in email: return email
    name, domain = email.split("@")
    if len(name) > 3:
        return f"{name[:2]}***{name[-1]}@{domain}"
    return f"{name[:1]}***@{domain}"

def get_beijing_time():
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
    os.makedirs("screenshots", exist_ok=True)
    
    parts = [p.strip() for p in account_line.split(",")]
    if len(parts) < 3:
        print(f"❌ 账号格式错误: {account_line}")
        return

    email, password, server_id = parts[0], parts[1], parts[2]
    tg_token = parts[3] if len(parts) > 3 else None
    tg_chat_id = parts[4] if len(parts) > 4 else None

    # 初始化日志
    log = {
        "account": mask_email(email),
        "ip": "Unknown",
        "status": "Unknown",
        "expiry": "Unknown",
        "renew_status": "无需续期",
        "hint": "",
        "logs": []
    }

    print(f"🚀 开始处理: {log['account']}")

    with SB(uc=True, test=True, locale="en") as sb:
        try:
            # ----------------- 1. 登录流程 -----------------
            print("👉 进入登录页...")
            sb.uc_open_with_reconnect(LOGIN_URL, 6)
            
            try: sb.uc_gui_click_captcha(); sb.sleep(2)
            except: pass

            # === 输入邮箱 ===
            print("👉 输入邮箱...")
            sb.type('input[name="identifier"]', email + "\n")
            sb.sleep(5) 

            # === 输入密码 ===
            print("👉 输入密码...")
            if not sb.is_element_visible('input[name="password"]'):
                if sb.is_element_visible('button:contains("Continue")'):
                    sb.uc_click('button:contains("Continue")')
                    sb.sleep(3)
            
            sb.wait_for_element('input[name="password"]', timeout=15)
            sb.type('input[name="password"]', password + "\n")
            sb.sleep(5)
            
            print("👉 等待 Dashboard...")
            sb.wait_for_element('a[href*="/server/"]', timeout=30)
            print("✅ 登录成功")

            # ----------------- 2. 进入服务器 -----------------
            target_url = SERVER_URL_TEMPLATE.format(server_id=server_id)
            print(f"👉 跳转服务器: {target_url}")
            sb.open(target_url)
            sb.sleep(8) 

            # ----------------- 3. 提取信息与操作 -----------------
            
            # [A] 获取 IP
            try:
                page_text = sb.get_text("body")
                ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', page_text)
                valid_ips = [ip for ip in ips if not ip.startswith("127.") and not ip.startswith("255.") and "0.0.0.0" not in ip]
                if valid_ips: log["ip"] = valid_ips[0]
                elif "0.0.0.0" in page_text: log["ip"] = "0.0.0.0"
                else: log["ip"] = f"ID: {server_id[:6]}..."
            except: pass

            # [B] 判断服务器状态 & 强力启动逻辑
            # XPath 忽略大小写
            stop_xpath = "//button[contains(translate(., 'STOP', 'stop'), 'stop')]"
            start_xpath = "//button[contains(translate(., 'START', 'start'), 'start')]"

            # 先检查是否有 STOP (红色按钮)，如果有就是运行中
            if sb.is_element_visible(stop_xpath):
                print("✅ 状态: 运行中")
                log["status"] = "运行中"
            
            elif sb.is_element_visible(start_xpath):
                print("⚠️ 状态: 已停止，准备强力启动...")
                log["status"] = "已停止"
                
                # --- JS 强力启动 ---
                try:
                    # 1. 获取按钮元素
                    start_btn = sb.find_element(start_xpath)
                    
                    # 2. 使用 JavaScript 直接执行点击 (不经过鼠标模拟)
                    print("👉 JS 触发启动事件...")
                    sb.execute_script("arguments[0].click();", start_btn)
                    sb.sleep(5)
                    
                    # 3. 检查控制台是否有反应 (截图30显示会有 Starting...)
                    console_text = sb.get_text("div.rounded-lg.bg-gray-900") # 尝试获取控制台区域文本
                    if "Starting" in console_text:
                        print("✅ 检测到控制台正在启动...")
                        log["logs"].append("指令已发送(Console确认)")
                    
                    # 4. 强制刷新页面检查状态 (Pella 状态变绿很慢)
                    print("👉 刷新页面检查状态...")
                    sb.refresh()
                    sb.sleep(8) # 等待加载
                    
                    if sb.is_element_visible(stop_xpath):
                        print("✅ 启动成功！(状态已变红)")
                        log["status"] = "运行中"
                        log["logs"].append("启动成功")
                    else:
                        log["status"] = "启动中/未知"
                        log["logs"].append("已尝试启动但未变绿")
                        
                except Exception as e:
                    print(f"❌ 启动失败: {e}")
                    log["logs"].append("启动JS执行失败")
            else:
                log["status"] = "找不到状态按钮"

            # [C] 获取到期时间
            try:
                if sb.is_element_visible("//*[contains(text(), 'expires in')]"):
                    expiry_el = sb.find_element("//*[contains(text(), 'expires in')]")
                    match = re.search(r"expires in\s+([0-9D\sHM]+)", expiry_el.text)
                    if match: log["expiry"] = match.group(1).strip()
                    else: log["expiry"] = "解析失败"
                else:
                    log["expiry"] = "未找到时间文本"
            except:
                log["expiry"] = "Error"

            if "D" in log["expiry"]: log["hint"] = "剩余 > 24小时"
            else: log["hint"] = "⚠️ 剩余 < 24小时"

            # [D] 续期检测 (纯查找，不等待)
            print("👉 检查续期按钮...")
            all_buttons = sb.find_elements("button")
            
            claimed_cnt = 0
            click_cnt = 0
            
            for btn in all_buttons:
                try:
                    txt = btn.text
                    if "Claimed" in txt:
                        claimed_cnt += 1
                    elif "Claim" in txt and "Claimed" not in txt:
                        print(f"👉 JS点击续期: {txt}")
                        # 同样使用 JS 点击续期按钮
                        sb.execute_script("arguments[0].click();", btn)
                        click_cnt += 1
                        sb.sleep(3)
                except: pass

            if click_cnt > 0: log["renew_status"] = f"成功续期 {click_cnt} 次"
            elif claimed_cnt > 0: log["renew_status"] = "无需续期"
            else: log["renew_status"] = "无可用按钮"

        except Exception as e:
            print(f"❌ 发生错误: {e}")
            log["status"] = "脚本出错"
            log["logs"].append(f"Err: {str(e)[:30]}")
            ts = int(time.time())
            sb.save_screenshot(f"screenshots/err_{ts}.png")
        
        finally:
            send_report(log, tg_token, tg_chat_id)

def send_report(log, token, chat_id):
    header_emoji = "ℹ️"
    if "启动" in "".join(log["logs"]): header_emoji = "⚠️"
    if "成功续期" in log["renew_status"]: header_emoji = "🎉"
    if "出错" in log["status"] or "Err" in "".join(log["logs"]): header_emoji = "❌"

    action_text = "无需续期"
    if "启动" in "".join(log["logs"]):
        action_text = "执行了启动操作"
    elif "成功续期" in log["renew_status"]:
        action_text = log["renew_status"]
    elif "出错" in log["status"]:
        action_text = "脚本执行出错"

    msg = f"""
<b>🎮 Pella 续期通知</b>
🆔 账号: <code>{log['account']}</code>
🖥 IP: <code>{log['ip']}</code>
⏰ 时间: {get_beijing_time()}

{header_emoji} <b>{action_text}</b>
📊 状态: <b>{log['status']}</b>
⏳ 剩余: {log['expiry']}
💡 提示: {log['hint']}
"""
    if log["logs"]:
        msg += f"\n📝 日志: {' | '.join(log['logs'])}"

    send_telegram(token, chat_id, msg)

if __name__ == "__main__":
    batch_data = os.getenv(ENV_VAR_NAME)
    if not batch_data: sys.exit(1)
    
    display = setup_xvfb()
    for line in batch_data.strip().splitlines():
        if line.strip() and not line.startswith("#"):
            run_pella_task(line)
            time.sleep(5)
    if display: display.stop()
