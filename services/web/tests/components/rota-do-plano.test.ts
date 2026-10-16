/**
 * O endereço das ações de um plano (§5.8).
 *
 * Relato: "apareceu o pop-up para poder selecionar se eu queria ou
 * não, só que o pop-up não funcionou, deu algum erro".
 *
 * No log do core-api: oito POSTs para `/plans/<id>/apply`, oito 404.
 * O `<id>` era o id do TURNO. O do plano é outro UUID, estava na mesma
 * tela a poucas linhas de distância, e a variável que guardava o id do
 * turno se chamava `planId`.
 *
 * O que torna este caso perigoso é que nada quebra: os dois valores são
 * UUID válido, o TypeScript aceita os dois, e o cartão continua
 * aparecendo correto. Só o servidor sabe que o número está errado, e
 * ele respondia 404 em silêncio porque o `reject` tinha um
 * `.catch(() => undefined)`.
 *
 * A defesa é de tipo: `rotaDoPlano` exige o objeto `Plan`, não uma
 * string. Passar um id solto não compila mais, qualquer que seja ele.
 */

import { describe, expect, it } from "vitest";

import { rotaDoPlano } from "@/components/chat/chat-screen";
import type { Plan } from "@/lib/types";

const ID_DO_PLANO = "32abdc6e-adf3-4b92-9540-dc7df1d45c7b";
// O id que o front mandava por engano: o turno que propôs o plano.
const ID_DO_TURNO = "85cc4c36-668f-48af-b064-971ccd24711b";

function plano(): Plan {
  return {
    id: ID_DO_PLANO,
    conversation_id: "11111111-1111-1111-1111-111111111111",
    status: "proposed",
    summary: "Dentista quinta às 15h",
    items: [],
    created_at: "2026-10-16T05:19:29Z",
    expires_at: "2026-10-17T05:19:29Z",
    origin_note: null,
    is_expired: false,
  };
}

describe("rota das ações do plano", () => {
  it("aponta para o id do PLANO, não o do turno", () => {
    // O teste do bug. Se alguém voltar a usar o id do turno, esta
    // asserção é a que fala.
    const rota = rotaDoPlano(plano(), "apply");

    expect(rota).toBe(`/plans/${ID_DO_PLANO}/apply`);
    expect(rota).not.toContain(ID_DO_TURNO);
  });

  it("vale para as duas ações", () => {
    // Eram duas chamadas com o mesmo defeito, e a correção de uma não
    // corrigia a outra: aprovar e recusar falhavam separadamente.
    expect(rotaDoPlano(plano(), "apply")).toBe(`/plans/${ID_DO_PLANO}/apply`);
    expect(rotaDoPlano(plano(), "reject")).toBe(`/plans/${ID_DO_PLANO}/reject`);
  });

  it("o id vem do objeto, então não há como passar outro por engano", () => {
    // A garantia real não é esta asserção, é a assinatura: a função
    // recebe `Plan`, e um UUID avulso não é um `Plan`. Este caso
    // documenta a intenção para quem for mexer aqui depois.
    const outro = { ...plano(), id: "99999999-9999-9999-9999-999999999999" };

    expect(rotaDoPlano(outro, "apply")).toContain(outro.id);
    expect(rotaDoPlano(outro, "apply")).not.toContain(ID_DO_PLANO);
  });
});
