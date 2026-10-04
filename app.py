import os
import sqlite3
import secrets
import hashlib
import base64
import time
import re
import html as html_lib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, quote

import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "chave-cacador-ofertas")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"

# ============================================================
# WHATSAPP BOT
# ============================================================

WHATSAPP_BOT_URL = os.getenv(
    "WHATSAPP_BOT_URL",
    "https://whatsapp-bot-production-c647.up.railway.app"
).strip().rstrip("/")
WHATSAPP_BOT_KEY = os.getenv("WHATSAPP_BOT_KEY", "").strip()

ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"
SITE_ID = "MLB"
DB_FILE = "ofertas.db"
COUPONS_URL = "https://www.mercadolivre.com.br/l/promocoes"

COUPON_SOURCE_URLS = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/ofertas/cupons",
]

MIN_PRODUCT_PRICE = 69.90

# ============================================================
# CATÁLOGO AUTOMÁTICO
# ============================================================

CATALOG = {
    "📱 Celulares": [
        "smartphone", "iphone", "samsung galaxy", "motorola moto",
        "xiaomi redmi", "poco smartphone", "realme smartphone"
    ],
    "🌸 Perfumes": [
        "perfume masculino", "perfume feminino", "perfume importado",
        "perfume nacional", "perfume eau de parfum"
    ],
    "🏋️ Academia": [
        "roupa academia masculina", "roupa academia feminina",
        "camiseta academia", "short academia", "legging academia",
        "tenis academia", "tenis corrida", "tenis treino",
        "whey protein", "creatina", "pre treino", "suplementos"
    ],
    "🔧 Ferramentas": [
        "furadeira", "parafusadeira", "esmerilhadeira",
        "kit ferramentas", "maleta ferramentas", "serra",
        "chave de impacto"
    ],
    "🎧 Eletrônicos": [
        "fone bluetooth", "headset", "smartwatch", "tablet",
        "caixa de som bluetooth", "camera digital", "power bank"
    ],
    "🏠 Casa": [
        "aspirador de pó", "liquidificador", "cafeteira",
        "air fryer", "ventilador", "ferro de passar"
    ],
    "🍳 Cozinha": [
        "air fryer", "panela elétrica", "jogo de panelas",
        "cafeteira", "liquidificador", "sandwichera"
    ],
    "🚗 Automotivo": [
        "compressor automotivo", "aspirador automotivo",
        "suporte celular carro", "carregador automotivo",
        "ferramentas automotivas", "tapete automotivo"
    ],
    "👕 Moda": [
        "tenis masculino", "tenis feminino", "mochila",
        "relogio masculino", "bolsa feminina", "oculos de sol"
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
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            item_id TEXT,
            title TEXT,
            permalink TEXT,
            price REAL,
            original_price REAL,
            discount REAL,
            seller_id TEXT,
            image TEXT,
            category_id TEXT,
            category_name TEXT,
            condition TEXT,
            listing_type_id TEXT,
            free_shipping INTEGER DEFAULT 0,
            shipping_cost REAL,
            total_price REAL,
            relevance_score REAL DEFAULT 0,
            affiliate_link TEXT,
            extra_earnings REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            valid_until TEXT,
            source_url TEXT,
            conditions TEXT,
            active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_publicacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT UNIQUE,
            last_price REAL NOT NULL,
            last_permalink TEXT,
            last_title TEXT,
            published_count INTEGER DEFAULT 1,
            last_published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Compatibilidade com bancos criados pelas versões anteriores.
    try:
        conn.execute(
            "ALTER TABLE cupons ADD COLUMN fixed_discount REAL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute(
            "ALTER TABLE cupons ADD COLUMN usage_limit INTEGER"
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


init_db()

# ============================================================
# UTILIDADES
# ============================================================

def json_safe(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v

    if isinstance(v, dict):
        return {
            str(k): json_safe(x)
            for k, x in v.items()
        }

    if isinstance(v, list):
        return [
            json_safe(x)
            for x in v
        ]

    return str(v)


def brl(v):
    try:
        return (
            f"R$ {float(v):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


def norm(s):
    if not s:
        return ""

    s = str(s).lower()

    trans = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüç",
        "aaaaaeeeeiiiiooooouuuuc"
    )

    s = s.translate(trans)

    return re.sub(
        r"\s+",
        " ",
        re.sub(r"[^a-z0-9\s]+", " ", s)
    ).strip()


def discount(price, original):
    try:
        p = float(price)
        o = float(original)

        return (
            round((1 - p / o) * 100, 2)
            if o > p > 0
            else 0
        )

    except Exception:
        return 0


def total(price, shipping):
    try:
        return round(
            float(price) + float(shipping or 0),
            2
        )
    except Exception:
        return None


def model_name(title):
    if not title:
        return "Produto"

    t = re.sub(
        r"\b(novo|original|oficial|promoção|frete grátis)\b",
        "",
        str(title),
        flags=re.I
    )

    return re.sub(
        r"\s+",
        " ",
        t
    ).strip()


def specs(title):
    if not title:
        return []

    t = str(title)
    out = []

    for x in re.findall(
        r"\b\d+(?:GB|TB)\b",
        t,
        re.I
    ):
        x = x.upper()

        if x not in out:
            out.append(x)

    for x in re.findall(
        r"\b\d+\s*GB\s*(?:RAM|MEMORIA|DE MEMORIA)\b",
        t,
        re.I
    ):
        x = re.sub(
            r"\s+",
            " ",
            x.upper()
        )

        if x not in out:
            out.append(x)

    for x in re.findall(
        r"\b(?:2G|3G|4G|5G)\b",
        t,
        re.I
    ):
        x = x.upper()

        if x not in out:
            out.append(x)

    for label, pattern in [
        ("Dual SIM", r"dual\s*sim"),
        ("NFC", r"\bnfc\b")
    ]:
        if re.search(
            pattern,
            t,
            re.I
        ) and label not in out:
            out.append(label)

    return out


# ============================================================
# PERFIS / RELEVÂNCIA
# ============================================================

PROFILES = {
    "celular": {
        "strong": [
            "smartphone",
            "iphone",
            "galaxy",
            "samsung",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "realme"
        ],
        "bad": [
            "capa",
            "capinha",
            "pelicula",
            "suporte",
            "ventosa",
            "carregador",
            "cabo",
            "adaptador",
            "bateria",
            "case",
            "holder"
        ]
    },

    "perfume": {
        "strong": [
            "perfume",
            "eau de parfum",
            "eau de toilette",
            "parfum"
        ],
        "bad": [
            "frasco vazio",
            "decant",
            "amostra",
            "porta perfume",
            "refil vazio"
        ]
    },

    "academia": {
        "strong": [
            "roupa",
            "camiseta",
            "short",
            "legging",
            "tenis",
            "corrida",
            "treino",
            "whey",
            "creatina",
            "pre treino",
            "suplemento",
            "suplementos"
        ],
        "bad": [
            "halter",
            "halteres",
            "anilha",
            "barra",
            "banco musculacao",
            "caneleira",
            "elastico",
            "adesivo",
            "capa",
            "suporte",
            "peca de reposicao"
        ]
    },

    "ferramenta": {
        "strong": [
            "furadeira",
            "parafusadeira",
            "esmerilhadeira",
            "ferramenta",
            "serra",
            "impacto"
        ],
        "bad": [
            "broca avulsa",
            "peca",
            "carvao",
            "bateria avulsa",
            "capa"
        ]
    },
}


def is_requested_product(title, query, category=None):
    """Filtra acessórios/peças e garante que o título pertence à categoria pedida."""

    t = norm(title)
    qn = norm(query)

    cat = (
        category
        or query_category(query)
        or _demand_category_from_text(title)
    )

    generic_bad = [
        "capa",
        "capinha",
        "pelicula",
        "película",
        "suporte",
        "holder",
        "cabo",
        "adaptador",
        "adesivo",
        "peca de reposicao",
        "peca avulsa",
        "refil vazio",
        "frasco vazio",
        "amostra",
        "decant",
        "miniatura",
        "pingente",
        "chaveiro",
        "brinde",
        "molde",
        "manual digital"
    ]

    category_rules = {
        "📱 Celulares": (
            [
                "smartphone",
                "celular",
                "iphone",
                "galaxy",
                "samsung",
                "motorola",
                "xiaomi",
                "redmi",
                "poco",
                "realme"
            ],
            generic_bad + [
                "carregador",
                "bateria avulsa",
                "case"
            ]
        ),

        "🌸 Perfumes": (
            [
                "perfume",
                "parfum",
                "eau de parfum",
                "eau de toilette",
                "fragrancia"
            ],
            generic_bad + [
                "porta perfume",
                "estojo vazio"
            ]
        ),

        "🏋️ Academia": (
            [
                "academia",
                "treino",
                "corrida",
                "legging",
                "camiseta",
                "short",
                "tenis",
                "whey",
                "creatina",
                "suplemento",
                "pre treino"
            ],
            [
                "capa",
                "adesivo",
                "suporte",
                "peca de reposicao",
                "broca"
            ]
        ),

        "🔧 Ferramentas": (
            [
                "furadeira",
                "parafusadeira",
                "esmerilhadeira",
                "ferramenta",
                "serra",
                "chave de impacto",
                "impacto"
            ],
            [
                "broca avulsa",
                "carvao",
                "bateria avulsa",
                "capa",
                "peca de reposicao"
            ]
        ),

        "🎧 Eletrônicos": (
            [
                "fone",
                "headset",
                "smartwatch",
                "tablet",
                "caixa de som",
                "camera",
                "power bank"
            ],
            [
                "cabo",
                "case",
                "capa",
                "pelicula",
                "suporte",
                "peca de reposicao"
            ]
        ),

        "🏠 Casa": (
            [
                "aspirador",
                "liquidificador",
                "cafeteira",
                "air fryer",
                "ventilador",
                "ferro de passar"
            ],
            [
                "peca",
                "refil",
                "capa",
                "suporte",
                "acessorio"
            ]
        ),

        "🍳 Cozinha": (
            [
                "air fryer",
                "panela eletrica",
                "jogo de panelas",
                "cafeteira",
                "liquidificador",
                "sanduicheira"
            ],
            [
                "peca",
                "refil",
                "capa",
                "suporte",
                "acessorio"
            ]
        ),

        "🚗 Automotivo": (
            [
                "compressor automotivo",
                "aspirador automotivo",
                "carregador automotivo",
                "ferramenta automotiva",
                "tapete automotivo"
            ],
            [
                "capa de celular",
                "pelicula",
                "brinde",
                "adesivo"
            ]
        ),

        "👕 Moda": (
            [
                "tenis",
                "mochila",
                "relogio",
                "bolsa",
                "oculos",
                "camiseta",
                "vestido"
            ],
            [
                "capa",
                "pelicula",
                "suporte",
                "peca de reposicao"
            ]
        ),
    }
        }

    if cat in category_rules:
        good, bad = category_rules[cat]

        if any(
            word in t
            for word in bad
        ):
            return False

        if not any(
            word in t
            for word in good
        ):
            return False

    # Se a busca é muito específica, pelo menos uma
    # palavra relevante da consulta precisa aparecer.
    query_words = [
        w for w in qn.split()
        if len(w) >= 4
    ]

    if query_words:
        relevant = [
            w for w in query_words
            if w in t
        ]

        if not relevant:
            # Permite categorias amplas, mas rejeita
            # resultados completamente desconectados.
            if cat not in (
                "👕 Moda",
                "🏠 Casa",
                "🍳 Cozinha",
                "🎧 Eletrônicos",
            ):
                return False

    return True


def query_category(query):
    q = norm(query)

    for category, queries in CATALOG.items():

        for item in queries:

            ni = norm(item)

            if ni and ni in q:
                return category

    return None


def _demand_category_from_text(text):
    t = norm(text)

    checks = [
        (
            "📱 Celulares",
            [
                "iphone",
                "smartphone",
                "celular",
                "galaxy",
                "samsung",
                "motorola",
                "xiaomi",
                "redmi",
                "poco"
            ]
        ),
        (
            "🌸 Perfumes",
            [
                "perfume",
                "parfum",
                "fragrancia"
            ]
        ),
        (
            "🏋️ Academia",
            [
                "academia",
                "whey",
                "creatina",
                "legging",
                "tenis",
                "treino"
            ]
        ),
        (
            "🔧 Ferramentas",
            [
                "furadeira",
                "parafusadeira",
                "esmerilhadeira",
                "ferramenta",
                "serra"
            ]
        ),
        (
            "🎧 Eletrônicos",
            [
                "fone",
                "headset",
                "smartwatch",
                "tablet",
                "camera",
                "power bank"
            ]
        ),
        (
            "🏠 Casa",
            [
                "aspirador",
                "liquidificador",
                "ventilador",
                "ferro de passar"
            ]
        ),
        (
            "🍳 Cozinha",
            [
                "air fryer",
                "panela",
                "cafeteira",
                "sanduicheira"
            ]
        ),
        (
            "🚗 Automotivo",
            [
                "automotivo",
                "carro",
                "compressor automotivo",
                "tapete automotivo"
            ]
        ),
        (
            "👕 Moda",
            [
                "camiseta",
                "short",
                "mochila",
                "bolsa",
                "oculos",
                "relogio",
                "tenis"
            ]
        ),
    ]

    for category, words in checks:

        if any(
            word in t
            for word in words
        ):
            return category

    return None


def profile_for_category(category):
    c = norm(category)

    if "celular" in c:
        return PROFILES["celular"]

    if "perfume" in c:
        return PROFILES["perfume"]

    if "academia" in c:
        return PROFILES["academia"]

    if "ferrament" in c:
        return PROFILES["ferramenta"]

    return {
        "strong": [],
        "bad": []
    }


def relevance_score(
    title,
    query="",
    category=None,
    discount_value=0,
    price=0,
    free_shipping=False
):
    t = norm(title)
    q = norm(query)

    score = 0

    cat = (
        category
        or query_category(query)
        or _demand_category_from_text(title)
    )

    profile =
        profile_for_category(cat)

    for word in profile.get(
        "strong",
        []
    ):

        if norm(word) in t:
            score += 12

    for word in profile.get(
        "bad",
        []
    ):

        if norm(word) in t:
            score -= 35

    if q:

        q_words = [
            x for x in q.split()
            if len(x) >= 4
        ]

        for word in q_words:

            if word in t:
                score += 7

    try:

        d = float(
            discount_value or 0
        )

        if d >= 60:
            score += 35

        elif d >= 50:
            score += 28

        elif d >= 40:
            score += 22

        elif d >= 30:
            score += 15

        elif d >= 20:
            score += 8

    except Exception:
        pass

    if free_shipping:
        score += 8

    try:

        p = float(price or 0)

        if p >= 69.90:
            score += 4

        if p >= 100:
            score += 3

    except Exception:
        pass

    return round(
        score,
        2
    )


# ============================================================
# OAUTH MERCADO LIVRE
# ============================================================

def get_token_row():

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM oauth_tokens
        WHERE id=1
        """
    ).fetchone()

    conn.close()

    return row


def save_tokens(
    access_token,
    refresh_token,
    expires_in=None,
    user_id=None,
    nickname=None
):

    expires_at = int(
        time.time()
        + int(expires_in or 0)
    )

    conn = get_db()

    conn.execute(
        """
        INSERT INTO oauth_tokens
        (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES
        (1, ?, ?, ?, ?, ?)

        ON CONFLICT(id)
        DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token,
            expires_at=excluded.expires_at,
            user_id=excluded.user_id,
            nickname=excluded.nickname
        """,
        (
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
    )

    conn.commit()
    conn.close()


def refresh_access_token():

    row =
        get_token_row()

    if not row:
        return None

    refresh_token =
        row["refresh_token"]

    if not refresh_token:
        return None

    data = {
        "grant_type":
            "refresh_token",

        "client_id":
            ML_CLIENT_ID,

        "client_secret":
            ML_CLIENT_SECRET,

        "refresh_token":
            refresh_token,
    }

    response =
        requests.post(
            ML_TOKEN,
            data=data,
            timeout=30
        )

    if not response.ok:

        print(
            "[ML] Falha ao renovar token:",
            response.status_code,
            response.text[:500]
        )

        return None

    payload =
        response.json()

    save_tokens(
        payload.get(
            "access_token"
        ),
        payload.get(
            "refresh_token",
            refresh_token
        ),
        payload.get(
            "expires_in",
            0
        ),
        row["user_id"],
        row["nickname"]
    )

    return payload.get(
        "access_token"
    )


def get_access_token():

    row =
        get_token_row()

    if not row:
        return None

    access_token =
        row["access_token"]

    expires_at =
        int(
            row["expires_at"]
            or 0
        )

    # Renova com antecedência
    if (
        access_token
        and expires_at >
            int(time.time()) + 120
    ):
        return access_token

    return refresh_access_token()


def ml_headers():

    token =
        get_access_token()

    if not token:
        return {}

    return {
        "Authorization":
            f"Bearer {token}",

        "Accept":
            "application/json",

        "Content-Type":
            "application/json",
    }


# ============================================================
# LOGIN MERCADO LIVRE
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado.",
            500
        )

    params = {
        "response_type":
            "code",

        "client_id":
            ML_CLIENT_ID,

        "redirect_uri":
            ML_REDIRECT_URI,
    }

    url =
        ML_AUTH + "?" + urlencode(
            params
        )

    return redirect(url)


@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    code =
        request.args.get(
            "code",
            ""
        ).strip()

    if not code:

        return (
            "Código de autorização não recebido.",
            400
        )

    data = {
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
    }

    response =
        requests.post(
            ML_TOKEN,
            data=data,
            timeout=30
        )

    if not response.ok:

        return (
            f"""
            <h2>Erro ao conectar ao Mercado Livre</h2>
            <pre>{html_lib.escape(response.text[:3000])}</pre>
            """,
            500
        )

    payload =
        response.json()

    access_token =
        payload.get(
            "access_token"
        )

    refresh_token =
        payload.get(
            "refresh_token"
        )

    if not access_token:

        return (
            "Mercado Livre não retornou access_token.",
            500
        )

    user_id = None
    nickname = None

    try:

        me =
            requests.get(
                f"{ML_API}/users/me",
                headers={
                    "Authorization":
                        f"Bearer {access_token}"
                },
                timeout=20
            )

        if me.ok:

            me_data =
                me.json()

            user_id =
                str(
                    me_data.get(
                        "id"
                    ) or ""
                )

            nickname =
                me_data.get(
                    "nickname"
                )

    except Exception as exc:

        print(
            "[ML] Erro ao buscar usuário:",
            repr(exc)
        )

    save_tokens(
        access_token,
        refresh_token,
        payload.get(
            "expires_in",
            0
        ),
        user_id,
        nickname
    )

    return redirect(
        "/"
    )


@app.route(
    "/mercadolivre/status"
)
def mercadolivre_status():

    row =
        get_token_row()

    connected =
        bool(
            row
            and row["access_token"]
        )

    return jsonify({
        "connected":
            connected,

        "user_id":
            row["user_id"]
            if row
            else None,

        "nickname":
            row["nickname"]
            if row
            else None,

        "expires_at":
            row["expires_at"]
            if row
            else None
    })


# ============================================================
# API MERCADO LIVRE
# ============================================================

def ml_get(
    endpoint,
    params=None,
    timeout=25
):

    headers =
        ml_headers()

    if not headers:

        return None, {
            "error":
                "Mercado Livre não conectado."
        }

    url =
        endpoint

    if not url.startswith("http"):

        url =
            ML_API.rstrip("/")
            + "/"
            + endpoint.lstrip("/")

    try:

        response =
            requests.get(
                url,
                headers=headers,
                params=params,
                timeout=timeout
            )

    except requests.RequestException as exc:

        return None, {
            "error":
                str(exc)
        }

    if response.status_code == 401:

        refreshed =
            refresh_access_token()

        if refreshed:

            headers =
                ml_headers()

            try:

                response =
                    requests.get(
                        url,
                        headers=headers,
                        params=params,
                        timeout=timeout
                    )

            except requests.RequestException as exc:

                return None, {
                    "error":
                        str(exc)
                }

    if not response.ok:

        return None, {
            "status":
                response.status_code,

            "error":
                response.text[:1000]
        }

    try:

        return response.json(), None

    except ValueError:

        return None, {
            "status":
                response.status_code,

            "error":
                "Resposta inválida do Mercado Livre."
        }


def ml_post(
    endpoint,
    data=None,
    timeout=25
):

    headers =
        ml_headers()

    if not headers:

        return None, {
            "error":
                "Mercado Livre não conectado."
        }

    url =
        endpoint

    if not url.startswith("http"):

        url =
            ML_API.rstrip("/")
            + "/"
            + endpoint.lstrip("/")

    try:

        response =
            requests.post(
                url,
                headers=headers,
                json=data or {},
                timeout=timeout
            )

    except requests.RequestException as exc:

        return None, {
            "error":
                str(exc)
        }

    if response.status_code == 401:

        refreshed =
            refresh_access_token()

        if refreshed:

            headers =
                ml_headers()

            try:

                response =
                    requests.post(
                        url,
                        headers=headers,
                        json=data or {},
                        timeout=timeout
                    )

            except requests.RequestException as exc:

                return None, {
                    "error":
                        str(exc)
                }

    if not response.ok:

        return None, {
            "status":
                response.status_code,

            "error":
                response.text[:1000]
        }

    try:

        return response.json(), None

    except ValueError:

        return None, {
            "status":
                response.status_code,

            "error":
                "Resposta inválida do Mercado Livre."
        }


# ============================================================
# NORMALIZAÇÃO DE PRODUTO
# ============================================================

def normalize_item(
    item,
    category=None,
    query=""
):

    if not isinstance(
        item,
        dict
    ):
        return None

    item_id =
        str(
            item.get(
                "id"
            ) or ""
        ).strip()

    if not item_id:
        return None

    title =
        str(
            item.get(
                "title"
            ) or ""
        ).strip()

    if not title:
        return None

    price =
        item.get(
            "price"
        )

    original_price =
        item.get(
            "original_price"
        )

    try:

        price =
            float(price)

    except Exception:

        return None

    if original_price:

        try:
            original_price =
                float(
                    original_price
                )

        except Exception:
            original_price =
                price

    else:

        original_price =
            price

    if price <= 0:
        return None

    if price < MIN_PRODUCT_PRICE:
        return None

    d =
        discount(
            price,
            original_price
        )

    shipping =
        (
            item.get(
                "shipping"
            )
            or {}
        )

    free_shipping =
        bool(
            shipping.get(
                "free_shipping"
            )
        )

    shipping_cost =
        shipping.get(
            "cost"
        )

    if shipping_cost is None:
        shipping_cost = 0

    try:
        shipping_cost =
            float(
                shipping_cost
                or 0
            )
    except Exception:
        shipping_cost = 0

    total_price =
        total(
            price,
            shipping_cost
        )

    pictures =
        item.get(
            "thumbnail"
        ) or ""

    permalink =
        item.get(
            "permalink"
        ) or ""

    seller =
        item.get(
            "seller"
        ) or {}

    seller_id =
        str(
            seller.get(
                "id"
            ) or ""
        )

    category_id =
        str(
            item.get(
                "category_id"
            ) or ""
        )

    condition =
        str(
            item.get(
                "condition"
            ) or ""
        )

    listing_type_id =
        str(
            item.get(
                "listing_type_id"
            ) or ""
        )

    result = {
        "id":
            item_id,

        "product_id":
            item_id,

        "item_id":
            item_id,

        "title":
            title,

        "model":
            model_name(
                title
            ),

        "permalink":
            permalink,

        "price":
            price,

        "original_price":
            original_price,

        "discount":
            d,

        "seller_id":
            seller_id,

        "image":
            pictures,

        "thumbnail":
            pictures,

        "category_id":
            category_id,

        "category":
            category,

        "category_name":
            category,

        "condition":
            condition,

        "listing_type_id":
            listing_type_id,

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost,

        "total_price":
            total_price,

        "affiliate_link":
            permalink,

        "specs":
            specs(title),

        "relevance_score":
            relevance_score(
                title,
                query,
                category,
                d,
                price,
                free_shipping
            ),
    }

    return result
    def sync_coupons():
    """Caça cupons em várias áreas públicas do Mercado Livre.

    Não existe um teto artificial de desconto no programa. Todos os cupons
    encontrados são considerados. O único limite aplicado ao cálculo é o
    próprio limite/condição informado pelo Mercado Livre para aquele cupom.
    """
    headers = {
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept-Language":"pt-BR,pt;q=0.9,en;q=0.8",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    urls = [
        COUPONS_URL,
        "https://www.mercadolivre.com.br/ofertas",
        "https://www.mercadolivre.com.br/cupons",
    ]

    found = {}

    for url in urls:
        try:
            r = requests.get(
                url,
                headers=headers,
                timeout=30
            )

            if r.status_code != 200:
                print(
                    "[CUPONS] HTTP",
                    r.status_code,
                    url
                )
                continue

            text = normalize_coupon_html(
                r.text
            )

            for code, block in coupon_blocks(text):

                coupon =
                    parse_coupon_block(
                        code,
                        block,
                        url
                    )

                # Mantém a ocorrência mais completa
                # quando o mesmo código aparece em mais
                # de uma página.
                old = found.get(code)

                if old is None:
                    found[code] = coupon
                else:
                    old_len =
                        len(
                            old.get(
                                "description",
                                ""
                            )
                        )

                    new_len =
                        len(
                            coupon.get(
                                "description",
                                ""
                            )
                        )

                    if new_len > old_len:
                        found[code] = coupon

        except Exception as e:
            print(
                "[CUPONS] Erro:",
                url,
                repr(e)
            )

    coupons = list(
        found.values()
    )

    # Salva no banco
    conn = get_db()

    for coupon in coupons:

        conn.execute(
            """
            INSERT INTO coupons
            (
                code,
                description,
                discount_percent,
                fixed_discount,
                min_purchase,
                max_discount,
                usage_limit,
                source_url,
                conditions,
                updated_at
            )
            VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)

            ON CONFLICT(code)
            DO UPDATE SET
                description=excluded.description,
                discount_percent=excluded.discount_percent,
                fixed_discount=excluded.fixed_discount,
                min_purchase=excluded.min_purchase,
                max_discount=excluded.max_discount,
                usage_limit=excluded.usage_limit,
                source_url=excluded.source_url,
                conditions=excluded.conditions,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                coupon["code"],
                coupon["description"],
                coupon["discount_percent"],
                coupon["fixed_discount"],
                coupon["min_purchase"],
                coupon["max_discount"],
                coupon["usage_limit"],
                coupon["source_url"],
                coupon["conditions"],
            )
        )

    conn.commit()
    conn.close()

    print(
        "[CUPONS] Sincronizados:",
        len(coupons)
    )

    return coupons


def get_coupons():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM coupons
        ORDER BY
            updated_at DESC,
            id DESC
        """
    ).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


def best_coupon_for_product(
    price,
    title="",
    category=None
):

    try:
        price =
            float(price)
    except Exception:
        return None

    if price <= 0:
        return None

    coupons =
        get_coupons()

    if not coupons:
        return None

    candidates = []

    for coupon in coupons:

        try:

            minimum =
                float(
                    coupon.get(
                        "min_purchase"
                    )
                    or 0
                )

        except Exception:

            minimum = 0

        if price < minimum:
            continue

        pct =
            coupon.get(
                "discount_percent"
            )

        fixed =
            coupon.get(
                "fixed_discount"
            )

        maximum =
            coupon.get(
                "max_discount"
            )

        try:
            pct =
                float(
                    pct
                    or 0
                )
        except Exception:
            pct = 0

        try:
            fixed =
                float(
                    fixed
                    or 0
                )
        except Exception:
            fixed = 0

        try:
            maximum =
                float(
                    maximum
                    or 0
                )
        except Exception:
            maximum = 0

        discount_value = 0

        if pct > 0:
            discount_value =
                price * pct / 100

        if fixed > 0:
            discount_value =
                max(
                    discount_value,
                    fixed
                )

        if maximum > 0:
            discount_value =
                min(
                    discount_value,
                    maximum
                )

        if discount_value <= 0:
            continue

        final_price =
            max(
                0,
                price - discount_value
            )

        if final_price < MIN_PRODUCT_PRICE:
            continue

        candidates.append(
            {
                "code":
                    coupon.get(
                        "code"
                    ),

                "discount":
                    round(
                        discount_value,
                        2
                    ),

                "final_price":
                    round(
                        final_price,
                        2
                    ),

                "discount_percent":
                    pct,

                "fixed_discount":
                    fixed,

                "min_purchase":
                    minimum,

                "max_discount":
                    maximum,

                "usage_limit":
                    coupon.get(
                        "usage_limit"
                    ),

                "description":
                    coupon.get(
                        "description"
                    ),

                "conditions":
                    coupon.get(
                        "conditions"
                    ),

                "source_url":
                    coupon.get(
                        "source_url"
                    ),
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["discount"],
            -x["final_price"]
        ),
        reverse=True
    )

    return candidates[0]


# ============================================================
# BUSCA DE PRODUTOS
# ============================================================

def search_products_direct(
    query,
    limit=50,
    category_id=None
):
    """
    Busca produtos reais do marketplace.

    A origem principal é /sites/MLB/search.
    O endpoint /products/search não é usado como
    fonte principal porque retorna catálogo e pode
    não representar a oferta real disponível.
    """

    query =
        str(
            query or ""
        ).strip()

    if not query:
        return []

    params = {
        "q":
            query,

        "limit":
            min(
                max(
                    int(limit or 50),
                    1
                ),
                50
            ),

        "offset":
            0,
    }

    if category_id:
        params[
            "category"
        ] = category_id

    data, status, raw =
        ml_get(
            f"/sites/{SITE_ID}/search",
            params
        )

    if status != 200:
        print(
            "[ML SEARCH] Falha:",
            status,
            raw
        )
        return []

    if not isinstance(
        data,
        dict
    ):
        return []

    results =
        data.get(
            "results",
            []
        )

    if not isinstance(
        results,
        list
    ):
        return []

    return results


def _search_category(
    category,
    limit=50
):
    """
    Busca uma categoria usando várias consultas
    reais de produto.

    O objetivo é ampliar a quantidade de candidatos
    sem depender do endpoint de catálogo.
    """

    queries =
        CATALOG.get(
            category,
            []
        )

    if not queries:
        queries = [
            category
        ]

    results = []

    seen = set()

    for query in queries:

        items =
            search_products_direct(
                query,
                limit=limit
            )

        for item in items:

            item_id =
                str(
                    item.get(
                        "id"
                    ) or ""
                )

            if not item_id:
                continue

            if item_id in seen:
                continue

            seen.add(
                item_id
            )

            results.append(
                item
            )

            if len(results) >= limit:
                return results

    return results


def search_products(
    query,
    category=None,
    limit=50
):

    if category:

        raw =
            _search_category(
                category,
                limit
            )

    else:

        raw =
            search_products_direct(
                query,
                limit
            )

    products = []

    for item in raw:

        title =
            str(
                item.get(
                    "title"
                ) or ""
            ).strip()

        if not title:
            continue

        if not valid_product_title(
            title
        ):
            continue

        price =
            item.get(
                "price"
            )

        try:
            price =
                float(price)
        except Exception:
            continue

        if not valid_catalog_price(
            price
        ):
            continue

        products.append(
            item
        )

    return products


# ============================================================
# OFERTAS
# ============================================================

def calculate_offer(
    price,
    original_price=None,
    coupon=None
):

    try:
        price =
            float(price)
    except Exception:
        return {
            "price": 0,
            "original_price": 0,
            "discount": 0,
            "coupon_discount": 0,
            "final_price": 0
        }

    try:

        original =
            float(
                original_price
                or price
            )

    except Exception:

        original = price

    if original < price:
        original = price

    base_discount =
        max(
            0,
            original - price
        )

    coupon_discount = 0
    coupon_code = None

    if coupon:

        try:
            coupon_discount =
                float(
                    coupon.get(
                        "discount"
                    )
                    or 0
                )
        except Exception:
            coupon_discount = 0

        coupon_code =
            coupon.get(
                "code"
            )

    final_price =
        max(
            0,
            price - coupon_discount
        )

    total_discount =
        max(
            0,
            original - final_price
        )

    discount_percent = 0

    if original > 0:

        discount_percent =
            round(
                (
                    total_discount
                    / original
                ) * 100,
                2
            )

    return {
        "price":
            round(
                price,
                2
            ),

        "original_price":
            round(
                original,
                2
            ),

        "discount":
            round(
                total_discount,
                2
            ),

        "discount_percent":
            discount_percent,

        "coupon_discount":
            round(
                coupon_discount,
                2
            ),

        "coupon_code":
            coupon_code,

        "final_price":
            round(
                final_price,
                2
            ),
    }


def product_from_item(
    item,
    category=None,
    query=""
):

    if not isinstance(
        item,
        dict
    ):
        return None

    item_id =
        str(
            item.get(
                "id"
            )
            or item.get(
                "item_id"
            )
            or ""
        ).strip()

    if not item_id:
        return None

    title =
        str(
            item.get(
                "title"
            ) or ""
        ).strip()

    if not title:
        return None

    try:
        price =
            float(
                item.get(
                    "price"
                )
            )
    except Exception:
        return None

    if not valid_catalog_price(
        price
    ):
        return None

    original_price =
        item.get(
            "original_price"
        )

    try:

        original_price =
            float(
                original_price
            ) if original_price is not None else price

    except Exception:

        original_price = price

    if original_price < price:
        original_price = price

    shipping =
        item.get(
            "shipping"
        ) or {}

    if not isinstance(
        shipping,
        dict
    ):
        shipping = {}

    free_shipping =
        bool(
            shipping.get(
                "free_shipping"
            )
        )

    shipping_cost =
        shipping.get(
            "cost"
        )

    try:
        shipping_cost =
            float(
                shipping_cost
                or 0
            )
    except Exception:
        shipping_cost = 0

    if free_shipping:
        shipping_cost = 0

    permalink =
        str(
            item.get(
                "permalink"
            ) or ""
        ).strip()

    thumbnail =
        str(
            item.get(
                "thumbnail"
            ) or ""
        ).strip()

    pictures =
        item.get(
            "pictures"
        ) or []

    image =
        thumbnail

    if (
        not image
        and isinstance(
            pictures,
            list
        )
        and pictures
    ):

        first =
            pictures[0]

        if isinstance(
            first,
            dict
        ):

            image =
                first.get(
                    "secure_url"
                ) or first.get(
                    "url"
                ) or ""

    coupon =
        best_coupon_for_product(
            price,
            title,
            category
        )

    offer =
        calculate_offer(
            price,
            original_price,
            coupon
        )

    return {
        "id":
            item_id,

        "item_id":
            item_id,

        "title":
            title,

        "price":
            price,

        "original_price":
            original_price,

        "discount":
            offer[
                "discount"
            ],

        "discount_percent":
            offer[
                "discount_percent"
            ],

        "coupon":
            coupon,

        "coupon_code":
            offer[
                "coupon_code"
            ],

        "coupon_discount":
            offer[
                "coupon_discount"
            ],

        "final_price":
            offer[
                "final_price"
            ],

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost,

        "permalink":
            permalink,

        "affiliate_link":
            permalink,

        "image":
            image,

        "thumbnail":
            thumbnail,

        "category":
            category,

        "category_id":
            item.get(
                "category_id"
            ),

        "seller_id":
            item.get(
                "seller_id"
            ),

        "condition":
            item.get(
                "condition"
            ),

        "listing_type_id":
            item.get(
                "listing_type_id"
            ),

        "query":
            query,
    }


def generate_offers(
    query,
    category=None,
    limit=20
):

    raw =
        search_products(
            query,
            category,
            limit
        )

    offers = []

    seen = set()

    for item in raw:

        product =
            product_from_item(
                item,
                category,
                query
            )

        if not product:
            continue

        item_id =
            product.get(
                "item_id"
            )

        if item_id in seen:
            continue

        seen.add(
            item_id
        )

        # Só considera oferta se houver
        # algum desconto real.
        if (
            product[
                "discount_percent"
            ] <= 0
            and not product.get(
                "coupon"
            )
        ):
            continue

        offers.append(
            product
        )

    offers.sort(
        key=lambda x: (
            x.get(
                "discount_percent",
                0
            ),
            x.get(
                "coupon_discount",
                0
            )
        ),
        reverse=True
    )

    return offers[:limit]
    # ============================================================
# FILTROS E ORDENAÇÃO DE OFERTAS
# ============================================================

def filter_offers(
    offers,
    min_discount=0,
    min_price=None,
    max_price=None,
    category=None
):

    filtered = []

    try:
        min_discount =
            float(
                min_discount
                or 0
            )
    except Exception:
        min_discount = 0

    if min_price is not None:
        try:
            min_price =
                float(
                    min_price
                )
        except Exception:
            min_price = None

    if max_price is not None:
        try:
            max_price =
                float(
                    max_price
                )
        except Exception:
            max_price = None

    for offer in offers:

        if not isinstance(
            offer,
            dict
        ):
            continue

        discount_percent =
            float(
                offer.get(
                    "discount_percent",
                    0
                )
                or 0
            )

        if (
            discount_percent
            < min_discount
        ):
            continue

        final_price =
            offer.get(
                "final_price"
            )

        try:
            final_price =
                float(
                    final_price
                )
        except Exception:
            continue

        if (
            min_price is not None
            and final_price < min_price
        ):
            continue

        if (
            max_price is not None
            and final_price > max_price
        ):
            continue

        if category:

            offer_category =
                norm(
                    offer.get(
                        "category"
                    )
                    or ""
                )

            wanted_category =
                norm(
                    category
                )

            if (
                wanted_category
                and wanted_category
                not in offer_category
            ):
                continue

        filtered.append(
            offer
        )

    return filtered


def sort_offers(
    offers,
    sort_by="discount"
):

    if not offers:
        return []

    if sort_by == "price":

        return sorted(
            offers,
            key=lambda x:
                float(
                    x.get(
                        "final_price",
                        999999
                    )
                    or 999999
                )
        )

    if sort_by == "relevance":

        return sorted(
            offers,
            key=lambda x:
                float(
                    x.get(
                        "relevance_score",
                        0
                    )
                    or 0
                ),
            reverse=True
        )

    if sort_by == "coupon":

        return sorted(
            offers,
            key=lambda x:
                float(
                    x.get(
                        "coupon_discount",
                        0
                    )
                    or 0
                ),
            reverse=True
        )

    return sorted(
        offers,
        key=lambda x:
            float(
                x.get(
                    "discount_percent",
                    0
                )
                or 0
            ),
        reverse=True
    )


# ============================================================
# BANCO DE OFERTAS PUBLICADAS
# ============================================================

def offer_was_published(
    item_id
):

    if not item_id:
        return False

    conn = get_db()

    row =
        conn.execute(
            """
            SELECT id
            FROM published_offers
            WHERE item_id=?
            LIMIT 1
            """,
            (
                str(
                    item_id
                ),
            )
        ).fetchone()

    conn.close()

    return bool(row)


def get_published_offer(
    item_id
):

    if not item_id:
        return None

    conn = get_db()

    row =
        conn.execute(
            """
            SELECT *
            FROM published_offers
            WHERE item_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                str(
                    item_id
                ),
            )
        ).fetchone()

    conn.close()

    return (
        dict(row)
        if row
        else None
    )


def save_published_offer(
    offer,
    channel="whatsapp"
):

    if not offer:
        return None

    item_id =
        str(
            offer.get(
                "item_id"
            )
            or offer.get(
                "id"
            )
            or ""
        ).strip()

    if not item_id:
        return None

    conn = get_db()

    conn.execute(
        """
        INSERT INTO published_offers
        (
            item_id,
            title,
            price,
            original_price,
            final_price,
            discount_percent,
            coupon_code,
            permalink,
            image,
            channel,
            published_at
        )
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            item_id,

            offer.get(
                "title"
            ),

            offer.get(
                "price"
            ),

            offer.get(
                "original_price"
            ),

            offer.get(
                "final_price"
            ),

            offer.get(
                "discount_percent"
            ),

            offer.get(
                "coupon_code"
            ),

            offer.get(
                "permalink"
            ),

            offer.get(
                "image"
            ),

            channel,
        )
    )

    conn.commit()
    conn.close()

    return True


# ============================================================
# MONTAGEM DA OFERTA
# ============================================================

def money_br(
    value
):

    try:
        value =
            float(
                value
                or 0
            )
    except Exception:
        value = 0

    return (
        "R$ "
        + f"{value:,.2f}"
        .replace(
            ",",
            "X"
        )
        .replace(
            ".",
            ","
        )
        .replace(
            "X",
            "."
        )
    )


def build_offer_text(
    offer
):

    title =
        str(
            offer.get(
                "title"
            )
            or "Produto"
        ).strip()

    original =
        offer.get(
            "original_price"
        )

    price =
        offer.get(
            "price"
        )

    final_price =
        offer.get(
            "final_price"
        )

    discount_percent =
        offer.get(
            "discount_percent"
        )

    coupon_code =
        str(
            offer.get(
                "coupon_code"
            )
            or ""
        ).strip()

    free_shipping =
        bool(
            offer.get(
                "free_shipping"
            )
        )

    permalink =
        str(
            offer.get(
                "permalink"
            )
            or offer.get(
                "affiliate_link"
            )
            or ""
        ).strip()

    lines = [
        "🔥 OFERTA OCULTA / VIP",
        "",
        title,
        "",
    ]

    try:

        original_value =
            float(
                original
                or 0
            )

        price_value =
            float(
                price
                or 0
            )

        final_value =
            float(
                final_price
                or price
                or 0
            )

    except Exception:

        original_value = 0
        price_value = 0
        final_value = 0

    if (
        original_value
        > price_value
        and original_value > 0
    ):

        lines.append(
            "~~De "
            + money_br(
                original_value
            )
            + "~~"
        )

    if (
        final_value > 0
    ):

        lines.append(
            "💰 Por "
            + money_br(
                final_value
            )
            + " 🔥"
        )

    if (
        discount_percent
        and float(
            discount_percent
            or 0
        ) > 0
    ):

        lines.append(
            "📉 "
            + str(
                round(
                    float(
                        discount_percent
                    )
                )
            )
            + "% OFF"
        )

    if coupon_code:

        lines.append(
            "🎟️ Cupom: "
            + coupon_code
        )

    if free_shipping:

        lines.append(
            "🚚 Frete grátis"
        )

    if permalink:

        lines.extend(
            [
                "",
                "👉 Pegar promoção:",
                permalink,
            ]
        )

    return "\n".join(
        lines
    )


# ============================================================
# WHATSAPP
# ============================================================

def whatsapp_configured():

    return bool(
        WHATSAPP_BOT_URL
        and WHATSAPP_BOT_KEY
    )


def send_whatsapp_offer(
    text
):

    if not whatsapp_configured():

        return {
            "ok": False,
            "error":
                "WhatsApp não configurado."
        }

    url =
        WHATSAPP_BOT_URL.rstrip(
            "/"
        ) + "/api/send-offer"

    headers = {
        "x-bot-key":
            WHATSAPP_BOT_KEY,

        "Content-Type":
            "application/json",
    }

    try:

        response =
            requests.post(
                url,
                headers=headers,
                json={
                    "text":
                        text
                },
                timeout=30
            )

    except requests.RequestException as exc:

        print(
            "[WHATSAPP] Erro:",
            repr(exc)
        )

        return {
            "ok": False,
            "error":
                str(exc)
        }

    try:

        payload =
            response.json()

    except Exception:

        payload = {
            "ok":
                response.ok,

            "error":
                response.text[:1000]
        }

    if not response.ok:

        return {
            "ok": False,
            "status":
                response.status_code,

            "error":
                payload.get(
                    "error"
                )
                or "Falha no WhatsApp."
        }

    return payload


# ============================================================
# API DE BUSCA
# ============================================================

@app.route(
    "/api/search",
    methods=["GET"]
)
def api_search():

    query =
        request.args.get(
            "q",
            ""
        ).strip()

    category =
        request.args.get(
            "category",
            ""
        ).strip()

    try:

        limit =
            int(
                request.args.get(
                    "limit",
                    20
                )
            )

    except Exception:

        limit = 20

    limit =
        max(
            1,
            min(
                limit,
                100
            )
        )

    if not query and category:

        query =
            category

    if not query:

        return jsonify({
            "ok": False,
            "error":
                "Informe uma busca."
        }), 400

    offers =
        generate_offers(
            query,
            category or None,
            limit
        )

    return jsonify({
        "ok": True,
        "query":
            query,
        "category":
            category,
        "count":
            len(offers),
        "products":
            offers,
        "offers":
            offers
    })


# ============================================================
# API DE GERAÇÃO DE OFERTA
# ============================================================

@app.route(
    "/api/gerar-oferta",
    methods=["POST"]
)
def api_gerar_oferta():

    data =
        request.get_json(
            silent=True
        ) or {}

    item_id =
        str(
            data.get(
                "item_id"
            )
            or data.get(
                "id"
            )
            or ""
        ).strip()

    if not item_id:

        return jsonify({
            "ok": False,
            "error":
                "item_id não informado."
        }), 400

    item = None

    # Primeiro tenta consultar o item diretamente.
    token =
        get_access_token()

    if token:

        item_data,
        item_status,
        item_raw =
            ml_get(
                f"/items/{item_id}"
            )

        if (
            item_status == 200
            and isinstance(
                item_data,
                dict
            )
        ):
            item =
                item_data

    # Se não conseguiu consultar diretamente,
    # aceita os dados enviados pelo frontend.
    if item is None:

        item = {
            "id":
                item_id,

            "title":
                data.get(
                    "title"
                ),

            "price":
                data.get(
                    "price"
                ),

            "original_price":
                data.get(
                    "original_price"
                ),

            "permalink":
                data.get(
                    "permalink"
                ),

            "thumbnail":
                data.get(
                    "thumbnail"
                )
                or data.get(
                    "image"
                ),

            "shipping":
                {
                    "free_shipping":
                        data.get(
                            "free_shipping",
                            False
                        )
                },

            "category_id":
                data.get(
                    "category_id"
                ),
        }

    category =
        data.get(
            "category"
        )

    query =
        data.get(
            "query",
            ""
        )

    offer =
        product_from_item(
            item,
            category,
            query
        )

    if not offer:

        return jsonify({
            "ok": False,
            "error":
                "Não foi possível gerar a oferta."
        }), 400

    text =
        build_offer_text(
            offer
        )

    offer[
        "offer_text"
    ] = text

    return jsonify({
        "ok": True,
        "offer":
            offer,
        "text":
            text
    })


# ============================================================
# API ENVIO WHATSAPP
# ============================================================

@app.route(
    "/api/enviar-whatsapp",
    methods=["POST"]
)
def api_enviar_whatsapp():

    data =
        request.get_json(
            silent=True
        ) or {}

    text =
        str(
            data.get(
                "text"
            )
            or ""
        ).strip()

    if not text:

        offer =
            data.get(
                "offer"
            ) or {}

        if offer:

            text =
                build_offer_text(
                    offer
                )

    if not text:

        return jsonify({
            "ok": False,
            "error":
                "Mensagem vazia."
        }), 400

    result =
        send_whatsapp_offer(
            text
        )

    if not result.get(
        "ok"
    ):

        return jsonify(
            result
        ), 502

    return jsonify(
        result
    )


# ============================================================
# TESTE WHATSAPP
# ============================================================

@app.route(
    "/whatsapp/teste"
)
def whatsapp_teste():

    text = (
        "🟢 TESTE DE INTEGRAÇÃO — "
        "Caçador de Ofertas conectado "
        "ao WhatsApp com sucesso!"
    )

    result =
        send_whatsapp_offer(
            text
        )

    if not result.get(
        "ok"
    ):

        return jsonify(
            result
        ), 502

    return jsonify({
        "ok": True,
        "message":
            "Teste enviado para o WhatsApp.",
        "result":
            result
    })


# ============================================================
# API STATUS
# ============================================================

@app.route(
    "/api/status"
)
def api_status():

    ml_row =
        get_token_row()

    return jsonify({

        "ok":
            True,

        "mercadolivre":
            bool(
                ml_row
                and ml_row[
                    "access_token"
                ]
            ),

        "whatsapp":
            whatsapp_configured(),

        "whatsapp_url":
            WHATSAPP_BOT_URL
            if WHATSAPP_BOT_URL
            else None,

    })


# ============================================================
# ROTAS DE OFERTAS SALVAS
# ============================================================

@app.route(
    "/api/published",
    methods=["GET"]
)
def api_published():

    conn = get_db()

    rows =
        conn.execute(
            """
            SELECT *
            FROM published_offers
            ORDER BY
                id DESC
            LIMIT 100
            """
        ).fetchall()

    conn.close()

    return jsonify({
        "ok":
            True,

        "count":
            len(rows),

        "offers":
            [
                dict(row)
                for row in rows
            ]
    })


@app.route(
    "/api/coupons",
    methods=["GET"]
)
def api_coupons():

    coupons =
        get_coupons()

    return jsonify({
        "ok":
            True,

        "count":
            len(coupons),

        "coupons":
            coupons
    })


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return render_template_string(
        HTML
    )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    port =
        int(
            os.getenv(
                "PORT",
                "5000"
            )
        )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
    # ============================================================
# INTERFACE WEB
# ============================================================

HTML = r"""
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

*{
    box-sizing:border-box;
}

body{
    margin:0;
    font-family:Arial,Helvetica,sans-serif;
    background:#f4f5f7;
    color:#111827;
}

header{
    background:#111827;
    color:white;
    padding:18px;
}

.header-inner{
    max-width:1200px;
    margin:auto;
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:15px;
}

.logo{
    font-size:22px;
    font-weight:800;
}

.status{
    display:flex;
    gap:8px;
    align-items:center;
    font-size:13px;
}

.dot{
    width:9px;
    height:9px;
    border-radius:50%;
    background:#22c55e;
}

.container{
    max-width:1200px;
    margin:0 auto;
    padding:20px;
}

.panel{
    background:white;
    border-radius:16px;
    padding:18px;
    margin-bottom:20px;
    box-shadow:0 3px 15px rgba(0,0,0,.06);
}

h1,
h2,
h3{
    margin-top:0;
}

.search-row{
    display:grid;
    grid-template-columns:
        minmax(0,1fr)
        220px
        120px;

    gap:10px;
}

input,
select,
button{
    border:1px solid #d1d5db;
    border-radius:10px;
    padding:12px;
    font-size:15px;
}

button{
    cursor:pointer;
    border:none;
    background:#111827;
    color:white;
    font-weight:700;
}

button:hover{
    opacity:.92;
}

button.green{
    background:#16a34a;
}

button.yellow{
    background:#eab308;
    color:#111827;
}

button.blue{
    background:#2563eb;
}

button.red{
    background:#dc2626;
}

button.gray{
    background:#6b7280;
}

.catalog{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px,1fr)
        );

    gap:10px;
}

