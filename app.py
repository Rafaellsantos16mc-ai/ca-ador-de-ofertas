import os
import sqlite3
import secrets
import hashlib
import base64
import time
import re
import html as html_lib
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    redirect,
    session,
    jsonify,
    render_template_string
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "chave-cacador-ofertas"
)


# ============================================================
# MERCADO LIVRE
# ============================================================

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

ML_API = "https://api.mercadolibre.com"

ML_AUTH = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN = (
    "https://api.mercadolibre.com/oauth/token"
)

SITE_ID = "MLB"

DB_FILE = "ofertas.db"

COUPONS_URL = (
    "https://www.mercadolivre.com.br/l/promocoes"
)


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
            id INTEGER PRIMARY KEY CHECK (id = 1),
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
            min_purchase REAL,
            max_discount REAL,
            valid_until TEXT,
            source_url TEXT,
            conditions TEXT,
            active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Compatibilidade com bancos antigos
    columns = [
        ("total_price", "REAL"),
        ("relevance_score", "REAL DEFAULT 0"),
        ("affiliate_link", "TEXT"),
        ("extra_earnings", "REAL DEFAULT 0")
    ]

    for column, definition in columns:

        try:

            conn.execute(
                f"ALTER TABLE ofertas ADD COLUMN {column} {definition}"
            )

        except sqlite3.OperationalError:

            pass

    conn.commit()

    conn.close()


init_db()


# ============================================================
# UTILIDADES
# ============================================================

def json_safe(value):

    if value is None:
        return None

    if isinstance(
        value,
        (str, int, float, bool)
    ):
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


def gerar_pkce():

    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")

    return verifier, challenge


def calcular_desconto(preco, original):

    try:

        preco = float(preco)
        original = float(original)

        if original > preco > 0:

            return round(
                (
                    1 - preco / original
                ) * 100,
                2
            )

    except Exception:

        pass

    return 0


def calcular_preco_total(
    price,
    shipping_cost
):

    try:
        price = float(price)
    except Exception:
        return None

    try:
        shipping_cost = float(
            shipping_cost or 0
        )
    except Exception:
        shipping_cost = 0

    return round(
        price + shipping_cost,
        2
    )


def normalizar_texto(texto):

    if not texto:
        return ""

    texto = str(texto).lower()

    substituicoes = {
        "á": "a",
        "à": "a",
        "ã": "a",
        "â": "a",
        "ä": "a",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "í": "i",
        "ì": "i",
        "î": "i",
        "ï": "i",
        "ó": "o",
        "ò": "o",
        "õ": "o",
        "ô": "o",
        "ö": "o",
        "ú": "u",
        "ù": "u",
        "û": "u",
        "ü": "u",
        "ç": "c"
    }

    for antigo, novo in substituicoes.items():

        texto = texto.replace(
            antigo,
            novo
        )

    texto = re.sub(
        r"[^a-z0-9\s]+",
        " ",
        texto
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    )

    return texto.strip()


