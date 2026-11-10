"""কলকাতা কথা: নিউজ ফেচার -> news.json (+ আর্কাইভ, আবহাওয়া, AQI, টেলিগ্রাম অ্যালার্ট)
শুধু Python বিল্ট-ইন লাইব্রেরি লাগে।
আইনি নিয়ম: শুধু হেডলাইন + সোর্স নাম + মূল লিংক রাখা হয়। পুরো লেখা বা ছবি কপি করা হয় না।
"""
import json, os, re, html, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

# ক্যাটাগরি: (বাংলা কোয়েরি, ইংরেজি কোয়েরি)
CATEGORIES = {
    "শীর্ষ": ("কলকাতা", "Kolkata"),
    "শহর": ("কলকাতা পুলিশ OR কলকাতা মেট্রো OR কলকাতা পুরসভা",
            "Kolkata Municipal Corporation OR Kolkata Metro OR Kolkata traffic"),
    "খেলা": ("মোহনবাগান OR ইস্টবেঙ্গল OR কেকেআর OR ইডেন গার্ডেন্স",
            "Mohun Bagan OR East Bengal OR KKR OR Eden Gardens"),
    "ব্যবসা": ("কলকাতা ব্যবসা OR কলকাতা শিল্প", "Kolkata business OR Kolkata economy"),
    "আবহাওয়া": ("কলকাতা আবহাওয়া OR কলকাতা বৃষ্টি", "Kolkata weather OR Kolkata rain OR cyclone Bengal"),
    "সংস্কৃতি": ("কলকাতা সিনেমা OR কলকাতা নাটক OR বইমেলা OR দুর্গাপুজো",
               "Kolkata film OR Kolkata theatre OR Kolkata book fair OR Durga Puja"),
}
PER_QUERY = 25
UA = {"User-Agent": "Mozilla/5.0 (KolkataKathaBot; headline aggregator)"}
BN = re.compile(r"[\u0980-\u09FF]")


def get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def fetch(cat, query, lang):
    loc = "hl=bn&gl=IN&ceid=IN:bn" if lang == "bn" else "hl=en-IN&gl=IN&ceid=IN:en"
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:2d") + "&" + loc
    out = []
    for it in ET.fromstring(get(url)).iter("item"):
        title = html.unescape(it.findtext("title", "")).strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            continue
        out.append({"title": title, "source": src or "News", "link": it.findtext("link", ""),
                    "time": ts.isoformat(), "cat": cat, "lang": "bn" if BN.search(title) else "en"})
    return sorted(out, key=lambda x: x["time"], reverse=True)[:PER_QUERY]


def env_info():
    w = json.loads(get("https://api.open-meteo.com/v1/forecast?latitude=22.5726&longitude=88.3639"
                       "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code"
                       "&timezone=Asia%2FKolkata"))["current"]
    res = {"temp": round(w["temperature_2m"]), "feels": round(w["apparent_temperature"]),
           "hum": w["relative_humidity_2m"], "code": w["weather_code"], "aqi": None}
    try:
        a = json.loads(get("https://air-quality-api.open-meteo.com/v1/air-quality?latitude=22.5726"
                           "&longitude=88.3639&current=us_aqi&timezone=Asia%2FKolkata"))["current"]
        res["aqi"] = round(a["us_aqi"])
    except Exception as e:
        print("aqi failed", e)
    return res


def ai_brief(items):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not items:
        return ""
    heads = "\n".join("- " + i["title"] for i in items[:35])
    prompt = ("নিচে আজকের কলকাতার খবরের হেডলাইন। শুধু এগুলোর ভিত্তিতে নিজের ভাষায় বাংলায় ৪-৫টা বুলেটে "
              "ছোট দৈনিক ব্রিফ লেখো, প্রতিটা এক বাক্য। কোনো তথ্য বা অভিযোগ নিজে থেকে যোগ করবে না। "
              "কারও বিরুদ্ধে অভিযোগ থাকলে 'অভিযোগ' বা 'দাবি' শব্দ ব্যবহার করো।\n\n" + heads)
    body = json.dumps({"model": os.environ.get("MODEL", "claude-haiku-5-5"), "max_tokens": 700,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    hdr = {"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}
    res = json.loads(get("https://api.anthropic.com/v1/messages", body, hdr))
    return "".join(b.get("text", "") for b in res["content"]).strip()


def telegram_alerts(new_items):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    kws = [k.strip().lower() for k in os.environ.get(
        "ALERT_KEYWORDS", "ঘূর্ণিঝড়,cyclone,ভূমিকম্প,earthquake,বন্যা,flood,breaking,মেট্রো বন্ধ").split(",") if k.strip()]
    sent = 0
    for it in new_items:
        if sent >= 5:
            break
        if any(k in it["title"].lower() for k in kws):
            msg = f"🔔 {it['title']}\n({it['source']})\n{it['link']}"
            try:
                get(f"https://api.telegram.org/bot{tok}/sendMessage",
                    urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode(),
                    {"Content-Type": "application/x-www-form-urlencoded"})
                sent += 1
            except Exception as e:
                print("telegram failed", e)


def main():
    seen, items = set(), []
    for cat, (bq, eq) in CATEGORIES.items():
        for q, lang in ((bq, "bn"), (eq, "en")):
            try:
                for it in fetch(cat, q, lang):
                    k = re.sub(r"\W+", "", it["title"].lower())[:60]
                    if k not in seen:
                        seen.add(k)
                        items.append(it)
            except Exception as e:
                print("skip", cat, lang, e)
    items.sort(key=lambda x: x["time"], reverse=True)
    if not items:
        print("no items; keeping old file")
        return

    old = []
    try:
        old = json.load(open("news.json", encoding="utf-8")).get("items", [])
    except Exception:
        pass
    old_links = {i["link"] for i in old}
    telegram_alerts([i for i in items if i["link"] not in old_links] if old else [])

    out = {"updated": datetime.now(timezone.utc).isoformat(), "env": None, "brief": "", "items": items}
    for key, fn, arg in (("env", env_info, None), ("brief", ai_brief, items)):
        try:
            out[key] = fn(arg) if arg else fn()
        except Exception as e:
            print(key, "failed", e)
    json.dump(out, open("news.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # দিনের আর্কাইভ (শুধু হেডলাইন + লিংক)
    os.makedirs("archive", exist_ok=True)
    path = "archive/" + datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".json"
    day = {}
    try:
        day = {i["link"]: i for i in json.load(open(path, encoding="utf-8"))}
    except Exception:
        pass
    for i in items:
        day[i["link"]] = i
    json.dump(list(day.values()), open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print("saved", len(items), "items")


if __name__ == "__main__":
    main()
