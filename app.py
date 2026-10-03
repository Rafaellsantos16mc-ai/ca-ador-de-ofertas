import os
import re
import time
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
    "cacador-ofertas-chave-2026"
)

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30
)

DB_FILE = "ofertas.db"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

SITE_ID = "MLB"

CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

REQUEST_TIMEOUT = 10

MAX_QUERIES_PER_CATEGORY = 2
MAX_DISCOVERED_CATEGORIES = 2
MAX_PRODUCTS_PER_QUERY = 4
MAX_ITEMS_PER_PRODUCT = 8
CATEGORY_TIME_LIMIT = 25

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 CacadorDeOfertas/2026"
})


# ============================================================
# CATEGORIAS
# ============================================================

CATALOG = {
    "📱 Celulares": [
        "smartphone",
        "celular",
        "iphone",
        "samsung galaxy",
        "motorola"
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "perfume",
        "kit perfume"
    ],

    # Academia SOMENTE roupas, tenis e suplementos
    "🏋️ Academia": [
        "roupa academia",
        "tenis corrida",
        "whey protein",
        "suplemento",
        "roupa fitness"
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "kit ferramentas",
        "chave de impacto",
        "ferramentas"
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "smartwatch",
        "caixa de som bluetooth",
        "mouse gamer",
        "teclado gamer"
    ],

    "🏠 Casa": [
        "aspirador",
        "ventilador",
        "organizador casa",
        "liquidificador",
        "produto para casa"
    ],

    "🚗 Automotivo": [
        "som automotivo",
        "acessorios automotivos",
        "tapete carro",
        "camera de re",
        "produto automotivo"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "liquidificador",
        "panela",
        "utensilios cozinha",
        "eletrodomestico cozinha"
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "camiseta masculina",
        "vestido feminino",
        "roupas"
    ]
}


SCAN_QUERIES = {
    "📱 Celulares": [
        "smartphone",
        "iphone"
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino"
    ],

    "🏋️ Academia": [
        "roupa academia",
        "whey protein"
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira"
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "smartwatch"
    ],

    "🏠 Casa": [
        "aspirador",
        "liquidificador"
    ],

    "🚗 Automotivo": [
        "som automotivo",
        "acessorios automotivos"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "liquidificador"
    ],

    "👕 Moda": [
        "tenis masculino",
        "camiseta masculina"
    ]
}


# ============================================================
# PERFIS
# ============================================================

PROFILES = {

    "celular": {
        "positive": [
            "smartphone", "celular", "iphone", "galaxy",
            "samsung", "motorola", "xiaomi", "redmi",
            "poco", "realme", "infinix", "tecno",
            "oneplus", "pixel"
        ],
        "negative": [
            "capa", "capinha", "pelicula", "película",
            "suporte", "ventosa", "carregador",
            "cabo", "adaptador", "case"
        ]
    },

    "fone": {
        "positive": [
            "fone", "headphone", "headset",
            "earbuds", "bluetooth", "airpods"
        ],
        "negative": [
            "capa", "case", "suporte",
            "almofada", "cabo"
        ]
    },

    "notebook": {
        "positive": [
            "notebook", "laptop",
            "macbook", "chromebook"
        ],
        "negative": [
            "capa", "suporte", "mochila",
            "pelicula", "película",
            "mouse", "teclado"
        ]
    },

    "tv": {
        "positive": [
            "tv", "televisao", "televisão",
            "smart tv", "televisor"
        ],
        "negative": [
            "suporte tv", "cabo hdmi",
            "controle", "antena"
        ]
    },

    "perfume": {
        "positive": [
            "perfume",
            "eau de parfum",
            "eau de toilette",
            "colonia",
            "colônia"
        ],
        "negative": [
            "frasco vazio",
            "amostra",
            "sache",
            "sachê"
        ]
    },

    "academia": {
        "positive": [
            "roupa academia",
            "fitness",
            "legging",
            "short academia",
            "camiseta academia",
            "top academia",
            "tenis",
            "tênis",
            "corrida",
            "whey",
            "proteina",
            "proteína",
            "creatina",
            "suplemento",
            "bcaa",
            "pre treino",
            "pré treino"
        ],
        "negative": [
            "halter",
            "halteres",
            "anilha",
            "barra",
            "banco",
            "estacao",
            "estação",
            "esteira",
            "bicicleta ergometrica",
            "bicicleta ergométrica",
            "aparelho musculacao",
            "aparelho musculação"
        ]
    },

    "ferramenta": {
        "positive": [
            "furadeira",
            "parafusadeira",
            "chave",
            "ferramenta",
            "kit ferramentas",
            "serra",
            "esmerilhadeira",
            "martelete"
        ],
        "negative": [
            "brinquedo",
            "miniatura",
            "chaveiro"
        ]
    },

    "eletronico": {
        "positive": [
            "fone",
            "headset",
            "smartwatch",
            "relogio inteligente",
            "relógio inteligente",
            "caixa de som",
            "mouse",
            "teclado",
            "eletronico",
            "eletrônico"
        ],
        "negative": []
    },

    "casa": {
        "positive": [
            "aspirador",
            "liquidificador",
            "ventilador",
            "organizador",
            "utilidades domesticas",
            "utilidades domésticas"
        ],
        "negative": []
    },

    "automotivo": {
        "positive": [
            "automotivo",
            "carro",
            "veicular",
            "automovel",
            "automóvel",
            "som automotivo",
            "camera de re",
            "câmera de ré",
            "tapete carro"
        ],
        "negative": []
    },

    "cozinha": {
        "positive": [
            "air fryer",
            "fritadeira",
            "liquidificador",
            "panela",
            "cozinha",
            "eletrodomestico",
            "eletrodoméstico"
        ],
        "negative": []
    },

    "moda": {
        "positive": [
            "tenis",
            "tênis",
            "camiseta",
            "camisa",
            "calca",
            "calça",
            "vestido",
            "roupa",
            "moda",
            "jaqueta"
        ],
        "negative": []
    }
}


# ============================================================
# BANCO
# ============================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS salvos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT,
            preco REAL,
            desconto REAL,
            frete REAL,
            total REAL,
            link TEXT,
            seller_id TEXT,
            produto_id TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def normalizar(texto):

    if texto is None:
        return ""

    texto = str(texto).lower()

    mapa = {
        "á": "a",
        "à": "a",
        "ã": "a",
        "â": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
        "ç": "c"
    }

    for a, b in mapa.items():
        texto = texto.replace(a, b)

    return texto


def money(value):

    try:
        return float(value or 0)
    except Exception:
        return 0.0


