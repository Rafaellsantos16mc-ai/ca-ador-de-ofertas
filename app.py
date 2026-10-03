import os
import re
import time
import html as html_lib
import sqlite3
import secrets
import hashlib
import base64
from urllib.parse import urlencode

import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string


# ============================================================
# APP
# ============================================================

app = Flask(__name__)
app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "chave-cacador-ofertas"
)

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

SITE_ID = "MLB"
DB_FILE = "ofertas.db"

# Produto mínimo
MIN_PRODUCT_PRICE = 69.90

# Fontes públicas onde procuramos cupons
COUPON_SOURCE_URLS = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/ofertas/cupons",
]


# ============================================================
# CATÁLOGO
# ============================================================

CATALOG = {
    "📱 Celulares": [
        "smartphone",
        "iphone",
        "samsung galaxy",
        "motorola moto",
        "xiaomi redmi",
        "poco smartphone",
        "realme smartphone",
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "perfume nacional",
        "eau de parfum",
    ],

    "🏋️ Academia": [
        "roupa academia",
        "camiseta academia",
        "short academia",
        "legging academia",
        "tenis academia",
        "tenis corrida",
        "tenis treino",
        "whey protein",
        "creatina",
        "pre treino",
        "suplementos",
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "esmerilhadeira",
        "kit ferramentas",
        "maleta ferramentas",
        "serra",
        "chave de impacto",
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "headset",
        "smartwatch",
        "tablet",
        "caixa de som bluetooth",
        "camera digital",
        "power bank",
    ],

    "🏠 Casa": [
        "aspirador de pó",
        "liquidificador",
        "cafeteira",
        "air fryer",
        "ventilador",
        "ferro de passar",
    ],

    "🍳 Cozinha": [
        "air fryer",
        "panela elétrica",
        "jogo de panelas",
        "cafeteira",
        "liquidificador",
        "sandwichera",
    ],

    "🚗 Automotivo": [
        "compressor automotivo",
        "aspirador automotivo",
        "suporte celular carro",
        "carregador automotivo",
        "ferramentas automotivas",
        "tapete automotivo",
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "mochila",
        "relogio masculino",
        "bolsa feminina",
        "oculos de sol",
    ],
}


# ============================================================
# BANCO
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK(id=1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            description TEXT,
            discount_percent REAL,
            fixed_discount REAL DEFAULT 0,
            min_purchase REAL,
            max_discount REAL,
            source_url TEXT,
            conditions TEXT,
            active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            item_id TEXT,
            title TEXT,
            permalink TEXT,
            price REAL,
            original_price REAL,
            coupon_code TEXT,
            coupon_discount REAL,
            final_price REAL,
            seller_id TEXT,
            image TEXT,
            category_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILIDADES
# ============================================================

def brl(value):
    try:
        return (
            f"R$ {float(value):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


def number(value):
    if value is None:
        return None

    m = re.search(
        r"(\d+(?:[.,]\d+)?)",
        str(value)
    )

    if not m:
        return None

    return float(
        m.group(1).replace(",", ".")
    )


def norm(text):
    if not text:
        return ""

    text = str(text).lower()

    trans = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüç",
        "aaaaaeeeeiiiiooooouuuuc"
    )

    text = text.translate(trans)

    text = re.sub(
        r"[^a-z0-9\s]+",
        " ",
        text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def json_safe(value):
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            json_safe(v)
            for v in value
        ]

    return str(value)


def discount_percent(price, original):
    try:
        price = float(price)
        original = float(original)

        if original > price > 0:
            return round(
                (1 - price / original) * 100,
                2
            )
    except Exception:
        pass

    return 0


# ============================================================
# OAUTH
# ============================================================

def create_pkce():
    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode()
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode().rstrip("=")

    return verifier, challenge


def get_tokens():
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def save_tokens(data, user=None):
    old = get_tokens() or {}

    conn = get_db()

    conn.execute("""
        INSERT INTO oauth_tokens(
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES(1,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=
                COALESCE(
                    excluded.refresh_token,
                    oauth_tokens.refresh_token
                ),
            expires_at=excluded.expires_at,
            user_id=
                COALESCE(
                    excluded.user_id,
                    oauth_tokens.user_id
                ),
            nickname=
                COALESCE(
                    excluded.nickname,
                    oauth_tokens.nickname
                )
    """, (
        data.get("access_token"),
        data.get("refresh_token"),
        int(time.time()) +
        int(data.get("expires_in", 21600)),
        str(user.get("id"))
        if user and user.get("id")
        else old.get("user_id"),
        user.get("nickname")
        if user
        else old.get("nickname"),
    ))

    conn.commit()
    conn.close()


def refresh_token():
    data = get_tokens()

    if not data:
        return None

    refresh = data.get("refresh_token")

    if not refresh:
        return None

    try:
        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh,
            },
            timeout=30,
        )

        if response.status_code != 200:
            print(
                "[REFRESH ERRO]",
                response.status_code,
                response.text[:500]
            )
            return None

        new_data = response.json()

        save_tokens(
            new_data,
            {
                "id": data.get("user_id"),
                "nickname": data.get("nickname"),
            }
        )

        return new_data.get("access_token")

    except Exception as e:
        print("[REFRESH EXCEPTION]", e)
        return None


def get_access_token():
    data = get_tokens()

    if not data:
        return None

    token = data.get("access_token")

    if token and time.time() < (
        data.get("expires_at") or 0
    ) - 120:
        return token

    return refresh_token() or token


