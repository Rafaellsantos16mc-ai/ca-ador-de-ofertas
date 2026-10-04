import os
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, render_template_string, redirect


# ============================================================
# CONFIGURAÇÃO
# ============================================================

APP_NAME = "Caçador de Ofertas"

DB_PATH = os.getenv("DB_PATH", "ofertas_mvp.db")
PORT = int(os.getenv("PORT", "8080"))

ML_API = "https://api.mercadolibre.com"
ML_SITE = "MLB"

ML_ACCESS_TOKEN = os.getenv("ML_ACCESS_TOKEN", "").strip()

MIN_PRICE = float(os.getenv("MIN_PRICE", "69.90"))
MIN_DISCOUNT = float(os.getenv("MIN_DISCOUNT", "10"))

REQUEST_TIMEOUT = 20


# Categorias que já estavam respondendo no seu projeto.
CATEGORIES = [
    ("📱 Celulares", "MLB1055"),
    ("🏋️ Academia", "MLB122102"),
    ("🎧 Eletrônicos", "MLB135384"),
    ("🏠 Casa", "MLB1645"),
    ("🌸 Perfumes", "MLB178938"),
    ("🍳 Cozinha", "MLB120373"),
    ("🔧 Ferramentas", "MLB271379"),
    ("👕 Moda", "MLB1398"),
    ("🚗 Automotivo", "MLB60608"),
]


app = Flask(__name__)


# ============================================================
# BANCO
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            titulo TEXT NOT NULL,
            url TEXT NOT NULL,

            preco REAL NOT NULL DEFAULT 0,
            preco_anterior REAL NOT NULL DEFAULT 0,
            desconto REAL NOT NULL DEFAULT 0,

            imagem TEXT DEFAULT '',

            origem TEXT DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'pendente',

            criado_em TEXT NOT NULL,
            confirmado_em TEXT,

            item_id TEXT DEFAULT '',
            categoria TEXT DEFAULT ''
        )
    """)

    cols = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(ofertas)"
        ).fetchall()
    }

    if "item_id" not in cols:
        conn.execute(
            "ALTER TABLE ofertas ADD COLUMN item_id TEXT DEFAULT ''"
        )

    if "categoria" not in cols:
        conn.execute(
            "ALTER TABLE ofertas ADD COLUMN categoria TEXT DEFAULT ''"
        )

    conn.commit()
    conn.close()


def row_dict(row):
    return dict(row) if row else None


# ============================================================
# UTILITÁRIOS
# ============================================================

def calcular_desconto(preco, anterior):
    try:
        preco = float(preco or 0)
        anterior = float(anterior or 0)

        if anterior > preco > 0:
            return round(
                (1 - (preco / anterior)) * 100,
                1
            )

    except Exception:
        pass

    return 0.0


def validar_url(url):
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:
        return False


# ============================================================
# MERCADO LIVRE
# ============================================================

def ml_headers():
    if not ML_ACCESS_TOKEN:
        return {}

    return {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}",
        "Accept": "application/json",
    }


def ml_get(path, params=None):
    """
    Faz uma chamada ao Mercado Livre e devolve:

    ok
    status
    data
    erro
    """

    url = ML_API + path

    try:
        response = requests.get(
            url,
            params=params or {},
            headers=ml_headers(),
            timeout=REQUEST_TIMEOUT,
        )

    except requests.RequestException as exc:

        print(
            f"[ML ERRO REDE] {path} | {exc}"
        )

        return {
            "ok": False,
            "status": 0,
            "data": None,
            "erro": str(exc),
        }

    try:
        data = response.json()

    except Exception:

        data = {
            "raw": response.text[:1000]
        }

    if response.status_code != 200:

        print(
            f"[ML ERRO] HTTP {response.status_code} "
            f"| {path} | {str(data)[:700]}"
        )

        return {
            "ok": False,
            "status": response.status_code,
            "data": data,
            "erro": data,
        }

    return {
        "ok": True,
        "status": 200,
        "data": data,
        "erro": None,
    }


# ============================================================
# TESTE DO TOKEN
# ============================================================

def testar_token():

    if not ML_ACCESS_TOKEN:

        return {
            "ok": False,
            "status": 0,
            "erro": "ML_ACCESS_TOKEN não configurado."
        }

    result = ml_get("/users/me")

    if not result["ok"]:

        return {
            "ok": False,
            "status": result["status"],
            "erro": result["erro"],
        }

    data = result["data"] or {}

    return {
        "ok": True,
        "status": 200,
        "user_id": data.get("id"),
        "nickname": data.get("nickname"),
    }


# ============================================================
# HIGHLIGHTS / MAIS VENDIDOS
# ============================================================

def buscar_highlights(categoria_id):

    path = (
        f"/highlights/"
        f"{ML_SITE}/category/"
        f"{categoria_id}"
    )

    result = ml_get(path)

    if not result["ok"]:

        print(
            f"[HIGHLIGHTS ERRO] "
            f"{categoria_id} | "
            f"HTTP {result['status']}"
        )

        return []

    data = result["data"] or {}

    content = data.get("content") or []

    print(
        f"[HIGHLIGHTS] "
        f"{categoria_id} | "
        f"itens={len(content)}"
    )

    return content


# ============================================================
# ITEM REAL
# ============================================================

def buscar_item(item_id):

    if not item_id:
        return None

    result = ml_get(
        f"/items/{item_id}"
    )

    if not result["ok"]:

        print(
            f"[ITEM ERRO] "
            f"{item_id} | "
            f"HTTP {result['status']}"
        )

        return None

    return result["data"]


# ============================================================
# PRODUTO DE CATÁLOGO
# ============================================================

def buscar_produto(product_id):

    if not product_id:
        return None

    result = ml_get(
        f"/products/{product_id}"
    )

    if not result["ok"]:

        print(
            f"[PRODUCT ERRO] "
            f"{product_id} | "
            f"HTTP {result['status']}"
        )

        return None

    return result["data"]


# ============================================================
# CONVERTER PRODUCT EM ITEM
# ============================================================

def item_a_partir_do_produto(product):

    if not isinstance(product, dict):
        return None

    # Algumas respostas possuem diretamente
    # buy_box_winner.
    buy_box = (
        product.get("buy_box_winner")
        or product.get("buy_box")
    )

    if isinstance(buy_box, dict):

        item_id = (
            buy_box.get("item_id")
            or buy_box.get("id")
        )

        if item_id:

            item = buscar_item(str(item_id))

            if item:
                return item

    # Alguns retornos podem possuir um item
    # dentro de uma estrutura de winners.
    for key in (
        "buy_box_winners",
        "items",
        "children"
    ):

        values = product.get(key)

        if not isinstance(values, list):
            continue

        for value in values:

            if not isinstance(value, dict):
                continue

            item_id = (
                value.get("item_id")
                or value.get("id")
            )

            if not item_id:
                continue

            # Só tentamos IDs que tenham cara
            # de publicação.
            item_id = str(item_id)

            if item_id.startswith("MLB"):

                item = buscar_item(item_id)

                if item:
                    return item

    return None


# ============================================================
# NORMALIZAR ANÚNCIO
# ============================================================

def normalizar_item(item, categoria):

    if not isinstance(item, dict):
        return None

    item_id = str(
        item.get("id") or ""
    ).strip()

    titulo = str(
        item.get("title") or ""
    ).strip()

    url = str(
        item.get("permalink") or ""
    ).strip()

    if not item_id:
        return None

    if not titulo:
        return None

    if not url:
        return None

    try:
        preco = float(
            item.get("price")
        )

    except Exception:
        return None

    if preco <= 0:
        return None

    if preco < MIN_PRICE:
        return None

    # Preço anterior.
    preco_anterior = 0.0

    original = item.get(
        "original_price"
    )

    try:
        if original:
            preco_anterior = float(original)

    except Exception:
        preco_anterior = 0.0

    # Não inventamos desconto.
    desconto = calcular_desconto(
        preco,
        preco_anterior
    )

    if desconto < MIN_DISCOUNT:
        return None

    imagem = (
        item.get("thumbnail")
        or ""
    )

    if imagem.startswith("http://"):

        imagem = (
            "https://"
            + imagem[7:]
        )

    return {
        "item_id": item_id,
        "titulo": titulo,
        "url": url,
        "preco": preco,
        "preco_anterior": preco_anterior,
        "desconto": desconto,
        "imagem": imagem,
        "categoria": categoria,
    }


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(oferta):

    conn = db()

    # Não duplicar o mesmo ITEM.
    existente = conn.execute(
        """
        SELECT id
        FROM ofertas
        WHERE item_id = ?
        AND status != 'ignorada'
        LIMIT 1
        """,
        (
            oferta["item_id"],
        )
    ).fetchone()

    if existente:

        conn.close()

        return False

    conn.execute(
        """
        INSERT INTO ofertas
        (
            titulo,
            url,
            preco,
            preco_anterior,
            desconto,
            imagem,
            origem,
            status,
            criado_em,
            item_id,
            categoria
        )
        VALUES (
            ?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            oferta["titulo"],
            oferta["url"],
            oferta["preco"],
            oferta["preco_anterior"],
            oferta["desconto"],
            oferta["imagem"],
            "mercado_livre_highlights",
            "pendente",
            datetime.now().isoformat(
                timespec="seconds"
            ),
            oferta["item_id"],
            oferta["categoria"],
        )
    )

    conn.commit()
    conn.close()

    return True


