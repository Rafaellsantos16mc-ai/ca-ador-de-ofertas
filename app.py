import os
import re
import json
import time
import sqlite3
import secrets
import hashlib
import base64
import html
import unicodedata
from urllib.parse import urlencode, quote

import requests
from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    send_file,
    jsonify,
)

# ============================================================
# APP
# ============================================================

app = Flask(__name__)

DB_PATH = "ofertas.db"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br"

SITE_ID = "MLB"

CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

# Quantidade de anúncios que tentaremos descobrir na página pública.
MAX_DISCOVERED_IDS = 80

# Quantos anúncios reais vamos consultar na API.
MAX_ITEMS_TO_CHECK = 50

# Timeout das requisições.
HTTP_TIMEOUT = 20

# User-Agent para a página pública do Mercado Livre.
PUBLIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ============================================================
# BANCO
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT UNIQUE,
            titulo TEXT,
            preco_atual REAL,
            preco_original REAL,
            desconto REAL,
            permalink TEXT,
            imagem TEXT,
            criado_em INTEGER
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def salvar_config(chave, valor):
    conn = get_db()

    conn.execute(
        """
        INSERT INTO config(chave, valor)
        VALUES (?, ?)
        ON CONFLICT(chave)
        DO UPDATE SET valor = excluded.valor
        """,
        (chave, valor),
    )

    conn.commit()
    conn.close()


def ler_config(chave, default=None):
    conn = get_db()

    row = conn.execute(
        "SELECT valor FROM config WHERE chave = ?",
        (chave,),
    ).fetchone()

    conn.close()

    if row:
        return row["valor"]

    return default


def salvar_oauth(data):
    conn = get_db()

    conn.execute(
        """
        INSERT INTO oauth(
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id)
        DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at,
            user_id = excluded.user_id,
            nickname = excluded.nickname
        """,
        (
            data.get("access_token"),
            data.get("refresh_token"),
            data.get("expires_at"),
            data.get("user_id"),
            data.get("nickname"),
        ),
    )

    conn.commit()
    conn.close()


def carregar_oauth():
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM oauth WHERE id = 1"
    ).fetchone()

    conn.close()

    return row


def token_valido():
    row = carregar_oauth()

    if not row:
        return False

    access_token = row["access_token"]
    expires_at = row["expires_at"] or 0

    if not access_token:
        return False

    # Renova 2 minutos antes de expirar.
    return int(time.time()) < (expires_at - 120)


def gerar_code_verifier():
    return secrets.token_urlsafe(64)[:128]


def gerar_code_challenge(verifier):
    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")


def normalizar_numero(valor):
    if valor is None:
        return None

    try:
        return float(valor)
    except Exception:
        return None


def calcular_desconto(original, atual):
    original = normalizar_numero(original)
    atual = normalizar_numero(atual)

    if not original or not atual:
        return 0

    if original <= atual:
        return 0

    return round(
        ((original - atual) / original) * 100,
        2,
    )


def moeda(valor):
    valor = normalizar_numero(valor)

    if valor is None:
        return "-"

    return (
        "R$ "
        + f"{valor:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def limpar_html(texto):
    if not texto:
        return ""

    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()


def slugify(texto):
    texto = unicodedata.normalize(
        "NFKD",
        texto,
    ).encode(
        "ascii",
        "ignore",
    ).decode("ascii")

    texto = texto.lower()

    texto = re.sub(
        r"[^a-z0-9\s-]",
        "",
        texto,
    )

    texto = re.sub(
        r"\s+",
        "-",
        texto,
    )

    texto = re.sub(
        r"-+",
        "-",
        texto,
    )

    return texto.strip("-")


# ============================================================
# OAUTH MERCADO LIVRE
# ============================================================

def refresh_access_token():
    row = carregar_oauth()

    if not row:
        return False

    refresh_token = row["refresh_token"]

    if not refresh_token:
        return False

    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    try:
        response = requests.post(
            f"{ML_API}/oauth/token",
            data=payload,
            timeout=HTTP_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                "[REFRESH TOKEN]",
                response.status_code,
                response.text[:1000],
            )
            return False

        data = response.json()

        access_token = data.get("access_token")
        novo_refresh = data.get(
            "refresh_token",
            refresh_token,
        )

        expires_in = int(
            data.get("expires_in", 21600)
        )

        # Descobre user novamente.
        user_id = row["user_id"]
        nickname = row["nickname"]

        if access_token:
            try:
                me = requests.get(
                    f"{ML_API}/users/me",
                    headers={
                        "Authorization": f"Bearer {access_token}"
                    },
                    timeout=HTTP_TIMEOUT,
                )

                if me.status_code == 200:
                    user = me.json()
                    user_id = str(
                        user.get("id", user_id)
                    )
                    nickname = user.get(
                        "nickname",
                        nickname,
                    )
            except Exception:
                pass

        salvar_oauth(
            {
                "access_token": access_token,
                "refresh_token": novo_refresh,
                "expires_at": int(time.time()) + expires_in,
                "user_id": user_id,
                "nickname": nickname,
            }
        )

        print("[REFRESH TOKEN] OK")

        return True

    except Exception as e:
        print("[REFRESH TOKEN ERRO]", e)
        return False


def obter_access_token():
    row = carregar_oauth()

    if not row:
        return None

    if token_valido():
        return row["access_token"]

    if refresh_access_token():
        row = carregar_oauth()

        if row:
            return row["access_token"]

    return None


def ml_get(path, params=None):
    token = obter_access_token()

    if not token:
        return None, 401

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            f"{ML_API}{path}",
            headers=headers,
            params=params,
            timeout=HTTP_TIMEOUT,
        )

        # Token expirado.
        if response.status_code == 401:
            if refresh_access_token():
                token = obter_access_token()

                if token:
                    headers["Authorization"] = (
                        f"Bearer {token}"
                    )

                    response = requests.get(
                        f"{ML_API}{path}",
                        headers=headers,
                        params=params,
                        timeout=HTTP_TIMEOUT,
                    )

        try:
            data = response.json()
        except Exception:
            data = {
                "raw": response.text
            }

        return data, response.status_code

    except Exception as e:
        print("[ML GET ERRO]", path, e)

        return {
            "error": str(e)
        }, 500


# ============================================================
# DESCOBERTA DE ANÚNCIOS REAIS
# ============================================================

def extrair_item_ids_html(conteudo):
    """
    Procura IDs MLB reais dentro do HTML da busca pública.

    Não dependemos de uma única classe CSS.
    O Mercado Livre pode mudar o HTML, então usamos
    várias formas de encontrar MLB + números.
    """

    ids = []

    padroes = [
        r'"item_id"\s*:\s*"?(MLB\d+)',
        r'"id"\s*:\s*"(MLB\d+)"',
        r'"itemId"\s*:\s*"(MLB\d+)"',
        r'"item_id"\s*:\s*"(MLB\d+)"',
        r'data-id=["\'](MLB\d+)',
        r'/MLB-(\d+)',
        r'/MLB(\d+)',
        r'(MLB\d{6,12})',
    ]

    for padrao in padroes:
        encontrados = re.findall(
            padrao,
            conteudo,
            flags=re.IGNORECASE,
        )

        for item in encontrados:
            if not item:
                continue

            item = str(item).upper()

            if item.startswith("MLB-"):
                item = item.replace(
                    "MLB-",
                    "MLB",
                )

            if item.isdigit():
                item = "MLB" + item

            if re.fullmatch(
                r"MLB\d{6,12}",
                item,
            ):
                if item not in ids:
                    ids.append(item)

    return ids


def buscar_ids_publicos(consulta, pagina=1):
    """
    Busca no site público do Mercado Livre.

    Exemplo:
    celular
    celular barato
    air fryer
    fone bluetooth
    """

    consulta = consulta.strip()

    if not consulta:
        return [], {
            "erro": "Consulta vazia."
        }

    slug = slugify(consulta)

    if not slug:
        return [], {
            "erro": "Não foi possível montar a busca."
        }

    # Paginação do site.
    offset = max(0, pagina - 1) * 48

    url = (
        f"https://lista.mercadolivre.com.br/"
        f"{quote(slug)}"
    )

    if offset:
        url += f"_Desde_{offset + 1}"

    print("[BUSCA PÚBLICA]", url)

    try:
        response = requests.get(
            url,
            headers=PUBLIC_HEADERS,
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )

        print(
            "[BUSCA PÚBLICA STATUS]",
            response.status_code,
            len(response.text),
        )

        if response.status_code != 200:
            return [], {
                "erro": (
                    f"Mercado Livre retornou "
                    f"HTTP {response.status_code}"
                ),
                "status": response.status_code,
                "url": response.url,
            }

        ids = extrair_item_ids_html(
            response.text
        )

        return ids[:MAX_DISCOVERED_IDS], {
            "status": response.status_code,
            "url": response.url,
            "ids_encontrados": len(ids),
        }

    except Exception as e:
        return [], {
            "erro": str(e)
        }


# ============================================================
# DADOS DO ANÚNCIO
# ============================================================

def obter_item(item_id):
    data, status = ml_get(
        f"/items/{item_id}"
    )

    if status != 200:
        return None, status, data

    if not isinstance(data, dict):
        return None, status, data

    if not data.get("id"):
        return None, status, data

    return data, status, None


def obter_preco_venda(item_id):
    data, status = ml_get(
        f"/items/{item_id}/sale_price",
        params={
            "context": "channel_marketplace"
        },
    )

    if status != 200:
        return None

    if not isinstance(data, dict):
        return None

    amount = normalizar_numero(
        data.get("amount")
    )

    regular_amount = normalizar_numero(
        data.get("regular_amount")
    )

    if amount is None:
        return None

    return {
        "amount": amount,
        "regular_amount": regular_amount,
        "raw": data,
    }


def obter_precos(item_id):
    data, status = ml_get(
        f"/items/{item_id}/prices"
    )

    if status != 200:
        return []

    if not isinstance(data, dict):
        return []

    prices = data.get("prices")

    if not isinstance(prices, list):
        return []

    return prices


def descobrir_preco_e_desconto(item):
    item_id = item.get("id")

    if not item_id:
        return None

    atual = None
    original = None

    # --------------------------------------------------------
    # 1. Campo atual do item
    # --------------------------------------------------------

    atual = normalizar_numero(
        item.get("price")
    )

    original = normalizar_numero(
        item.get("original_price")
    )

    # --------------------------------------------------------
    # 2. Endpoint sale_price
    # --------------------------------------------------------

    sale = obter_preco_venda(
        item_id
    )

    if sale:
        if sale.get("amount") is not None:
            atual = sale["amount"]

        if sale.get("regular_amount") is not None:
            original = sale["regular_amount"]

    # --------------------------------------------------------
    # 3. Endpoint prices
    # --------------------------------------------------------

    prices = obter_precos(
        item_id
    )

    if prices:

        # Procuramos uma promoção ativa.
        promocoes = []

        for p in prices:
            if not isinstance(p, dict):
                continue

            amount = normalizar_numero(
                p.get("amount")
            )

            regular = normalizar_numero(
                p.get("regular_amount")
            )

            tipo = str(
                p.get("type", "")
            ).lower()

            if amount is None:
                continue

            if tipo == "promotion":
                promocoes.append(
                    {
                        "amount": amount,
                        "regular_amount": regular,
                    }
                )

        if promocoes:

            # Menor preço promocional.
            promocoes.sort(
                key=lambda x: x["amount"]
            )

            promo = promocoes[0]

            atual = promo["amount"]

            if promo.get("regular_amount"):
                original = promo[
                    "regular_amount"
                ]

        # Se ainda não existe original,
        # tentamos encontrar um standard.
        if original is None:

            standards = []

            for p in prices:
                if not isinstance(p, dict):
                    continue

                tipo = str(
                    p.get("type", "")
                ).lower()

                amount = normalizar_numero(
                    p.get("amount")
                )

                if (
                    tipo == "standard"
                    and amount is not None
                ):
                    standards.append(amount)

            if standards:
                standard = max(
                    standards
                )

                if atual is not None:
                    if standard > atual:
                        original = standard

    # --------------------------------------------------------
    # 4. Calcula desconto
    # --------------------------------------------------------

    if atual is None:
        return None

    desconto = calcular_desconto(
        original,
        atual,
    )

    return {
        "atual": atual,
        "original": original,
        "desconto": desconto,
        "precos": prices,
    }


# ============================================================
# SALVAR OFERTAS
# ============================================================

