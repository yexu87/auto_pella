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

    # 初始化日志对象
    log = {
        "account": mask_email(email),
        "ip": "Unknown",
        "status": "Unknown",      # 运行状态：运行中 / 已停止
        "expiry": "Unknown",      # 到期时间
        "renew_status": "Unknown",# 续期状态：无需续期 / 已执行续期
        "hint": "",               # 提示信息
        "logs": []                # 操作日志
    }

    print(f"🚀 开始处理: {log['account']}")

    with SB(uc=True, test=True, locale="en") as sb:
        try:
            # ----------------- 1. 登录流程 -----------------
            print("👉 进入登录页...")
            sb.uc_open_with_reconnect(LOGIN_URL, 6)
            
            # 尝试过盾
            try: sb.uc_gui_click_captcha(); sb.sleep(2)
            except: pass

            # === 输入邮箱 ===
            print("👉 输入邮箱...")
            # Clerk 专用选择器
            sb.wait_for_element('input[name="identifier"]', timeout=20)
            sb.type('input[name="identifier"]', email + "\n") # 使用回车提交
            sb.sleep(5) # 等待跳转

            # === 输入密码 ===
            print("👉 输入密码...")
            # 检查是否成功跳转到密码页
            if not sb.is_element_visible('input[name="password"]'):
                # 如果没跳转，尝试补点一下 Continue
                if sb.is_element_visible('button:contains("Continue")'):
                    sb.uc_click('button:contains("Continue")')
                    sb.sleep(3)
            
            sb.wait_for_element('input[name="password"]', timeout=15)
            sb.type('input[name="password"]', password + "\n") # 使用回车提交
            sb.sleep(5)
            
            # 确保登录成功 (等待 Dashboard 元素)
            print("👉 等待 Dashboard...")
            sb.wait_for_element('a[href*="/server/"]', timeout=30)
            print("✅ 登录成功")

            # ----------------- 2. 进入服务器 -----------------
            target_url = SERVER_URL_TEMPLATE.format(server_id=server_id)
            print(f"👉 跳转服务器: {target_url}")
            sb.open(target_url)
            sb.sleep(8) # 等待动态资源加载

            # ----------------- 3. 提取信息与操作 -----------------
            
            # [A] 获取 IP (尝试从控制台文本或页面提取)
            try:
                page_text = sb.get_text("body")
                # 匹配 IP 格式，排除版本号
                ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', page_text)
                # 过滤掉常见的非公网 IP
                valid_ips = [ip for ip in ips if not ip.startswith("127.") and not ip.startswith("255.") and "0.0.0.0" not in ip]
                if valid_ips:
                    log["ip"] = valid_ips[0]
                elif "0.0.0.0" in page_text:
                    log["ip"] = "0.0.0.0"
                else:
                    log["ip"] = f"ID: {server_id[:6]}..."
            except: pass

            # [B] 判断服务器状态 (START / STOP)
            # 逻辑：有 STOP 按钮 -> 运行中；有 START 按钮 -> 已停止
            if sb.is_element_visible('button:contains("STOP")'):
                print("✅ 检测到 STOP 按钮 -> 服务器运行中")
                log["status"] = "运行中"
            
            elif sb.is_element_visible('button:contains("START")'):
                print("⚠️ 检测到 START 按钮 -> 服务器已停止")
                log["status"] = "已停止"
                # 执行启动
                print("👉 点击启动...")
                sb.uc_click('button:contains("START")')
                sb.sleep(5)
                log["logs"].append("已执行启动指令")
                log["status"] = "启动中"
            
            else:
                log["status"] = "状态未知 (未找到按钮)"

            # [C] 获取到期时间
            try:
                # 查找类似 "Your server expires in 1D 13H 25M"
                # 使用 XPath 定位包含 expires in 的文本节点
                expiry_element = sb.find_element("//*[contains(text(), 'expires in')]")
                raw_text = expiry_element.text
                match = re.search(r"expires in\s+([0-9D\sHM]+)", raw_text, re.IGNORECASE)
                if match:
                    log["expiry"] = match.group(1).strip()
                else:
                    log["expiry"] = "时间解析失败"
            except:
                log["expiry"] = "未找到时间元素"

            # 设置提示
            if "D" in log["expiry"]:
                log["hint"] = "剩余 > 24小时"
            else:
                log["hint"] = "⚠️ 剩余 < 24小时，请注意"

            # [D] 续期检测 (Claim / Claimed)
            print("👉 检查续期按钮...")
            # 查找所有按钮
            buttons = sb.find_elements("button")
            claim_btns = [b for b in buttons if "Claim" in b.text]
            
            claimed_count = 0
            to_claim_count = 0
            
            if not claim_btns:
                log["renew_status"] = "未找到按钮"
            else:
                for btn in claim_btns:
                    btn_text = btn.text
                    if "Claimed" in btn_text:
                        claimed_count += 1
                    elif "Claim" in btn_text:
                        # 需要续期 (例如 "16 HOURS Claim")
                        print(f"👉 点击续期: {btn_text}")
                        try:
                            btn.click()
                            to_claim_count += 1
                            sb.sleep(2)
                        except:
                            log["logs"].append("点击续期失败")

                if to_claim_count > 0:
                    log["renew_status"] = f"成功续期 {to_claim_count} 次"
                elif claimed_count > 0:
                    log["renew_status"] = "无需续期 (已Claimed)"
                else:
                    log["renew_status"] = "未知状态"

        except Exception as e:
            print(f"❌ 发生错误: {e}")
            log["status"] = "脚本出错"
            log["logs"].append(f"Err: {str(e)[:50]}")
            # 截图保存
            ts = int(time.time())
            sb.save_screenshot(f"screenshots/err_{ts}.png")
        
        finally:
            send_report(log, tg_token, tg_chat_id)

def send_report(log, token, chat_id):
    # 根据用户要求的格式构建消息
    # 🎮 Pella 续期通知
    # 🆔 账号: ...
    # 🖥 IP: ...
    # ⏰ 时间: ...
    #
    # ℹ️ 无需续期 (或者其他状态)
    # 📅 状态: 运行中
    # ⏳ 剩余: ...
    # 💡 提示: ...
    
    header_emoji = "ℹ️"
    if "启动" in "".join(log["logs"]): header_emoji = "⚠️"
    if "成功续期" in log["renew_status"]: header_emoji = "🎉"
    if "出错" in log["status"]: header_emoji = "❌"

    msg = f"""
<b>🎮 Pella 续期通知</b>
🆔 账号: <code>{log['account']}</code>
🖥 IP: <code>{log['ip']}</code>
⏰ 时间: {get_beijing_time()}

{header_emoji} <b>{log['renew_status']}</b>
📊 状态: <b>{log['status']}</b>
⏳ 剩余: {log['expiry']}
💡 提示: {log['hint']}
"""
    # 如果有额外日志（如启动了服务器），附在最后
    if log["logs"]:
        msg += f"\n📝 操作: {' | '.join(log['logs'])}"

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