# ============================================================
# BUSCA AUTOMÁTICA
# ============================================================

def buscar_ofertas_automaticas():

    if not ML_ACCESS_TOKEN:

        return {
            "ok": False,
            "erro": (
                "ML_ACCESS_TOKEN não configurado "
                "no Railway."
            ),
        }

    token = testar_token()

    if not token["ok"]:

        return {
            "ok": False,
            "erro": (
                "O token do Mercado Livre "
                "não foi aceito."
            ),
            "status": token.get(
                "status"
            ),
            "detalhe": token.get(
                "erro"
            ),
        }

    total_ranking = 0
    itens_reais = 0
    produtos_catalogo = 0
    ofertas_validas = 0
    novas = 0

    categorias_resultado = []

    # Evita buscar o mesmo item várias vezes.
    itens_processados = set()

    for categoria_nome, categoria_id in CATEGORIES:

        ranking = buscar_highlights(
            categoria_id
        )

        total_ranking += len(ranking)

        categoria_itens = 0
        categoria_produtos = 0
        categoria_validas = 0
        categoria_novas = 0

        for entry in ranking:

            if not isinstance(entry, dict):
                continue

            tipo = str(
                entry.get("type") or ""
            ).upper()

            element_id = str(
                entry.get("id") or ""
            ).strip()

            if not element_id:
                continue

            item = None

            # ------------------------------------------------
            # ITEM
            # ------------------------------------------------

            if tipo == "ITEM":

                if element_id in itens_processados:
                    continue

                itens_processados.add(
                    element_id
                )

                item = buscar_item(
                    element_id
                )

                if item:
                    itens_reais += 1
                    categoria_itens += 1

            # ------------------------------------------------
            # PRODUCT
            # ------------------------------------------------

            elif tipo == "PRODUCT":

                categoria_produtos += 1
                produtos_catalogo += 1

                product = buscar_produto(
                    element_id
                )

                if product:

                    item = (
                        item_a_partir_do_produto(
                            product
                        )
                    )

                    if item:
                        itens_reais += 1
                        categoria_itens += 1

            # ------------------------------------------------
            # USER_PRODUCT
            # ------------------------------------------------

            elif tipo == "USER_PRODUCT":

                # Não vamos inventar um endpoint.
                # USER_PRODUCT não é publicação ITEM.
                print(
                    f"[USER_PRODUCT IGNORADO] "
                    f"{element_id}"
                )

                continue

            else:

                print(
                    f"[TIPO DESCONHECIDO] "
                    f"{tipo} | {element_id}"
                )

                continue

            if not item:
                continue

            oferta = normalizar_item(
                item,
                categoria_nome
            )

            if not oferta:
                continue

            ofertas_validas += 1
            categoria_validas += 1

            if salvar_oferta(oferta):

                novas += 1
                categoria_novas += 1

                print(
                    f"[NOVA OFERTA] "
                    f"{oferta['item_id']} | "
                    f"{oferta['titulo'][:70]} | "
                    f"R$ {oferta['preco']:.2f} | "
                    f"-{oferta['desconto']}%"
                )

        categorias_resultado.append({
            "categoria": categoria_nome,
            "categoria_id": categoria_id,
            "ranking": len(ranking),
            "itens_reais": categoria_itens,
            "produtos": categoria_produtos,
            "validas": categoria_validas,
            "novas": categoria_novas,
        })

        print(
            f"[CATEGORIA FINAL] "
            f"{categoria_nome} | "
            f"ranking={len(ranking)} | "
            f"itens={categoria_itens} | "
            f"produtos={categoria_produtos} | "
            f"validas={categoria_validas} | "
            f"novas={categoria_novas}"
        )

    return {
        "ok": True,
        "usuario": (
            token.get("nickname")
            or token.get("user_id")
        ),
        "ranking_analisado": total_ranking,
        "itens_reais": itens_reais,
        "produtos_catalogo": produtos_catalogo,
        "ofertas_validas": ofertas_validas,
        "novas_ofertas": novas,
        "preco_minimo": MIN_PRICE,
        "desconto_minimo": MIN_DISCOUNT,
        "categorias": categorias_resultado,
    }


# ============================================================
# HTML
# ============================================================

HTML = r"""
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>
{{ app_name }}
</title>

<style>

*{
box-sizing:border-box
}

body{
margin:0;
background:#f4f6f8;
font-family:Arial,Helvetica,sans-serif;
color:#17202a
}

header{
background:#111827;
color:white;
padding:18px 20px;
position:sticky;
top:0;
z-index:5
}

.header{
max-width:1100px;
margin:auto;
display:flex;
justify-content:space-between;
align-items:center;
gap:12px
}

.logo{
font-size:21px;
font-weight:800
}

.badge{
background:#16a34a;
padding:7px 10px;
border-radius:999px;
font-size:12px;
font-weight:700
}

main{
max-width:1100px;
margin:20px auto;
padding:0 14px
}

.grid{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:12px;
margin-bottom:18px
}

.card{
background:white;
border-radius:14px;
padding:16px;
box-shadow:0 2px 10px #0000000b
}

.num{
font-size:28px;
font-weight:800;
margin-top:6px
}

.label{
font-size:12px;
color:#6b7280
}

.panel{
background:white;
border-radius:14px;
padding:15px;
margin-bottom:14px
}

.controls{
display:flex;
gap:8px;
flex-wrap:wrap;
align-items:center
}

button{
border:0;
border-radius:10px;
padding:11px 14px;
font-weight:700;
cursor:pointer
}

.green{
background:#16a34a;
color:white
}

.red{
background:#dc2626;
color:white
}

.dark{
background:#111827;
color:white
}

.light{
background:#e5e7eb;
color:#111827
}

.note{
background:#eff6ff;
border:1px solid #bfdbfe;
padding:12px;
border-radius:12px;
font-size:13px;
margin-bottom:14px
}

.statusbox{
font-size:13px;
color:#4b5563;
margin-top:10px;
white-space:pre-wrap;
line-height:1.5
}

.success{
background:#dcfce7;
color:#166534;
border:1px solid #bbf7d0;
padding:12px;
border-radius:10px
}

.error{
background:#fee2e2;
color:#991b1b;
border:1px solid #fecaca;
padding:12px;
border-radius:10px
}

form{
display:grid;
grid-template-columns:2fr 2fr 1fr 1fr auto;
gap:8px;
background:white;
padding:14px;
border-radius:14px;
margin-bottom:14px
}

input{
width:100%;
padding:11px;
border:1px solid #d1d5db;
border-radius:9px;
font-size:14px
}

.offer{
display:grid;
grid-template-columns:90px 1fr auto;
gap:14px;
align-items:center;
background:white;
border-radius:14px;
padding:13px;
margin-bottom:10px;
box-shadow:0 2px 9px #00000009
}

.offer img{
width:90px;
height:90px;
border-radius:10px;
object-fit:cover;
background:#eee
}

.title{
font-weight:800;
margin-bottom:7px
}

.meta{
font-size:13px;
color:#6b7280;
line-height:1.5
}

.price{
font-size:21px;
font-weight:800
}

.old{
text-decoration:line-through;
color:#9ca3af;
font-size:12px
}

.actions{
display:flex;
gap:7px;
flex-direction:column
}

.status{
display:inline-block;
padding:5px 8px;
border-radius:999px;
font-size:11px;
font-weight:700
}

.pendente{
background:#fef3c7;
color:#92400e
}

.confirmada{
background:#dcfce7;
color:#166534
}

.ignorada{
background:#fee2e2;
color:#991b1b
}

.empty{
background:white;
padding:35px;
text-align:center;
border-radius:14px;
color:#6b7280
}

@media(max-width:800px){

.grid{
grid-template-columns:repeat(2,1fr)
}

form{
grid-template-columns:1fr
}

.offer{
grid-template-columns:65px 1fr
}

.offer img{
width:65px;
height:65px
}

.actions{
grid-column:1/-1;
flex-direction:row
}

.actions button{
flex:1
}

}

</style>

</head>

<body>

<header>

<div class="header">

<div class="logo">
🎯 {{ app_name }}
</div>

<div class="badge">
MVP • busca automática
</div>

</div>

</header>

<main>

<div class="grid">

<div class="card">
<div class="label">
Pendentes
</div>
<div class="num">
{{ stats.pendente }}
</div>
</div>

<div class="card">
<div class="label">
Confirmadas
</div>
<div class="num">
{{ stats.confirmada }}
</div>
</div>

<div class="card">
<div class="label">
Ignoradas
</div>
<div class="num">
{{ stats.ignorada }}
</div>
</div>

<div class="card">
<div class="label">
Total
</div>
<div class="num">
{{ stats.total }}
</div>
</div>

</div>


<div class="note">

<b>Busca automática:</b>

o sistema consulta os
<b>mais vendidos</b>
do Mercado Livre.

As ofertas encontradas entram como
<b>PENDENTES</b>.

Nada é enviado automaticamente.

Você continua decidindo o que vai para a fila do WhatsApp.

</div>


<div class="panel">

<div class="controls">

<button
class="green"
onclick="buscar()"
>
🔎 BUSCAR OFERTAS AGORA
</button>

<button
class="dark"
onclick="testar()"
>
🔐 TESTAR MERCADO LIVRE
</button>

<button
class="light"
onclick="carregar()"
>
↻ ATUALIZAR
</button>

</div>

<div
id="statusbusca"
class="statusbox"
></div>

</div>


<form
method="post"
action="/oferta"
>

<input
name="titulo"
placeholder="Nome do produto"
required
>

<input
name="url"
placeholder="Link do produto"
required
>

<input
name="preco"
type="number"
step="0.01"
placeholder="Preço atual"
required
>

<input
name="preco_anterior"
type="number"
step="0.01"
placeholder="Preço anterior"
>

<input
name="imagem"
placeholder="URL da imagem"
>

<button class="dark">
Adicionar
</button>

</form>


<div
class="controls"
style="margin:14px 0"
>

<button
class="light"
onclick="filtrar('pendente')"
>
Pendentes
</button>

<button
class="green"
onclick="filtrar('confirmada')"
>
Confirmadas / fila WhatsApp
</button>

<button
class="light"
onclick="filtrar('todas')"
>
Todas
</button>

</div>


<div id="lista"></div>

</main>


<script>

let filtro='pendente';


async function carregar(){

const r=await fetch(
'/api/ofertas?status='+filtro
);

const data=await r.json();

const el=
document.getElementById('lista');


if(!data.length){

el.innerHTML=
'<div class="empty">Nenhuma oferta nesta lista.</div>';

return;

}


el.innerHTML=data.map(o=>`

<div class="offer">

<img
src="${esc(o.imagem||'')}"
onerror="this.style.visibility='hidden'"
>

<div>

<div class="title">
${esc(o.titulo)}
</div>

<div class="meta">

<span class="status ${o.status}">
${o.status}
</span>

&nbsp;

${o.categoria
? esc(o.categoria)+' • '
: ''
}

${o.desconto>0
? '<b>-'+o.desconto+'%</b>'
: ''
}

</div>

<div class="price">

R$
${Number(o.preco)
.toFixed(2)
.replace('.',',')}

</div>

${
o.preco_anterior>0
?
'<div class="old">R$ '+
Number(o.preco_anterior)
.toFixed(2)
.replace('.',',')+
'</div>'
:
''
}

</div>


<div class="actions">

${
o.status==='pendente'
?
`
<button
class="green"
onclick="acao(${o.id},'confirmar')"
>
CONFIRMAR
</button>

<button
class="red"
onclick="acao(${o.id},'ignorar')"
>
IGNORAR
</button>
`
:
''
}


${
o.status!=='pendente'
?
`
<button
class="light"
onclick="acao(${o.id},'reverter')"
>
REVERTER
</button>
`
:
''
}


<button
class="dark"
onclick="window.open('${esc(o.url)}','_blank')"
>
ABRIR LINK
</button>


</div>

</div>

`).join('');

}


async function acao(id,a){

const r=
await fetch(
'/api/ofertas/'+id+'/'+a,
{
method:'POST'
}
);

if(!r.ok){

alert(
'Não foi possível atualizar a oferta.'
);

return;

}

carregar();

}


function mostrar(texto,tipo){

const box=
document.getElementById(
'statusbusca'
);

box.className=
'statusbox '+(tipo||'');

box.textContent=texto;

}


async function testar(){

mostrar(
'Testando autorização do Mercado Livre...',
''
);

const r=
await fetch(
'/mercadolivre/teste'
);

const d=
await r.json();


if(d.ok){

mostrar(
'✅ Mercado Livre respondeu HTTP 200.\\n'+
'Usuário: '+
(d.nickname||d.user_id||'identificado')+
'\\nToken aceito pela API.',
'success'
);

}else{

mostrar(
'❌ Mercado Livre recusou o teste.\\n'+
'HTTP: '+d.status+'\\n'+
'Detalhe: '+
JSON.stringify(d.erro),
'error'
);

}

}


async function buscar(){

mostrar(
'Buscando os mais vendidos do Mercado Livre...',
''
);

const r=
await fetch(
'/api/buscar',
{
method:'POST'
}
);

const d=
await r.json();


if(!d.ok){

mostrar(
'❌ BUSCA NÃO CONCLUÍDA\\n'+
'HTTP: '+
(d.status||'desconhecido')+
'\\n'+
'Motivo: '+
(d.erro||'erro desconhecido')+
'\\n'+
'Detalhe: '+
JSON.stringify(d.detalhe||''),
'error'
);

return;

}


mostrar(
'✅ Busca concluída.\\n'+
'Ranking analisado: '+
d.ranking_analisado+
'\\n'+
'Publicações reais encontradas: '+
d.itens_reais+
'\\n'+
'Produtos de catálogo encontrados: '+
d.produtos_catalogo+
'\\n'+
'Ofertas que passaram pelos filtros: '+
d.ofertas_validas+
'\\n'+
'Novas ofertas adicionadas: '+
d.novas_ofertas+
'\\n'+
'Preço mínimo: R$ '+
Number(d.preco_minimo)
.toFixed(2)
.replace('.',',')+
' • Desconto mínimo: '+
d.desconto_minimo+
'%',
'success'
);

carregar();

}


function filtrar(s){

filtro=s;

carregar();

}


function esc(v){

return String(v??'')
.replaceAll('&','&amp;')
.replaceAll('<','&lt;')
.replaceAll('>','&gt;')
.replaceAll('"','&quot;')
.replaceAll("'","&#039;");

}


carregar();

</script>

</body>

</html>
"""