def detectar_perfil_busca(query):

    q = normalizar(query)

    if any(x in q for x in [
        "celular",
        "smartphone",
        "iphone",
        "galaxy",
        "motorola",
        "xiaomi",
        "redmi",
        "poco"
    ]):
        return "celular"

    if any(x in q for x in [
        "fone",
        "headset",
        "airpods",
        "earbuds"
    ]):
        return "fone"

    if any(x in q for x in [
        "notebook",
        "laptop",
        "macbook"
    ]):
        return "notebook"

    if any(x in q for x in [
        "smart tv",
        "televisao",
        "tv"
    ]):
        return "tv"

    if any(x in q for x in [
        "perfume",
        "colonia"
    ]):
        return "perfume"

    if any(x in q for x in [
        "academia",
        "whey",
        "creatina",
        "fitness",
        "suplemento"
    ]):
        return "academia"

    if any(x in q for x in [
        "furadeira",
        "parafusadeira",
        "ferramenta",
        "esmerilhadeira",
        "martelete"
    ]):
        return "ferramenta"

    if any(x in q for x in [
        "smartwatch",
        "mouse",
        "teclado",
        "caixa de som"
    ]):
        return "eletronico"

    if any(x in q for x in [
        "aspirador",
        "ventilador",
        "liquidificador",
        "organizador",
        "casa"
    ]):
        return "casa"

    if any(x in q for x in [
        "automotivo",
        "som automotivo",
        "camera de re",
        "tapete carro"
    ]):
        return "automotivo"

    if any(x in q for x in [
        "air fryer",
        "fritadeira",
        "panela",
        "cozinha"
    ]):
        return "cozinha"

    if any(x in q for x in [
        "tenis",
        "camiseta",
        "vestido",
        "roupa"
    ]):
        return "moda"

    return None


def calcular_relevancia(
    titulo,
    query=None,
    perfil=None
):

    texto = normalizar(titulo)

    if not perfil and query:
        perfil = detectar_perfil_busca(query)

    if not perfil:
        return 100

    dados = PROFILES.get(perfil)

    if not dados:
        return 100

    score = 0

    for termo in dados["positive"]:

        if normalizar(termo) in texto:
            score += 25

    for termo in dados["negative"]:

        if normalizar(termo) in texto:
            score -= 60

    if query and normalizar(query) in texto:
        score += 30

    return score


def produto_relevante(
    titulo,
    query=None,
    perfil=None
):

    score = calcular_relevancia(
        titulo,
        query,
        perfil
    )

    if perfil in PROFILES:
        return score > 0

    return True


# ============================================================
# API ML
# ============================================================

def get_token():
    return session.get("ml_access_token")


def api_headers():

    headers = {
        "Accept": "application/json"
    }

    token = get_token()

    if token:
        headers["Authorization"] = (
            f"Bearer {token}"
        )

    return headers


def ml_get(
    path,
    params=None
):

    try:

        return HTTP.get(
            ML_API + path,
            headers=api_headers(),
            params=params or {},
            timeout=REQUEST_TIMEOUT
        )

    except requests.RequestException:

        return None


# ============================================================
# PKCE
# ============================================================

def gerar_pkce():

    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")

    return verifier, challenge


# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def ml_login():

    if not CLIENT_ID:
        return "ML_CLIENT_ID não configurado.", 500

    if not CLIENT_SECRET:
        return "ML_CLIENT_SECRET não configurado.", 500

    verifier, challenge = gerar_pkce()

    state = secrets.token_urlsafe(32)

    session.permanent = True

    session["oauth_state"] = state
    session["pkce_verifier"] = verifier

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256"
    }

    return redirect(
        ML_AUTH + "?" + urlencode(params)
    )


# ============================================================
# CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def ml_callback():

    error = request.args.get("error")

    if error:

        return jsonify({
            "ok": False,
            "erro": error,
            "descricao": request.args.get(
                "error_description"
            )
        }), 400

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:

        return jsonify({
            "ok": False,
            "erro": "Código de autorização não recebido."
        }), 400

    if state != session.get("oauth_state"):

        return jsonify({
            "ok": False,
            "erro": "State OAuth inválido."
        }), 400

    verifier = session.get(
        "pkce_verifier"
    )

    if not verifier:

        return jsonify({
            "ok": False,
            "erro": "PKCE não encontrado."
        }), 400

    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier
    }

    try:

        response = HTTP.post(
            ML_TOKEN,
            data=data,
            timeout=REQUEST_TIMEOUT
        )

        result = response.json()

    except Exception as e:

        return jsonify({
            "ok": False,
            "erro": "Falha ao obter token.",
            "detalhes": str(e)
        }), 500

    if response.status_code >= 400:

        return jsonify({
            "ok": False,
            "erro": "Mercado Livre recusou o token.",
            "status": response.status_code,
            "resposta": result
        }), response.status_code

    access_token = result.get(
        "access_token"
    )

    refresh_token = result.get(
        "refresh_token"
    )

    if not access_token:

        return jsonify({
            "ok": False,
            "erro": "Access token não retornado."
        }), 500

    session.permanent = True

    session["ml_access_token"] = access_token
    session["ml_refresh_token"] = refresh_token
    session["ml_user_id"] = result.get(
        "user_id"
    )

    session.pop(
        "oauth_state",
        None
    )

    session.pop(
        "pkce_verifier",
        None
    )

    return redirect("/")


# ============================================================
# LOGOUT
# ============================================================

@app.route("/mercadolivre/logout")
def ml_logout():

    session.clear()

    return redirect("/")


# ============================================================
# STATUS
# ============================================================

@app.route("/api/mercadolivre/status")
def ml_status():

    token = get_token()

    if not token:

        return jsonify({
            "conectado": False,
            "usuario": None
        })

    response = ml_get(
        "/users/me"
    )

    if response is None:

        return jsonify({
            "conectado": False,
            "usuario": None
        })

    if response.status_code != 200:

        session.pop(
            "ml_access_token",
            None
        )

        return jsonify({
            "conectado": False,
            "usuario": None
        })

    try:
        user = response.json()
    except Exception:
        user = {}

    return jsonify({
        "conectado": True,
        "usuario": {
            "id": user.get("id"),
            "nickname": user.get("nickname"),
            "country_id": user.get("country_id")
        }
    })


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "app": "Cacador de Ofertas",
        "mercadolivre": bool(
            get_token()
        )
    })


# ============================================================
# DIAGNOSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    response = ml_get(
        "/users/me"
    )

    if response is None:

        return jsonify({
            "ok": False,
            "erro": "Falha de conexão."
        }), 500

    try:
        data = response.json()
    except Exception:
        data = response.text

    return jsonify({
        "ok": True,
        "status": response.status_code,
        "usuario": data
    })


# ============================================================
# CATEGORIAS API
# ============================================================

def discover_categories(
    query,
    limite=MAX_DISCOVERED_CATEGORIES
):

    response = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {
            "q": query
        }
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    if isinstance(data, list):

        registros = data

    elif isinstance(data, dict):

        registros = (
            data.get("results")
            or data.get("categories")
            or data.get("data")
            or []
        )

    else:

        registros = []

    categorias = []

    for item in registros:

        if not isinstance(item, dict):
            continue

        category_id = (
            item.get("category_id")
            or item.get("id")
        )

        category_name = (
            item.get("category_name")
            or item.get("name")
        )

        if category_id:

            categorias.append({
                "id": category_id,
                "name": category_name or category_id
            })

        if len(categorias) >= limite:
            break

    return categorias


def get_highlights(category_id):

    response = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    content = data.get(
        "content",
        []
    )

    if not isinstance(content, list):
        return []

    resultado = []

    for item in content:

        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        item_id = item.get("id")

        if (
            item_id
            and item_type in [
                "ITEM",
                "PRODUCT",
                "USER_PRODUCT"
            ]
        ):

            resultado.append({
                "type": item_type,
                "id": item_id
            })

    return resultado


def get_product(product_id):

    response = ml_get(
        f"/products/{product_id}"
    )

    if response is None:
        return None

    if response.status_code != 200:
        return None

    try:
        return response.json()
    except Exception:
        return None


