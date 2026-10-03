import os
import re
import time
import uuid
import sqlite3
import hashlib
import secrets
import threading
import html as html_lib
import base64

import requests

from flask import (
    Flask,
    request,
    redirect,
    session,
    jsonify,
    render_template_string,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "cacador-ofertas-secret-key-change-me"
)

DATABASE = "ofertas.db"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

ML_CLIENT_ID = os.getenv(
    "ML_CLIENT_ID",
    ""
).strip()

ML_CLIENT_SECRET = os.getenv(
    "ML_CLIENT_SECRET",
    ""
).strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

SITE_ID = "MLB"

MIN_PRODUCT_PRICE = 69.90

REQUEST_TIMEOUT = 18

MAX_PRODUCTS_SCAN = 150

MAX_ITEMS_PER_PRODUCT = 8

PRODUCT_SEARCH_LIMIT = 50


# ============================================================
# BUSCAS
# ============================================================

SEARCH_GROUPS = {

    "Celulares": [
        "celular",
        "smartphone",
        "iphone",
        "samsung galaxy",
        "xiaomi",
    ],

    "Eletrônicos": [
        "smart tv",
        "fone bluetooth",
        "notebook",
        "tablet",
        "caixa de som",
    ],

    "Casa": [
        "air fryer",
        "liquidificador",
        "aspirador",
        "cafeteira",
        "ventilador",
    ],

    "Cozinha": [
        "panela",
        "jogo de panelas",
        "microondas",
        "sanduicheira",
        "processador",
    ],

    "Academia": [
        "halter",
        "kit academia",
        "whey protein",
        "acessorios academia",
        "barra musculação",
    ],

    "Ferramentas": [
        "furadeira",
        "parafusadeira",
        "jogo ferramentas",
        "chave impacto",
        "kit ferramentas",
    ],

    "Automotivo": [
        "pneu",
        "central multimidia",
        "capa banco carro",
        "lampada led carro",
        "acessorios carro",
    ],

    "Moda": [
        "tenis",
        "tenis feminino",
        "tenis masculino",
        "mochila",
        "sandalia",
    ],

    "Perfumes": [
        "perfume",
        "perfume feminino",
        "perfume masculino",
        "kit perfume",
        "perfume importado",
    ],
}


# ============================================================
# CUPONS FALLBACK
# ============================================================

OFFICIAL_COUPON_FALLBACK = [

    {
        "code": "1FRUIT",
        "type": "percent",
        "value": 10.0,
        "min_purchase": 79.0,
        "max_discount": 50.0,
        "source": "official_fallback",
    },

    {
        "code": "S5PRUNK",
        "type": "percent",
        "value": 15.0,
        "min_purchase": 119.0,
        "max_discount": 70.0,
        "source": "official_fallback",
    },

    {
        "code": "EC0LACOLA",
        "type": "percent",
        "value": 12.0,
        "min_purchase": 99.0,
        "max_discount": 60.0,
        "source": "official_fallback",
    },

    {
        "code": "CR3VI1S",
        "type": "percent",
        "value": 12.0,
        "min_purchase": 129.0,
        "max_discount": 50.0,
        "source": "official_fallback",
    },

    {
        "code": "N4GAS4K1",
        "type": "percent",
        "value": 10.0,
        "min_purchase": 159.0,
        "max_discount": 50.0,
        "source": "official_fallback",
    },

    {
        "code": "W33ENY1",
        "type": "percent",
        "value": 10.0,
        "min_purchase": 99.0,
        "max_discount": 60.0,
        "source": "official_fallback",
    },

    {
        "code": "P4NOR4M1C",
        "type": "percent",
        "value": 12.0,
        "min_purchase": 129.0,
        "max_discount": 60.0,
        "source": "official_fallback",
    },
]


# ============================================================
# RESTRIÇÕES PÚBLICAS DOS CUPONS
# ============================================================

EXCLUDED_TERMS = [

    "bola oficial copa do mundo 2026",

    "camiseta oficial",
    "camisetas oficiais",

    "adidas",

    "puma",
    "pandora",
    "mizuno",
    "dream fitness",
    "nike",
    "natura",
    "decathlon",
    "casas bahia",
    "vulcabras",
    "olympikus",
    "under armour",
    "wct fitness",
    "converse",
    "pampers",
    "hp",
    "max titanium",
    "probiótica",
    "epay",
    "anker",
    "assai",
    "sony",
    "nespresso",
    "nestle",
    "principia",
    "rockstar games",
    "gta vi",
    "digital goods",
    "level up",
    "roblox",
    "razer",
    "google playstation",
    "playstation",
    "steam",
    "nintendo",
    "xbox",
    "spotify",
    "uber",

    "stanley",
    "dewalt",
    "black & decker",
    "black and decker",

    "cicampo",
    "dutra maquinas",
    "inter level",
    "opção parafusos",
    "growth",
    "web continental",
    "gallant",
    "krw bikes",
    "ogm bikes",
    "south bikes",
    "boxer",
    "menegotti",
    "deca",
    "esab",
    "vonder",
    "razr",
    "cjau",
    "ferragens floresta",
    "tork tools",
]


# ============================================================
# BANCO
# ============================================================

def db():

    conn = sqlite3.connect(
        DATABASE,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at REAL,
            user_id TEXT,
            nickname TEXT,
            updated_at REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            type TEXT,
            value REAL,
            min_purchase REAL,
            max_discount REAL,
            source TEXT,
            updated_at REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            product_title TEXT,
            item_id TEXT,
            seller_id TEXT,
            category TEXT,
            current_price REAL,
            coupon_code TEXT,
            coupon_discount REAL,
            final_price REAL,
            effective_discount REAL,
            coupon_status TEXT,
            coupon_source TEXT,
            free_shipping INTEGER DEFAULT 0,
            buy_box INTEGER DEFAULT 0,
            product_url TEXT,
            created_at REAL
        )
    """)

    # Migração para banco antigo
    columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(offers)"
        ).fetchall()
    ]

    if "coupon_status" not in columns:

        conn.execute("""
            ALTER TABLE offers
            ADD COLUMN coupon_status TEXT
        """)

    if "coupon_source" not in columns:

        conn.execute("""
            ALTER TABLE offers
            ADD COLUMN coupon_source TEXT
        """)

    conn.commit()

    conn.close()


init_db()


# ============================================================
# UTILITÁRIOS
# ============================================================

def money(value):

    try:
        value = float(value)
    except Exception:
        value = 0

    return (
        f"R$ {value:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def clean_text(value):

    if value is None:
        return ""

    value = str(value)

    value = html_lib.unescape(
        value
    )

    value = re.sub(
        r"<[^>]+>",
        " ",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def normalize_coupon_text(text):

    text = html_lib.unescape(
        text or ""
    )

    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = html_lib.unescape(
        text
    )

    text = text.replace(
        "\xa0",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def parse_money(text):

    if not text:
        return None

    text = str(text)

    text = text.replace(
        "R$",
        ""
    )

    text = text.replace(
        " ",
        ""
    )

    text = re.sub(
        r"[^\d,.\-]",
        "",
        text
    )

    if not text:
        return None

    if "," in text:

        text = text.replace(
            ".",
            ""
        )

        text = text.replace(
            ",",
            "."
        )

    try:
        return float(text)

    except Exception:
        return None


# ============================================================
# OAUTH
# ============================================================

def get_token_row():

    conn = db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id = 1"
    ).fetchone()

    conn.close()

    return row


def save_tokens(
    access_token,
    refresh_token=None,
    expires_in=21600,
    user_id=None,
    nickname=None
):

    current = get_token_row()

    if (
        refresh_token is None
        and current
    ):

        refresh_token = current[
            "refresh_token"
        ]

    conn = db()

    conn.execute("""
        INSERT INTO oauth_tokens
        (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(id)
        DO UPDATE SET

            access_token =
                excluded.access_token,

            refresh_token =
                excluded.refresh_token,

            expires_at =
                excluded.expires_at,

            user_id =
                excluded.user_id,

            nickname =
                excluded.nickname,

            updated_at =
                excluded.updated_at
    """, (
        access_token,
        refresh_token,
        time.time()
        + max(
            int(expires_in or 21600) - 60,
            60
        ),
        user_id,
        nickname,
        time.time()
    ))

    conn.commit()

    conn.close()


def get_access_token():

    row = get_token_row()

    if not row:
        return None

    return row[
        "access_token"
    ]


def get_refresh_token():

    row = get_token_row()

    if not row:
        return None

    return row[
        "refresh_token"
    ]


def refresh_token_for_worker(
    refresh_token
):

    if not refresh_token:
        return None

    if (
        not ML_CLIENT_ID
        or not ML_CLIENT_SECRET
    ):
        return None

    payload = {

        "grant_type":
            "refresh_token",

        "client_id":
            ML_CLIENT_ID,

        "client_secret":
            ML_CLIENT_SECRET,

        "refresh_token":
            refresh_token,
    }

    try:

        response = requests.post(
            ML_TOKEN,
            data=payload,
            timeout=REQUEST_TIMEOUT
        )

    except Exception as e:

        print(
            "[OAUTH REFRESH EXCEPTION]",
            e
        )

        return None

    if response.status_code != 200:

        print(
            "[OAUTH REFRESH ERRO]",
            response.status_code,
            response.text[:500]
        )

        return None

    data = response.json()

    access_token = data.get(
        "access_token"
    )

    if not access_token:
        return None

    new_refresh = data.get(
        "refresh_token",
        refresh_token
    )

    save_tokens(
        access_token,
        new_refresh,
        data.get(
            "expires_in",
            21600
        )
    )

    return access_token


# ============================================================
# PKCE
# ============================================================

def make_code_verifier():

    return secrets.token_urlsafe(
        64
    )


def make_code_challenge(
    verifier
):

    digest = hashlib.sha256(
        verifier.encode(
            "utf-8"
        )
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).rstrip(
        b"="
    ).decode(
        "utf-8"
    )


# ============================================================
# HTTP MERCADO LIVRE
# ============================================================

def ml_get_worker(
    path,
    auth=None,
    params=None
):

    auth = auth or {}

    access_token = auth.get(
        "access_token"
    )

    refresh_token = auth.get(
        "refresh_token"
    )

    headers = {

        "Accept":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/2.0",
    }

    if access_token:

        headers[
            "Authorization"
        ] = (
            f"Bearer {access_token}"
        )

    url = (
        path
        if path.startswith("http")
        else ML_API + path
    )

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

    except Exception as e:

        print(
            "[ML GET EXCEPTION]",
            e
        )

        return None, None

    if (
        response.status_code == 401
        and refresh_token
    ):

        new_token = (
            refresh_token_for_worker(
                refresh_token
            )
        )

        if new_token:

            auth[
                "access_token"
            ] = new_token

            headers[
                "Authorization"
            ] = (
                f"Bearer {new_token}"
            )

            try:

                response = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=REQUEST_TIMEOUT
                )

            except Exception as e:

                print(
                    "[ML RETRY EXCEPTION]",
                    e
                )

                return None, None

    try:

        data = response.json()

    except Exception:

        data = {}

    return (
        response.status_code,
        data
    )


# ============================================================
# CUPONS - BANCO
# ============================================================

def save_coupon(
    coupon
):

    conn = db()

    conn.execute("""
        INSERT INTO coupons
        (
            code,
            type,
            value,
            min_purchase,
            max_discount,
            source,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(code)
        DO UPDATE SET

            type =
                excluded.type,

            value =
                excluded.value,

            min_purchase =
                excluded.min_purchase,

            max_discount =
                excluded.max_discount,

            source =
                excluded.source,

            updated_at =
                excluded.updated_at
    """, (
        coupon[
            "code"
        ],

        coupon.get(
            "type"
        ),

        coupon.get(
            "value"
        ),

        coupon.get(
            "min_purchase"
        ),

        coupon.get(
            "max_discount"
        ),

        coupon.get(
            "source",
            "scraped"
        ),

        time.time()
    ))

    conn.commit()

    conn.close()


def save_coupons(
    coupons
):

    for coupon in coupons:

        save_coupon(
            coupon
        )


def load_coupons():

    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM coupons
        ORDER BY code
    """).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# PARSER DE CUPONS
# ============================================================

def parse_coupon_blocks(
    page
):

    text = normalize_coupon_text(
        page
    )

    matches = list(
        re.finditer(
            r"\bCupom\s+([A-Z0-9][A-Z0-9_-]{3,29})\b",
            text,
            re.IGNORECASE
        )
    )

    coupons = []

    for index, match in enumerate(
        matches
    ):

        code = match.group(
            1
        ).upper()

        start = match.start()

        if (
            index + 1
            < len(matches)
        ):

            end = matches[
                index + 1
            ].start()

        else:

            end = min(
                len(text),
                start + 1800
            )

        block = text[
            start:end
        ]

        if len(block) < 15:
            continue

        percent = None

        percent_patterns = [

            r"desconto\s+de\s+até\s+(\d+(?:[.,]\d+)?)\s*%",

            r"desconto\s+de\s+(\d+(?:[.,]\d+)?)\s*%",

            r"(\d+(?:[.,]\d+)?)\s*%\s*(?:de\s+)?desconto",
        ]

        for pattern in percent_patterns:

            m = re.search(
                pattern,
                block,
                re.IGNORECASE
            )

            if m:

                try:

                    percent = float(
                        m.group(
                            1
                        ).replace(
                            ",",
                            "."
                        )
                    )

                    break

                except Exception:
                    pass

        fixed = None

        fixed_patterns = [

            r"desconto\s+de\s+até\s+R\$\s*([\d\.,]+)",

            r"R\$\s*([\d\.,]+)\s*(?:OFF|de desconto)",
        ]

        for pattern in fixed_patterns:

            m = re.search(
                pattern,
                block,
                re.IGNORECASE
            )

            if m:

                fixed = parse_money(
                    m.group(1)
                )

                if fixed:
                    break

        min_purchase = None

        min_patterns = [

            r"compra\s+a\s+partir\s+de\s+R\$\s*([\d\.,]+)",

            r"compras?\s+a\s+partir\s+de\s+R\$\s*([\d\.,]+)",

            r"pedido\s+a\s+partir\s+de\s+R\$\s*([\d\.,]+)",
        ]

        for pattern in min_patterns:

            m = re.search(
                pattern,
                block,
                re.IGNORECASE
            )

            if m:

                min_purchase = parse_money(
                    m.group(1)
                )

                if min_purchase is not None:
                    break

        max_discount = None

        max_patterns = [

            r"desconto\s+máximo\s+de\s+R\$\s*([\d\.,]+)",

            r"máximo\s+de\s+R\$\s*([\d\.,]+)",
        ]

        for pattern in max_patterns:

            m = re.search(
                pattern,
                block,
                re.IGNORECASE
            )

            if m:

                max_discount = parse_money(
                    m.group(1)
                )

                if max_discount is not None:
                    break

        if (
            percent is None
            and fixed is None
        ):
            continue

        coupon = {

            "code":
                code,

            "type":
                "percent"
                if percent is not None
                else "fixed",

            "value":
                percent
                if percent is not None
                else fixed,

            "min_purchase":
                min_purchase or 0,

            "max_discount":
                max_discount,

            "source":
                "scraped",
        }

        coupons.append(
            coupon
        )

        print(
            "[CUPOM OK]",
            code,
            "|",
            coupon["type"],
            coupon["value"],
            "| min",
            coupon["min_purchase"],
            "| max",
            coupon["max_discount"]
        )

    unique = {}

    for coupon in coupons:

        unique[
            coupon["code"]
        ] = coupon

    return list(
        unique.values()
    )


# ============================================================
# SINCRONIZAR CUPONS
# ============================================================

def sync_coupons():

    url = (
        "https://www.mercadolivre.com.br/l/promocoes"
    )

    headers = {

        "User-Agent":
            (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),

        "Accept-Language":
            "pt-BR,pt;q=0.9",
    }

    coupons = []

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        print(
            "[CUPONS HTTP]",
            response.status_code
        )

        if response.ok:

            coupons = parse_coupon_blocks(
                response.text
            )

    except Exception as e:

        print(
            "[CUPONS EXCEPTION]",
            e
        )

    if not coupons:

        print(
            "[CUPONS] "
            "Usando fallback oficial."
        )

        coupons = (
            OFFICIAL_COUPON_FALLBACK.copy()
        )

    save_coupons(
        coupons
    )

    print(
        "[CUPONS TOTAL]",
        len(coupons)
    )

    return coupons


# ============================================================
# VERIFICAR RESTRIÇÃO
# ============================================================

def product_has_coupon_restriction(
    title
):

    normalized = clean_text(
        title
    ).lower()

    for term in EXCLUDED_TERMS:

        if term in normalized:

            print(
                "[CUPOM BLOQUEADO]",
                term,
                "|",
                title[:150]
            )

            return True

    return False


# ============================================================
# CÁLCULO
# ============================================================

def calculate_coupon(
    coupon,
    price
):

    try:

        price = float(
            price
        )

    except Exception:

        return None

    if price < MIN_PRODUCT_PRICE:
        return None

    minimum = float(
        coupon.get(
            "min_purchase"
        ) or 0
    )

    if price < minimum:
        return None

    coupon_type = coupon.get(
        "type"
    )

    value = float(
        coupon.get(
            "value"
        ) or 0
    )

    if coupon_type == "percent":

        discount = (
            price * value / 100.0
        )

    else:

        discount = value

    max_discount = coupon.get(
        "max_discount"
    )

    if max_discount is not None:

        try:

            discount = min(
                discount,
                float(max_discount)
            )

        except Exception:
            pass

    discount = min(
        max(
            discount,
            0
        ),
        price
    )

    if discount <= 0:
        return None

    final_price = (
        price - discount
    )

    effective = (
        discount / price * 100
        if price > 0
        else 0
    )

    return {

        "code":
            coupon["code"],

        "discount":
            round(
                discount,
                2
            ),

        "final_price":
            round(
                final_price,
                2
            ),

        "effective_discount":
            round(
                effective,
                2
            ),

        "source":
            coupon.get(
                "source",
                "unknown"
            ),
    }


def best_coupon(
    price,
    coupons,
    title
):

    if product_has_coupon_restriction(
        title
    ):

        return None

    candidates = []

    for coupon in coupons:

        result = calculate_coupon(
            coupon,
            price
        )

        if result:

            candidates.append(
                result
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (

            -x[
                "discount"
            ],

            -x[
                "effective_discount"
            ],

            x[
                "final_price"
            ],
        )
    )

    return candidates[0]


# ============================================================
# PRODUTOS
# ============================================================

def product_search(
    query,
    auth
):

    status, data = ml_get_worker(

        "/products/search",

        auth=auth,

        params={

            "site_id":
                SITE_ID,

            "q":
                query,

            "status":
                "active",

            "limit":
                PRODUCT_SEARCH_LIMIT,

            "offset":
                0,
        }
    )

    if status != 200:

        print(
            "[PRODUCT SEARCH ERRO]",
            query,
            status,
            str(data)[:500]
        )

        return []

    results = data.get(
        "results",
        []
    )

    print(
        "[PRODUCT SEARCH]",
        query,
        "=>",
        len(results)
    )

    return results


def get_product(
    product_id,
    auth
):

    status, data = ml_get_worker(

        f"/products/{product_id}",

        auth=auth
    )

    if status == 200:

        return data

    return None


def get_product_items(
    product_id,
    auth
):

    status, data = ml_get_worker(

        f"/products/{product_id}/items",

        auth=auth,

        params={

            "limit":
                MAX_ITEMS_PER_PRODUCT,

            "offset":
                0,
        }
    )

    if status == 200:

        return data.get(
            "results",
            []
        )

    print(
        "[PRODUCT ITEMS ERRO]",
        product_id,
        status
    )

    return []


# ============================================================
# PREÇO ATUAL
# ============================================================

def get_sale_price(
    item_id,
    auth
):

    status, data = ml_get_worker(

        f"/items/{item_id}/sale_price",

        auth=auth,

        params={
            "context":
                "channel_marketplace"
        }
    )

    if status != 200:
        return None

    if not isinstance(
        data,
        dict
    ):
        return None

    amount = data.get(
        "amount"
    )

    if amount is None:

        amount = data.get(
            "price"
        )

    try:

        return float(
            amount
        )

    except Exception:

        return None


def get_prices(
    item_id,
    auth
):

    status, data = ml_get_worker(

        f"/items/{item_id}/prices",

        auth=auth
    )

    if status != 200:
        return None

    if not isinstance(
        data,
        dict
    ):
        return None

    prices = data.get(
        "prices",
        []
    )

    if not isinstance(
        prices,
        list
    ):
        return None

    values = []

    for price in prices:

        if not isinstance(
            price,
            dict
        ):
            continue

        amount = price.get(
            "amount"
        )

        if amount is None:
            continue

        try:

            amount = float(
                amount
            )

        except Exception:

            continue

        if amount > 0:

            values.append(
                amount
            )

    if not values:
        return None

    return min(
        values
    )


def get_current_item_price(
    item,
    auth
):

    item_id = item.get(
        "item_id"
    )

    if not item_id:
        return None

    sale_price = get_sale_price(
        item_id,
        auth
    )

    if sale_price is not None:
        return sale_price

    prices = get_prices(
        item_id,
        auth
    )

    if prices is not None:
        return prices

    try:

        price = float(
            item.get(
                "price"
            )
        )

        if price > 0:
            return price

    except Exception:
        pass

    return None


# ============================================================
# FRETE
# ============================================================

def shipping_is_free(
    item
):

    shipping = item.get(
        "shipping"
    )

    if not isinstance(
        shipping,
        dict
    ):
        return False

    if shipping.get(
        "free_shipping"
    ) is True:

        return True

    cost = shipping.get(
        "cost"
    )

    try:

        return (
            float(cost or 0)
            <= 0
        )

    except Exception:

        return False


# ============================================================
# CANDIDATO
# ============================================================

def build_candidate(
    product,
    item,
    category,
    coupons,
    auth,
    buy_box=False
):

    item_id = item.get(
        "item_id"
    )

    if not item_id:
        return None

    title = clean_text(

        product.get(
            "name"
        )

        or product.get(
            "title"
        )

        or "Produto"
    )

    price = get_current_item_price(
        item,
        auth
    )

    if price is None:
        return None

    try:

        price = float(
            price
        )

    except Exception:

        return None

    if price < MIN_PRODUCT_PRICE:
        return None

    coupon = best_coupon(

        price,

        coupons,

        title
    )

    if coupon:

        final_price = coupon[
            "final_price"
        ]

        discount = coupon[
            "discount"
        ]

        effective = coupon[
            "effective_discount"
        ]

        coupon_code = coupon[
            "code"
        ]

        coupon_source = coupon.get(
            "source"
        )

        # ----------------------------------------------------
        # IMPORTANTE:
        # nenhum cupom público está sendo tratado como
        # confirmado pelo checkout.
        # ----------------------------------------------------

        if coupon_source == "scraped":

            coupon_status = (
                "estimado"
            )

        else:

            coupon_status = (
                "estimado"
            )

    else:

        final_price = price

        discount = 0

        effective = 0

        coupon_code = None

        coupon_source = None

        coupon_status = None

    seller_id = item.get(
        "seller_id"
    )

    if seller_id is None:
        seller_id = ""

    return {

        "product_id":
            product.get(
                "id"
            ),

        "product_title":
            title,

        "item_id":
            item_id,

        "seller_id":
            seller_id,

        "category":
            category,

        "current_price":
            round(
                price,
                2
            ),

        "coupon_code":
            coupon_code,

        "coupon_discount":
            round(
                discount,
                2
            ),

        "final_price":
            round(
                final_price,
                2
            ),

        "effective_discount":
            round(
                effective,
                2
            ),

        "coupon_status":
            coupon_status,

        "coupon_source":
            coupon_source,

        "free_shipping":
            shipping_is_free(
                item
            ),

        "buy_box":
            bool(
                buy_box
            ),

        "product_url":
            (
                "https://www.mercadolivre.com.br/"
                f"p/{product.get('id')}"
            ),
    }


# ============================================================
# MELHOR CANDIDATO
# ============================================================

def choose_best_candidate(
    candidates
):

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (

            x[
                "final_price"
            ],

            -x[
                "coupon_discount"
            ],

            -x[
                "effective_discount"
            ],

            not x[
                "free_shipping"
            ],

            not x[
                "buy_box"
            ],

            x[
                "current_price"
            ],
        )
    )

    return candidates[0]


