import os
import re
import sqlite3
import secrets
import hashlib
import base64
from datetime import datetime
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    jsonify,
    session,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = (
    os.getenv("SECRET_KEY")
    or os.getenv("FLASK_SECRET_KEY")
    or secrets.token_hex(32)
)

DB_PATH = os.getenv(
    "DB_PATH",
    "promocoes.db"
)

ML_API = "https://api.mercadolibre.com"

ML_AUTH = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN = (
    "https://api.mercadolibre.com/oauth/token"
)

SITE_ID = "MLB"

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "20"
    )
)

DEFAULT_LIMIT = int(
    os.getenv(
        "DEFAULT_LIMIT",
        "20"
    )
)

MAX_LIMIT = int(
    os.getenv(
        "MAX_LIMIT",
        "50"
    )
)


# ============================================================
# CREDENCIAIS DO MERCADO LIVRE
# ============================================================

CLIENT_ID = (
    os.getenv("ML_CLIENT_ID")
    or os.getenv("MERCADOLIVRE_CLIENT_ID")
    or os.getenv("CLIENT_ID")
    or os.getenv("APP_ID")
)

CLIENT_SECRET = (
    os.getenv("ML_CLIENT_SECRET")
    or os.getenv("MERCADOLIVRE_CLIENT_SECRET")
    or os.getenv("CLIENT_SECRET")
    or os.getenv("SECRET_KEY_ML")
)

REDIRECT_URI_ENV = (
    os.getenv("ML_REDIRECT_URI")
    or os.getenv("MERCADOLIVRE_REDIRECT_URI")
    or os.getenv("REDIRECT_URI")
)

APP_URL = (
    os.getenv("APP_URL")
    or os.getenv("RAILWAY_PUBLIC_DOMAIN")
    or ""
).strip()

USE_PKCE = (
    os.getenv(
        "ML_USE_PKCE",
        "false"
    ).lower()
    == "true"
)


# ============================================================
# REDIRECT URI
# ============================================================

def get_redirect_uri():

    if REDIRECT_URI_ENV:
        return REDIRECT_URI_ENV.rstrip("/")

    if APP_URL:

        base = APP_URL.rstrip("/")

        if base.startswith("http://"):
            return base + "/oauth/callback"

        if base.startswith("https://"):
            return base + "/oauth/callback"

        return (
            "https://"
            + base
            + "/oauth/callback"
        )

    return url_for(
        "oauth_callback",
        _external=True
    )


# ============================================================
# BANCO
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            titulo TEXT,
            preco REAL,
            preco_antigo REAL,
            desconto REAL,
            imagem TEXT,
            permalink TEXT,
            link_afiliado TEXT,
            texto TEXT,
            criado_em TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_auth (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            token_type TEXT,
            expires_at INTEGER,
            user_id TEXT,
            scope TEXT,
            atualizado_em TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# AUTENTICAÇÃO
# ============================================================

def get_auth():

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM ml_auth
        WHERE id = 1
    """).fetchone()

    conn.close()

    return row


def save_auth(
    access_token,
    refresh_token,
    token_type,
    expires_in,
    user_id,
    scope
):

    expires_at = (
        int(datetime.now().timestamp())
        + int(expires_in or 21600)
        - 60
    )

    conn = get_db()

    conn.execute("""
        INSERT INTO ml_auth (
            id,
            access_token,
            refresh_token,
            token_type,
            expires_at,
            user_id,
            scope,
            atualizado_em
        )
        VALUES (
            1, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(id)
        DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            token_type = excluded.token_type,
            expires_at = excluded.expires_at,
            user_id = excluded.user_id,
            scope = excluded.scope,
            atualizado_em = excluded.atualizado_em
    """, (
        access_token,
        refresh_token,
        token_type or "Bearer",
        expires_at,
        str(user_id or ""),
        scope or "",
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def delete_auth():

    conn = get_db()

    conn.execute("""
        DELETE FROM ml_auth
        WHERE id = 1
    """)

    conn.commit()
    conn.close()


def token_expired(auth):

    if not auth:
        return True

    try:

        return (
            int(datetime.now().timestamp())
            >= int(auth["expires_at"])
        )

    except Exception:

        return True


# ============================================================
# REFRESH TOKEN
# ============================================================

def refresh_access_token():

    auth = get_auth()

    if not auth:
        return False

    refresh_token = auth["refresh_token"]

    if not refresh_token:
        return False

    if not CLIENT_ID or not CLIENT_SECRET:
        return False

    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    try:

        response = requests.post(
            ML_TOKEN,
            data=data,
            timeout=REQUEST_TIMEOUT,
            headers={
                "Accept":
                    "application/json",

                "Content-Type":
                    "application/x-www-form-urlencoded"
            }
        )

        if response.status_code != 200:

            print(
                "[ML REFRESH ERRO]",
                response.status_code,
                response.text
            )

            return False

        data = response.json()

        save_auth(
            data.get("access_token"),
            data.get("refresh_token"),
            data.get(
                "token_type",
                "Bearer"
            ),
            data.get(
                "expires_in",
                21600
            ),
            data.get("user_id"),
            data.get("scope", "")
        )

        print(
            "[ML] Token renovado"
        )

        return True

    except Exception as e:

        print(
            "[ML REFRESH EXCEPTION]",
            e
        )

        return False


# ============================================================
# TOKEN VÁLIDO
# ============================================================

def get_valid_access_token():

    auth = get_auth()

    if not auth:
        return None

    if token_expired(auth):

        if not refresh_access_token():
            return None

        auth = get_auth()

    return auth["access_token"]


# ============================================================
# MERCADO LIVRE GET
# ============================================================

def ml_get(
    endpoint,
    params=None,
    authenticated=False
):

    headers = {
        "Accept": "application/json",
        "User-Agent":
            "PromocoesMercadoLivre/3.0"
    }

    if authenticated:

        token = get_valid_access_token()

        if not token:

            raise Exception(
                "Conta do Mercado Livre não conectada."
            )

        headers["Authorization"] = (
            "Bearer " + token
        )

    response = requests.get(
        ML_API + endpoint,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# UTILIDADES
# ============================================================

def money(value):

    try:

        value = float(value)

        return (
            f"R$ {value:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return "R$ 0,00"


def calcular_desconto(
    preco,
    preco_antigo
):

    try:

        preco = float(preco)
        preco_antigo = float(
            preco_antigo
        )

        if (
            preco_antigo > preco
            and preco_antigo > 0
        ):

            return round(
                (
                    (
                        preco_antigo
                        - preco
                    )
                    / preco_antigo
                )
                * 100,
                1
            )

    except Exception:
        pass

    return 0


def limpar_titulo(titulo):

    if not titulo:
        return ""

    titulo = re.sub(
        r"\s+",
        " ",
        titulo
    ).strip()

    if len(titulo) > 110:

        titulo = (
            titulo[:107]
            + "..."
        )

    return titulo


def normalizar_item_id(valor):

    if not valor:
        return ""

    match = re.search(
        r"(MLB\d+)",
        valor.upper()
    )

    if match:
        return match.group(1)

    return valor.strip().upper()


# ============================================================
# PRODUTOS
# ============================================================

def buscar_produtos(
    query,
    limit=20
):

    limit = max(
        1,
        min(
            int(limit),
            MAX_LIMIT
        )
    )

    data = ml_get(
        "/sites/MLB/search",
        params={
            "q": query,
            "limit": limit
        }
    )

    resultados = []

    for item in data.get(
        "results",
        []
    ):

        preco = item.get(
            "price"
        )

        preco_antigo = item.get(
            "original_price"
        )

        desconto = calcular_desconto(
            preco,
            preco_antigo
        )

        resultados.append({

            "id":
                item.get("id"),

            "titulo":
                limpar_titulo(
                    item.get("title")
                ),

            "preco":
                preco,

            "preco_formatado":
                money(preco),

            "preco_antigo":
                preco_antigo,

            "preco_antigo_formatado":
                (
                    money(preco_antigo)
                    if preco_antigo
                    else ""
                ),

            "desconto":
                desconto,

            "imagem":
                item.get(
                    "thumbnail",
                    ""
                ),

            "permalink":
                item.get(
                    "permalink",
                    ""
                )

        })

    return resultados


def buscar_item(item_id):

    item_id = normalizar_item_id(
        item_id
    )

    data = ml_get(
        "/items/" + item_id
    )

    preco = data.get(
        "price"
    )

    preco_antigo = data.get(
        "original_price"
    )

    desconto = calcular_desconto(
        preco,
        preco_antigo
    )

    imagem = (
        data.get(
            "thumbnail"
        )
        or ""
    )

    pictures = (
        data.get(
            "pictures"
        )
        or []
    )

    if pictures:

        imagem = (
            pictures[0].get(
                "secure_url"
            )
            or pictures[0].get(
                "url"
            )
            or imagem
        )

    return {

        "id":
            data.get("id"),

        "titulo":
            limpar_titulo(
                data.get("title")
            ),

        "preco":
            preco,

        "preco_formatado":
            money(preco),

        "preco_antigo":
            preco_antigo,

        "preco_antigo_formatado":
            (
                money(preco_antigo)
                if preco_antigo
                else ""
            ),

        "desconto":
            desconto,

        "imagem":
            imagem,

        "permalink":
            data.get(
                "permalink",
                ""
            )

    }


# ============================================================
# TEXTO
# ============================================================

def gerar_texto(
    titulo,
    preco,
    preco_antigo,
    desconto,
    link=""
):

    texto = [
        "🔥 OFERTA DO DIA 🔥",
        "",
        "🛒 " + titulo,
        ""
    ]

    if (
        preco_antigo
        and desconto > 0
    ):

        texto.append(
            "❌ De: "
            + money(preco_antigo)
        )

        texto.append(
            "🔥 Por: "
            + money(preco)
        )

        texto.append(
            f"🏷️ {desconto}% OFF"
        )

    else:

        texto.append(
            "💰 Por apenas: "
            + money(preco)
        )

    texto.append("")

    if link:

        texto.append(
            "👉 COMPRAR AQUI:"
        )

        texto.append(
            link
        )

    texto.append("")

    texto.append(
        "⚠️ Preço e disponibilidade "
        "podem mudar sem aviso."
    )

    return "\n".join(texto)


# ============================================================
# USUÁRIO DO MERCADO LIVRE
# ============================================================

def get_user_me():

    token = get_valid_access_token()

    if not token:
        return None

    response = requests.get(
        ML_API + "/users/me",
        headers={
            "Authorization":
                "Bearer " + token,

            "Accept":
                "application/json"
        },
        timeout=REQUEST_TIMEOUT
    )

    if response.status_code == 401:

        if refresh_access_token():

            token = get_valid_access_token()

            response = requests.get(
                ML_API + "/users/me",
                headers={
                    "Authorization":
                        "Bearer " + token,

                    "Accept":
                        "application/json"
                },
                timeout=REQUEST_TIMEOUT
            )

    response.raise_for_status()

    return response.json()


# ============================================================
# CONECTAR
# ============================================================

@app.route("/conectar")
@app.route("/conectar-mercadolivre")
def conectar():

    if not CLIENT_ID:

        return error_page(
            "Client ID não encontrado",
            "Verifique as Variables do Railway."
        )

    redirect_uri = get_redirect_uri()

    state = secrets.token_urlsafe(
        32
    )

    session[
        "ml_oauth_state"
    ] = state

    params = {

        "response_type":
            "code",

        "client_id":
            CLIENT_ID,

        "redirect_uri":
            redirect_uri,

        "state":
            state
    }

    # --------------------------------------------------------
    # PKCE OPCIONAL
    # --------------------------------------------------------

    if USE_PKCE:

        verifier = secrets.token_urlsafe(
            64
        )

        digest = hashlib.sha256(
            verifier.encode()
        ).digest()

        challenge = (
            base64.urlsafe_b64encode(
                digest
            )
            .decode()
            .rstrip("=")
        )

        session[
            "ml_code_verifier"
        ] = verifier

        params[
            "code_challenge"
        ] = challenge

        params[
            "code_challenge_method"
        ] = "S256"

    url = (
        ML_AUTH
        + "?"
        + urlencode(params)
    )

    print(
        "[ML OAUTH]",
        url
    )

    return redirect(url)


# ============================================================
# CALLBACK PRINCIPAL
# ============================================================

@app.route("/oauth/callback")
@app.route("/callback")
@app.route("/auth/callback")
def oauth_callback():

    return process_oauth_callback()


# ============================================================
# RAIZ TAMBÉM ACEITA CALLBACK
# ============================================================

@app.route("/")
def index():

    # Se o Mercado Livre retornar
    # para a raiz com ?code=...
    if request.args.get("code"):

        return process_oauth_callback()

    return render_home()


# ============================================================
# PROCESSAR OAUTH
# ============================================================

def process_oauth_callback():

    error = request.args.get(
        "error"
    )

    if error:

        descricao = request.args.get(
            "error_description",
            error
        )

        return error_page(
            "Autorização cancelada",
            descricao
        )

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    saved_state = session.pop(
        "ml_oauth_state",
        None
    )

    if not code:

        return error_page(
            "Código não recebido",
            "O Mercado Livre não enviou o código de autorização."
        )

    if saved_state and state != saved_state:

        return error_page(
            "Erro de segurança",
            "O state recebido não corresponde à solicitação."
        )

    if not CLIENT_ID:

        return error_page(
            "Client ID ausente",
            "Configure o Client ID no Railway."
        )

    if not CLIENT_SECRET:

        return error_page(
            "Client Secret ausente",
            "Configure o Client Secret no Railway."
        )

    redirect_uri = get_redirect_uri()

    data = {

        "grant_type":
            "authorization_code",

        "client_id":
            CLIENT_ID,

        "client_secret":
            CLIENT_SECRET,

        "code":
            code,

        "redirect_uri":
            redirect_uri
    }

    if USE_PKCE:

        verifier = session.pop(
            "ml_code_verifier",
            None
        )

        if verifier:

            data[
                "code_verifier"
            ] = verifier

    try:

        response = requests.post(

            ML_TOKEN,

            data=data,

            headers={
                "Accept":
                    "application/json",

                "Content-Type":
                    "application/x-www-form-urlencoded"
            },

            timeout=REQUEST_TIMEOUT
        )

        print(
            "[ML TOKEN STATUS]",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[ML TOKEN ERRO]",
                response.text
            )

            try:
                detalhe = response.json()

            except Exception:
                detalhe = response.text

            return error_page(
                "Mercado Livre recusou a conexão",
                "<pre>"
                + str(detalhe)
                + "</pre>"
            )

        data = response.json()

        access_token = data.get(
            "access_token"
        )

        refresh_token = data.get(
            "refresh_token"
        )

        if not access_token:

            return error_page(
                "Access Token não recebido",
                "O Mercado Livre não retornou o token."
            )

        save_auth(

            access_token,

            refresh_token,

            data.get(
                "token_type",
                "Bearer"
            ),

            data.get(
                "expires_in",
                21600
            ),

            data.get(
                "user_id"
            ),

            data.get(
                "scope",
                ""
            )
        )

        print(
            "[ML] CONTA CONECTADA"
        )

        return redirect(
            "/?conectado=1"
        )

    except Exception as e:

        print(
            "[ML CALLBACK ERRO]",
            e
        )

        return error_page(
            "Erro na conexão",
            str(e)
        )


# ============================================================
# DESCONECTAR
# ============================================================

@app.route("/desconectar")
def desconectar():

    delete_auth()

    return redirect(
        "/?desconectado=1"
    )


# ============================================================
# BUSCAR
# ============================================================

@app.route("/buscar")
def buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return redirect("/")

    try:

        resultados = buscar_produtos(
            query
        )

        erro = ""

    except Exception as e:

        resultados = []

        erro = str(e)

    return render_home(
        resultados=resultados,
        query=query,
        erro=erro
    )


# ============================================================
# CRIAR OFERTA
# ============================================================

@app.route(
    "/gerar",
    methods=["GET"]
)
def gerar_get():

    item_id = request.args.get(
        "item_id",
        ""
    )

    if not item_id:

        return redirect("/")

    try:

        produto = buscar_item(
            item_id
        )

        texto = gerar_texto(

            produto["titulo"],

            produto["preco"],

            produto["preco_antigo"],

            produto["desconto"],

            ""
        )

    except Exception as e:

        return render_home(
            erro=str(e)
        )

    return render_home(
        produto=produto,
        texto=texto
    )


# ============================================================
# SALVAR OFERTA
# ============================================================

@app.route(
    "/gerar",
    methods=["POST"]
)
def gerar_post():

    item_id = request.form.get(
        "item_id",
        ""
    )

    link = request.form.get(
        "link_afiliado",
        ""
    ).strip()

    texto = request.form.get(
        "texto",
        ""
    ).strip()

    try:

        produto = buscar_item(
            item_id
        )

        if not texto:

            texto = gerar_texto(

                produto["titulo"],

                produto["preco"],

                produto["preco_antigo"],

                produto["desconto"],

                link
            )

        conn = get_db()

        conn.execute("""
            INSERT INTO ofertas (
                item_id,
                titulo,
                preco,
                preco_antigo,
                desconto,
                imagem,
                permalink,
                link_afiliado,
                texto,
                criado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (

            produto["id"],
            produto["titulo"],
            produto["preco"],
            produto["preco_antigo"],
            produto["desconto"],
            produto["imagem"],
            produto["permalink"],
            link,
            texto,
            datetime.now().isoformat()

        ))

        conn.commit()
        conn.close()

    except Exception as e:

        return render_home(
            erro=str(e)
        )

    return redirect("/")


# ============================================================
# HOME HTML
# ============================================================

def render_home(
    resultados=None,
    query="",
    produto=None,
    texto="",
    erro=""
):

    resultados = resultados or []

    auth = get_auth()

    conectado = False

    usuario = None

    if auth:

        token = get_valid_access_token()

        if token:

            conectado = True

            try:

                usuario = get_user_me()

            except Exception:
                usuario = None

    conn = get_db()

    ofertas = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    mensagem = ""

    if request.args.get(
        "conectado"
    ) == "1":

        mensagem = (
            "✅ Conta do Mercado Livre "
            "conectada com sucesso!"
        )

    elif request.args.get(
        "desconectado"
    ) == "1":

        mensagem = (
            "Conta desconectada."
        )

    elif erro:

        mensagem = (
            "❌ " + erro
        )

    return render_template_string(

        HTML,

        resultados=resultados,

        query=query,

        produto=produto,

        texto=texto,

        ofertas=ofertas,

        conectado=conectado,

        usuario=usuario,

        mensagem=mensagem,

        money=money
    )


# ============================================================
# ERRO
# ============================================================

def error_page(
    titulo,
    mensagem
):

    return render_template_string(
        ERROR_HTML,
        titulo=titulo,
        mensagem=mensagem
    )


# ============================================================
# STATUS
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "ok": True,

        "app":
            "Promocoes Mercado Livre",

        "oauth":
            bool(
                CLIENT_ID
                and CLIENT_SECRET
            ),

        "conectado":
            bool(
                get_auth()
            ),

        "redirect_uri":
            get_redirect_uri()

    })


