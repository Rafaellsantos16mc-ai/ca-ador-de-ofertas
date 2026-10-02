import os
import sqlite3
import secrets
import hashlib
import base64
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    render_template_string,
    redirect,
    session
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "troque-esta-chave-no-railway"
)

DB_FILE = os.getenv(
    "DB_FILE",
    "ofertas.db"
)


# ============================================================
# CONFIGURAÇÕES
# ============================================================

SITE_ID = "MLB"

DESCONTO_MINIMO_PADRAO = float(
    os.getenv(
        "DESCONTO_MINIMO",
        "10"
    )
)

LIMITE_BUSCA = int(
    os.getenv(
        "LIMITE_BUSCA",
        "30"
    )
)


# ============================================================
# MERCADO LIVRE
# ============================================================

ML_CLIENT_ID = os.getenv(
    "ML_CLIENT_ID"
)

ML_CLIENT_SECRET = os.getenv(
    "ML_CLIENT_SECRET"
)

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
)

ML_AUTH_URL = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
)

ML_API_URL = (
    "https://api.mercadolibre.com"
)

ML_SEARCH_URL = (
    f"{ML_API_URL}/sites/{SITE_ID}/search"
)


# ============================================================
# BANCO
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (

            id INTEGER PRIMARY KEY,

            access_token TEXT,

            refresh_token TEXT,

            expires_at INTEGER

        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            produto_id TEXT,

            item_id TEXT UNIQUE,

            titulo TEXT,

            preco REAL,

            preco_original REAL,

            desconto REAL,

            link TEXT,

            affiliate_link TEXT,

            criado_em TEXT

        )
    """)

    conn.commit()

    conn.close()


init_db()


# ============================================================
# TOKEN
# ============================================================

def salvar_tokens(data):

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
        int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )
        + expires_in
    )

    conn = get_db()

    conn.execute(
        "DELETE FROM tokens"
    )

    conn.execute("""
        INSERT INTO tokens (

            id,
            access_token,
            refresh_token,
            expires_at

        )

        VALUES (
            1,
            ?,
            ?,
            ?
        )
    """, (

        access_token,

        refresh_token,

        expires_at

    ))

    conn.commit()

    conn.close()


def obter_tokens():

    conn = get_db()

    row = conn.execute("""
        SELECT

            access_token,
            refresh_token,
            expires_at

        FROM tokens

        WHERE id = 1
    """).fetchone()

    conn.close()

    if not row:
        return None

    return {

        "access_token":
            row["access_token"],

        "refresh_token":
            row["refresh_token"],

        "expires_at":
            row["expires_at"]

    }


def renovar_access_token():

    tokens = obter_tokens()

    if not tokens:

        return None

    refresh_token = tokens.get(
        "refresh_token"
    )

    if not refresh_token:

        return None

    payload = {

        "grant_type":
            "refresh_token",

        "client_id":
            ML_CLIENT_ID,

        "client_secret":
            ML_CLIENT_SECRET,

        "refresh_token":
            refresh_token

    }

    try:

        response = requests.post(

            ML_TOKEN_URL,

            data=payload,

            timeout=30

        )

        print(
            "[REFRESH TOKEN]",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[REFRESH ERRO]",
                response.text[:1000]
            )

            return None

        data = response.json()

        salvar_tokens(
            data
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


def obter_access_token():

    tokens = obter_tokens()

    if not tokens:

        return None

    agora = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    expires_at = int(
        tokens["expires_at"]
    )

    if expires_at > agora + 300:

        return tokens[
            "access_token"
        ]

    print(
        "[TOKEN] Renovando token..."
    )

    return renovar_access_token()


def headers_ml():

    token = obter_access_token()

    if not token:

        return None

    return {

        "Authorization":
            f"Bearer {token}",

        "Accept":
            "application/json",

        "Content-Type":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/1.0"

    }


# ============================================================
# OAUTH
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

    state = secrets.token_urlsafe(
        32
    )

    code_verifier = secrets.token_urlsafe(
        64
    )

    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(
                code_verifier.encode()
            ).digest()
        )
        .rstrip(b"=")
        .decode()
    )

    session[
        "oauth_state"
    ] = state

    session[
        "code_verifier"
    ] = code_verifier

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
            code_challenge,

        "code_challenge_method":
            "S256"

    }

    url = (
        ML_AUTH_URL
        + "?"
        + urlencode(params)
    )

    return redirect(
        url
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

        return f"""
        <h2>Erro no Mercado Livre</h2>
        <p>{error}</p>
        """

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    saved_state = session.get(
        "oauth_state"
    )

    code_verifier = session.get(
        "code_verifier"
    )

    if not code:

        return (
            "Código de autorização não recebido.",
            400
        )

    if (
        not state
        or state != saved_state
    ):

        return (
            "Estado OAuth inválido.",
            400
        )

    if not code_verifier:

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
            code_verifier

    }

    try:

        response = requests.post(

            ML_TOKEN_URL,

            data=payload,

            timeout=30

        )

        print(
            "[OAUTH TOKEN]",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[OAUTH ERRO]",
                response.text[:1000]
            )

            return (
                "Erro ao obter token: "
                + response.text[:1000],
                400
            )

        data = response.json()

        if not data.get(
            "refresh_token"
        ):

            return (
                "Mercado Livre não retornou "
                "Refresh Token.",
                400
            )

        salvar_tokens(
            data
        )

        session.pop(
            "oauth_state",
            None
        )

        session.pop(
            "code_verifier",
            None
        )

        return redirect(
            "/"
        )

    except Exception as e:

        print(
            "[ERRO OAUTH]",
            e
        )

        return (
            f"Erro OAuth: {e}",
            500
        )


@app.route(
    "/mercadolivre/callback2"
)
def callback2():

    return redirect(
        "/mercadolivre/callback"
    )


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/mercadolivre/status"
)
def mercadolivre_status():

    conectado = (
        obter_access_token()
        is not None
    )

    return {

        "conectado":
            conectado

    }


# ============================================================
# NOTIFICAÇÕES
# ============================================================

@app.route(
    "/mercadolivre/notificacoes",
    methods=[
        "GET",
        "POST"
    ]
)
def notificacoes():

    print(
        "[NOTIFICACAO ML]",
        request.method,
        request.get_json(
            silent=True
        )
    )

    return {
        "ok": True
    }


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    return {
        "status":
            "ok"
    }


# ============================================================
# BUSCAR ANÚNCIOS
# ============================================================

def buscar_anuncios(
    query,
    limite=30
):

    headers = headers_ml()

    if not headers:

        headers = {

            "Accept":
                "application/json",

            "User-Agent":
                "Mozilla/5.0"

        }

    params = {

        "q":
            query,

        "limit":
            min(
                limite,
                50
            ),

        "offset":
            0

    }

    try:

        response = requests.get(

            ML_SEARCH_URL,

            headers=headers,

            params=params,

            timeout=30

        )

        print(
            "[BUSCA ANÚNCIOS]",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[ERRO API]",
                response.text[:1500]
            )

            raise Exception(

                "Mercado Livre retornou "
                f"HTTP {response.status_code}: "
                f"{response.text[:500]}"

            )

        data = response.json()

        resultados = data.get(
            "results",
            []
        )

        print(
            "[ANÚNCIOS ENCONTRADOS]",
            len(resultados)
        )

        return resultados

    except requests.RequestException as e:

        print(
            "[ERRO REQUEST]",
            e
        )

        raise Exception(
            f"Erro de comunicação com "
            f"Mercado Livre: {e}"
        )


# ============================================================
# DESCONTO
# ============================================================

def calcular_desconto(
    preco,
    original
):

    try:

        if (
            preco is None
            or original is None
        ):

            return 0

        preco = float(
            preco
        )

        original = float(
            original
        )

        if original <= 0:

            return 0

        if preco >= original:

            return 0

        desconto = (
            (
                original - preco
            )
            / original
        ) * 100

        return round(
            desconto,
            1
        )

    except Exception:

        return 0


# ============================================================
# PRODUTO JÁ PUBLICADO?
# ============================================================

def oferta_ja_salva(
    item_id
):

    conn = get_db()

    row = conn.execute("""
        SELECT id

        FROM ofertas

        WHERE item_id = ?

        LIMIT 1
    """, (
        item_id,
    )).fetchone()

    conn.close()

    return row is not None


# ============================================================
# BUSCAR OFERTAS
# ============================================================

def buscar_ofertas(
    query,
    limite=30,
    desconto_minimo=10
):

    anuncios = buscar_anuncios(
        query,
        limite
    )

    resultados = []

    vistos = set()

    for anuncio in anuncios:

        item_id = anuncio.get(
            "id"
        )

        if not item_id:

            continue

        # Evita duplicados na própria busca
        if item_id in vistos:

            continue

        vistos.add(
            item_id
        )

        # Evita ofertas já geradas/publicadas
        if oferta_ja_salva(
            item_id
        ):

            print(
                "[REPETIDO]",
                item_id
            )

            continue

        titulo = (
            anuncio.get(
                "title"
            )
            or
            "Produto"
        )

        link = (
            anuncio.get(
                "permalink"
            )
            or
            ""
        )

        preco = anuncio.get(
            "price"
        )

        preco_original = anuncio.get(
            "original_price"
        )

        moeda = anuncio.get(
            "currency_id"
        ) or "BRL"

        desconto = calcular_desconto(
            preco,
            preco_original
        )

        # ====================================================
        # FILTRO DE DESCONTO
        # ====================================================

        if desconto < desconto_minimo:

            print(
                "[SEM DESCONTO MÍNIMO]",
                item_id,
                desconto
            )

            continue

        imagem = (
            anuncio.get(
                "thumbnail"
            )
            or
            ""
        )

        catalog_product_id = (
            anuncio.get(
                "catalog_product_id"
            )
            or
            ""
        )

        categoria = (
            anuncio.get(
                "category_id"
            )
            or
            ""
        )

        seller = anuncio.get(
            "seller",
            {}
        )

        seller_id = seller.get(
            "id"
        )

        seller_nickname = seller.get(
            "nickname"
        )

        resultados.append({

            "produto_id":
                catalog_product_id,

            "item_id":
                item_id,

            "titulo":
                titulo,

            "categoria":
                categoria,

            "preco":
                preco,

            "preco_original":
                preco_original,

            "desconto":
                desconto,

            "moeda":
                moeda,

            "link":
                link,

            "imagem":
                imagem,

            "seller_id":
                seller_id,

            "seller_nickname":
                seller_nickname

        })

    # ========================================================
    # MAIOR DESCONTO PRIMEIRO
    # ========================================================

    resultados.sort(

        key=lambda x:
            x.get(
                "desconto",
                0
            ),

        reverse=True

    )

    print(
        "[OFERTAS VÁLIDAS]",
        len(resultados)
    )

    return resultados


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(
    produto,
    affiliate_link
):

    conn = get_db()

    try:

        conn.execute("""
            INSERT OR IGNORE INTO ofertas (

                produto_id,
                item_id,
                titulo,
                preco,
                preco_original,
                desconto,
                link,
                affiliate_link,
                criado_em

            )

            VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?
            )
        """, (

            produto.get(
                "produto_id"
            ),

            produto.get(
                "item_id"
            ),

            produto.get(
                "titulo"
            ),

            produto.get(
                "preco"
            ),

            produto.get(
                "preco_original"
            ),

            produto.get(
                "desconto"
            ),

            produto.get(
                "link"
            ),

            affiliate_link,

            datetime.now(
                timezone.utc
            ).isoformat()

        ))

        conn.commit()

    finally:

        conn.close()


# ============================================================
# FORMATAR PREÇO
# ============================================================

def formatar_preco(
    valor
):

    if valor is None:

        return "Consultar"

    try:

        numero = float(
            valor
        )

        return (
            "R$ "
            + f"{numero:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return str(
            valor
        )


# ============================================================
# GERAR MENSAGEM
# ============================================================

def gerar_mensagem(
    titulo,
    preco,
    preco_original,
    desconto,
    affiliate_link
):

    mensagem = (
        "🔥 OFERTA ENCONTRADA!\n\n"
    )

    mensagem += (
        f"🛒 {titulo}\n\n"
    )

    if (
        preco_original
        and desconto > 0
    ):

        mensagem += (
            "❌ De: "
            + formatar_preco(
                preco_original
            )
            + "\n"
        )

        mensagem += (
            "🔥 Por: "
            + formatar_preco(
                preco
            )
            + "\n"
        )

        mensagem += (
            "💰 Desconto: "
            f"{desconto:.0f}%\n\n"
        )

    else:

        mensagem += (
            "💰 Preço: "
            + formatar_preco(
                preco
            )
            + "\n\n"
        )

    mensagem += (
        "👉 COMPRAR AQUI:\n"
    )

    mensagem += (
        affiliate_link
    )

    mensagem += (
        "\n\n"
        "⚠️ Preço e disponibilidade "
        "podem mudar a qualquer momento."
    )

    return mensagem


# ============================================================
# FILTRO BRL
# ============================================================

@app.template_filter(
    "brl"
)
def brl_filter(
    value
):

    return formatar_preco(
        value
    )


# ============================================================
# HTML
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

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    padding: 20px;

    background: #f5f5f5;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    color: #222;
}

.container {

    max-width: 1100px;

    margin: auto;
}

h1 {

    margin-bottom: 5px;

    font-size: 34px;
}

.sub {

    color: #666;

    font-size: 18px;

    margin-bottom: 25px;
}

.card {

    background: white;

    padding: 20px;

    border-radius: 16px;

    margin-bottom: 20px;

    box-shadow:
        0 3px 15px
        rgba(0,0,0,.06);
}

.status {

    padding: 15px;

    border-radius: 12px;

    background: #e8f5e9;

    color: #237a36;

    font-size: 18px;

    margin-bottom: 20px;
}

input {

    width: 100%;

    padding: 15px;

    border: 1px solid #ddd;

    border-radius: 11px;

    font-size: 17px;

    margin-bottom: 12px;
}

button {

    width: 100%;

    padding: 15px;

    border: none;

    border-radius: 11px;

    background: #3483fa;

    color: white;

    font-size: 17px;

    font-weight: bold;

    cursor: pointer;
}

button:hover {

    opacity: .92;
}

.produto {

    border: 1px solid #e5e5e5;

    border-radius: 15px;

    padding: 18px;

    margin-top: 16px;

    background: white;
}

.produto h3 {

    margin-top: 12px;

    line-height: 1.4;

    font-size: 19px;
}

.badge {

    display: inline-block;

    padding: 6px 9px;

    background: #e8f0fe;

    color: #174ea6;

    border-radius: 7px;

    font-size: 12px;

    margin: 2px;
}

.badge-oferta {

    display: inline-block;

    padding: 7px 10px;

    background: #e6f4ea;

    color: #137333;

    border-radius: 8px;

    font-weight: bold;

    font-size: 13px;
}

.preco-antigo {

    color: #777;

    text-decoration: line-through;

    font-size: 15px;

    margin-top: 8px;
}

.preco {

    font-size: 28px;

    font-weight: bold;

    margin-top: 3px;
}

.desconto {

    display: inline-block;

    padding: 8px 11px;

    background: #e6f4ea;

    color: #137333;

    border-radius: 9px;

    font-weight: bold;

    margin-top: 9px;
}

.link {

    display: block;

    margin-top: 12px;

    color: #3483fa;

    word-break: break-all;

    text-decoration: none;
}

textarea {

    width: 100%;

    min-height: 190px;

    margin-top: 12px;

    padding: 14px;

    border-radius: 11px;

    border: 1px solid #ddd;

    font-size: 15px;
}

.small {

    font-size: 13px;

    color: #777;
}

.erro {

    background: white;

    padding: 20px;

    border-radius: 15px;

    border-left:
        6px solid #e53935;

    margin-bottom: 20px;
}

.info {

    background: #f1f5ff;

    border-radius: 11px;

    padding: 13px;

    color: #345;
}

hr {

    border: none;

    border-top: 1px solid #eee;

    margin: 20px 0;
}

</style>

</head>

<body>

<div class="container">

<h1>
🛒 Caçador de Ofertas
</h1>

<div class="sub">
Mercado Livre → ofertas reais → filtro de desconto → publicação
</div>


{% if conectado %}

<div class="status">
✅ Mercado Livre conectado
</div>

{% else %}

<div class="card">

<h3>
🔐 Mercado Livre
</h3>

<p>
Conecte sua conta do Mercado Livre para começar.
</p>

<a href="/mercadolivre/login">

<button>
🔐 Conectar Mercado Livre
</button>

</a>

</div>

{% endif %}


<div class="card">

<h2>
🔎 Procurar ofertas
</h2>

<div class="info">

<strong>
Filtro atual:
</strong>

desconto mínimo de
<strong>
{{ desconto_minimo }}%
</strong>

<br>

<strong>
Limite:
</strong>

{{ limite_busca }}
anúncios por busca.

</div>

<br>

<form
method="GET"
action="/buscar"
>

<input
type="text"
name="q"
placeholder="Ex: Air Fryer, celular, TV, tênis..."
value="{{ query or '' }}"
required
>

<input
type="number"
name="desconto"
min="0"
max="100"
step="1"
value="{{ desconto_minimo }}"
placeholder="Desconto mínimo (%)"
>

<button type="submit">
🔍 Buscar ofertas
</button>

</form>

</div>


{% if erro %}

<div class="erro">

<h2>
❌ Erro
</h2>

<p>
{{ erro }}
</p>

</div>

{% endif %}


{% if produtos %}

<div class="card">

<h2>
🔥 Ofertas encontradas
</h2>

<p class="small">

Encontramos
<strong>
{{ produtos|length }}
</strong>
oferta(s) válida(s).

</p>


{% for p in produtos %}

<div class="produto">

<span class="badge-oferta">
🔥 OFERTA
</span>

<span class="badge">
ID: {{ p.item_id }}
</span>

<span class="badge-oferta">
{{ "%.0f"|format(p.desconto) }}% OFF
</span>

<h3>
{{ p.titulo }}
</h3>


{% if p.preco_original and p.desconto > 0 %}

<div class="preco-antigo">

De:
{{ p.preco_original|brl }}

</div>

<div class="preco">

Por:
{{ p.preco|brl }}

</div>

<div class="desconto">

🔥
{{ "%.0f"|format(p.desconto) }}%
OFF

</div>

{% else %}

<div class="preco">

{{ p.preco|brl }}

</div>

{% endif %}


<hr>


<div class="small">

Categoria:
{{ p.categoria }}

</div>


{% if p.seller_nickname %}

<div class="small">

Vendedor:
{{ p.seller_nickname }}

</div>

{% endif %}


<a
class="link"
href="{{ p.link }}"
target="_blank"
>

🔗 Ver anúncio no Mercado Livre

</a>


<form
method="POST"
action="/gerar"
>

<input
type="hidden"
name="produto_id"
value="{{ p.produto_id }}"
>

<input
type="hidden"
name="item_id"
value="{{ p.item_id }}"
>

<input
type="hidden"
name="titulo"
value="{{ p.titulo }}"
>

<input
type="hidden"
name="preco"
value="{{ p.preco }}"
>

<input
type="hidden"
name="preco_original"
value="{{ p.preco_original or '' }}"
>

<input
type="hidden"
name="desconto"
value="{{ p.desconto }}"
>

<input
type="hidden"
name="link"
value="{{ p.link }}"
>

<input
type="text"
name="affiliate_link"
placeholder="Cole aqui seu link de afiliado"
required
>

<button type="submit">

📢 Gerar publicação

</button>

</form>

</div>

{% endfor %}

</div>

{% endif %}


{% if mensagem %}

<div class="card">

<h2>
📢 Publicação pronta
</h2>

<p class="small">
Copie a mensagem e publique no seu canal do WhatsApp.
</p>

<textarea
readonly
>{{ mensagem }}</textarea>

</div>

{% endif %}


</div>

</body>

</html>

"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    conectado = (
        obter_access_token()
        is not None
    )

    return render_template_string(

        HTML,

        conectado=conectado,

        produtos=None,

        query=None,

        erro=None,

        mensagem=None,

        desconto_minimo=
            DESCONTO_MINIMO_PADRAO,

        limite_busca=
            LIMITE_BUSCA

    )


# ============================================================
# BUSCAR
# ============================================================

@app.route(
    "/buscar"
)
def buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return redirect(
            "/"
        )

    try:

        desconto_param = request.args.get(
            "desconto",
            str(
                DESCONTO_MINIMO_PADRAO
            )
        )

        try:

            desconto_minimo = float(
                desconto_param
            )

        except Exception:

            desconto_minimo = (
                DESCONTO_MINIMO_PADRAO
            )

        desconto_minimo = max(
            0,
            min(
                desconto_minimo,
                100
            )
        )

        produtos = buscar_ofertas(

            query,

            limite=LIMITE_BUSCA,

            desconto_minimo=
                desconto_minimo

        )

        if not produtos:

            erro = (
                "Nenhuma oferta válida "
                "foi encontrada com o "
                f"filtro de {desconto_minimo:.0f}% "
                "ou todos os anúncios "
                "já foram processados."
            )

        else:

            erro = None

        conectado = (
            obter_access_token()
            is not None
        )

        return render_template_string(

            HTML,

            conectado=conectado,

            produtos=produtos,

            query=query,

            erro=erro,

            mensagem=None,

            desconto_minimo=
                desconto_minimo,

            limite_busca=
                LIMITE_BUSCA

        )

    except Exception as e:

        print(
            "[ERRO BUSCA]",
            e
        )

        conectado = (
            obter_access_token()
            is not None
        )

        return render_template_string(

            HTML,

            conectado=conectado,

            produtos=None,

            query=query,

            erro=str(e),

            mensagem=None,

            desconto_minimo=
                DESCONTO_MINIMO_PADRAO,

            limite_busca=
                LIMITE_BUSCA

        )


# ============================================================
# GERAR PUBLICAÇÃO
# ============================================================

@app.route(
    "/gerar",
    methods=["POST"]
)
def gerar():

    produto_id = request.form.get(
        "produto_id"
    )

    item_id = request.form.get(
        "item_id"
    )

    titulo = request.form.get(
        "titulo"
    )

    preco = request.form.get(
        "preco"
    )

    preco_original = request.form.get(
        "preco_original"
    )

    desconto = request.form.get(
        "desconto",
        "0"
    )

    link = request.form.get(
        "link"
    )

    affiliate_link = request.form.get(
        "affiliate_link",
        ""
    ).strip()

    if not affiliate_link:

        return (
            "Link de afiliado obrigatório.",
            400
        )

    try:

        preco_float = float(
            preco
        )

    except Exception:

        preco_float = 0

    try:

        original_float = (

            float(
                preco_original
            )

            if preco_original

            else None

        )

    except Exception:

        original_float = None

    try:

        desconto_float = float(
            desconto
        )

    except Exception:

        desconto_float = 0

    mensagem = gerar_mensagem(

        titulo,

        preco_float,

        original_float,

        desconto_float,

        affiliate_link

    )

    salvar_oferta(

        {

            "produto_id":
                produto_id,

            "item_id":
                item_id,

            "titulo":
                titulo,

            "preco":
                preco_float,

            "preco_original":
                original_float,

            "desconto":
                desconto_float,

            "link":
                link

        },

        affiliate_link

    )

    conectado = (
        obter_access_token()
        is not None
    )

    return render_template_string(

        HTML,

        conectado=conectado,

        produtos=None,

        query=None,

        erro=None,

        mensagem=mensagem,

        desconto_minimo=
            DESCONTO_MINIMO_PADRAO,

        limite_busca=
            LIMITE_BUSCA

    )


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