/**
 * As cores de categoria de um evento, com nome que a pessoa reconhece.
 *
 * São as onze cores de evento do Google Agenda, em português: quem vem
 * de lá já sabe que "Mirtilo" é o azul e "Sálvia" o verde. O valor em
 * cada tema mora em `globals.css` (`--ev-<nome>`), calculado para
 * manter o título do evento legível sobre o fundo tingido nos dois
 * modos. Aqui fica só o que a tela precisa para oferecer a escolha.
 */

import type { EventColor } from "@/lib/types";

export const CORES_DE_EVENTO: { valor: EventColor; nome: string }[] = [
  { valor: "tomate", nome: "Tomate" },
  { valor: "flamingo", nome: "Flamingo" },
  { valor: "tangerina", nome: "Tangerina" },
  { valor: "banana", nome: "Banana" },
  { valor: "salvia", nome: "Sálvia" },
  { valor: "manjericao", nome: "Manjericão" },
  { valor: "pavao", nome: "Pavão" },
  { valor: "mirtilo", nome: "Mirtilo" },
  { valor: "lavanda", nome: "Lavanda" },
  { valor: "uva", nome: "Uva" },
  { valor: "grafite", nome: "Grafite" },
];

export function nomeDaCor(cor: EventColor | null | undefined): string {
  if (!cor) return "Cor do tipo";
  return CORES_DE_EVENTO.find((c) => c.valor === cor)?.nome ?? cor;
}
