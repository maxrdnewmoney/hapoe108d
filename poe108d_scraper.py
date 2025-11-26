#!/usr/bin/env python3
"""
Hasivo PoE Switch Scraper for Home Assistant (最終穩定版 - 整合所有 URL/操作/登入登出邏輯)

主要功能:
1. 獲取交換機數據 (/101)。
2. 執行 Port Reboot (callcmd: 103, URL: /103)。
3. 執行 全機 Reboot (callcmd: 104, URL: /104)。
4. 所有網路操作均強制執行 Login 和 Finally Logout。
5. 支援命令行傳入 IP 地址。
"""
import requests
import json
import sys
import time

# ================= 配置區域 (請確認) =================
# 如果命令行未提供 IP，將使用此預設 IP 執行數據獲取
TARGET_IP = "192.168.60.15" 
PASSWORD = "Nx661021Nx"

# BASE_URL 將在 main() 函式中根據 TARGET_IP 設定
BASE_URL = "" 

LOGIN_URL = "/123"
DATA_URL = "/101"
LOGOUT_URL = "/126"
ACTION_URL = "/103"  # Port Reboot URL
REBOOT_URL = "/104"  # 全機 Reboot URL
LOGOUT_CALLCMD = 126
TIMEOUT = 15
# ===========================================

# 用來 call port reboot 的數值陣列 (i=0 Port 10 -> 147; i=9 Port 1 -> 3)
PORT_REBOOT_IDS = [3, 19, 35, 51, 67, 83, 99, 115, 131, 147] 


# 初始化 headers
headers = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (HA Scraper)",
    "Connection": "close"
}

def execute_post(session, url, payload):
    """執行 POST 請求並處理常見錯誤。使用全局 BASE_URL。"""
    global BASE_URL
    if not BASE_URL:
        # 如果 BASE_URL 在這裡還沒被設定，說明 main 函式邏輯有問題
        raise Exception("IP 位址尚未設定 (BASE_URL 為空)")
        
    try:
        # 使用傳入的 url 參數
        res = session.post(f"{BASE_URL}{url}", json=payload, headers=headers, timeout=TIMEOUT)
        res.raise_for_status()
        return res
    except requests.exceptions.ConnectionError as e:
        raise Exception(f"連線被重置/關閉，請確認 IP({BASE_URL}) 或有多人登入衝突: {e}")
    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP 錯誤: {e.response.status_code} - 請檢查密碼是否正確或 Session 是否過期")
    except Exception as e:
        raise Exception(f"發生錯誤: {e}")

def get_session_and_login():
    """建立 Session 並登入，成功則回傳 Session 物件。"""
    session = requests.Session()
    
    login_payload = {
        "data": {
            "callcmd": 123,
            "calldata": {"password": PASSWORD}
        }
    }
    
    # 步驟 1: 登入 (使用 LOGIN_URL /123)
    execute_post(session, LOGIN_URL, login_payload)
    time.sleep(1)
    return session

def logout_and_close(session):
    """執行登出並關閉 Session。"""
    if not session:
        return
        
    try:
        # 步驟 3: 登出 (使用 LOGOUT_URL /126)
        logout_payload = {"data": {"callcmd": LOGOUT_CALLCMD}}
        session.post(f"{BASE_URL}{LOGOUT_URL}", json=logout_payload, headers=headers, timeout=5)
    except Exception:
        pass
    
    try:
        time.sleep(1)
        session.close()
    except Exception:
        pass


def post_action(payload, url):
    """
    執行操作 (Login -> Action -> Finally Logout)
    :param payload: POST 請求的 JSON 資料
    :param url: 目標 URL 字串 (例如: "/104" 或 "/103")
    """
    session = None
    try:
        # 步驟 1: 登入
        session = get_session_and_login()
        
        # 步驟 2: 執行操作
        execute_post(session, url, payload)

        output = {"status": "Success", "message": f"操作指令已發送至 {BASE_URL}{url}"}
        print(json.dumps(output, ensure_ascii=False))

    except Exception as e:
        error_msg = {"status": "Error", "error": str(e)}
        print(json.dumps(error_msg, ensure_ascii=False))
        sys.exit(1)
        
    finally:
        # 步驟 3: 登出
        logout_and_close(session)


def get_data():
    """獲取數據 (Login -> Data -> Finally Logout)"""
    session = None
    try:
        # 步驟 1: 登入
        session = get_session_and_login()
        
        # 步驟 2: 獲取數據 (使用 DATA_URL /101)
        data_payload = {
            "data": {
                "callcmd": 101
            }
        }
        data_res = execute_post(session, DATA_URL, data_payload)
        
        return data_res.json()

    except Exception as e:
        error_msg = {"status": "Error", "error": str(e)}
        print(json.dumps(error_msg, ensure_ascii=False))
        sys.exit(1)
        
    finally:
        # 步驟 3: 登出
        logout_and_close(session)


