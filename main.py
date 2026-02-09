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
        "renew_status": "Unknown",
        "hint": "",
        "logs": []
    }

    print(f"🚀 开始处理: {log['account']}")

    with SB(uc=True, test=True, locale="en") as sb:
        try:
            # ----------------- 1. 登录流程 -----------------
            print("👉 进入登录页...")
            sb.uc_open_with_reconnect(LOGIN_URL, 6)
            
            # 过盾尝试
            try: sb.uc_gui_click_captcha(); sb.sleep(2)
            except: pass

            # === 输入邮箱 ===
            print("👉 输入邮箱...")
            # 兼容多种邮箱框定位
            email_input = None
            for sel in ['input[name="identifier"]', 'input[type="email"]', 'input[name="email"]']:
                if sb.is_element_visible(sel):
                    email_input = sel
                    break
            
            if not email_input: raise Exception("找不到邮箱输入框")
            
            sb.type(email_input, email + "\n") # 回车提交
            sb.sleep(5) 

            # === 输入密码 ===
            print("👉 输入密码...")
            # 尝试定位密码框，如果还在邮箱页则补点Continue
            if not sb.is_element_visible('input[name="password"]'):
                if sb.is_element_visible('button:contains("Continue")'):
                    sb.uc_click('button:contains("Continue")')
                    sb.sleep(3)
            
            sb.wait_for_element('input[name="password"]', timeout=15)
            sb.type('input[name="password"]', password + "\n") # 回车提交
            sb.sleep(5)
            
            # 确保登录成功
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
                # 过滤无效IP
                valid_ips = [ip for ip in ips if not ip.startswith("127.") and not ip.startswith("255.") and "0.0.0.0" not in ip]
                if valid_ips: log["ip"] = valid_ips[0]
                elif "0.0.0.0" in page_text: log["ip"] = "0.0.0.0"
                else: log["ip"] = f"ID: {server_id[:6]}..."
            except: pass

            # [B] 判断服务器状态 & 启动逻辑 (核心修改)
            # 定义更精准的 XPath 选择器
            # 查找包含文本 "STOP" 的按钮 (不区分大小写，移除空格干扰)
            stop_btn_xpath = "//button[contains(., 'STOP')]"
            start_btn_xpath = "//button[contains(., 'START')]"

            if sb.is_element_visible(stop_btn_xpath):
                print("✅ 检测到 STOP 按钮 -> 状态: 运行中")
                log["status"] = "运行中"
            
            elif sb.is_element_visible(start_btn_xpath):
                print("⚠️ 检测到 START 按钮 -> 状态: 已停止")
                log["status"] = "已停止"
                
                # --- 执行启动 ---
                print("👉 尝试点击启动...")
                try:
                    # 使用 XPath 点击
                    sb.click(start_btn_xpath)
                    log["logs"].append("已点击启动")
                    
                    # 等待一下看状态是否改变
                    sb.sleep(5)
                    if sb.is_element_visible(stop_btn_xpath):
                         log["status"] = "启动成功 (运行中)"
                    else:
                         log["status"] = "启动指令已发送"
                except Exception as e:
                    print(f"❌ 点击启动失败: {e}")
                    log["logs"].append("点击启动失败")
            
            else:
                # 兜底：如果找不到文字按钮，尝试找颜色类名 (根据截图推测)
                print("⚠️ 未找到文字按钮，尝试备用方案...")
                if sb.is_element_visible("button.bg-red-500") or sb.is_element_visible("button.bg-red-600"):
                     log["status"] = "运行中 (按颜色判断)"
                elif sb.is_element_visible("button.bg-green-500") or sb.is_element_visible("button.bg-green-600"):
                     log["status"] = "已停止"
                     sb.click("button.bg-green-500") # 盲点绿色按钮
                     log["logs"].append("已点击启动(颜色识别)")
                else:
                    log["status"] = "状态未知"

            # [C] 获取到期时间
            try:
                # 寻找包含 "expires in" 的文本
                expiry_el = sb.find_element("//*[contains(text(), 'expires in')]")
                match = re.search(r"expires in\s+([0-9D\sHM]+)", expiry_el.text)
                if match: log["expiry"] = match.group(1).strip()
                else: log["expiry"] = "解析失败"
            except:
                log["expiry"] = "未找到时间"

            # 设置提示
            if "D" in log["expiry"]: log["hint"] = "剩余 > 24小时"
            else: log["hint"] = "⚠️ 剩余 < 24小时"

            # [D] 续期检测
            print("👉 检查续期按钮...")
            # 查找所有包含 "Claim" 的链接或按钮
            claim_items = sb.find_elements("//*[contains(text(), 'Claim')]")
            
            claimed_cnt = 0
            click_cnt = 0
            
            # 截图中的 Claim 按钮可能是 button 也可能是 a 标签
            # 遍历所有包含 Claim 文本的元素
            processed_elements = [] # 防止重复点击
            
            for item in claim_items:
                try:
                    txt = item.text
                    if item in processed_elements: continue
                    
                    # 排除掉说明文字，只点按钮 (通常文字较短)
                    if len(txt) > 20 and "HOURS" not in txt: continue

                    if "Claimed" in txt:
                        claimed_cnt += 1
                        processed_elements.append(item)
                    elif "HOURS" in txt: # 例如 "16 HOURS Claim"
                        print(f"👉 点击续期: {txt}")
                        item.click()
                        click_cnt += 1
                        sb.sleep(2)
                        processed_elements.append(item)
                except: pass

            if click_cnt > 0: log["renew_status"] = f"成功续期 {click_cnt} 次"
            elif claimed_cnt > 0: log["renew_status"] = "无需续期 (已Claimed)"
            else: log["renew_status"] = "未找到可用按钮"

        except Exception as e:
            print(f"❌ 发生错误: {e}")
            log["status"] = "脚本出错"
            log["logs"].append(f"Err: {str(e)[:30]}")
            # 出错截图
            ts = int(time.time())
            sb.save_screenshot(f"screenshots/err_{ts}.png")
        
        finally:
            send_report(log, tg_token, tg_chat_id)

def send_report(log, token, chat_id):
    # 构建 Telegram 消息
    header_emoji = "ℹ️"
    if "启动" in "".join(log["logs"]) or "启动" in log["status"]: header_emoji = "⚠️"
    if "成功续期" in log["renew_status"]: header_emoji = "🎉"
    if "出错" in log["status"]: header_emoji = "❌"

    # 如果有启动操作，修改标题
    action_title = log['renew_status']
    if "启动" in log["logs"] or "启动" in log["status"]:
         action_title = "执行了启动操作"

    msg = f"""
<b>🎮 Pella 续期通知</b>
🆔 账号: <code>{log['account']}</code>
🖥 IP: <code>{log['ip']}</code>
⏰ 时间: {get_beijing_time()}

{header_emoji} <b>{action_title}</b>
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
