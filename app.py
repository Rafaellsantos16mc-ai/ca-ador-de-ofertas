import os
import re
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

import requests
from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    jsonify,
)

# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "troque-essa-chave")

DB_PATH = os.getenv("DB_PATH", "promocoes.db")

ML_API = "https://api.mercadolibre.com"

SITE_ID = "MLB"

DEFAULT_LIMIT = int(os.getenv("DEFAULT_LIMIT", "20"))
MAX_LIMIT = int(os.getenv("MAX_LIMIT", "50"))

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))


# ============================================================
# BANCO
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
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

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILIDADES
# ============================================================

def money(value):
    try:
        value = float(value)
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def calcular_desconto(preco, preco_antigo):
    try:
        preco = float(preco)
        preco_antigo = float(preco_antigo)

        if preco_antigo > preco and preco_antigo > 0:
            return round(((preco_antigo - preco) / preco_antigo) * 100, 1)

    except Exception:
        pass

    return 0


def limpar_titulo(titulo):
    if not titulo:
        return ""

    titulo = re.sub(r"\s+", " ", titulo).strip()

    if len(titulo) > 110:
        titulo = titulo[:107] + "..."

    return titulo


def validar_url(url):
    if not url:
        return False

    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:
        return False


def normalizar_item_id(valor):
    if not valor:
        return ""

    valor = valor.strip()

    # Exemplo:
    # MLB123456789
    match = re.search(r"(MLB\d+)", valor.upper())

    if match:
        return match.group(1)

    return valor.upper()


# ============================================================
# MERCADO LIVRE
# ============================================================

