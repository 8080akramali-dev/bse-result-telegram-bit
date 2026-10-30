import os, json, time, hashlib, logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
DATA_FILE = "watchlist.json"
STATE_FILE = "seen.json"
TZ = ZoneInfo("Asia/Kolkata")

BSE_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bse-alert")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

def announcement_key(row):
    raw = "|".join(str(row.get(k, "")) for k in
                   ("SCRIP_CD", "NEWSID", "NEWS_DT", "ATTACHMENTNAME", "NEWSSUB", "HEADLINE"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def row_text(row):
    return " ".join(str(v) for v in row.values() if v is not None).lower()

def is_financial_result(row):
    # BSE query is already narrowed to Result / Financial Results.
    # This extra check helps reject unrelated rows if BSE changes its response.
    t = row_text(row)
    terms = ("financial result", "unaudited", "audited", "quarter ended",
             "quarterly results", "standalone", "consolidated", "financials")
    return any(term in t for term in terms) or "result" in t

def fetch_results(scrip_code):
    params = {
        "pageno": 1,
        "strCat": "Result",
        "subcategory": "Financial+Results",
        "strPrevDate": datetime.now(TZ).strftime("%Y%m%d"),
        "strToDate": datetime.now(TZ).strftime("%Y%m%d"),
        "strSearch": "P",
        "strscrip": str(scrip_code),
        "strType": "C",
    }
    response = requests.get(BSE_URL, params=params, headers=BSE_HEADERS, timeout=15)
    response.raise_for_status()
    payload = response.json()
    return [r for r in payload.get("Table", []) if is_financial_result(r)]

def pdf_url(row):
    attachment = row.get("ATTACHMENTNAME") or row.get("ATTACHMENT") or ""
    if not attachment:
        return ""
    if str(attachment).startswith("http"):
        return str(attachment)
    return "https://www.bseindia.com/xml-data/corpfiling/AttachLive/" + str(attachment).lstrip("/")

def format_alert(company, row):
    subject = (row.get("NEWSSUB") or row.get("HEADLINE") or
               row.get("NEWS_SUB") or row.get("MORE") or "Financial results filing")
    published = row.get("NEWS_DT") or row.get("DT_TM") or row.get("DissemDT") or "Time not supplied by BSE"
    link = pdf_url(row)
    msg = (
        "🚨 <b>BSE QUARTERLY RESULT ALERT</b>\n\n"
        f"<b>Company:</b> {company['name']}\n"
        f"<b>BSE Code:</b> {company['code']}\n"
        f"<b>Announcement:</b> {str(subject)[:500]}\n"
        f"<b>Published:</b> {published}\n"
        "<b>Source:</b> BSE Corporate Announcements"
    )
    if link:
        msg += f'\n\n<a href="{link}">📄 Open result filing / PDF</a>'
    msg += "\n\n<i>Verify the filing and quarter from the original document.</i>"
    return msg

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "BSE Result Alert Bot is ready.\n\n"
        "Add: /add 543390 PB Fintech\n"
        "List: /list\nRemove: /remove 543390\n"
        "Help: /help\n\n"
        "Add companies using their verified BSE scrip codes."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/add BSE_CODE Company_Name — add a company\n"
        "/remove BSE_CODE — remove a company\n"
        "/list — show your watchlist\n\n"
        f"Polling interval: {POLL_SECONDS} seconds. "
        "The bot checks BSE's result announcements feed; delivery depends on BSE publication and API availability."
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("Format: /add 543390 PB Fintech")
        return
    code, name = context.args[0], " ".join(context.args[1:])
    watch = load_json(DATA_FILE, {})
    chat_id = str(update.effective_chat.id)
    watch.setdefault(chat_id, {})
    watch[chat_id][code] = name
    save_json(DATA_FILE, watch)
    await update.message.reply_text(f"✅ Added {name} (BSE: {code}) to your watchlist.")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Format: /remove 543390")
        return
    code = context.args[0]
    watch = load_json(DATA_FILE, {})
    chat_id = str(update.effective_chat.id)
    removed = watch.get(chat_id, {}).pop(code, None)
    save_json(DATA_FILE, watch)
    await update.message.reply_text(f"Removed {removed}." if removed else "That code was not in your watchlist.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    watch = load_json(DATA_FILE, {})
    items = watch.get(str(update.effective_chat.id), {})
    if not items:
        await update.message.reply_text("Your watchlist is empty. Add one with /add 543390 PB Fintech")
        return
    await update.message.reply_text("📋 Your BSE watchlist:\n" + "\n".join(f"• {n} — {c}" for c, n in items.items()))

async def monitor(context: ContextTypes.DEFAULT_TYPE):
    watch = load_json(DATA_FILE, {})
    seen = load_json(STATE_FILE, {})
    for chat_id, companies in watch.items():
        for code, name in companies.items():
            try:
                rows = fetch_results(code)
                for row in rows:
                    key = code + ":" + announcement_key(row)
                    if key in seen:
                        continue
                    # First observation for a code is treated as baseline to avoid old alerts.
                    if code not in seen.get("_initialized_codes", []):
                        seen[key] = True
                        continue
                    await context.bot.send_message(
                        chat_id=int(chat_id),
                        text=format_alert({"code": code, "name": name}, row),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    seen[key] = True
                initialized = set(seen.get("_initialized_codes", []))
                initialized.add(code)
                seen["_initialized_codes"] = sorted(initialized)
            except Exception as exc:
                log.warning("BSE check failed for %s (%s): %s", name, code, exc)
            time.sleep(0.2)
    # Keep state bounded
    save_json(STATE_FILE, seen)

def main():
    if not TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in your environment first.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("list", list_cmd))
    app.job_queue.run_repeating(monitor, interval=POLL_SECONDS, first=5)
    log.info("BSE Result Alert Bot starting; poll=%ss", POLL_SECONDS)
    app.run_polling()

if __name__ == "__main__":
    main()