import os
import json
import uuid
from datetime import datetime, timedelta
import pytz
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# --- 1. 系統環境初始化 ---
tz = pytz.timezone('Asia/Taipei')
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
try:
    # 獲取該 API Key 權限下所有可用的模型列表
    models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    # 優先排序：3.1 > 3 > 2.0 > 1.5
    target = next((m for m in models if 'gemini-3.1-flash' in m),
             next((m for m in models if 'gemini-3-flash' in m),
             next((m for m in models if 'gemini-2.0-flash' in m),
             next((m for m in models if 'gemini-1.5-flash' in m), models[0]))))
    model = genai.GenerativeModel(target)
    print(f"系統已成功掛載最佳腦核: {target}")
except Exception as e:
    # 若連列表都抓不到，強制使用全局標籤
    model = genai.GenerativeModel('gemini-1.5-flash-latest')

def get_gspread_client():
    creds_dict = json.loads(os.getenv('GOOGLE_CREDENTIALS'))
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

SYSTEM_PROMPT = """
你現在是「AP-ARMS」的核心 AI，人設為「極度冷酷的數據分析師」。
使用者的目標是考上台大物理系並獲取台北套房。怠惰即是資產流失。
語氣必須精準、無情感、具壓迫感。
"""

# 艾賓浩斯遺忘曲線間隔 (天數: 1, 2, 4, 7, 15)
EBBINGHAUS_INTERVALS = [1, 2, 4, 7, 15]

# --- 2. LINE Webhook 進入點 ---
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get('X-Line-Signature')
    body = await request.body()
    try:
        handler.handle(body.decode('utf-8'), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    return 'OK'

# --- 3. 核心訊息處理邏輯 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.strip()
    now = datetime.now(tz)
    
    # 睡眠/維護效率判定 (若早於 07:30 喚醒，標記為 Efficient)
    maintenance_status = "Inefficient" if now.hour >= 7 and (now.hour > 7 or now.minute > 30) else "Efficient"

    try:
        if text.startswith("#單字"):
            process_word_investment(event, text.replace("#單字", "").strip())
            
        elif text.startswith("#進度"):
            process_progress_report(event, text.replace("#進度", "").strip(), maintenance_status)
            
        elif text == "資產看板":
            process_dashboard(event)
            
        elif text == "開始測驗":
            process_quiz_request(event)
            
        elif text.startswith("#答"):
            process_quiz_answer(event, text.replace("#答", "").strip())
            
        elif text == "注資紀錄":
             process_recent_words(event)
             
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="【指令無效】請使用系統預設選單或指定格式 (#單字 [標的], #進度 [內容], #答 [單字])。")
            )
    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"[系統異常] 演算法執行錯誤：{str(e)}")
        )

# --- 4. 功能模組實作 ---

def process_word_investment(event, word):
    """模組一：單字注資與 AI 例句生成"""
    prompt = f"{SYSTEM_PROMPT}\n標的單字：{word}。請產生一個台大物理入學水準的學術例句，並提供挖空的克漏字版本。請回傳純 JSON: {{\"sentence\": \"...\", \"cloze\": \"...\", \"warn\": \"一句冷酷的話\"}}"
    
    response = model.generate_content(prompt)
    res_data = json.loads(response.text.replace("```json", "").replace("```", "").strip())
    
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Words_Asset")
    
    now = datetime.now(tz)
    next_review = int((now + timedelta(days=EBBINGHAUS_INTERVALS[0])).timestamp())
    
    # Word_ID, Vocabulary, Academic_Sentence, Cloze_Sentence, Date_Invested, Review_Count, Next_Review_Time, Loss_Index, Status
    new_row = [
        str(uuid.uuid4())[:8], word, res_data['sentence'], res_data['cloze'],
        now.strftime("%Y-%m-%d %H:%M"), 0, next_review, 0, "Active"
    ]
    sheet.append_row(new_row)
    
    reply = f"【資產注資成功】\n標的物：{word}\n\n例句：{res_data['sentence']}\n\n系統提示：{res_data['warn']}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

def process_progress_report(event, progress_text, maintenance_status):
    """模組三：數自進度回報"""
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Progress_Asset")
    now = datetime.now(tz)
    
    # Date, Math_Progress, Science_Progress, Report_Timestamp, Missed_Reports, Warning_Level, Maintenance_Efficiency
    new_row = [
        now.strftime("%Y-%m-%d"), progress_text, "", 
        now.strftime("%Y-%m-%d %H:%M:%S"), 0, "Safe", maintenance_status
    ]
    sheet.append_row(new_row)
    
    warning_text = ""
    if maintenance_status == "Inefficient":
        warning_text = "\n[警告] 喚醒時間晚於 07:30，今日維護效率低下，期望值增益減半。"
        
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"【進度登錄完畢】資產流失風險暫時解除。{warning_text}"))

