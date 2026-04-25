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

# --- 腦核自動對接程序 ---
# --- 腦核降級：強制使用極速版避開 10 秒斷電限制 ---
model = genai.GenerativeModel('gemini-1.5-flash-8b')
def get_gspread_client():
    creds_dict = json.loads(os.getenv('GOOGLE_CREDENTIALS'))
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

SYSTEM_PROMPT = """
你現在是「AP-ARMS」的核心 AI，人設為「極度冷酷的數據分析師」。
使用者目標：考上台大物理系。
任務：針對使用者提供的單字，生成符合台大物理系水準的例句。
輸出規範：嚴格遵守 JSON 格式，包含 sentence (例句), cloze (克漏字), warn (冷酷警告)。
"""

EBBINGHAUS_INTERVALS = [1, 2, 4, 7, 15]

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get('X-Line-Signature')
    body = await request.body()
    try:
        handler.handle(body.decode('utf-8'), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.strip()
    now = datetime.now(tz)
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
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"[系統異常] 演算法執行錯誤：{str(e)}"))

# --- 功能模組實作 ---

def process_word_investment(event, word):
    prompt = f"{SYSTEM_PROMPT}\n標的單字：{word}。請以此格式回傳：{{\"sentence\": \"...\", \"cloze\": \"...\", \"warn\": \"...\"}}"
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        res_data = json.loads(response.text)
    except Exception as e:
        # 修正：直接印出底層 API 傳來的真實錯誤，不再呼叫不存在的 response
        raise ValueError(f"AI 核心呼叫失敗，真實錯誤碼：{str(e)}")
    
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Words_Asset")
    now = datetime.now(tz)
    next_review = int((now + timedelta(days=EBBINGHAUS_INTERVALS[0])).timestamp())
    
    new_row = [
        str(uuid.uuid4())[:8], word, res_data['sentence'], res_data['cloze'],
        now.strftime("%Y-%m-%d %H:%M"), 0, next_review, 0, "Active"
    ]
    sheet.append_row(new_row)
    
    reply = f"【資產注資成功】\n標的物：{word}\n\n例句：{res_data['sentence']}\n\n系統提示：{res_data.get('warn', '無警告')}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
   

def process_progress_report(event, progress_text, maintenance_status):
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Progress_Asset")
    now = datetime.now(tz)
    new_row = [now.strftime("%Y-%m-%d"), progress_text, "", now.strftime("%Y-%m-%d %H:%M:%S"), 0, "Safe", maintenance_status]
    sheet.append_row(new_row)
    warning_text = "\n[警告] 喚醒時間晚於 07:30，今日維護效率低下，期望值增益減半。" if maintenance_status == "Inefficient" else ""
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"【進度登錄完畢】資產流失風險暫時解除。{warning_text}"))

def process_dashboard(event):
    client = get_gspread_client()
    doc = client.open_by_key(os.getenv('SPREADSHEET_ID'))
    word_sheet, prog_sheet = doc.worksheet("Words_Asset"), doc.worksheet("Progress_Asset")
    words_data, prog_data = word_sheet.get_all_records(), prog_sheet.get_all_records()
    vocab_count = len([w for w in words_data if w.get('Review_Count', 0) > 0])
    report_count = len(prog_data)
    
    penalty = 0
    if prog_data:
        try:
             last_report_time = datetime.strptime(str(prog_data[-1].get('Report_Timestamp', '')), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
             if (datetime.now(tz) - last_report_time).total_seconds() > 172800: penalty = 5.0
        except: pass
             
    p_total = min(max(50.0 + (vocab_count * 0.05) + (report_count * 0.1) - penalty, 0), 100)
    c_deed = min(max(((vocab_count / 7500) * 50.0) + ((report_count / 250) * 50.0), 0), 100)
    
    reply = f"📊 【AP-ARMS 資產清算看板】\n\n📍 台大物理錄取期望值：{p_total:.2f} %\n🏢 台北套房權狀完整度：{c_deed:.4f} %\n(單字 {vocab_count}/7500, 回報 {report_count}/250)"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

def process_quiz_request(event):
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Words_Asset")
    now_ts = int(datetime.now(tz).timestamp())
    pending = [w for w in sheet.get_all_records() if w['Status'] == 'Active' and int(w.get('Next_Review_Time', 0)) <= now_ts]
    if not pending:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="【查無風險資產】目前沒有待提取的記憶。"))
        return
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚨 【記憶提取程序啟動】\n{pending[0]['Cloze_Sentence']}"))

def process_quiz_answer(event, answer):
    client = get_gspread_client()
    sheet = client.open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Words_Asset")
    words_data = sheet.get_all_records()
    now_ts = int(datetime.now(tz).timestamp())
    pending = [(i+2, w) for i, w in enumerate(words_data) if w['Status'] == 'Active' and int(w.get('Next_Review_Time', 0)) <= now_ts]
    if not pending: return
    
    row_idx, target = pending[0]
    if answer.lower() == str(target['Vocabulary']).lower():
        rev_count = int(target.get('Review_Count', 0)) + 1
        status = "Active" if rev_count <= len(EBBINGHAUS_INTERVALS) else "Completed"
        interval_idx = min(rev_count - 1, len(EBBINGHAUS_INTERVALS) - 1)
        next_ts = int((datetime.now(tz) + timedelta(days=EBBINGHAUS_INTERVALS[interval_idx])).timestamp()) if status == "Active" else now_ts
        
        sheet.update_cell(row_idx, 6, rev_count)
        sheet.update_cell(row_idx, 7, next_ts)
        sheet.update_cell(row_idx, 9, status)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="【資產保全成功】記憶體提取正確。"))
    else:
        sheet.update_cell(row_idx, 8, float(target.get('Loss_Index', 0)) + 0.1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"【資產流失】解：{target['Vocabulary']}"))

def process_recent_words(event):
    sheet = get_gspread_client().open_by_key(os.getenv('SPREADSHEET_ID')).worksheet("Words_Asset")
    data = sheet.get_all_records()
    reply = "📝 【近期資產注資明細】\n\n" + "\n". join([f"• {w['Vocabulary']}" for w in reversed(data[-5:])]) if data else "無紀錄。"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