def get_product_items(product_id):

    response = ml_get(
        f"/products/{product_id}/items"
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    results = data.get(
        "results",
        []
    )

    if not isinstance(results, list):
        return []

    return results[
        :MAX_ITEMS_PER_PRODUCT
    ]


# ============================================================
# MONTAR OFERTA
# ============================================================

def montar_oferta(
    item,
    product=None,
    query=None,
    categoria=None
):

    if not isinstance(item, dict):
        return None

    titulo = (
        item.get("title")
        or item.get("name")
        or item.get("product_name")
        or ""
    )

    if not titulo and product:

        titulo = (
            product.get("name")
            or product.get("title")
            or ""
        )

    if not titulo:
        titulo = "Produto Mercado Livre"

    preco = money(
        item.get("price")
    )

    if preco <= 0 and product:

        buy_box = product.get(
            "buy_box_winner"
        )

        if isinstance(
            buy_box,
            dict
        ):

            preco = money(
                buy_box.get("price")
            )

    if preco <= 0:
        return None

    preco_original = money(
        item.get("original_price")
    )

    if preco_original <= 0 and product:

        preco_original = money(
            product.get("original_price")
        )

    desconto = 0

    if preco_original > preco:

        desconto = round(
            (
                (
                    preco_original - preco
                )
                / preco_original
            ) * 100,
            2
        )

    shipping = item.get(
        "shipping"
    ) or {}

    if not isinstance(
        shipping,
        dict
    ):
        shipping = {}

    frete_gratis = bool(
        shipping.get(
            "free_shipping"
        )
    )

    frete = money(
        shipping.get(
            "cost"
        )
    )

    if frete_gratis:
        frete = 0

    total = round(
        preco + frete,
        2
    )

    seller_id = (
        item.get("seller_id")
        or ""
    )

    item_id = (
        item.get("item_id")
        or item.get("id")
        or ""
    )

    user_product_id = (
        item.get("user_product_id")
        or ""
    )

    product_id = ""

    if product:

        product_id = (
            product.get("id")
            or ""
        )

    link = (
        item.get("permalink")
        or item.get("link")
        or ""
    )

    if not link and item_id:

        link = (
            "https://www.mercadolivre.com.br/p/"
            + str(item_id)
        )

    perfil = detectar_perfil_busca(
        query or ""
    )

    relevancia = calcular_relevancia(
        titulo,
        query,
        perfil
    )

    if (
        perfil
        and not produto_relevante(
            titulo,
            query,
            perfil
        )
    ):
        return None

    return {
        "titulo": titulo,
        "preco": preco,
        "preco_original": preco_original,
        "desconto": desconto,
        "frete": frete,
        "frete_gratis": frete_gratis,
        "total": total,
        "seller_id": str(seller_id),
        "item_id": item_id,
        "product_id": product_id,
        "user_product_id": user_product_id,
        "link": link,
        "query": query or "",
        "categoria": categoria or "",
        "relevancia": relevancia,
        "condicao": item.get(
            "condition",
            "new"
        )
    }


# ============================================================
# SCORE
# ============================================================

def score_oferta(oferta):

    score = 0

    desconto = money(
        oferta.get("desconto")
    )

    total = money(
        oferta.get("total")
    )

    relevancia = money(
        oferta.get("relevancia")
    )

    score += min(
        relevancia,
        120
    )

    if desconto >= 50:
        score += 55

    elif desconto >= 30:
        score += 40

    elif desconto >= 20:
        score += 30

    elif desconto >= 10:
        score += 18

    elif desconto >= 5:
        score += 8

    if oferta.get(
        "frete_gratis"
    ):
        score += 25

    if total <= 50:
        score += 8

    elif total <= 100:
        score += 6

    elif total <= 300:
        score += 4

    return round(
        score,
        2
    )


# ============================================================
# DEDUPLICAÇÃO
# ============================================================

def chave_oferta(oferta):

    titulo = normalizar(
        oferta.get(
            "titulo",
            ""
        )
    )

    seller = str(
        oferta.get(
            "seller_id",
            ""
        )
    )

    return (
        titulo[:150],
        seller
    )


def deduplicar_ofertas(ofertas):

    melhores = {}

    for oferta in ofertas:

        chave = chave_oferta(
            oferta
        )

        atual = melhores.get(
            chave
        )

        if atual is None:

            melhores[chave] = oferta

            continue

        if (
            oferta.get(
                "total",
                999999
            )
            <
            atual.get(
                "total",
                999999
            )
        ):

            melhores[chave] = oferta

    resultado = list(
        melhores.values()
    )

    for oferta in resultado:

        oferta["score_oferta"] = score_oferta(
            oferta
        )

    resultado.sort(
        key=lambda x: (
            -x.get(
                "score_oferta",
                0
            ),
            x.get(
                "total",
                999999
            )
        )
    )

    return resultado


# ============================================================
# MODELO
# ============================================================

def chave_modelo(titulo):

    texto = normalizar(
        titulo
    )

    texto = re.sub(
        r"\b(16gb|32gb|64gb|128gb|256gb|512gb|1tb|2tb)\b",
        "",
        texto
    )

    texto = re.sub(
        r"\b(2gb|3gb|4gb|6gb|8gb|12gb|16gb|24gb)\s*ram\b",
        "",
        texto
    )

    texto = re.sub(
        r"\b(preto|branco|azul|verde|rosa|vermelho|cinza|dourado|prata|roxo)\b",
        "",
        texto
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    ).strip()

    return texto[:130]


def marcar_menores_precos(ofertas):

    grupos = {}

    for oferta in ofertas:

        chave = chave_modelo(
            oferta.get(
                "titulo",
                ""
            )
        )

        grupos.setdefault(
            chave,
            []
        ).append(
            oferta
        )

    for grupo in grupos.values():

        grupo.sort(
            key=lambda x:
                x.get(
                    "total",
                    999999
                )
        )

        for index, oferta in enumerate(
            grupo
        ):

            oferta["menor_preco"] = (
                index == 0
            )

    return ofertas


# ============================================================
# SCAN
# ============================================================

def scan_query(
    query,
    categoria=None,
    limite_tempo=CATEGORY_TIME_LIMIT
):

    inicio = time.time()

    categorias = discover_categories(
        query,
        MAX_DISCOVERED_CATEGORIES
    )

    if not categorias:
        return []

    resultados = []

    produtos_processados = 0
    vistos = set()

    for categoria_api in categorias:

        if (
            time.time() - inicio
            >= limite_tempo
        ):
            break

        highlights = get_highlights(
            categoria_api["id"]
        )

        if not highlights:
            continue

        for ref in highlights:

            if (
                time.time() - inicio
                >= limite_tempo
            ):
                break

            product_id = ref.get(
                "id"
            )

            if not product_id:
                continue

            if product_id in vistos:
                continue

            vistos.add(
                product_id
            )

            if (
                produtos_processados
                >= MAX_PRODUCTS_PER_QUERY
            ):
                break

            produtos_processados += 1

            product = get_product(
                product_id
            )

            if not product:
                continue

            items = get_product_items(
                product_id
            )

            if not items:
                continue

            for item in items:

                oferta = montar_oferta(
                    item=item,
                    product=product,
                    query=query,
                    categoria=categoria
                )

                if oferta:
                    resultados.append(
                        oferta
                    )

    return resultados


# ============================================================
# BUSCA MANUAL
# ============================================================

def buscar_manual(query):

    resultados = scan_query(
        query,
        "Busca"
    )

    resultados = deduplicar_ofertas(
        resultados
    )

    resultados = marcar_menores_precos(
        resultados
    )

    return resultados[:60]


# ============================================================
# CAÇAR CATEGORIA
# ============================================================

def cacar_categoria(categoria):

    consultas = SCAN_QUERIES.get(
        categoria,
        CATALOG.get(
            categoria,
            []
        )
    )

    consultas = consultas[
        :MAX_QUERIES_PER_CATEGORY
    ]

    inicio = time.time()

    todos = []

    for query in consultas:

        restante = (
            CATEGORY_TIME_LIMIT
            -
            (
                time.time()
                -
                inicio
            )
        )

        if restante <= 0:
            break

        try:

            resultados = scan_query(
                query=query,
                categoria=categoria,
                limite_tempo=restante
            )

            todos.extend(
                resultados
            )

        except Exception:

            continue

    todos = deduplicar_ofertas(
        todos
    )

    todos = marcar_menores_precos(
        todos
    )

    return todos[:40]


# ============================================================
# API BUSCAR
# ============================================================

@app.route("/api/buscar")
def api_buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return jsonify({
            "ok": False,
            "erro": "Digite uma busca."
        }), 400

    inicio = time.time()

    try:

        resultados = buscar_manual(
            query
        )

    except Exception as e:

        return jsonify({
            "ok": False,
            "erro": str(e)
        }), 500

    return jsonify({
        "ok": True,
        "query": query,
        "perfil": detectar_perfil_busca(
            query
        ),
        "tempo": round(
            time.time() - inicio,
            2
        ),
        "total": len(resultados),
        "resultados": resultados
    })


# ============================================================
# API CAÇAR CATEGORIA
# ============================================================

@app.route("/api/cacar")
def api_cacar():

    categoria = request.args.get(
        "categoria",
        ""
    ).strip()

    if categoria not in CATALOG:

        return jsonify({
            "ok": False,
            "erro": "Categoria inválida."
        }), 400

    inicio = time.time()

    try:

        resultados = cacar_categoria(
            categoria
        )

        return jsonify({
            "ok": True,
            "categoria": categoria,
            "tempo": round(
                time.time() - inicio,
                2
            ),
            "total": len(resultados),
            "resultados": resultados
        })

    except Exception as e:

        return jsonify({
            "ok": False,
            "categoria": categoria,
            "erro": str(e),
            "resultados": []
        }), 500


# ============================================================
# CUPONS
# ============================================================

def buscar_cupons():

    try:

        response = HTTP.get(
            "https://www.mercadolivre.com.br/l/promocoes",
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=8
        )

        if response.status_code != 200:
            return []

        html = response.text

    except Exception:

        return []

    padrao = re.compile(
        r"(?i)(?:código|codigo|cupom|usar cupom)"
        r".{0,120}?([A-Z0-9]{5,20})"
    )

    encontrados = padrao.findall(
        html
    )

    cupons = []
    vistos = set()

    for codigo in encontrados:

        codigo = codigo.upper().strip()

        if codigo in vistos:
            continue

        vistos.add(
            codigo
        )

        if codigo in [
            "MERCADOLIVRE",
            "MERCADOPAGO",
            "PROMOCOES",
            "PROMOÇÕES",
            "OFERTAS"
        ]:
            continue

        cupons.append({
            "codigo": codigo,
            "estimativa": True
        })

        if len(cupons) >= 20:
            break

    return cupons


@app.route("/api/cupons")
def api_cupons():

    return jsonify({
        "ok": True,
        "cupons": buscar_cupons()
    })


@app.route("/cupons")
def cupons():

    return jsonify({
        "ok": True,
        "cupons": buscar_cupons()
    })


# ============================================================
# GERAR ANUNCIO
# ============================================================

@app.route(
    "/api/gerar-anuncio",
    methods=["POST"]
)
def gerar_anuncio():

    data = request.get_json(
        silent=True
    ) or {}

    titulo = data.get(
        "titulo",
        "Oferta"
    )

    preco = money(
        data.get("preco")
    )

    original = money(
        data.get("preco_original")
    )

    desconto = money(
        data.get("desconto")
    )

    link = data.get(
        "link",
        ""
    )

    frete_gratis = bool(
        data.get("frete_gratis")
    )

    categoria = data.get(
        "categoria",
        ""
    )

    linhas = [
        "🔥 OFERTA ENCONTRADA!",
        ""
    ]

    if categoria:

        linhas.append(
            f"📌 {categoria}"
        )

        linhas.append("")

    linhas.append(
        f"🛒 {titulo}"
    )

    linhas.append("")

    if original > preco and desconto > 0:

        linhas.append(
            f"💸 De R$ {original:.2f} por R$ {preco:.2f}"
        )

        linhas.append(
            f"🏷️ {desconto:.0f}% OFF"
        )

    else:

        linhas.append(
            f"💰 Por apenas R$ {preco:.2f}"
        )

    if frete_gratis:

        linhas.append(
            "🚚 FRETE GRÁTIS"
        )

    linhas.extend([
        "",
        "👉 Confira:",
        link,
        "",
        "⚠️ Preço e disponibilidade podem mudar."
    ])

    return jsonify({
        "ok": True,
        "texto": "\n".join(
            linhas
        )
    })


# ============================================================
# SALVAR
# ============================================================

@app.route(
    "/api/salvar",
    methods=["POST"]
)
def salvar():

    data = request.get_json(
        silent=True
    ) or {}

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.execute("""
        INSERT INTO salvos (
            titulo,
            preco,
            desconto,
            frete,
            total,
            link,
            seller_id,
            produto_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("titulo"),
        money(data.get("preco")),
        money(data.get("desconto")),
        money(data.get("frete")),
        money(data.get("total")),
        data.get("link"),
        str(
            data.get("seller_id")
            or ""
        ),
        str(
            data.get("item_id")
            or data.get("product_id")
            or ""
        )
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True
    })


# ============================================================
# SALVOS
# ============================================================

@app.route("/api/salvos")
def api_salvos():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT *
        FROM salvos
        ORDER BY id DESC
        LIMIT 100
    """).fetchall()

    conn.close()

    return jsonify({
        "ok": True,
        "salvos": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# TESTES
# ============================================================

@app.route("/mercadolivre/teste-categoria")
def teste_categoria():

    query = request.args.get(
        "q",
        "celular"
    )

    return jsonify({
        "query": query,
        "resultado": discover_categories(
            query
        )
    })


@app.route("/mercadolivre/teste-highlights")
def teste_highlights():

    category = request.args.get(
        "category",
        ""
    )

    if not category:

        return jsonify({
            "ok": False,
            "erro": "Use ?category=MLB..."
        }), 400

    return jsonify(
        get_highlights(
            category
        )
    )


@app.route("/mercadolivre/teste-produto")
def teste_produto():

    product_id = request.args.get(
        "id",
        ""
    )

    if not product_id:

        return jsonify({
            "ok": False,
            "erro": "Use ?id=MLB..."
        }), 400

    produto = get_product(
        product_id
    )

    if not produto:

        return jsonify({
            "ok": False,
            "erro": "Produto não encontrado."
        }), 404

    return jsonify(
        produto
    )


@app.route("/mercadolivre/teste-produto-itens")
def teste_produto_itens():

    product_id = request.args.get(
        "id",
        ""
    )

    if not product_id:

        return jsonify({
            "ok": False,
            "erro": "Use ?id=MLB..."
        }), 400

    itens = get_product_items(
        product_id
    )

    return jsonify({
        "ok": True,
        "product_id": product_id,
        "total": len(itens),
        "itens": itens
    })


@app.route("/mercadolivre/teste-busca")
def teste_busca():

    query = request.args.get(
        "q",
        "celular"
    )

    inicio = time.time()

    resultados = buscar_manual(
        query
    )

    return jsonify({
        "ok": True,
        "query": query,
        "tempo": round(
            time.time() - inicio,
            2
        ),
        "total": len(resultados),
        "resultados": resultados
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
    background: #0f1117;
    color: #fff;
    font-family: Arial, Helvetica, sans-serif;
}

.container {
    max-width: 1100px;
    margin: auto;
    padding: 16px;
}

.header {
    background: linear-gradient(
        135deg,
        #171b24,
        #202735
    );
    border-radius: 20px;
    padding: 22px;
    margin-bottom: 15px;
}

.header h1 {
    margin: 0 0 8px;
    font-size: 28px;
}

.header p {
    margin: 0;
    color: #aeb7c5;
    line-height: 1.5;
}

.buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 9px;
    margin-top: 20px;
}

.btn {
    border: 0;
    border-radius: 12px;
    padding: 14px 18px;
    font-weight: bold;
    font-size: 15px;
    cursor: pointer;
}

.btn-primary {
    background: #00a650;
    color: white;
}

.btn-secondary {
    background: #2b313d;
    color: white;
    text-decoration: none;
}

.btn:disabled {
    opacity: .55;
}

.connection {
    margin-top: 14px;
    padding: 11px 14px;
    border-radius: 11px;
    font-weight: bold;
}

.connected {
    background: #103b27;
    color: #55e494;
}

.disconnected {
    background: #3b1c1c;
    color: #ff8585;
}

.search {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
}

.search input {
    flex: 1;
    min-width: 0;
    background: #181c24;
    border: 1px solid #343b49;
    border-radius: 13px;
    padding: 15px;
    color: white;
    font-size: 16px;
}

.categories {
    display: grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px, 1fr)
        );
    gap: 9px;
    margin-bottom: 15px;
}

.category {
    min-height: 62px;
    border-radius: 14px;
    border: 1px solid #303744;
    background: #181c24;
    color: white;
    font-weight: bold;
    font-size: 14px;
    cursor: pointer;
}

.category.active {
    border-color: #00a650;
    background: #103b27;
}

.status {
    background: #181c24;
    border-radius: 14px;
    padding: 15px;
    margin-bottom: 15px;
    color: #cbd3df;
}

.progress {
    height: 7px;
    background: #303642;
    border-radius: 20px;
    margin-top: 10px;
    overflow: hidden;
}

.progress-bar {
    height: 100%;
    width: 0%;
    background: #00a650;
    transition: width .25s ease;
}

.stats {
    display: grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(130px, 1fr)
        );
    gap: 9px;
    margin-bottom: 15px;
}

