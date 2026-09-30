import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import json
import os
import random
from datetime import datetime
from yarl import URL

# ============================================================
# CONFIGURATION
# ============================================================
DISCORD_TOKEN  = os.environ.get("DISCORD_TOKEN", "")
CHANNEL_ID     = int(os.environ.get("CHANNEL_ID", "1504584240451551432"))
CHECK_INTERVAL = 10   # secondes entre chaque scan

# Mots-clés à surveiller
SEARCH_QUERIES = [

    "ralph lauren",
    "lacoste",
    "tommy hilfiger",
    "carhartt",
    "nike vintage",
    "adidas vintage",
]

PRICE_MIN = 3
PRICE_MAX = 15

# Mots interdits — annonces ignorées si contiennent un de ces mots
BLACKLIST = [
    "coque",
    "housse",
    "chargeur",
    "cable",
    "etui",
    "sacoche",
    "protection",
    "verre trempe",
    "accessoire",
    "chaussures",
    "chaussure",
    "basket",
    "paire",
]

seen_ids_file = "seen_ids.json"

# ============================================================
# UTILITAIRES
# ============================================================

def load_seen_ids() -> set:
    if os.path.exists(seen_ids_file):
        with open(seen_ids_file, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_ids(ids: set):
    with open(seen_ids_file, "w") as f:
        json.dump(list(ids), f)

def get_price(raw) -> float:
    if isinstance(raw, dict):
        return float(raw.get("amount", 0))
    try:
        return float(raw)
    except:
        return 0.0

def is_blacklisted(title: str) -> tuple[bool, str]:
    """Vérifie si le titre contient un mot blacklisté."""
    t = title.lower()
    for word in BLACKLIST:
        if word in t:
            return True, word
    return False, ""

# ============================================================
# VINTED API
# ============================================================

VINTED_BASE = "https://www.vinted.fr"
VINTED_API  = "https://www.vinted.fr/api/v2"
HEADERS = {
    "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept"         : "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Referer"        : "https://www.vinted.fr/",
    "Origin"         : "https://www.vinted.fr",
}

async def get_cookie(session: aiohttp.ClientSession):
    try:
        async with session.get(VINTED_BASE, headers=HEADERS) as r:
            session.cookie_jar.filter_cookies(URL(VINTED_BASE))
    except Exception as e:
        print(f"[Cookie] {e}")

async def search_vinted(session: aiohttp.ClientSession, query: str,
                        price_min=None, price_max=None, per_page=20) -> list:
    params = {
        "search_text": query,
        "order"      : "newest_first",
        "per_page"   : per_page,
        "page"       : 1,
    }
    if price_min is not None: params["price_from"] = price_min
    if price_max is not None: params["price_to"]   = price_max
    try:
        async with session.get(
            f"{VINTED_API}/catalog/items",
            params=params,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            if r.status == 200:
                data = await r.json()
                items = data.get("items", [])
                print(f"[Vinted] '{query}' → {len(items)} articles")
                return items
            return []
    except Exception as e:
        print(f"[Search '{query}'] {e}")
        return []

async def get_all_photos(session: aiohttp.ClientSession, item_id: str) -> list[str]:
    """
    Récupère TOUTES les photos d'une annonce via l'endpoint détail.
    """
    try:
        async with session.get(
            f"{VINTED_API}/items/{item_id}",
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status == 200:
                data = await r.json()
                item = data.get("item", {})
                photos = item.get("photos", [])
                urls = []
                for p in photos:
                    url = p.get("full_size_url") or p.get("url") or p.get("thumb_url", "")
                    if url:
                        urls.append(url)
                return urls
    except Exception as e:
        print(f"[Photos] item {item_id} : {e}")
    return []

# ============================================================
# BOT DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user}")
    print(f"📡 {len(SEARCH_QUERIES)} mots-clés | Prix : {PRICE_MIN}€→{PRICE_MAX}€")
    print(f"🚫 {len(BLACKLIST)} mots blacklistés")
    ch = bot.get_channel(CHANNEL_ID)
    if ch:
        print(f"✅ Salon trouvé : #{ch.name}")
        await ch.send(
            f"🤖 **Vinted Flipper démarré !**\n"
            f"📡 {len(SEARCH_QUERIES)} mots-clés surveillés\n"
            f"🚫 {len(BLACKLIST)} mots blacklistés\n"
            f"💶 Prix : {PRICE_MIN}€ → {PRICE_MAX}€"
        )
    else:
        print(f"❌ Salon {CHANNEL_ID} INTROUVABLE")
    check_vinted.start()

@tasks.loop(seconds=CHECK_INTERVAL)
async def check_vinted():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("❌ Salon introuvable")
        return

    seen      = load_seen_ids()
    new_items = []

    # Mélange aléatoire pour ne pas toujours commencer par le même mot-clé
    queries_shuffled = SEARCH_QUERIES.copy()
    random.shuffle(queries_shuffled)

    async with aiohttp.ClientSession() as session:
        await get_cookie(session)

        for query in queries_shuffled:
            items = await search_vinted(session, query, PRICE_MIN, PRICE_MAX)
            await asyncio.sleep(1.5)

            for item in items:
                iid = str(item.get("id", ""))
                if not iid or iid in seen:
                    continue
                seen.add(iid)

                title = item.get("title", "")

                # Filtre blacklist
                blacklisted, word = is_blacklisted(title)
                if blacklisted:
                    print(f"[Blacklist] '{title[:40]}' → mot interdit : '{word}'")
                    continue

                new_items.append(item)

    save_seen_ids(seen)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scan terminé — {len(new_items)} nouvelle(s) annonce(s)")

    # Trie par prix croissant — les meilleures affaires en premier
    new_items.sort(key=lambda x: get_price(x.get("price", 0)))

    for item in new_items[:20]:
        await send_alert(channel, item, session if False else None)
        await asyncio.sleep(0.8)

async def send_alert(channel, item: dict, session=None):
    try:
        title     = item.get("title", "Sans titre")
        prix      = get_price(item.get("price", 0))
        item_id   = str(item.get("id", ""))
        url       = f"https://www.vinted.fr/items/{item_id}"
        condition = item.get("status", "Inconnu")
        seller    = (item.get("user") or {}).get("login", "?")
        location  = item.get("city", "")
        desc      = item.get("description", "")[:200] if item.get("description") else ""

        # Photo principale depuis l'item
        photo_main = item.get("photo") or {}
        main_photo_url = photo_main.get("url", "")

        # Photos supplémentaires — récupération via API détail
        async with aiohttp.ClientSession() as s:
            await get_cookie(s)
            all_photos = await get_all_photos(s, item_id)

        # Embed principal
        embed = discord.Embed(
            title=f"📦 {title[:80]}",
            url=url,
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="💶 Prix",    value=f"**{prix}€**",  inline=True)
        embed.add_field(name="✨ État",    value=condition,        inline=True)
        embed.add_field(name="👤 Vendeur", value=seller,           inline=True)
        if location:
            embed.add_field(name="📍 Ville", value=location, inline=True)
        if desc:
            embed.add_field(name="📝 Description", value=desc, inline=False)
        embed.add_field(name="🔗 Lien", value=f"[Voir l'annonce sur Vinted]({url})", inline=False)

        if main_photo_url:
            embed.set_image(url=main_photo_url)

        embed.set_footer(text=f"Vinted Flipper • {datetime.now().strftime('%d/%m/%Y %H:%M')}")

        await channel.send(embed=embed)

        # Envoie les photos supplémentaires (max 4) dans des embeds séparés
        extra_photos = [p for p in all_photos if p != main_photo_url][:4]
        if extra_photos:
            for i, photo_url in enumerate(extra_photos):
                photo_embed = discord.Embed(
                    color=discord.Color.blurple(),
                    url=url  # même URL pour regrouper les embeds
                )
                photo_embed.set_image(url=photo_url)
                await channel.send(embed=photo_embed)
                await asyncio.sleep(0.3)

    except Exception as e:
        print(f"[Alert] Erreur : {e}")

# ============================================================
# COMMANDES
# ============================================================

@bot.command(name="status")
async def cmd_status(ctx):
    embed = discord.Embed(title="📊 Statut du Bot", color=discord.Color.blurple())
    embed.add_field(name="🔍 Mots-clés",     value="\n".join(f"• {q}" for q in SEARCH_QUERIES), inline=False)
    embed.add_field(name="🚫 Blacklist",      value="\n".join(f"• {w}" for w in BLACKLIST),      inline=False)
    embed.add_field(name="💶 Prix",           value=f"{PRICE_MIN}€ → {PRICE_MAX}€",              inline=True)
    embed.add_field(name="📦 Articles vus",   value=str(len(load_seen_ids())),                   inline=True)
    await ctx.send(embed=embed)

@bot.command(name="reset")
async def cmd_reset(ctx):
    save_seen_ids(set())
    await ctx.send("✅ Historique remis à zéro !")

@bot.command(name="scan")
async def cmd_scan(ctx):
    await ctx.send("🔍 Scan en cours...")
    await check_vinted()

@bot.command(name="blacklist")
async def cmd_blacklist(ctx, *, mot: str):
    """!blacklist coque — ajoute un mot à la blacklist sans redémarrer"""
    mot = mot.lower().strip()
    if mot not in BLACKLIST:
        BLACKLIST.append(mot)
        await ctx.send(f"✅ **`{mot}`** ajouté à la blacklist ! ({len(BLACKLIST)} mots au total)")
    else:
        await ctx.send(f"⚠️ **`{mot}`** est déjà dans la blacklist.")

@bot.command(name="unblacklist")
async def cmd_unblacklist(ctx, *, mot: str):
    """!unblacklist coque — retire un mot de la blacklist"""
    mot = mot.lower().strip()
    if mot in BLACKLIST:
        BLACKLIST.remove(mot)
        await ctx.send(f"✅ **`{mot}`** retiré de la blacklist.")
    else:
        await ctx.send(f"⚠️ **`{mot}`** n'est pas dans la blacklist.")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
