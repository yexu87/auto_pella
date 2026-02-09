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
    os.makedirs("screenshots", exist_ok=True)
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
            # --- 1. 登录 ---
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
            sb.open(target_url)
            sb.sleep(8) 

            # --- 3. 获取信息 ---
            try:
                txt = sb.get_text("body")
                # IP
                ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', txt)
                valid = [i for i in ips if not i.startswith("127.") and "0.0.0.0" not in i]
                log["ip"] = valid[0] if valid else ("0.0.0.0" if "0.0.0.0" in txt else "ID: "+server_id[:6])
                # Expiry
                match = re.search(r"expires in\s+([0-9D\sHM]+)", txt)
                log["expiry"] = match.group(1).strip() if match else "Error"
            except: pass
            
            if "D" in log["expiry"]: log["hint"] = "剩余 > 24小时"
            else: log["hint"] = "⚠️ 剩余 < 24小时"

            # ===============================================
            # 🔍 视觉暴力搜索 (不依赖文字，依赖颜色和特征)
            # ===============================================
            print("👉 开始视觉扫描按钮...")
            
            # 获取所有可能是按钮的元素 (button, a, div)
            candidates = sb.find_elements("button") + sb.find_elements("a.btn") + sb.find_elements("div[role='button']")
            
            start_btn = None
            stop_btn = None
            claim_btns = []

            for el in candidates:
                try:
                    # 获取元素的 HTML 和 文本
                    html = el.get_attribute("outerHTML").lower()
                    text = el.text.upper()
                    
                    # 1. 识别 STOP (红色按钮)
                    if "stop" in text or "bg-red" in html:
                        stop_btn = el
                    
                    # 2. 识别 START (绿色按钮)
                    # Pella 的绿色按钮通常有 bg-green-500 或 bg-emerald-500
                    if "start" in text or "bg-green" in html or "bg-emerald" in html:
                        # 排除掉 "Restart" 按钮
                        if "RESTART" not in text:
                            start_btn = el
                    
                    # 3. 识别 Claim (紫色/灰色)
                    if "claim" in html or "claim" in text.lower():
                        claim_btns.append(el)
                        
                except: pass

            # --- 逻辑判断 ---
            
            # 场景 A: 已经在运行
            if stop_btn:
                print("✅ 发现红色按钮 -> 状态: 运行中")
                log["status"] = "运行中"
            
            # 场景 B: 已停止，需要启动
            elif start_btn:
                print("⚠️ 发现绿色按钮 -> 状态: 已停止")
                log["status"] = "已停止"
                
                print("👉 执行 JS 强力点击启动...")
                sb.execute_script("arguments[0].click();", start_btn)
                sb.sleep(5)
                
                # 检查是否成功
                logs = sb.get_text("body")[-1000:]
                if "Starting" in logs or "Booting" in logs:
                    log["status"] = "启动指令已发"
                    log["logs"].append("已触发启动")
                else:
                    # 刷新再看一眼
                    sb.refresh()
                    sb.sleep(5)
                    if sb.is_element_visible("button:contains('STOP')") or sb.is_element_visible(".bg-red-500"):
                        log["status"] = "启动成功"
                    else:
                        log["logs"].append("点击后状态未变")

            else:
                log["status"] = "未找到控制按钮"
                log["logs"].append("按钮定位失败")

            # --- 续期处理 ---
            print(f"👉 发现 {len(claim_btns)} 个续期相关元素")
            clicked_cnt = 0
            claimed_cnt = 0
            
            for btn in claim_btns:
                try:
                    t = btn.text.upper()
                    if "CLAIMED" in t:
                        claimed_cnt += 1
                    elif "HOURS" in t or "CLAIM" in t:
                        print(f"👉 点击续期: {t}")
                        sb.execute_script("arguments[0].click();", btn)
                        clicked_cnt += 1
                        sb.sleep(2)
                except: pass
            
            if clicked_cnt > 0: log["renew_status"] = f"成功续期 {clicked_cnt} 次"
            elif claimed_cnt > 0: log["renew_status"] = "无需续期"
            else: log["renew_status"] = "无可用按钮"

        except Exception as e:
            print(f"❌ 错误: {e}")
            log["logs"].append(f"Err: {str(e)[:30]}")
            ts = int(time.time())
            sb.save_screenshot(f"screenshots/err_{ts}.png")
        finally:
            send_report(log, tg_token, tg_chat_id)

def send_report(log, token, chat_id):
    header = "ℹ️"
    if "启动" in "".join(log["logs"]): header = "⚠️"
    if "成功续期" in log["renew_status"]: header = "🎉"
    
    act = "无需续期"
    if "启动" in "".join(log["logs"]): act = "执行了启动操作"
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
