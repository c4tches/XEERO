import motor.motor_asyncio
import datetime
import os
import logging
from typing import Optional, Dict, Any, List
from bson import ObjectId

logger = logging.getLogger(__name__)

# MongoDB connection URI (set environment variable)
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://ZenoShopifyBot:ZenoShopifyBot@zenoshopifybot.wzgefrn.mongodb.net/?appName=ZenoShopifyBot")
DB_NAME = os.getenv("DB_NAME", "ZenoShopifyBot")

# These are initialized in init_db() to ensure they use the correct event loop
client = None
db = None
users_col = None
proxies_col = None
sites_col = None
keys_col = None
cards_col = None

async def init_db():
    global client, db, users_col, proxies_col, sites_col, keys_col, cards_col
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    users_col = db["users"]
    proxies_col = db["user_proxies"]
    sites_col = db["user_sites"]
    keys_col = db["plan_keys"]
    cards_col = db["card_stats"]
    logger.info("MongoDB client created inside running event loop")
    await users_col.create_index("user_id", unique=True)
    await proxies_col.create_index([("user_id", 1), ("proxy_url", 1)])
    await sites_col.create_index([("user_id", 1), ("site", 1)], unique=True)
    await keys_col.create_index("key", unique=True)
    await cards_col.create_index("checked_at")
    logger.info("MongoDB indexes ensured")

# ---------- USER ----------
async def ensure_user(user_id: int):
    await users_col.update_one(
        {"user_id": user_id},
        {"$setOnInsert": {"plan": "free", "plan_expiry": None, "banned": False}},
        upsert=True
    )

async def get_user_plan(user_id: int) -> str:
    doc = await users_col.find_one({"user_id": user_id})
    if not doc:
        return "free"
    plan = doc.get("plan", "free")
    expiry = doc.get("plan_expiry")
    if expiry and datetime.datetime.now(datetime.timezone.utc) > expiry:
        return "free"
    return plan

async def set_user_plan(user_id: int, plan: str, days: int = 0):
    expiry = None
    if days > 0:
        expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"plan": plan, "plan_expiry": expiry}}
    )

async def is_banned_user(user_id: int) -> bool:
    doc = await users_col.find_one({"user_id": user_id})
    return doc.get("banned", False) if doc else False

async def ban_user(user_id: int, admin_id: int):
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"banned": True, "banned_by": admin_id, "banned_at": datetime.datetime.now(datetime.timezone.utc)}}
    )

async def unban_user(user_id: int):
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"banned": False}, "$unset": {"banned_by": "", "banned_at": ""}}
    )

# ---------- PROXY ----------
async def add_proxy_db(user_id: int, proxy_data: Dict):
    doc = {
        "user_id": user_id,
        "ip": proxy_data['ip'],
        "port": proxy_data['port'],
        "username": proxy_data.get('username'),
        "password": proxy_data.get('password'),
        "proxy_url": proxy_data['proxy_url'],
        "proxy_type": proxy_data.get('type', 'http'),
        "added_at": datetime.datetime.now(datetime.timezone.utc)
    }
    await proxies_col.insert_one(doc)

async def get_random_proxy(user_id: int) -> Optional[Dict]:
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$sample": {"size": 1}}
    ]
    cursor = proxies_col.aggregate(pipeline)
    doc = await cursor.to_list(length=1)
    if doc:
        p = doc[0]
        return {
            'ip': p['ip'],
            'port': p['port'],
            'username': p.get('username'),
            'password': p.get('password'),
            'proxy_url': p['proxy_url'],
            'type': p['proxy_type']
        }
    return None

async def get_all_user_proxies(user_id: int) -> List[Dict]:
    cursor = proxies_col.find({"user_id": user_id}).sort("added_at", 1)
    proxies = []
    async for p in cursor:
        proxies.append({
            'id': str(p['_id']),
            'ip': p['ip'],
            'port': p['port'],
            'username': p.get('username'),
            'password': p.get('password'),
            'proxy_url': p['proxy_url'],
            'proxy_type': p['proxy_type']
        })
    return proxies

async def get_proxy_count(user_id: int) -> int:
    return await proxies_col.count_documents({"user_id": user_id})

async def remove_proxy_by_index(user_id: int, idx: int) -> Optional[Dict]:
    proxies = await get_all_user_proxies(user_id)
    if 0 <= idx < len(proxies):
        proxy_id = proxies[idx]['id']
        await proxies_col.delete_one({"_id": ObjectId(proxy_id)})
        return proxies[idx]
    return None

async def clear_all_proxies(user_id: int) -> int:
    result = await proxies_col.delete_many({"user_id": user_id})
    return result.deleted_count

# ---------- SITE ----------
async def add_site_db(user_id: int, site: str) -> bool:
    try:
        await sites_col.update_one(
            {"user_id": user_id, "site": site},
            {"$setOnInsert": {"added_at": datetime.datetime.now(datetime.timezone.utc)}},
            upsert=True
        )
        return True
    except Exception as e:
        logger.warning("add_site_db failed for user %s: %s", user_id, e)
        return False

async def get_user_sites(user_id: int) -> List[str]:
    cursor = sites_col.find({"user_id": user_id})
    sites = []
    async for doc in cursor:
        sites.append(doc['site'])
    return sites

async def remove_site_db(user_id: int, site: str) -> bool:
    result = await sites_col.delete_one({"user_id": user_id, "site": site})
    return result.deleted_count > 0

# ---------- PLAN KEYS ----------
async def create_key(key: str, days: int, plan_type: str = "pro"):
    await keys_col.insert_one({
        "key": key,
        "days": days,
        "plan_type": plan_type,
        "used": False,
        "used_by": None,
        "used_at": None,
        "created_at": datetime.datetime.now(datetime.timezone.utc)
    })

async def get_key_data(key: str) -> Optional[Dict]:
    doc = await keys_col.find_one({"key": key})
    if doc:
        return {
            'key': doc['key'],
            'days': doc['days'],
            'plan_type': doc['plan_type'],
            'used': doc['used'],
            'used_by': doc.get('used_by')
        }
    return None

async def use_key(user_id: int, key: str):
    doc = await keys_col.find_one({"key": key, "used": False})
    if not doc:
        return False, "Invalid or already used key"
    days = doc['days']
    plan_type = doc['plan_type']
    # Update key
    await keys_col.update_one(
        {"key": key},
        {"$set": {"used": True, "used_by": user_id, "used_at": datetime.datetime.now(datetime.timezone.utc)}}
    )
    # Update user plan
    expiry = None
    if days > 0:
        expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"plan": plan_type, "plan_expiry": expiry}}
    )
    return True, f"{plan_type.upper()} plan activated for {days} days"

async def get_all_keys() -> List[Dict]:
    cursor = keys_col.find().sort("created_at", -1)
    keys = []
    async for doc in cursor:
        keys.append({
            'key': doc['key'],
            'days': doc['days'],
            'plan_type': doc['plan_type'],
            'used': doc['used'],
            'used_by': doc.get('used_by'),
            'created_at': doc['created_at'],
            'used_at': doc.get('used_at')
        })
    return keys

async def delete_key(key: str) -> bool:
    result = await keys_col.delete_one({"key": key})
    return result.deleted_count > 0

# ---------- CARD STATS ----------
async def save_card_to_db(card: str, status: str, response: str, gateway: str, price: str):
    await cards_col.insert_one({
        "card": card,
        "status": status,
        "response": response,
        "gateway": gateway,
        "price": price,
        "checked_at": datetime.datetime.now(datetime.timezone.utc)
    })

# ---------- STATISTICS ----------
async def get_total_users() -> int:
    return await users_col.count_documents({})

async def get_premium_count() -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    return await users_col.count_documents({
        "plan": {"$in": ["pro", "toji"]},
        "$or": [{"plan_expiry": None}, {"plan_expiry": {"$gt": now}}]
    })

async def get_total_sites_count() -> int:
    return await sites_col.count_documents({})

async def get_total_cards_count() -> int:
    return await cards_col.count_documents({})

async def get_approved_count() -> int:
    return await cards_col.count_documents({"status": "APPROVED"})
