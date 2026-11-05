/**
 * Ícones de traço, desenhados aqui.
 *
 * A versão anterior usava emoji (`💬 📅 📋 ⚙`), e emoji não é ícone:
 *
 *   - renderiza colorido, com o estilo do SISTEMA, não do produto — o
 *     mesmo `📅` é azul no Windows, cinza no Android e vermelho no
 *     iOS, então a interface muda de cara por máquina;
 *   - não herda `currentColor`, então o item ativo da navegação não
 *     consegue tingir o próprio ícone;
 *   - tem métrica de fonte, não de caixa: alinha mal com texto e
 *     escorrega meio pixel conforme o peso;
 *   - alguns viram sequência dupla com seletor de variação, e a largura
 *     muda entre plataformas.
 *
 * Trazer `lucide-react` resolveria, mas por ~30 glifos usados são
 * centenas de componentes e uma dependência a versionar. São traços
 * simples; cabem aqui.
 *
 * Convenção: `viewBox` 24, traço 1.75, pontas e junções redondas,
 * `currentColor` sempre. Tamanho por classe (`h-4 w-4`), nunca por
 * prop, para que o ícone acompanhe a tipografia ao redor.
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Svg({ children, className, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      // `aria-hidden` por padrão: o ícone acompanha um rótulo de texto
      // em praticamente todo uso, e anunciá-lo duplicaria a leitura.
      // Quem precisar de ícone sozinho passa `aria-hidden={false}` com
      // `aria-label`.
      aria-hidden
      {...rest}
      /*
       * O tamanho padrao MESCLA com a classe de quem chama, em vez de
       * ser sobrescrito por ela.
       *
       * Antes o `className` vinha depois no `{...rest}` e substituia o
       * padrao inteiro: quem passasse so `className="text-muted"`
       * perdia `h-4 w-4 shrink-0` e recebia um icone do tamanho que o
       * SVG quisesse. Na tela de Conexoes isso aparecia como um icone
       * gigante e duplicado ao lado de "Minhas tarefas", empurrando o
       * nome para duas linhas. Visto na captura, invisivel no codigo.
       *
       * Vem DEPOIS do `{...rest}` para que o padrao nao seja perdido, e
       * quem precisar de outro tamanho continua podendo passar `h-5 w-5`,
       * que ganha por vir por ultimo na string.
       */
      className={"h-4 w-4 shrink-0" + (className ? " " + className : "")}
    >
      {children}
    </svg>
  );
}

/* ---------------- navegação ---------------- */

export const IconChat = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20.5 12a8.5 8.5 0 0 1-12.3 7.6L3.5 21l1.4-4.7A8.5 8.5 0 1 1 20.5 12Z" />
  </Svg>
);

export const IconCalendar = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="5" width="18" height="16" rx="2.5" />
    <path d="M3 10h18M8 3v4M16 3v4" />
  </Svg>
);

export const IconTasks = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 6.5 6 8.5 9.5 5M4 17.5l2 2 3.5-3.5M13 7h7M13 17h7" />
  </Svg>
);

export const IconStudy = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 7 3 4.8v12.4L12 19.5l9-2.3V4.8L12 7Z" />
    <path d="M12 7v12.5" />
  </Svg>
);

export const IconRoutine = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 11a8 8 0 1 0-2.6 5.9" />
    <path d="M20 5.5V11h-5.3" />
  </Svg>
);

export const IconReport = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20h16" />
    <path d="M7 20v-6M12 20V6M17 20v-9" />
  </Svg>
);

export const IconSettings = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.5v2.2M12 19.3v2.2M4.2 7.2l1.9 1.1M17.9 15.7l1.9 1.1M4.2 16.8l1.9-1.1M17.9 8.3l1.9-1.1" />
  </Svg>
);

/* ---------------- ações ---------------- */

export const IconSend = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" />
  </Svg>
);

export const IconPaperclip = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20.5 11.5l-8.2 8.2a4.5 4.5 0 0 1-6.4-6.4l8.3-8.3a3 3 0 0 1 4.3 4.3l-8.3 8.3a1.5 1.5 0 0 1-2.2-2.2l7.6-7.6" />
  </Svg>
);

export const IconImage = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
    <circle cx="8.5" cy="10" r="1.5" />
    <path d="M3.5 17.5l4.6-4.6a2 2 0 0 1 2.8 0l3 3M14.5 15.5l1.6-1.6a2 2 0 0 1 2.8 0l1.6 1.6" />
  </Svg>
);

export const IconDoc = (p: IconProps) => (
  <Svg {...p}>
    <path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5L13.5 3Z" />
    <path d="M13.5 3v5.5H19M8.5 13h7M8.5 16.5h5" />
  </Svg>
);

export const IconPlus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 12.5 9 17 19.5 6.5" />
  </Svg>
);

export const IconX = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);

export const IconChevronDown = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 9.5 12 15.5 18 9.5" />
  </Svg>
);

export const IconChevronLeft = (p: IconProps) => (
  <Svg {...p}>
    <path d="M14.5 6 8.5 12l6 6" />
  </Svg>
);

export const IconChevronRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9.5 6l6 6-6 6" />
  </Svg>
);

