/**
 * As peças novas da agenda: atalhos, cor e o "Desfazer".
 *
 * O arrastar em si (mover, redimensionar, soltar tarefa) foi verificado
 * num navegador de verdade, porque o jsdom não implementa o arrastar da
 * biblioteca da grade. Aqui ficam as regras que dá para cercar sem ele.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Aviso, type AvisoAgenda } from "@/components/calendar/aviso";
import { SeletorDeCor, SeletorDeFlex } from "@/components/calendar/seletores";
import { useAtalhos } from "@/lib/atalhos";
import type { EventColor, Flexibility } from "@/lib/types";

function ComAtalhos({ onN }: { onN: () => void }) {
  useAtalhos({ n: onN });
  return <input aria-label="campo" />;
}

describe("atalhos de teclado", () => {
  it("N dispara quando nada está em foco", () => {
    const onN = vi.fn();
    render(<ComAtalhos onN={onN} />);
    fireEvent.keyDown(window, { key: "n" });
    expect(onN).toHaveBeenCalledTimes(1);
  });

  it("não dispara enquanto a pessoa digita num campo", () => {
    // Sabotagem: tirar a checagem de `digitando`. Escrever "Dentista"
    // no título abriria um novo compromisso a cada "n".
    const onN = vi.fn();
    render(<ComAtalhos onN={onN} />);
    const campo = screen.getByLabelText("campo");
    fireEvent.keyDown(campo, { key: "n" });
    expect(onN).not.toHaveBeenCalled();
  });

  it("não dispara com Ctrl (Ctrl+N é do navegador)", () => {
    const onN = vi.fn();
    render(<ComAtalhos onN={onN} />);
    fireEvent.keyDown(window, { key: "n", ctrlKey: true });
    expect(onN).not.toHaveBeenCalled();
  });
});

function CorControlada() {
  const [cor, setCor] = useState<EventColor | null>("mirtilo");
  return (
    <>
      <SeletorDeCor valor={cor} onChange={setCor} />
      <output>{cor ?? "nenhuma"}</output>
    </>
  );
}

describe("seletor de cor", () => {
  it("oferece as onze cores e a opção sem cor, com nome acessível", () => {
    render(<CorControlada />);
    expect(screen.getAllByRole("radio")).toHaveLength(12);
    expect(
      screen.getByRole("radio", { name: "Mirtilo" }).getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("troca e tira a cor", () => {
    render(<CorControlada />);
    fireEvent.click(screen.getByRole("radio", { name: "Sálvia" }));
    expect(document.querySelector("output")?.textContent).toBe("salvia");
    fireEvent.click(screen.getByRole("radio", { name: /sem categoria/i }));
    expect(document.querySelector("output")?.textContent).toBe("nenhuma");
  });
});

describe("seletor de flexibilidade", () => {
  it("os três estados de §5.6 estão à vista", () => {
    const fn = vi.fn<(f: Flexibility) => void>();
    render(<SeletorDeFlex valor="fixed" onChange={fn} />);
    const opcoes = screen.getAllByRole("radio");
    expect(opcoes).toHaveLength(3);
    fireEvent.click(screen.getByRole("radio", { name: /Semi/ }));
    expect(fn).toHaveBeenCalledWith("semi_flexible");
  });
});

describe("aviso com desfazer", () => {
  it("Desfazer chama a volta e some", async () => {
    const desfazer = vi.fn();
    const sumir = vi.fn();
    const aviso: AvisoAgenda = { id: 1, texto: '"Dentista" foi para sexta.', desfazer };
    render(<Aviso aviso={aviso} onSumir={sumir} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Desfazer/ }));
    });
    expect(desfazer).toHaveBeenCalledTimes(1);
    expect(sumir).toHaveBeenCalled();
  });

  it("some sozinho depois de alguns segundos", () => {
    vi.useFakeTimers();
    const sumir = vi.fn();
    render(<Aviso aviso={{ id: 2, texto: "Pronto." }} onSumir={sumir} />);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(sumir).toHaveBeenCalled();
    vi.useRealTimers();
  });
});