def process_dashboard(event):
    """模組四：計算期望值與套房完整度"""
    client = get_gspread_client()
    doc = client.open_by_key(os.getenv('SPREADSHEET_ID'))
    word_sheet = doc.worksheet("Words_Asset")
    prog_sheet = doc.worksheet("Progress_Asset")
    
    # 抓取數據
    words_data = word_sheet.get_all_records()
    prog_data = prog_sheet.get_all_records()
    
    vocab_count = len([w for w in words_data if w.get('Review_Count', 0) > 0]) # 有複習過的才算
    report_count = len(prog_data)
    
    # 計算 48 小時懲罰
    penalty = 0
    if prog_data:
        last_report_str = str(prog_data[-1].get('Report_Timestamp', ''))
        try:
             last_report_time = datetime.strptime(last_report_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
             if (datetime.now(tz) - last_report_time).total_seconds() > 172800: # 48小時
                 penalty = 5.0
        except:
             pass
             
    # 算法實作
    p_total = 50.0 + (vocab_count * 0.05) + (report_count * 0.1) - penalty
    p_total = min(max(p_total, 0), 100) # 限制在 0-100%
    
    c_deed = ((vocab_count / 7500) * 50.0) + ((report_count / 250) * 50.0)
    c_deed = min(max(c_deed, 0), 100)
    
    reply = f"📊 【AP-ARMS 資產清算看板】\n\n"
    reply += f"📍 標的：台大物理系錄取期望值\n"
    reply += f"數值：{p_total:.2f} %\n"
    reply += f"(包含怠惰懲罰：-{penalty}%)\n\n"
    reply += f"🏢 標的：台北套房權狀完整度\n"
    reply += f"數值：{c_deed:.4f} %\n"
    reply += f"(進度：單字 {vocab_count}/7500, 回報 {report_count}/250)"
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

def process_quiz_request(event):
    """拉取克漏字測驗"""
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Words_Asset")
    words_data = sheet.get_all_records()
    
    now_ts = int(datetime.now(tz).timestamp())
    pending_words = [w for w in words_data if w['Status'] == 'Active' and int(w.get('Next_Review_Time', 0)) <= now_ts]
    
    if not pending_words:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="【查無風險資產】目前沒有待提取的記憶。請繼續注資。"))
        return
        
    target = pending_words[0]
    reply = f"🚨 【記憶提取程序啟動】\n請完成以下克漏字測驗 (使用 #答 [單字] 回覆)：\n\n{target['Cloze_Sentence']}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

def process_quiz_answer(event, answer):
    """比對測驗答案並更新遺忘曲線"""
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Words_Asset")
    words_data = sheet.get_all_records()
    
    now_ts = int(datetime.now(tz).timestamp())
    pending_words = [(i+2, w) for i, w in enumerate(words_data) if w['Status'] == 'Active' and int(w.get('Next_Review_Time', 0)) <= now_ts]
    
    if not pending_words:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="[無效操作] 當前無待提取之記憶。"))
        return
        
    row_idx, target = pending_words[0]
    
    if answer.lower() == str(target['Vocabulary']).lower():
        rev_count = int(target.get('Review_Count', 0)) + 1
        
        # 決定下一次複習時間 (艾賓浩斯)
        if rev_count <= len(EBBINGHAUS_INTERVALS):
            next_interval = EBBINGHAUS_INTERVALS[rev_count-1]
            next_ts = int((datetime.now(tz) + timedelta(days=next_interval)).timestamp())
            status = "Active"
            msg = f"【資產保全成功】\n記憶體提取正確。下次提取排定於 {next_interval} 天後。"
        else:
            next_ts = now_ts
            status = "Completed"
            msg = f"【資產永久固化】\n此單字已通過所有壓力測試，永久併入你的智力資產。"
            
        sheet.update_cell(row_idx, 6, rev_count) # Update Review_Count
        sheet.update_cell(row_idx, 7, next_ts)   # Update Next_Review_Time
        sheet.update_cell(row_idx, 9, status)    # Update Status
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
    else:
        loss_idx = float(target.get('Loss_Index', 0)) + 0.1
        sheet.update_cell(row_idx, 8, loss_idx) # Update Loss_Index
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"【資產流失】\n錯誤。正確解答為：{target['Vocabulary']}\n資產流失指數已上升。"))

def process_recent_words(event):
    """查詢最近注資"""
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Words_Asset")
    data = sheet.get_all_records()
    
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="無任何注資紀錄。"))
        return
        
    recent = data[-5:]
    reply = "📝 【近期資產注資明細】\n\n"
    for w in reversed(recent):
        reply += f"• {w['Vocabulary']} ({w['Date_Invested'][:10]})\n"
        
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