def ml_get(path, params=None):
    token = get_access_token()

    if not token:
        return {}, 401

    url = (
        path
        if path.startswith("http")
        else ML_API + path
    )

    try:
        response = requests.get(
            url,
            headers={
                "Authorization":
                    f"Bearer {token}",
                "Accept":
                    "application/json",
            },
            params=params,
            timeout=30,
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "message": response.text
            }

        return data, response.status_code

    except requests.RequestException as e:
        return {
            "error": str(e)
        }, 500


# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:
        return jsonify({
            "erro":
                "ML_CLIENT_ID não configurado."
        }), 500

    verifier, challenge = create_pkce()

    state = secrets.token_urlsafe(32)

    session["ml_state"] = state
    session["ml_code_verifier"] = verifier

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    return redirect(
        ML_AUTH + "?" + urlencode(params)
    )


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    if request.args.get("error"):
        return jsonify({
            "erro":
                request.args.get("error"),
            "descricao":
                request.args.get(
                    "error_description"
                ),
        }), 400

    code = request.args.get("code")
    state = request.args.get("state")

    if (
        not code
        or state != session.get("ml_state")
    ):
        return jsonify({
            "erro":
                "Código ou state inválido."
        }), 400

    try:

        response = requests.post(
            ML_TOKEN,
            data={
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
                    session.get(
                        "ml_code_verifier"
                    ),
            },
            timeout=30,
        )

        if response.status_code != 200:
            return jsonify({
                "erro":
                    "Falha ao obter token.",
                "status":
                    response.status_code,
                "resposta":
                    response.text,
            }), response.status_code

        data = response.json()

        user = None

        if data.get("access_token"):

            me = requests.get(
                ML_API + "/users/me",
                headers={
                    "Authorization":
                        "Bearer " +
                        data["access_token"]
                },
                timeout=30,
            )

            if me.status_code == 200:
                user = me.json()

        save_tokens(data, user)

        session.pop("ml_state", None)
        session.pop(
            "ml_code_verifier",
            None
        )

        return redirect(
            "/?conectado=1"
        )

    except Exception as e:

        return jsonify({
            "erro": str(e)
        }), 500


@app.route("/mercadolivre/logout")
def mercadolivre_logout():

    conn = get_db()

    conn.execute(
        "DELETE FROM oauth_tokens WHERE id=1"
    )

    conn.commit()
    conn.close()

    session.clear()

    return redirect("/")


# ============================================================
# PRODUTOS MERCADO LIVRE
# ============================================================

def discover_categories(query):

    data, status = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {"q": query}
    )

    if status != 200:
        return []

    if not isinstance(data, list):
        return []

    result = []

    for item in data:

        category_id = (
            item.get("category_id")
            or item.get("id")
        )

        category_name = (
            item.get("category_name")
            or item.get("name")
            or category_id
        )

        if category_id:

            result.append({
                "category_id":
                    category_id,
                "category_name":
                    category_name,
            })

    return result


def get_highlights(category_id):

    data, status = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if status != 200:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        return (
            data.get("content")
            or data.get("results")
            or []
        )

    return []


def get_product(product_id):

    data, status = ml_get(
        f"/products/{product_id}"
    )

    if (
        status == 200
        and isinstance(data, dict)
    ):
        return data

    return None


def get_product_items(product_id):

    data, status = ml_get(
        f"/products/{product_id}/items"
    )

    if status != 200:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return data.get(
            "results",
            []
        )

    return []


def normalize_item(item):

    if (
        not isinstance(item, dict)
        or not item.get("item_id")
    ):
        return None

    shipping = (
        item.get("shipping")
        or {}
    )

    free_shipping = bool(
        shipping.get("free_shipping")
    )

    shipping_cost = (
        0
        if free_shipping
        else shipping.get("cost")
    )

    return {
        "item_id":
            item.get("item_id"),

        "seller_id":
            item.get("seller_id"),

        "price":
            item.get("price"),

        "original_price":
            item.get("original_price"),

        "condition":
            item.get("condition"),

        "permalink":
            item.get("permalink"),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost,

        "user_product_id":
            item.get("user_product_id"),
    }


# ============================================================
# CUPONS
# ============================================================

def normalize_coupon_html(raw):

    text = html_lib.unescape(
        raw or ""
    )

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<noscript.*?</noscript>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"</(?:div|p|li|h1|h2|h3|h4|h5|h6|section|article|br|tr)>",
        "\n",
        text,
        flags=re.I
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = text.replace(
        "\r",
        "\n"
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n[ \t]+",
        "\n",
        text
    )

    return text.strip()


def valid_coupon_code(code):

    code = (
        code or ""
    ).strip().upper()

    if not re.fullmatch(
        r"[A-Z0-9][A-Z0-9_-]{5,29}",
        code
    ):
        return False

    if not re.search(
        r"[A-Z]",
        code
    ):
        return False

    if not re.search(
        r"\d",
        code
    ):
        return False

    invalid = {
        "NAOCOUPOM",
        "CUPOMVALIDO",
        "VALIDO",
        "DESCONTO",
    }

    return code not in invalid


def find_coupon_blocks(text):

    pattern = re.compile(
        r"\bCupom\s+"
        r"([A-Z0-9][A-Z0-9_-]{5,29})"
        r"\b",
        re.I
    )

    matches = []

    for match in pattern.finditer(text):

        code = match.group(1).upper()

        if valid_coupon_code(code):
            matches.append(
                (
                    match.start(),
                    match.end(),
                    code
                )
            )

    blocks = []

    for index, (
        start,
        end,
        code
    ) in enumerate(matches):

        next_start = (
            matches[index + 1][0]
            if index + 1 < len(matches)
            else len(text)
        )

        block = text[
            end:next_start
        ].strip()

        if len(block) > 6000:
            block = block[:6000]

        blocks.append(
            (
                code,
                block
            )
        )

    return blocks


