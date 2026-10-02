import os
import sqlite3
import requests
import html
from datetime import datetime
from urllib.parse import quote

from flask import Flask, request, render_template_string, redirect, url_for, flash


# ============================================================
# APP
# ============================================================

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "troque-esta-chave")

DB_FILE = os.getenv("DB_FILE", "ofertas.db")

SITE_ID = "MLB"
API_URL = "https://api.mercadolibre.com/sites/MLB/search"


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
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT UNIQUE,
            titulo TEXT,
            preco REAL,
            preco_original REAL,
            desconto REAL,
            url TEXT,
            link_afiliado TEXT,
            imagem TEXT,
            criado_em TEXT
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# FORMATAÇÃO
# ============================================================

def moeda(valor):
    if valor is None:
        return "R$ 0,00"

    return (
        "R$ "
        + f"{float(valor):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def limpar_titulo(titulo):
    if not titulo:
        return ""

    titulo = html.unescape(titulo)
    return " ".join(titulo.split())


def calcular_desconto(preco, original):
    if not original or not preco:
        return 0

    if original <= preco:
        return 0

    return round(((original - preco) / original) * 100, 1)


# ============================================================
# BUSCAR PRODUTOS
# ============================================================

def buscar_produtos(query, limite=20):

    params = {
        "q": query,
        "limit": limite,
        "offset": 0
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            timeout=20,
            headers={
                "User-Agent": "OfertasWhatsApp/1.0"
            }
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:
        print("[ERRO API]", e)
        return []

    produtos = []

    for item in data.get("results", []):

        preco = item.get("price")
        preco_original = item.get("original_price")

        desconto = calcular_desconto(
            preco,
            preco_original
        )

        thumbnail = item.get("thumbnail")

        if thumbnail:
            thumbnail = thumbnail.replace("-I.jpg", "-O.jpg")

        produtos.append({
            "id": item.get("id"),
            "titulo": limpar_titulo(item.get("title")),
            "preco": preco,
            "preco_original": preco_original,
            "desconto": desconto,
            "url": item.get("permalink"),
            "imagem": thumbnail
        })

    return produtos


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(produto, link_afiliado):

    conn = get_db()

    try:

        conn.execute("""
            INSERT OR REPLACE INTO ofertas (
                item_id,
                titulo,
                preco,
                preco_original,
                desconto,
                url,
                link_afiliado,
                imagem,
                criado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            produto["id"],
            produto["titulo"],
            produto["preco"],
            produto["preco_original"],
            produto["desconto"],
            produto["url"],
            link_afiliado,
            produto["imagem"],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# GERAR TEXTO
# ============================================================

def gerar_mensagem(
    produto,
    link_afiliado,
    incluir_original=True
):

    titulo = produto["titulo"]
    preco = moeda(produto["preco"])
    original = moeda(produto["preco_original"])
    desconto = produto["desconto"]

    texto = "🔥 OFERTA ENCONTRADA!\n\n"

    texto += f"🛍️ {titulo}\n\n"

    if incluir_original and produto["preco_original"]:
        texto += f"❌ De: {original}\n"

    texto += f"🔥 Por: {preco}\n"

    if desconto > 0:
        texto += f"💰 Desconto: {desconto:.0f}%\n"

    texto += "\n⚡ Aproveite enquanto estiver disponível!\n\n"

    texto += "👉 PEGAR OFERTA:\n"
    texto += link_afiliado

    texto += "\n\n📢 Publicidade / link de afiliado."

    return texto


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

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f4f5f7;
    font-family: Arial, sans-serif;
    color: #222;
}

header {
    background: #ffe600;
    padding: 20px;
    text-align: center;
    font-weight: bold;
}

.container {
    max-width: 1000px;
    margin: auto;
    padding: 20px;
}

.card {
    background: white;
    border-radius: 14px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 3px 15px rgba(0,0,0,.08);
}

input,
select,
button,
textarea {
    width: 100%;
    padding: 13px;
    border-radius: 8px;
    border: 1px solid #ccc;
    font-size: 15px;
    margin-top: 7px;
    margin-bottom: 12px;
}

button {
    background: #3483fa;
    color: white;
    border: none;
    cursor: pointer;
    font-weight: bold;
}

button:hover {
    opacity: .9;
}

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 18px;
}

.produto {
    border: 1px solid #ddd;
    border-radius: 12px;
    padding: 15px;
    background: white;
}

.produto img {
    width: 100%;
    height: 220px;
    object-fit: contain;
    background: #fafafa;
    border-radius: 10px;
}

.titulo {
    font-weight: bold;
    margin-top: 12px;
    line-height: 1.4;
}

.preco {
    font-size: 24px;
    font-weight: bold;
    margin-top: 8px;
}

.original {
    color: #777;
    text-decoration: line-through;
}

.desconto {
    display: inline-block;
    background: #00a650;
    color: white;
    padding: 5px 8px;
    border-radius: 6px;
    margin-top: 7px;
    font-weight: bold;
}

textarea {
    min-height: 180px;
    resize: vertical;
}

a {
    color: #3483fa;
}

.alert {
    padding: 12px;
    border-radius: 8px;
    background: #fff3cd;
    margin-bottom: 15px;
}

.pequeno {
    color: #666;
    font-size: 13px;
}

</style>

</head>

<body>

<header>
    🛒 CAÇADOR DE OFERTAS
</header>

<div class="container">

<div class="card">

<h2>🔎 Procurar ofertas</h2>

<form method="POST" action="/buscar">

<label>Produto ou categoria</label>

<input
    name="query"
    placeholder="Ex: air fryer, celular, ferramenta..."
    value="{{ query or '' }}"
    required
>

<label>Desconto mínimo</label>

<input
    type="number"
    name="desconto_minimo"
    min="0"
    max="100"
    value="{{ desconto_minimo or 10 }}"
>

<button type="submit">
    🔎 BUSCAR OFERTAS
</button>

</form>

</div>


{% if produtos %}

<div class="card">

<h2>🔥 Resultados</h2>

<p class="pequeno">
Os produtos abaixo foram encontrados na busca do Mercado Livre.
O link exibido originalmente é o link do produto.
Para receber comissão, use o seu link de afiliado gerado no Mercado Livre.
</p>

</div>


<div class="grid">

{% for p in produtos %}

<div class="produto">

{% if p.imagem %}
<img src="{{ p.imagem }}">
{% endif %}

<div class="titulo">
{{ p.titulo }}
</div>

{% if p.preco_original and p.preco_original > p.preco %}

<div class="original">
{{ moeda(p.preco_original) }}
</div>

{% endif %}

<div class="preco">
{{ moeda(p.preco) }}
</div>

{% if p.desconto > 0 %}

<div class="desconto">
-{{ "%.0f"|format(p.desconto) }}%
</div>

{% endif %}


<form method="POST" action="/gerar">

<input
    type="hidden"
    name="item_id"
    value="{{ p.id }}"
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
    name="url"
    value="{{ p.url }}"
>

<input
    type="hidden"
    name="imagem"
    value="{{ p.imagem or '' }}"
>

<label>
Seu link de afiliado:
</label>

<input
    type="url"
    name="link_afiliado"
    placeholder="Cole aqui seu link de afiliado"
    required
>

<button type="submit">
    📝 GERAR PUBLICAÇÃO
</button>

</form>

<a
    href="{{ p.url }}"
    target="_blank"
>
    Ver produto no Mercado Livre
</a>

</div>

{% endfor %}

</div>

{% endif %}


{% if mensagem %}

<div class="card">

<h2>📢 Publicação pronta</h2>

<p class="pequeno">
Copie o texto abaixo e publique no seu Canal do WhatsApp.
</p>

<textarea id="mensagem">{{ mensagem }}</textarea>

<button
    onclick="copiarMensagem()"
>
    📋 COPIAR PUBLICAÇÃO
</button>

</div>

<script>

function copiarMensagem() {

    const texto =
        document.getElementById("mensagem").value;

    navigator.clipboard.writeText(texto);

    alert("Publicação copiada!");

}

</script>

{% endif %}


<div class="card">

<h2>⚙️ Próxima etapa</h2>

<p>
Nesta V1 a publicação no WhatsApp é manual.
Isso evita depender de uma API não documentada do Canal.
</p>

<p>
Na próxima etapa podemos adicionar:
</p>

<ul>
<li>Banco de ofertas</li>
<li>Filtro por categoria</li>
<li>Filtro por desconto</li>
<li>Evitar produtos repetidos</li>
<li>Agendamento</li>
<li>Histórico de publicações</li>
<li>Integração de publicação no canal através de uma solução compatível</li>
</ul>

</div>

</div>

</body>

</html>
"""


# ============================================================
# ROTAS
# ============================================================

@app.route("/", methods=["GET"])
def index():

    return render_template_string(
        HTML,
        produtos=None,
        mensagem=None,
        query="",
        desconto_minimo=10,
        moeda=moeda
    )


@app.route("/buscar", methods=["POST"])
def buscar():

    query = request.form.get("query", "").strip()

    try:
        desconto_minimo = float(
            request.form.get(
                "desconto_minimo",
                10
            )
        )
    except:
        desconto_minimo = 10

    if not query:
        return redirect(url_for("index"))

    produtos = buscar_produtos(
        query,
        limite=30
    )

    filtrados = []

    for produto in produtos:

        if produto["desconto"] >= desconto_minimo:
            filtrados.append(produto)

    return render_template_string(
        HTML,
        produtos=filtrados,
        mensagem=None,
        query=query,
        desconto_minimo=desconto_minimo,
        moeda=moeda
    )


@app.route("/gerar", methods=["POST"])
def gerar():

    try:

        produto = {
            "id": request.form.get("item_id"),
            "titulo": request.form.get("titulo"),
            "preco": float(
                request.form.get("preco", 0)
            ),
            "preco_original": (
                float(request.form.get("preco_original"))
                if request.form.get("preco_original")
                else None
            ),
            "desconto": float(
                request.form.get("desconto", 0)
            ),
            "url": request.form.get("url"),
            "imagem": request.form.get("imagem")
        }

        link_afiliado = request.form.get(
            "link_afiliado",
            ""
        ).strip()

        if not link_afiliado:
            raise ValueError(
                "Link de afiliado não informado."
            )

        mensagem = gerar_mensagem(
            produto,
            link_afiliado
        )

        salvar_oferta(
            produto,
            link_afiliado
        )

        return render_template_string(
            HTML,
            produtos=[produto],
            mensagem=mensagem,
            query="",
            desconto_minimo=0,
            moeda=moeda
        )

    except Exception as e:

        print("[ERRO GERANDO]", e)

        return redirect(
            url_for("index")
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return {
        "status": "ok",
        "app": "cacador-de-ofertas"
    }


# ============================================================
# INICIALIZAÇÃO
# ============================================================

init_db()


if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "8080")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )