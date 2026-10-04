# Caçador de Ofertas — MVP 1

Esta é uma versão propositalmente simples e estável.

## O que já funciona
- Dashboard.
- Cadastro de ofertas.
- Cálculo automático do percentual de desconto.
- Lista de ofertas pendentes.
- Botão CONFIRMAR.
- Botão IGNORAR.
- Reverter decisão.
- Fila exclusiva de ofertas confirmadas para a futura integração com WhatsApp.
- SQLite.
- `/health`.
- `/api/status`.
- `/api/whatsapp/fila`.
- Funciona sem Mercado Livre e sem WhatsApp nesta primeira etapa.

## Rodar localmente

```bash
pip install -r requirements.txt
python app.py
```

Abra:
`http://localhost:8080`

## Railway

Suba estes arquivos:
- app.py
- requirements.txt
- railway.toml

O Railway usará o comando:
`gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`

Não é necessário Procfile.

## Testar rapidamente

Depois de abrir o endereço público do Railway:

1. Cadastre uma oferta usando o formulário.
2. Ela aparece como PENDENTE.
3. Clique CONFIRMAR.
4. Clique "Confirmadas / fila WhatsApp".
5. A oferta estará disponível em:
`/api/whatsapp/fila`

## Próxima etapa

Depois que esta base estiver comprovadamente funcionando, a próxima camada pode:
1. buscar produtos automaticamente;
2. calcular regras de oferta;
3. gerar o link de afiliado;
4. mandar as ofertas confirmadas para WhatsApp.

A integração com WhatsApp fica propositalmente separada da confirmação para não misturar dois problemas ao mesmo tempo.
