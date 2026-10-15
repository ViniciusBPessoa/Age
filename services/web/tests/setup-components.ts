/**
 * Preparo dos testes de componente.
 *
 * Carregado só pelo projeto `components` do `vitest.config.ts`, que roda
 * em `jsdom` — então `document` existe aqui, e o import estático de
 * `@testing-library/react` (que o toca ao ser carregado) é seguro.
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Sem `cleanup`, cada `render` acumula no mesmo `document.body`: o
// segundo teste de um arquivo encontraria DOIS elementos com o mesmo
// texto e `getByText` falharia com "found multiple elements" — uma falha
// que parece bug do componente e é vazamento entre testes.
afterEach(cleanup);