def ml_get(endpoint, params=None, token=None):
    headers = {
        "Accept": "application/json",
        "User-Agent": "PromocoesML/1.0"
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = ML_API + endpoint

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


def buscar_produtos(query, limit=20):

    limit = max(1, min(int(limit), MAX_LIMIT))

    data = ml_get(
        f"/sites/{SITE_ID}/search",
        params={
            "q": query,
            "limit": limit
        }
    )

    resultados = []

    for item in data.get("results", []):

        item_id = item.get("id")

        if not item_id:
            continue

        titulo = item.get("title") or "Produto"

        preco = item.get("price")

        preco_antigo = item.get("original_price")

        imagem = (
            item.get("thumbnail")
            or ""
        )

        permalink = item.get("permalink") or ""

        desconto = calcular_desconto(
            preco,
            preco_antigo
        )

        resultados.append({
            "id": item_id,
            "titulo": limpar_titulo(titulo),
            "preco": preco,
            "preco_formatado": money(preco),
            "preco_antigo": preco_antigo,
            "preco_antigo_formatado": (
                money(preco_antigo)
                if preco_antigo
                else ""
            ),
            "desconto": desconto,
            "imagem": imagem,
            "permalink": permalink,
        })

    return resultados


def buscar_item(item_id):

    item_id = normalizar_item_id(item_id)

    if not item_id:
        return None

    data = ml_get(
        f"/items/{item_id}"
    )

    preco = data.get("price")

    preco_antigo = data.get("original_price")

    desconto = calcular_desconto(
        preco,
        preco_antigo
    )

    imagem = ""

    pictures = data.get("pictures") or []

    if pictures:
        imagem = pictures[0].get("secure_url") or pictures[0].get("url") or ""

    if not imagem:
        imagem = data.get("thumbnail") or ""

    return {
        "id": data.get("id"),
        "titulo": limpar_titulo(data.get("title")),
        "preco": preco,
        "preco_formatado": money(preco),
        "preco_antigo": preco_antigo,
        "preco_antigo_formatado": (
            money(preco_antigo)
            if preco_antigo
            else ""
        ),
        "desconto": desconto,
        "imagem": imagem,
        "permalink": data.get("permalink") or "",
        "status": data.get("status"),
        "condition": data.get("condition"),
        "available_quantity": data.get("available_quantity"),
        "sold_quantity": data.get("sold_quantity"),
    }


# ============================================================
# TEXTO DA OFERTA
# ============================================================

def gerar_texto_oferta(
    titulo,
    preco,
    preco_antigo=None,
    desconto=0,
    link=""
):

    linhas = []

    linhas.append("🔥 OFERTA DO DIA 🔥")
    linhas.append("")
    linhas.append(f"🛒 {titulo}")
    linhas.append("")

    if preco_antigo and desconto > 0:

        linhas.append(
            f"❌ De: {money(preco_antigo)}"
        )

        linhas.append(
            f"🔥 Por: {money(preco)}"
        )

        linhas.append(
            f"🏷️ {desconto}% OFF"
        )

    else:

        linhas.append(
            f"💰 Por apenas: {money(preco)}"
        )

    linhas.append("")

    if link:
        linhas.append("👉 COMPRAR AQUI:")
        linhas.append(link)

    linhas.append("")
    linhas.append("⚠️ Preço e disponibilidade podem mudar sem aviso.")

    return "\n".join(linhas)


# ============================================================
# HTML
# ============================================================

HTML = """
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Promoções Mercado Livre</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f3f4f6;
    font-family: Arial, Helvetica, sans-serif;
    color: #222;
}

header {
    background: #ffe600;
    padding: 18px;
    text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,.12);
}

header h1 {
    margin: 0;
    font-size: 24px;
}

header p {
    margin: 7px 0 0;
    font-size: 14px;
}

.container {
    max-width: 1100px;
    margin: auto;
    padding: 18px;
}

.box {
    background: white;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 18px;
    box-shadow: 0 2px 10px rgba(0,0,0,.07);
}

.search {
    display: flex;
    gap: 10px;
}

.search input {
    flex: 1;
}

input,
textarea,
button {
    font-size: 16px;
}

input,
textarea {
    width: 100%;
    border: 1px solid #ddd;
    border-radius: 9px;
    padding: 12px;
}

button {
    border: 0;
    border-radius: 9px;
    padding: 12px 18px;
    cursor: pointer;
    font-weight: bold;
}

.btn {
    background: #3483fa;
    color: white;
}

.btn-green {
    background: #00a650;
    color: white;
}

.btn-yellow {
    background: #ffe600;
    color: #222;
}

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px;
}

.card {
    background: white;
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 0 2px 10px rgba(0,0,0,.08);
    display: flex;
    flex-direction: column;
}

.card img {
    width: 100%;
    height: 230px;
    object-fit: contain;
    background: white;
}

.card-body {
    padding: 15px;
}

.title {
    font-weight: bold;
    line-height: 1.35;
    min-height: 58px;
}

.price-old {
    color: #777;
    text-decoration: line-through;
    margin-top: 10px;
}

.price {
    font-size: 25px;
    font-weight: bold;
    color: #00a650;
    margin-top: 3px;
}

.discount {
    display: inline-block;
    background: #e6f7ed;
    color: #008c45;
    padding: 5px 8px;
    border-radius: 6px;
    font-weight: bold;
    margin-top: 7px;
}

.card-actions {
    display: flex;
    gap: 8px;
    margin-top: 12px;
}

.card-actions a,
.card-actions button {
    flex: 1;
    text-align: center;
    text-decoration: none;
}

.form-group {
    margin-top: 12px;
}

label {
    display: block;
    font-weight: bold;
    margin-bottom: 6px;
}

textarea {
    min-height: 150px;
    resize: vertical;
}

.resultado {
    margin-bottom: 15px;
    font-weight: bold;
}

.alert {
    background: #fff3cd;
    padding: 12px;
    border-radius: 9px;
    margin-bottom: 15px;
}

.success {
    background: #d1e7dd;
    padding: 12px;
    border-radius: 9px;
    margin-bottom: 15px;
}

.debug-link {
    color: #555;
    font-size: 13px;
}

@media(max-width:600px) {

    .search {
        flex-direction: column;
    }

    .card img {
        height: 210px;
    }

}

</style>

</head>

<body>

<header>

<h1>🛒 Promoções Mercado Livre</h1>

<p>Encontre produtos e prepare suas ofertas para divulgação</p>

</header>


<div class="container">


<div class="box">

<h2>🔎 Buscar produto</h2>

<form method="GET" action="/buscar">

<div class="search">

<input
    type="text"
    name="q"
    placeholder="Ex.: fone bluetooth, air fryer, celular..."
    value="{{ query }}"
    required
>

<button class="btn">
Buscar
</button>

</div>

</form>

</div>


{% if message %}

<div class="alert">
{{ message }}
</div>

{% endif %}


{% if resultados %}

<div class="box">

<div class="resultado">
{{ resultados|length }} produto(s) encontrado(s)
</div>

<div class="grid">

{% for p in resultados %}

<div class="card">

{% if p.imagem %}

<img
    src="{{ p.imagem }}"
    alt="{{ p.titulo }}"
    loading="lazy"
>

{% endif %}

<div class="card-body">

<div class="title">
{{ p.titulo }}
</div>


{% if p.preco_antigo and p.desconto > 0 %}

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


<div class="card-actions">

<a
    class="btn"
    href="{{ p.permalink }}"
    target="_blank"
>
Ver produto
</a>

<a
    class="btn-green"
    href="/gerar?item_id={{ p.id }}"
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

<h2>🔥 Criar oferta</h2>

<form method="POST" action="/gerar">

<input
    type="hidden"
    name="item_id"
    value="{{ produto.id }}"
>


<div class="grid">

<div>

{% if produto.imagem %}

<img
    src="{{ produto.imagem }}"
    style="
        width:100%;
        max-height:320px;
        object-fit:contain;
        border-radius:10px;
    "
>

{% endif %}

</div>


<div>

<h3>
{{ produto.titulo }}
</h3>

{% if produto.preco_antigo and produto.desconto > 0 %}

<p>
De:
<strong style="text-decoration:line-through;">
{{ produto.preco_antigo_formatado }}
</strong>
</p>

{% endif %}

<p>
Preço:
<strong style="font-size:25px;color:#00a650;">
{{ produto.preco_formatado }}
</strong>
</p>

{% if produto.desconto > 0 %}

<p>
🏷️ <strong>{{ produto.desconto }}% OFF</strong>
</p>

{% endif %}

</div>

</div>


<div class="form-group">

<label>
🔗 Seu link de afiliado
</label>

<input
    type="url"
    name="link_afiliado"
    placeholder="Cole aqui o seu link de afiliado do Mercado Livre"
>

</div>


<div class="form-group">

<label>
📝 Texto da oferta
</label>

<textarea
    name="texto"
>{{ texto or "" }}</textarea>

</div>


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

<h2>📦 Ofertas salvas</h2>

{% if ofertas %}

<div class="grid">

{% for oferta in ofertas %}

<div class="card">

{% if oferta.imagem %}

<img
    src="{{ oferta.imagem }}"
    loading="lazy"
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

<div class="form-group">

<textarea
    id="texto{{ oferta.id }}"
    readonly
>{{ oferta.texto }}</textarea>

</div>

<div class="card-actions">

<button
    class="btn-yellow"
    onclick="copiarTexto('texto{{ oferta.id }}')"
>
📋 Copiar
</button>

{% if oferta.link_afiliado %}

<a
    class="btn-green"
    href="{{ oferta.link_afiliado }}"
    target="_blank"
>
Abrir link
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


<div style="text-align:center;margin:25px 0;color:#777;">

<a
    class="debug-link"
    href="/health"
>
Status do sistema
</a>

&nbsp; | &nbsp;

<a
    class="debug-link"
    href="/mercadolivre/status"
>
Status Mercado Livre
</a>

</div>


</div>


<script>

function copiarTexto(id) {

    const elemento = document.getElementById(id);

    navigator.clipboard.writeText(elemento.value)
        .then(() => {

            alert("Oferta copiada!");

        })
        .catch(() => {

            elemento.select();

            document.execCommand("copy");

            alert("Oferta copiada!");

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

    conn = get_db()

    ofertas = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    return render_template_string(
        HTML,
        resultados=[],
        query="",
        produto=None,
        texto="",
        ofertas=ofertas,
        money=money,
        message=""
    )


# ============================================================
# BUSCAR
# ============================================================

@app.route("/buscar")
def buscar():

    query = request.args.get("q", "").strip()

    if not query:
        return redirect(url_for("index"))

    try:

        resultados = buscar_produtos(
            query,
            DEFAULT_LIMIT
        )

        if not resultados:

            message = (
                "Nenhum produto encontrado. "
                "Tente uma busca diferente."
            )

        else:

            message = ""

    except requests.HTTPError as e:

        resultados = []

        message = (
            "Erro na API do Mercado Livre: "
            f"{e.response.status_code if e.response else 'desconhecido'}"
        )

    except Exception as e:

        resultados = []

        message = (
            "Erro ao buscar produtos: "
            + str(e)
        )

    conn = get_db()

    ofertas = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    return render_template_string(
        HTML,
        resultados=resultados,
        query=query,
        produto=None,
        texto="",
        ofertas=ofertas,
        money=money,
        message=message
    )


# ============================================================
# ABRIR PRODUTO
# ============================================================

@app.route("/gerar", methods=["GET"])
def abrir_gerar():

    item_id = request.args.get("item_id", "").strip()

    if not item_id:
        return redirect(url_for("index"))

    try:

        produto = buscar_item(item_id)

        if not produto:
            return redirect(url_for("index"))

        texto = gerar_texto_oferta(
            produto["titulo"],
            produto["preco"],
            produto["preco_antigo"],
            produto["desconto"],
            ""
        )

    except Exception as e:

        return render_template_string(
            HTML,
            resultados=[],
            query="",
            produto=None,
            texto="",
            ofertas=[],
            money=money,
            message=f"Erro ao abrir produto: {e}"
        )

    conn = get_db()

    ofertas = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    return render_template_string(
        HTML,
        resultados=[],
        query="",
        produto=produto,
        texto=texto,
        ofertas=ofertas,
        money=money,
        message=""
    )


# ============================================================
# SALVAR OFERTA
# ============================================================

@app.route("/gerar", methods=["POST"])
def salvar_oferta():

    item_id = normalizar_item_id(
        request.form.get("item_id", "")
    )

    link_afiliado = (
        request.form.get("link_afiliado", "")
        .strip()
    )

    texto = (
        request.form.get("texto", "")
        .strip()
    )

    if not item_id:

        return redirect(url_for("index"))

    try:

        produto = buscar_item(item_id)

        if not produto:

            raise Exception(
                "Produto não encontrado."
            )

        # Se o usuário não digitou o texto,
        # o sistema gera automaticamente.
        if not texto:

            texto = gerar_texto_oferta(
                produto["titulo"],
                produto["preco"],
                produto["preco_antigo"],
                produto["desconto"],
                link_afiliado
            )

        else:

            # Se existe link de afiliado e o texto ainda
            # não contém esse link, acrescentamos.
            if (
                link_afiliado
                and link_afiliado not in texto
            ):

                texto += (
                    "\n\n👉 COMPRAR AQUI:\n"
                    + link_afiliado
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
            link_afiliado,
            texto,
            datetime.now().isoformat()
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("index"))

    except Exception as e:

        conn = get_db()

        ofertas = conn.execute("""
            SELECT *
            FROM ofertas
            ORDER BY id DESC
            LIMIT 50
        """).fetchall()

        conn.close()

        return render_template_string(
            HTML,
            resultados=[],
            query="",
            produto=None,
            texto="",
            ofertas=ofertas,
            money=money,
            message=f"Erro ao salvar oferta: {e}"
        )


# ============================================================
# API JSON — BUSCA
# ============================================================

@app.route("/api/buscar")
def api_buscar():

    query = request.args.get("q", "").strip()

    if not query:

        return jsonify({
            "erro": "Informe q"
        }), 400

    try:

        produtos = buscar_produtos(
            query,
            DEFAULT_LIMIT
        )

        return jsonify({
            "erro": None,
            "query": query,
            "total": len(produtos),
            "produtos": produtos
        })

    except Exception as e:

        return jsonify({
            "erro": str(e)
        }), 500


# ============================================================
# API JSON — ITEM
# ============================================================

@app.route("/api/item/<item_id>")
def api_item(item_id):

    try:

        produto = buscar_item(item_id)

        if not produto:

            return jsonify({
                "erro": "Produto não encontrado"
            }), 404

        return jsonify({
            "erro": None,
            "produto": produto
        })

    except Exception as e:

        return jsonify({
            "erro": str(e)
        }), 500


# ============================================================
# MERCADO LIVRE STATUS
# ============================================================

@app.route("/mercadolivre/status")
def mercadolivre_status():

    try:

        data = ml_get(
            f"/sites/{SITE_ID}"
        )

        return jsonify({
            "ok": True,
            "site": SITE_ID,
            "nome": data.get("name"),
            "country": data.get("country_id"),
            "status": 200
        })

    except Exception as e:

        return jsonify({
            "ok": False,
            "site": SITE_ID,
            "erro": str(e)
        }), 500


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    resultado = {
        "api": ML_API,
        "site": SITE_ID,
        "busca_publica": False,
        "erro": None
    }

    try:

        data = ml_get(
            f"/sites/{SITE_ID}/search",
            params={
                "q": "fone bluetooth",
                "limit": 1
            }
        )

        resultado["busca_publica"] = True
        resultado["total"] = data.get(
            "paging",
            {}
        ).get(
            "total"
        )

    except Exception as e:

        resultado["erro"] = str(e)

    return jsonify(resultado)


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "app": "Promocoes Mercado Livre",
        "status": "online"
    })


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "8080")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )