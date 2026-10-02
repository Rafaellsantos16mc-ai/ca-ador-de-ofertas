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
    url_for,
    session,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    secrets.token_hex(32)
)

app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ============================================================
# CONFIGURAÇÕES
# ============================================================

DB_FILE = os.getenv("DB_FILE", "ofertas.db")

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


# OAuth
ML_AUTH_URL = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
)


# NOVA BUSCA DE PRODUTOS
ML_PRODUCTS_SEARCH_URL = (
    "https://api.mercadolibre.com/products/search"
)

ML_PRODUCT_URL = (
    "https://api.mercadolibre.com/products"
)

SITE_ID = "MLB"


# ============================================================
# BANCO
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id TEXT,
            titulo TEXT,
            preco REAL,
            preco_original REAL,
            desconto INTEGER,
            link TEXT,
            imagem TEXT,
            criado_em TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mercadolivre_token (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            scope TEXT,
            atualizado_em TEXT
        )
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# UTILITÁRIOS
# ============================================================

def agora_timestamp():

    return int(
        datetime.now(timezone.utc).timestamp()
    )


def moeda(valor):

    if valor is None:
        return "R$ 0,00"

    try:
        valor = float(valor)
    except Exception:
        valor = 0

    texto = f"{valor:,.2f}"

    texto = (
        texto
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    return f"R$ {texto}"


def limpar_titulo(titulo):

    if not titulo:
        return ""

    titulo = " ".join(
        str(titulo).split()
    )

    return titulo[:180]


# ============================================================
# PKCE
# ============================================================

def gerar_code_verifier():

    return secrets.token_urlsafe(64)


def gerar_code_challenge(code_verifier):

    digest = hashlib.sha256(
        code_verifier.encode("utf-8")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")


# ============================================================
# TOKEN
# ============================================================

def salvar_token(
    access_token,
    refresh_token,
    expires_in,
    user_id=None,
    scope=None
):

    conn = get_db()

    expires_at = (
        agora_timestamp()
        + int(expires_in or 21600)
    )

    conn.execute(
        """
        INSERT INTO mercadolivre_token
        (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            scope,
            atualizado_em
        )
        VALUES (1, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(id) DO UPDATE SET

            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at,
            user_id = excluded.user_id,
            scope = excluded.scope,
            atualizado_em = excluded.atualizado_em
        """,
        (
            access_token,
            refresh_token,
            expires_at,
            str(user_id) if user_id else None,
            scope,
            datetime.now(
                timezone.utc
            ).isoformat(),
        )
    )

    conn.commit()
    conn.close()


def obter_token_db():

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM mercadolivre_token
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)


def apagar_token():

    conn = get_db()

    conn.execute(
        """
        DELETE FROM mercadolivre_token
        WHERE id = 1
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# REFRESH TOKEN
# ============================================================

def renovar_access_token(refresh_token):

    if not ML_CLIENT_ID:
        return False, "ML_CLIENT_ID não configurado."

    if not ML_CLIENT_SECRET:
        return False, "ML_CLIENT_SECRET não configurado."

    if not refresh_token:
        return False, "Refresh token não encontrado."

    try:

        resposta = requests.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            headers={
                "Accept": "application/json",
                "Content-Type":
                    "application/x-www-form-urlencoded",
            },
            timeout=30,
        )

    except Exception as e:

        return False, str(e)

    if resposta.status_code != 200:

        try:
            erro = resposta.json()
        except Exception:
            erro = resposta.text

        return False, (
            f"HTTP {resposta.status_code}: {erro}"
        )

    dados = resposta.json()

    novo_access_token = dados.get(
        "access_token"
    )

    novo_refresh_token = dados.get(
        "refresh_token"
    )

    if not novo_access_token:
        return False, (
            "Mercado Livre não retornou "
            "um novo access token."
        )

    if not novo_refresh_token:
        return False, (
            "Mercado Livre não retornou "
            "um novo refresh token."
        )

    salvar_token(
        access_token=novo_access_token,
        refresh_token=novo_refresh_token,
        expires_in=dados.get(
            "expires_in",
            21600
        ),
        user_id=dados.get("user_id"),
        scope=dados.get("scope"),
    )

    return True, novo_access_token


def obter_access_token():

    token = obter_token_db()

    if not token:
        return None

    access_token = token.get(
        "access_token"
    )

    refresh_token = token.get(
        "refresh_token"
    )

    expires_at = int(
        token.get("expires_at") or 0
    )

    agora = agora_timestamp()

    # Ainda válido
    if (
        access_token
        and expires_at > agora + 120
    ):
        return access_token

    # Precisa renovar
    if not refresh_token:

        apagar_token()

        return None

    sucesso, resultado = (
        renovar_access_token(
            refresh_token
        )
    )

    if sucesso:
        return resultado

    print(
        "[ERRO REFRESH]",
        resultado
    )

    apagar_token()

    return None


# ============================================================
# OAUTH - LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return """
        <h2>ML_CLIENT_ID não configurado</h2>
        """, 500

    state = secrets.token_urlsafe(32)

    code_verifier = (
        gerar_code_verifier()
    )

    code_challenge = (
        gerar_code_challenge(
            code_verifier
        )
    )

    session["ml_oauth_state"] = state

    session["ml_code_verifier"] = (
        code_verifier
    )

    parametros = {

        "response_type": "code",

        "client_id":
            ML_CLIENT_ID,

        "redirect_uri":
            ML_REDIRECT_URI,

        "state":
            state,

        "code_challenge":
            code_challenge,

        "code_challenge_method":
            "S256",
    }

    url = (
        ML_AUTH_URL
        + "?"
        + urlencode(parametros)
    )

    return redirect(url)


# ============================================================
# OAUTH - CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    erro = request.args.get(
        "error"
    )

    if erro:

        descricao = request.args.get(
            "error_description",
            "Autorização recusada."
        )

        return f"""
        <h2>Erro na autorização</h2>

        <p>
        <strong>{erro}</strong>
        </p>

        <p>
        {descricao}
        </p>
        """, 400

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    state_salvo = session.get(
        "ml_oauth_state"
    )

    code_verifier = session.get(
        "ml_code_verifier"
    )

    if (
        not state
        or not state_salvo
        or state != state_salvo
    ):

        return """
        <h2>Erro de segurança</h2>

        <p>
        O state do OAuth não corresponde
        à solicitação iniciada.
        </p>
        """, 400

    if not code:

        return """
        <h2>Código não recebido.</h2>
        """, 400

    if not code_verifier:

        return """
        <h2>Code verifier não encontrado.</h2>
        """, 400

    try:

        resposta = requests.post(
            ML_TOKEN_URL,
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
                    code_verifier,
            },
            headers={
                "Accept":
                    "application/json",

                "Content-Type":
                    "application/x-www-form-urlencoded",
            },
            timeout=30,
        )

    except Exception as e:

        return f"""
        <h2>Erro de conexão</h2>
        <p>{e}</p>
        """, 500

    if resposta.status_code != 200:

        try:
            erro_json = resposta.json()
        except Exception:
            erro_json = resposta.text

        return f"""
        <h2>Erro OAuth</h2>

        <pre>
        {erro_json}
        </pre>
        """, 400

    dados = resposta.json()

    access_token = dados.get(
        "access_token"
    )

    refresh_token = dados.get(
        "refresh_token"
    )

    if not access_token:

        return """
        <h2>
        Mercado Livre não retornou
        o Access Token.
        </h2>
        """, 500

    if not refresh_token:

        return """
        <h2>
        Mercado Livre não retornou
        o Refresh Token.
        </h2>

        <p>
        Verifique o fluxo Refresh Token
        na configuração da aplicação.
        </p>
        """, 500

    salvar_token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=dados.get(
            "expires_in",
            21600
        ),
        user_id=dados.get(
            "user_id"
        ),
        scope=dados.get(
            "scope"
        ),
    )

    session.pop(
        "ml_oauth_state",
        None
    )

    session.pop(
        "ml_code_verifier",
        None
    )

    return redirect(
        url_for("index")
    )


# ============================================================
# CALLBACK 2
# ============================================================

@app.route("/mercadolivre/callback2")
def callback2():

    parametros = (
        request.query_string
        .decode("utf-8")
    )

    if parametros:

        return redirect(
            url_for(
                "mercadolivre_callback"
            )
            + "?"
            + parametros
        )

    return redirect(
        url_for(
            "mercadolivre_login"
        )
    )


# ============================================================
# NOTIFICAÇÕES
# ============================================================

@app.route(
    "/mercadolivre/notificacoes",
    methods=["GET", "POST"]
)
def mercadolivre_notificacoes():

    dados = request.get_json(
        silent=True
    )

    print(
        "[ML NOTIFICACAO]",
        dados
    )

    return "OK", 200


# ============================================================
# STATUS
# ============================================================

@app.route("/mercadolivre/status")
def mercadolivre_status():

    token = obter_access_token()

    if not token:

        return """
        <h2>
        🔴 Mercado Livre desconectado
        </h2>

        <a href="/mercadolivre/login">
        Conectar Mercado Livre
        </a>
        """, 401

    try:

        resposta = requests.get(
            "https://api.mercadolibre.com/users/me",
            headers={
                "Authorization":
                    f"Bearer {token}"
            },
            timeout=30,
        )

    except Exception as e:

        return f"""
        <h2>Erro</h2>
        <p>{e}</p>
        """, 500

    if resposta.status_code != 200:

        return f"""
        <h2>
        ❌ Token recusado
        </h2>

        <pre>
        {resposta.text}
        </pre>

        <a href="/mercadolivre/login">
        Conectar novamente
        </a>
        """, resposta.status_code

    dados = resposta.json()

    return render_template_string(
        """
        <h2>
        🟢 Mercado Livre conectado
        </h2>

        <p>
        <strong>ID:</strong>
        {{ dados.get("id") }}
        </p>

        <p>
        <strong>Nickname:</strong>
        {{ dados.get("nickname") }}
        </p>

        <a href="/">
        Voltar
        </a>
        """,
        dados=dados
    )


# ============================================================
# BUSCA DE PRODUTOS - NOVA API
# ============================================================

def buscar_produtos(
    query,
    limit=20
):

    access_token = (
        obter_access_token()
    )

    if not access_token:

        return {
            "erro":
                "Mercado Livre não conectado.",

            "produtos": []
        }

    parametros = {

        "status":
            "active",

        "site_id":
            SITE_ID,

        "q":
            query,

        "limit":
            limit,

        "offset":
            0,
    }

    headers = {

        "Authorization":
            f"Bearer {access_token}",

        "Accept":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/1.0",
    }

    try:

        resposta = requests.get(
            ML_PRODUCTS_SEARCH_URL,
            params=parametros,
            headers=headers,
            timeout=30,
        )

    except Exception as e:

        return {
            "erro":
                f"Erro de conexão: {e}",

            "produtos": []
        }

    if resposta.status_code == 401:

        apagar_token()

        return {
            "erro":
                "Token expirado ou inválido. "
                "Conecte o Mercado Livre novamente.",

            "produtos": []
        }

    if resposta.status_code == 403:

        try:
            erro = resposta.json()
        except Exception:
            erro = resposta.text

        print(
            "[ML 403]",
            erro
        )

        return {
            "erro":
                "Mercado Livre recusou a busca (403). "
                "Detalhes: "
                + str(erro),

            "produtos": []
        }

    if resposta.status_code != 200:

        try:
            erro = resposta.json()
        except Exception:
            erro = resposta.text

        return {
            "erro":
                f"Mercado Livre retornou "
                f"{resposta.status_code}: "
                f"{erro}",

            "produtos": []
        }

    try:

        dados = resposta.json()

    except Exception:

        return {
            "erro":
                "Resposta inválida da API.",

            "produtos": []
        }

    produtos = []

    for item in dados.get(
        "results",
        []
    ):

        produto_id = item.get(
            "id"
        )

        nome = item.get(
            "name"
        )

        if not nome:
            nome = item.get(
                "title",
                ""
            )

        nome = limpar_titulo(
            nome
        )

        produto_url = (
            f"https://www.mercadolivre.com.br/"
            f"produto/{produto_id}"
        )

        produtos.append({

            "id":
                produto_id,

            "titulo":
                nome,

            "preco":
                None,

            "preco_original":
                None,

            "desconto":
                0,

            "link":
                produto_url,

            "imagem":
                None,

            "domain_id":
                item.get(
                    "domain_id"
                ),

            "status":
                item.get(
                    "status"
                ),
        })

    return {

        "erro":
            None,

        "produtos":
            produtos
    }


# ============================================================
# SALVAR
# ============================================================

def salvar_oferta(produto):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO ofertas
        (
            produto_id,
            titulo,
            preco,
            preco_original,
            desconto,
            link,
            imagem,
            criado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            produto.get("id"),
            produto.get("titulo"),
            produto.get("preco"),
            produto.get("preco_original"),
            produto.get("desconto"),
            produto.get("link"),
            produto.get("imagem"),
            datetime.now(
                timezone.utc
            ).isoformat(),
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# MENSAGEM
# ============================================================

def gerar_mensagem(
    produto,
    link_afiliado=""
):

    titulo = produto.get(
        "titulo",
        ""
    )

    link = (
        link_afiliado.strip()
        or produto.get(
            "link",
            ""
        )
    )

    texto = f"""
🔥 PRODUTO ENCONTRADO!

🛒 {titulo}

🔎 Confira no Mercado Livre:

👉 {link}

⚡ Consulte preço e disponibilidade.
"""

    return texto.strip()


# ============================================================
# HTML
# ============================================================

HTML = """
<!doctype html>

<html lang="pt-br">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1"
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

    padding: 15px;

    font-family:
        Arial,
        sans-serif;

    background:
        #f5f5f5;

    color:
        #222;
}

.container {

    max-width:
        900px;

    margin:
        auto;
}

.header {

    background:
        #ffe600;

    padding:
        25px;

    border-radius:
        18px;

    margin-bottom:
        20px;
}

.header h1 {

    margin:
        0 0 10px;

    font-size:
        34px;
}

.card {

    background:
        white;

    padding:
        20px;

    border-radius:
        18px;

    margin-bottom:
        20px;

    box-shadow:
        0 3px 15px
        rgba(0,0,0,.06);
}

input {

    width:
        100%;

    padding:
        15px;

    border:
        1px solid #ccc;

    border-radius:
        10px;

    font-size:
        17px;

    margin-bottom:
        12px;
}

button,
.button {

    display:
        inline-block;

    background:
        #3483fa;

    color:
        white;

    border:
        none;

    padding:
        13px 18px;

    border-radius:
        10px;

    text-decoration:
        none;

    font-size:
        16px;

    cursor:
        pointer;
}

.green {

    background:
        #16883d;
}

.product {

    border:
        1px solid #ddd;

    padding:
        16px;

    border-radius:
        14px;

    margin-top:
        15px;
}

.product h3 {

    margin-top:
        0;
}

.badge {

    display:
        inline-block;

    background:
        #e8f7ed;

    color:
        #16883d;

    padding:
        6px 9px;

    border-radius:
        8px;

    margin:
        5px 0;
}

.error {

    background:
        #ffe1e1;

    color:
        #9b1717;

    padding:
        15px;

    border-radius:
        10px;

    white-space:
        pre-wrap;
}

textarea {

    width:
        100%;

    min-height:
        200px;

    padding:
        15px;

    border-radius:
        10px;

    border:
        1px solid #ccc;

    font-size:
        15px;
}

</style>

</head>

<body>

<div class="container">

<div class="header">

<h1>
🛒 Caçador de Ofertas
</h1>

<p>
Busca de produtos do Mercado Livre.
</p>

{% if conectado %}

<a
class="button green"
href="/mercadolivre/status"
>
🟢 Mercado Livre conectado
</a>

{% else %}

<a
class="button"
href="/mercadolivre/login"
>
🔐 Conectar Mercado Livre
</a>

{% endif %}

</div>


<div class="card">

<h2>
🔎 Buscar produto
</h2>

<form
action="/buscar"
method="get"
>

<input
type="text"
name="q"
value="{{ query }}"
placeholder="Ex.: air fryer, celular Samsung, TV 50"
required
>

<button
type="submit"
>
Buscar
</button>

</form>

</div>


{% if erro %}

<div class="card">

<div class="error">
{{ erro }}
</div>

</div>

{% endif %}


{% if produtos %}

<div class="card">

<h2>
🛍️ Produtos encontrados
</h2>

<p>
Encontramos {{ produtos|length }}
produto(s).
</p>

{% for produto in produtos %}

<div class="product">

<h3>
{{ produto.titulo }}
</h3>

<div class="badge">
Produto de catálogo
</div>

<p>
<strong>ID:</strong>
{{ produto.id }}
</p>

{% if produto.domain_id %}

<p>
<strong>Categoria:</strong>
{{ produto.domain_id }}
</p>

{% endif %}

<a
class="button"
href="{{ produto.link }}"
target="_blank"
>
Ver no Mercado Livre
</a>


<form
action="/gerar"
method="post"
style="margin-top:15px;"
>

<input
type="hidden"
name="produto_id"
value="{{ produto.id }}"
>

<input
type="hidden"
name="titulo"
value="{{ produto.titulo }}"
>

<input
type="hidden"
name="link"
value="{{ produto.link }}"
>

<input
type="text"
name="link_afiliado"
placeholder="Cole aqui o link de afiliado"
>

<button
class="green"
type="submit"
>
Gerar publicação
</button>

</form>

</div>

{% endfor %}

</div>

{% endif %}


{% if publicacao %}

<div class="card">

<h2>
📱 Publicação pronta
</h2>

<textarea
id="texto"
>{{ publicacao }}</textarea>

<br><br>

<button
onclick="copiarTexto()"
>
📋 Copiar publicação
</button>

</div>

{% endif %}


</div>


<script>

function copiarTexto() {

    const texto =
        document.getElementById(
            "texto"
        );

    texto.select();

    texto.setSelectionRange(
        0,
        99999
    );

    navigator.clipboard.writeText(
        texto.value
    );

    alert(
        "Publicação copiada!"
    );
}

</script>

</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    conectado = bool(
        obter_access_token()
    )

    return render_template_string(
        HTML,

        conectado=
            conectado,

        produtos=
            [],

        erro=
            None,

        query=
            "",

        publicacao=
            None,

        moeda=
            moeda,
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

        return redirect(
            url_for("index")
        )

    resultado = (
        buscar_produtos(
            query=query,
            limit=20
        )
    )

    return render_template_string(

        HTML,

        conectado=
            bool(
                obter_access_token()
            ),

        produtos=
            resultado.get(
                "produtos",
                []
            ),

        erro=
            resultado.get(
                "erro"
            ),

        query=
            query,

        publicacao=
            None,

        moeda=
            moeda,
    )


# ============================================================
# GERAR PUBLICAÇÃO
# ============================================================

@app.route(
    "/gerar",
    methods=["POST"]
)
def gerar():

    produto = {

        "id":
            request.form.get(
                "produto_id"
            ),

        "titulo":
            request.form.get(
                "titulo"
            ),

        "link":
            request.form.get(
                "link"
            ),
    }

    link_afiliado = (
        request.form.get(
            "link_afiliado",
            ""
        ).strip()
    )

    salvar_oferta(
        produto
    )

    publicacao = (
        gerar_mensagem(
            produto,
            link_afiliado
        )
    )

    return render_template_string(

        HTML,

        conectado=
            bool(
                obter_access_token()
            ),

        produtos=
            [],

        erro=
            None,

        query=
            "",

        publicacao=
            publicacao,

        moeda=
            moeda,
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return {

        "status":
            "ok",

        "app":
            "Cacador de Ofertas",

        "mercadolivre_configurado":
            bool(
                ML_CLIENT_ID
                and
                ML_CLIENT_SECRET
            ),

        "oauth":
            bool(
                obter_token_db()
            ),

        "search_endpoint":
            "products/search",
    }


# ============================================================
# START
# ============================================================

init_db()


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