def formatar_brl(valor):

    try:

        valor = float(valor)

        return (
            f"R$ {valor:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return "R$ 0,00"


# ============================================================
# PERFIS DE BUSCA
# ============================================================

PERFIS_BUSCA = {

    "celular": {

        "termos": [
            "celular",
            "smartphone",
            "iphone",
            "galaxy",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "realme",
            "oppo",
            "asus",
            "zenfone",
            "samsung"
        ],

        "fortes": [
            "smartphone",
            "iphone",
            "galaxy",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "realme",
            "samsung",
            "zenfone"
        ],

        "negativos": [
            "capa",
            "capinha",
            "pelicula",
            "suporte",
            "suporte veicular",
            "ventosa",
            "carregador",
            "cabo",
            "adaptador",
            "bateria",
            "case",
            "tripe",
            "holder",
            "bolsa",
            "carteira",
            "mouse",
            "teclado"
        ]
    },

    "fone": {

        "termos": [
            "fone",
            "fone de ouvido",
            "headphone",
            "headset",
            "earphone",
            "earbud",
            "bluetooth",
            "tws"
        ],

        "fortes": [
            "fone de ouvido",
            "headphone",
            "headset",
            "earbud",
            "tws"
        ],

        "negativos": [
            "suporte para fone",
            "case para fone",
            "capa para fone",
            "cabo para fone",
            "peca"
        ]
    },

    "notebook": {

        "termos": [
            "notebook",
            "laptop",
            "computador"
        ],

        "fortes": [
            "notebook",
            "laptop"
        ],

        "negativos": [
            "capa",
            "case",
            "suporte",
            "mesa",
            "base",
            "mochila",
            "carregador",
            "fonte",
            "teclado",
            "mouse"
        ]
    },

    "tv": {

        "termos": [
            "smart tv",
            "smart",
            "televisao",
            "tv",
            "televisor"
        ],

        "fortes": [
            "smart tv",
            "televisao",
            "televisor"
        ],

        "negativos": [
            "suporte",
            "controle remoto",
            "controle",
            "cabo",
            "antena",
            "painel",
            "rack"
        ]
    },

    "geladeira": {

        "termos": [
            "geladeira",
            "refrigerador",
            "freezer"
        ],

        "fortes": [
            "geladeira",
            "refrigerador"
        ],

        "negativos": [
            "peca",
            "borracha",
            "prateleira",
            "filtro",
            "suporte"
        ]
    },

    "air fryer": {

        "termos": [
            "air fryer",
            "airfryer",
            "fritadeira"
        ],

        "fortes": [
            "air fryer",
            "airfryer",
            "fritadeira"
        ],

        "negativos": [
            "cesta",
            "forma",
            "papel",
            "forro",
            "suporte"
        ]
    },

    "smartwatch": {

        "termos": [
            "smartwatch",
            "smart watch",
            "relogio inteligente",
            "apple watch",
            "galaxy watch"
        ],

        "fortes": [
            "smartwatch",
            "smart watch",
            "apple watch",
            "galaxy watch"
        ],

        "negativos": [
            "pulseira",
            "pelicula",
            "capa",
            "case",
            "carregador",
            "suporte"
        ]
    },

    "tablet": {

        "termos": [
            "tablet",
            "ipad",
            "galaxy tab"
        ],

        "fortes": [
            "tablet",
            "ipad",
            "galaxy tab"
        ],

        "negativos": [
            "capa",
            "capinha",
            "pelicula",
            "suporte",
            "teclado",
            "carregador"
        ]
    },

    "camera": {

        "termos": [
            "camera",
            "camera digital",
            "camera fotografica",
            "gopro",
            "action cam"
        ],

        "fortes": [
            "camera",
            "gopro",
            "action cam"
        ],

        "negativos": [
            "capa",
            "case",
            "bolsa",
            "suporte",
            "tripe",
            "bateria",
            "cartao"
        ]
    }
}


def detectar_perfil_busca(query):

    texto = normalizar_texto(query)

    if any(x in texto for x in [
        "iphone",
        "galaxy",
        "samsung",
        "motorola",
        "xiaomi",
        "redmi",
        "poco",
        "realme"
    ]):

        return "celular"

    if any(x in texto for x in [
        "fone",
        "headphone",
        "headset",
        "earbud",
        "tws"
    ]):

        return "fone"

    if any(x in texto for x in [
        "notebook",
        "laptop"
    ]):

        return "notebook"

    if (
        texto == "tv"
        or "televisao" in texto
        or "smart tv" in texto
    ):

        return "tv"

    if any(x in texto for x in [
        "geladeira",
        "refrigerador"
    ]):

        return "geladeira"

    if any(x in texto for x in [
        "air fryer",
        "airfryer"
    ]):

        return "air fryer"

    if any(x in texto for x in [
        "smartwatch",
        "smart watch",
        "apple watch",
        "galaxy watch"
    ]):

        return "smartwatch"

    if any(x in texto for x in [
        "tablet",
        "ipad"
    ]):

        return "tablet"

    if any(x in texto for x in [
        "camera",
        "gopro",
        "action cam"
    ]):

        return "camera"

    if any(x in texto for x in [
        "celular",
        "smartphone"
    ]):

        return "celular"

    return None


def obter_perfil_busca(query):

    perfil = detectar_perfil_busca(query)

    if perfil:

        return (
            perfil,
            PERFIS_BUSCA[perfil]
        )

    return None, None


def calcular_relevancia(
    title,
    query
):

    texto = normalizar_texto(title)

    busca = normalizar_texto(query)

    perfil_nome, perfil = (
        obter_perfil_busca(query)
    )

    if not perfil:

        palavras = [
            p
            for p in busca.split()
            if len(p) >= 3
        ]

        return sum(
            20
            for palavra in palavras
            if palavra in texto
        )

    score = 0

    for termo in perfil["termos"]:

        termo_n = normalizar_texto(termo)

        if termo_n in texto:

            if termo_n in [
                normalizar_texto(x)
                for x in perfil["fortes"]
            ]:

                score += 40

            else:

                score += 20

    for termo in perfil["fortes"]:

        termo_n = normalizar_texto(termo)

        if termo_n in texto:

            score += 25

    for termo in perfil["negativos"]:

        termo_n = normalizar_texto(termo)

        if termo_n in texto:

            score -= 80

    for palavra in [
        p
        for p in busca.split()
        if len(p) >= 3
    ]:

        if palavra in texto:

            score += 10

    if perfil_nome == "celular":

        acessorio = any(
            termo in texto
            for termo in [
                "capa",
                "capinha",
                "pelicula",
                "suporte",
                "ventosa",
                "carregador",
                "cabo",
                "adaptador",
                "case",
                "holder",
                "tripe"
            ]
        )

        celular_real = any(
            termo in texto
            for termo in [
                "smartphone",
                "iphone",
                "galaxy",
                "samsung",
                "motorola",
                "xiaomi",
                "redmi",
                "poco",
                "realme",
                "zenfone"
            ]
        )

        if acessorio and not celular_real:

            score -= 150

        if celular_real:

            score += 100

    return score


def produto_relevante(
    title,
    query,
    minimo=20
):

    score = calcular_relevancia(
        title,
        query
    )

    return (
        score >= minimo,
        score
    )


# ============================================================
# TOKEN
# ============================================================

def obter_tokens():

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM oauth_tokens
        WHERE id = 1
    """).fetchone()

    conn.close()

    return dict(row) if row else None


def salvar_tokens(
    data,
    user=None
):

    access_token = data.get(
        "access_token"
    )

    refresh_token = data.get(
        "refresh_token"
    )

    expires_in = int(
        data.get(
            "expires_in",
            21600
        )
    )

    expires_at = (
        int(time.time())
        + expires_in
    )

    user_id = None
    nickname = None

    if user:

        if user.get("id") is not None:

            user_id = str(
                user.get("id")
            )

        nickname = user.get(
            "nickname"
        )

    conn = get_db()

    conn.execute("""
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

        ON CONFLICT(id) DO UPDATE SET

            access_token =
                excluded.access_token,

            refresh_token =
                COALESCE(
                    excluded.refresh_token,
                    oauth_tokens.refresh_token
                ),

            expires_at =
                excluded.expires_at,

            user_id =
                COALESCE(
                    excluded.user_id,
                    oauth_tokens.user_id
                ),

            nickname =
                COALESCE(
                    excluded.nickname,
                    oauth_tokens.nickname
                )
    """, (
        access_token,
        refresh_token,
        expires_at,
        user_id,
        nickname
    ))

    conn.commit()
    conn.close()


def renovar_token(refresh_token):

    if not refresh_token:
        return None

    try:

        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh_token
            },
            timeout=30
        )

        if response.status_code != 200:

            print(
                "[ERRO REFRESH]",
                response.status_code,
                response.text[:1000]
            )

            return None

        data = response.json()

        antigos = obter_tokens()

        user = None

        if antigos:

            user = {
                "id":
                    antigos.get("user_id"),

                "nickname":
                    antigos.get("nickname")
            }

        salvar_tokens(
            data,
            user
        )

        return data.get(
            "access_token"
        )

    except Exception as e:

        print(
            "[ERRO REFRESH]",
            e
        )

        return None


def get_access_token():

    tokens = obter_tokens()

    if not tokens:
        return None

    access_token = tokens.get(
        "access_token"
    )

    expires_at = (
        tokens.get("expires_at")
        or 0
    )

    if (
        access_token
        and time.time()
        < expires_at - 120
    ):

        return access_token

    refresh_token = tokens.get(
        "refresh_token"
    )

    if refresh_token:

        novo = renovar_token(
            refresh_token
        )

        if novo:
            return novo

    return access_token


# ============================================================
# API MERCADO LIVRE
# ============================================================

def ml_get(
    path,
    params=None
):

    token = get_access_token()

    if not token:

        return (
            None,
            401,
            {}
        )

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
                    "application/json"
            },
            params=params,
            timeout=30
        )

        try:

            data = response.json()

        except Exception:

            data = {
                "message":
                    response.text
            }

        return (
            data,
            response.status_code,
            dict(response.headers)
        )

    except requests.RequestException as e:

        return (
            {
                "error":
                    str(e)
            },
            500,
            {}
        )


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

    verifier, challenge = (
        gerar_pkce()
    )

    state = secrets.token_urlsafe(32)

    session["ml_state"] = state

    session["ml_code_verifier"] = (
        verifier
    )

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
            "S256"
    }

    return redirect(
        ML_AUTH
        + "?"
        + urlencode(params)
    )


# ============================================================
# CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return jsonify({

            "erro":
                error,

            "descricao":
                request.args.get(
                    "error_description"
                )

        }), 400

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    if not code:

        return jsonify({
            "erro":
                "Código não recebido."
        }), 400

    if state != session.get(
        "ml_state"
    ):

        return jsonify({
            "erro":
                "State inválido."
        }), 400

    verifier = session.get(
        "ml_code_verifier"
    )

    if not verifier:

        return jsonify({
            "erro":
                "Code verifier não encontrado."
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
                    verifier
            },
            timeout=30
        )

        if response.status_code != 200:

            return jsonify({

                "erro":
                    "Falha ao obter token.",

                "status":
                    response.status_code,

                "resposta":
                    response.text

            }), response.status_code

        token_data = response.json()

        access_token = (
            token_data.get(
                "access_token"
            )
        )

        user = None

        if access_token:

            try:

                me = requests.get(

                    f"{ML_API}/users/me",

                    headers={
                        "Authorization":
                            f"Bearer {access_token}"
                    },

                    timeout=30
                )

                if me.status_code == 200:

                    user = me.json()

            except Exception as e:

                print(
                    "[USERS ME]",
                    e
                )

        salvar_tokens(
            token_data,
            user
        )

        session.pop(
            "ml_state",
            None
        )

        session.pop(
            "ml_code_verifier",
            None
        )

        return redirect(
            "/?conectado=1"
        )

    except Exception as e:

        return jsonify({
            "erro":
                str(e)
        }), 500


# ============================================================
# LOGOUT
# ============================================================

@app.route("/mercadolivre/logout")
def mercadolivre_logout():

    conn = get_db()

    conn.execute("""
        DELETE FROM oauth_tokens
        WHERE id = 1
    """)

    conn.commit()
    conn.close()

    session.clear()

    return redirect("/")


# ============================================================
# TESTES MERCADO LIVRE
# ============================================================

@app.route("/mercadolivre/teste-produto-itens")
def teste_produto_itens():

    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    ).strip()

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    return jsonify({

        "endpoint":
            f"/products/{product_id}/items",

        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data

    }), status


@app.route("/mercadolivre/teste-produto")
def teste_produto():

    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    ).strip()

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    return jsonify({

        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data

    }), status


@app.route("/mercadolivre/teste-categoria")
def teste_categoria():

    q = request.args.get(
        "q",
        "fone"
    ).strip()

    data, status, _ = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {"q": q}
    )

    return jsonify({

        "query":
            q,

        "status_http":
            status,

        "resposta":
            data

    }), status


@app.route("/mercadolivre/teste-highlights")
def teste_highlights():

    category_id = request.args.get(
        "category_id",
        "MLB1664"
    ).strip()

    data, status, _ = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    return jsonify({

        "category_id":
            category_id,

        "status_http":
            status,

        "resposta":
            data

    }), status


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    token = get_access_token()

    resultado = {

        "configuracao": {

            "client_id_configurado":
                bool(ML_CLIENT_ID),

            "client_secret_configurado":
                bool(ML_CLIENT_SECRET),

            "redirect_uri":
                ML_REDIRECT_URI
        },

        "token": {

            "disponivel":
                bool(token)
        }
    }

    if not token:

        resultado["usuario"] = {
            "status":
                "não conectado"
        }

        return jsonify(resultado)

    me, status, _ = ml_get(
        "/users/me"
    )

    resultado["users_me"] = {

        "status_http":
            status,

        "resposta":
            me
    }

    tokens = obter_tokens()

    if tokens:

        resultado["token_local"] = {

            "user_id":
                tokens.get("user_id"),

            "nickname":
                tokens.get("nickname"),

            "tem_access_token":
                bool(
                    tokens.get(
                        "access_token"
                    )
                ),

            "tem_refresh_token":
                bool(
                    tokens.get(
                        "refresh_token"
                    )
                ),

            "expires_at":
                tokens.get(
                    "expires_at"
                )
        }

    return jsonify(resultado)


# ============================================================
# CATEGORIAS
# ============================================================

def descobrir_categorias(query):

    data, status, _ = ml_get(

        f"/sites/{SITE_ID}/domain_discovery/search",

        {
            "q":
                query
        }
    )

    if status != 200:

        return [], {
            "status":
                status,
            "resposta":
                data
        }

    categorias = []

    if not isinstance(
        data,
        list
    ):

        return categorias, {
            "status":
                status,
            "resposta":
                data
        }

    for item in data:

        category_id = (
            item.get("category_id")
            or
            item.get("id")
        )

        category_name = (
            item.get("category_name")
            or
            item.get("name")
        )

        if not category_id:
            continue

        categorias.append({

            "category_id":
                category_id,

            "category_name":
                category_name
                or category_id
        })

    return categorias, {
        "status":
            status
    }


# ============================================================
# HIGHLIGHTS
# ============================================================

def buscar_highlights(category_id):

    data, status, _ = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if status != 200:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        for chave in [
            "content",
            "results"
        ]:

            valor = data.get(chave)

            if isinstance(
                valor,
                list
            ):

                return valor

    return []


# ============================================================
# PRODUTO
# ============================================================

def obter_produto(product_id):

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    if (
        status != 200
        or not isinstance(data, dict)
    ):

        return None

    return data


# ============================================================
# PRODUTO -> ANÚNCIOS
# ============================================================

def obter_itens_do_produto(product_id):

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    if status != 200:

        print(
            "[PRODUTO-ITENS]",
            product_id,
            "HTTP",
            status
        )

        return [], status, data

    if isinstance(data, list):

        return data, status, data

    if isinstance(data, dict):

        results = data.get(
            "results"
        )

        if isinstance(
            results,
            list
        ):

            return (
                results,
                status,
                data
            )

    return [], status, data


# ============================================================
# NORMALIZAR ITEM
# ============================================================

def normalizar_item(item):

    if not isinstance(
        item,
        dict
    ):

        return None

    item_id = item.get(
        "item_id"
    )

    if not item_id:
        return None

    price = item.get(
        "price"
    )

    original_price = item.get(
        "original_price"
    )

    shipping = (
        item.get("shipping")
        or {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping",
            False
        )
    )

    shipping_cost = shipping.get(
        "cost"
    )

    try:

        if shipping_cost is not None:

            shipping_cost = float(
                shipping_cost
            )

    except Exception:

        shipping_cost = None

    if free_shipping:

        shipping_cost = 0.0

    return {

        "item_id":
            item_id,

        "seller_id":
            item.get("seller_id"),

        "price":
            price,

        "original_price":
            original_price,

        "condition":
            item.get("condition"),

        "listing_type_id":
            item.get(
                "listing_type_id"
            ),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost,

        "user_product_id":
            item.get(
                "user_product_id"
            ),

        "permalink":
            item.get(
                "permalink"
            ),

        "raw":
            item
    }


# ============================================================
# CUPONS
# ============================================================

def extrair_numero(texto):

    if texto is None:
        return None

    texto = str(texto)

    match = re.search(
        r"(\d+(?:[.,]\d+)?)",
        texto
    )

    if not match:
        return None

    try:

        return float(
            match.group(1).replace(
                ",",
                "."
            )
        )

    except Exception:

        return None


def sincronizar_cupons():

    """
    Busca a página pública oficial de promoções do Mercado Livre.

    O parser é propositalmente conservador:
    somente grava cupons que aparecem
    acompanhados de informações de desconto.

    Não considera o cupom automaticamente elegível
    para todos os produtos.
    """

    try:

        response = requests.get(
            COUPONS_URL,
            headers={
                "User-Agent":
                    "Mozilla/5.0"
            },
            timeout=30
        )

        if response.status_code != 200:

            return {
                "ok":
                    False,

                "status":
                    response.status_code,

                "erro":
                    "Não foi possível consultar a página oficial."
            }

        texto = html_lib.unescape(
            response.text
        )

        texto = re.sub(
            r"<script.*?</script>",
            " ",
            texto,
            flags=re.I | re.S
        )

        texto = re.sub(
            r"<style.*?</style>",
            " ",
            texto,
            flags=re.I | re.S
        )

        texto = re.sub(
            r"<[^>]+>",
            " ",
            texto
        )

        texto = re.sub(
            r"\s+",
            " ",
            texto
        )

        texto_lower = normalizar_texto(
            texto
        )

        # ----------------------------------------------------
        # CÓDIGOS
        # ----------------------------------------------------

        codigos = re.findall(
            r"(?:Cupom\s+)([A-Z0-9]{5,20})",
            texto,
            flags=re.I
        )

        codigos = list(dict.fromkeys([
            codigo.upper()
            for codigo in codigos
        ]))

        encontrados = 0

        conn = get_db()

        for codigo in codigos:

            pos = texto_lower.find(
                normalizar_texto(
                    codigo
                )
            )

            trecho = texto[
                max(0, pos - 100):
                min(
                    len(texto),
                    pos + 900
                )
            ] if pos >= 0 else ""

            trecho_lower = normalizar_texto(
                trecho
            )

            desconto_percentual = None
            min_purchase = None
            max_discount = None

            # Exemplo:
            # "até 15%"
            match_percent = re.search(
                r"até\s+(\d+(?:[.,]\d+)?)\s*%",
                trecho,
                flags=re.I
            )

            if match_percent:

                desconto_percentual = (
                    extrair_numero(
                        match_percent.group(1)
                    )
                )

            match_min = re.search(
                r"(?:partir de|a partir de)\s*R?\$?\s*"
                r"(\d+(?:[.,]\d+)?)",
                trecho,
                flags=re.I
            )

            if match_min:

                min_purchase = (
                    extrair_numero(
                        match_min.group(1)
                    )
                )

            match_max = re.search(
                r"(?:máximo de|maximo de)\s*R?\$?\s*"
                r"(\d+(?:[.,]\d+)?)",
                trecho,
                flags=re.I
            )

            if match_max:

                max_discount = (
                    extrair_numero(
                        match_max.group(1)
                    )
                )

            if (
                desconto_percentual is None
                and max_discount is None
            ):

                continue

            descricao = (
                trecho.strip()
            )

            conn.execute("""
                INSERT INTO cupons
                (
                    code,
                    description,
                    discount_percent,
                    min_purchase,
                    max_discount,
                    valid_until,
                    source_url,
                    conditions,
                    active,
                    updated_at
                )

                VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)

                ON CONFLICT(code) DO UPDATE SET

                    description =
                        excluded.description,

                    discount_percent =
                        excluded.discount_percent,

                    min_purchase =
                        excluded.min_purchase,

                    max_discount =
                        excluded.max_discount,

                    conditions =
                        excluded.conditions,

                    active =
                        1,

                    updated_at =
                        CURRENT_TIMESTAMP
            """, (
                codigo,
                descricao,
                desconto_percentual,
                min_purchase,
                max_discount,
                "",
                COUPONS_URL,
                descricao
            ))

            encontrados += 1

        conn.commit()
        conn.close()

        return {

            "ok":
                True,

            "status":
                200,

            "cupons_encontrados":
                encontrados,

            "fonte":
                COUPONS_URL
        }

    except Exception as e:

        print(
            "[CUPONS]",
            e
        )

        return {

            "ok":
                False,

            "erro":
                str(e)
        }


def obter_cupons():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM cupons
        WHERE active = 1
        ORDER BY
            discount_percent DESC,
            max_discount DESC
    """).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


def cupom_aplicavel(
    cupom,
    preco
):

    try:

        preco = float(preco)

    except Exception:

        return False

    minimo = cupom.get(
        "min_purchase"
    )

    if minimo is not None:

        try:

            if preco < float(minimo):

                return False

        except Exception:

            pass

    return True


def calcular_desconto_cupom(
    cupom,
    preco
):

    if not cupom_aplicavel(
        cupom,
        preco
    ):

        return 0

    try:

        preco = float(preco)

    except Exception:

        return 0

    percentual = (
        cupom.get(
            "discount_percent"
        )
    )

    max_discount = (
        cupom.get(
            "max_discount"
        )
    )

    desconto = 0

    if percentual:

        desconto = (
            preco
            * float(percentual)
            / 100
        )

    if max_discount:

        desconto = min(
            desconto,
            float(max_discount)
        )

    return round(
        max(0, desconto),
        2
    )


# ============================================================
# ESCOLHER CUPOM PARA OFERTA
# ============================================================

def encontrar_melhor_cupom(
    preco
):

    cupons = obter_cupons()

    elegiveis = []

    for cupom in cupons:

        desconto = (
            calcular_desconto_cupom(
                cupom,
                preco
            )
        )

        if desconto <= 0:
            continue

        item = dict(cupom)

        item[
            "desconto_estimado"
        ] = desconto

        elegiveis.append(
            item
        )

    if not elegiveis:
        return None

    elegiveis.sort(
        key=lambda x:
            x.get(
                "desconto_estimado",
                0
            ),
        reverse=True
    )

    return elegiveis[0]


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(oferta):

    conn = get_db()

    conn.execute("""
        INSERT INTO ofertas
        (
            product_id,
            item_id,
            title,
            permalink,
            price,
            original_price,
            discount,
            seller_id,
            image,
            category_id,
            category_name,
            condition,
            listing_type_id,
            free_shipping,
            shipping_cost,
            total_price,
            relevance_score,
            affiliate_link,
            extra_earnings
        )

        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (

        oferta.get("product_id"),
        oferta.get("item_id"),
        oferta.get("title"),
        oferta.get("permalink"),
        oferta.get("price"),
        oferta.get("original_price"),
        oferta.get("discount"),
        oferta.get("seller_id"),
        oferta.get("image"),
        oferta.get("category_id"),
        oferta.get("category_name"),
        oferta.get("condition"),
        oferta.get("listing_type_id"),
        1 if oferta.get(
            "free_shipping"
        ) else 0,
        oferta.get("shipping_cost"),
        oferta.get("total_price"),
        oferta.get("relevance_score"),
        oferta.get("affiliate_link"),
        oferta.get("extra_earnings", 0)
    ))

    conn.commit()
    conn.close()


# ============================================================
# BUSCAR OFERTAS
# ============================================================

def search_offers(
    query,
    desconto_minimo=0
):

    try:

        desconto_minimo = float(
            desconto_minimo
        )

    except Exception:

        desconto_minimo = 0

    perfil_nome, perfil = (
        obter_perfil_busca(query)
    )

    categorias, _ = (
        descobrir_categorias(query)
    )

    stats = {

        "query":
            query,

        "perfil_detectado":
            perfil_nome,

        "categorias_encontradas":
            len(categorias),

        "produtos_highlights":
            0,

        "produtos_unicos":
            0,

        "produtos_consultados":
            0,

        "anuncios_encontrados":
            0,

        "anuncios_relevantes":
            0,

        "anuncios_com_preco":
            0,

        "produtos_sem_anuncios":
            0,

        "ofertas":
            0,

        "ofertas_com_cupom":
            0,

        "erros":
            0
    }

    produtos = {}

    for categoria in categorias:

        category_id = categoria[
            "category_id"
        ]

        highlights = (
            buscar_highlights(
                category_id
            )
        )

        stats[
            "produtos_highlights"
        ] += len(
            highlights
        )

        for h in highlights:

            if h.get("type") != "PRODUCT":
                continue

            product_id = (
                h.get("id")
                or
                h.get("product_id")
            )

            if not product_id:
                continue

            if product_id not in produtos:

                produtos[
                    product_id
                ] = {

                    "product_id":
                        product_id,

                    "category_id":
                        category_id,

                    "category_name":
                        categoria[
                            "category_name"
                        ]
                }

    stats[
        "produtos_unicos"
    ] = len(produtos)

    ofertas = []

    itens_processados = set()

    for product_id, base in produtos.items():

        produto = obter_produto(
            product_id
        )

        stats[
            "produtos_consultados"
        ] += 1

        if not produto:

            stats[
                "erros"
            ] += 1

            continue

        title = (
            produto.get("name")
            or
            produto.get("title")
            or
            product_id
        )

        relevante, score = (
            produto_relevante(
                title,
                query
            )
        )

        if not relevante:
            continue

        product_permalink = (
            produto.get("permalink")
            or
            f"https://www.mercadolivre.com.br/p/{product_id}"
        )

        image = None

        pictures = (
            produto.get("pictures")
            or []
        )

        if pictures:

            primeira = pictures[0]

            if isinstance(
                primeira,
                dict
            ):

                image = (
                    primeira.get("url")
                    or
                    primeira.get(
                        "secure_url"
                    )
                )

        itens, status, _ = (
            obter_itens_do_produto(
                product_id
            )
        )

        if status != 200:

            stats[
                "erros"
            ] += 1

            continue

        if not itens:

            stats[
                "produtos_sem_anuncios"
            ] += 1

            continue

        for item_raw in itens:

            item = normalizar_item(
                item_raw
            )

            if not item:
                continue

            item_id = item[
                "item_id"
            ]

            if item_id in itens_processados:
                continue

            itens_processados.add(
                item_id
            )

            stats[
                "anuncios_encontrados"
            ] += 1

            price = item.get(
                "price"
            )

            try:

                if isinstance(
                    price,
                    dict
                ):

                    price = (
                        price.get("amount")
                        or
                        price.get("value")
                    )

                price = float(price)

            except Exception:

                continue

            original_price = item.get(
                "original_price"
            )

            if original_price is not None:

                try:

                    if isinstance(
                        original_price,
                        dict
                    ):

                        original_price = (
                            original_price.get(
                                "amount"
                            )
                            or
                            original_price.get(
                                "value"
                            )
                        )

                    original_price = float(
                        original_price
                    )

                except Exception:

                    original_price = None

            shipping_cost = item.get(
                "shipping_cost"
            )

            if item.get(
                "free_shipping"
            ):

                shipping_cost = 0.0

            if shipping_cost is None:

                shipping_known = False

                total_price = price

            else:

                shipping_known = True

                total_price = (
                    calcular_preco_total(
                        price,
                        shipping_cost
                    )
                )

            desconto = calcular_desconto(
                price,
                original_price
            )

            stats[
                "anuncios_com_preco"
            ] += 1

            if (
                desconto_minimo > 0
                and
                desconto < desconto_minimo
            ):

                continue

            stats[
                "anuncios_relevantes"
            ] += 1

            permalink = (
                item.get("permalink")
                or
                product_permalink
            )

            # ------------------------------------------------
            # CUPOM
            # ------------------------------------------------

            melhor_cupom = (
                encontrar_melhor_cupom(
                    price
                )
            )

            cupom_estimado = 0

            if melhor_cupom:

                cupom_estimado = (
                    melhor_cupom.get(
                        "desconto_estimado",
                        0
                    )
                )

                stats[
                    "ofertas_com_cupom"
                ] += 1

            oferta = {

                "product_id":
                    product_id,

                "item_id":
                    item_id,

                "title":
                    title,

                "permalink":
                    permalink,

                "price":
                    price,

                "original_price":
                    original_price,

                "discount":
                    desconto,

                "seller_id":
                    item.get(
                        "seller_id"
                    ),

                "image":
                    image,

                "category_id":
                    base[
                        "category_id"
                    ],

                "category_name":
                    base[
                        "category_name"
                    ],

                "condition":
                    item.get(
                        "condition"
                    ),

                "listing_type_id":
                    item.get(
                        "listing_type_id"
                    ),

                "free_shipping":
                    item.get(
                        "free_shipping"
                    ),

                "shipping_cost":
                    shipping_cost,

                "shipping_known":
                    shipping_known,

                "total_price":
                    total_price,

                "relevance_score":
                    score,

                "affiliate_link":
                    "",

                "extra_earnings":
                    0,

                "cupom":
                    melhor_cupom,

                "cupom_estimado":
                    cupom_estimado
            }

            ofertas.append(
                oferta
            )

    ofertas.sort(
        key=lambda x: (
            -float(
                x.get(
                    "relevance_score"
                )
                or 0
            ),

            float(
                x.get(
                    "total_price"
                )
                or
                x.get(
                    "price"
                )
                or
                999999999
            )
        )
    )

    # Marca menor preço entre os resultados
    if ofertas:

        menor = min(
            float(
                x.get(
                    "total_price"
                )
                or
                x.get(
                    "price"
                )
                or
                999999999
            )
            for x in ofertas
        )

        for oferta in ofertas:

            valor = float(
                oferta.get(
                    "total_price"
                )
                or
                oferta.get(
                    "price"
                )
                or
                999999999
            )

            oferta[
                "menor_preco"
            ] = (
                abs(valor - menor)
                < 0.01
            )

    stats[
        "ofertas"
    ] = len(ofertas)

    return {

        "stats":
            stats,

        "categorias":
            categorias,

        "ofertas":
            ofertas
    }


# ============================================================
# API BUSCAR
# ============================================================

@app.route("/api/buscar")
def api_buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    desconto = request.args.get(
        "desconto",
        "0"
    ).strip()

    if not query:

        return jsonify({

            "erro":
                "Informe uma busca.",

            "exemplo":
                "/api/buscar?q=celular&desconto=0"

        }), 400

    resultado = search_offers(
        query,
        desconto
    )

    return jsonify(
        json_safe(resultado)
    )


# ============================================================
# CUPONS
# ============================================================

@app.route("/api/cupons")
def api_cupons():

    sincronizar = (
        request.args.get(
            "atualizar",
            "0"
        ) == "1"
    )

    sincronizacao = None

    if sincronizar:

        sincronizacao = (
            sincronizar_cupons()
        )

    return jsonify({

        "cupons":
            json_safe(
                obter_cupons()
            ),

        "sincronizacao":
            sincronizacao,

        "fonte":
            COUPONS_URL
    })


@app.route("/cupons")
def pagina_cupons():

    resultado = (
        sincronizar_cupons()
    )

    cupons = obter_cupons()

    return render_template_string(
        HTML_CUPONS,
        cupons=cupons,
        resultado=resultado
    )


# ============================================================
# GERADOR DE ANÚNCIO
# ============================================================

def gerar_texto_anuncio(
    title,
    price,
    original_price=None,
    discount=0,
    shipping_free=False,
    shipping_cost=None,
    cupom=None,
    affiliate_link="",
    extra_earnings=0
):

    linhas = []

    linhas.append(
        "🔥 OFERTA ENCONTRADA!"
    )

    linhas.append("")

    linhas.append(
        f"📦 {title}"
    )

    if original_price:

        linhas.append(
            f"💸 De: {formatar_brl(original_price)}"
        )

    linhas.append(
        f"🔥 Por: {formatar_brl(price)}"
    )

    if discount and float(discount) > 0:

        linhas.append(
            f"🏷️ {discount}% OFF"
        )

    if shipping_free:

        linhas.append(
            "🚚 Frete grátis"
        )

    elif shipping_cost is not None:

        linhas.append(
            f"🚚 Frete: {formatar_brl(shipping_cost)}"
        )

    if cupom:

        codigo = cupom.get(
            "code"
        )

        if codigo:

            linhas.append("")

            linhas.append(
                f"🎟️ CUPOM: {codigo}"
            )

            if cupom.get(
                "discount_percent"
            ):

                linhas.append(
                    "💥 Até "
                    f"{cupom['discount_percent']}% OFF"
                )

            if cupom.get(
                "max_discount"
            ):

                linhas.append(
                    "💰 Desconto máximo: "
                    f"{formatar_brl(cupom['max_discount'])}"
                )

            if cupom.get(
                "min_purchase"
            ):

                linhas.append(
                    "🛒 Compra mínima: "
                    f"{formatar_brl(cupom['min_purchase'])}"
                )

    if extra_earnings:

        linhas.append("")

        linhas.append(
            f"💰 Ganhos Extras: +{extra_earnings}%"
        )

    linhas.append("")

    linhas.append(
        "⚠️ Confira as condições do cupom "
        "e a disponibilidade no checkout."
    )

    linhas.append("")

    if affiliate_link:

        linhas.append(
            "👉 PEGAR OFERTA:"
        )

        linhas.append(
            affiliate_link
        )

    else:

        linhas.append(
            "👉 PEGAR OFERTA:"
        )

        linhas.append(
            "Cole aqui seu link de afiliado"
        )

    return "\n".join(linhas)


@app.route("/api/gerar-anuncio")
def api_gerar_anuncio():

    title = request.args.get(
        "title",
        "Produto"
    ).strip()

    price = request.args.get(
        "price",
        "0"
    )

    original_price = request.args.get(
        "original_price"
    )

    discount = request.args.get(
        "discount",
        "0"
    )

    shipping_free = (
        request.args.get(
            "shipping_free",
            "0"
        ) == "1"
    )

    shipping_cost = request.args.get(
        "shipping_cost"
    )

    affiliate_link = request.args.get(
        "affiliate_link",
        ""
    ).strip()

    extra_earnings = request.args.get(
        "extra_earnings",
        "0"
    )

    cupom = None

    cupom_code = request.args.get(
        "cupom"
    )

    if cupom_code:

        conn = get_db()

        row = conn.execute("""
            SELECT *
            FROM cupons
            WHERE code = ?
              AND active = 1
            LIMIT 1
        """, (
            cupom_code.upper(),
        )).fetchone()

        conn.close()

        if row:

            cupom = dict(row)

    texto = gerar_texto_anuncio(

        title=title,

        price=price,

        original_price=original_price,

        discount=discount,

        shipping_free=shipping_free,

        shipping_cost=shipping_cost,

        cupom=cupom,

        affiliate_link=affiliate_link,

        extra_earnings=extra_earnings
    )

    return jsonify({

        "anuncio":
            texto
    })


# ============================================================
# TESTE BUSCA
# ============================================================

@app.route("/mercadolivre/teste-busca")
def teste_busca():

    query = request.args.get(
        "q",
        "fone"
    ).strip()

    desconto = request.args.get(
        "desconto",
        "0"
    ).strip()

    resultado = search_offers(
        query,
        desconto
    )

    return jsonify(
        json_safe(resultado)
    )


# ============================================================
# OFERTAS SALVAS
# ============================================================

@app.route("/api/salvos")
def api_salvos():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY
            relevance_score DESC,
            total_price ASC
        LIMIT 100
    """).fetchall()

    conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# ============================================================
# HTML CUPONS
# ============================================================

HTML_CUPONS = """

<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Cupons - Caçador de Ofertas</title>

<style>

body {
    margin: 0;
    background: #f4f5f7;
    font-family: Arial, Helvetica, sans-serif;
    color: #222;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 20px;
}

.card {
    background: white;
    padding: 20px;
    border-radius: 16px;
    margin-bottom: 15px;
    box-shadow: 0 5px 20px rgba(0,0,0,.07);
}

.cupom {
    border: 1px solid #eee;
    border-radius: 14px;
    padding: 16px;
    margin-top: 12px;
}

.codigo {
    display: inline-block;
    background: #ffe600;
    padding: 8px 12px;
    border-radius: 8px;
    font-weight: bold;
    font-size: 18px;
}

.verde {
    color: #00a650;
    font-weight: bold;
}

button {
    width: 100%;
    padding: 13px;
    border: 0;
    border-radius: 10px;
    background: #3483fa;
    color: white;
    font-size: 16px;
}

a {
    color: #3483fa;
    text-decoration: none;
}

.small {
    color: #666;
    font-size: 13px;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>
🎟️ Cupons e Promoções
</h1>

<p>
Cupons encontrados na página oficial
de promoções do Mercado Livre.
</p>

<form
    action="/cupons"
    method="get"
>

<button>
🔄 Atualizar cupons
</button>

</form>

<br>

<a href="/">
← Voltar para ofertas
</a>

</div>


{% if resultado %}

<div class="card">

<strong>
Sincronização:
</strong>

{{ resultado }}

</div>

{% endif %}


<div class="card">

<h2>
🎟️ Cupons encontrados
</h2>


{% if cupons %}

{% for cupom in cupons %}

<div class="cupom">

<div class="codigo">

{{ cupom.code }}

</div>

{% if cupom.discount_percent %}

<p class="verde">

🔥 Até
{{ cupom.discount_percent }}% OFF

</p>

{% endif %}


{% if cupom.min_purchase %}

<p>

🛒 Compra mínima:
<strong>
R$
{{ "%.2f"|format(cupom.min_purchase)|replace(".", ",") }}
</strong>

</p>

{% endif %}


{% if cupom.max_discount %}

<p>

💰 Desconto máximo:
<strong>
R$
{{ "%.2f"|format(cupom.max_discount)|replace(".", ",") }}
</strong>

</p>

{% endif %}


<p class="small">

⚠️ O cupom pode possuir restrições
de produto, categoria, usuário,
estoque e validade.

</p>


<a
    href="{{ cupom.source_url }}"
    target="_blank"
>

Ver condições oficiais

</a>

</div>

{% endfor %}

{% else %}

<p>
Nenhum cupom encontrado no momento.
</p>

{% endif %}

</div>

</div>

</body>

</html>

"""


# ============================================================
# HTML PRINCIPAL
# ============================================================

HTML = """

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

    background: #f4f5f7;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    color: #222;
}

.container {

    max-width: 1100px;

    margin: auto;

    padding: 20px;
}

.card {

    background: white;

    border-radius: 16px;

    padding: 20px;

    margin-bottom: 20px;

    box-shadow:
        0 5px 20px
        rgba(0,0,0,.07);
}

h1 {
    margin-top: 0;
}

input,
button {

    width: 100%;

    padding: 13px;

    border-radius: 10px;

    border: 1px solid #ddd;

    font-size: 16px;
}

input {
    margin-bottom: 10px;
}

button {

    background: #3483fa;

    color: white;

    border: 0;

    cursor: pointer;

    margin-top: 5px;
}

.login {
    background: #ffe600;
    color: #222;
}

.logout {
    background: #555;
}

.stats {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(145px, 1fr)
        );

    gap: 10px;
}

.stat {

    background: #f4f4f4;

    border-radius: 12px;

    padding: 15px;
}

.stat strong {

    display: block;

    font-size: 24px;

    margin-top: 5px;
}

.oferta {

    border: 1px solid #eee;

    border-radius: 14px;

    padding: 15px;

    margin-top: 12px;

    display: flex;

    gap: 15px;

    background: white;
}

.oferta img {

    width: 105px;

    height: 105px;

    object-fit: contain;

    border-radius: 10px;

    background: #fafafa;
}

.oferta-info {
    flex: 1;
}

.preco {

    font-size: 23px;

    font-weight: bold;

    margin-top: 8px;
}

.preco-total {

    font-size: 25px;

    font-weight: bold;

    color: #008000;

    margin-top: 8px;
}

.preco-original {

    color: #777;

    text-decoration: line-through;

    margin-top: 3px;
}

.desconto {

    color: #00a650;

    font-weight: bold;

    margin-top: 5px;
}

.frete {

    color: #008000;

    font-weight: bold;

    margin-top: 5px;
}

.menor {

    display: inline-block;

    background: #00a650;

    color: white;

    border-radius: 7px;

    padding: 5px 9px;

    font-size: 12px;

    font-weight: bold;

    margin-bottom: 5px;
}

.perfil {

    display: inline-block;

    background: #eef4ff;

    color: #3483fa;

    border-radius: 8px;

    padding: 5px 9px;

    font-size: 12px;

    font-weight: bold;

    margin-bottom: 10px;
}

.cupom-box {

    background: #fff8d6;

    border: 1px dashed #e0b800;

    border-radius: 10px;

    padding: 10px;

    margin-top: 10px;
}

.cupom-code {

    font-size: 18px;

    font-weight: bold;

    color: #b88600;
}

.ganho-extra {

    background: #eaf8ef;

    color: #008a3e;

    border-radius: 8px;

    padding: 8px;

    margin-top: 8px;

    font-weight: bold;
}

.anuncio {

    background: #f7f7f7;

    border-radius: 10px;

    padding: 10px;

    margin-top: 10px;

    white-space: pre-wrap;

    font-size: 13px;

    display: none;
}

.btn-anuncio {

    background: #00a650;

    margin-top: 10px;
}

.btn-afiliado {

    background: #ff8a00;

    margin-top: 10px;
}

a {

    color: #3483fa;

    text-decoration: none;
}

.small {

    color: #666;

    font-size: 13px;
}

.status {

    background: #eef8f0;

    border-radius: 10px;

    padding: 10px;

    margin-bottom: 10px;
}

.aviso-frete {

    color: #777;

    font-size: 12px;

    margin-top: 4px;
}

.relevancia {

    color: #777;

    font-size: 11px;

    margin-top: 5px;
}

.extra-input {

    margin-top: 8px;
}

@media(max-width:600px) {

    .oferta {
        align-items: flex-start;
    }

    .oferta img {

        width: 80px;

        height: 80px;
    }

    .preco {
        font-size: 20px;
    }

    .preco-total {
        font-size: 22px;
    }

}

</style>

<script>

function gerarAnuncio(
    id,
    title,
    price,
    originalPrice,
    discount,
    shippingFree,
    shippingCost,
    cupom,
    extra
) {

    const linkInput =
        document.getElementById(
            "affiliate_" + id
        );

    const link =
        linkInput
        ? linkInput.value.trim()
        : "";

    const params = new URLSearchParams();

    params.set(
        "title",
        title
    );

    params.set(
        "price",
        price
    );

    if (originalPrice) {

        params.set(
            "original_price",
            originalPrice
        );
    }

    params.set(
        "discount",
        discount || 0
    );

    params.set(
        "shipping_free",
        shippingFree ? "1" : "0"
    );

    if (shippingCost) {

        params.set(
            "shipping_cost",
            shippingCost
        );
    }

    if (cupom) {

        params.set(
            "cupom",
            cupom
        );
    }

    params.set(
        "affiliate_link",
        link
    );

    params.set(
        "extra_earnings",
        extra || 0
    );

    fetch(
        "/api/gerar-anuncio?"
        + params.toString()
    )
    .then(
        response =>
            response.json()
    )
    .then(
        data => {

            const box =
                document.getElementById(
                    "anuncio_" + id
                );

            box.style.display =
                "block";

            box.textContent =
                data.anuncio;

            const copiar =
                document.getElementById(
                    "copiar_" + id
                );

            copiar.style.display =
                "block";
        }
    );
}


function copiarAnuncio(id) {

    const box =
        document.getElementById(
            "anuncio_" + id
        );

    navigator.clipboard
        .writeText(
            box.textContent
        )
        .then(
            () => {

                alert(
                    "Anúncio copiado!"
                );

            }
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
Encontre produtos relevantes,
compare anúncios, descontos,
cupons e oportunidades de afiliado.
</p>


{% if conectado %}

<div class="status">

🟢 Mercado Livre conectado

{% if nickname %}

<br>

<strong>
{{ nickname }}
</strong>

{% endif %}

</div>


<a href="/mercadolivre/logout">

<button class="logout">
Desconectar Mercado Livre
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
🔎 Procurar produto
</h2>

<form
    action="/buscar"
    method="get"
>

<input
    name="q"
    placeholder="Ex: celular, iPhone, Samsung, notebook..."
    required
>

<input
    name="desconto"
    type="number"
    min="0"
    value="0"
    placeholder="Desconto mínimo (%)"
>

<button type="submit">
🔍 Procurar ofertas
</button>

</form>


<p class="small">

💡 O sistema identifica automaticamente
o tipo de produto e elimina acessórios
quando a busca for específica.

</p>

<a href="/cupons">

🎟️ Ver / atualizar cupons e promoções

</a>

</div>


{% if resultado %}


<div class="card">

<h2>
📊 Resultado
</h2>


{% if resultado.stats.perfil_detectado %}

<div class="perfil">

🎯 Perfil detectado:
{{ resultado.stats.perfil_detectado }}

</div>

{% endif %}


<div class="stats">


<div class="stat">

Categorias

<strong>
{{ resultado.stats.categorias_encontradas }}
</strong>

</div>


<div class="stat">

Produtos

<strong>
{{ resultado.stats.produtos_consultados }}
</strong>

</div>


<div class="stat">

Anúncios

<strong>
{{ resultado.stats.anuncios_encontrados }}
</strong>

</div>


<div class="stat">

Relevantes

<strong>
{{ resultado.stats.anuncios_relevantes }}
</strong>

</div>


<div class="stat">

Ofertas

<strong>
{{ resultado.stats.ofertas }}
</strong>

</div>


<div class="stat">

🎟️ Com cupom

<strong>
{{ resultado.stats.ofertas_com_cupom }}
</strong>

</div>

</div>

</div>


<div class="card">

<h2>
🔥 Anúncios encontrados
</h2>


{% if resultado.ofertas %}


{% for oferta in resultado.ofertas %}


<div class="oferta">


{% if oferta.image %}

<img
    src="{{ oferta.image }}"
>

{% endif %}


<div class="oferta-info">


{% if oferta.menor_preco %}

<span class="menor">

🏆 MENOR PREÇO TOTAL

</span>

{% endif %}


<strong>
{{ oferta.title }}
</strong>


<div class="preco">

Produto:

{{ "R$ %.2f"|format(oferta.price)|replace(".", ",") }}

</div>


{% if oferta.original_price %}

<div class="preco-original">

De:

{{ "R$ %.2f"|format(oferta.original_price)|replace(".", ",") }}

</div>

{% endif %}


{% if oferta.discount > 0 %}

<div class="desconto">

🔥
{{ oferta.discount }}% OFF

</div>

{% endif %}


{% if oferta.free_shipping %}

<div class="frete">

🚚 Frete grátis

</div>

{% elif oferta.shipping_known %}

<div class="small">

🚚 Frete:

{{ "R$ %.2f"|format(oferta.shipping_cost)|replace(".", ",") }}

</div>

{% else %}

<div class="aviso-frete">

🚚 Frete não informado pela API

</div>

{% endif %}


{% if oferta.shipping_known %}

<div class="preco-total">

💰 Total:

{{ "R$ %.2f"|format(oferta.total_price)|replace(".", ",") }}

</div>

{% endif %}


{% if oferta.cupom %}

<div class="cupom-box">

<div class="cupom-code">

🎟️ CUPOM:
{{ oferta.cupom.code }}

</div>


{% if oferta.cupom.discount_percent %}

<div>

🔥 Até
{{ oferta.cupom.discount_percent }}% OFF

</div>

{% endif %}


{% if oferta.cupom.min_purchase %}

<div class="small">

Compra mínima:
{{ "R$ %.2f"|format(oferta.cupom.min_purchase)|replace(".", ",") }}

</div>

{% endif %}


{% if oferta.cupom.max_discount %}

<div class="small">

Desconto máximo:
{{ "R$ %.2f"|format(oferta.cupom.max_discount)|replace(".", ",") }}

</div>

{% endif %}


<div class="small">

⚠️ Elegibilidade deve ser confirmada
no checkout.

</div>

</div>

{% endif %}


<div class="small">

👤 Vendedor:
{{ oferta.seller_id or "N/A" }}

</div>


<div class="small">

📦 Condição:
{{ oferta.condition or "N/A" }}

</div>


<div class="relevancia">

🎯 Relevância:
{{ oferta.relevance_score }}

</div>


<br>


<a
    href="{{ oferta.permalink }}"
    target="_blank"
>

🛒 Ver produto

</a>


<br>


<input
    class="extra-input"
    id="affiliate_{{ loop.index }}"
    placeholder="Cole aqui seu link de afiliado"
>


<button
    class="btn-anuncio"
    onclick="gerarAnuncio(
        '{{ loop.index }}',
        {{ oferta.title|tojson }},
        '{{ oferta.price }}',
        '{{ oferta.original_price or '' }}',
        '{{ oferta.discount }}',
        {{ 'true' if oferta.free_shipping else 'false' }},
        '{{ oferta.shipping_cost or '' }}',
        '{{ oferta.cupom.code if oferta.cupom else '' }}',
        '0'
    )"
>

📢 Gerar anúncio

</button>


<button
    id="copiar_{{ loop.index }}"
    class="btn-afiliado"
    style="display:none"
    onclick="copiarAnuncio('{{ loop.index }}')"
>

📋 Copiar anúncio

</button>


<div
    id="anuncio_{{ loop.index }}"
    class="anuncio"
></div>


</div>

</div>


{% endfor %}


{% else %}


<p>
Nenhuma oferta relevante encontrada.
</p>


{% endif %}

</div>


{% endif %}


<div class="card">

<h3>
🧪 Testes
</h3>


<a
href="/mercadolivre/teste-produto-itens?product_id=MLB58793248"
target="_blank"
>

Testar Produto → Anúncios

</a>


<br>
<br>


<a
href="/mercadolivre/diagnostico"
target="_blank"
>

Diagnóstico

</a>

</div>


</div>

</body>

</html>

"""


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/")
def index():

    tokens = obter_tokens()

    conectado = bool(
        get_access_token()
    )

    nickname = None

    if tokens:

        nickname = tokens.get(
            "nickname"
        )

    return render_template_string(

        HTML,

        resultado=None,

        conectado=conectado,

        nickname=nickname
    )


