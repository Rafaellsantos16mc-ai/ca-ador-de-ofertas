import os
import re
import time
import uuid
import sqlite3
import secrets
import hashlib
import base64
from urllib.parse import urlencode

import requests
from flask import (
    Flask,
    request,
    redirect,
    session,
    jsonify,
    render_template_string,
    send_file,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "troque-esta-chave-no-railway"
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

REQUEST_TIMEOUT = 12

# Limites para impedir que CAÇAR TODAS fique pesado
MAX_QUERIES_PER_CATEGORY = 2
MAX_DISCOVERED_CATEGORIES = 2
MAX_PRODUCTS_PER_QUERY = 4
MAX_ITEMS_PER_PRODUCT = 8

# Tempo máximo aproximado de cada categoria
CATEGORY_TIME_LIMIT = 28


# ============================================================
# CATEGORIAS DO PROJETO
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
        "acessórios automotivos",
        "som automotivo",
        "tapete carro",
        "câmera de ré",
        "produto automotivo"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "liquidificador",
        "panela",
        "utensílios cozinha",
        "eletrodoméstico cozinha"
    ],

    "👕 Moda": [
        "tênis masculino",
        "tênis feminino",
        "camiseta masculina",
        "vestido feminino",
        "roupas"
    ],
}


# ============================================================
# CONSULTAS USADAS NO CAÇAR TODAS
# ============================================================
# Importante:
# Não usamos todos os termos do CATALOG.
# Apenas 2 por categoria para evitar centenas de chamadas.

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
        "acessórios automotivos"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "liquidificador"
    ],

    "👕 Moda": [
        "tênis masculino",
        "camiseta masculina"
    ],
}


# ============================================================
# PERFIS DE RELEVÂNCIA
# ============================================================

PROFILES = {

    "celular": {
        "positive": [
            "smartphone",
            "celular",
            "iphone",
            "galaxy",
            "samsung",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "realme",
            "infinix",
            "tecno",
            "oneplus",
            "pixel"
        ],
        "negative": [
            "capa",
            "capinha",
            "película",
            "pelicula",
            "suporte",
            "ventosa",
            "carregador",
            "cabo",
            "adaptador",
            "case",
            "película 3d",
            "pelicula 3d"
        ]
    },

    "fone": {
        "positive": [
            "fone",
            "headphone",
            "headset",
            "earbuds",
            "bluetooth",
            "airpods"
        ],
        "negative": [
            "capa",
            "case",
            "suporte",
            "almofada",
            "cabo"
        ]
    },

    "notebook": {
        "positive": [
            "notebook",
            "laptop",
            "macbook",
            "chromebook"
        ],
        "negative": [
            "capa",
            "suporte",
            "mochila",
            "película",
            "mouse",
            "teclado"
        ]
    },

    "tv": {
        "positive": [
            "tv",
            "televisão",
            "televisao",
            "smart tv",
            "televisor"
        ],
        "negative": [
            "suporte tv",
            "cabo hdmi",
            "controle",
            "antena"
        ]
    },

    "perfume": {
        "positive": [
            "perfume",
            "eau de parfum",
            "eau de toilette",
            "colônia",
            "colonia",
            "fragrance"
        ],
        "negative": [
            "frasco vazio",
            "decant",
            "amostra",
            "sachê",
            "sache"
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
            "proteína",
            "proteina",
            "creatina",
            "suplemento",
            "bcaa",
            "pré treino",
            "pre treino"
        ],
        "negative": [
            "halter",
            "halteres",
            "anilha",
            "barra",
            "banco",
            "estação",
            "esteira",
            "bicicleta ergométrica",
            "bicicleta ergometrica",
            "aparelho musculação",
            "aparelho musculacao"
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
            "relógio inteligente",
            "relogio inteligente",
            "caixa de som",
            "mouse",
            "teclado",
            "eletrônico",
            "eletronico"
        ],
        "negative": []
    },

    "casa": {
        "positive": [
            "casa",
            "aspirador",
            "liquidificador",
            "ventilador",
            "organizador",
            "utilidades domésticas",
            "utilidades domesticas"
        ],
        "negative": []
    },

    "automotivo": {
        "positive": [
            "automotivo",
            "carro",
            "veicular",
            "automóvel",
            "automovel",
            "som automotivo",
            "câmera de ré",
            "camera de re",
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
            "eletrodoméstico",
            "eletrodomestico"
        ],
        "negative": []
    },

    "moda": {
        "positive": [
            "tênis",
            "tenis",
            "camiseta",
            "camisa",
            "calça",
            "calca",
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
# HTTP SESSION
# ============================================================

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": "CacadorDeOfertas/1.0"
})


# ============================================================
# HELPERS
# ============================================================

def agora():
    return time.time()


def money(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def normalizar(texto):
    if texto is None:
        return ""

    texto = str(texto).lower()

    substituicoes = {
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
        "ç": "c",
    }

    for a, b in substituicoes.items():
        texto = texto.replace(a, b)

    return texto


def detectar_perfil_busca(query):
    q = normalizar(query)

    if any(x in q for x in [
        "celular",
        "smartphone",
        "iphone",
        "galaxy",
        "motorola",
        "xiaomi",
        "redmi"
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
        "televisão",
        "tv"
    ]):
        return "tv"

    if "perfume" in q or "colonia" in q or "colônia" in q:
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
        "fone",
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
        "casa"
    ]):
        return "casa"

    if any(x in q for x in [
        "automotivo",
        "carro",
        "som automotivo",
        "camera de re"
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
        "tênis",
        "camiseta",
        "vestido",
        "roupa"
    ]):
        return "moda"

    return None


def calcular_relevancia(titulo, query=None, perfil=None):
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
        termo_n = normalizar(termo)

        if termo_n in texto:
            score += 25

    for termo in dados["negative"]:
        termo_n = normalizar(termo)

        if termo_n in texto:
            score -= 60

    if query:
        q = normalizar(query)

        if q in texto:
            score += 30

    return score


def produto_relevante(titulo, query=None, perfil=None):
    score = calcular_relevancia(
        titulo,
        query=query,
        perfil=perfil
    )

    if perfil in PROFILES:
        return score > 0

    return True


# ============================================================
# TOKEN
# ============================================================

def get_token():
    return session.get("ml_access_token")


def api_headers():
    token = get_token()

    headers = {
        "Accept": "application/json"
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def ml_get(path, params=None, timeout=REQUEST_TIMEOUT):
    try:
        url = ML_API + path

        response = HTTP.get(
            url,
            headers=api_headers(),
            params=params or {},
            timeout=timeout
        )

        return response

    except requests.RequestException:
        return None


# ============================================================
# OAUTH PKCE
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


@app.route("/mercadolivre/login")
def ml_login():

    if not CLIENT_ID:
        return jsonify({
            "erro": "ML_CLIENT_ID não configurado"
        }), 500

    verifier, challenge = gerar_pkce()

    state = secrets.token_urlsafe(32)

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


@app.route("/mercadolivre/callback")
def ml_callback():

    error = request.args.get("error")

    if error:
        return jsonify({
            "erro": error,
            "descricao": request.args.get("error_description")
        }), 400

    state = request.args.get("state")
    code = request.args.get("code")

    if not code:
        return jsonify({
            "erro": "Código de autorização não recebido"
        }), 400

    if state != session.get("oauth_state"):
        return jsonify({
            "erro": "State OAuth inválido"
        }), 400

    verifier = session.get("pkce_verifier")

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
            "erro": "Falha ao obter token",
            "detalhes": str(e)
        }), 500

    if response.status_code >= 400:
        return jsonify({
            "erro": "Mercado Livre recusou o token",
            "status": response.status_code,
            "resposta": result
        }), response.status_code

    session["ml_access_token"] = result.get("access_token")
    session["ml_refresh_token"] = result.get("refresh_token")
    session["ml_user_id"] = result.get("user_id")

    session.pop("oauth_state", None)
    session.pop("pkce_verifier", None)

    return redirect("/")


@app.route("/mercadolivre/logout")
def ml_logout():

    session.pop("ml_access_token", None)
    session.pop("ml_refresh_token", None)
    session.pop("ml_user_id", None)

    return redirect("/")


# ============================================================
# MEU USUÁRIO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    response = ml_get("/users/me")

    if response is None:
        return jsonify({
            "erro": "Não foi possível conectar à API"
        }), 500

    try:
        data = response.json()
    except Exception:
        data = response.text

    return jsonify({
        "status": response.status_code,
        "usuario": data
    })


# ============================================================
# BUSCA DE CATEGORIA
# ============================================================

def discover_categories(query, limite=MAX_DISCOVERED_CATEGORIES):

    response = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        params={
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

    categorias = []

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


# ============================================================
# HIGHLIGHTS
# ============================================================

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

    resultados = data.get("content", [])

    if not isinstance(resultados, list):
        return []

    produtos = []

    for item in resultados:

        if not isinstance(item, dict):
            continue

        item_type = item.get("type")

        if item_type == "ITEM":
            item_id = item.get("id")

            if item_id:
                produtos.append({
                    "type": "ITEM",
                    "id": item_id
                })

        elif item_type == "PRODUCT":
            product_id = item.get("id")

            if product_id:
                produtos.append({
                    "type": "PRODUCT",
                    "id": product_id
                })

        elif item_type == "USER_PRODUCT":
            product_id = item.get("id")

            if product_id:
                produtos.append({
                    "type": "USER_PRODUCT",
                    "id": product_id
                })

    return produtos


# ============================================================
# PRODUTO
# ============================================================

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


# ============================================================
# ITENS DO PRODUTO
# ============================================================

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

    results = data.get("results", [])

    if not isinstance(results, list):
        return []

    return results[:MAX_ITEMS_PER_PRODUCT]


# ============================================================
# ITEM / USER PRODUCT
# ============================================================

def obter_titulo_item(item):

    return (
        item.get("title")
        or item.get("name")
        or item.get("product_name")
        or ""
    )


def obter_link_item(item):

    return (
        item.get("permalink")
        or item.get("link")
        or ""
    )


def montar_oferta(
    item,
    product=None,
    query=None,
    categoria=None
):

    if not isinstance(item, dict):
        return None

    titulo = obter_titulo_item(item)

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
        buy_box = product.get("buy_box_winner")

        if isinstance(buy_box, dict):
            preco = money(
                buy_box.get("price")
            )

    if preco <= 0:
        return None

    original = money(
        item.get("original_price")
    )

    if original <= 0 and product:
        original = money(
            product.get("original_price")
        )

    desconto = 0

    if original > preco:
        desconto = round(
            ((original - preco) / original) * 100,
            2
        )

    shipping = item.get("shipping") or {}

    if not isinstance(shipping, dict):
        shipping = {}

    frete = money(
        shipping.get("cost")
    )

    frete_gratis = bool(
        shipping.get("free_shipping")
    )

    if frete_gratis:
        frete = 0

    total = round(
        preco + frete,
        2
    )

    seller_id = (
        item.get("seller_id")
        or item.get("seller")
        or ""
    )

    user_product_id = (
        item.get("user_product_id")
        or ""
    )

    item_id = (
        item.get("item_id")
        or item.get("id")
        or ""
    )

    product_id = ""

    if product:
        product_id = product.get("id") or ""

    if not item_id:
        item_id = user_product_id

    link = obter_link_item(item)

    if not link and item_id:
        link = f"https://www.mercadolivre.com.br/p/{item_id}"

    perfil = detectar_perfil_busca(
        query or ""
    )

    relevancia = calcular_relevancia(
        titulo,
        query=query,
        perfil=perfil
    )

    if perfil and not produto_relevante(
        titulo,
        query=query,
        perfil=perfil
    ):
        return None

    return {
        "titulo": titulo,
        "preco": preco,
        "preco_original": original,
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
        "condicao": item.get("condition") or "new"
    }


# ============================================================
# CUPONS PÚBLICOS
# ============================================================

def buscar_cupons():

    url = "https://www.mercadolivre.com.br/l/promocoes"

    try:
        response = HTTP.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 "
                    "Version/18.0 Mobile/15E148 Safari/604.1"
                )
            },
            timeout=10
        )

        if response.status_code != 200:
            return []

        html = response.text

    except Exception:
        return []

    cupons = []

    # Procura códigos aparentes
    padrao = re.compile(
        r'(?i)'
        r'(?:c[oó]digo|cupom|usar cupom)'
        r'.{0,120}?'
        r'([A-Z0-9]{5,20})'
    )

    encontrados = padrao.findall(html)

    vistos = set()

    for codigo in encontrados:

        codigo = codigo.upper().strip()

        if codigo in vistos:
            continue

        if codigo in {
            "MERCADOLIVRE",
            "MERCADOPAGO",
            "PROMOCOES",
            "PROMOÇÕES",
            "OFERTAS"
        }:
            continue

        vistos.add(codigo)

        cupons.append({
            "codigo": codigo,
            "estimativa": True
        })

        if len(cupons) >= 20:
            break

    return cupons