.stat {
    background: #181c24;
    border-radius: 14px;
    padding: 15px;
}

.stat strong {
    display: block;
    font-size: 23px;
    margin-bottom: 4px;
}

.stat span {
    color: #8f99a9;
    font-size: 13px;
}

.results {
    display: grid;
    gap: 12px;
}

.card {
    background: #181c24;
    border: 1px solid #2d3440;
    border-radius: 17px;
    padding: 17px;
}

.card.best {
    border-color: #00a650;
    box-shadow:
        0 0 0 1px rgba(
            0,
            166,
            80,
            .15
        );
}

.badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 10px;
}

.badge {
    padding: 6px 9px;
    border-radius: 20px;
    background: #2c333f;
    color: #e3e7ed;
    font-size: 11px;
    font-weight: bold;
}

.badge.green {
    background: #103e29;
    color: #58e498;
}

.badge.yellow {
    background: #4d3e09;
    color: #ffe16a;
}

.badge.blue {
    background: #182f4b;
    color: #78b9ff;
}

.card h3 {
    margin: 8px 0 12px;
    line-height: 1.4;
    font-size: 18px;
}

.price {
    font-size: 28px;
    font-weight: bold;
    color: #4be18b;
}

.old-price {
    margin-left: 8px;
    color: #7e8795;
    text-decoration: line-through;
}