export const IconTrash = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v5M14 11v5" />
  </Svg>
);

export const IconEdit = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3Z" />
    <path d="M15 6l3 3" />
  </Svg>
);

export const IconSearch = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="M15.8 15.8 20 20" />
  </Svg>
);

/**
 * Globo, para "pesquisar na internet".
 *
 * Não reaproveitei `IconSearch`: a lupa já significa "buscar na
 * conversa" na lista de conversas, e dois sentidos para o mesmo glifo
 * na mesma tela confunde. Globo diz "fora daqui".
 */
export const IconGlobe = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17" />
    <path d="M12 3.5c2.2 2.3 3.4 5.3 3.4 8.5S14.2 18.2 12 20.5c-2.2-2.3-3.4-5.3-3.4-8.5S9.8 5.8 12 3.5Z" />
  </Svg>
);

export const IconMenu = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Svg>
);

export const IconLogout = (p: IconProps) => (
  <Svg {...p}>
    <path d="M14 4h4.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H14" />
    <path d="M10 8l-4 4 4 4M6 12h9" />
  </Svg>
);

export const IconUndo = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 9h9.5a5.5 5.5 0 1 1 0 11H8" />
    <path d="M8 4.5 4 9l4 4.5" />
  </Svg>
);

export const IconRefresh = (p: IconProps) => (
  <Svg {...p}>
    <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
    <path d="M20 4.5V10h-5.4" />
  </Svg>
);

/* ---------------- domínio ---------------- */

/** Evento FIXED: não se mexe (§5.6). */
export const IconLock = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5" y="11" width="14" height="9" rx="2" />
    <path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3" />
  </Svg>
);

/** Evento FLEXIBLE: pode mover. */
export const IconUnlock = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5" y="11" width="14" height="9" rx="2" />
    <path d="M8.5 11V8a3.5 3.5 0 0 1 6.6-1.7" />
  </Svg>
);

export const IconClock = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Svg>
);

/** Prazo sem horário reservado (§5.9.5): o aviso da lista de tarefas. */
export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4.5 2.8 20h18.4L12 4.5Z" />
    <path d="M12 10v4.5M12 17.3v.2" />
  </Svg>
);

export const IconInfo = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 7.8v.2" />
  </Svg>
);

export const IconSparkle = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.5l1.7 4.8L18.5 10l-4.8 1.7L12 16.5l-1.7-4.8L5.5 10l4.8-1.7L12 3.5Z" />
    <path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z" />
  </Svg>
);

export const IconPause = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9.5 5.5v13M14.5 5.5v13" />
  </Svg>
);

export const IconPlay = (p: IconProps) => (
  <Svg {...p}>
    <path d="M7.5 5.2v13.6L19 12 7.5 5.2Z" />
  </Svg>
);

export const IconSkip = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 7l6 5-6 5V7ZM14 7l6 5-6 5V7Z" />
  </Svg>
);

export const IconPin = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 13.5V21" />
    <path d="M8 3.5h8l-1 6 2.5 2.5h-11L9 9.5l-1-6Z" />
  </Svg>
);

export const IconArchive = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3.5" y="4.5" width="17" height="4" rx="1.3" />
    <path d="M5 8.5V19a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5M10 13h4" />
  </Svg>
);

export const IconDownload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4v10M8 10.5l4 4 4-4M5 19.5h14" />
  </Svg>
);

export const IconShield = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.2 5 5.8v5.4c0 4 2.9 7.6 7 9.6 4.1-2 7-5.6 7-9.6V5.8L12 3.2Z" />
  </Svg>
);

export const IconKey = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="12" r="3.5" />
    <path d="M11.5 12H21M17 12v3M19.5 12v2.2" />
  </Svg>
);

export const IconLink = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10 13.8a3.6 3.6 0 0 0 5.1 0l2.9-2.9a3.6 3.6 0 0 0-5.1-5.1l-1 1" />
    <path d="M14 10.2a3.6 3.6 0 0 0-5.1 0L6 13.1a3.6 3.6 0 0 0 5.1 5.1l1-1" />
  </Svg>
);

export const IconSun = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.8v1.9M12 19.3v1.9M4.3 12H2.4M21.6 12h-1.9M6.5 6.5 5.2 5.2M18.8 18.8l-1.3-1.3M6.5 17.5l-1.3 1.3M18.8 5.2l-1.3 1.3" />
  </Svg>
);

export const IconMoon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
  </Svg>
);

export const IconEye = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" />
    <circle cx="12" cy="12" r="3" />
  </Svg>
);

export const IconEyeOff = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10.7 5.1A9.8 9.8 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-2.6 3.5" />
    <path d="M6.6 6.6A17.5 17.5 0 0 0 2 12s3.6 7 10 7a9.6 9.6 0 0 0 5.4-1.6" />
    <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
    <path d="M3 3l18 18" />
  </Svg>
);

export const IconMonitor = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="12" rx="2" />
    <path d="M8 20h8M12 16v4" />
  </Svg>
);

export const IconMail = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="m3.5 6.5 8.5 6 8.5-6" />
  </Svg>
);