# ============================================================
# BUSCA
# ============================================================

@app.route("/buscar")
def buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    desconto = request.args.get(
        "desconto",
        "0"
    ).strip()

    resultado = None

    if query:

        # Atualiza os cupons antes da busca.
        # Se a página oficial estiver indisponível,
        # os cupons antigos continuam no banco.
        try:

            sincronizar_cupons()

        except Exception as e:

            print(
                "[CUPONS BUSCA]",
                e
            )

        resultado = search_offers(
            query,
            desconto
        )

    tokens = obter_tokens()

    conectado = bool(
        get_access_token()
    )

    nickname = None

    if tokens:

        nickname = tokens.get(
            "nickname"
        )

    return render_template_string(

        HTML,

        resultado=resultado,

        conectado=conectado,

        nickname=nickname
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

        "mercado_livre_configurado":
            bool(ML_CLIENT_ID),

        "mercado_livre_conectado":
            bool(
                get_access_token()
            ),

        "fluxo":
            "products/{product_id}/items",

        "criterio_menor_preco":
            "produto + frete",

        "filtro_relevancia":
            "ativo",

        "cupons":
            "ativo",

        "pagina_cupons":
            "/cupons",

        "gerador_anuncio":
            "/api/gerar-anuncio"
    })


# ============================================================
# ERROS
# ============================================================

@app.errorhandler(404)
def pagina_nao_encontrada(error):

    return jsonify({

        "erro":
            "Rota não encontrada.",

        "rota":
            request.path

    }), 404


@app.errorhandler(500)
def erro_interno(error):

    return jsonify({

        "erro":
            "Erro interno no servidor.",

        "detalhes":
            str(error)

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