def parse_json(raw_json):
    """解析 Hasivo /101 JSON 格式 (Port 反向映射，輸出所有 10 個 Port)"""
    output = {
        "status": "on",
        "device_info": {},
        "ports": {}
    }

    try:
        data = raw_json.get("data", {}).get("calldata", {})
        
        if not data:
            raise ValueError("JSON 結構異常: 找不到 calldata")

        # 1. 全機資訊
        total_power_mw = float(data.get("tp", 0))
        output["device_info"] = {
            "model_sn": data.get("sn", "Unknown"),
            "voltage_v": float(data.get("vol", 0)),
            "total_power_w": total_power_mw / 1000.0,
            "mac": data.get("mac", ""),
            "ip": data.get("ip", ""),
            "version": data.get("V", "")
        }

        # 2. 端口解析
        link_arr = data.get("link", [])
        pw_arr = data.get("pw", [])
        tx_arr = data.get("tx", [])
        rx_arr = data.get("rx", [])
        admin_state_arr = data.get("AdminState", [])
        
        NUM_PORTS = len(link_arr)
        
        # 物理 Port 類型定義
        LOWEST_POE_PORT = 3
        
        # 迴圈從 i=0 (Port 10) 到 i=9 (Port 1)，處理所有 10 個 Port
        for i in range(NUM_PORTS):
            # Port 反向映射： i=0 -> Port 10; i=9 -> Port 1
            port_num = NUM_PORTS - i
            port_key = f"port{port_num}"
            
            # --- Port 類型標籤 (Port 3-10 PoE, Port 1/2 Uplink) ---
            is_physical_poe_port = (port_num >= LOWEST_POE_PORT)
            
            # --- 讀取 10 個元素的陣列 ---
            link_val = link_arr[i]
            tx_count = tx_arr[i]
            rx_count = rx_arr[i]
            admin_state = int(admin_state_arr[i]) if i < len(admin_state_arr) else 0
            
            # 取得 Port Reboot ID
            port_opcode = PORT_REBOOT_IDS[i] if i < len(PORT_REBOOT_IDS) else 0 

            # --- PoE 功率處理 ---
            poe_power = 0.0
            
            # 只有 Port 3 到 Port 10 應該顯示功率
            if is_physical_poe_port:
                j = i # Port 10 (i=0) -> j=0
                
                # 確保索引在 pw 陣列的範圍內 (0 到 7)
                if 0 <= j < len(pw_arr):
                    poe_power = float(pw_arr[j])
            
            # 實際連線狀態 (使用 link_val)
            is_connected = int(link_val) > 0
            state_str = "on" if is_connected else "off"

            # 寫入輸出字典
            output["ports"][port_key] = {
                "id": port_num,
                "type": "PoE" if is_physical_poe_port else "Uplink", # 使用物理標籤
                "state": state_str,
                "link_code": int(link_val),
                "admin_state": admin_state,
                "poe_power_w": poe_power, 
                "tx_count": int(tx_count),
                "rx_count": int(rx_count),
                "ip": data.get("ip", ""),
                "opcode": port_opcode 
            }

    except Exception as e:
        output["status"] = "off"
        output["error"] = str(e)

    return output


def main():
    """主函數，處理命令行參數，設定 IP 地址並執行操作。"""
    global TARGET_IP
    global BASE_URL
    
    args = sys.argv[1:] # 忽略腳本名稱
    
    # 檢查是否提供 IP 地址作為第一個參數
    if args:
        TARGET_IP = args[0]
        
    BASE_URL = f"http://{TARGET_IP}"
    
    # --- 參數解析和執行邏輯 ---
    
    # 檢查是否有操作指令 (例如 reboot 或 port)
    if len(args) > 1:
        action = args[1].lower() # 第二個參數: port 或 reboot
        
        # 1. 全機 Reboot: python script.py <ip> reboot
        if action == "reboot" and len(args) == 2:
            print(f"📣 執行全機 Reboot 指令 (目標 IP: {TARGET_IP})...")
            reboot_payload = {
                "data": {
                    "callcmd": 104
                }
            }
            post_action(reboot_payload, REBOOT_URL) 
            return

        # 2. Port Reboot: python script.py <ip> port <opcode>
        elif action == "port" and len(args) == 3:
            try:
                # 第三個參數是 reboot opcode
                reboot_opcode = int(args[2])
            except ValueError:
                print("❌ 錯誤: 第三個參數 (reboot opcode) 必須是整數。")
                print("用法 3 (Port 重啟): python hasivo_2.5g_scraper.py <ip> port <opcode>")
                sys.exit(1)

            print(f"📣 執行 Port Reboot 指令 (目標 IP: {TARGET_IP}, Opcode: {reboot_opcode})...")
            port_reboot_payload = {
                "data": {
                    "callcmd": 103,
                    "calldata": {
                        "opcode": reboot_opcode
                    }
                }
            }
            post_action(port_reboot_payload, ACTION_URL) 
            return
            
        else:
            print(f"⚠️ 未知的參數組合或參數數量錯誤。目前的 IP: {TARGET_IP}")
            print("用法 1 (獲取資料): python hasivo_2.5g_scraper.py <ip>")
            print("用法 2 (全機重啟): python hasivo_2.5g_scraper.py <ip> reboot")
            print("用法 3 (Port 重啟): python hasivo_2.5g_scraper.py <ip> port <opcode>")
            sys.exit(1)
            
    # 參數數量為 1: 獲取數據 (python script.py <ip>)
    elif len(args) == 1:
        #print(f"📊 執行數據獲取 (目標 IP: {TARGET_IP})...")
        json_data = get_data() # 使用 DATA_URL /101
        if json_data:
            parsed_data = parse_json(json_data)
            print(json.dumps(parsed_data, ensure_ascii=False))
            
    # 參數數量為 0: 使用預設 IP 獲取數據 (python script.py)
    elif len(args) == 0:
        print(f"📊 執行數據獲取 (使用預設 IP: {TARGET_IP})...")
        json_data = get_data() 
        if json_data:
            parsed_data = parse_json(json_data)
            print(json.dumps(parsed_data, ensure_ascii=False))
            
    else:
        # 雖然前面的邏輯已經涵蓋了所有情況，但作為保護
        print("❌ 參數數量錯誤。")
        sys.exit(1)

if __name__ == "__main__":
    main()