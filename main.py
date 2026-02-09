#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pella.app 自动保活与续期脚本 (仿 XServer 结构版)
"""

import asyncio
import os
import datetime
import re
import requests
from datetime import timezone, timedelta
from playwright.async_api import async_playwright
# 直接使用与 XServer 脚本相同的导入方式
from playwright_stealth import stealth_async

# =====================================================================
#                          配置区域
# =====================================================================

# 强制无头模式
USE_HEADLESS = True 
WAIT_TIMEOUT = 30000 

# 从单一变量中读取所有配置
# 格式: 邮箱,密码,服务器ID,BotToken,ChatID
PELLA_CREDENTIALS = os.getenv("PELLA_CREDENTIALS")

# =====================================================================
#                        Telegram 通知类
# =====================================================================

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)

    def send_pella_notify(self, email_addr, server_name, status, expiry_text, claim_status):
        if not self.enabled: return
        
        # 北京时间
        beijing_time = datetime.datetime.now(timezone(timedelta(hours=8)))
        timestamp = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 简单脱敏
        safe_email = email_addr[:2] + "***" + email_addr.split('@')[-1] if email_addr else "Unknown"

        # 构建消息 (仿照 XServer 格式)
        msg = f"<b>🟣 Pella.app 续期通知</b>\n"
        msg += f"🆔 账号: <code>{safe_email}</code>\n"
        msg += f"🖥 服务器: <code>{server_name}</code>\n"
        msg += f"⏰ 时间: {timestamp}\n\n"
        
        # 状态图标
        if "Running" in status or "运行中" in status:
            status_icon = "🟢"
            status_text = "运行中"
        else:
            status_icon = "🔴"
            status_text = status
            
        msg += f"{status_icon} 状态: <b>{status_text}</b>\n"
        msg += f"⏳ 剩余: <b>{expiry_text}</b>\n"
        msg += f"🎁 续期: {claim_status}\n"
        
        # 发送
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
            print("✅ Telegram 通知已发送")
        except Exception as e:
            print(f"❌ Telegram 发送失败: {e}")

# =====================================================================
#                        Pella 自动化类
# =====================================================================

class PellaBot:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        
        # 配置信息
        self.email = ""
        self.password = ""
        self.server_id = ""
        self.notifier = None
        
        # 结果数据
        self.server_name = "Unknown"
        self.server_status = "Unknown"
        self.expiry_text = "Unknown"
        self.claim_log = []

    def parse_config(self):
        """解析配置"""
        if not PELLA_CREDENTIALS:
            print("❌ 未找到环境变量 PELLA_CREDENTIALS")
            return False
            
        try:
            parts = [p.strip() for p in PELLA_CREDENTIALS.split(',')]
            if len(parts) < 3:
                print("❌ PELLA_CREDENTIALS 格式错误，需: 邮箱,密码,服务器ID")
                return False
                
            self.email = parts[0]
            self.password = parts[1]
            self.server_id = parts[2]
            
            # TG 配置可选
            if len(parts) >= 5:
                self.notifier = TelegramNotifier(parts[3], parts[4])
            else:
                self.notifier = TelegramNotifier("", "")
            return True
        except Exception as e:
            print(f"❌ 配置解析异常: {e}")
            return False

    async def start(self):
        """启动浏览器"""
        p = await async_playwright().start()
        args = ['--no-sandbox', '--disable-blink-features=AutomationControlled']
        self.browser = await p.chromium.launch(headless=USE_HEADLESS, args=args)
        
        # 这里的 viewport 设置即模仿 XServer 脚本
        self.context = await self.browser.new_context(viewport={'width': 1920, 'height': 1080})
        self.page = await self.context.new_page()
        await stealth_async(self.page)

    async def close(self):
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()

    async def run(self):
        if not self.parse_config(): return

        try:
            await self.start()
            print(f"🚀 开始任务: {self.email}")

            # 1. 登录流程
            await self.page.goto("https://www.pella.app/login", wait_until='networkidle')
            
            # 输入邮箱 -> Continue
            await self.page.locator("input[type='email']").fill(self.email)
            await self.page.click("button:has-text('Continue')")
            
            # 等待密码框出现 (跳转 factor-one)
            await self.page.wait_for_selector("input[type='password']", timeout=WAIT_TIMEOUT)
            
            # 输入密码 -> Continue
            await self.page.locator("input[type='password']").fill(self.password)
            await self.page.click("button:has-text('Continue')")
            
            # 等待进入 Dashboard
            await self.page.wait_for_url("**/dashboard", timeout=WAIT_TIMEOUT)
            print("✅ 登录成功")

            # 2. 进入服务器页面
            target_url = f"https://www.pella.app/server/{self.server_id}"
            print(f"🌐 访问服务器: {target_url}")
            await self.page.goto(target_url, wait_until='networkidle')
            await asyncio.sleep(5) # 等待页面元素渲染

            # 获取服务器名
            try:
                self.server_name = await self.page.locator("h1").first.text_content()
                self.server_name = self.server_name.strip()
            except: pass

            # 3. 检查状态 (Start/Stop)
            # 如果有 STOP 按钮，说明正在运行
            if await self.page.locator("button:has-text('STOP')").count() > 0:
                self.server_status = "Running"
                print("🟢 服务器运行中")
            # 如果有 START 按钮，说明停止了，点击启动
            elif await self.page.locator("button:has-text('START')").count() > 0:
                self.server_status = "Stopped (Starting...)"
                print("🔴 服务器已停止，正在启动...")
                await self.page.click("button:has-text('START')")
                await asyncio.sleep(3)
            else:
                self.server_status = "Unknown"

            # 4. 获取剩余时间 (Target: "Your server expires in 1D 15H 0M.")
            try:
                # 模糊匹配包含 expires in 的文本
                expiry_el = self.page.locator("text=/expires in/i")
                if await expiry_el.count() > 0:
                    full_text = await expiry_el.text_content()
                    # 正则提取时间部分 (匹配数字+字母的组合)
                    # 例如: 1D 15H 0M
                    match = re.search(r'expires in\s+(.*?)\.', full_text)
                    if match:
                        self.expiry_text = match.group(1).strip()
                    else:
                        # 备用方案：截取字符串
                        self.expiry_text = full_text.split("expires in")[-1].split(".")[0].strip()
                    print(f"⏳ 剩余时间: {self.expiry_text}")
            except Exception as e:
                print(f"⚠️ 获取时间失败: {e}")

            # 5. 续期 (Claim)
            # 查找所有按钮
            buttons = await self.page.locator("button").all()
            claimed_count = 0
            
            for btn in buttons:
                txt = await btn.text_content()
                txt = txt.strip() if txt else ""
                
                # 逻辑: 包含 "Claim" 且 不包含 "Claimed"
                if "Claim" in txt and "Claimed" not in txt:
                    print(f"🎁 发现可用续期按钮: {txt}")
                    try:
                        await btn.click()
                        self.claim_log.append(f"已领 ({txt})")
                        claimed_count += 1
                        await asyncio.sleep(2)
                    except:
                        self.claim_log.append("领取失败")
            
            if claimed_count == 0:
                self.claim_log.append("无可用/已领完")

        except Exception as e:
            print(f"❌ 运行异常: {e}")
            self.server_status = "Error"
        finally:
            # 发送通知
            if self.notifier:
                claim_str = ", ".join(list(set(self.claim_log)))
                self.notifier.send_pella_notify(
                    self.email, 
                    self.server_name, 
                    self.server_status, 
                    self.expiry_text, 
                    claim_str
                )
            await self.close()

if __name__ == "__main__":
    asyncio.run(PellaBot().run())