def salvar_oferta(oferta):
    conn = get_db()

    conn.execute(
        """
        INSERT INTO ofertas(
            item_id,
            titulo,
            preco_atual,
            preco_original,
            desconto,
            permalink,
            imagem,
            criado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id)
        DO UPDATE SET
            titulo = excluded.titulo,
            preco_atual = excluded.preco_atual,
            preco_original = excluded.preco_original,
            desconto = excluded.desconto,
            permalink = excluded.permalink,
            imagem = excluded.imagem,
            criado_em = excluded.criado_em
        """,
        (
            oferta["item_id"],
            oferta["titulo"],
            oferta["preco_atual"],
            oferta["preco_original"],
            oferta["desconto"],
            oferta["permalink"],
            oferta["imagem"],
            int(time.time()),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# GERAÇÃO DE TEXTO
# ============================================================

def gerar_texto_oferta(oferta):
    titulo = oferta["titulo"]
    atual = moeda(oferta["preco_atual"])
    original = moeda(oferta["preco_original"])
    desconto = oferta["desconto"]
    link = oferta["permalink"]

    return (
        f"🔥 OFERTA ENCONTRADA!\n\n"
        f"🛍️ {titulo}\n\n"
        f"❌ De: {original}\n"
        f"✅ Por: {atual}\n"
        f"📉 {desconto:.0f}% OFF\n\n"
        f"🛒 Comprar:\n"
        f"{link}\n"
    )


# ============================================================
# HTML
# ============================================================

HTML_BASE = """
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
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background:
        linear-gradient(
            180deg,
            #090909,
            #141414
        );

    color: #fff;
    min-height: 100vh;
}

.container {
    width: min(1100px, 94%);
    margin: auto;
    padding: 25px 0 50px;
}

h1 {
    margin-bottom: 5px;
}

.sub {
    color: #aaa;
    margin-top: 0;
}

.card {
    background: #1d1d1d;
    border: 1px solid #303030;
    border-radius: 16px;
    padding: 18px;
    margin-top: 18px;
}

.form-grid {
    display: grid;
    grid-template-columns:
        1fr
        160px
        160px;

    gap: 10px;
}

input,
select,
button {
    width: 100%;
    border: 0;
    border-radius: 10px;
    padding: 13px;
    font-size: 15px;
}

input,
select {
    background: #292929;
    color: #fff;
}

button {
    background: #00a650;
    color: #fff;
    font-weight: 700;
    cursor: pointer;
}

button:hover {
    opacity: .9;
}

.stats {
    display: grid;

    grid-template-columns:
        repeat(6, 1fr);

    gap: 10px;

    margin-top: 15px;
}

.stat {
    background: #222;
    border-radius: 12px;
    padding: 14px;
}

.stat strong {
    display: block;
    font-size: 23px;
}

.stat span {
    color: #999;
    font-size: 12px;
}

.offer {
    display: grid;

    grid-template-columns:
        160px 1fr;

    gap: 18px;

    background: #1c1c1c;

    border: 1px solid #303030;

    border-radius: 16px;

    padding: 15px;

    margin-top: 15px;
}

.offer img {
    width: 160px;
    height: 160px;
    object-fit: contain;
    background: #fff;
    border-radius: 10px;
}

.title {
    font-size: 18px;
    font-weight: 700;
}

.old {
    color: #999;
    text-decoration: line-through;
    margin-top: 12px;
}

.price {
    color: #00d66b;
    font-size: 27px;
    font-weight: 800;
}

.discount {
    display: inline-block;

    background: #00a650;

    padding: 5px 9px;

    border-radius: 8px;

    font-weight: 800;

    margin: 5px 0 10px;
}

.actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
}

.actions a,
.actions button {
    display: inline-block;
    width: auto;

    text-decoration: none;

    padding: 10px 13px;

    border-radius: 9px;

    background: #333;

    color: #fff;

    border: 0;

    cursor: pointer;
}

.actions a:hover,
.actions button:hover {
    background: #444;
}

.actions .green {
    background: #00a650;
}

.notice {
    background: #332800;
    border: 1px solid #6b5500;
    color: #ffe9a3;
    padding: 14px;
    border-radius: 12px;
    margin-top: 15px;
}

.error {
    background: #3a1111;
    border: 1px solid #712121;
    color: #ffb2b2;
    padding: 14px;
    border-radius: 12px;
    margin-top: 15px;
}

.success {
    background: #10351f;
    border: 1px solid #176d3a;
    color: #a7ffc9;
    padding: 14px;
    border-radius: 12px;
    margin-top: 15px;
}

.small {
    color: #999;
    font-size: 12px;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
}

@media(max-width: 800px) {

    .form-grid {
        grid-template-columns: 1fr;
    }

    .stats {
        grid-template-columns:
            repeat(2, 1fr);
    }

    .offer {
        grid-template-columns: 1fr;
    }

    .offer img {
        width: 100%;
        height: 220px;
    }

}

</style>

</head>

<body>

<div class="container">

<h1>🔥 Caçador de Ofertas</h1>

<p class="sub">
Mercado Livre • busca de anúncios reais • filtro de desconto
</p>

<div class="card">

<form action="/buscar" method="get">

<div class="form-grid">

<input
    type="text"
    name="q"
    placeholder="Ex.: celular, air fryer, fone bluetooth..."
    value="{{ consulta or '' }}"
    required
>

<select name="desconto">

<option value="5"
{% if desconto_min == 5 %}selected{% endif %}
>
5% ou mais
</option>

<option value="10"
{% if desconto_min == 10 %}selected{% endif %}
>
10% ou mais
</option>

<option value="15"
{% if desconto_min == 15 %}selected{% endif %}
>
15% ou mais
</option>

<option value="20"
{% if desconto_min == 20 %}selected{% endif %}
>
20% ou mais
</option>

<option value="30"
{% if desconto_min == 30 %}selected{% endif %}
>
30% ou mais
</option>

<option value="40"
{% if desconto_min == 40 %}selected{% endif %}
>
40% ou mais
</option>

<option value="50"
{% if desconto_min == 50 %}selected{% endif %}
>
50% ou mais
</option>

</select>

<button type="submit">
CAÇAR OFERTAS
</button>

</div>

</form>

</div>


{% if aviso %}

<div class="notice">
{{ aviso }}
</div>

{% endif %}


{% if erro %}

<div class="error">
{{ erro }}
</div>

{% endif %}


{% if stats %}

<div class="stats">

<div class="stat">
<strong>{{ stats.descobertos }}</strong>
<span>IDs descobertos</span>
</div>

<div class="stat">
<strong>{{ stats.anuncios }}</strong>
<span>Anúncios consultados</span>
</div>

<div class="stat">
<strong>{{ stats.ofertas }}</strong>
<span>Ofertas</span>
</div>

<div class="stat">
<strong>{{ stats.com_desconto }}</strong>
<span>Com desconto</span>
</div>

<div class="stat">
<strong>{{ stats.sem_original }}</strong>
<span>Sem preço original</span>
</div>

<div class="stat">
<strong>{{ stats.erros }}</strong>
<span>Erros</span>
</div>

</div>

{% endif %}


{% if ofertas %}

{% for oferta in ofertas %}

<div class="offer">

<img
    src="{{ oferta.imagem }}"
    alt=""
    loading="lazy"
>

<div>

<div class="title">
{{ oferta.titulo }}
</div>

<div class="old">
De: {{ oferta.preco_original_fmt }}
</div>

<div class="price">
Por: {{ oferta.preco_atual_fmt }}
</div>

<div class="discount">
{{ oferta.desconto_fmt }} OFF
</div>

<div class="small">
ID: {{ oferta.item_id }}
</div>

<br>

<div class="actions">

<a
    class="green"
    href="{{ oferta.permalink }}"
    target="_blank"
>
🛒 Abrir produto
</a>

<a
    href="{{ afiliado_url }}"
    target="_blank"
>
🔗 Gerar afiliado
</a>

<button
    onclick="copiarOferta({{ oferta.texto_js|tojson }})"
>
📋 Copiar oferta
</button>

</div>

</div>

</div>

{% endfor %}

{% elif buscou %}

<div class="card">

<h3>
Nenhuma oferta encontrada.
</h3>

<p class="small">
Foram encontrados anúncios reais, mas nenhum apresentou
preço original maior que o preço atual dentro do desconto mínimo.
</p>

</div>

{% endif %}


<div class="card">

<h3>🔐 Mercado Livre</h3>

{% if oauth %}

<div class="success">

Conectado como:
<strong>
{{ oauth.nickname or oauth.user_id }}
</strong>

<br>

User ID:
{{ oauth.user_id }}

</div>

<br>

<a
    href="/mercadolivre/status"
    style="color:#00d66b"
>
Ver status da conexão
</a>

{% else %}

<p>
Seu Mercado Livre ainda não está conectado.
</p>

<a
    href="/mercadolivre/login"
    style="
        display:inline-block;
        background:#00a650;
        color:white;
        padding:12px 15px;
        border-radius:9px;
        text-decoration:none;
        font-weight:700;
    "
>
🔐 Conectar Mercado Livre
</a>

{% endif %}

</div>


<p class="small">

A descoberta dos anúncios usa a página pública de resultados
do Mercado Livre; depois os IDs encontrados são conferidos
pela API do Mercado Livre.

O link de afiliado deve ser gerado pelo Gerador de Links
ou pela Barra de Afiliados do Mercado Livre.

</p>


</div>


<script>

function copiarOferta(texto) {

    navigator.clipboard.writeText(texto)
        .then(function() {

            alert(
                "Oferta copiada!"
            );

        })
        .catch(function() {

            alert(
                "Não foi possível copiar automaticamente."
            );

        });

}

</script>

</body>
</html>
"""


# ============================================================
# ROTAS
# ============================================================

@app.route("/")
def index():

    oauth = carregar_oauth()

    return render_template_string(
        HTML_BASE,
        oauth=oauth,
        consulta="",
        desconto_min=10,
        ofertas=[],
        stats=None,
        aviso=None,
        erro=None,
        buscou=False,
        afiliado_url=(
            "https://www.mercadolivre.com.br/"
            "afiliados"
        ),
    )


# ============================================================
# BUSCAR
# ============================================================

@app.route("/buscar")
def buscar():

    consulta = request.args.get(
        "q",
        ""
    ).strip()

    try:
        desconto_min = float(
            request.args.get(
                "desconto",
                "10"
            )
        )
    except Exception:
        desconto_min = 10

    ofertas = []

    stats = {
        "descobertos": 0,
        "anuncios": 0,
        "ofertas": 0,
        "com_desconto": 0,
        "sem_original": 0,
        "erros": 0,
    }

    aviso = None
    erro = None

    if not consulta:

        return render_template_string(
            HTML_BASE,
            oauth=carregar_oauth(),
            consulta="",
            desconto_min=desconto_min,
            ofertas=[],
            stats=None,
            aviso=None,
            erro="Digite o produto que deseja procurar.",
            buscou=True,
            afiliado_url=(
                "https://www.mercadolivre.com.br/"
                "afiliados"
            ),
        )

    # --------------------------------------------------------
    # 1. Descobre IDs reais
    # --------------------------------------------------------

    ids, info = buscar_ids_publicos(
        consulta
    )

    stats["descobertos"] = len(ids)

    if not ids:

        erro = (
            "Não consegui descobrir anúncios reais "
            "nessa busca. "
        )

        if info.get("erro"):
            erro += info["erro"]

        return render_template_string(
            HTML_BASE,
            oauth=carregar_oauth(),
            consulta=consulta,
            desconto_min=desconto_min,
            ofertas=[],
            stats=stats,
            aviso=None,
            erro=erro,
            buscou=True,
            afiliado_url=(
                "https://www.mercadolivre.com.br/"
                "afiliados"
            ),
        )

    # --------------------------------------------------------
    # 2. Consulta anúncios pela API oficial
    # --------------------------------------------------------

    for item_id in ids[:MAX_ITEMS_TO_CHECK]:

        stats["anuncios"] += 1

        item, status, raw_error = obter_item(
            item_id
        )

        if not item:

            stats["erros"] += 1

            continue

        titulo = (
            item.get("title")
            or "Produto Mercado Livre"
        )

        permalink = (
            item.get("permalink")
            or f"https://www.mercadolivre.com.br/"
               f"{item_id}"
        )

        imagens = item.get(
            "pictures",
            []
        )

        imagem = ""

        if isinstance(imagens, list) and imagens:

            primeira = imagens[0]

            if isinstance(primeira, dict):

                imagem = (
                    primeira.get("secure_url")
                    or primeira.get("url")
                    or ""
                )

        dados_preco = (
            descobrir_preco_e_desconto(
                item
            )
        )

        if not dados_preco:

            stats["erros"] += 1

            continue

        atual = dados_preco.get(
            "atual"
        )

        original = dados_preco.get(
            "original"
        )

        desconto = dados_preco.get(
            "desconto",
            0
        )

        if original is None:

            stats["sem_original"] += 1

            continue

        stats["com_desconto"] += 1

        if desconto < desconto_min:

            continue

        oferta = {
            "item_id": item_id,
            "titulo": titulo,
            "preco_atual": atual,
            "preco_original": original,
            "desconto": desconto,
            "permalink": permalink,
            "imagem": imagem,
        }

        oferta["preco_atual_fmt"] = moeda(
            atual
        )

        oferta["preco_original_fmt"] = moeda(
            original
        )

        oferta["desconto_fmt"] = (
            f"{desconto:.0f}%"
        )

        oferta["texto"] = gerar_texto_oferta(
            oferta
        )

        oferta["texto_js"] = oferta["texto"]

        ofertas.append(
            oferta
        )

        salvar_oferta(
            oferta
        )

        stats["ofertas"] += 1

    # --------------------------------------------------------
    # Ordena do maior desconto para menor
    # --------------------------------------------------------

    ofertas.sort(
        key=lambda x: x["desconto"],
        reverse=True,
    )

    if stats["ofertas"]:

        aviso = (
            f"Encontradas "
            f"{stats['ofertas']} oferta(s) "
            f"com {desconto_min:.0f}% ou mais de desconto."
        )

    elif stats["com_desconto"]:

        aviso = (
            "Foram encontrados anúncios com "
            "preço atual e preço original, "
            "mas nenhum atingiu o desconto mínimo."
        )

    else:

        aviso = (
            "Os anúncios foram encontrados, "
            "mas a API não disponibilizou preço "
            "original suficiente para confirmar desconto."
        )

    return render_template_string(
        HTML_BASE,
        oauth=carregar_oauth(),
        consulta=consulta,
        desconto_min=desconto_min,
        ofertas=ofertas,
        stats=stats,
        aviso=aviso,
        erro=erro,
        buscou=True,
        afiliado_url=(
            "https://www.mercadolivre.com.br/"
            "afiliados"
        ),
    )


# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not CLIENT_ID:

        return (
            "ERRO: ML_CLIENT_ID não configurado.",
            500,
        )

    state = secrets.token_urlsafe(32)

    verifier = gerar_code_verifier()
    challenge = gerar_code_challenge(
        verifier
    )

    salvar_config(
        "oauth_state",
        state
    )

    salvar_config(
        "oauth_code_verifier",
        verifier
    )

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    url = (
        f"{ML_AUTH}/authorization?"
        + urlencode(params)
    )

    return redirect(url)


# ============================================================
# CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return f"""
        <h2>Erro no Mercado Livre</h2>
        <pre>{html.escape(error)}</pre>
        """

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    saved_state = ler_config(
        "oauth_state"
    )

    verifier = ler_config(
        "oauth_code_verifier"
    )

    if not code:

        return (
            "Código de autorização não recebido.",
            400,
        )

    if not state or state != saved_state:

        return (
            "STATE inválido.",
            400,
        )

    if not verifier:

        return (
            "Code verifier não encontrado.",
            400,
        )

    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }

    try:

        response = requests.post(
            f"{ML_API}/oauth/token",
            data=payload,
            timeout=HTTP_TIMEOUT,
        )

        if response.status_code != 200:

            return f"""
            <h2>Erro ao obter token</h2>
            <pre>
HTTP {response.status_code}

{html.escape(response.text)}
            </pre>
            """, 500

        data = response.json()

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

        if not access_token:

            return (
                "Mercado Livre não retornou access_token.",
                500,
            )

        # ----------------------------------------------------
        # Busca dados da conta
        # ----------------------------------------------------

        me = requests.get(
            f"{ML_API}/users/me",
            headers={
                "Authorization":
                    f"Bearer {access_token}"
            },
            timeout=HTTP_TIMEOUT,
        )

        user_id = None
        nickname = None

        if me.status_code == 200:

            user = me.json()

            user_id = str(
                user.get("id")
            )

            nickname = user.get(
                "nickname"
            )

        salvar_oauth(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at":
                    int(time.time())
                    + expires_in,
                "user_id": user_id,
                "nickname": nickname,
            }
        )

        return redirect("/")

    except Exception as e:

        return f"""
        <h2>Erro no callback</h2>
        <pre>{html.escape(str(e))}</pre>
        """, 500


# ============================================================
# STATUS
# ============================================================

@app.route("/mercadolivre/status")
def mercadolivre_status():

    row = carregar_oauth()

    if not row:

        return jsonify(
            {
                "conectado": False,
                "mensagem":
                    "Nenhum OAuth salvo."
            }
        )

    token = obter_access_token()

    if not token:

        return jsonify(
            {
                "conectado": False,
                "mensagem":
                    "Token ausente ou expirado.",
                "user_id":
                    row["user_id"],
                "nickname":
                    row["nickname"],
            }
        )

    me, status = ml_get(
        "/users/me"
    )

    return jsonify(
        {
            "conectado": status == 200,
            "http": status,
            "user_id":
                row["user_id"],
            "nickname":
                row["nickname"],
            "me":
                me,
        }
    )


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def mercadolivre_diagnostico():

    resultado = {
        "client_id_configurado":
            bool(CLIENT_ID),

        "client_secret_configurado":
            bool(CLIENT_SECRET),

        "redirect_uri":
            REDIRECT_URI,

        "oauth_salvo":
            bool(carregar_oauth()),

        "token_valido":
            token_valido(),

        "endpoints": {},
    }

    # users/me
    me, status_me = ml_get(
        "/users/me"
    )

    resultado["endpoints"]["users_me"] = {
        "http": status_me,
        "resultado": me,
    }

    # products/search
    produtos, status_produtos = ml_get(
        "/products/search",
        params={
            "status": "active",
            "site_id": "MLB",
            "q": "celular",
            "limit": 3,
        },
    )

    resultado["endpoints"][
        "products_search"
    ] = {
        "http": status_produtos,
        "resultado": produtos,
    }

    return jsonify(
        resultado
    )


# ============================================================
# TESTE DE BUSCA PÚBLICA
# ============================================================

@app.route("/mercadolivre/teste-busca")
def teste_busca():

    consulta = request.args.get(
        "q",
        "celular"
    )

    ids, info = buscar_ids_publicos(
        consulta
    )

    resultado = {
        "consulta": consulta,
        "info": info,
        "ids": ids,
        "total": len(ids),
    }

    # Testa os primeiros 5 IDs na API.
    testes = []

    for item_id in ids[:5]:

        item, status, erro = obter_item(
            item_id
        )

        if item:

            dados_preco = (
                descobrir_preco_e_desconto(
                    item
                )
            )

            testes.append(
                {
                    "item_id": item_id,
                    "http": status,
                    "titulo":
                        item.get("title"),
                    "price":
                        item.get("price"),
                    "original_price":
                        item.get(
                            "original_price"
                        ),
                    "precos":
                        dados_preco,
                    "permalink":
                        item.get(
                            "permalink"
                        ),
                }
            )

        else:

            testes.append(
                {
                    "item_id": item_id,
                    "http": status,
                    "erro": erro,
                }
            )

    resultado["testes_api"] = testes

    return jsonify(
        resultado
    )


# ============================================================
# TESTE DE PREÇO
# ============================================================

@app.route("/mercadolivre/teste-preco")
def teste_preco():

    item_id = request.args.get(
        "item_id",
        ""
    ).strip().upper()

    if not item_id:

        return jsonify(
            {
                "erro":
                    "Informe ?item_id=MLB123456789"
            }
        ), 400

    item, status, erro = obter_item(
        item_id
    )

    if not item:

        return jsonify(
            {
                "item_id":
                    item_id,
                "http":
                    status,
                "erro":
                    erro,
            }
        ), 400

    sale = obter_preco_venda(
        item_id
    )

    prices = obter_precos(
        item_id
    )

    return jsonify(
        {
            "item": {
                "id":
                    item.get("id"),
                "title":
                    item.get("title"),
                "price":
                    item.get("price"),
                "original_price":
                    item.get(
                        "original_price"
                    ),
                "permalink":
                    item.get(
                        "permalink"
                    ),
            },
            "sale_price":
                sale,
            "prices":
                prices,
            "desconto":
                calcular_desconto(
                    item.get(
                        "original_price"
                    ),
                    item.get(
                        "price"
                    ),
                ),
        }
    )


# ============================================================
# HISTÓRICO
# ============================================================

@app.route("/ofertas")
def listar_ofertas():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM ofertas
        ORDER BY desconto DESC, criado_em DESC
        LIMIT 200
        """
    ).fetchall()

    conn.close()

    resultado = []

    for row in rows:

        resultado.append(
            dict(row)
        )

    return jsonify(
        resultado
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify(
        {
            "status": "ok",
            "app":
                "cacador-de-ofertas",
            "mercadolivre":
                bool(carregar_oauth()),
        }
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
        port=port,
        debug=False,
    )