.catalog button{
    background:#f3f4f6;
    color:#111827;
    border:1px solid #e5e7eb;
}

.catalog button:hover{
    background:#e5e7eb;
}

.results{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fill,
            minmax(270px,1fr)
        );

    gap:16px;
}

.card{
    background:white;
    border:1px solid #e5e7eb;
    border-radius:16px;
    overflow:hidden;
    display:flex;
    flex-direction:column;
}

.card-image{
    width:100%;
    height:230px;
    background:#f9fafb;
    display:flex;
    align-items:center;
    justify-content:center;
}

.card-image img{
    width:100%;
    height:100%;
    object-fit:contain;
}

.card-body{
    padding:15px;
}

.card-title{
    font-weight:700;
    line-height:1.35;
    min-height:54px;
}

.old-price{
    color:#6b7280;
    text-decoration:line-through;
    font-size:14px;
    margin-top:10px;
}

.price{
    font-size:24px;
    font-weight:900;
    color:#16a34a;
    margin-top:4px;
}

.discount{
    display:inline-block;
    margin-top:8px;
    padding:5px 8px;
    background:#dcfce7;
    color:#166534;
    border-radius:7px;
    font-size:13px;
    font-weight:800;
}

.coupon{
    margin-top:8px;
    padding:8px;
    border-radius:8px;
    background:#fff7ed;
    color:#9a3412;
    font-size:13px;
    font-weight:700;
}

