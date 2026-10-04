# Caçador de Ofertas — MVP 2

Esta versão mantém a confirmação manual da MVP 1 e adiciona uma primeira busca automática de anúncios do Mercado Livre.

## Variável necessária no Railway

Crie em Variables:

`ML_ACCESS_TOKEN`

Valor: seu Access Token da aplicação do Mercado Livre.

Também é possível configurar:

`MIN_PRICE=69.90`
`MIN_DISCOUNT=10`

## Fluxo

1. Abra o site.
2. Clique em **BUSCAR OFERTAS AGORA**.
3. O sistema consulta várias buscas de produtos.
4. Só entram ofertas com preço mínimo e desconto mínimo configurados.
5. As novas ofertas entram como **PENDENTES**.
6. Você decide CONFIRMAR ou IGNORAR.
7. As confirmadas continuam na fila `/api/whatsapp/fila`.

## Importante

A busca automática é propositalmente separada do WhatsApp. Primeiro validamos a obtenção real dos anúncios e a qualidade das ofertas.

A documentação atual do Mercado Livre exige Authorization Bearer para os recursos privados de API e documenta `/sites/MLB/search` para consultas de itens/listagens. Se a API responder 401/403, a própria aplicação mostra o status no retorno da busca e no endpoint `/mercadolivre/status`.

## Endpoints

`/`
`/api/buscar` POST
`/api/ofertas?status=pendente`
`/api/whatsapp/fila`
`/mercadolivre/status`
`/health`