# ============================================================
# PROCESSAR PRODUTO
# ============================================================

def process_product(
    product_id,
    category,
    auth,
    coupons
):

    product = get_product(
        product_id,
        auth
    )

    if not product:
        return None

    items = get_product_items(
        product_id,
        auth
    )

    # --------------------------------------------------------
    # BUY BOX
    # --------------------------------------------------------

    if not items:

        winner = product.get(
            "buy_box_winner"
        )

        if isinstance(
            winner,
            dict
        ):

            winner_item_id = winner.get(
                "item_id"
            )

            if winner_item_id:

                items = [
                    {
                        **winner,
                        "item_id":
                            winner_item_id,
                    }
                ]

    # --------------------------------------------------------
    # CHILDREN
    # --------------------------------------------------------

    if not items:

        children = product.get(
            "children_ids"
        )

        if isinstance(
            children,
            list
        ):

            for child_id in children[:3]:

                child = get_product(
                    child_id,
                    auth
                )

                if not child:
                    continue

                child_items = get_product_items(
                    child_id,
                    auth
                )

                if child_items:

                    items.extend(
                        child_items
                    )

    if not items:
        return None

    winner = product.get(
        "buy_box_winner"
    )

    buy_box_item_id = None

    if isinstance(
        winner,
        dict
    ):

        buy_box_item_id = winner.get(
            "item_id"
        )

    items = items[
        :MAX_ITEMS_PER_PRODUCT
    ]

    candidates = []

    for item in items:

        is_buy_box = (
            item.get(
                "item_id"
            )
            == buy_box_item_id
        )

        candidate = build_candidate(

            product=product,

            item=item,

            category=category,

            coupons=coupons,

            auth=auth,

            buy_box=is_buy_box
        )

        if candidate:

            candidates.append(
                candidate
            )

    return choose_best_candidate(
        candidates
    )


# ============================================================
# JOBS
# ============================================================

JOBS = {}


def update_job(
    job_id,
    status=None,
    progress=None,
    message=None
):

    job = JOBS.get(
        job_id
    )

    if not job:
        return

    if status is not None:

        job[
            "status"
        ] = status

    if progress is not None:

        job[
            "progress"
        ] = progress

    if message is not None:

        job[
            "message"
        ] = message

    job[
        "updated_at"
    ] = time.time()


# ============================================================
# SCAN
# ============================================================

def run_scan(
    job_id,
    auth
):

    update_job(
        job_id,
        status="running",
        progress=0,
        message="Atualizando cupons..."
    )

    try:

        coupons = sync_coupons()

        if not coupons:

            coupons = (
                OFFICIAL_COUPON_FALLBACK.copy()
            )

        update_job(

            job_id,

            message=(
                f"{len(coupons)} cupons "
                "encontrados. Buscando produtos..."
            )
        )

        product_map = {}

        all_queries = []

        for category, queries in SEARCH_GROUPS.items():

            for query in queries:

                all_queries.append(
                    (
                        category,
                        query
                    )
                )

        total_queries = len(
            all_queries
        )

        for index, (
            category,
            query
        ) in enumerate(
            all_queries,
            start=1
        ):

            if (
                len(product_map)
                >= MAX_PRODUCTS_SCAN
            ):
                break

            results = product_search(
                query,
                auth
            )

            for result in results:

                product_id = None

                if isinstance(
                    result,
                    str
                ):

                    product_id = result

                elif isinstance(
                    result,
                    dict
                ):

                    product_id = (

                        result.get(
                            "id"
                        )

                        or result.get(
                            "product_id"
                        )
                    )

                if not product_id:
                    continue

                product_map.setdefault(
                    product_id,
                    category
                )

                if (
                    len(product_map)
                    >= MAX_PRODUCTS_SCAN
                ):
                    break

            progress = int(
                (
                    index
                    / max(
                        total_queries,
                        1
                    )
                )
                * 25
            )

            update_job(

                job_id,

                progress=progress,

                message=(
                    f"Encontrados "
                    f"{len(product_map)} "
                    "produtos..."
                )
            )

        print(
            "[SCAN] PRODUTOS:",
            len(product_map)
        )

        unique_offers = {}

        product_items = list(
            product_map.items()
        )

        total_products = len(
            product_items
        )

        for index, (
            product_id,
            category
        ) in enumerate(
            product_items,
            start=1
        ):

            try:

                candidate = process_product(

                    product_id,

                    category,

                    auth,

                    coupons
                )

                if candidate:

                    existing = (
                        unique_offers.get(
                            product_id
                        )
                    )

                    if existing is None:

                        unique_offers[
                            product_id
                        ] = candidate

                    else:

                        chosen = (
                            choose_best_candidate(
                                [
                                    existing,
                                    candidate
                                ]
                            )
                        )

                        if chosen:

                            unique_offers[
                                product_id
                            ] = chosen

            except Exception as e:

                print(
                    "[PROCESS PRODUCT ERRO]",
                    product_id,
                    repr(e)
                )

            progress = (

                25

                + int(

                    (
                        index
                        / max(
                            total_products,
                            1
                        )
                    )
                    * 70
                )
            )

            update_job(

                job_id,

                progress=progress,

                message=(
                    f"Analisando "
                    f"{index}/"
                    f"{total_products} "
                    "produtos..."
                )
            )

        offers = list(
            unique_offers.values()
        )

        offers.sort(
            key=lambda x: (

                -x[
                    "coupon_discount"
                ],

                -x[
                    "effective_discount"
                ],

                x[
                    "final_price"
                ],

                not x[
                    "free_shipping"
                ],
            )
        )

        # ----------------------------------------------------
        # BANCO
        # ----------------------------------------------------

        conn = db()

        conn.execute(
            "DELETE FROM offers"
        )

        for offer in offers:

            conn.execute("""
                INSERT INTO offers
                (
                    product_id,
                    product_title,
                    item_id,
                    seller_id,
                    category,
                    current_price,
                    coupon_code,
                    coupon_discount,
                    final_price,
                    effective_discount,
                    coupon_status,
                    coupon_source,
                    free_shipping,
                    buy_box,
                    product_url,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (

                offer[
                    "product_id"
                ],

                offer[
                    "product_title"
                ],

                offer[
                    "item_id"
                ],

                offer[
                    "seller_id"
                ],

                offer[
                    "category"
                ],

                offer[
                    "current_price"
                ],

                offer[
                    "coupon_code"
                ],

                offer[
                    "coupon_discount"
                ],

                offer[
                    "final_price"
                ],

                offer[
                    "effective_discount"
                ],

                offer[
                    "coupon_status"
                ],

                offer[
                    "coupon_source"
                ],

                int(
                    offer[
                        "free_shipping"
                    ]
                ),

                int(
                    offer[
                        "buy_box"
                    ]
                ),

                offer[
                    "product_url"
                ],

                time.time()
            ))

        conn.commit()

        conn.close()

        update_job(

            job_id,

            status="done",

            progress=100,

            message=(
                f"Caça finalizada: "
                f"{len(offers)} "
                "produtos únicos."
            )
        )

        print(
            "[SCAN FINALIZADO]",
            len(offers),
            "produtos únicos"
        )

    except Exception as e:

        print(
            "[SCAN ERRO]",
            repr(e)
        )

        update_job(

            job_id,

            status="error",

            progress=100,

            message=(
                f"Erro durante a caça: {e}"
            )
        )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    row = get_token_row()

    connected = bool(
        row
        and row[
            "access_token"
        ]
    )

    nickname = (
        row[
            "nickname"
        ]
        if row
        else None
    )

    return render_template_string(

        HTML,

        connected=connected,

        nickname=nickname
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    row = get_token_row()

    return jsonify({

        "status":
            "ok",

        "app":
            "Cacador de Ofertas",

        "mercadolivre":
            bool(
                row
                and row[
                    "access_token"
                ]
            )
    })


# ============================================================
# CONECTAR MERCADO LIVRE
# ============================================================

@app.route(
    "/mercadolivre/connect"
)
def mercadolivre_connect():

    if not ML_CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado.",
            500
        )

    verifier = (
        make_code_verifier()
    )

    challenge = (
        make_code_challenge(
            verifier
        )
    )

    state = (
        secrets.token_urlsafe(
            32
        )
    )

    session[
        "oauth_state"
    ] = state

    session[
        "oauth_verifier"
    ] = verifier

    params = {

        "response_type":
            "code",

        "client_id":
            ML_CLIENT_ID,

        "redirect_uri":
            ML_REDIRECT_URI,

        "state":
            state,

        "code_challenge":
            challenge,

        "code_challenge_method":
            "S256",
    }

    return redirect(

        ML_AUTH
        + "?"
        + requests.models.PreparedRequest
        .prepare_url(
            "",
            params
        )[1:]
    )


# ============================================================
# CALLBACK
# ============================================================

@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return (
            f"Mercado Livre retornou erro: "
            f"{error}",
            400
        )

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    expected_state = session.get(
        "oauth_state"
    )

    verifier = session.get(
        "oauth_verifier"
    )

    if not code:

        return (
            "Código OAuth não recebido.",
            400
        )

    if (
        not state
        or state != expected_state
    ):

        return (
            "State OAuth inválido.",
            400
        )

    if not verifier:

        return (
            "Code verifier não encontrado.",
            400
        )

    payload = {

        "grant_type":
            "authorization_code",

        "client_id":
            ML_CLIENT_ID,

        "client_secret":
            ML_CLIENT_SECRET,

        "code":
            code,

        "redirect_uri":
            ML_REDIRECT_URI,

        "code_verifier":
            verifier,
    }

    try:

        response = requests.post(

            ML_TOKEN,

            data=payload,

            timeout=REQUEST_TIMEOUT
        )

    except Exception as e:

        return (
            f"Erro ao trocar token: {e}",
            500
        )

    if response.status_code != 200:

        return (

            "Erro ao trocar código por token:"
            "<br><br>"
            + response.text[:2000],

            500
        )

    data = response.json()

    access_token = data.get(
        "access_token"
    )

    refresh_token = data.get(
        "refresh_token"
    )

    if not access_token:

        return (
            "Access token não retornado.",
            500
        )

    user_id = None

    nickname = None

    try:

        me = requests.get(

            ML_API
            + "/users/me",

            headers={

                "Authorization":
                    f"Bearer {access_token}"
            },

            timeout=REQUEST_TIMEOUT
        )

        if me.status_code == 200:

            me_data = me.json()

            user_id = me_data.get(
                "id"
            )

            nickname = me_data.get(
                "nickname"
            )

    except Exception:

        pass

    save_tokens(

        access_token,

        refresh_token,

        data.get(
            "expires_in",
            21600
        ),

        user_id,

        nickname
    )

    session.pop(
        "oauth_state",
        None
    )

    session.pop(
        "oauth_verifier",
        None
    )

    return redirect(
        "/"
    )


# ============================================================
# DESCONECTAR
# ============================================================

@app.route(
    "/mercadolivre/disconnect"
)
def mercadolivre_disconnect():

    conn = db()

    conn.execute(
        "DELETE FROM oauth_tokens"
    )

    conn.commit()

    conn.close()

    return redirect(
        "/"
    )


# ============================================================
# START SCAN
# ============================================================

@app.route(
    "/api/scan/start",
    methods=["POST"]
)
def api_scan_start():

    access_token = (
        get_access_token()
    )

    refresh_token = (
        get_refresh_token()
    )

    if not access_token:

        return jsonify({

            "ok":
                False,

            "error":
                (
                    "Conecte sua conta "
                    "Mercado Livre primeiro."
                )
        }), 401

    job_id = uuid.uuid4().hex

    JOBS[
        job_id
    ] = {

        "status":
            "starting",

        "progress":
            0,

        "message":
            "Iniciando caça...",

        "created_at":
            time.time(),

        "updated_at":
            time.time(),
    }

    auth = {

        "access_token":
            access_token,

        "refresh_token":
            refresh_token,
    }

    thread = threading.Thread(

        target=run_scan,

        args=(
            job_id,
            auth
        ),

        daemon=True
    )

    thread.start()

    return jsonify({

        "ok":
            True,

        "job_id":
            job_id
    })


# ============================================================
# STATUS SCAN
# ============================================================

@app.route(
    "/api/scan/status/<job_id>"
)
def api_scan_status(
    job_id
):

    job = JOBS.get(
        job_id
    )

    if not job:

        return jsonify({

            "ok":
                False,

            "error":
                "Job não encontrado."
        }), 404

    return jsonify({

        "ok":
            True,

        **job
    })


# ============================================================
# OFERTAS
# ============================================================

@app.route(
    "/api/offers"
)
def api_offers():

    conn = db()

    rows = conn.execute("""

        SELECT *

        FROM offers

        ORDER BY

            coupon_discount DESC,

            effective_discount DESC,

            final_price ASC

    """).fetchall()

    conn.close()

    unique = {}

    for row in rows:

        offer = dict(
            row
        )

        pid = offer.get(
            "product_id"
        )

        if pid not in unique:

            unique[
                pid
            ] = offer

    offers = list(
        unique.values()
    )

    return jsonify({

        "ok":
            True,

        "offers":
            offers,

        "total":
            len(offers)
    })


# ============================================================
# CUPONS
# ============================================================

@app.route(
    "/api/coupons"
)
def api_coupons():

    coupons = load_coupons()

    return jsonify({

        "ok":
            True,

        "coupons":
            coupons,

        "total":
            len(coupons)
    })


# ============================================================
# GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/generate-ad",
    methods=["POST"]
)
def api_generate_ad():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    title = data.get(
        "title",
        "Oferta"
    )

    current_price = float(
        data.get(
            "current_price",
            0
        )
        or 0
    )

    coupon_code = data.get(
        "coupon_code"
    )

    coupon_discount = float(
        data.get(
            "coupon_discount",
            0
        )
        or 0
    )

    final_price = float(
        data.get(
            "final_price",
            current_price
        )
        or current_price
    )

    url = data.get(
        "url",
        ""
    )

    if coupon_code:

        text = (

            "🔥 OFERTA ENCONTRADA!\n\n"

            f"{title}\n\n"

            f"💰 Valor atual: "
            f"{money(current_price)}\n"

            f"🎟️ Cupom estimado: "
            f"{coupon_code}\n"

            f"💸 Economia estimada: "
            f"{money(coupon_discount)}\n"

            f"🔥 Por: "
            f"{money(final_price)}\n\n"

            f"🛒 Compre aqui:\n"
            f"{url}\n\n"

            "⚠️ Confira o cupom e o valor "
            "no checkout do Mercado Livre."
        )

    else:

        text = (

            "🔥 OFERTA!\n\n"

            f"{title}\n\n"

            f"💰 Por: "
            f"{money(final_price)}\n\n"

            f"🛒 Compre aqui:\n"
            f"{url}"
        )

    return jsonify({

        "ok":
            True,

        "text":
            text
    })


# ============================================================
# HTML
# ============================================================

HTML = r'''
<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    background: #f4f5f7;

    color: #111827;

    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Roboto,
        Arial,
        sans-serif;
}

.container {

    width: 100%;

    max-width: 900px;

    margin: auto;

    padding: 18px;
}

.header {

    background: white;

    border-radius: 18px;

    padding: 20px;

    margin-bottom: 15px;

    box-shadow:
        0 4px 18px
        rgba(0,0,0,.06);
}

.header h1 {

    margin:
        0 0 7px;

    font-size: 25px;
}

.header p {

    margin: 0;

    color: #6b7280;
}

.connection {

    margin-top: 14px;

    display: flex;

    align-items: center;

    justify-content: space-between;

    gap: 10px;

    flex-wrap: wrap;
}

.connected {

    color: #059669;

    font-weight: 800;
}

.btn {

    border: 0;

    border-radius: 12px;

    padding: 13px 17px;

    cursor: pointer;

    font-weight: 800;

    font-size: 14px;
}

.btn-primary {

    background: #3483fa;

    color: white;
}

.btn-danger {

    background: #ef4444;

    color: white;
}

.btn-secondary {

    background: #111827;

    color: white;
}

.btn:disabled {

    opacity: .55;

    cursor: not-allowed;
}

.stats {

    display: grid;

    grid-template-columns:
        repeat(4, 1fr);

    gap: 10px;

    margin-bottom: 15px;
}

.stat {

    background: white;

    border-radius: 15px;

    padding: 15px;

    box-shadow:
        0 4px 18px
        rgba(0,0,0,.05);
}

.stat-label {

    color: #6b7280;

    font-size: 12px;

    margin-bottom: 7px;
}

.stat-value {

    font-size: 21px;

    font-weight: 900;
}

.scan-box {

    background: white;

    border-radius: 18px;

    padding: 18px;

    margin-bottom: 15px;

    box-shadow:
        0 4px 18px
        rgba(0,0,0,.06);
}

.scan-title {

    font-weight: 900;

    margin-bottom: 8px;
}

.progress {

    width: 100%;

    height: 10px;

    background: #e5e7eb;

    border-radius: 999px;

    overflow: hidden;

    margin: 12px 0;
}

.progress-bar {

    height: 100%;

    width: 0%;

    background: #3483fa;

    transition:
        width .25s;
}

.status {

    color: #6b7280;

    font-size: 14px;
}

.offer {

    background: white;

    border-radius: 18px;

    padding: 18px;

    margin-bottom: 13px;

    box-shadow:
        0 4px 18px
        rgba(0,0,0,.06);
}

.offer-title {

    font-size: 17px;

    font-weight: 850;

    line-height: 1.35;

    margin-bottom: 13px;
}

.price-row {

    display: flex;

    align-items: center;

    justify-content: space-between;

    gap: 15px;

    flex-wrap: wrap;
}

.price-current {

    font-size: 15px;

    color: #6b7280;
}

.price-final {

    font-size: 25px;

    font-weight: 950;

    color: #111827;
}

.coupon {

    margin-top: 13px;

    background: #fff7ed;

    border:
        1px solid
        #fed7aa;

    border-radius: 12px;

    padding: 12px;
}

.coupon-code {

    font-weight: 950;

    color: #c2410c;

    font-size: 16px;
}

.coupon-info {

    color: #9a3412;

    margin-top: 4px;

    font-size: 13px;

    line-height: 1.5;
}

.coupon-badge {

    display: inline-block;

    margin-top: 7px;

    padding: 4px 8px;

    border-radius: 999px;

    background: #ffedd5;

    color: #c2410c;

    font-size: 11px;

    font-weight: 900;
}

.no-coupon {

    margin-top: 13px;

    background: #f9fafb;

    border-radius: 12px;

    padding: 12px;

    color: #6b7280;
}

.actions {

    display: flex;

    gap: 8px;

    flex-wrap: wrap;

    margin-top: 14px;
}

.actions a {

    text-decoration: none;
}

.affiliate {

    width: 100%;

    padding: 11px;

    border:
        1px solid
        #d1d5db;

    border-radius: 10px;

    margin-top: 12px;
}

.ad-box {

    margin-top: 12px;

    display: none;
}

.ad-box textarea {

    width: 100%;

    min-height: 160px;

    border:
        1px solid
        #d1d5db;

    border-radius: 12px;

    padding: 12px;

    resize: vertical;
}

.footer-note {

    margin: 18px 0;

    color: #6b7280;

    font-size: 12px;

    line-height: 1.5;
}

.empty {

    background: white;

    padding: 30px;

    border-radius: 18px;

    text-align: center;

    color: #6b7280;
}

@media(max-width:700px) {

    .stats {

        grid-template-columns:
            repeat(2, 1fr);
    }

    .container {

        padding: 12px;
    }

    .header h1 {

        font-size: 22px;
    }
}

</style>

</head>

<body>

<div class="container">

<div class="header">

<h1>
🔥 Caçador de Ofertas
</h1>

<p>
Produtos acima de R$ 69,90 com
melhor combinação de produto + preço + cupom.
</p>

<div class="connection">

{% if connected %}

<div class="connected">

🟢 Mercado Livre conectado

{% if nickname %}
— {{ nickname }}
{% endif %}

</div>

<a
    class="btn btn-danger"
    href="/mercadolivre/disconnect"
>
Desconectar
</a>

{% else %}

<a
    class="btn btn-primary"
    href="/mercadolivre/connect"
>
🔗 Conectar Mercado Livre
</a>

{% endif %}

</div>

</div>


<div class="stats">

<div class="stat">

<div class="stat-label">
Ofertas
</div>

<div
    class="stat-value"
    id="statOffers"
>
0
</div>

</div>


<div class="stat">

<div class="stat-label">
Cupom estimado
</div>

<div
    class="stat-value"
    id="statCoupons"
>
0
</div>

</div>


<div class="stat">

<div class="stat-label">
Valor
</div>

<div
    class="stat-value"
    id="statCurrent"
>
R$ 0,00
</div>

</div>


<div class="stat">

<div class="stat-label">
Valor com o desconto
</div>

<div
    class="stat-value"
    id="statFinal"
>
R$ 0,00
</div>

</div>

</div>


{% if connected %}

<div class="scan-box">

<div class="scan-title">

🔎 Caçar ofertas

</div>

<button
    class="btn btn-primary"
    id="scanButton"
    onclick="startScan()"
>
🚀 CAÇAR OFERTAS
</button>

<div class="progress">

<div
    class="progress-bar"
    id="progressBar"
></div>

</div>

<div
    class="status"
    id="scanStatus"
>
Pronto para começar.
</div>

</div>

{% endif %}


<div id="offers"></div>


<div class="footer-note">

🟡 <strong>Cupom estimado:</strong>
o sistema encontrou uma regra pública de cupom
compatível com o valor do produto. Isso não significa
que o Mercado Livre confirmou a elegibilidade daquele
item/conta.

<br><br>

O Mercado Livre informa que os cupons dependem dos
itens aplicáveis e que o desconto é aplicado no checkout.
Confira sempre o código e o preço antes de publicar.

</div>

</div>


<script>

let currentJob = null;


function money(value) {

    value = Number(
        value || 0
    );

    return value.toLocaleString(
        "pt-BR",
        {
            style:
                "currency",

            currency:
                "BRL"
        }
    );
}


async function startScan() {

    const button =
        document.getElementById(
            "scanButton"
        );

    if (!button) {
        return;
    }

    button.disabled = true;

    button.innerText =
        "🔎 CAÇANDO...";

    document.getElementById(
        "scanStatus"
    ).innerText =
        "Iniciando caça...";

    document.getElementById(
        "progressBar"
    ).style.width =
        "0%";

    try {

        const response =
            await fetch(
                "/api/scan/start",
                {
                    method:
                        "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        "{}"
                }
            );

        const data =
            await response.json();

        if (!data.ok) {

            document.getElementById(
                "scanStatus"
            ).innerText =
                data.error
                || "Erro ao iniciar.";

            button.disabled =
                false;

            button.innerText =
                "🚀 CAÇAR OFERTAS";

            return;
        }

        currentJob =
            data.job_id;

        pollScan();

    } catch(error) {

        document.getElementById(
            "scanStatus"
        ).innerText =
            "Erro: "
            + error;

        button.disabled =
            false;

        button.innerText =
            "🚀 CAÇAR OFERTAS";
    }
}


async function pollScan() {

    if (!currentJob) {
        return;
    }

    try {

        const response =
            await fetch(
                "/api/scan/status/"
                + currentJob
            );

        const data =
            await response.json();

        if (!data.ok) {
            return;
        }

        document.getElementById(
            "progressBar"
        ).style.width =
            (
                data.progress
                || 0
            )
            + "%";

        document.getElementById(
            "scanStatus"
        ).innerText =
            data.message
            || "";

        if (
            data.status
            === "done"
        ) {

            await loadOffers();

            const button =
                document.getElementById(
                    "scanButton"
                );

            if (button) {

                button.disabled =
                    false;

                button.innerText =
                    "🚀 CAÇAR OFERTAS";
            }

            return;
        }

        if (
            data.status
            === "error"
        ) {

            const button =
                document.getElementById(
                    "scanButton"
                );

            if (button) {

                button.disabled =
                    false;

                button.innerText =
                    "🚀 CAÇAR OFERTAS";
            }

            return;
        }

        setTimeout(
            pollScan,
            900
        );

    } catch(error) {

        setTimeout(
            pollScan,
            1500
        );
    }
}


async function loadOffers() {

    try {

        const response =
            await fetch(
                "/api/offers"
            );

        const data =
            await response.json();

        if (!data.ok) {
            return;
        }

        renderOffers(
            data.offers
            || []
        );

    } catch(error) {

        console.error(
            error
        );
    }
}


function renderOffers(
    offers
) {

    const container =
        document.getElementById(
            "offers"
        );

    container.innerHTML =
        "";

    let couponCount =
        0;

    let currentTotal =
        0;

    let finalTotal =
        0;

    const seen =
        new Set();


    offers.forEach(
        offer => {

            if (
                seen.has(
                    offer.product_id
                )
            ) {
                return;
            }

            seen.add(
                offer.product_id
            );

            currentTotal +=
                Number(
                    offer.current_price
                    || 0
                );

            finalTotal +=
                Number(
                    offer.final_price
                    || 0
                );

            if (
                offer.coupon_code
            ) {

                couponCount++;
            }

            const card =
                document.createElement(
                    "div"
                );

            card.className =
                "offer";

            const title =
                escapeHtml(
                    offer.product_title
                    || "Produto"
                );


            let couponHtml = "";


            if (
                offer.coupon_code
            ) {

                couponHtml = `

<div class="coupon">

<div class="coupon-code">

🎟️

${escapeHtml(
    offer.coupon_code
)}

</div>

<div class="coupon-info">

Economia estimada:

<strong>

${money(
    offer.coupon_discount
)}

</strong>

—

${Number(
    offer.effective_discount
    || 0
).toFixed(2)}% de economia efetiva

</div>

<div class="coupon-badge">

🟡 CUPOM ESTIMADO

</div>

</div>

`;

            } else {

                couponHtml = `

<div class="no-coupon">

Nenhum cupom estimado
para este produto.

</div>

`;
            }


            card.innerHTML = `

<div class="offer-title">

${title}

</div>


<div class="price-row">

<div>

<div class="price-current">
Valor atual
</div>

<strong>

${money(
    offer.current_price
)}

</strong>

</div>


<div>

<div class="price-current">
Valor com o desconto
</div>

<div class="price-final">

${money(
    offer.final_price
)}

</div>

</div>

</div>


${couponHtml}


<div class="actions">

<a
    class="btn btn-primary"
    href="${offer.product_url}"
    target="_blank"
>
🛒 Abrir produto
</a>


<button
    class="btn btn-secondary"
    onclick='generateAd(
        ${JSON.stringify(
            offer
        )}
    )'
>
📢 Gerar anúncio
</button>

</div>


<input
    class="affiliate"
    placeholder="Cole aqui seu link de afiliado"
    id="affiliate-${offer.product_id}"
>


<div
    class="ad-box"
    id="ad-${offer.product_id}"
>

<textarea
    id="adtext-${offer.product_id}"
    readonly
></textarea>


<button
    class="btn btn-secondary"
    onclick="copyAd(
        '${offer.product_id}'
    )"
    style="margin-top:8px;"
>
📋 Copiar anúncio
</button>

</div>

`;

            container.appendChild(
                card
            );
        }
    );


    document.getElementById(
        "statOffers"
    ).innerText =
        seen.size;


    document.getElementById(
        "statCoupons"
    ).innerText =
        couponCount;


    document.getElementById(
        "statCurrent"
    ).innerText =
        money(
            currentTotal
        );


    document.getElementById(
        "statFinal"
    ).innerText =
        money(
            finalTotal
        );


    if (
        !offers.length
    ) {

        container.innerHTML = `

<div class="empty">

Nenhuma oferta encontrada.

Clique em

<strong>
CAÇAR OFERTAS
</strong>

para tentar novamente.

</div>

`;
    }
}


async function generateAd(
    offer
) {

    const affiliate =
        document.getElementById(
            "affiliate-"
            + offer.product_id
        );

    const affiliateUrl =
        affiliate
        ? affiliate.value.trim()
        : "";


    const url =
        affiliateUrl
        || offer.product_url;


    try {

        const response =
            await fetch(
                "/api/generate-ad",
                {
                    method:
                        "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify({

                            title:
                                offer.product_title,

                            current_price:
                                offer.current_price,

                            coupon_code:
                                offer.coupon_code,

                            coupon_discount:
                                offer.coupon_discount,

                            final_price:
                                offer.final_price,

                            url:
                                url
                        })
                }
            );


        const data =
            await response.json();


        if (!data.ok) {
            return;
        }


        const box =
            document.getElementById(
                "ad-"
                + offer.product_id
            );


        const textarea =
            document.getElementById(
                "adtext-"
                + offer.product_id
            );


        textarea.value =
            data.text;


        box.style.display =
            "block";


    } catch(error) {

        alert(
            "Erro ao gerar anúncio."
        );
    }
}


async function copyAd(
    productId
) {

    const textarea =
        document.getElementById(
            "adtext-"
            + productId
        );


    if (!textarea) {
        return;
    }


    try {

        await navigator.clipboard.writeText(
            textarea.value
        );

        alert(
            "Anúncio copiado!"
        );

    } catch(error) {

        textarea.select();

        document.execCommand(
            "copy"
        );

        alert(
            "Anúncio copiado!"
        );
    }
}


function escapeHtml(
    value
) {

    return String(
        value || ""
    )

    .replace(
        /&/g,
        "&amp;"
    )

    .replace(
        /</g,
        "&lt;"
    )

    .replace(
        />/g,
        "&gt;"
    )

    .replace(
        /"/g,
        "&quot;"
    )

    .replace(
        /'/g,
        "&#039;"
    );
}


loadOffers();

</script>

</body>

</html>
'''


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )