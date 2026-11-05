/**
 * Campo de senha com "mostrar" e aviso de Caps Lock.
 *
 * Os dois vêm das diretrizes de acessibilidade de formulário de login:
 * - mostrar a senha deixa conferir o que foi digitado antes de enviar,
 *   e ajuda quem usa leitor de tela, que pula caractere mascarado;
 * - o aviso de Caps Lock evita o erro de senha mais comum que existe,
 *   e que gasta uma das cinco tentativas do limite por conta.
 */

"use client";

import { forwardRef, useState, type InputHTMLAttributes } from "react";

import { IconAlert, IconEye, IconEyeOff, Input } from "@/components/ui";

export const CampoSenha = forwardRef<
  HTMLInputElement,
  Omit<InputHTMLAttributes<HTMLInputElement>, "type">
>(function CampoSenha({ id, ...props }, ref) {
  const [visivel, setVisivel] = useState(false);
  const [capsLock, setCapsLock] = useState(false);

  const checarCaps = (e: React.KeyboardEvent<HTMLInputElement>) =>
    setCapsLock(e.getModifierState?.("CapsLock") ?? false);

  return (
    <div>
      <div className="relative">
        <Input
          ref={ref}
          id={id}
          type={visivel ? "text" : "password"}
          onKeyDown={checarCaps}
          onKeyUp={checarCaps}
          onBlur={() => setCapsLock(false)}
          className="pr-11"
          aria-describedby={capsLock ? `${id}-caps` : undefined}
          {...props}
        />
        <button
          type="button"
          onClick={() => setVisivel((v) => !v)}
          aria-label={visivel ? "Esconder senha" : "Mostrar senha"}
          aria-pressed={visivel}
          aria-controls={id}
          className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-lg text-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand"
        >
          {visivel ? (
            <IconEyeOff className="h-4 w-4" />
          ) : (
            <IconEye className="h-4 w-4" />
          )}
        </button>
      </div>
      {capsLock ? (
        <p
          id={`${id}-caps`}
          role="status"
          className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-warn"
        >
          <IconAlert className="h-3.5 w-3.5" />
          Caps Lock está ligado.
        </p>
      ) : null}
    </div>
  );
});
