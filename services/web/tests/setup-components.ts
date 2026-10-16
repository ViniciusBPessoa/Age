/**
 * Preparo dos testes de componente.
 *
 * Carregado só pelo projeto `components` do `vitest.config.ts`, que roda
 * em `jsdom` — então `document` existe aqui, e o import estático de
 * `@testing-library/react` (que o toca ao ser carregado) é seguro.
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// O jsdom não implementa `scrollIntoView` — ele não calcula layout, e
// rolagem não existe lá. Sem este stub, qualquer componente que role
// (o chat, ao receber mensagem) quebra no `useEffect` com
// "scrollIntoView is not a function", e a falha parece bug do
// componente quando é ausência do ambiente.
//
// Stub vazio e não espiã: o que o teste do chat verifica é o CSS que
// permite rolar, não quantas vezes o scroll foi chamado.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}

// Sem `cleanup`, cada `render` acumula no mesmo `document.body`: o
// segundo teste de um arquivo encontraria DOIS elementos com o mesmo
// texto e `getByText` falharia com "found multiple elements" — uma falha
// que parece bug do componente e é vazamento entre testes.
afterEach(cleanup);
