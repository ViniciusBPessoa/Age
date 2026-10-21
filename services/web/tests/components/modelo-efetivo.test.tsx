/**
 * Qual modelo o chat usa, quando a conta não tem chave própria.
 *
 * Este arquivo existe por uma falha de verificação minha. A captura da
 * tela mostrou o botão de anexo sumindo para quem usa a chave da
 * instância, eu corrigi no `chat-screen` — e depois sabotei a correção
 * de volta: **nenhum teste caiu**. Os testes de `composer-anexo`
 * montam o `Composer` direto, com `providerKey` explícito, então nunca
 * exercitam a regra que escolhe esse valor.
 *
 * A regra mora no `chat-screen`, e é aqui que ela é afirmada.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { Credential, KeySource } from "@/lib/types";

/**
 * A mesma precedência que o `chat-screen` aplica, extraída para poder
 * ser exercitada com todas as combinações.
 *
 * Duplicar a regra num teste seria vazio — ela passaria mesmo com o
 * componente errado. Por isso o último teste confere que o componente
 * REALMENTE consulta `keySource`, lendo o fonte: a função abaixo
 * documenta o comportamento, e o teste de fonte amarra o componente a
 * ele.
 */
function modeloEfetivo(
  ativo: { provider_key: string | null; model: string | null } | null,
  credentials: Credential[],
  keySource: KeySource | null,
): { providerKey: string | null; model: string | null } {
  return {
    providerKey:
      ativo?.provider_key ??
      credentials[0]?.provider_key ??
      (keySource?.source === "instance" ? keySource.provider_key : null) ??
      null,
    model:
      ativo?.model ??
      credentials[0]?.default_model ??
      (keySource?.source === "instance" ? keySource.model : null) ??
      null,
  };
}

const INSTANCIA: KeySource = {
  source: "instance",
  provider_key: "deepseek",
  model: "deepseek-flash",
  used_usd: 0,
  limit_usd: 2,
};

function credencial(provider_key: string, default_model: string): Credential {
  return {
    id: "c1",
    provider_key,
    key_last4: "1234",
    default_model,
    base_url: null,
    label: null,
    is_active: true,
    last_error: null,
    last_verified_at: null,
    created_at: new Date().toISOString(),
  };
}

describe("qual modelo o chat usa", () => {
  it("sem credencial propria, usa o da instancia", () => {
    /*
     * O caminho PADRÃO do Age: a chave é do operador e a conta não tem
     * nenhuma. Sem esta regra, o composer não sabe qual modelo vai
     * rodar, e qualquer coisa que dependa das capacidades dele — o
     * botão de anexo, por exemplo — se comporta como se não houvesse
     * modelo nenhum.
     */
    const r = modeloEfetivo(null, [], INSTANCIA);
    expect(r.providerKey).toBe("deepseek");
    expect(r.model).toBe("deepseek-flash");
  });

  it("a credencial propria tem precedencia sobre a instancia", () => {
    /*
     * Sabotagem: ler `keySource` primeiro. Quem colou a própria chave
     * passaria a conversar com o modelo do operador — e a gastar a
     * cota dele.
     */
    const r = modeloEfetivo(null, [credencial("openai", "gpt-4o-mini")], INSTANCIA);
    expect(r.providerKey).toBe("openai");
    expect(r.model).toBe("gpt-4o-mini");
  });

  it("a conversa aberta tem precedencia sobre tudo", () => {
    /*
     * Trocar de modelo vale para a conversa (§3.1.3). Uma conversa
     * começada no `pro` continua no `pro` mesmo que o padrão mude.
     */
    const r = modeloEfetivo(
      { provider_key: "deepseek", model: "deepseek-v4-pro" },
      [credencial("openai", "gpt-4o-mini")],
      INSTANCIA,
    );
    expect(r.model).toBe("deepseek-v4-pro");
  });

  it("sem nada configurado, fica null", () => {
    /*
     * Sabotagem: devolver um default inventado. O seletor diria o nome
     * de um modelo que não existe para esta conta, e o envio falharia
     * depois, sem explicação.
     */
    const r = modeloEfetivo(null, [], null);
    expect(r.providerKey).toBeNull();
    expect(r.model).toBeNull();
  });

  it("`source: user` nao empresta modelo", () => {
    /*
     * Sabotagem: ler `keySource.model` sem checar `source`.
     *
     * Com `source: "user"`, o backend manda `provider_key`/`model`
     * nulos — mas se um dia preenchê-los, ler sem checar pegaria o
     * valor errado. A checagem é o que torna a regra legível.
     */
    const r = modeloEfetivo(null, [], {
      source: "user",
      provider_key: null,
      model: null,
      used_usd: null,
      limit_usd: null,
    });
    expect(r.providerKey).toBeNull();
  });

  it("o chat-screen REALMENTE consulta keySource", () => {
    /*
     * O teste que faltava, e sem o qual todos os acima são decoração.
     *
     * Eu sabotei a correção no `chat-screen` e nenhum teste caiu: os
     * de `composer-anexo` montam o `Composer` com `providerKey`
     * explícito e nunca tocam a regra. Afirmar sobre o fonte é o que
     * amarra o componente ao comportamento descrito aqui.
     */
    const fonte = readFileSync(
      join(process.cwd(), "components", "chat", "chat-screen.tsx"),
      "utf8",
    );
    const trecho = fonte.slice(fonte.indexOf("<Composer"));
    expect(trecho).toContain('keySource?.source === "instance"');
    expect(trecho).toContain("keySource.provider_key");
    expect(trecho).toContain("keySource.model");
  });
});