def parse_coupon_block(
    code,
    block,
    source_url
):

    percent = None
    fixed = None
    minimum = None
    maximum = None

    percent_patterns = [
        r"(\d+(?:[.,]\d+)?)\s*%\s*OFF",
        r"(\d+(?:[.,]\d+)?)\s*%\s*de\s*desconto",
        r"desconto\s+de\s+(\d+(?:[.,]\d+)?)\s*%",
        r"até\s+(\d+(?:[.,]\d+)?)\s*%",
    ]

    for pattern in percent_patterns:

        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:
            percent = number(
                match.group(1)
            )
            break

    fixed_patterns = [
        r"R\$\s*(\d+(?:[.,]\d+)?)\s*OFF",
        r"R\$\s*(\d+(?:[.,]\d+)?)\s*de\s*desconto",
        r"desconto\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
        r"ganhe\s+R\$\s*(\d+(?:[.,]\d+)?)",
    ]

    for pattern in fixed_patterns:

        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:
            fixed = number(
                match.group(1)
            )
            break

    minimum_patterns = [
        r"compra\s+m[ií]nima\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
        r"valor\s+m[ií]nimo\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
        r"a\s+partir\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
        r"partir\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
        r"m[ií]nimo\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
    ]

    for pattern in minimum_patterns:

        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:
            minimum = number(
                match.group(1)
            )
            break

    maximum_patterns = [
        r"desconto\s+m[aá]ximo\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
        r"m[aá]ximo\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
        r"limitado\s+a\s+R\$\s*(\d+(?:[.,]\d+)?)",
        r"limite\s+de\s+R\$\s*(\d+(?:[.,]\d+)?)",
    ]

    for pattern in maximum_patterns:

        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:
            maximum = number(
                match.group(1)
            )
            break

    if not percent and not fixed:
        return None

    return {
        "code":
            code,

        "description":
            block[:2500],

        "discount_percent":
            percent,

        "fixed_discount":
            fixed or 0,

        "min_purchase":
            minimum,

        "max_discount":
            maximum,

        "source_url":
            source_url,

        "conditions":
            block[:2500],

        "active":
            1,
    }


def scrape_coupon_source(url):

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent":
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/140.0 Safari/537.36",

                "Accept-Language":
                    "pt-BR,pt;q=0.9,en;q=0.8",
            },
            timeout=30,
        )

        if response.status_code != 200:
            print(
                "[CUPONS]",
                url,
                response.status_code
            )
            return []

        text = normalize_coupon_html(
            response.text
        )

        blocks = find_coupon_blocks(
            text
        )

        result = []

        for code, block in blocks:

            coupon = parse_coupon_block(
                code,
                block,
                url
            )

            if coupon:
                result.append(coupon)

        return result

    except Exception as e:

        print(
            "[CUPONS ERRO]",
            url,
            e
        )

        return []


def sync_coupons():

    all_coupons = {}
    errors = []

    for url in COUPON_SOURCE_URLS:

        coupons_found = scrape_coupon_source(
            url
        )

        for coupon in coupons_found:

            code = coupon["code"]

            # Se o mesmo cupom aparecer em mais
            # de uma fonte, mantém a informação
            # mais completa.
            old = all_coupons.get(code)

            if not old:
                all_coupons[code] = coupon
                continue

            if (
                len(
                    coupon.get(
                        "conditions",
                        ""
                    )
                )
                >
                len(
                    old.get(
                        "conditions",
                        ""
                    )
                )
            ):
                all_coupons[code] = coupon

    if not all_coupons:
        return {
            "ok": False,
            "erro":
                "Nenhum cupom encontrado nas fontes consultadas.",
        }

    conn = get_db()

    conn.execute(
        "UPDATE cupons SET active=0"
    )

    for coupon in all_coupons.values():

        conn.execute("""
            INSERT INTO cupons(
                code,
                description,
                discount_percent,
                fixed_discount,
                min_purchase,
                max_discount,
                source_url,
                conditions,
                active,
                updated_at
            )
            VALUES(
                ?,?,?,?,?,?,?,?,1,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT(code)
            DO UPDATE SET
                description=
                    excluded.description,
                discount_percent=
                    excluded.discount_percent,
                fixed_discount=
                    excluded.fixed_discount,
                min_purchase=
                    excluded.min_purchase,
                max_discount=
                    excluded.max_discount,
                source_url=
                    excluded.source_url,
                conditions=
                    excluded.conditions,
                active=1,
                updated_at=
                    CURRENT_TIMESTAMP
        """, (
            coupon["code"],
            coupon["description"],
            coupon["discount_percent"],
            coupon["fixed_discount"],
            coupon["min_purchase"],
            coupon["max_discount"],
            coupon["source_url"],
            coupon["conditions"],
        ))

    conn.commit()
    conn.close()

    print("")
    print(
        "================ CUPONS ================"
    )

    for coupon in sorted(
        all_coupons.values(),
        key=lambda x: (
            -float(
                x.get("max_discount")
                or 0
            ),
            -float(
                x.get("discount_percent")
                or 0
            ),
            -float(
                x.get("fixed_discount")
                or 0
            ),
        )
    ):

        print(
            "[CUPOM]",
            coupon["code"],
            "|",
            coupon.get(
                "discount_percent"
            ),
            "%",
            "| fixo",
            brl(
                coupon.get(
                    "fixed_discount"
                )
            ),
            "| mínimo",
            brl(
                coupon.get(
                    "min_purchase"
                )
            ),
            "| máximo",
            brl(
                coupon.get(
                    "max_discount"
                )
            ),
        )

    print(
        "=========================================="
    )

    return {
        "ok": True,
        "cupons_encontrados":
            len(all_coupons),
    }