# ============================================================
# BUSCA DE UM TERMO
# ============================================================

def scan_query(
    query,
    categoria=None,
    limite_tempo=CATEGORY_TIME_LIMIT
):

    inicio = agora()

    perfil = detectar_perfil_busca(query)

    encontrados = []

    categorias = discover_categories(
        query,
        limite=MAX_DISCOVERED_CATEGORIES
    )

    if not categorias:
        return []

    produtos_processados = 0

    vistos_produtos = set()

    for cat in categorias:

        if agora() - inicio > limite_tempo:
            break

        highlights = get_highlights(
            cat["id"]
        )

        if not highlights:
            continue

        for ref in highlights:

            if agora() - inicio > limite_tempo:
                break

            product_id = ref.get("id")

            if not product_id:
                continue

            if product_id in vistos_produtos:
                continue

            vistos_produtos.add(product_id)

            # Limite pequeno por consulta
            if produtos_processados >= MAX_PRODUCTS_PER_QUERY:
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
                    encontrados.append(
                        oferta
                    )

    return encontrados


# ============================================================
# AGRUPAMENTO / DEDUPLICAÇÃO
# ============================================================

def chave_oferta(oferta):

    titulo = normalizar(
        oferta.get("titulo", "")
    )

    seller = str(
        oferta.get("seller_id", "")
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

        atual = melhores.get(chave)

        if atual is None:
            melhores[chave] = oferta
            continue

        if oferta["total"] < atual["total"]:
            melhores[chave] = oferta

    resultado = list(
        melhores.values()
    )

    resultado.sort(
        key=lambda x: (
            -x.get("relevancia", 0),
            x.get("total", 999999)
        )
    )

    return resultado


# ============================================================
# AGRUPAR PRODUTOS POR MODELO
# ============================================================

def chave_modelo(titulo):

    texto = normalizar(titulo)

    texto = re.sub(
        r'\b(128gb|256gb|512gb|1tb|2tb|64gb|32gb)\b',
        '',
        texto
    )

    texto = re.sub(
        r'\b(preto|branco|azul|verde|rosa|vermelho|cinza|dourado)\b',
        '',
        texto
    )

    texto = re.sub(
        r'\s+',
        ' ',
        texto
    ).strip()

    return texto[:120]


def marcar_menores_precos(ofertas):

    grupos = {}

    for oferta in ofertas:

        chave = chave_modelo(
            oferta["titulo"]
        )

        grupos.setdefault(
            chave,
            []
        ).append(oferta)

    for lista in grupos.values():

        lista.sort(
            key=lambda x: x["total"]
        )

        for i, oferta in enumerate(lista):
            oferta["menor_preco"] = (
                i == 0
            )

    return ofertas


# ============================================================
# BUSCA MANUAL
# ============================================================

def buscar_manual(
    query,
    limite_queries=3
):

    query = (query or "").strip()

    if not query:
        return []

    resultados = scan_query(
        query=query,
        categoria="Busca"
    )

    resultados = deduplicar_ofertas(
        resultados
    )

    resultados = marcar_menores_precos(
        resultados
    )

    return resultados[:50]


# ============================================================
# CAÇAR UMA CATEGORIA
# ============================================================

def cacar_categoria(nome_categoria):

    consultas = SCAN_QUERIES.get(
        nome_categoria,
        CATALOG.get(nome_categoria, [])
    )

    consultas = consultas[
        :MAX_QUERIES_PER_CATEGORY
    ]

    todos = []

    inicio = agora()

    for query in consultas:

        if agora() - inicio > CATEGORY_TIME_LIMIT:
            break

        try:

            resultados = scan_query(
                query=query,
                categoria=nome_categoria,
                limite_tempo=(
                    CATEGORY_TIME_LIMIT -
                    (agora() - inicio)
                )
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

    # Prioriza relevância e depois preço
    todos.sort(
        key=lambda x: (
            -x.get("relevancia", 0),
            x.get("total", 999999)
        )
    )

    return todos[:30]


# ============================================================
# API BUSCA
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
            "erro": "Digite alguma coisa para buscar."
        }), 400

    inicio = agora()

    resultados = buscar_manual(
        query
    )

    return jsonify({
        "ok": True,
        "query": query,
        "perfil": detectar_perfil_busca(query),
        "tempo": round(
            agora() - inicio,
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

    if not categoria:
        return jsonify({
            "ok": False,
            "erro": "Categoria não informada."
        }), 400

    if categoria not in CATALOG:
        return jsonify({
            "ok": False,
            "erro": "Categoria inválida."
        }), 400

    inicio = agora()

    try:

        resultados = cacar_categoria(
            categoria
        )

        return jsonify({
            "ok": True,
            "categoria": categoria,
            "tempo": round(
                agora() - inicio,
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
# LISTA DE CATEGORIAS
# ============================================================

@app.route("/api/categorias")
def api_categorias():

    return jsonify({
        "ok": True,
        "categorias": list(
            CATALOG.keys()
        )
    })


# ============================================================
# CUPONS
# ============================================================

@app.route("/api/cupons")
def api_cupons():

    return jsonify({
        "ok": True,
        "cupons": buscar_cupons()
    })


@app.route("/cupons")
def cupons_page():

    return jsonify({
        "ok": True,
        "cupons": buscar_cupons()
    })


# ============================================================
# GERAR ANÚNCIO
# ============================================================

@app.route("/api/gerar-anuncio", methods=["POST"])
def gerar_anuncio():

    data = request.get_json(
        silent=True
    ) or {}

    titulo = (
        data.get("titulo")
        or "Oferta"
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

    link = (
        data.get("link")
        or ""
    )

    frete_gratis = bool(
        data.get("frete_gratis")
    )

    texto = []

    texto.append(
        f"🔥 OFERTA ENCONTRADA!"
    )

    texto.append("")

    texto.append(
        f"🛒 {titulo}"
    )

    texto.append("")

    if original > preco and desconto > 0:

        texto.append(
            f"💸 De R$ {original:.2f} por "
            f"R$ {preco:.2f}"
        )

        texto.append(
            f"🏷️ {desconto:.0f}% OFF"
        )

    else:

        texto.append(
            f"💰 Por apenas R$ {preco:.2f}"
        )

    if frete_gratis:
        texto.append(
            "🚚 Frete grátis"
        )

    texto.append("")

    texto.append(
        "👉 Confira a oferta:"
    )

    texto.append(
        link
    )

    texto.append("")

    texto.append(
        "⚠️ Preço e disponibilidade "
        "podem mudar a qualquer momento."
    )

    return jsonify({
        "ok": True,
        "texto": "\n".join(texto)
    })


# ============================================================
# SALVAR OFERTA
# ============================================================

@app.route("/api/salvar", methods=["POST"])
def salvar():

    data = request.get_json(
        silent=True
    ) or {}

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.execute("""
        INSERT INTO salvos
        (
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
        str(data.get("seller_id") or ""),
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
            "erro": "Informe ?category=MLB..."
        }), 400

    return jsonify(
        get_highlights(category)
    )


@app.route("/mercadolivre/teste-produto")
def teste_produto():

    product_id = request.args.get(
        "id",
        ""
    )

    if not product_id:
        return jsonify({
            "erro": "Informe ?id=MLB..."
        }), 400

    data = get_product(
        product_id
    )

    if data is None:
        return jsonify({
            "erro": "Produto não encontrado"
        }), 404

    return jsonify(data)


@app.route("/mercadolivre/teste-produto-itens")
def teste_produto_itens():

    product_id = request.args.get(
        "id",
        ""
    )

    if not product_id:
        return jsonify({
            "erro": "Informe ?id=MLB..."
        }), 400

    itens = get_product_items(
        product_id
    )

    return jsonify({
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

    inicio = agora()

    resultados = buscar_manual(
        query
    )

    return jsonify({
        "ok": True,
        "query": query,
        "tempo": round(
            agora() - inicio,
            2
        ),
        "total": len(resultados),
        "resultados": resultados
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
# PÁGINA PRINCIPAL
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

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, Helvetica, sans-serif;
    background: #0f1115;
    color: #fff;
}

.container {
    max-width: 1100px;
    margin: auto;
    padding: 18px;
}

.header {
    background: linear-gradient(
        135deg,
        #151922,
        #202633
    );

    border-radius: 18px;
    padding: 22px;
    margin-bottom: 16px;
}

.header h1 {
    margin: 0 0 7px;
    font-size: 28px;
}

.header p {
    margin: 0;
    color: #aeb6c5;
}

.top-buttons {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 18px;
}

.btn {
    border: 0;
    border-radius: 12px;
    padding: 13px 18px;
    font-size: 15px;
    font-weight: bold;
    cursor: pointer;
}

.btn-primary {
    background: #00a650;
    color: white;
}

.btn-primary:hover {
    background: #008f45;
}

.btn-secondary {
    background: #252b36;
    color: white;
}

.search-box {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
}

.search-box input {
    flex: 1;
    min-width: 0;
    background: #181c24;
    color: white;
    border: 1px solid #303746;
    border-radius: 12px;
    padding: 14px;
    font-size: 16px;
}

.categories {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(145px, 1fr));

    gap: 10px;
    margin-bottom: 18px;
}

.category {
    background: #181c24;
    border: 1px solid #303746;
    color: white;
    border-radius: 14px;
    padding: 15px 10px;
    cursor: pointer;
    font-weight: bold;
    min-height: 62px;
}

.category.active {
    border-color: #00a650;
    background: #123524;
}

.status {
    background: #181c24;
    border-radius: 14px;
    padding: 15px;
    margin-bottom: 16px;
    color: #cdd4df;
}

.progress {
    height: 8px;
    background: #303642;
    border-radius: 20px;
    overflow: hidden;
    margin-top: 10px;
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
        repeat(auto-fit, minmax(130px, 1fr));

    gap: 10px;
    margin-bottom: 18px;
}

.stat {
    background: #181c24;
    border-radius: 14px;
    padding: 16px;
}

.stat strong {
    display: block;
    font-size: 24px;
    margin-bottom: 4px;
}

.stat span {
    color: #9099a8;
    font-size: 13px;
}

.results {
    display: grid;
    gap: 13px;
}

.card {
    background: #181c24;
    border: 1px solid #2c3340;
    border-radius: 16px;
    padding: 17px;
}

.card.best {
    border-color: #00a650;
}

.badges {
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
    margin-bottom: 10px;
}

.badge {
    display: inline-block;
    padding: 5px 9px;
    border-radius: 20px;
    background: #2a303b;
    font-size: 12px;
}

.badge.green {
    background: #123c28;
    color: #59e39a;
}

.badge.yellow {
    background: #493e10;
    color: #ffe16b;
}

.card h3 {
    margin: 8px 0 12px;
    font-size: 17px;
    line-height: 1.35;
}

.price {
    font-size: 27px;
    font-weight: bold;
    color: #4be38d;
}

.old-price {
    color: #7d8695;
    text-decoration: line-through;
    margin-left: 8px;
}

.info {
    color: #aeb6c5;
    font-size: 13px;
    margin-top: 9px;
    line-height: 1.6;
}

.actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 14px;
}

.actions a,
.actions button {
    text-decoration: none;
    border: 0;
    border-radius: 10px;
    padding: 10px 13px;
    cursor: pointer;
    font-weight: bold;
}

.open {
    background: #00a650;
    color: white;
}

.copy {
    background: #303744;
    color: white;
}

.empty {
    text-align: center;
    padding: 35px 15px;
    color: #8f98a7;
}

.loading {
    color: #ffe16b;
}

@media(max-width: 600px) {

    .container {
        padding: 10px;
    }

    .header h1 {
        font-size: 23px;
    }

    .search-box {
        flex-direction: column;
    }

    .search-box .btn {
        width: 100%;
    }

}

</style>

</head>

<body>

<div class="container">

    <div class="header">

        <h1>🔥 Caçador de Ofertas</h1>

        <p>
            Encontre produtos, compare vendedores
            e descubra oportunidades.
        </p>

        <div class="top-buttons">

            <button
                class="btn btn-primary"
                onclick="cacarTodas()"
            >
                🚀 CAÇAR TODAS
            </button>

            <a
                class="btn btn-secondary"
                href="/mercadolivre/login"
                style="text-decoration:none"
            >
                🔗 Conectar Mercado Livre
            </a>

            <a
                class="btn btn-secondary"
                href="/api/salvos"
                style="text-decoration:none"
            >
                💾 Salvos
            </a>

        </div>

    </div>


    <div class="search-box">

        <input
            id="search"
            placeholder="Digite: celular, perfume, fone..."
            onkeydown="if(event.key==='Enter') buscar()"
        >

        <button
            class="btn btn-primary"
            onclick="buscar()"
        >
            🔎 BUSCAR
        </button>

    </div>


    <div class="categories" id="categories"></div>


    <div class="status">

        <div id="statusText">
            Pronto para caçar ofertas.
        </div>

        <div class="progress">
            <div
                class="progress-bar"
                id="progressBar"
            ></div>
        </div>

    </div>


    <div class="stats">

        <div class="stat">
            <strong id="statProducts">0</strong>
            <span>Ofertas</span>
        </div>

        <div class="stat">
            <strong id="statBest">R$ 0,00</strong>
            <span>Menor preço</span>
        </div>

        <div class="stat">
            <strong id="statDiscount">0%</strong>
            <span>Maior desconto</span>
        </div>

        <div class="stat">
            <strong id="statCategories">0</strong>
            <span>Categorias</span>
        </div>

    </div>


    <div
        class="results"
        id="results"
    >

        <div class="empty">
            Clique em uma categoria ou em
            <b>CAÇAR TODAS</b>.
        </div>

    </div>

</div>


<script>

const CATEGORIES = {{ categories | tojson }};

let resultadosGlobais = [];

let categoriaSelecionada = "";


function money(valor) {

    return Number(valor || 0).toLocaleString(
        "pt-BR",
        {
            style: "currency",
            currency: "BRL"
        }
    );
}


function escapeHtml(text) {

    return String(text || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function renderCategorias() {

    const box =
        document.getElementById("categories");

    box.innerHTML = "";

    CATEGORIES.forEach(cat => {

        const button =
            document.createElement("button");

        button.className = "category";

        button.innerText = cat;

        button.onclick = () => {

            categoriaSelecionada = cat;

            document
                .querySelectorAll(".category")
                .forEach(x =>
                    x.classList.remove("active")
                );

            button.classList.add("active");

            cacarCategoria(cat);
        };

        box.appendChild(button);

    });
}


function atualizarStats(lista) {

    document.getElementById(
        "statProducts"
    ).innerText = lista.length;

    if (!lista.length) {

        document.getElementById(
            "statBest"
        ).innerText = "R$ 0,00";

        document.getElementById(
            "statDiscount"
        ).innerText = "0%";

        return;
    }

    const menor = Math.min(
        ...lista.map(
            x => Number(x.total || x.preco || 0)
        )
    );

    const maiorDesconto = Math.max(
        ...lista.map(
            x => Number(x.desconto || 0)
        )
    );

    const categorias = new Set(
        lista.map(x => x.categoria)
    );

    document.getElementById(
        "statBest"
    ).innerText = money(menor);

    document.getElementById(
        "statDiscount"
    ).innerText =
        maiorDesconto.toFixed(0) + "%";

    document.getElementById(
        "statCategories"
    ).innerText =
        categorias.size;
}


function renderResultados(lista) {

    const box =
        document.getElementById("results");

    if (!lista.length) {

        box.innerHTML = `
            <div class="empty">
                Nenhuma oferta relevante encontrada.
            </div>
        `;

        atualizarStats([]);

        return;
    }

    box.innerHTML = "";

    lista.forEach((item, index) => {

        const card =
            document.createElement("div");

        card.className =
            "card " +
            (item.menor_preco ? "best" : "");

        let badges = "";

        if (item.menor_preco) {

            badges += `
                <span class="badge green">
                    💰 MENOR PREÇO
                </span>
            `;
        }

        if (item.desconto > 0) {

            badges += `
                <span class="badge yellow">
                    🏷️ ${item.desconto}% OFF
                </span>
            `;
        }

        if (item.frete_gratis) {

            badges += `
                <span class="badge green">
                    🚚 FRETE GRÁTIS
                </span>
            `;
        }

        if (item.categoria) {

            badges += `
                <span class="badge">
                    ${escapeHtml(item.categoria)}
                </span>
            `;
        }

        card.innerHTML = `

            <div class="badges">
                ${badges}
            </div>

            <h3>
                ${escapeHtml(item.titulo)}
            </h3>

            <div>

                <span class="price">
                    ${money(item.preco)}
                </span>

                ${
                    item.preco_original > item.preco
                    ?
                    `<span class="old-price">
                        ${money(item.preco_original)}
                    </span>`
                    :
                    ""
                }

            </div>

            <div class="info">

                💰 Total:
                <b>${money(item.total)}</b>

                <br>

                🚚 Frete:
                ${
                    item.frete_gratis
                    ?
                    "Grátis"
                    :
                    money(item.frete)
                }

                <br>

                👤 Vendedor:
                ${escapeHtml(item.seller_id || "-")}

                <br>

                ⭐ Relevância:
                ${item.relevancia ?? "-"}

            </div>

            <div class="actions">

                ${
                    item.link
                    ?
                    `
                    <a
                        class="open"
                        href="${escapeHtml(item.link)}"
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
                    onclick='copiarAnuncio(${JSON.stringify(item)})'
                >
                    📋 COPIAR ANÚNCIO
                </button>

                <button
                    class="copy"
                    onclick='salvarOferta(${JSON.stringify(item)})'
                >
                    💾 SALVAR
                </button>

            </div>
        `;

        box.appendChild(card);

    });

    atualizarStats(lista);
}


async function buscar() {

    const query =
        document
            .getElementById("search")
            .value
            .trim();

    if (!query) {
        return;
    }

    setStatus(
        `🔎 Buscando "${query}"...`,
        15
    );

    try {

        const response =
            await fetch(
                "/api/buscar?q=" +
                encodeURIComponent(query)
            );

        const data =
            await response.json();

        if (!data.ok) {

            setStatus(
                "❌ " +
                (data.erro || "Erro na busca."),
                0
            );

            return;
        }

        resultadosGlobais =
            data.resultados || [];

        renderResultados(
            resultadosGlobais
        );

        setStatus(
            `✅ ${resultadosGlobais.length} ofertas encontradas.`,
            100
        );

    } catch (error) {

        setStatus(
            "❌ Erro de conexão com o servidor.",
            0
        );
    }
}


async function cacarCategoria(categoria) {

    setStatus(
        `🔎 Caçando ${categoria}...`,
        10
    );

    try {

        const response =
            await fetch(
                "/api/cacar?categoria=" +
                encodeURIComponent(categoria)
            );

        const data =
            await response.json();

        if (!data.ok) {

            setStatus(
                "❌ " +
                (data.erro || "Erro."),
                0
            );

            return [];
        }

        return data.resultados || [];

    } catch (error) {

        console.error(error);

        return [];

    }

}


async function cacarTodas() {

    const button =
        document.querySelector(
            ".btn-primary"
        );

    button.disabled = true;

    resultadosGlobais = [];

    renderResultados([]);

    const totalCategorias =
        CATEGORIES.length;

    for (
        let i = 0;
        i < totalCategorias;
        i++
    ) {

        const categoria =
            CATEGORIES[i];

        const progresso =
            Math.round(
                (i / totalCategorias) * 100
            );

        setStatus(
            `🚀 Caçando ${i + 1}/${totalCategorias}: ${categoria}`,
            progresso
        );

        const resultados =
            await cacarCategoria(
                categoria
            );

        resultadosGlobais =
            resultadosGlobais.concat(
                resultados
            );

        resultadosGlobais =
            dedupeClient(
                resultadosGlobais
            );

        resultadosGlobais =
            marcarMenoresClient(
                resultadosGlobais
            );

        renderResultados(
            resultadosGlobais
        );

        setStatus(
            `✅ ${categoria} concluída — ${resultados.length} ofertas encontradas.`,
            Math.round(
                ((i + 1) / totalCategorias) * 100
            )
        );

        // Pequena pausa para não bombardear a API
        await sleep(250);
    }

    resultadosGlobais.sort(
        (a, b) => {

            const relA =
                Number(a.relevancia || 0);

            const relB =
                Number(b.relevancia || 0);

            if (relA !== relB) {
                return relB - relA;
            }

            return Number(a.total || 999999)
                -
                Number(b.total || 999999);
        }
    );

    renderResultados(
        resultadosGlobais
    );

    setStatus(
        `🏆 CAÇA FINALIZADA — ${resultadosGlobais.length} ofertas encontradas.`,
        100
    );

    button.disabled = false;
}


function dedupeClient(lista) {

    const map = new Map();

    lista.forEach(item => {

        const key =
            [
                item.titulo,
                item.seller_id
            ]
            .join("|")
            .toLowerCase();

        const anterior =
            map.get(key);

        if (
            !anterior ||
            Number(item.total || 999999)
            <
            Number(anterior.total || 999999)
        ) {

            map.set(
                key,
                item
            );
        }

    });

    return Array.from(
        map.values()
    );
}


function marcarMenoresClient(lista) {

    const grupos = {};

    lista.forEach(item => {

        const chave =
            normalizarTitulo(
                item.titulo
            );

        if (!grupos[chave]) {
            grupos[chave] = [];
        }

        grupos[chave].push(item);

    });

   