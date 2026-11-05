/**
 * Fixa o `X-Forwarded-For` a partir do socket, antes do Next ver o pedido.
 *
 * Carregado com `node --require` (ver `package.json` e Dockerfile.web).
 *
 * Por que existe: o Next preenche esse header com `??=`, ou seja, só
 * quando ele NÃO veio. Um navegador que manda `X-Forwarded-For: 1.2.3.4`
 * passa esse valor intacto até o core-api, que o usa no rate limit de
 * login por IP. Trocando o IP a cada tentativa, o limite nunca batia.
 *
 * Aqui a última entrada passa a ser SEMPRE o endereço do socket. As
 * entradas anteriores só são mantidas se `AGE_TRUSTED_PROXY_HOPS` > 0,
 * isto é, se há um proxy nosso (Caddy, §11.1.1) na frente que escreveu
 * o IP real do cliente. Quem escolhe qual entrada vale é
 * `ipDoCliente()` em `lib/bff-seguranca.ts`.
 */
"use strict";

const http = require("node:http");

const hops = Number.parseInt(process.env.AGE_TRUSTED_PROXY_HOPS || "0", 10) || 0;
const emitOriginal = http.Server.prototype.emit;

http.Server.prototype.emit = function emit(evento, req) {
  if (evento === "request" && req && req.socket && req.headers) {
    const socket = req.socket.remoteAddress || "";
    const anterior = req.headers["x-forwarded-for"];
    req.headers["x-forwarded-for"] =
      hops > 0 && anterior ? `${anterior}, ${socket}` : socket;
  }
  return emitOriginal.apply(this, arguments);
};
