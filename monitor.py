import os
import json
import hashlib
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# TELEGRAM CONFIGURATION
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# YOUR COMPANY WATCHLIST
COMPANIES = {
    "543390": "PB Fintech",
    "543266": "Heranba Industries",
    "544686": "Shayona Engineering",
    "543530": "Paradeep Phosphates",
    "531795": "Atul Auto"
}

# BSE API
BSE_URL = (
    "https://api.bseindia.com/"
    "BseIndiaAPI/api/AnnSubCategoryGetData/w"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/155.0.0.0 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*"
}

STATE_FILE = "seen.json"


def load_seen():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_seen(seen):
    with open(STATE_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": True
        },
        timeout=20
    )

    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError("Telegram delivery failed")


def check_company(code, company, seen):

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    yesterday = today - timedelta(days=2)

    params = {
        "pageno": 1,
        "strCat": "Result",
        "subcategory": "Financial+Results",
        "strPrevDate": yesterday.strftime("%Y%m%d"),
        "strToDate": today.strftime("%Y%m%d"),
        "strSearch": "P",
        "strScrip": code,
        "strType": "C"
    }

    response = requests.get(
        BSE_URL,
        params=params,
        headers=HEADERS,
        timeout=25
    )

    response.raise_for_status()

    data = response.json()

    rows = data.get("Table", [])

    for row in rows:

        subject = str(
            row.get("NEWSSUB", "")
        )

        headline = str(
            row.get("HEADLINE", "")
        )

        text = (subject + " " + headline).lower()

        keywords = [
            "financial results",
            "unaudited",
            "audited",
            "quarterly results",
            "quarter ended"
        ]

        if not any(word in text for word in keywords):
            continue

        attachment = row.get("ATTACHMENTNAME", "")

        identity = json.dumps(
            row, sort_keys=True, default=str
        )

        key = hashlib.sha256(
            identity.encode()
        ).hexdigest()

        if key in seen:
            continue

        pdf = ""

        if attachment:
            pdf = (
                "https://www.bseindia.com/"
                "xml-data/corpfiling/AttachLive/"
                + str(attachment).lstrip("/")
            )

        message = (
            "🚨 BSE RESULT ALERT\n\n"
            f"Company: {company}\n"
            f"BSE Code: {code}\n\n"
            f"Announcement: {subject}\n"
            f"Headline: {headline}\n\n"
            f"Published: {row.get('NEWS_DT', 'See BSE filing')}\n\n"
            f"PDF: {pdf if pdf else 'Check BSE website'}\n\n"
            "Source: BSE India"
        )

        send_telegram(message)

        seen.append(key)

        print("NEW RESULT:", company)


def main():

    seen = load_seen()

    for code, company in COMPANIES.items():

        try:
            check_company(code, company, seen)

        except Exception as error:
            print(company, "ERROR:", error)

    save_seen(seen)


if __name__ == "__main__":
    main()