# ============================================================
# ROTAS
# ============================================================

@app.get("/")
def index():

    conn = db()

    rows = conn.execute(
        """
        SELECT status, COUNT(*) c
        FROM ofertas
        GROUP BY status
        """
    ).fetchall()

    conn.close()

    stats = {
        "pendente": 0,
        "confirmada": 0,
        "ignorada": 0,
        "total": 0,
    }

    for row in rows:

        stats[row["status"]] = row["c"]

        stats["total"] += row["c"]

    return render_template_string(
        HTML,
        app_name=APP_NAME,
        stats=stats
    )


# ============================================================
# CADASTRO MANUAL
# ============================================================

@app.post("/oferta")
def criar_oferta():

    titulo = (
        request.form.get("titulo")
        or ""
    ).strip()

    url = (
        request.form.get("url")
        or ""
    ).strip()

    imagem = (
        request.form.get("imagem")
        or ""
    ).strip()

    try:

        preco = float(
            request.form.get("preco")
            or 0
        )

        anterior = float(
            request.form.get(
                "preco_anterior"
            )
            or 0
        )

    except ValueError:

        return "Preço inválido", 400


    if (
        not titulo
        or not validar_url(url)
        or preco <= 0
    ):

        return "Dados inválidos.", 400


    conn = db()

    conn.execute(
        """
        INSERT INTO ofertas
        (
            titulo,
            url,
            preco,
            preco_anterior,
            desconto,
            imagem,
            origem,
            status,
            criado_em,
            item_id,
            categoria
        )
        VALUES (
            ?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            titulo,
            url,
            preco,
            anterior,
            calcular_desconto(
                preco,
                anterior
            ),
            imagem,
            "manual",
            "pendente",
            datetime.now().isoformat(
                timespec="seconds"
            ),
            "",
            "",
        )
    )

    conn.commit()
    conn.close()

    return redirect("/")


# ============================================================
# API OFERTAS
# ============================================================

@app.get("/api/ofertas")
def api_ofertas():

    status = request.args.get(
        "status",
        "todas"
    )

    conn = db()

    if status in (
        "pendente",
        "confirmada",
        "ignorada"
    ):

        rows = conn.execute(
            """
            SELECT *
            FROM ofertas
            WHERE status=?
            ORDER BY id DESC
            """,
            (status,)
        ).fetchall()

    else:

        rows = conn.execute(
            """
            SELECT *
            FROM ofertas
            ORDER BY id DESC
            """
        ).fetchall()

    conn.close()

    return jsonify([
        row_dict(row)
        for row in rows
    ])


# ============================================================
# CONFIRMAR / IGNORAR / REVERTER
# ============================================================

@app.post(
    "/api/ofertas/<int:offer_id>/<action>"
)
def api_acao(
    offer_id,
    action
):

    actions = {
        "confirmar": "confirmada",
        "ignorar": "ignorada",
        "reverter": "pendente",
    }

    if action not in actions:

        return jsonify({
            "ok": False,
            "erro": "ação inválida"
        }), 400


    novo_status = actions[action]

    agora = datetime.now().isoformat(
        timespec="seconds"
    )

    conn = db()

    if novo_status == "confirmada":

        conn.execute(
            """
            UPDATE ofertas
            SET status=?,
                confirmado_em=?
            WHERE id=?
            """,
            (
                novo_status,
                agora,
                offer_id
            )
        )

    else:

        conn.execute(
            """
            UPDATE ofertas
            SET status=?,
                confirmado_em=NULL
            WHERE id=?
            """,
            (
                novo_status,
                offer_id
            )
        )

    changed = conn.total_changes

    conn.commit()
    conn.close()


    if not changed:

        return jsonify({
            "ok": False,
            "erro": "oferta não encontrada"
        }), 404


    return jsonify({
        "ok": True,
        "id": offer_id,
        "status": novo_status
    })


# ============================================================
# BUSCAR AUTOMATICAMENTE
# ============================================================

@app.post("/api/buscar")
def api_buscar():

    resultado = (
        buscar_ofertas_automaticas()
    )

    return jsonify(resultado)


# ============================================================
# TESTE MERCADO LIVRE
# ============================================================

@app.get("/mercadolivre/teste")
def mercado_livre_teste():

    return jsonify(
        testar_token()
    )


@app.get("/mercadolivre/status")
def mercado_livre_status():

    teste = testar_token()

    return jsonify({

        "token_configurado":
            bool(ML_ACCESS_TOKEN),

        "teste_api":
            teste,

        "site":
            ML_SITE,

        "preco_minimo":
            MIN_PRICE,

        "desconto_minimo":
            MIN_DISCOUNT,

        "categorias":
            len(CATEGORIES),

    })


# ============================================================
# FILA WHATSAPP
# ============================================================

@app.get("/api/whatsapp/fila")
def whatsapp_fila():

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            titulo,
            url,
            preco,
            preco_anterior,
            desconto,
            imagem,
            confirmado_em,
            item_id,
            categoria

        FROM ofertas

        WHERE status='confirmada'

        ORDER BY
            confirmado_em ASC,
            id ASC
        """
    ).fetchall()

    conn.close()

    return jsonify({
        "total": len(rows),
        "ofertas": [
            row_dict(row)
            for row in rows
        ]
    })


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
def api_status():

    conn = db()

    rows = conn.execute(
        """
        SELECT
            status,
            COUNT(*) c

        FROM ofertas

        GROUP BY status
        """
    ).fetchall()

    conn.close()

    data = {
        "pendente": 0,
        "confirmada": 0,
        "ignorada": 0,
    }

    for row in rows:

        data[row["status"]] = row["c"]

    data["total"] = sum(
        data.values()
    )

    return jsonify(data)


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return jsonify({

        "ok": True,

        "app":
            APP_NAME,

        "etapa":
            "busca_por_mais_vendidos",

        "mercado_livre_token":
            bool(ML_ACCESS_TOKEN),

        "whatsapp_fila":
            True,

    })


# ============================================================
# INICIALIZA
# ============================================================

init_db()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT
    )