.info {
    color: #aeb6c4;
    font-size: 13px;
    line-height: 1.7;
    margin-top: 9px;
}

.score {
    color: #55e494;
    font-weight: bold;
}

.actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 14px;
}

.actions button,
.actions a {
    border: 0;
    border-radius: 10px;
    padding: 11px 13px;
    text-decoration: none;
    font-weight: bold;
    cursor: pointer;
}

.open {
    background: #00a650;
    color: white;
}

.copy {
    background: #303743;
    color: white;
}

.empty {
    text-align: center;
    color: #8d97a6;
    padding: 40px 10px;
}

.notice {
    background: #29250f;
    border: 1px solid #655313;
    color: #ffe27b;
    padding: 12px;
    border-radius: 12px;
    margin-bottom: 15px;
    font-size: 12px;
    line-height: 1.5;
}

@media (max-width: 600px) {

    .container {
        padding: 10px;
    }

    .search {
        flex-direction: column;
    }

    .search .btn {
        width: 100%;
    }

    .header h1 {
        font-size: 24px;
    }

    .price {
        font-size: 25px;
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
            Encontre ofertas, compare vendedores e
            descubra oportunidades automaticamente.
        </p>

        <div class="buttons">

            <button
                id="btnCacar"
                class="btn btn-primary"
                type="button"
                onclick="cacarTodas()"
            >
                🚀 CAÇAR TODAS
            </button>

            <button
                id="btnMercadoLivre"
                class="btn btn-secondary"
                type="button"
                onclick="conectarMercadoLivre()"
            >
                🔗 Conectar Mercado Livre
            </button>

            <a
                class="btn btn-secondary"
                href="/api/salvos"
                target="_blank"
            >
                💾 Salvos
            </a>

        </div>

        <div
            id="connectionStatus"
            class="connection disconnected"
        >
            🔴 Mercado Livre desconectado
        </div>

    </div>

    <div class="notice">

        ⚠️ Descontos, frete e disponibilidade podem
        mudar no Mercado Livre. Sempre confirme a oferta
        antes de publicar.

    </div>

    <div class="search">

        <input
            id="search"
            type="text"
            placeholder="Digite: celular, perfume, fone..."
            onkeydown="if(event.key === 'Enter') buscar()"
        >

        <button
            class="btn btn-primary"
            type="button"
            onclick="buscar()"
        >
            🔎 BUSCAR
        </button>

    </div>

    <div
        id="categories"
        class="categories"
    ></div>

    <div class="status">

        <div id="statusText">
            Pronto para caçar ofertas.
        </div>

        <div class="progress">

            <div
                id="progressBar"
                class="progress-bar"
            ></div>

        </div>

    </div>

    <div class="stats">

        <div class="stat">

            <strong id="statProducts">
                0
            </strong>

            <span>
                Ofertas
            </span>

        </div>

        <div class="stat">

            <strong id="statBest">
                R$ 0,00
            </strong>

            <span>
                Menor preço
            </span>

        </div>

        <div class="stat">

            <strong id="statDiscount">
                0%
            </strong>

            <span>
                Maior desconto
            </span>

        </div>

        <div class="stat">

            <strong id="statCategories">
                0
            </strong>

            <span>
                Categorias
            </span>

        </div>

    </div>

    <div
        id="results"
        class="results"
    >

        <div class="empty">
            Clique em uma categoria ou em
            <b>CAÇAR TODAS</b>.
        </div>

    </div>

</div>


<script>

const CATEGORIES =
    {{ categories | tojson }};

let resultadosGlobais = [];


function money(value) {

    return Number(
        value || 0
    ).toLocaleString(
        "pt-BR",
        {
            style: "currency",
            currency: "BRL"
        }
    );
}


function escapeHtml(value) {

    return String(
        value || ""
    )
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


function sleep(ms) {

    return new Promise(
        resolve =>
            setTimeout(
                resolve,
                ms
            )
    );
}


function setStatus(
    texto,
    progresso
) {

    document.getElementById(
        "statusText"
    ).innerText = texto;

    document.getElementById(
        "progressBar"
    ).style.width =
        Math.max(
            0,
            Math.min(
                100,
                progresso || 0
            )
        ) + "%";
}


/* =========================================================
   MERCADO LIVRE
   ========================================================= */

function conectarMercadoLivre() {

    setStatus(
        "🔗 Abrindo autorização do Mercado Livre...",
        5
    );

    window.location.href =
        "/mercadolivre/login";
}


async function verificarMercadoLivre() {

    try {

        const response =
            await fetch(
                "/api/mercadolivre/status",
                {
                    cache: "no-store"
                }
            );

        const data =
            await response.json();

        const status =
            document.getElementById(
                "connectionStatus"
            );

        const button =
            document.getElementById(
                "btnMercadoLivre"
            );

        if (data.conectado) {

            const usuario =
                data.usuario || {};

            status.className =
                "connection connected";

            status.innerText =
                "🟢 Mercado Livre conectado"
                +
                (
                    usuario.nickname
                    ?
                    " — " +
                    usuario.nickname
                    :
                    ""
                );

            button.innerText =
                "🟢 Mercado Livre conectado";

        } else {

            status.className =
                "connection disconnected";

            status.innerText =
                "🔴 Mercado Livre desconectado";

            button.innerText =
                "🔗 Conectar Mercado Livre";
        }

    } catch (error) {

        console.error(error);
    }
}


/* =========================================================
   CATEGORIAS
   ========================================================= */

function renderCategories() {

    const box =
        document.getElementById(
            "categories"
        );

    box.innerHTML = "";

    CATEGORIES.forEach(
        categoria => {

            const button =
                document.createElement(
                    "button"
                );

            button.className =
                "category";

            button.innerText =
                categoria;

            button.onclick =
                () => {

                    document
                        .querySelectorAll(
                            ".category"
                        )
                        .forEach(
                            x =>
                                x.classList.remove(
                                    "active"
                                )
                        );

                    button.classList.add(
                        "active"
                    );

                    cacarCategoria(
                        categoria,
                        true
                    );
                };

            box.appendChild(
                button
            );
        }
    );
}


/* =========================================================
   STATS
   ========================================================= */

function updateStats(lista) {

    const products =
        document.getElementById(
            "statProducts"
        );

    const best =
        document.getElementById(
            "statBest"
        );

    const discount =
        document.getElementById(
            "statDiscount"
        );

    const categories =
        document.getElementById(
            "statCategories"
        );

    products.innerText =
        lista.length;

    if (!lista.length) {

        best.innerText =
            "R$ 0,00";

        discount.innerText =
            "0%";

        categories.innerText =
            "0";

        return;
    }

    const menor =
        Math.min(
            ...lista.map(
                x =>
                    Number(
                        x.total ||
                        x.preco ||
                        0
                    )
            )
        );

    const maiorDesconto =
        Math.max(
            ...lista.map(
                x =>
                    Number(
                        x.desconto ||
                        0
                    )
            )
        );

    const cats =
        new Set(
            lista.map(
                x =>
                    x.categoria
            )
        );

    best.innerText =
        money(
            menor
        );

    discount.innerText =
        maiorDesconto.toFixed(0)
        + "%";

    categories.innerText =
        cats.size;
}


/* =========================================================
   RESULTADOS
   ========================================================= */

function renderResults(lista) {

    const box =
        document.getElementById(
            "results"
        );

    if (!lista.length) {

        box.innerHTML = `
            <div class="empty">
                Nenhuma oferta encontrada.
            </div>
        `;

        updateStats([]);

        return;
    }

    box.innerHTML = "";

    lista.forEach(
        item => {

            const card =
                document.createElement(
                    "div"
                );

            card.className =
                "card " +
                (
                    item.menor_preco
                    ?
                    "best"
                    :
                    ""
                );

            let badges = "";

            if (
                item.menor_preco
            ) {

                badges += `
                    <span class="badge green">
                        💰 MENOR PREÇO
                    </span>
                `;
            }

            if (
                Number(
                    item.desconto || 0
                ) > 0
            ) {

                badges += `
                    <span class="badge yellow">
                        🏷️ ${
                            item.desconto
                        }% OFF
                    </span>
                `;
            }

            if (
                item.frete_gratis
            ) {

                badges += `
                    <span class="badge green">
                        🚚 FRETE GRÁTIS
                    </span>
                `;
            }

            if (
                item.categoria
            ) {

                badges += `
                    <span class="badge blue">
                        ${
                            escapeHtml(
                                item.categoria
                            )
                        }
                    </span>
                `;
            }

            card.innerHTML = `

                <div class="badges">
                    ${badges}
                </div>

                <h3>
                    ${
                        escapeHtml(
                            item.titulo
                        )
                    }
                </h3>

                <div>

                    <span class="price">
                        ${
                            money(
                                item.preco
                            )
                        }
                    </span>

                    ${
                        Number(
                            item.preco_original || 0
                        )
                        >
                        Number(
                            item.preco || 0
                        )
                        ?
                        `
                        <span class="old-price">
                            ${
                                money(
                                    item.preco_original
                                )
                            }
                        </span>
                        `
                        :
                        ""
                    }

                </div>

                <div class="info">

                    💰 Total:
                    <b>
                        ${
                            money(
                                item.total
                            )
                        }
                    </b>

                    <br>

                    🚚 Frete:
                    ${
                        item.frete_gratis
                        ?
                        "Grátis"
                        :
                        money(
                            item.frete
                        )
                    }

                    <br>

                    👤 Vendedor:
                    ${
                        escapeHtml(
                            item.seller_id || "-"
                        )
                    }

                    <br>

                    ⭐ Relevância:
                    ${
                        item.relevancia ?? "-"
                    }

                    <br>

                    🔥 Score:
                    <span class="score">
                        ${
                            item.score_oferta ?? "-"
                        }
                    </span>

                </div>

                <div class="actions">

                    ${
                        item.link
                        ?
                        `
                        <a
                            class="open"
                            href="${
                                escapeHtml(
                                    item.link
                                )
                            }"
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            🛒 ABRIR OFERTA
                        </a>
                        `
                        :
                        ""
                    }

                    <button
                        class="copy"
                        type="button"
                        onclick='copiarAnuncio(${JSON.stringify(item)})'
                    >
                        📋 COPIAR
                    </button>

                    <button
                        class="copy"
                        type="button"
                        onclick='salvarOferta(${JSON.stringify(item)})'
                    >
                        💾 SALVAR
                    </button>

                </div>
            `;

            box.appendChild(
                card
            );
        }
    );

    updateStats(
        lista
    );
}


/* =========================================================
   BUSCA MANUAL
   ========================================================= */

async function buscar() {

    const query =
        document.getElementById(
            "search"
        ).value.trim();

    if (!query) {
        return;
    }

    setStatus(
        `🔎 Buscando "${query}"...`,
        10
    );

    try {

        const response =
            await fetch(
                "/api/buscar?q="
                +
                encodeURIComponent(
                    query
                )
            );

        const data =
            await response.json();

        if (!data.ok) {

            setStatus(
                "❌ "
                +
                (
                    data.erro ||
                    "Erro na busca."
                ),
                0
            );

            return;
        }

        resultadosGlobais =
            data.resultados || [];

        renderResults(
            resultadosGlobais
        );

        setStatus(
            `✅ ${resultadosGlobais.length} ofertas encontradas.`,
            100
        );

    } catch (error) {

        console.error(error);

        setStatus(
            "❌ Erro de conexão.",
            0
        );
    }
}


/* =========================================================
   CAÇAR UMA CATEGORIA
   ========================================================= */

/*
    IMPORTANTE:

    Quando CAÇAR TODAS estiver funcionando,
    esta função NÃO pode substituir resultadosGlobais.

    Ela apenas retorna os resultados da categoria.

    O parâmetro atualizarTela controla se deve
    substituir a busca atual quando o usuário
    clicar diretamente em uma categoria.
*/

async function cacarCategoria(
    categoria,
    atualizarTela = true
) {

    setStatus(
        `🔎 Caçando ${categoria}...`,
        10
    );

    try {

        const response =
            await fetch(
                "/api/cacar?categoria="
                +
                encodeURIComponent(
                    categoria
                ),
                {
                    cache: "no-store"
                }
            );

        const data =
            await response.json();

        if (!data.ok) {

            setStatus(
                "❌ "
                +
                (
                    data.erro ||
                    "Erro."
                ),
                0
            );

            return [];
        }

        const resultados =
            data.resultados || [];

        /*
            CORREÇÃO PRINCIPAL:

            Só altera resultadosGlobais quando
            estamos fazendo uma busca individual.

            Durante CAÇAR TODAS, os resultados
            serão adicionados pelo cacarTodas().
        */

        if (atualizarTela) {

            resultadosGlobais =
                resultados;

            renderResults(
                resultadosGlobais
            );
        }

        setStatus(
            `✅ ${categoria} — ${resultados.length} ofertas.`,
            100
        );

        return resultados;

    } catch (error) {

        console.error(error);

        setStatus(
            `⚠️ Erro em ${categoria}.`,
            0
        );

        return [];
    }
}


/* =========================================================
   CAÇAR TODAS
   ========================================================= */

async function cacarTodas() {

    const button =
        document.getElementById(
            "btnCacar"
        );

    button.disabled = true;

    resultadosGlobais = [];

    renderResults([]);

    const total =
        CATEGORIES.length;

    for (
        let i = 0;
        i < total;
        i++
    ) {

        const categoria =
            CATEGORIES[i];

        setStatus(
            `🚀 ${i + 1}/${total} — Caçando ${categoria}...`,
            Math.round(
                (
                    i /
                    total
                ) * 100
            )
        );

        /*
            AQUI ESTÁ A OUTRA PARTE DA CORREÇÃO.

            false = não substituir resultadosGlobais.
        */

        const resultados =
            await cacarCategoria(
                categoria,
                false
            );

        /*
            Agora adicionamos as ofertas
            da categoria atual às anteriores.
        */

        if (
            Array.isArray(
                resultados
            )
        ) {

            resultadosGlobais =
                resultadosGlobais.concat(
                    resultados
                );
        }

        /*
            Remove duplicados.
        */

        resultadosGlobais =
            dedupeClient(
                resultadosGlobais
            );

        /*
            Marca menor preço por modelo.
        */

        resultadosGlobais =
            marcarMenoresClient(
                resultadosGlobais
            );

        /*
            Ordena pelas melhores ofertas.
        */

        resultadosGlobais.sort(
            (a, b) => {

                const scoreA =
                    Number(
                        a.score_oferta ||
                        0
                    );

                const scoreB =
                    Number(
                        b.score_oferta ||
                        0
                    );

                if (
                    scoreA !==
                    scoreB
                ) {

                    return (
                        scoreB -
                        scoreA
                    );
                }

                return (
                    Number(
                        a.total ||
                        999999
                    )
                    -
                    Number(
                        b.total ||
                        999999
                    )
                );
            }
        );

        /*
            MOSTRA NA TELA O QUE JÁ FOI ENCONTRADO.

            Assim você consegue acompanhar
            a caça enquanto ela acontece.
        */

        renderResults(
            resultadosGlobais
        );

        setStatus(
            `✅ ${categoria} concluída — ${resultadosGlobais.length} ofertas acumuladas.`,
            Math.round(
                (
                    (i + 1)
                    /
                    total
                ) * 100
            )
        );

        await sleep(200);
    }

    /*
        GARANTIA FINAL:

        Reprocessa tudo antes de terminar.
    */

    resultadosGlobais =
        dedupeClient(
            resultadosGlobais
        );

    resultadosGlobais =
        marcarMenoresClient(
            resultadosGlobais
        );

    resultadosGlobais.sort(
        (a, b) => {

            const scoreA =
                Number(
                    a.score_oferta ||
                    0
                );

            const scoreB =
                Number(
                    b.score_oferta ||
                    0
                );

            if (
                scoreA !==
                scoreB
            ) {

                return (
                    scoreB -
                    scoreA
                );
            }

            return (
                Number(
                    a.total ||
                    999999
                )
                -
                Number(
                    b.total ||
                    999999
                )
            );
        }
    );

    renderResults(
        resultadosGlobais
    );

    setStatus(
        `🏆 CAÇA FINALIZADA — ${resultadosGlobais.length} ofertas encontradas.`,
        100
    );

    button.disabled = false;
}


/* =========================================================
   DUPLICADOS NO NAVEGADOR
   ========================================================= */

function dedupeClient(lista) {

    const map =
        new Map();

    lista.forEach(
        item => {

            const key =
                (
                    String(
                        item.titulo || ""
                    )
                    +
                    "|"
                    +
                    String(
                        item.seller_id || ""
                    )
                )
                .toLowerCase();

            const atual =
                map.get(
                    key
                );

            if (
                !atual
                ||
                Number(
                    item.total ||
                    999999
                )
                <
                Number(
                    atual.total ||
                    999999
                )
            ) {

                map.set(
                    key,
                    item
                );
            }
        }
    );

    return Array.from(
        map.values()
    );
}


/* =========================================================
   MENOR PREÇO
   ========================================================= */

function normalizarTitulo(
    texto
) {

    return String(
        texto || ""
    )
    .toLowerCase()
    .replace(
        /\b(16gb|32gb|64gb|128gb|256gb|512gb|1tb|2tb)\b/g,
        ""
    )
    .replace(
        /\b(2gb|3gb|4gb|6gb|8gb|12gb|16gb|24gb)\s*ram\b/g,
        ""
    )
    .replace(
        /\b(preto|branco|azul|verde|rosa|vermelho|cinza|dourado|prata|roxo)\b/g,
        ""
    )
    .replace(
        /\s+/g,
        " "
    )
    .trim()
    .substring(
        0,
        130
    );
}


function marcarMenoresClient(
    lista
) {

    const grupos = {};

    lista.forEach(
        item => {

            const chave =
                normalizarTitulo(
                    item.titulo
                );

            if (!grupos[chave]) {

                grupos[chave] = [];
            }

            grupos[chave].push(
                item
            );
        }
    );

    Object.values(
        grupos
    ).forEach(
        grupo => {

            grupo.sort(
                (a, b) =>
                    Number(
                        a.total ||
                        999999
                    )
                    -
                    Number(
                        b.total ||
                        999999
                    )
            );

            grupo.forEach(
                (item, index) => {

                    item.menor_preco =
                        index === 0;
                }
            );
        }
    );

    return lista;
}


/* =========================================================
   COPIAR
   ========================================================= */

async function copiarAnuncio(
    item
) {

    try {

        const response =
            await fetch(
                "/api/gerar-anuncio",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            item
                        )
                }
            );

        const data =
            await response.json();

        await navigator.clipboard.writeText(
            data.texto
        );

        setStatus(
            "📋 Anúncio copiado!",
            100
        );

    } catch (error) {

        alert(
            "Não foi possível copiar."
        );
    }
}


/* =========================================================
   SALVAR
   ========================================================= */

async function salvarOferta(
    item
) {

    try {

        const response =
            await fetch(
                "/api/salvar",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            item
                        )
                }
            );

        const data =
            await response.json();

        if (data.ok) {

            setStatus(
                "💾 Oferta salva!",
                100
            );
        }

    } catch (error) {

        alert(
            "Erro ao salvar oferta."
        );
    }
}


/* =========================================================
   INICIO
   ========================================================= */

renderCategories();

verificarMercadoLivre();

</script>

</body>

</html>
'''


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    return render_template_string(
        HTML,
        categories=list(
            CATALOG.keys()
        )
    )


# ============================================================
# ERROS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "ok": False,
        "erro": "Rota não encontrada."
    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({
        "ok": False,
        "erro": "Erro interno do servidor."
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