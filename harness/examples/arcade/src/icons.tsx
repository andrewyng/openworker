import React from "react";
export function Icon({
  name,
  size = 20,
  ...props
}: {
  name: string;
  size?: number;
  className?: string;
}) {
  const paths: Record<string, React.ReactNode> = {
    cube: (
      <>
        <path d="m12 2 9 5v10l-9 5-9-5V7Zm0 10 9-5M12 12 3 7m9 5v10" />
      </>
    ),
    fullscreen: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />,
    code: <path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18" />,
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="2" />
        <rect x="14" y="3" width="7" height="7" rx="2" />
        <rect x="3" y="14" width="7" height="7" rx="2" />
        <rect x="14" y="14" width="7" height="7" rx="2" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    play: <path d="m9 5 11 7-11 7Z" />,
    arrow: <path d="m5 12 14 0m-6-6 6 6-6 6" />,
    back: <path d="m15 5-7 7 7 7" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    down: <path d="m6 9 6 6 6-6" />,
    send: <path d="m4 11 8-8 8 8M12 3v18" />,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    settings: (
      <>
        <path d="M4 7h16M4 17h16" />
        <circle cx="9" cy="7" r="3" fill="currentColor" />
        <circle cx="15" cy="17" r="3" fill="currentColor" />
      </>
    ),
    game: (
      <>
        <path d="M7 7h10c4 0 6 13 2 13-2 0-3-4-5-4h-4c-2 0-3 4-5 4C1 20 3 7 7 7Z" />
        <path d="M7 10v5m-2.5-2.5h5M16 11h.01M18 14h.01" />
      </>
    ),
    sparkle: (
      <path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5Z" />
    ),
    copy: (
      <>
        <rect x="8" y="8" width="13" height="13" rx="3" />
        <path d="M16 5V3H3v13h2" />
      </>
    ),
    download: <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />,
    folder: <path d="M3 6h7l2 3h9v12H3Z" />,
    check: <path d="m5 12 4 4L20 5" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    search: (
      <>
        <circle cx="10" cy="10" r="7" />
        <path d="m15 15 6 6" />
      </>
    ),
    rotate: (
      <>
        <path d="M3 11a9 9 0 1 1 2 7M3 4v7h7" />
      </>
    ),
    cloud: <path d="M7 19a5 5 0 0 1-1-10 7 7 0 0 1 13 1 4.5 4.5 0 0 1-1 9Z" />,
    computer: (
      <>
        <rect x="3" y="3" width="18" height="13" rx="2" />
        <path d="M12 16v5M7 21h10" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name] ?? paths.sparkle}
    </svg>
  );
}