@app.route(
    "/mercadolivre/status"
)
def ml_status():

    auth = get_auth()

    conectado = False

    usuario = None

    if auth:

        token = get_valid_access_token()

        if token:

            conectado = True

            try:
                usuario = get_user_me()

            except Exception:
                pass

    return jsonify({

        "oauth_configurado":
            bool(
                CLIENT_ID
                and CLIENT_SECRET
            ),

        "conectado":
            conectado,

        "user_id":
            (
                auth["user_id"]
                if auth
                else None
            ),

        "usuario":
            usuario,

        "redirect_uri":
            get_redirect_uri()

    })


@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    return jsonify({

        "client_id":
            bool(CLIENT_ID),

        "client_secret":
            bool(CLIENT_SECRET),

        "redirect_uri":
            get_redirect_uri(),

        "pkce":
            USE_PKCE,

        "token_salvo":
            bool(get_auth()),

        "rotas_callback": [
            "/oauth/callback",
            "/callback",
            "/auth/callback",
            "/"
        ]

    })


# ============================================================
# HTML
# ============================================================

HTML = """
<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Promoções Mercado Livre</title>

<style>

* {
    box-sizing:border-box;
}

body {
    margin:0;
    background:#f3f4f6;
    font-family:Arial,sans-serif;
    color:#222;
}

header {
    background:#ffe600;
    padding:20px;
    text-align:center;
}

header h1 {
    margin:0;
}

.container {
    max-width:1100px;
    margin:auto;
    padding:18px;
}

.box {
    background:#fff;
    border-radius:14px;
    padding:18px;
    margin-bottom:18px;
    box-shadow:0 2px 10px rgba(0,0,0,.08);
}

.connection {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:15px;
    flex-wrap:wrap;
}

.connected {
    background:#d1e7dd;
    color:#075b36;
    padding:12px;
    border-radius:9px;
    font-weight:bold;
}

.disconnected {
    background:#fff3cd;
    color:#664d03;
    padding:12px;
    border-radius:9px;
    font-weight:bold;
}

input,
textarea,
button {
    font-size:16px;
}

input,
textarea {
    width:100%;
    padding:12px;
    border:1px solid #ddd;
    border-radius:9px;
}

button,
a.btn {
    padding:12px 18px;
    border:0;
    border-radius:9px;
    font-weight:bold;
    text-decoration:none;
    cursor:pointer;
}

.btn-green {
    background:#00a650;
    color:#fff;
}

.btn-blue {
    background:#3483fa;
    color:#fff;
}

.btn-yellow {
    background:#ffe600;
    color:#222;
}

.search {
    display:flex;
    gap:10px;
}

.search input {
    flex:1;
}

.grid {
    display:grid;
    grid-template-columns:
        repeat(auto-fill,minmax(280px,1fr));
    gap:16px;
}

.card {
    background:#fff;
    border-radius:14px;
    overflow:hidden;
    box-shadow:0 2px 10px rgba(0,0,0,.08);
}

.card img {
    width:100%;
    height:220px;
    object-fit:contain;
}

.card-body {
    padding:15px;
}

.title {
    font-weight:bold;
    min-height:55px;
}

.price-old {
    text-decoration:line-through;
    color:#777;
}

.price {
    color:#00a650;
    font-size:24px;
    font-weight:bold;
}

.discount {
    display:inline-block;
    margin-top:7px;
    padding:5px 8px;
    border-radius:6px;
    background:#e6f7ed;
    color:#008c45;
    font-weight:bold;
}

.actions {
    display:flex;
    gap:8px;
    margin-top:12px;
}

.actions > * {
    flex:1;
    text-align:center;
}

textarea {
    min-height:150px;
}

.alert {
    padding:14px;
    background:#fff3cd;
    border-radius:9px;
    margin-bottom:15px;
}

.success {
    padding:14px;
    background:#d1e7dd;
    border-radius:9px;
    margin-bottom:15px;
}

@media(max-width:600px) {

    .search {
        flex-direction:column;
    }

    .connection {
        align-items:stretch;
    }

}

</style>

</head>

<body>

<header>

<h1>🛒 Promoções Mercado Livre</h1>

<p>Seu painel de afiliado</p>

</header>


<div class="container">


<div class="box">

<div class="connection">

<div>

{% if conectado %}

<div class="connected">
🟢 Mercado Livre conectado
</div>

{% if usuario %}

<div style="margin-top:7px;">
👤
{{ usuario.nickname
or usuario.first_name
or "Conta conectada" }}
</div>

{% endif %}

{% else %}

<div class="disconnected">
🟡 Mercado Livre não conectado
</div>

{% endif %}

</div>


<div>

{% if conectado %}

<a
href="/desconectar"
class="btn"
style="
background:#d32f2f;
color:white;
"
>
Desconectar
</a>

{% else %}

<a
href="/conectar"
class="btn btn-green"
>
🔐 Conectar Mercado Livre
</a>

{% endif %}

</div>

</div>

</div>


{% if mensagem %}

<div class="
{% if conectado %}
success
{% else %}
alert
{% endif %}
">

{{ mensagem }}

</div>

{% endif %}


<div class="box">

<h2>🔎 Buscar produto</h2>

<form
method="GET"
action="/buscar"
>

<div class="search">

<input
name="q"
value="{{ query }}"
placeholder="Ex.: fone bluetooth"
required
>

<button
class="btn-blue"
type="submit"
>
Buscar
</button>

</div>

</form>

</div>


{% if resultados %}

<div class="box">

<h2>
Produtos encontrados
</h2>

<div class="grid">

{% for p in resultados %}

<div class="card">

{% if p.imagem %}

<img
src="{{ p.imagem }}"
loading="lazy"
>

{% endif %}

<div class="card-body">

<div class="title">
{{ p.titulo }}
</div>

{% if p.preco_antigo
and p.desconto > 0 %}

<div class="price-old">
{{ p.preco_antigo_formatado }}
</div>

{% endif %}

<div class="price">
{{ p.preco_formatado }}
</div>

{% if p.desconto > 0 %}

<div class="discount">
{{ p.desconto }}% OFF
</div>

{% endif %}

<div class="actions">

<a
href="{{ p.permalink }}"
target="_blank"
class="btn btn-blue"
>
Ver
</a>

<a
href="/gerar?item_id={{ p.id }}"
class="btn btn-green"
>
Criar oferta
</a>

</div>

</div>

</div>

{% endfor %}

</div>

</div>

{% endif %}


{% if produto %}

<div class="box">

<h2>
🔥 Criar oferta
</h2>

<div class="grid">

<div>

<img
src="{{ produto.imagem }}"
style="
width:100%;
max-height:300px;
object-fit:contain;
"
>

</div>

<div>

<h3>
{{ produto.titulo }}
</h3>

<div class="price">
{{ produto.preco_formatado }}
</div>

{% if produto.desconto > 0 %}

<div class="discount">
{{ produto.desconto }}% OFF
</div>

{% endif %}

</div>

</div>


<form
method="POST"
action="/gerar"
>

<input
type="hidden"
name="item_id"
value="{{ produto.id }}"
>

<p>
<strong>
🔗 Seu link de afiliado
</strong>
</p>

<input
type="url"
name="link_afiliado"
placeholder="Cole seu link de afiliado aqui"
>

<p>
<strong>
📝 Texto
</strong>
</p>

<textarea
name="texto"
>{{ texto }}</textarea>

<br><br>

<button
class="btn-green"
type="submit"
>
💾 Salvar oferta
</button>

</form>

</div>

{% endif %}


<div class="box">

<h2>
📦 Ofertas salvas
</h2>

{% if ofertas %}

<div class="grid">

{% for oferta in ofertas %}

<div class="card">

{% if oferta.imagem %}

<img
src="{{ oferta.imagem }}"
>

{% endif %}

<div class="card-body">

<div class="title">
{{ oferta.titulo }}
</div>

<div class="price">
{{ money(oferta.preco) }}
</div>

{% if oferta.desconto > 0 %}

<div class="discount">
{{ oferta.desconto }}% OFF
</div>

{% endif %}

<textarea
id="texto{{ oferta.id }}"
readonly
>{{ oferta.texto }}</textarea>

<div class="actions">

<button
class="btn-yellow"
onclick="
navigator.clipboard.writeText(
document.getElementById(
'texto{{ oferta.id }}'
).value
);
alert('Copiado!');
"
>
📋 Copiar
</button>

{% if oferta.link_afiliado %}

<a
href="{{ oferta.link_afiliado }}"
target="_blank"
class="btn btn-green"
>
Abrir
</a>

{% endif %}

</div>

</div>

</div>

{% endfor %}

</div>

{% else %}

<p>
Nenhuma oferta salva ainda.
</p>

{% endif %}

</div>


</div>

</body>

</html>
"""


# ============================================================
# ERROR HTML
# ============================================================

ERROR_HTML = """

<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Erro</title>

<style>

body {
    margin:0;
    background:#f3f4f6;
    font-family:Arial;
    display:flex;
    justify-content:center;
    align-items:center;
    min-height:100vh;
}

.box {
    width:90%;
    max-width:600px;
    background:white;
    padding:25px;
    border-radius:15px;
    box-shadow:0 4px 20px rgba(0,0,0,.12);
}

a {
    display:inline-block;
    margin-top:15px;
    background:#ffe600;
    color:#222;
    padding:12px 18px;
    border-radius:9px;
    text-decoration:none;
    font-weight:bold;
}

</style>

</head>

<body>

<div class="box">

<h2>
{{ titulo }}
</h2>

<div>
{{ mensagem|safe }}
</div>

<a href="/">
Voltar
</a>

</div>

</body>

</html>

"""


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