.shipping{
    margin-top:7px;
    color:#15803d;
    font-size:13px;
    font-weight:700;
}

.card-actions{
    display:grid;
    grid-template-columns:
        1fr 1fr;

    gap:8px;
    padding:15px;
    padding-top:0;
}

.card-actions button{
    width:100%;
    font-size:13px;
}

.offer-box{
    white-space:pre-wrap;
    background:#111827;
    color:#f9fafb;
    border-radius:12px;
    padding:15px;
    line-height:1.5;
    font-size:14px;
}

.modal{
    position:fixed;
    inset:0;
    background:rgba(0,0,0,.65);
    display:none;
    align-items:center;
    justify-content:center;
    padding:20px;
    z-index:9999;
}

.modal.show{
    display:flex;
}

.modal-content{
    width:min(
        700px,
        100%
    );

    max-height:90vh;
    overflow:auto;

    background:white;
    border-radius:18px;
    padding:20px;
}

.modal-actions{
    display:flex;
    gap:10px;
    margin-top:15px;
    flex-wrap:wrap;
}

.toast{
    position:fixed;
    bottom:20px;
    left:50%;
    transform:translateX(-50%);

    background:#111827;
    color:white;

    padding:12px 18px;
    border-radius:10px;

    display:none;
    z-index:10000;
}

.toast.show{
    display:block;
}

.loading{
    padding:30px;
    text-align:center;
    color:#6b7280;
}

.empty{
    padding:30px;
    text-align:center;
    color:#6b7280;
}

.stats{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(160px,1fr)
        );

    gap:10px;
}

.stat{
    background:#f9fafb;
    border:1px solid #e5e7eb;
    border-radius:12px;
    padding:15px;
}

.stat-value{
    font-size:25px;
    font-weight:900;
}

.stat-label{
    color:#6b7280;
    font-size:13px;
    margin-top:4px;
}

@media(max-width:700px){

    .search-row{
        grid-template-columns:1fr;
    }

    .container{
        padding:12px;
    }

    .results{
        grid-template-columns:
            repeat(
                2,
                minmax(0,1fr)
            );

        gap:10px;
    }

    .card-image{
        height:180px;
    }

    .card-title{
        font-size:14px;
    }

    .price{
        font-size:20px;
    }

    .card-actions{
        grid-template-columns:1fr;
    }

}

</style>

</head>

<body>

<header>

<div class="header-inner">

<div class="logo">
🕵️ Caçador de Ofertas
</div>

<div class="status">
<span
    class="dot"
    id="statusDot"
></span>

<span id="statusText">
Verificando...
</span>
</div>

</div>

</header>


<div class="container">

<div class="panel">

<h2>
🔎 Encontrar ofertas
</h2>

<div class="search-row">

<input
    id="searchInput"
    placeholder="Digite o produto..."
    autocomplete="off"
>

<select id="categorySelect">

<option value="">
Todas as categorias
</option>

<option value="📱 Celulares">
📱 Celulares
</option>

<option value="🏋️ Academia">
🏋️ Academia
</option>

<option value="🎧 Eletrônicos">
🎧 Eletrônicos
</option>

<option value="🏠 Casa">
🏠 Casa
</option>

<option value="🍳 Cozinha">
🍳 Cozinha
</option>

<option value="🔧 Ferramentas">
🔧 Ferramentas
</option>

<option value="🌸 Perfumes">
🌸 Perfumes
</option>

<option value="👕 Moda">
👕 Moda
</option>

<option value="🚗 Automotivo">
🚗 Automotivo
</option>

</select>

<button
    class="green"
    onclick="buscar()"
>
🔍 Buscar
</button>

</div>

</div>


<div class="panel">

<h3>
Categorias
</h3>

<div class="catalog">

<button
    onclick="buscarCategoria('📱 Celulares')"
>
📱 Celulares
</button>

<button
    onclick="buscarCategoria('🏋️ Academia')"
>
🏋️ Academia
</button>

<button
    onclick="buscarCategoria('🎧 Eletrônicos')"
>
🎧 Eletrônicos
</button>

<button
    onclick="buscarCategoria('🏠 Casa')"
>
🏠 Casa
</button>

<button
    onclick="buscarCategoria('🍳 Cozinha')"
>
🍳 Cozinha
</button>

<button
    onclick="buscarCategoria('🔧 Ferramentas')"
>
🔧 Ferramentas
</button>

<button
    onclick="buscarCategoria('🌸 Perfumes')"
>
🌸 Perfumes
</button>

<button
    onclick="buscarCategoria('👕 Moda')"
>
👕 Moda
</button>

<button
    onclick="buscarCategoria('🚗 Automotivo')"
>
🚗 Automotivo
</button>

</div>

</div>


<div class="panel">

<div class="stats">

<div class="stat">

<div
    class="stat-value"
    id="resultCount"
>
0
</div>

<div class="stat-label">
Ofertas encontradas
</div>

</div>


<div class="stat">

<div
    class="stat-value"
    id="whatsappStatus"
>
—
</div>

<div class="stat-label">
WhatsApp
</div>

</div>


<div class="stat">

<div
    class="stat-value"
    id="mlStatus"
>
—
</div>

<div class="stat-label">
Mercado Livre
</div>

</div>

</div>

</div>


<div
    id="results"
    class="results"
>
<div class="empty">
Faça uma busca para encontrar ofertas.
</div>
</div>

</div>


<div
    class="modal"
    id="offerModal"
>

<div class="modal-content">

<h2>
📢 Anúncio
</h2>

<div
    id="offerPreview"
    class="offer-box"
></div>

<div class="modal-actions">

<button
    class="blue"
    onclick="copiarOferta()"
>
📋 Copiar oferta
</button>

<button
    class="green"
    onclick="enviarWhatsApp()"
>
📲 Enviar para WhatsApp
</button>

<button
    class="gray"
    onclick="fecharModal()"
>
Fechar
</button>

</div>

</div>

</div>


<div
    id="toast"
    class="toast"
>
Copiado!
</div>


<script>

let currentOffer = null;


function escapeHtml(value){

    return String(
        value ?? ""
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


function money(value){

    const n =
        Number(
            value || 0
        );

    return n.toLocaleString(
        "pt-BR",
        {
            style:"currency",
            currency:"BRL"
        }
    );
}


function toast(message){

    const el =
        document.getElementById(
            "toast"
        );

    el.textContent =
        message;

    el.classList.add(
        "show"
    );

    setTimeout(
        () => {
            el.classList.remove(
                "show"
            );
        },
        2200
    );
}


function fecharModal(){

    document
        .getElementById(
            "offerModal"
        )
        .classList.remove(
            "show"
        );

    currentOffer = null;
}


function buscarCategoria(
    category
){

    document
        .getElementById(
            "categorySelect"
        )
        .value =
            category;

    document
        .getElementById(
            "searchInput"
        )
        .value = "";

    buscar();

}


async function buscar(){

    const input =
        document.getElementById(
            "searchInput"
        );

    const category =
        document.getElementById(
            "categorySelect"
        ).value;

    const query =
        input.value.trim();

    if(
        !query
        && !category
    ){

        toast(
            "Digite um produto ou escolha uma categoria."
        );

        return;
    }

    const results =
        document.getElementById(
            "results"
        );

    results.innerHTML =
        `
        <div class="loading">
            🔎 Procurando as melhores ofertas...
        </div>
        `;

    try{

        const params =
            new URLSearchParams();

        if(query){
            params.set(
                "q",
                query
            );
        }

        if(category){
            params.set(
                "category",
                category
            );
        }

        params.set(
            "limit",
            "50"
        );

        const response =
            await fetch(
                "/api/search?"
                + params.toString()
            );

        const data =
            await response.json();

        if(
            !response.ok
            || !data.ok
        ){

            throw new Error(
                data.error
                || "Falha na busca."
            );
        }

        renderProducts(
            data.products
            || data.offers
            || []
        );

    }catch(error){

        console.error(
            error
        );

        results.innerHTML =
            `
            <div class="empty">
                ❌ ${escapeHtml(
                    error.message
                    || "Erro ao buscar."
                )}
            </div>
            `;
    }

}


function renderProducts(
    products
){

    const results =
        document.getElementById(
            "results"
        );

    document.getElementById(
        "resultCount"
    ).textContent =
        products.length;

    if(
        !products.length
    ){

        results.innerHTML =
            `
            <div class="empty">
                Nenhuma oferta encontrada.
            </div>
            `;

        return;
    }

    results.innerHTML =
        products
            .map(
                (
                    product,
                    index
                ) =>
                    productCard(
                        product,
                        index
                    )
            )
            .join("");

}


function productCard(
    product,
    index
){

    const title =
        product.title
        || "Produto";

    const image =
        product.image
        || product.thumbnail
        || "";

    const original =
        Number(
            product.original_price
            || 0
        );

    const price =
        Number(
            product.price
            || 0
        );

    const finalPrice =
        Number(
            product.final_price
            || price
            || 0
        );

    const discount =
        Number(
            product.discount_percent
            || product.discount
            || 0
        );

    const coupon =
        product.coupon_code
        || (
            product.coupon
            && product.coupon.code
        )
        || "";

    const freeShipping =
        Boolean(
            product.free_shipping
        );

    return `
    <div class="card">

        <div class="card-image">

            ${
                image
                ?
                `
                <img
                    src="${escapeHtml(image)}"
                    alt="${escapeHtml(title)}"
                    loading="lazy"
                    onerror="
                        this.style.display='none';
                    "
                >
                `
                :
                `
                <span>
                    Sem imagem
                </span>
                `
            }

        </div>

        <div class="card-body">

            <div class="card-title">
                ${escapeHtml(title)}
            </div>

            ${
                original > price
                ?
                `
                <div class="old-price">
                    ${money(original)}
                </div>
                `
                :
                ""
            }

            <div class="price">
                ${
                    finalPrice > 0
                    ?
                    money(finalPrice)
                    :
                    money(price)
                }
            </div>

            ${
                discount > 0
                ?
                `
                <div class="discount">
                    ${Math.round(discount)}% OFF
                </div>
                `
                :
                ""
            }

            ${
                coupon
                ?
                `
                <div class="coupon">
                    🎟️ Cupom: ${escapeHtml(coupon)}
                </div>
                `
                :
                ""
            }

            ${
                freeShipping
                ?
                `
                <div class="shipping">
                    🚚 Frete grátis
                </div>
                `
                :
                ""
            }

        </div>

        <div class="card-actions">

            <button
                class="blue"
                onclick="abrirProduto(${index})"
            >
                👁️ Ver produto
            </button>

            <button
                class="gray"
                onclick="copiarUrl(${index})"
            >
                🔗 Copiar URL
            </button>

            <button
                class="yellow"
                onclick="gerarAnuncio(${index})"
            >
                📢 Gerar anúncio
            </button>

            <button
                class="green"
                onclick="gerarEEnviar(${index})"
            >
                📲 Enviar para WhatsApp
            </button>

        </div>

    </div>
    `;
}


let lastProducts = [];


function abrirProduto(
    index
){

    const product =
        lastProducts[index];

    if(
        !product
    ){
        return;
    }

    const url =
        product.permalink
        || product.affiliate_link
        || "";

    if(!url){

        toast(
            "Link do produto não encontrado."
        );

        return;
    }

    window.open(
        url,
        "_blank"
    );

}


function copiarUrl(
    index
){

    const product =
        lastProducts[index];

    if(
        !product
    ){
        return;
    }

    const url =
        product.permalink
        || product.affiliate_link
        || "";

    if(!url){

        toast(
            "URL não encontrada."
        );

        return;
    }

    navigator.clipboard
        .writeText(
            url
        )
        .then(
            () => toast(
                "URL copiada!"
            )
        )
        .catch(
            () => {
                toast(
                    "Não foi possível copiar."
                );
            }
        );

}


async function gerarAnuncio(
    index
){

    const product =
        lastProducts[index];

    if(
        !product
    ){
        return;
    }

    try{

        const response =
            await fetch(
                "/api/gerar-oferta",
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            product
                        )
                }
            );

        const data =
            await response.json();

        if(
            !response.ok
            || !data.ok
        ){

            throw new Error(
                data.error
                || "Erro ao gerar anúncio."
            );
        }

        currentOffer =
            data.offer;

        document
            .getElementById(
                "offerPreview"
            )
            .textContent =
                data.text
                || "";

        document
            .getElementById(
                "offerModal"
            )
            .classList.add(
                "show"
            );

    }catch(error){

        console.error(
            error
        );

        toast(
            error.message
            || "Erro ao gerar anúncio."
        );
    }

}


async function gerarEEnviar(
    index
){

    const product =
        lastProducts[index];

    if(
        !product
    ){
        return;
    }

    try{

        const response =
            await fetch(
                "/api/gerar-oferta",
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            product
                        )
                }
            );

        const data =
            await response.json();

        if(
            !response.ok
            || !data.ok
        ){

            throw new Error(
                data.error
                || "Erro ao gerar anúncio."
            );
        }

        currentOffer =
            data.offer;

        const sendResponse =
            await fetch(
                "/api/enviar-whatsapp",
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            {
                                text:
                                    data.text,

                                offer:
                                    data.offer
                            }
                        )
                }
            );

        const sendData =
            await sendResponse.json();

        if(
            !sendResponse.ok
            || !sendData.ok
        ){

            throw new Error(
                sendData.error
                || "Erro ao enviar para WhatsApp."
            );
        }

        toast(
            "📲 Oferta enviada para o WhatsApp!"
        );

    }catch(error){

        console.error(
            error
        );

        toast(
            error.message
            || "Erro ao enviar oferta."
        );
    }

}


async function copiarOferta(){

    if(
        !currentOffer
    ){

        toast(
            "Nenhuma oferta aberta."
        );

        return;
    }

    const text =
        document
            .getElementById(
                "offerPreview"
            )
            .textContent
            || "";

    try{

        await navigator.clipboard
            .writeText(
                text
            );

        toast(
            "Oferta copiada!"
        );

    }catch(error){

        toast(
            "Não foi possível copiar."
        );
    }

}


async function enviarWhatsApp(){

    if(
        !currentOffer
    ){

        toast(
            "Nenhuma oferta aberta."
        );

        return;
    }

    const text =
        document
            .getElementById(
                "offerPreview"
            )
            .textContent
            || "";

    try{

        const response =
            await fetch(
                "/api/enviar-whatsapp",
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            {
                                text:
                                    text,

                                offer:
                                    currentOffer
                            }
                        )
                }
            );

        const data =
            await response.json();

        if(
            !response.ok
            || !data.ok
        ){

            throw new Error(
                data.error
                || "Falha no envio."
            );
        }

        toast(
            "📲 Enviado para o WhatsApp!"
        );

    }catch(error){

        console.error(
            error
        );

        toast(
            error.message
            || "Erro no envio."
        );
    }

}


async function carregarStatus(){

    try{

        const response =
            await fetch(
                "/api/status"
            );

        const data =
            await response.json();

        const dot =
            document.getElementById(
                "statusDot"
            );

        const text =
            document.getElementById(
                "statusText"
            );

        if(
            data.whatsapp
        ){

            text.textContent =
                "WhatsApp configurado";

            dot.style.background =
                "#22c55e";

            document.getElementById(
                "whatsappStatus"
            ).textContent =
                "🟢";

        }else{

            text.textContent =
                "WhatsApp não configurado";

            dot.style.background =
                "#ef4444";

            document.getElementById(
                "whatsappStatus"
            ).textContent =
                "🔴";
        }

        document.getElementById(
            "mlStatus"
        ).textContent =
            data.mercadolivre
            ? "🟢"
            : "🔴";

    }catch(error){

        document.getElementById(
            "statusText"
        ).textContent =
            "Status indisponível";

        document.getElementById(
            "statusDot"
        ).style.background =
            "#ef4444";
    }

}


const originalRenderProducts =
    renderProducts;


renderProducts =
    function(products){

        lastProducts =
            Array.isArray(
                products
            )
            ? products
            : [];

        originalRenderProducts(
            lastProducts
        );
    };


document
    .getElementById(
        "searchInput"
    )
    .addEventListener(
        "keydown",
        function(event){

            if(
                event.key ===
                "Enter"
            ){

                buscar();
            }
        }
    );


carregarStatus();

</script>

</body>

</html>
"""
    return matched


# ============================================================
# MATCH DE CUPOM COM PRODUTO
# ============================================================

def match_public_coupon(
    title,
    price,
    public_cards,
    item_id=None
):

    if not public_cards:
        return None

    title_norm = norm(title)

    best = None
    best_score = 0

    for card in public_cards:

        if not isinstance(
            card,
            dict
        ):
            continue

        code = str(
            card.get(
                "code"
            )
            or ""
        ).strip()

        if not code:
            continue

        # Associação direta pelo ID do produto.
        card_item_id = str(
            card.get(
                "item_id"
            )
            or card.get(
                "product_id"
            )
            or ""
        ).strip()

        score = 0

        if (
            item_id
            and card_item_id
            and str(item_id)
            == card_item_id
        ):
            score += 1000

        card_title = norm(
            card.get(
                "title"
            )
            or card.get(
                "product_title"
            )
            or ""
        )

        if card_title:

            words = [
                w
                for w in card_title.split()
                if len(w) >= 4
            ]

            for word in words:

                if word in title_norm:
                    score += 25

        # Também aceita palavras importantes
        # encontradas na descrição pública do cupom.
        description = norm(
            card.get(
                "description"
            )
            or card.get(
                "conditions"
            )
            or ""
        )

        desc_words = [
            w
            for w in description.split()
            if len(w) >= 5
        ]

        for word in desc_words:

            if word in title_norm:
                score += 2

        if score <= 0:
            continue

        try:

            discount_value = float(
                card.get(
                    "desconto_estimado"
                )
                or card.get(
                    "discount_value"
                )
                or 0
            )

        except Exception:

            discount_value = 0

        if discount_value <= 0:

            discount_value =
                coupon_discount(
                    card,
                    price
                )

        if discount_value <= 0:
            continue

        candidate = dict(card)

        candidate[
            "desconto_estimado"
        ] = round(
            discount_value,
            2
        )

        candidate[
            "percentual_efetivo"
        ] = round(
            (
                discount_value
                / float(price)
                * 100
            )
            if float(price) > 0
            else 0,
            2
        )

        candidate[
            "match_score"
        ] = score

        if (
            score > best_score
            or (
                score == best_score
                and discount_value
                > float(
                    best.get(
                        "desconto_estimado",
                        0
                    )
                    if best
                    else 0
                )
            )
        ):

            best = candidate
            best_score = score

    return best


# ============================================================
# BUSCA DE PRODUTOS — VERSÃO AUTOMÁTICA
# ============================================================

def search_products_automatic(
    query,
    category=None,
    limit=50
):

    query = str(
        query or ""
    ).strip()

    if not query and category:
        query = category

    if not query:
        return []

    category_name =
        category or query_category(
            query
        )

    raw = []

    # --------------------------------------------------------
    # 1. Tenta busca direta no marketplace.
    # --------------------------------------------------------

    direct =
        search_products_direct(
            query,
            limit=limit
        )

    if direct:
        raw.extend(
            direct
        )

    # --------------------------------------------------------
    # 2. Se a busca direta não trouxe quantidade suficiente,
    #    amplia usando consultas do catálogo.
    # --------------------------------------------------------

    if len(raw) < limit:

        related_queries = []

        if category_name:

            related_queries.extend(
                CATALOG.get(
                    category_name,
                    []
                )
            )

        if query not in related_queries:

            related_queries.insert(
                0,
                query
            )

        seen_queries = set()

        for related in related_queries:

            normalized_query =
                norm(
                    related
                )

            if (
                not normalized_query
                or normalized_query
                in seen_queries
            ):
                continue

            seen_queries.add(
                normalized_query
            )

            extra =
                search_products_direct(
                    related,
                    limit=min(
                        30,
                        limit
                    )
                )

            raw.extend(
                extra
            )

            if len(raw) >= limit * 2:
                break

    # --------------------------------------------------------
    # 3. Remove duplicados.
    # --------------------------------------------------------

    unique = []
    seen = set()

    for item in raw:

        if not isinstance(
            item,
            dict
        ):
            continue

        item_id = str(
            item.get(
                "id"
            )
            or item.get(
                "item_id"
            )
            or ""
        ).strip()

        if not item_id:
            continue

        if item_id in seen:
            continue

        seen.add(
            item_id
        )

        unique.append(
            item
        )

    # --------------------------------------------------------
    # 4. Normaliza e filtra.
    # --------------------------------------------------------

    products = []

    for item in unique:

        title = str(
            item.get(
                "title"
            )
            or ""
        ).strip()

        if not title:
            continue

        if not is_requested_product(
            title,
            query,
            category_name
        ):
            continue

        normalized =
            normalize_item(
                {
                    "item_id":
                        item.get(
                            "id"
                        )
                        or item.get(
                            "item_id"
                        ),

                    "seller_id":
                        item.get(
                            "seller_id"
                        ),

                    "price":
                        item.get(
                            "price"
                        ),

                    "original_price":
                        item.get(
                            "original_price"
                        ),

                    "condition":
                        item.get(
                            "condition"
                        ),

                    "listing_type_id":
                        item.get(
                            "listing_type_id"
                        ),

                    "permalink":
                        item.get(
                            "permalink"
                        ),

                    "shipping":
                        item.get(
                            "shipping"
                        ),

                }
            )

        if not normalized:
            continue

        try:

            price =
                float(
                    normalized.get(
                        "price"
                    )
                    or 0
                )

        except Exception:

            continue

        if not valid_catalog_price(
            price
        ):
            continue

        normalized[
            "title"
        ] = title

        normalized[
            "category_name"
        ] = category_name

        normalized[
            "category"
        ] = category_name

        normalized[
            "image"
        ] = (
            item.get(
                "thumbnail"
            )
            or item.get(
                "secure_thumbnail"
            )
            or ""
        )

        normalized[
            "thumbnail"
        ] = normalized[
            "image"
        ]

        normalized[
            "original_item"
        ] = item

        # ----------------------------------------------------
        # Busca preço de venda atual quando disponível.
        # ----------------------------------------------------

        current_price, regular_price =
            get_current_sale_price(
                normalized.get(
                    "item_id"
                )
            )

        if current_price:

            normalized[
                "price"
            ] = current_price

            price =
                current_price

            if (
                regular_price
                and regular_price > current_price
            ):

                normalized[
                    "original_price"
                ] = regular_price

        # ----------------------------------------------------
        # Frete.
        # ----------------------------------------------------

        free_shipping =
            bool(
                normalized.get(
                    "free_shipping"
                )
            )

        shipping_cost =
            normalized.get(
                "shipping_cost"
            )

        try:

            shipping_cost =
                float(
                    shipping_cost
                    or 0
                )

        except Exception:

            shipping_cost = 0

        if free_shipping:
            shipping_cost = 0

        normalized[
            "shipping_cost"
        ] = shipping_cost

        normalized[
            "total_price"
        ] = total(
            price,
            shipping_cost
        )

        # ----------------------------------------------------
        # Desconto real.
        # ----------------------------------------------------

        original_price =
            normalized.get(
                "original_price"
            )

        try:

            original_price =
                float(
                    original_price
                    or price
                )

        except Exception:

            original_price = price

        if original_price < price:
            original_price = price

        normalized[
            "original_price"
        ] = original_price

        normalized[
            "discount"
        ] = discount(
            price,
            original_price
        )

        normalized[
            "discount_percent"
        ] = normalized[
            "discount"
        ]

        normalized[
            "relevance_score"
        ] = relevance(
            title,
            query
        )

        normalized[
            "affiliate_link"
        ] = normalized.get(
            "permalink"
        )

        products.append(
            normalized
        )

    # --------------------------------------------------------
    # Ordenação.
    # --------------------------------------------------------

    products.sort(
        key=lambda x: (
            float(
                x.get(
                    "relevance_score",
                    0
                )
                or 0
            ),
            float(
                x.get(
                    "discount_percent",
                    0
                )
                or 0
            )
        ),
        reverse=True
    )

    return products[:limit]


# ============================================================
# API PRINCIPAL DE BUSCA
# ============================================================

@app.route(
    "/api/buscar",
    methods=["GET"]
)
def api_buscar():

    query =
        request.args.get(
            "q",
            ""
        ).strip()

    category =
        request.args.get(
            "category",
            ""
        ).strip()

    try:

        limit =
            int(
                request.args.get(
                    "limit",
                    30
                )
            )

    except Exception:

        limit = 30

    limit =
        max(
            1,
            min(
                limit,
                100
            )
        )

    if not query and category:
        query = category

    if not query:

        return jsonify({
            "ok": False,
            "error":
                "Digite o produto que deseja procurar."
        }), 400

    products =
        search_products_automatic(
            query,
            category or None,
            limit
        )

    return jsonify({
        "ok": True,
        "query":
            query,
        "category":
            category,
        "count":
            len(products),
        "products":
            json_safe(
                products
            )
    })


# ============================================================
# API GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/anuncio",
    methods=["POST"]
)
def api_anuncio():

    data =
        request.get_json(
            silent=True
        ) or {}

    product =
        data.get(
            "product"
        )

    if not isinstance(
        product,
        dict
    ):
        product = data

    title =
        str(
            product.get(
                "title"
            )
            or "Produto"
        ).strip()

    price =
        product.get(
            "price"
        )

    original =
        product.get(
            "original_price"
        )

    final_price =
        product.get(
            "final_price"
        )

    if final_price is None:
        final_price = price

    discount_percent =
        product.get(
            "discount_percent"
        )

    coupon =
        product.get(
            "coupon"
        )

    coupon_code =
        product.get(
            "coupon_code"
        )

    if not coupon_code and isinstance(
        coupon,
        dict
    ):
        coupon_code =
            coupon.get(
                "code"
            )

    free_shipping =
        bool(
            product.get(
                "free_shipping"
            )
        )

    permalink =
        str(
            product.get(
                "permalink"
            )
            or product.get(
                "affiliate_link"
            )
            or ""
        ).strip()

    lines = [
        "🔥 OFERTA OCULTA / VIP",
        "",
        title,
        ""
    ]

    try:

        original_value =
            float(
                original
                or 0
            )

    except Exception:

        original_value = 0

    try:

        price_value =
            float(
                price
                or 0
            )

    except Exception:

        price_value = 0

    try:

        final_value =
            float(
                final_price
                or price_value
                or 0
            )

    except Exception:

        final_value = price_value

    if (
        original_value > price_value
        and original_value > 0
    ):

        lines.append(
            "~~De "
            + brl(
                original_value
            )
            + "~~"
        )

    if final_value > 0:

        lines.append(
            "💰 Por "
            + brl(
                final_value
            )
            + " 🔥"
        )

    try:

        dp =
            float(
                discount_percent
                or 0
            )

    except Exception:

        dp = 0

    if dp > 0:

        lines.append(
            "📉 "
            + str(
                round(dp)
            )
            + "% OFF"
        )

    if coupon_code:

        lines.append(
            "🎟️ Cupom: "
            + str(
                coupon_code
            )
        )

    if free_shipping:

        lines.append(
            "🚚 Frete grátis"
        )

    if permalink:

        lines.extend([
            "",
            "👉 Pegar promoção:",
            permalink
        ])

    text =
        "\n".join(
            lines
        )

    return jsonify({
        "ok": True,
        "text":
            text,
        "offer":
            json_safe(
                product
            )
    })


# ============================================================
# ENVIO PARA WHATSAPP
# ============================================================

def send_whatsapp(
    text
):

    if not WHATSAPP_BOT_URL:
        return {
            "ok":False,
            "error":
                "WHATSAPP_BOT_URL não configurado."
        }

    if not WHATSAPP_BOT_KEY:
        return {
            "ok":False,
            "error":
                "WHATSAPP_BOT_KEY não configurado."
        }

    url =
        WHATSAPP_BOT_URL
        + "/api/send-offer"

    try:

        response =
            requests.post(
                url,
                headers={
                    "x-bot-key":
                        WHATSAPP_BOT_KEY,

                    "Content-Type":
                        "application/json"
                },
                json={
                    "text":
                        text
                },
                timeout=30
            )

        try:

            result =
                response.json()

        except Exception:

            result = {
                "ok":
                    response.ok,

                "error":
                    response.text[:1000]
            }

        if not response.ok:

            return {
                "ok":False,
                "status":
                    response.status_code,

                "error":
                    result.get(
                        "error"
                    )
                    or "Falha no bot do WhatsApp."
            }

        return result

    except Exception as e:

        return {
            "ok":False,
            "error":
                str(e)
        }


@app.route(
    "/api/enviar-whatsapp",
    methods=["POST"]
)
def api_enviar_whatsapp():

    data =
        request.get_json(
            silent=True
        ) or {}

    text =
        str(
            data.get(
                "text"
            )
            or ""
        ).strip()

    if not text:

        product =
            data.get(
                "product"
            )

        if isinstance(
            product,
            dict
        ):

            result =
                api_anuncio()

            return result

    if not text:

        return jsonify({
            "ok":False,
            "error":
                "Mensagem vazia."
        }), 400

    result =
        send_whatsapp(
            text
        )

    if not result.get(
        "ok"
    ):

        return jsonify(
            result
        ), 502

    return jsonify(
        result
    )


# ============================================================
# TESTE DE INTEGRAÇÃO
# ============================================================

@app.route(
    "/whatsapp/teste"
)
def whatsapp_test():

    text = (
        "🟢 TESTE DE INTEGRAÇÃO — "
        "Caçador de Ofertas conectado "
        "ao WhatsApp com sucesso!"
    )

    result =
        send_whatsapp(
            text
        )

    if not result.get(
        "ok"
    ):

        return jsonify(
            result
        ), 502

    return jsonify({
        "ok":True,
        "message":
            "Teste enviado para o WhatsApp.",
        "result":
            result
    })


# ============================================================
# AUTOMAÇÃO WHATSAPP
# ============================================================

AUTO_INTERVAL =
    int(
        os.getenv(
            "AUTO_INTERVAL_MINUTES",
            "15"
        )
    )

AUTO_LIMIT =
    int(
        os.getenv(
            "AUTO_OFFERS_PER_SCAN",
            "3"
        )
    )

auto_lock =
    threading.Lock()

last_auto_run =
    0


def was_whatsapp_published(
    product_id,
    price
):

    if not product_id:
        return False

    conn =
        get_db()

    row =
        conn.execute(
            """
            SELECT *
            FROM whatsapp_publicacoes
            WHERE product_id=?
            LIMIT 1
            """,
            (
                str(
                    product_id
                ),
            )
        ).fetchone()

    conn.close()

    if not row:
        return False

    try:

        old_price =
            float(
                row["last_price"]
            )

        current_price =
            float(
                price
            )

    except Exception:

        return True

    # Se ficou mais barato, permite publicar novamente.
    if current_price < old_price:
        return False

    return True


def save_whatsapp_publication(
    product
):

    product_id =
        str(
            product.get(
                "item_id"
            )
            or product.get(
                "id"
            )
            or ""
        ).strip()

    if not product_id:
        return

    try:

        price =
            float(
                product.get(
                    "final_price"
                )
                    or product.get(
                        "price"
                    )
                    or 0
            )

    except Exception:

        price = 0

    conn =
        get_db()

    existing =
        conn.execute(
            """
            SELECT id
            FROM whatsapp_publicacoes
            WHERE product_id=?
            """,
            (
                product_id,
            )
        ).fetchone()

    if existing:

        conn.execute(
            """
            UPDATE whatsapp_publicacoes
            SET
                last_price=?,
                last_permalink=?,
                last_title=?,
                published_count=
                    published_count + 1,
                last_published_at=
                    CURRENT_TIMESTAMP
            WHERE product_id=?
            """,
            (
                price,

                product.get(
                    "permalink"
                ),

                product.get(
                    "title"
                ),

                product_id,
            )
        )

    else:

        conn.execute(
            """
            INSERT INTO whatsapp_publicacoes
            (
                product_id,
                last_price,
                last_permalink,
                last_title,
                published_count,
                last_published_at
            )
            VALUES
            (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (
                product_id,
                price,

                product.get(
                    "permalink"
                ),

                product.get(
                    "title"
                )
            )
        )

    conn.commit()
    conn.close()


