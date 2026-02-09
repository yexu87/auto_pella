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
    if len(name) > 3: return f"{name[:2]}***{name[-1]}@{domain}"
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
    parts = [p.strip() for p in account_line.split(",")]
    if len(parts) < 3: return

    email, password, server_id = parts[0], parts[1], parts[2]
    tg_token = parts[3] if len(parts) > 3 else None
    tg_chat_id = parts[4] if len(parts) > 4 else None

    log = {
        "account": mask_email(email), "ip": "Unknown", "status": "Unknown",
        "expiry": "Unknown", "renew_status": "无需续期", "logs": [], "hint": ""
    }
    print(f"🚀 处理: {log['account']}")

    with SB(uc=True, test=True, locale="en") as sb:
        try:
            # --- 1. 登录 (保持不变) ---
            print("👉 登录...")
            sb.uc_open_with_reconnect(LOGIN_URL, 6)
            try: sb.uc_gui_click_captcha(); sb.sleep(2)
            except: pass

            sb.type('input[name="identifier"]', email + "\n")
            sb.sleep(5)
            
            if not sb.is_element_visible('input[name="password"]'):
                if sb.is_element_visible('button:contains("Continue")'): 
                    sb.uc_click('button:contains("Continue")')
            sb.wait_for_element('input[name="password"]', timeout=15)
            sb.type('input[name="password"]', password + "\n")
            sb.wait_for_element('a[href*="/server/"]', timeout=30)
            print("✅ 登录成功")

            # --- 2. 进入服务器 ---
            target_url = SERVER_URL_TEMPLATE.format(server_id=server_id)
            print(f"👉 跳转: {target_url}")
            sb.open(target_url)
            sb.sleep(8) 

            # --- 3. 获取信息 ---
            try:
                txt = sb.get_text("body")
                ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', txt)
                valid = [i for i in ips if not i.startswith("127.") and "0.0.0.0" not in i]
                log["ip"] = valid[0] if valid else ("0.0.0.0" if "0.0.0.0" in txt else "ID: "+server_id[:6])
                
                match = re.search(r"expires in\s+([0-9D\sHM]+)", txt)
                log["expiry"] = match.group(1).strip() if match else "Error"
            except: pass
            
            if "D" in log["expiry"]: log["hint"] = "剩余 > 24小时"
            else: log["hint"] = "⚠️ 剩余 < 24小时"

            # ===============================================
            # ⚡️ 核心修复：精准定位 START/STOP 按钮
            # ===============================================
            
            # 定义精准的选择器 (基于你提供的源码)
            # STOP 按钮通常是红色的 bg-brand-red 或包含 STOP 文字
            STOP_SELECTOR = "button:contains('STOP')"
            # START 按钮类名包含 bg-brand-green
            START_SELECTOR = "button.bg-brand-green" 

            print("👉 检查服务器状态...")
            
            # 1. 优先检查是否正在运行 (STOP 按钮存在)
            if sb.is_element_visible(STOP_SELECTOR):
                print("✅ 状态: 运行中 (找到 STOP 按钮)")
                log["status"] = "运行中"
            
            # 2. 检查是否需要启动 (START 按钮存在)
            elif sb.is_element_visible(START_SELECTOR):
                print("⚠️ 状态: 已停止 (找到 START 按钮)")
                log["status"] = "已停止"
                
                print("👉 执行启动操作...")
                # 获取按钮元素
                start_btn = sb.find_element(START_SELECTOR)
                
                # 使用 JS 点击 (最稳妥)
                sb.execute_script("arguments[0].click();", start_btn)
                sb.sleep(2)
                
                # 二次确认：有时点击没反应，再点一次
                if sb.is_element_visible(START_SELECTOR):
                    print("👉 再次尝试点击...")
                    sb.click(START_SELECTOR)
                
                sb.sleep(5)
                
                # 检查控制台日志 (源码显示 console 在 pre 标签里)
                console_text = sb.get_text("pre")
                if "Starting" in console_text or "Booting" in console_text:
                    log["status"] = "启动指令已发"
                    log["logs"].append("已触发启动")
                else:
                    # 刷新页面看状态变了没
                    sb.refresh()
                    sb.sleep(5)
                    if sb.is_element_visible(STOP_SELECTOR):
                        log["status"] = "启动成功"
                    else:
                        log["logs"].append("点击后无反应")
            else:
                # 兜底逻辑：如果在源码里找不到特定 class，尝试找文字
                print("⚠️ 未找到标准按钮，尝试文字匹配...")
                if sb.is_element_visible("//button[contains(., 'START')]"):
                    sb.execute_script("arguments[0].click();", sb.find_element("//button[contains(., 'START')]"))
                    log["logs"].append("触发备用启动")
                    log["status"] = "尝试启动(备用)"
                else:
                    log["status"] = "按钮定位失败"

            # --- 续期处理 (根据源码优化) ---
            print("👉 检查续期...")
            # 源码显示 Claim 按钮可能是 <a> 标签且包含 'Claim'
            # <a ...>Claimed...</a>
            # 我们查找所有包含 Claim 文本的 <a> 或 <button>
            
            claim_candidates = sb.find_elements("a:contains('Claim')") + sb.find_elements("button:contains('Claim')")
            
            clicked_cnt = 0
            claimed_cnt = 0
            
            for el in claim_candidates:
                try:
                    txt = el.text
                    if "Claimed" in txt:
                        claimed_cnt += 1
                    elif "Claim" in txt: # 未领取的按钮
                        print(f"👉 点击续期: {txt}")
                        sb.execute_script("arguments[0].click();", el)
                        clicked_cnt += 1
                        sb.sleep(2)
                except: pass
            
            if clicked_cnt > 0: log["renew_status"] = f"成功续期 {clicked_cnt} 次"
            elif claimed_cnt > 0: log["renew_status"] = "无需续期"
            else: log["renew_status"] = "无可用按钮"

        except Exception as e:
            print(f"❌ 错误: {e}")
            log["logs"].append(f"Err: {str(e)[:30]}")
        finally:
            send_report(log, tg_token, tg_chat_id)

def send_report(log, token, chat_id):
    header = "ℹ️"
    if "启动" in "".join(log["logs"]): header = "⚠️"
    if "成功续期" in log["renew_status"]: header = "🎉"
    
    act = "无需续期"
    if "启动" in "".join(log["logs"]) or "启动" in log["status"]: act = "执行了启动操作"
    elif "成功续期" in log["renew_status"]: act = log["renew_status"]

    msg = f"""
<b>🎮 Pella 续期通知</b>
🆔 账号: <code>{log['account']}</code>
🖥 IP: <code>{log['ip']}</code>
⏰ 时间: {get_beijing_time()}

{header} <b>{act}</b>
📊 状态: <b>{log['status']}</b>
⏳ 剩余: {log['expiry']}
💡 提示: {log['hint']}
"""
    if log["logs"]: msg += f"\n📝 日志: {' | '.join(log['logs'])}"
    send_telegram(token, chat_id, msg)

if __name__ == "__main__":
    batch = os.getenv(ENV_VAR_NAME)
    if not batch: sys.exit(1)
    display = setup_xvfb()
    for line in batch.strip().splitlines():
        if line.strip() and not line.startswith("#"):
            run_pella_task(line)
            time.sleep(5)
    if display: display.stop()
