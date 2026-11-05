/**
 * Preferência de tema: sistema, claro ou escuro.
 *
 * Antes havia só um botão que alternava claro/escuro, e quem escolhia
 * uma vez perdia o "seguir o sistema" para sempre. Agora são três
 * estados, como no Android, no iOS e no Google Agenda.
 *
 * O tema é aplicado por um script no `<head>` (`SCRIPT_DO_TEMA`) ANTES
 * da primeira pintura. Aplicado só no efeito do React, quem escolheu
 * claro com o sistema em escuro via a tela piscar escura a cada
 * navegação completa.
 */

export type PreferenciaDeTema = "sistema" | "claro" | "escuro";

const CHAVE = "age-theme";

export function lerPreferencia(): PreferenciaDeTema {
  try {
    const v = localStorage.getItem(CHAVE);
    if (v === "light") return "claro";
    if (v === "dark") return "escuro";
  } catch {
    // Sem armazenamento (navegação privada): segue o sistema.
  }
  return "sistema";
}

export function temaEfetivo(p: PreferenciaDeTema): "claro" | "escuro" {
  if (p !== "sistema") return p;
  return globalThis.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "claro"
    : "escuro";
}

export function aplicarPreferencia(p: PreferenciaDeTema): void {
  const raiz = document.documentElement;
  if (p === "sistema") delete raiz.dataset.theme;
  else raiz.dataset.theme = p === "claro" ? "light" : "dark";
  try {
    if (p === "sistema") localStorage.removeItem(CHAVE);
    else localStorage.setItem(CHAVE, p === "claro" ? "light" : "dark");
  } catch {
    // O tema da sessão vale; só não é lembrado.
  }
  globalThis.dispatchEvent(new CustomEvent("age-tema", { detail: p }));
}

/**
 * Roda no `<head>`, antes do CSS pintar. Curto e sem dependências: é
 * texto puro dentro de uma tag `<script>`. Os valores gravados são
 * `light`/`dark`, os mesmos da versão anterior, para quem já tinha
 * escolhido não perder a escolha.
 */
export const SCRIPT_DO_TEMA = `(function(){try{var t=localStorage.getItem("${CHAVE}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}catch(e){}})();`;