def get_coupons():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM cupons
        WHERE active=1
        ORDER BY
            discount_percent DESC,
            max_discount DESC,
            fixed_discount DESC
    """).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# CÁLCULO DOS CUPONS
# ============================================================

def coupon_discount(coupon, price):

    try:

        price = float(price)

        minimum = coupon.get(
            "min_purchase"
        )

        if (
            minimum
            and price < float(minimum)
        ):
            return 0

        possibilities = []

        percent = float(
            coupon.get(
                "discount_percent"
            )
            or 0
        )

        fixed = float(
            coupon.get(
                "fixed_discount"
            )
            or 0
        )

        maximum = coupon.get(
            "max_discount"
        )

        # Percentual
        if percent > 0:

            discount = (
                price *
                percent /
                100
            )

            if maximum:
                discount = min(
                    discount,
                    float(maximum)
                )

            possibilities.append(
                discount
            )

        # Valor fixo
        if fixed > 0:

            discount = fixed

            if maximum:
                discount = min(
                    discount,
                    float(maximum)
                )

            possibilities.append(
                discount
            )

        if not possibilities:
            return 0

        # Nunca permite desconto maior
        # que o próprio produto.
        return round(
            min(
                price,
                max(possibilities)
            ),
            2
        )

    except Exception:
        return 0


def find_best_coupon(price):

    candidates = []

    for coupon in get_coupons():

        discount = coupon_discount(
            coupon,
            price
        )

        if discount <= 0:
            continue

        item = dict(coupon)

        item["discount_value"] = (
            discount
        )

        item["effective_percent"] = round(
            (
                discount /
                float(price)
            ) * 100,
            2
        )

        candidates.append(item)

    if not candidates:
        return None

    # PRINCIPAL:
    # maior desconto REAL em R$
    #
    # Não existe limite artificial.
    #
    # Se aparecer um cupom que economize
    # R$200, ele vence um de R$70.
    #
    # Se aparecer R$500, vence R$200.
    #
    # O teto existente é somente o teto
    # informado pelo próprio cupom.
    return max(
        candidates,
        key=lambda x: (
            x["discount_value"],
            x["effective_percent"],
            -float(
                x.get("min_purchase")
                or 0
            ),
        )
    )


# ============================================================
# RELEVÂNCIA
# ============================================================

BAD_WORDS = [
    "capa",
    "capinha",
    "pelicula",
    "película",
    "suporte",
    "cabo",
    "adaptador",
    "case",
    "bateria avulsa",
]


def relevance(title, query):

    title_n = norm(title)
    query_n = norm(query)

    score = 0

    for word in query_n.split():

        if len(word) >= 3 and word in title_n:
            score += 20

    for bad in BAD_WORDS:

        if norm(bad) in title_n:
            score -= 80

    return score


# ============================================================
# CAÇADOR
# ============================================================

def scan_queries(queries):

    # --------------------------------------------------------
    # 1. Descobrir produtos
    # --------------------------------------------------------

    products = {}

    for query in queries:

        categories = discover_categories(
            query
        )

        for category in categories[:4]:

            highlights = get_highlights(
                category["category_id"]
            )

            for highlight in highlights:

                if highlight.get("type") != "PRODUCT":
                    continue

                product_id = (
                    highlight.get("id")
                    or
                    highlight.get("product_id")
                )

                if not product_id:
                    continue

                products.setdefault(
                    product_id,
                    {
                        "query": query,
                        "category_name":
                            category[
                                "category_name"
                            ],
                    }
                )

    print(
        "[PRODUTOS ENCONTRADOS]",
        len(products)
    )

    # --------------------------------------------------------
    # 2. Processar produtos
    # --------------------------------------------------------

    offers = []

    # Evita repetir o mesmo item
    seen_items = set()

    for product_id, base in products.items():

        product_data = get_product(
            product_id
        )

        if not product_data:
            continue

        title = (
            product_data.get("name")
            or
            product_data.get("title")
            or
            product_id
        )

        score = relevance(
            title,
            base["query"]
        )

        if score < 0:
            continue

        pictures = (
            product_data.get(
                "pictures"
            )
            or []
        )

        image = None

        if pictures:

            first = pictures[0]

            if isinstance(first, dict):
                image = (
                    first.get("url")
                    or
                    first.get("secure_url")
                )

        items = get_product_items(
            product_id
        )

        # ----------------------------------------------------
        # 3. Comparar todos os vendedores
        # ----------------------------------------------------

        for raw_item in items:

            item = normalize_item(
                raw_item
            )

            if not item:
                continue

            item_id = item[
                "item_id"
            ]

            if item_id in seen_items:
                continue

            seen_items.add(item_id)

            try:
                price = float(
                    item["price"]
                )
            except Exception:
                continue

            if price < MIN_PRODUCT_PRICE:
                continue

            original_price = item.get(
                "original_price"
            )

            try:
                if original_price:
                    original_price = float(
                        original_price
                    )
            except Exception:
                original_price = None

            seller_discount = (
                discount_percent(
                    price,
                    original_price
                )
            )

            # ------------------------------------------------
            # 4. CAÇAR O MELHOR CUPOM
            # ------------------------------------------------

            best_coupon = find_best_coupon(
                price
            )

            coupon_discount_value = 0

            coupon_final_price = price

            if best_coupon:

                coupon_discount_value = (
                    best_coupon[
                        "discount_value"
                    ]
                )

                coupon_final_price = round(
                    max(
                        0,
                        price -
                        coupon_discount_value
                    ),
                    2
                )

            # ------------------------------------------------
            # 5. Oferta
            # ------------------------------------------------

            permalink = (
                item.get("permalink")
                or
                product_data.get(
                    "permalink"
                )
                or
                f"https://www.mercadolivre.com.br/p/{product_id}"
            )

            offers.append({

                "product_id":
                    product_id,

                "item_id":
                    item_id,

                "title":
                    title,

                "image":
                    image,

                "category_name":
                    base[
                        "category_name"
                    ],

                "price":
                    price,

                "original_price":
                    original_price,

                "seller_discount":
                    seller_discount,

                "seller_id":
                    item.get(
                        "seller_id"
                    ),

                "free_shipping":
                    item.get(
                        "free_shipping"
                    ),

                "shipping_cost":
                    item.get(
                        "shipping_cost"
                    ),

                "permalink":
                    permalink,

                "coupon":
                    best_coupon,

                "coupon_discount":
                    coupon_discount_value,

                "coupon_final_price":
                    coupon_final_price,

                "total_saving":
                    coupon_discount_value,

                "effective_percent":
                    round(
                        (
                            coupon_discount_value /
                            price
                        ) * 100,
                        2
                    )
                    if coupon_discount_value
                    else 0,

                "relevance":
                    score,
            })

    # --------------------------------------------------------
    # 6. Agrupar pelo produto
    #
    # IMPORTANTE:
    # vários vendedores do mesmo produto
    # não viram várias oportunidades.
    # --------------------------------------------------------

    groups = {}

    for offer in offers:

        pid = offer[
            "product_id"
        ]

        groups.setdefault(
            pid,
            []
        ).append(offer)

    final_offers = []

    for product_id, product_offers in groups.items():

        # Melhor combinação:
        #
        # 1. maior desconto em R$
        # 2. maior percentual efetivo
        # 3. menor preço final
        # 4. frete grátis
        # 5. menor preço original
        #
        best = max(
            product_offers,
            key=lambda x: (
                x["total_saving"],
                x["effective_percent"],
                -x["coupon_final_price"],
                1
                if x["free_shipping"]
                else 0,
                -x["price"],
            )
        )

        final_offers.append(
            best
        )

    # --------------------------------------------------------
    # 7. Ordenar pelas maiores oportunidades
    # --------------------------------------------------------

    final_offers.sort(
        key=lambda x: (
            -x["total_saving"],
            -x["effective_percent"],
            x["coupon_final_price"],
            0
            if x["free_shipping"]
            else 1,
            x["price"],
        )
    )

    # --------------------------------------------------------
    # 8. Estatísticas
    # --------------------------------------------------------

    coupon_count = sum(
        1
        for x in final_offers
        if x.get("coupon")
    )

    prices = [
        x["price"]
        for x in final_offers
    ]

    final_prices = [
        x["coupon_final_price"]
        for x in final_offers
    ]

    stats = {
        "Ofertas":
            len(final_offers),

        "Cupom aplicável":
            coupon_count,

        "Valor":
            brl(
                min(prices)
                if prices
                else 0
            ),

        "Valor com o desconto":
            brl(
                min(final_prices)
                if final_prices
                else 0
            ),
    }

    return {
        "stats":
            stats,

        "ofertas":
            final_offers,

        "produtos_unicos":
            len(final_offers),
    }


def auto_scan(category=None):

    if category:
        queries = CATALOG.get(
            category,
            []
        )
    else:
        queries = [
            query
            for category_queries
            in CATALOG.values()
            for query
            in category_queries
        ]

    return scan_queries(
        queries
    )


# ============================================================
# ANÚNCIO
# ============================================================

def generate_ad(
    offer,
    affiliate_link=""
):

    title = offer.get(
        "title",
        "Produto"
    )

    price = offer.get(
        "price",
        0
    )

    final_price = offer.get(
        "coupon_final_price",
        price
    )

    coupon = offer.get(
        "coupon"
    )

    lines = []

    lines.append(
        "🔥 OFERTA IMPERDÍVEL!"
    )

    lines.append("")

    lines.append(
        title
    )

    lines.append("")

    if offer.get(
        "original_price"
    ):
        lines.append(
            f"De {brl(offer['original_price'])}"
        )

    lines.append(
        f"Por {brl(final_price)} 🔥"
    )

    if coupon:

        lines.append("")

        lines.append(
            f"🎟️ Cupom: {coupon['code']}"
        )

        lines.append(
            f"💰 Desconto: "
            f"{brl(offer['coupon_discount'])}"
        )

    lines.append("")

    lines.append(
        "👉 Pegar promoção:"
    )

    lines.append(
        affiliate_link
        or
        offer.get(
            "permalink",
            ""
        )
    )

    lines.append("")

    lines.append(
        "🏪 Loja oficial no MELI!"
    )

    lines.append("")

    lines.append(
        "⚠️ Confira as condições do cupom "
        "e confirme o desconto no checkout."
    )

    return "\n".join(lines)


# ============================================================
# API
# ============================================================

@app.route("/api/cupons")
def api_cupons():

    atualizar = request.args.get(
        "atualizar"
    )

    resultado = None

    if atualizar == "1":

        resultado = sync_coupons()

    return jsonify({
        "cupons":
            json_safe(
                get_coupons()
            ),

        "fontes":
            COUPON_SOURCE_URLS,

        "sincronizacao":
            resultado,
    })


@app.route("/api/cacar")
def api_cacar():

    if not get_access_token():
        return jsonify({
            "erro":
                "Mercado Livre não conectado."
        }), 401

    # Primeiro procura cupons.
    coupon_sync = sync_coupons()

    category = request.args.get(
        "categoria"
    )

    try:

        result = auto_scan(
            category
        )

        result["coupon_sync"] = (
            coupon_sync
        )

        return jsonify(
            json_safe(result)
        )

    except Exception as e:

        print(
            "[ERRO CAÇA]",
            e
        )

        return jsonify({
            "erro":
                str(e)
        }), 500


@app.route("/api/buscar")
def api_buscar():

    if not get_access_token():
        return jsonify({
            "erro":
                "Mercado Livre não conectado."
        }), 401

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:
        return jsonify({
            "erro":
                "Informe o produto."
        }), 400

    coupon_sync = sync_coupons()

    try:

        result = scan_queries(
            [query]
        )

        result["coupon_sync"] = (
            coupon_sync
        )

        return jsonify(
            json_safe(result)
        )

    except Exception as e:

        return jsonify({
            "erro":
                str(e)
        }), 500


@app.route("/api/gerar-anuncio")
def api_gerar_anuncio():

    title = request.args.get(
        "title",
        "Produto"
    )

    price = float(
        request.args.get(
            "price",
            0
        )
        or 0
    )

    original_price = request.args.get(
        "original_price"
    )

    if original_price:

        try:
            original_price = float(
                original_price
            )
        except Exception:
            original_price = None

    coupon_code = request.args.get(
        "cupom",
        ""
    ).strip().upper()

    coupon = None
    coupon_discount_value = 0

    if coupon_code:

        for item in get_coupons():

            if item["code"] == coupon_code:

                coupon = item

                coupon_discount_value = (
                    coupon_discount(
                        coupon,
                        price
                    )
                )

                break

    offer = {

        "title":
            title,

        "price":
            price,

        "original_price":
            original_price,

        "coupon":
            coupon,

        "coupon_discount":
            coupon_discount_value,

        "coupon_final_price":
            max(
                0,
                price -
                coupon_discount_value
            ),

        "permalink":
            request.args.get(
                "product_link",
                ""
            ),
    }

    affiliate = request.args.get(
        "affiliate_link",
        ""
    ).strip()

    return jsonify({
        "anuncio":
            generate_ad(
                offer,
                affiliate
            )
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    token = get_access_token()

    result = {
        "configurado":
            bool(ML_CLIENT_ID),

        "conectado":
            bool(token),
    }

    if token:

        data, status = ml_get(
            "/users/me"
        )

        result["users_me"] = {
            "status_http":
                status,

            "resposta":
                data,
        }

    local = get_tokens()

    if local:

        result["token_local"] = {

            "user_id":
                local.get(
                    "user_id"
                ),

            "nickname":
                local.get(
                    "nickname"
                ),

            "expires_at":
                local.get(
                    "expires_at"
                ),
        }

    return jsonify(
        json_safe(result)
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "status":
            "ok",

        "app":
            "Cacador de Ofertas",

        "mercado_livre_conectado":
            bool(
                get_access_token()
            ),

        "produto_minimo":
            MIN_PRODUCT_PRICE,

        "busca_cupons":
            "ativa",

        "fontes_cupons":
            len(
                COUPON_SOURCE_URLS
            ),

        "maior_desconto":
            "sem limite artificial",

        "1_produto_1_oportunidade":
            True,
    })


# ============================================================
# INTERFACE
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
Caçador de Ofertas
</title>

<style>

*{
    box-sizing:border-box;
}

body{
    margin:0;
    background:#f4f5f7;
    font-family:Arial,sans-serif;
    color:#222;
}

.container{
    max-width:1050px;
    margin:auto;
    padding:18px;
}

.card{
    background:#fff;
    border-radius:16px;
    padding:18px;
    margin-bottom:18px;
    box-shadow:
        0 5px 20px #0000000c;
}

button,
input,
select{
    width:100%;
    padding:13px;
    border-radius:10px;
    border:1px solid #ddd;
    font-size:15px;
}

button{
    border:0;
    background:#3483fa;
    color:#fff;
    cursor:pointer;
    margin-top:7px;
}

.login{
    background:#ffe600;
    color:#222;
}

.grid{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px,1fr)
        );
    gap:9px;
}

.cat{
    background:#fff;
    color:#222;
    border:1px solid #ddd;
    text-align:left;
}

.stats{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(130px,1fr)
        );
    gap:9px;
}

.stat{
    background:#f3f4f6;
    padding:13px;
    border-radius:11px;
}

.stat b{
    display:block;
    font-size:23px;
    margin-top:4px;
}

.offer{
    border:1px solid #eee;
    border-radius:15px;
    padding:14px;
    margin:13px 0;
    background:#fff;
}

.offer img{
    width:100%;
    max-height:250px;
    object-fit:contain;
    border-radius:10px;
    background:#fafafa;
}

.title{
    font-size:18px;
    font-weight:bold;
    margin:10px 0;
}

.price{
    font-size:25px;
    font-weight:bold;
}

.old{
    text-decoration:line-through;
    color:#777;
}

.green{
    color:#00a650;
    font-weight:bold;
}

.coupon{
    background:#fff8d6;
    border:1px dashed #d7ad00;
    border-radius:12px;
    padding:12px;
    margin-top:12px;
}

.final{
    background:#eaf8ef;
    color:#008a3e;
    font-size:20px;
    font-weight:bold;
    padding:11px;
    border-radius:9px;
    margin-top:8px;
}

.ad{
    display:none;
    white-space:pre-wrap;
    background:#f7f7f7;
    padding:12px;
    border-radius:9px;
    margin-top:9px;
    font-size:14px;
}

.small{
    font-size:12px;
    color:#666;
}

.status{
    background:#eef8f0;
    padding:10px;
    border-radius:9px;
}

.badge{
    display:inline-block;
    background:#eef4ff;
    color:#3483fa;
    padding:5px 8px;
    border-radius:7px;
    font-size:11px;
}

</style>

<script>

async function getJson(url){

    const response =
        await fetch(url);

    const text =
        await response.text();

    let data;

    try{
        data =
            JSON.parse(text);
    }catch(e){
        throw new Error(
            text || "Resposta inválida."
        );
    }

    if(!response.ok){

        throw new Error(
            data.erro ||
            "Erro no servidor."
        );
    }

    return data;
}


async function cacar(categoria){

    const status =
        document.getElementById(
            "status"
        );

    status.textContent =
        "🔄 Procurando os melhores cupons e ofertas...";

    try{

        let url =
            "/api/cacar";

        if(categoria){

            url +=
                "?categoria=" +
                encodeURIComponent(
                    categoria
                );
        }

        const data =
            await getJson(url);

        render(data);

        status.textContent =
            "✅ Caça finalizada: " +
            (data.produtos_unicos || 0) +
            " produtos únicos.";

    }catch(error){

        status.textContent =
            "❌ " +
            error.message;
    }
}


async function buscar(){

    const input =
        document.getElementById(
            "q"
        );

    const query =
        input.value.trim();

    if(!query){
        return;
    }

    document.getElementById(
        "status"
    ).textContent =
        "🔄 Procurando...";

    try{

        const data =
            await getJson(
                "/api/buscar?q=" +
                encodeURIComponent(
                    query
                )
            );

        render(data);

        document.getElementById(
            "status"
        ).textContent =
            "✅ Busca finalizada.";

    }catch(error){

        document.getElementById(
            "status"
        ).textContent =
            "❌ " +
            error.message;
    }
}


function brl(value){

    return "R$ " +
        Number(
            value || 0
        ).toLocaleString(
            "pt-BR",
            {
                minimumFractionDigits:2,
                maximumFractionDigits:2
            }
        );
}


function esc(value){

    return String(
        value || ""
    ).replace(
        /[&<>"']/g,
        function(char){

            return {
                "&":"&amp;",
                "<":"&lt;",
                ">":"&gt;",
                '"':"&quot;",
                "'":"&#039;"
            }[char];

        }
    );
}


function render(data){

    const stats =
        document.getElementById(
            "stats"
        );

    stats.innerHTML =
        Object.entries(
            data.stats || {}
        )
        .map(
            ([key,value]) => `
                <div class="stat">
                    ${esc(key)}
                    <b>${esc(value)}</b>
                </div>
            `
        )
        .join("");


    const results =
        document.getElementById(
            "results"
        );

    const offers =
        data.ofertas || [];


    results.innerHTML =
        offers.map(
            (offer,index) => {

                const id =
                    "offer_" +
                    index;

                const coupon =
                    offer.coupon;


                return `
                <div class="offer">

                    <span class="badge">
                        🏆 OPORTUNIDADE #${index+1}
                    </span>

                    ${
                        offer.image
                        ?
                        `<img
                            src="${esc(
                                offer.image
                            )}"
                            loading="lazy"
                        >`
                        :
                        ""
                    }

                    <div class="title">
                        ${esc(
                            offer.title
                        )}
                    </div>


                    ${
                        offer.original_price
                        ?
                        `<div class="old">
                            De:
                            ${brl(
                                offer.original_price
                            )}
                        </div>`
                        :
                        ""
                    }


                    <div class="price">
                        ${brl(
                            offer.price
                        )}
                    </div>


                    ${
                        offer.seller_discount > 0
                        ?
                        `<div class="green">
                            🔥
                            ${offer.seller_discount}%
                            OFF no produto
                        </div>`
                        :
                        ""
                    }


                    ${
                        offer.free_shipping
                        ?
                        `<div class="green">
                            🚚 Frete grátis
                        </div>`
                        :
                        ""
                    }


                    ${
                        coupon
                        ?
                        `
                        <div class="coupon">

                            <b>
                                🎟️ CUPOM:
                                ${esc(
                                    coupon.code
                                )}
                            </b>

                            ${
                                coupon.discount_percent
                                ?
                                `<div>
                                    🔥
                                    ${coupon.discount_percent}%
                                    OFF
                                </div>`
                                :
                                ""
                            }

                            ${
                                coupon.fixed_discount
                                ?
                                `<div>
                                    💰
                                    ${brl(
                                        coupon.fixed_discount
                                    )}
                                    OFF
                                </div>`
                                :
                                ""
                            }

                            ${
                                coupon.min_purchase
                                ?
                                `<div class="small">
                                    Compra mínima:
                                    ${brl(
                                        coupon.min_purchase
                                    )}
                                </div>`
                                :
                                ""
                            }

                            ${
                                coupon.max_discount
                                ?
                                `<div class="small">
                                    Teto do próprio cupom:
                                    ${brl(
                                        coupon.max_discount
                                    )}
                                </div>`
                                :
                                ""
                            }

                            <div>
                                💰 Economia:
                                <b>
                                    ${brl(
                                        offer.coupon_discount
                                    )}
                                </b>
                            </div>

                            <div class="final">
                                💥 Por:
                                ${brl(
                                    offer.coupon_final_price
                                )}
                            </div>

                            <div class="small">
                                ⚠️ Valor estimado.
                                Confirme no checkout.
                            </div>

                        </div>
                        `
                        :
                        `
                        <div class="coupon">

                            <b>
                                ⚠️ Nenhum cupom
                                encontrado para esta oferta.
                            </b>

                            <div class="small">
                                O preço exibido é o preço
                                encontrado no Mercado Livre.
                            </div>

                        </div>
                        `
                    }


                    <br>

                    <a
                        href="${esc(
                            offer.permalink
                        )}"
                        target="_blank"
                    >
                        🛒 Ver produto
                    </a>


                    <input
                        id="link_${id}"
                        placeholder="Cole seu link de afiliado"
                    >


                    <button
                        onclick='gerarAnuncio(
                            "${id}",
                            ${JSON.stringify(
                                offer
                            )}
                        )'
                    >
                        📢 Gerar anúncio
                    </button>


                    <button
                        id="copy_${id}"
                        style="
                            display:none;
                            background:#ff8a00
                        "
                        onclick="
                            copiar('${id}')
                        "
                    >
                        📋 Copiar anúncio
                    </button>


                    <div
                        id="ad_${id}"
                        class="ad"
                    ></div>

                </div>
                `;

            }
        )
        .join("");


    if(!offers.length){

        results.innerHTML =
            "<p>Nenhuma oportunidade encontrada.</p>";
    }
}


async function gerarAnuncio(
    id,
    offer
){

    const link =
        document.getElementById(
            "link_" + id
        ).value;


    const params =
        new URLSearchParams({

            title:
                offer.title,

            price:
                offer.price,

            original_price:
                offer.original_price || "",

            cupom:
                offer.coupon
                ?
                offer.coupon.code
                :
                "",

            product_link:
                offer.permalink,

            affiliate_link:
                link,
        });


    try{

        const data =
            await getJson(
                "/api/gerar-anuncio?" +
                params.toString()
            );


        const box =
            document.getElementById(
                "ad_" + id
            );

        box.style.display =
            "block";

        box.textContent =
            data.anuncio;


        document.getElementById(
            "copy_" + id
        ).style.display =
            "block";

    }catch(error){

        alert(
            error.message
        );
    }
}


function copiar(id){

    const text =
        document.getElementById(
            "ad_" + id
        ).textContent;


    navigator.clipboard.writeText(
        text
    );


    alert(
        "Anúncio copiado!"
    );
}

</script>

</head>


<body>

<div class="container">


<div class="card">

<h1>
🛒 Caçador de Ofertas
</h1>

<p>
Procura produtos a partir de
R$ 69,90 e caça os melhores cupons
disponíveis no Mercado Livre.
</p>

<p class="small">
A prioridade é a maior economia real
em reais. Não existe limite artificial
de desconto no sistema.
</p>


{% if conectado %}

<div class="status">

🟢 Mercado Livre conectado

{% if nickname %}
<br>
<b>
{{ nickname }}
</b>
{% endif %}

</div>

<a href="/mercadolivre/logout">

<button>
Desconectar
</button>

</a>

{% else %}

<a href="/mercadolivre/login">

<button class="login">
🔗 Conectar Mercado Livre
</button>

</a>

{% endif %}

</div>


<div class="card">

<h2>
🔥 Caçar promoções
</h2>


<button
    onclick="cacar('')"
>
🚀 CAÇAR TODAS AS CATEGORIAS
</button>


<div
    class="grid"
    style="margin-top:10px"
>

{% for category in categorias %}

<button
    class="cat"
    onclick="cacar({{ category|tojson }})"
>
{{ category }}
</button>

{% endfor %}

</div>


<p
    id="status"
    class="small"
>
Escolha uma categoria ou cace tudo.
</p>

</div>


<div class="card">

<h2>
🔎 Busca manual
</h2>


<input
    id="q"
    placeholder="Ex: celular, perfume, air fryer..."
>


<button onclick="buscar()">
Procurar
</button>

</div>


<div class="card">

<h2>
📊 Resultado
</h2>

<div
    id="stats"
    class="stats"
>
</div>

</div>


<div class="card">

<h2>
🏆 Melhores oportunidades
</h2>

<p class="small">
1 produto = 1 oportunidade.
O sistema compara os vendedores do
mesmo produto e mantém a combinação
com a maior economia encontrada.
</p>

<div id="results">

<p>
Faça uma busca para começar.
</p>

</div>

</div>


<div class="card">

<a
    href="/api/cupons?atualizar=1"
    target="_blank"
>
🎟️ Consultar cupons encontrados
</a>

<br>
<br>

<a
    href="/mercadolivre/diagnostico"
    target="_blank"
>
🧪 Diagnóstico Mercado Livre
</a>

<br>
<br>

<a
    href="/health"
    target="_blank"
>
❤️ Health
</a>

</div>


</div>

</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    data = get_tokens()

    return render_template_string(
        HTML,

        conectado=
            bool(
                get_access_token()
            ),

        nickname=
            data.get(
                "nickname"
            )
            if data
            else None,

        categorias=
            list(
                CATALOG.keys()
            ),
    )


# ============================================================
# ERROS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "erro":
            "Rota não encontrada.",
        "rota":
            request.path,
    }), 404


@app.errorhandler(500)
def internal_error(error):

    return jsonify({
        "erro":
            "Erro interno.",
        "detalhes":
            str(error),
    }), 500


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
        port=port,
        debug=False
    )