def automatic_scan():

    global last_auto_run

    if not auto_lock.acquire(
        blocking=False
    ):
        return

    try:

        last_auto_run =
            time.time()

        # Sincroniza cupons antes da busca.
        try:
            sync_coupons()
        except Exception as e:
            print(
                "[AUTO] Falha ao sincronizar cupons:",
                repr(e)
            )

        all_products = []

        categories =
            list(
                CATALOG.keys()
            )

        # Seleciona algumas categorias
        # por rodada para não sobrecarregar
        # a API do Mercado Livre.
        start =
            int(
                time.time()
                / (
                    AUTO_INTERVAL
                    * 60
                )
            )

        selected = []

        if categories:

            for i in range(
                min(
                    3,
                    len(categories)
                )
            ):

                selected.append(
                    categories[
                        (
                            start
                            + i
                        )
                        % len(categories)
                    ]
                )

        for category in selected:

            queries =
                CATALOG.get(
                    category,
                    []
                )

            for query in queries[
                :2
            ]:

                try:

                    products =
                        search_products_automatic(
                            query,
                            category,
                            15
                        )

                    all_products.extend(
                        products
                    )

                except Exception as e:

                    print(
                        "[AUTO] Erro na busca:",
                        query,
                        repr(e)
                    )

        # Remove duplicados.
        unique = {}
        for product in all_products:

            pid =
                str(
                    product.get(
                        "item_id"
                    )
                    or product.get(
                        "id"
                    )
                    or ""
                )

            if pid:
                unique[
                    pid
                ] = product

        candidates =
            list(
                unique.values()
            )

        candidates.sort(
            key=lambda x: (
                float(
                    x.get(
                        "discount_percent",
                        0
                    )
                    or 0
                ),

                float(
                    x.get(
                        "relevance_score",
                        0
                    )
                    or 0
                )
            ),
            reverse=True
        )

        sent = 0

        for product in candidates:

            if sent >= AUTO_LIMIT:
                break

            pid =
                str(
                    product.get(
                        "item_id"
                    )
                    or product.get(
                        "id"
                    )
                    or ""
                )

            if not pid:
                continue

            price =
                product.get(
                    "final_price"
                ) or product.get(
                    "price"
                )

            if was_whatsapp_published(
                pid,
                price
            ):
                continue

            text =
                build_auto_offer_text(
                    product
                )

            result =
                send_whatsapp(
                    text
                )

            if not result.get(
                "ok"
            ):

                print(
                    "[AUTO] WhatsApp falhou:",
                    result
                )

                # Se o WhatsApp falhar, não marca
                # como publicado e encerra a rodada.
                break

            save_whatsapp_publication(
                product
            )

            sent += 1

            print(
                "[AUTO] Oferta enviada:",
                pid
            )

        print(
            f"[AUTO] Rodada concluída. Enviadas: {sent}"
        )

    except Exception as e:

        print(
            "[AUTO] Erro geral:",
            repr(e)
        )

    finally:

        auto_lock.release()


def build_auto_offer_text(
    product
):

    title =
        str(
            product.get(
                "title"
            )
            or "Produto"
        ).strip()

    original =
        product.get(
            "original_price"
        )

    price =
        product.get(
            "price"
        )

    final_price =
        product.get(
            "final_price"
        )

    discount_percent =
        product.get(
            "discount_percent"
        )

    coupon_code =
        product.get(
            "coupon_code"
        )

    coupon =
        product.get(
            "coupon"
        )

    if (
        not coupon_code
        and isinstance(
            coupon,
            dict
        )
    ):

        coupon_code =
            coupon.get(
                "code"
            )

    free_shipping =
        bool(
            product.get(
                "free_shipping"
            )
        )

    permalink =
        str(
            product.get(
                "permalink"
            )
            or product.get(
                "affiliate_link"
            )
            or ""
        ).strip()

    lines = [
        "🔥 OFERTA OCULTA / VIP",
        "",
        title,
        ""
    ]

    try:

        original_value =
            float(
                original
                or 0
            )

    except Exception:

        original_value = 0

    try:

        price_value =
            float(
                price
                or 0
            )

    except Exception:

        price_value = 0

    try:

        final_value =
            float(
                final_price
                or price_value
                or 0
            )

    except Exception:

        final_value = price_value

    if (
        original_value
        > price_value
        and original_value > 0
    ):

        lines.append(
            "~~De "
            + brl(
                original_value
            )
            + "~~"
        )

    lines.append(
        "💰 Por "
        + brl(
            final_value
        )
        + " 🔥"
    )

    try:

        dp =
            float(
                discount_percent
                or 0
            )

    except Exception:

        dp = 0

    if dp > 0:

        lines.append(
            "📉 "
            + str(
                round(dp)
            )
            + "% OFF"
        )

    if coupon_code:

        lines.append(
            "🎟️ Cupom: "
            + str(
                coupon_code
            )
        )

    if free_shipping:

        lines.append(
            "🚚 Frete grátis"
        )

    if permalink:

        lines.extend([
            "",
            "👉 Pegar promoção:",
            permalink
        ])

    return "\n".join(
        lines
    )


def auto_worker():

    # Espera um pouco após o boot para o serviço
    # terminar de subir antes da primeira rodada.
    time.sleep(20)

    while True:

        try:

            automatic_scan()

        except Exception as e:

            print(
                "[AUTO WORKER] Erro:",
                repr(e)
            )

        time.sleep(
            max(
                60,
                AUTO_INTERVAL * 60
            )
        )


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/api/status"
)
def api_status():

    t =
        tokens()

    connected =
        bool(
            t
            and t.get(
                "access_token"
            )
        )

    return jsonify({
        "ok":True,

        "mercadolivre":
            connected,

        "nickname":
            t.get(
                "nickname"
            )
            if t
            else None,

        "whatsapp":
            bool(
                WHATSAPP_BOT_URL
                and WHATSAPP_BOT_KEY
            ),

        "auto_interval_minutes":
            AUTO_INTERVAL,

        "auto_offers_per_scan":
            AUTO_LIMIT
    })


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    t =
        tokens()

    return render_template_string(
        HTML,
        connected=bool(
            t
            and t.get(
                "access_token"
            )
        ),
        nickname=(
            t.get(
                "nickname"
            )
            if t
            else None
        )
    )


# ============================================================
# START AUTOMÁTICO
# ============================================================

_worker_started = False
_worker_lock =
    threading.Lock()


def start_auto_worker():

    global _worker_started

    with _worker_lock:

        if _worker_started:
            return

        _worker_started = True

        thread =
            threading.Thread(
                target=
                    auto_worker,
                daemon=True
            )

        thread.start()


start_auto_worker()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    port =
        int(
            os.getenv(
                "PORT",
                "5000"
            )
        )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )