import os
import re
import time
import uuid
import sqlite3
import hashlib
import secrets
import base64
import html as html_lib
from urllib.parse import urlencode

import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string


# ============================================================
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "cacador-ofertas-secret-key"
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

MAX_ITEMS_PER_PRODUCT = 5

PRODUCT_SEARCH_LIMIT = 50


# ============================================================
# TERMOS DE BUSCA
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
# CUPONS OFICIAIS DE FALLBACK
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
            id INTEGER PRIMARY KEY CHECK(id = 1),
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
            image_url TEXT,
            item_id TEXT,
            seller_id TEXT,
            seller_name TEXT,
            category TEXT,
            current_price REAL,
            original_price REAL,
            coupon_code TEXT,
            coupon_discount REAL,
            final_price REAL,
            effective_discount REAL,
            coupon_status TEXT,
            coupon_source TEXT,
            free_shipping INTEGER DEFAULT 0,
            official_store INTEGER DEFAULT 0,
            buy_box INTEGER DEFAULT 0,
            product_url TEXT,
            created_at REAL
        )
    """)

    # Migração caso o banco antigo não tenha essas colunas
    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(offers)"
        ).fetchall()
    }

    new_columns = {
        "image_url": "TEXT",
        "seller_name": "TEXT",
        "original_price": "REAL",
        "coupon_status": "TEXT",
        "coupon_source": "TEXT",
        "official_store": "INTEGER DEFAULT 0",
    }

    for column, definition in new_columns.items():

        if column not in columns:

            try:
                conn.execute(
                    f"ALTER TABLE offers ADD COLUMN {column} {definition}"
                )
            except Exception:
                pass

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILITÁRIOS
# ============================================================

def money(value):

    try:
        value = float(value or 0)
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

    value = html_lib.unescape(
        str(value)
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


def parse_money(value):

    if value is None:
        return None

    value = str(value)

    value = value.replace(
        "R$",
        ""
    )

    value = value.replace(
        " ",
        ""
    )

    value = re.sub(
        r"[^\d,.\-]",
        "",
        value
    )

    if not value:
        return None

    if "," in value:

        value = value.replace(
            ".",
            ""
        )

        value = value.replace(
            ",",
            "."
        )

    try:
        return float(value)

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

    old = get_token_row()

    if refresh_token is None and old:
        refresh_token = old["refresh_token"]

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
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at,
            user_id = excluded.user_id,
            nickname = excluded.nickname,
            updated_at = excluded.updated_at
    """, (
        1,
        access_token,
        refresh_token,
        time.time() + max(
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

    return row["access_token"]


def get_refresh_token():

    row = get_token_row()

    if not row:
        return None

    return row["refresh_token"]


def make_code_verifier():

    return secrets.token_urlsafe(
        64
    )


def make_code_challenge(
    verifier
):

    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).rstrip(
        b"="
    ).decode(
        "utf-8"
    )


# ============================================================
# REFRESH TOKEN
# ============================================================

def refresh_token_for_worker(
    refresh_token
):

    if not refresh_token:
        return None

    if not ML_CLIENT_ID:
        return None

    payload = {
        "grant_type": "refresh_token",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    try:

        response = requests.post(
            ML_TOKEN,
            data=payload,
            timeout=REQUEST_TIMEOUT
        )

    except Exception as e:

        print(
            "[REFRESH ERRO]",
            e
        )

        return None

    if response.status_code != 200:

        print(
            "[REFRESH HTTP]",
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
# MERCADO LIVRE REQUEST
# ============================================================

def ml_get_worker(
    path,
    auth,
    params=None
):

    access_token = auth.get(
        "access_token"
    )

    refresh_token = auth.get(
        "refresh_token"
    )

    headers = {
        "Accept": "application/json",
        "User-Agent": "CacadorDeOfertas/3.0",
    }

    if access_token:

        headers["Authorization"] = (
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
            "[ML GET ERRO]",
            e
        )

        return None, {}

    if (
        response.status_code == 401
        and refresh_token
    ):

        new_token = refresh_token_for_worker(
            refresh_token
        )

        if new_token:

            auth["access_token"] = new_token

            headers["Authorization"] = (
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
                    "[ML RETRY ERRO]",
                    e
                )

                return None, {}

    try:
        data = response.json()

    except Exception:
        data = {}

    return response.status_code, data


# ============================================================
# CUPONS
# ============================================================

def save_coupons(
    coupons
):

    conn = db()

    for coupon in coupons:

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
                type = excluded.type,
                value = excluded.value,
                min_purchase = excluded.min_purchase,
                max_discount = excluded.max_discount,
                source = excluded.source,
                updated_at = excluded.updated_at
        """, (
            coupon["code"],
            coupon.get("type"),
            coupon.get("value"),
            coupon.get("min_purchase"),
            coupon.get("max_discount"),
            coupon.get(
                "source",
                "scraped"
            ),
            time.time()
        ))

    conn.commit()
    conn.close()


def parse_coupon_page(
    raw_html
):

    text = html_lib.unescape(
        raw_html or ""
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

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    matches = list(
        re.finditer(
            r"\bCupom\s+([A-Z0-9][A-Z0-9_-]{3,29})\b",
            text,
            re.I
        )
    )

    coupons = []

    for i, match in enumerate(matches):

        code = match.group(
            1
        ).upper()

        start = match.start()

        if i + 1 < len(matches):

            end = matches[
                i + 1
            ].start()

        else:

            end = min(
                len(text),
                start + 1800
            )

        block = text[
            start:end
        ]

        percent = None

        patterns = [

            r"até\s+(\d+(?:[.,]\d+)?)\s*%\s*de\s*desconto",

            r"(\d+(?:[.,]\d+)?)\s*%\s*de\s*desconto",

            r"desconto\s+de\s+(\d+(?:[.,]\d+)?)\s*%",
        ]

        for pattern in patterns:

            m = re.search(
                pattern,
                block,
                re.I
            )

            if m:

                try:

                    percent = float(
                        m.group(1).replace(
                            ",",
                            "."
                        )
                    )

                    break

                except Exception:
                    pass

        fixed = None

        patterns_fixed = [

            r"desconto\s+de\s+até\s+R\$\s*([\d\.,]+)",

            r"até\s+R\$\s*([\d\.,]+)\s*de\s*desconto",

            r"R\$\s*([\d\.,]+)\s*OFF",
        ]

        for pattern in patterns_fixed:

            m = re.search(
                pattern,
                block,
                re.I
            )

            if m:

                fixed = parse_money(
                    m.group(1)
                )

                if fixed is not None:
                    break

        minimum = 0

        patterns_min = [

            r"compra\s+a\s+partir\s+de\s+R\$\s*([\d\.,]+)",

            r"pedido\s+a\s+partir\s+de\s+R\$\s*([\d\.,]+)",

            r"mínimo\s+de\s+R\$\s*([\d\.,]+)",
        ]

        for pattern in patterns_min:

            m = re.search(
                pattern,
                block,
                re.I
            )

            if m:

                minimum = (
                    parse_money(
                        m.group(1)
                    )
                    or 0
                )

                break

        maximum = None

        patterns_max = [

            r"máximo\s+de\s+R\$\s*([\d\.,]+)",

            r"desconto\s+máximo\s+de\s+R\$\s*([\d\.,]+)",
        ]

        for pattern in patterns_max:

            m = re.search(
                pattern,
                block,
                re.I
            )

            if m:

                maximum = parse_money(
                    m.group(1)
                )

                if maximum is not None:
                    break

        if percent is None and fixed is None:
            continue

        coupons.append({
            "code": code,
            "type": (
                "percent"
                if percent is not None
                else "fixed"
            ),
            "value": (
                percent
                if percent is not None
                else fixed
            ),
            "min_purchase": minimum,
            "max_discount": maximum,
            "source": "scraped",
        })

    unique = {}

    for coupon in coupons:

        unique[
            coupon["code"]
        ] = coupon

    return list(
        unique.values()
    )


def sync_coupons():

    url = (
        "https://www.mercadolivre.com.br/l/promocoes"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9",
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

            coupons = parse_coupon_page(
                response.text
            )

    except Exception as e:

        print(
            "[CUPONS ERRO]",
            e
        )

    if not coupons:

        print(
            "[CUPONS] Usando fallback."
        )

        coupons = [
            dict(x)
            for x in OFFICIAL_COUPON_FALLBACK
        ]

    save_coupons(
        coupons
    )

    print(
        "[CUPONS TOTAL]",
        len(coupons)
    )

    return coupons


# ============================================================
# CALCULAR CUPOM
# ============================================================

def calculate_coupon(
    coupon,
    price
):

    try:
        price = float(price)
    except Exception:
        return None

    if price < MIN_PRODUCT_PRICE:
        return None

    minimum = float(
        coupon.get(
            "min_purchase"
        )
        or 0
    )

    if price < minimum:
        return None

    value = float(
        coupon.get(
            "value"
        )
        or 0
    )

    if coupon.get(
        "type"
    ) == "percent":

        discount = (
            price * value / 100
        )

    else:

        discount = value

    maximum = coupon.get(
        "max_discount"
    )

    if maximum is not None:

        try:

            discount = min(
                discount,
                float(maximum)
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

    return {
        "code": coupon["code"],
        "discount": round(
            discount,
            2
        ),
        "final_price": round(
            final_price,
            2
        ),
        "effective_discount": round(
            discount / price * 100,
            2
        ),
        "source": coupon.get(
            "source"
        ),
    }


def best_coupon(
    price,
    coupons
):

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
            -x["discount"],
            -x["effective_discount"],
            x["final_price"],
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
        auth,
        params={
            "site_id": SITE_ID,
            "q": query,
            "status": "active",
            "limit": PRODUCT_SEARCH_LIMIT,
            "offset": 0,
        }
    )

    if status != 200:

        print(
            "[PRODUCT SEARCH ERRO]",
            query,
            status
        )

        return []

    results = data.get(
        "results",
        []
    )

    print(
        "[BUSCA]",
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
        auth
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
        auth,
        params={
            "limit": MAX_ITEMS_PER_PRODUCT,
            "offset": 0,
        }
    )

    if status == 200:

        return data.get(
            "results",
            []
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
        auth,
        params={
            "context":
                "channel_marketplace"
        }
    )

    if status != 200:
        return None, None

    if not isinstance(
        data,
        dict
    ):
        return None, None

    amount = data.get(
        "amount"
    )

    if amount is None:

        amount = data.get(
            "price"
        )

    regular = data.get(
        "regular_amount"
    )

    if regular is None:

        regular = data.get(
            "original_amount"
        )

    try:

        amount = float(
            amount
        )

    except Exception:

        amount = None

    try:

        regular = float(
            regular
        ) if regular is not None else None

    except Exception:

        regular = None

    return amount, regular


def get_prices(
    item_id,
    auth
):

    status, data = ml_get_worker(
        f"/items/{item_id}/prices",
        auth
    )

    if status != 200:
        return None, None

    if not isinstance(
        data,
        dict
    ):
        return None, None

    prices = data.get(
        "prices",
        []
    )

    if not isinstance(
        prices,
        list
    ):
        return None, None

    amounts = []

    regulars = []

    for p in prices:

        if not isinstance(
            p,
            dict
        ):
            continue

        amount = p.get(
            "amount"
        )

        regular = (
            p.get("regular_amount")
            or p.get("original_amount")
        )

        try:

            if amount is not None:
                amounts.append(
                    float(amount)
                )

        except Exception:
            pass

        try:

            if regular is not None:
                regulars.append(
                    float(regular)
                )

        except Exception:
            pass

    if not amounts:
        return None, None

    current = min(
        x for x in amounts
        if x > 0
    )

    original = None

    if regulars:

        valid = [
            x for x in regulars
            if x > current
        ]

        if valid:
            original = max(
                valid
            )

    return current, original


def get_current_price(
    item,
    auth
):

    item_id = item.get(
        "item_id"
    )

    if not item_id:
        return None, None

    # PRIMEIRO: SALE PRICE
    current, original = get_sale_price(
        item_id,
        auth
    )

    if current is not None:

        if original is None:

            try:

                item_original = float(
                    item.get(
                        "original_price"
                    )
                    or 0
                )

                if item_original > current:
                    original = item_original

            except Exception:
                pass

        return current, original

    # SEGUNDO: PRICES
    current, original = get_prices(
        item_id,
        auth
    )

    if current is not None:
        return current, original

    # TERCEIRO: ITEM
    try:

        current = float(
            item.get(
                "price"
            )
        )

        original = None

        try:

            original = float(
                item.get(
                    "original_price"
                )
                or 0
            )

        except Exception:
            pass

        if current > 0:

            if (
                original is not None
                and original <= current
            ):
                original = None

            return current, original

    except Exception:
        pass

    return None, None


# ============================================================
# IMAGEM
# ============================================================

def get_product_image(
    product,
    item
):

    pictures = product.get(
        "pictures"
    )

    if isinstance(
        pictures,
        list
    ):

        for picture in pictures:

            if not isinstance(
                picture,
                dict
            ):
                continue

            for key in (
                "url",
                "secure_url",
                "source"
            ):

                url = picture.get(
                    key
                )

                if url:
                    return url

    thumbnail = item.get(
        "thumbnail"
    )

    if thumbnail:

        if thumbnail.startswith(
            "http:"
        ):
            thumbnail = thumbnail.replace(
                "http:",
                "https:",
                1
            )

        return thumbnail

    return ""


# ============================================================
# LOJA
# ============================================================

def get_seller_name(
    item
):

    seller_name = (
        item.get("seller_nickname")
        or item.get("seller_name")
        or item.get("seller_nick")
    )

    if seller_name:
        return clean_text(
            seller_name
        )

    return ""


def is_official_store(
    item
):

    return bool(
        item.get(
            "official_store_id"
        )
    )


def free_shipping(
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

    try:

        return float(
            shipping.get(
                "cost"
            )
            or 0
        ) <= 0

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

    current_price, original_price = (
        get_current_price(
            item,
            auth
        )
    )

    if current_price is None:
        return None

    try:

        current_price = float(
            current_price
        )

    except Exception:

        return None

    if current_price < MIN_PRODUCT_PRICE:
        return None

    coupon = best_coupon(
        current_price,
        coupons
    )

    if coupon:

        final_price = coupon[
            "final_price"
        ]

        coupon_discount = coupon[
            "discount"
        ]

        effective_discount = coupon[
            "effective_discount"
        ]

        coupon_code = coupon[
            "code"
        ]

        coupon_source = coupon.get(
            "source"
        )

        coupon_status = "estimado"

    else:

        final_price = current_price

        coupon_discount = 0

        effective_discount = 0

        coupon_code = None

        coupon_source = None

        coupon_status = None

    if (
        original_price is not None
        and original_price <= current_price
    ):
        original_price = None

    seller_id = item.get(
        "seller_id"
    )

    seller_name = get_seller_name(
        item
    )

    title = clean_text(
        product.get("name")
        or product.get("title")
        or item.get("title")
        or "Produto"
    )

    image_url = get_product_image(
        product,
        item
    )

    product_id = (
        product.get("id")
        or item.get("catalog_product_id")
    )

    return {

        "product_id":
            product_id,

        "product_title":
            title,

        "image_url":
            image_url,

        "item_id":
            item_id,

        "seller_id":
            seller_id,

        "seller_name":
            seller_name,

        "category":
            category,

        "current_price":
            round(
                current_price,
                2
            ),

        "original_price":
            (
                round(
                    original_price,
                    2
                )
                if original_price
                else None
            ),

        "coupon_code":
            coupon_code,

        "coupon_discount":
            round(
                coupon_discount,
                2
            ),

        "final_price":
            round(
                final_price,
                2
            ),

        "effective_discount":
            round(
                effective_discount,
                2
            ),

        "coupon_status":
            coupon_status,

        "coupon_source":
            coupon_source,

        "free_shipping":
            free_shipping(
                item
            ),

        "official_store":
            is_official_store(
                item
            ),

        "buy_box":
            bool(
                buy_box
            ),

        "product_url":
            (
                "https://www.mercadolivre.com.br/"
                f"p/{product_id}"
            ),
    }


# ============================================================
# ESCOLHER MELHOR
# ============================================================

def choose_best(
    candidates
):

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (

            # Primeiro:
            # menor preço final
            x["final_price"],

            # Depois:
            # maior economia
            -x["coupon_discount"],

            # Depois:
            # maior percentual
            -x["effective_discount"],

            # Frete grátis
            not x["free_shipping"],

            # Loja oficial
            not x["official_store"],

            # Buy Box
            not x["buy_box"],

            # Preço atual
            x["current_price"],
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

    # Buy Box como fallback
    if not items:

        winner = product.get(
            "buy_box_winner"
        )

        if isinstance(
            winner,
            dict
        ):

            if winner.get(
                "item_id"
            ):

                items = [
                    winner
                ]

    # Filhos do produto
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

    buy_box_item = None

    if isinstance(
        winner,
        dict
    ):

        buy_box_item = winner.get(
            "item_id"
        )

    candidates = []

    for item in items[
        :MAX_ITEMS_PER_PRODUCT
    ]:

        buy_box = (
            item.get(
                "item_id"
            )
            == buy_box_item
        )

        candidate = build_candidate(
            product,
            item,
            category,
            coupons,
            auth,
            buy_box
        )

        if candidate:
            candidates.append(
                candidate
            )

    return choose_best(
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
        job["status"] = status

    if progress is not None:
        job["progress"] = progress

    if message is not None:
        job["message"] = message

    job["updated_at"] = time.time()


# ============================================================
# CAÇA
# ============================================================

def run_scan(
    job_id,
    auth
):

    try:

        update_job(
            job_id,
            "running",
            2,
            "Buscando cupons..."
        )

        coupons = sync_coupons()

        update_job(
            job_id,
            message=(
                f"{len(coupons)} cupons encontrados. "
                "Buscando produtos..."
            )
        )

        product_map = {}

        searches = []

        for category, queries in SEARCH_GROUPS.items():

            for query in queries:

                searches.append(
                    (
                        category,
                        query
                    )
                )

        total_searches = len(
            searches
        )

        for index, (
            category,
            query
        ) in enumerate(
            searches,
            start=1
        ):

            if len(product_map) >= MAX_PRODUCTS_SCAN:
                break

            results = product_search(
                query,
                auth
            )

            for result in results:

                if isinstance(
                    result,
                    str
                ):

                    product_id = result

                else:

                    product_id = (
                        result.get("id")
                        or result.get("product_id")
                    )

                if not product_id:
                    continue

                if product_id not in product_map:

                    product_map[
                        product_id
                    ] = category

                if len(product_map) >= MAX_PRODUCTS_SCAN:
                    break

            progress = int(
                (
                    index
                    / max(
                        total_searches,
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
            "[SCAN] TOTAL PRODUTOS:",
            len(product_map)
        )

        offers = {}

        products = list(
            product_map.items()
        )

        total = len(
            products
        )

        for index, (
            product_id,
            category
        ) in enumerate(
            products,
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

                    existing = offers.get(
                        product_id
                    )

                    if existing:

                        candidate = choose_best(
                            [
                                existing,
                                candidate
                            ]
                        )

                    offers[
                        product_id
                    ] = candidate

            except Exception as e:

                print(
                    "[PRODUTO ERRO]",
                    product_id,
                    repr(e)
                )

            progress = (
                25
                + int(
                    (
                        index
                        / max(
                            total,
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
                    f"Montando ofertas "
                    f"{index}/{total}..."
                )
            )

        final_offers = list(
            offers.values()
        )

        # Melhor economia primeiro
        final_offers.sort(
            key=lambda x: (
                -x["coupon_discount"],
                -x["effective_discount"],
                x["final_price"],
                not x["free_shipping"],
            )
        )

        conn = db()

        conn.execute(
            "DELETE FROM offers"
        )

        for offer in final_offers:

            conn.execute("""
                INSERT INTO offers
                (
                    product_id,
                    product_title,
                    image_url,
                    item_id,
                    seller_id,
                    seller_name,
                    category,
                    current_price,
                    original_price,
                    coupon_code,
                    coupon_discount,
                    final_price,
                    effective_discount,
                    coupon_status,
                    coupon_source,
                    free_shipping,
                    official_store,
                    buy_box,
                    product_url,
                    created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                offer["product_id"],
                offer["product_title"],
                offer["image_url"],
                offer["item_id"],
                offer["seller_id"],
                offer["seller_name"],
                offer["category"],
                offer["current_price"],
                offer["original_price"],
                offer["coupon_code"],
                offer["coupon_discount"],
                offer["final_price"],
                offer["effective_discount"],
                offer["coupon_status"],
                offer["coupon_source"],
                int(
                    offer["free_shipping"]
                ),
                int(
                    offer["official_store"]
                ),
                int(
                    offer["buy_box"]
                ),
                offer["product_url"],
                time.time()
            ))

        conn.commit()
        conn.close()

        update_job(
            job_id,
            "done",
            100,
            (
                f"Caça finalizada: "
                f"{len(final_offers)} "
                "produtos únicos."
            )
        )

        print(
            "[SCAN FINALIZADO]",
            len(final_offers)
        )

    except Exception as e:

        print(
            "[SCAN FATAL]",
            repr(e)
        )

        update_job(
            job_id,
            "error",
            100,
            f"Erro durante a caça: {e}"
        )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    row = get_token_row()

    connected = bool(
        row and row["access_token"]
    )

    nickname = (
        row["nickname"]
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
        "status": "ok",
        "app": "Cacador de Ofertas",
        "mercadolivre": bool(
            row and row["access_token"]
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

    verifier = make_code_verifier()

    challenge = make_code_challenge(
        verifier
    )

    state = secrets.token_urlsafe(
        32
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

    auth_url = (
        ML_AUTH
        + "?"
        + urlencode(params)
    )

    return redirect(
        auth_url
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
            f"Mercado Livre retornou erro: {error}",
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
            ML_API + "/users/me",
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

    return redirect("/")


# ============================================================
# DESCONECTAR
# ============================================================

@app.route(
    "/mercadolivre/disconnect"
)
def disconnect():

    conn = db()

    conn.execute(
        "DELETE FROM oauth_tokens"
    )

    conn.commit()
    conn.close()

    return redirect("/")


# ============================================================
# INICIAR CAÇA
# ============================================================

@app.route(
    "/api/scan/start",
    methods=["POST"]
)
def start_scan():

    access_token = get_access_token()

    refresh_token = get_refresh_token()

    if not access_token:

        return jsonify({
            "ok": False,
            "error":
                "Conecte o Mercado Livre primeiro."
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
            "Iniciando...",

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

    threading.Thread(
        target=run_scan,
        args=(
            job_id,
            auth
        ),
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "job_id": job_id
    })


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/api/scan/status/<job_id>"
)
def scan_status(
    job_id
):

    job = JOBS.get(
        job_id
    )

    if not job:

        return jsonify({
            "ok": False,
            "error":
                "Job não encontrado."
        }), 404

    return jsonify({
        "ok": True,
        **job
    })


# ============================================================
# OFERTAS
# ============================================================

@app.route(
    "/api/offers"
)
def offers_api():

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

    offers = []

    seen = set()

    for row in rows:

        data = dict(row)

        product_id = data.get(
            "product_id"
        )

        if product_id in seen:
            continue

        seen.add(
            product_id
        )

        offers.append(
            data
        )

    return jsonify({
        "ok": True,
        "offers": offers,
        "total": len(offers)
    })


# ============================================================
# GERAR TEXTO DA OFERTA
# ============================================================

@app.route(
    "/api/generate-ad",
    methods=["POST"]
)
def generate_ad():

    data = request.get_json(
        silent=True
    ) or {}

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

    original_price = data.get(
        "original_price"
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

    affiliate_url = (
        data.get(
            "affiliate_url"
        )
        or data.get(
            "url"
        )
        or ""
    ).strip()

    official_store = bool(
        data.get(
            "official_store"
        )
    )

    seller_name = (
        data.get(
            "seller_name"
        )
        or ""
    ).strip()

    # --------------------------------
    # TITULO
    # --------------------------------

    lines = []

    lines.append(
        "🔥 OFERTA ENCONTRADA!"
    )

    lines.append("")

    lines.append(
        str(title)
    )

    lines.append("")

    # --------------------------------
    # DE / POR
    # --------------------------------

    if original_price:

        try:
            original_price = float(
                original_price
            )
        except Exception:
            original_price = None

    if (
        original_price
        and original_price > current_price
    ):

        lines.append(
            f"De {money(original_price)}"
        )

    if coupon_code:

        lines.append(
            f"Por {money(final_price)} 🔥"
        )

    else:

        lines.append(
            f"Por {money(current_price)} 🔥"
        )

    # --------------------------------
    # CUPOM
    # --------------------------------

    if coupon_code:

        lines.append("")

        lines.append(
            f"🎟️ Cupom: {coupon_code}"
        )

    # --------------------------------
    # LINK
    # --------------------------------

    if affiliate_url:

        lines.append("")

        lines.append(
            "👉 Pegar promoção:"
        )

        lines.append(
            affiliate_url
        )

    # --------------------------------
    # LOJA
    # --------------------------------

    lines.append("")

    if official_store:

        lines.append(
            "🏪 Loja oficial no MELI!"
        )

    elif seller_name:

        lines.append(
            f"🏪 Loja {seller_name} no MELI!"
        )

    else:

        lines.append(
            "🏪 Loja no MELI!"
        )

    # --------------------------------
    # AVISO
    # --------------------------------

    if coupon_code:

        lines.append("")

        lines.append(
            "⚠️ Cupom e valor final "
            "devem ser confirmados no checkout."
        )

    return jsonify({
        "ok": True,
        "text":
            "\n".join(lines)
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

<title>
Caçador de Ofertas
</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    background:
        #f1f3f6;

    color:
        #111827;

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

    max-width: 920px;

    margin: auto;

    padding: 14px;
}

.header {

    background:
        white;

    border-radius:
        20px;

    padding:
        20px;

    margin-bottom:
        14px;

    box-shadow:
        0 4px 20px
        rgba(0,0,0,.06);
}

.header h1 {

    margin:
        0 0 7px;

    font-size:
        25px;
}

.header p {

    margin: 0;

    color:
        #6b7280;
}

.connection {

    margin-top:
        15px;

    display:
        flex;

    justify-content:
        space-between;

    align-items:
        center;

    gap:
        10px;

    flex-wrap:
        wrap;
}

.connected {

    color:
        #16895c;

    font-weight:
        850;

    font-size:
        17px;
}

.btn {

    border: 0;

    border-radius:
        12px;

    padding:
        13px 18px;

    font-size:
        14px;

    font-weight:
        850;

    cursor:
        pointer;

    text-decoration:
        none;

    display:
        inline-flex;

    align-items:
        center;

    justify-content:
        center;
}

.btn-primary {

    background:
        #3483fa;

    color:
        white;
}

.btn-danger {

    background:
        #ef5350;

    color:
        white;
}

.btn-dark {

    background:
        #111827;

    color:
        white;
}

.btn:disabled {

    opacity:
        .55;

    cursor:
        not-allowed;
}


/* ==========================================
   ESTATÍSTICAS
   ========================================== */

.stats {

    display:
        grid;

    grid-template-columns:
        repeat(4, 1fr);

    gap:
        10px;

    margin-bottom:
        14px;
}

.stat {

    background:
        white;

    border-radius:
        17px;

    padding:
        17px;

    box-shadow:
        0 4px 18px
        rgba(0,0,0,.05);
}

.stat-label {

    color:
        #6b7280;

    font-size:
        13px;

    margin-bottom:
        7px;
}

.stat-value {

    font-size:
        22px;

    font-weight:
        950;
}


/* ==========================================
   CAÇA
   ========================================== */

.scan {

    background:
        white;

    border-radius:
        20px;

    padding:
        19px;

    margin-bottom:
        15px;

    box-shadow:
        0 4px 18px
        rgba(0,0,0,.05);
}

.scan-title {

    font-size:
        21px;

    font-weight:
        950;

    margin-bottom:
        13px;
}

.progress {

    width:
        100%;

    height:
        10px;

    background:
        #e5e7eb;

    border-radius:
        999px;

    overflow:
        hidden;

    margin:
        15px 0 10px;
}

.progress-bar {

    height:
        100%;

    width:
        0%;

    background:
        #3483fa;

    transition:
        width .25s;
}

.status {

    color:
        #6b7280;

    font-size:
        14px;
}


/* ==========================================
   OFERTA
   ========================================== */

.offer {

    background:
        #202222;

    color:
        white;

    border-radius:
        20px;

    overflow:
        hidden;

    margin-bottom:
        18px;

    box-shadow:
        0 5px 22px
        rgba(0,0,0,.12);
}

.offer-image {

    width:
        100%;

    aspect-ratio:
        1 / 1;

    background:
        #e5e7eb;

    display:
        flex;

    align-items:
        center;

    justify-content:
        center;

    overflow:
        hidden;
}

.offer-image img {

    width:
        100%;

    height:
        100%;

    object-fit:
        cover;

    display:
        block;
}

.offer-content {

    padding:
        17px 18px 20px;
}

.offer-title {

    font-size:
        21px;

    line-height:
        1.25;

    font-weight:
        900;

    margin-bottom:
        18px;
}

.old-price {

    color:
        #eeeeee;

    font-size:
        18px;

    margin-bottom:
        3px;
}

.old-price span {

    text-decoration:
        line-through;
}

.current-label {

    color:
        #d1d5db;

    font-size:
        13px;
}

.final-price {

    font-size:
        31px;

    font-weight:
        950;

    margin-top:
        2px;

    color:
        #ffffff;
}

.final-price small {

    font-size:
        16px;

    font-weight:
        700;
}


/* ==========================================
   CUPOM
   ========================================== */

.coupon {

    margin-top:
        18px;

    padding:
        13px;

    border-radius:
        13px;

    background:
        #292b2b;

    border:
        1px solid
        #444;
}

.coupon-title {

    font-size:
        17px;

    margin-bottom:
        5px;
}

.coupon-title strong {

    color:
        #ffffff;
}

.coupon-saving {

    color:
        #d1d5db;

    font-size:
        13px;

    line-height:
        1.45;
}

.coupon-status {

    display:
        inline-block;

    margin-top:
        8px;

    background:
        #f59e0b;

    color:
        #111827;

    border-radius:
        999px;

    padding:
        4px 9px;

    font-size:
        10px;

    font-weight:
        950;
}


/* ==========================================
   LINK
   ========================================== */

.affiliate-box {

    margin-top:
        15px;
}

.affiliate-box label {

    display:
        block;

    font-size:
        12px;

    color:
        #bfc2c4;

    margin-bottom:
        6px;
}

.affiliate {

    width:
        100%;

    border:
        1px solid
        #555;

    background:
        #292b2b;

    color:
        white;

    border-radius:
        10px;

    padding:
        12px;

    outline:
        none;
}

.actions {

    display:
        grid;

    grid-template-columns:
        1fr 1fr;

    gap:
        8px;

    margin-top:
        12px;
}

.actions .btn {

    width:
        100%;
}

.store {

    margin-top:
        14px;

    font-weight:
        700;

    color:
        #eeeeee;
}


/* ==========================================
   ANÚNCIO
   ========================================== */

.ad-box {

    display:
        none;

    margin-top:
        12px;
}

.ad-box textarea {

    width:
        100%;

    min-height:
        180px;

    resize:
        vertical;

    border:
        1px solid
        #555;

    border-radius:
        12px;

    background:
        #151717;

    color:
        white;

    padding:
        12px;

    font-size:
        14px;

    line-height:
        1.45;
}

.note {

    color:
        #6b7280;

    font-size:
        12px;

    line-height:
        1.5;

    margin:
        18px 3px 30px;
}

.empty {

    background:
        white;

    border-radius:
        18px;

    padding:
        35px 20px;

    text-align:
        center;

    color:
        #6b7280;
}


/* ==========================================
   MOBILE
   ========================================== */

@media(max-width:700px) {

    .stats {

        grid-template-columns:
            repeat(2, 1fr);
    }

    .container {

        padding:
            10px;
    }

    .offer-title {

        font-size:
            20px;
    }

    .final-price {

        font-size:
            29px;
    }
}

</style>

</head>

<body>

<div class="container">


<!-- ======================================
     CABEÇALHO
     ====================================== -->

<div class="header">

<h1>
🔥 Caçador de Ofertas
</h1>

<p>
Encontre produtos com desconto e cupons
para publicar diretamente no seu grupo.
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
    href="/mercadolivre/disconnect"
    class="btn btn-danger"
>
Desconectar
</a>

{% else %}

<a
    href="/mercadolivre/connect"
    class="btn btn-primary"
>
🔗 Conectar Mercado Livre
</a>

{% endif %}

</div>

</div>


<!-- ======================================
     ESTATÍSTICAS
     ====================================== -->

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
Cupom encontrado
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
Valor com desconto
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

<!-- ======================================
     CAÇA
     ====================================== -->

<div class="scan">

<div class="scan-title">
🔎 Caçar ofertas
</div>

<button
    id="scanButton"
    class="btn btn-primary"
    onclick="startScan()"
>
🚀 CAÇAR OFERTAS
</button>

<div class="progress">

<div
    id="progressBar"
    class="progress-bar"
></div>

</div>

<div
    id="scanStatus"
    class="status"
>
Pronto para começar.
</div>

</div>

{% endif %}


<!-- ======================================
     OFERTAS
     ====================================== -->

<div id="offers"></div>


<div class="note">

⚠️ O cupom mostrado é uma estimativa baseada
nas regras públicas encontradas. A disponibilidade
e aplicação do cupom devem ser confirmadas no
checkout do Mercado Livre.

</div>

</div>


<script>


// ============================================================
// DINHEIRO
// ============================================================

function money(value) {

    return Number(
        value || 0
    ).toLocaleString(
        "pt-BR",
        {
            style:
                "currency",

            currency:
                "BRL"
        }
    );
}


// ============================================================
// ESCAPE
// ============================================================

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


// ============================================================
// START SCAN
// ============================================================

let currentJob = null;


async function startScan() {

    const button =
        document.getElementById(
            "scanButton"
        );

    if (!button) {
        return;
    }

    button.disabled =
        true;

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
                data.error ||
                "Erro ao iniciar.";

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
            "Erro: " + error;

        button.disabled =
            false;

        button.innerText =
            "🚀 CAÇAR OFERTAS";
    }
}


// ============================================================
// STATUS
// ============================================================

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
                data.progress || 0
            )
            + "%";

        document.getElementById(
            "scanStatus"
        ).innerText =
            data.message || "";


        if (
            data.status ===
            "done"
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
            data.status ===
            "error"
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


// ============================================================
// CARREGAR OFERTAS
// ============================================================

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
            data.offers || []
        );

    } catch(error) {

        console.error(
            error
        );
    }
}


// ============================================================
// RENDER
// ============================================================

function renderOffers(
    offers
) {

    const container =
        document.getElementById(
            "offers"
        );

    container.innerHTML =
        "";

    let coupons =
        0;

    let totalCurrent =
        0;

    let totalFinal =
        0;

    const seen =
        new Set();


    for (
        const offer
        of offers
    ) {

        if (
            seen.has(
                offer.product_id
            )
        ) {
            continue;
        }

        seen.add(
            offer.product_id
        );


        totalCurrent +=
            Number(
                offer.current_price
                || 0
            );


        totalFinal +=
            Number(
                offer.final_price
                || 0
            );


        if (
            offer.coupon_code
        ) {

            coupons++;
        }


        const card =
            document.createElement(
                "div"
            );

        card.className =
            "offer";


        // ---------------------------------
        // IMAGEM
        // ---------------------------------

        let imageHtml = "";

        if (
            offer.image_url
        ) {

            imageHtml = `

<div class="offer-image">

<img
    src="${escapeHtml(
        offer.image_url
    )}"
    onerror="this.parentElement.style.display='none';"
>

</div>

`;

        }


        // ---------------------------------
        // PREÇO ANTIGO
        // ---------------------------------

        let oldPrice =
            "";

        if (
            offer.original_price
            &&
            Number(
                offer.original_price
            )
            >
            Number(
                offer.current_price
            )
        ) {

            oldPrice = `

<div class="old-price">

De
<span>
${money(
    offer.original_price
)}
</span>

</div>

`;

        }


        // ---------------------------------
        // CUPOM
        // ---------------------------------

        let couponHtml =
            "";

        if (
            offer.coupon_code
        ) {

            couponHtml = `

<div class="coupon">

<div class="coupon-title">

🎟️ Cupom:
<strong>
${escapeHtml(
    offer.coupon_code
)}
</strong>

</div>


<div class="coupon-saving">

Economia estimada:
<strong>
${money(
    offer.coupon_discount
)}
</strong>

</div>


<div class="coupon-status">
CUPOM ESTIMADO
</div>

</div>

`;

        }


        // ---------------------------------
        // LOJA
        // ---------------------------------

        let storeText =
            "🏪 Loja no MELI!";

        if (
            offer.official_store
        ) {

            storeText =
                "🏪 Loja oficial no MELI!";

        } else if (
            offer.seller_name
        ) {

            storeText =
                "🏪 Loja "
                + escapeHtml(
                    offer.seller_name
                )
                + " no MELI!";

        }


        // ---------------------------------
        // CARD
        // ---------------------------------

        card.innerHTML = `

${imageHtml}


<div class="offer-content">


<div class="offer-title">

${escapeHtml(
    offer.product_title
)}

</div>


${oldPrice}


<div class="current-label">

Por

</div>


<div class="final-price">

${money(
    offer.final_price
)}

${offer.coupon_code
    ? "<small> 🔥</small>"
    : ""
}

</div>


${couponHtml}


<div class="affiliate-box">

<label>
Seu link de afiliado
</label>

<input
    class="affiliate"
    id="affiliate-${escapeHtml(
        offer.product_id
    )}"
    placeholder="Cole aqui seu link meli.la..."
>


</div>


<div class="actions">


<a
    class="btn btn-primary"
    href="${escapeHtml(
        offer.product_url
    )}"
    target="_blank"
>
🛒 Abrir produto
</a>


<button
    class="btn btn-dark"
    onclick='generateAd(
        ${JSON.stringify(
            offer
        )}
    )'
>
📢 Gerar anúncio
</button>


</div>


<div class="store">

${storeText}

</div>


<div
    class="ad-box"
    id="ad-${escapeHtml(
        offer.product_id
    )}"
>

<textarea
    id="adtext-${escapeHtml(
        offer.product_id
    )}"
    readonly
></textarea>


<button
    class="btn btn-primary"
    style="margin-top:8px;"
    onclick="copyAd(
        '${escapeHtml(
            offer.product_id
        )}'
    )"
>
📋 Copiar anúncio
</button>

</div>


</div>

`;

        container.appendChild(
            card
        );
    }


    // =====================================
    // ESTATÍSTICAS
    // =====================================

    document.getElementById(
        "statOffers"
    ).innerText =
        seen.size;


    document.getElementById(
        "statCoupons"
    ).innerText =
        coupons;


    document.getElementById(
        "statCurrent"
    ).innerText =
        money(
            totalCurrent
        );


    document.getElementById(
        "statFinal"
    ).innerText =
        money(
            totalFinal
        );


    if (!offers.length) {

        container.innerHTML = `

<div class="empty">

🔎 Nenhuma oferta encontrada.

<br><br>

Clique em
<strong>
CAÇAR OFERTAS
</strong>
para procurar novamente.

</div>

`;

    }
}


// ============================================================
// GERAR ANÚNCIO
// ============================================================

async function generateAd(
    offer
) {

    const input =
        document.getElementById(
            "affiliate-"
            + offer.product_id
        );

    const affiliate =
        input
        ? input.value.trim()
        : "";


    if (!affiliate) {

        alert(
            "Cole primeiro o seu link de afiliado."
        );

        if (input) {
            input.focus();
        }

        return;
    }


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

                            original_price:
                                offer.original_price,

                            coupon_code:
                                offer.coupon_code,

                            coupon_discount:
                                offer.coupon_discount,

                            final_price:
                                offer.final_price,

                            affiliate_url:
                                affiliate,

                            official_store:
                                offer.official_store,

                            seller_name:
                                offer.seller_name
                        })
                }
            );


        const data =
            await response.json();


        if (!data.ok) {

            alert(
                "Erro ao gerar anúncio."
            );

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


        box.scrollIntoView({
            behavior:
                "smooth",
            block:
                "center"
        });


    } catch(error) {

        alert(
            "Erro ao gerar anúncio."
        );
    }
}


// ============================================================
// COPIAR
// ============================================================

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


// ============================================================
// CARREGAR AO ABRIR
// ============================================================

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