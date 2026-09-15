// Connector logo registry — maps a stable `logo` id (from a connector's API descriptor) to the
// brand's real monochrome mark, plus a human label. The brand color is deliberately NOT stored
// here: it comes from the API (`brand_color`) so the descriptor stays the single source of truth.
// Unknown / empty ids resolve to FALLBACK (a neutral plug glyph).
//
// Most marks come from the `simple-icons` package: official 24x24 single-path monochrome brand
// glyphs that paint with `currentColor`, so ConnectorIcon / ConnectorBadge tint them with the
// connector's brand color. Slack, Salesforce, Outlook, and Canva are no longer distributed by
// the package (removed at the trademark holders' request), so their path data is vendored below in
// the same format. Brands with no published monochrome mark (Attio, Apollo.io, Hunter,
// Amplitude, Descript, Clay, Close, Docusign — whose current post-rebrand mark no icon pack
// ships) and the non-brand utilities (email, browser, MCP, fallback plug) keep simple custom
// glyphs. (Filename is `.tsx` because the entries are JSX — the spec's `registry.ts` can't hold
// JSX.)

import type { SimpleIcon } from "simple-icons";
import {
  siAsana,
  siBox,
  siClickup,
  siConfluence,
  siDatadog,
  siDiscord,
  siDropbox,
  siFigma,
  siGithub,
  siGitlab,
  siGmail,
  siGooglecalendar,
  siGoogledrive,
  siHubspot,
  siJira,
  siLinear,
  siMixpanel,
  siNotion,
  siPagerduty,
  siPosthog,
  siQuickbooks,
  siStripe,
  siTelegram,
  siWhatsapp,
  siZendesk,
} from "simple-icons";

// `JSX` is global with the react-jsx runtime + @types/react.
type LogoComponent = () => JSX.Element;

export interface ConnectorRegistryEntry {
  label: string;
  logo: LogoComponent;
}

/** 24x24 single-path brand mark (simple-icons path format) painting with `currentColor`. */
function pathLogo(d: string): LogoComponent {
  return () => (
    <svg viewBox="0 0 24 24" width="100%" height="100%" fill="currentColor" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

function brand(icon: SimpleIcon): ConnectorRegistryEntry {
  return { label: icon.title, logo: pathLogo(icon.path) };
}

// Path data vendored from simple-icons v9 (CC0) — these brands were later removed from the
// package and can't be imported from v16.
const SLACK_PATH =
  "M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z";

const SALESFORCE_PATH =
  "M10.006 5.415a4.195 4.195 0 013.045-1.306c1.56 0 2.954.9 3.69 2.205.63-.3 1.35-.45 2.1-.45 2.85 0 5.159 2.34 5.159 5.22s-2.31 5.22-5.176 5.22c-.345 0-.69-.044-1.02-.104a3.75 3.75 0 01-3.3 1.95c-.6 0-1.155-.15-1.65-.375A4.314 4.314 0 018.88 20.4a4.302 4.302 0 01-4.05-2.82c-.27.062-.54.076-.825.076-2.204 0-4.005-1.8-4.005-4.05 0-1.5.811-2.805 2.01-3.51-.255-.57-.39-1.2-.39-1.846 0-2.58 2.1-4.65 4.65-4.65 1.53 0 2.85.705 3.72 1.8";

const CANVA_PATH =
  "M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zM6.962 7.68c.754 0 1.337.549 1.405 1.2.069.583-.171 1.097-.822 1.406-.343.171-.48.172-.549.069-.034-.069 0-.137.069-.206.617-.514.617-.926.548-1.508-.034-.378-.308-.618-.583-.618-1.2 0-2.914 2.674-2.674 4.629.103.754.549 1.646 1.509 1.646.308 0 .65-.103.96-.24.5-.264.799-.47 1.097-.8-.073-.885.704-2.046 1.851-2.046.515 0 .926.205.96.583.068.514-.377.582-.514.582s-.378-.034-.378-.17c-.034-.138.309-.07.275-.378-.035-.206-.24-.274-.446-.274-.72 0-1.131.994-1.029 1.611.035.275.172.549.447.549.205 0 .514-.31.617-.755.068-.308.343-.514.583-.514.102 0 .17.034.205.171v.138c-.034.137-.137.548-.102.651 0 .069.034.171.17.171.092 0 .436-.18.777-.459.117-.59.253-1.298.253-1.357.034-.24.137-.48.617-.48.103 0 .171.034.205.171v.138l-.136.617c.445-.583 1.097-.994 1.508-.994.172 0 .309.102.309.274 0 .103 0 .274-.069.446-.137.377-.309.96-.412 1.474 0 .137.035.274.207.274.171 0 .685-.206 1.096-.754l.007-.004c-.002-.068-.007-.134-.007-.202 0-.411.035-.754.104-.994.068-.274.411-.514.617-.514.103 0 .205.069.205.171 0 .035 0 .103-.034.137-.137.446-.24.857-.24 1.269 0 .24.034.582.102.788 0 .034.035.069.07.069.068 0 .548-.445.89-1.028-.308-.206-.48-.549-.48-.96 0-.72.446-1.097.858-1.097.343 0 .617.24.617.72 0 .308-.103.65-.274.96h.102a.77.77 0 0 0 .584-.24.293.293 0 0 1 .134-.117c.335-.425.83-.74 1.41-.74.48 0 .924.205.959.582.068.515-.378.618-.515.618l-.002-.002c-.138 0-.377-.035-.377-.172 0-.137.309-.068.274-.376-.034-.206-.24-.275-.446-.275-.686 0-1.13.891-1.028 1.611.034.275.171.583.445.583.206 0 .515-.308.652-.754.068-.274.343-.514.583-.514.103 0 .17.034.205.171 0 .069 0 .206-.137.652-.17.308-.171.48-.137.617.034.274.171.48.309.583.034.034.068.102.068.102 0 .069-.034.138-.137.138-.034 0-.068 0-.103-.035-.514-.205-.72-.548-.789-.891-.205.24-.445.377-.72.377-.445 0-.89-.411-.96-.926a1.609 1.609 0 0 1 .075-.649c-.203.13-.422.203-.623.203h-.17c-.447.652-.927 1.098-1.27 1.303a.896.896 0 0 1-.377.104c-.068 0-.171-.035-.205-.104-.095-.152-.156-.392-.193-.667-.481.527-1.145.805-1.453.805-.343 0-.548-.206-.582-.55v-.376c.102-.754.377-1.2.377-1.337a.074.074 0 0 0-.069-.07c-.24 0-1.028.824-1.166 1.373l-.103.445c-.068.309-.377.515-.582.515-.103 0-.172-.035-.206-.172v-.137l.046-.233c-.435.31-.87.508-1.075.508-.308 0-.48-.172-.514-.412-.206.274-.445.412-.754.412-.352 0-.696-.24-.862-.593-.244.275-.523.553-.852.764-.48.309-1.028.549-1.68.549-.582 0-1.097-.309-1.371-.583-.412-.377-.651-.96-.686-1.509-.205-1.68.823-3.84 2.4-4.8.378-.205.755-.343 1.132-.343zm9.77 3.291c-.104 0-.172.172-.172.343 0 .274.137.583.309.755a1.74 1.74 0 0 0 .102-.583c0-.343-.137-.515-.24-.515z";

const OUTLOOK_PATH =
  "M7.88 12.04q0 .45-.11.87-.1.41-.33.74-.22.33-.58.52-.37.2-.87.2t-.85-.2q-.35-.21-.57-.55-.22-.33-.33-.75-.1-.42-.1-.86t.1-.87q.1-.43.34-.76.22-.34.59-.54.36-.2.87-.2t.86.2q.35.21.57.55.22.34.31.77.1.43.1.88zM24 12v9.38q0 .46-.33.8-.33.32-.8.32H7.13q-.46 0-.8-.33-.32-.33-.32-.8V18H1q-.41 0-.7-.3-.3-.29-.3-.7V7q0-.41.3-.7Q.58 6 1 6h6.5V2.55q0-.44.3-.75.3-.3.75-.3h12.9q.44 0 .75.3.3.3.3.75V10.85l1.24.72h.01q.1.07.18.18.07.12.07.25zm-6-8.25v3h3v-3zm0 4.5v3h3v-3zm0 4.5v1.83l3.05-1.83zm-5.25-9v3h3.75v-3zm0 4.5v3h3.75v-3zm0 4.5v2.03l2.41 1.5 1.34-.8v-2.73zM9 3.75V6h2l.13.01.12.04v-2.3zM5.98 15.98q.9 0 1.6-.3.7-.32 1.19-.86.48-.55.73-1.28.25-.74.25-1.61 0-.83-.25-1.55-.24-.71-.71-1.24t-1.15-.83q-.68-.3-1.55-.3-.92 0-1.64.3-.71.3-1.2.85-.5.54-.75 1.3-.25.74-.25 1.63 0 .85.26 1.56.26.72.74 1.23.48.52 1.17.81.69.3 1.56.3zM7.5 21h12.39L12 16.08V17q0 .41-.3.7-.29.3-.7.3H7.5zm15-.13v-7.24l-5.9 3.54Z";

/** Shared shell for the custom stroke glyphs (utilities + brands with no published mono mark). */
function strokeLogo(children: JSX.Element): LogoComponent {
  return () => (
    <svg
      viewBox="0 0 24 24"
      width="100%"
      height="100%"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

const EmailLogo = strokeLogo(
  <>
    <rect x="3" y="5" width="18" height="14" rx="2.5" />
    <path d="M3.5 7.5 12 13.5l8.5-6" />
  </>,
);

const BrowserLogo = strokeLogo(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a13.5 13.5 0 0 1 0 18M12 3a13.5 13.5 0 0 0 0 18" />
  </>,
);

const McpLogo = strokeLogo(
  <>
    <circle cx="12" cy="12" r="2.2" />
    <circle cx="5" cy="6" r="1.8" />
    <circle cx="19" cy="6" r="1.8" />
    <circle cx="12" cy="20" r="1.8" />
    <path d="M10.3 10.6 6.4 7.3M13.7 10.6l3.9-3.3M12 14.2V18.2" />
  </>,
);

const AttioLogo = strokeLogo(
  <>
    <path d="M4.5 19.5 12 4.5l7.5 15M7.6 13.4h8.8" />
  </>,
);

// monday.com's mark is three staggered capsule bars; no simple-icons mono mark exists.
const MondayLogo = strokeLogo(
  <>
    <path d="M4.5 7.5h9M4.5 12h15M4.5 16.5h6.5" />
  </>,
);

// TinyFish's official fish icon (ux-labs docs/logo — black monochrome master
// "TF_ICON BLK1.svg"): a single even-odd path, so it's one color that ConnectorIcon tints
// with brand_color (#ff6700), and the eyes + body read as negative space. 440x406 source viewBox.
const TinyFishLogo: LogoComponent = () => (
  <svg
    viewBox="0 0 440 406"
    width="100%"
    height="100%"
    fill="currentColor"
    fillRule="evenodd"
    aria-hidden="true"
  >
    <path d="M202.22 0.06C206.48 0.06 210.73 0.06 214.99 0.06C216.01 1.28 218.6 0.91 220.11 1.3C224.21 2.35 228.15 3.59 232.07 5.14C244.96 10.25 258.07 17.26 269.54 25.13C283.96 35.03 297.37 45.85 309.93 58.08C313.93 61.97 318.01 66.07 321.67 70.27C323.86 72.78 326.47 77.14 329.36 78.8C331.48 80.02 334.31 80.37 336.61 81.23C341.54 83.08 346.63 84.97 351.36 87.28C366.02 94.41 378.91 103.3 391.73 113.31C409.64 127.3 423.14 148.38 431.77 168.96C434.48 175.45 437.01 183.1 437.97 190.1C438.26 192.21 438.4 198.74 439.94 199.95C439.94 209.61 439.94 219.27 439.94 228.94C438.66 230.15 439.06 232.8 438.77 234.46C437.99 238.98 437.02 243.58 435.77 247.97C432.37 259.93 427.09 271.28 420.72 281.96C407.79 303.66 388.19 321.9 366.57 334.72C360.94 338.06 355.01 341.2 349.02 343.8C346.12 345.07 341.96 345.81 339.44 347.73C338.3 348.59 331.82 361.88 329.78 364.72C317.3 382.1 289.82 401.83 267.45 393.88C263.57 392.5 259.66 389.71 257.26 386.33C255.92 384.43 255.27 381.38 253.39 380.11C245.17 389.31 233.3 396.43 221.9 400.8C217.47 402.49 212.85 403.83 208.21 404.81C206.69 405.13 204.04 404.71 203.02 405.97C198.37 405.97 193.72 405.97 189.07 405.97C187.53 404.34 183 404.55 180.81 403.8C176.02 402.18 171.53 399.68 167.58 396.52C153.91 385.56 152.42 366 159.19 350.8C161.08 346.54 163.47 342.28 166.14 338.46C167.77 336.13 170.39 333.96 171.46 331.35C168.52 328.05 155.93 321.21 151.32 317.81C143.24 311.86 135.31 305.22 128.21 298.09C125.27 295.14 122.55 291.84 119.46 289.08C115.24 292.67 112.37 298.46 108.79 302.72C101.67 311.21 93.65 319.19 84.66 325.72C63.52 341.06 21.37 351.03 9.18 319.4C7.26 314.41 6.38 308.51 6.17 303.18C5.43 284.18 11.11 263.87 18.24 246.52C20.54 240.93 22.53 235.01 25.27 229.62C26.3 227.61 28.95 223.62 28.83 221.36C28.66 218.22 20.87 203.57 19.18 199.44C11.33 180.28 3.94 160 1.87 139.14C0.85 128.87 0.41 117.55 5.26 108.12C12.15 94.74 27.67 91.47 41.49 93.83C64.1 97.7 83.3 113.87 96.76 131.92C100.56 137.01 104.5 142.09 107.8 147.53C108.98 149.47 110.05 152.12 111.74 153.63C114.58 152.92 122.46 142.01 125.2 139.28C132.58 131.92 140.21 124.56 148.5 118.21C153.19 114.62 158.44 111.43 162.83 107.49C157.08 90.84 145.97 62.26 168.21 52.25C173.85 49.72 179.58 50.02 185.54 49.21C185.24 45.51 182.26 41.55 181.15 37.92C178.06 27.75 178.91 14.77 187.27 7.23C189.64 5.09 192.66 3.16 195.69 2.13C197.32 1.58 201.27 1.37 202.22 0.06ZM303.64 71.98C302.39 68.63 291.03 59.35 287.75 56.73C269.7 42.26 250.7 30.69 229.26 22.21C222.6 19.57 200.46 12.71 199.12 24.51C203.69 27.57 209.78 28.96 214.78 31.26C226.34 36.58 237.73 42.39 248.46 49.31C254.08 52.93 259.68 56.84 264.73 61.23C266.79 63.03 271.74 69.02 273.91 69.76C276.67 70.71 290.88 70.72 295.21 71.22C297.82 71.52 301.18 72.78 303.64 71.98ZM276.96 87.01C273.79 87.3 269.56 90.2 266.74 91.69C258.26 96.18 250.17 100.98 242.53 106.88C198.49 140.91 182.56 198.1 196.16 251.43C199.86 265.92 205.82 279.64 213.22 292.61C214.82 295.4 217.12 300.77 219.76 302.64C221.85 304.11 225.3 303.21 225.99 306.78C227.15 312.82 218.08 316.26 214.15 319.33C203.64 327.56 192.3 336.52 185.73 348.41C184.15 351.28 179.72 356.82 180.34 360.27C180.95 363.61 190.96 368.84 193.93 369.83C216.96 377.5 245.82 354.54 254.71 334.39C256.79 329.66 258.53 323.95 258.03 318.7C257.92 317.51 257.12 316.72 257.09 315.53C257.05 313.66 258.32 312.16 259.98 311.5C265.95 309.1 266.76 319.69 269.72 321.75C274.1 324.81 289.05 327.28 294.6 327.86C305.92 329.03 319.37 330.9 330.56 328C351.45 322.57 380.34 303.8 394.78 287.66C406.45 274.61 415.35 260.08 420.83 243.45C423.88 234.22 424.76 224.47 425.18 214.79C427.84 152.61 373.73 107.95 318.76 92.84C307.31 89.69 288.71 85.94 276.96 87.01ZM89.74 187.46C92.03 186.59 93.17 181.07 94.15 178.89C95.4 176.12 99.38 169.34 99.25 166.54C99.13 164.07 93.74 157.36 92.21 155C81.37 138.16 66.56 121.83 47.65 114.26C41.59 111.83 30.95 109.5 25.05 113.26C20.51 116.14 19.84 134.28 21.42 138.84C24.08 135.9 25.57 132.74 29.55 131.22C35.95 128.77 44.06 132.2 49.32 135.74C62.51 144.64 72.91 157.24 80.87 170.96C82.88 174.42 87.49 185.43 89.74 187.46ZM339.23 184.68C340.37 183.83 341.75 183.53 342.68 182.3C346.32 177.46 344.04 168.75 339.29 165.36C337.58 164.14 335.91 164.3 333.99 163.84C331.38 153.13 341.05 137.22 353.3 143.24C364.89 148.93 370.39 163.38 371.66 175.42C372.48 183.17 370.83 194.08 363.68 198.69C355.45 203.99 340.38 193.28 339.23 184.68ZM284.21 195.88C285.76 194.37 287.79 194.04 288.8 191.7C292.32 183.45 286.82 174.75 278.46 173.88C275.72 162.82 285.08 146.58 297.83 151.2C309.09 155.28 315.95 169.65 317.84 180.61C319.52 190.34 319.55 204.99 309.82 210.83C301.02 216.1 291.45 207.81 286.78 200.89C285.76 199.38 284.29 197.7 284.21 195.88ZM292.37 247.12C294.88 246.56 297.52 247.02 300.09 246.65C306.22 245.77 312.03 244.16 317.78 241.91C327.96 237.92 337.09 230.56 347.95 228.09C353.06 226.93 364.92 223.19 366.94 231.03C369.08 239.29 362.97 247.43 358.47 253.68C350.4 264.9 336.11 276.01 322.09 277.91C306.41 280.05 297.41 271.83 293.81 257.23C293.05 254.14 291.88 250.3 292.37 247.12Z" />
  </svg>
);

const AmplitudeLogo = strokeLogo(
  <>
    <path d="M2.5 13.5h4l3-8 4.5 13 3-8h4.5" />
  </>,
);

const ApolloLogo = strokeLogo(
  <>
    <circle cx="10" cy="14" r="6" />
    <path d="M14.5 9.5 21 3M16.5 3H21v4.5" />
  </>,
);

const DescriptLogo = strokeLogo(
  <>
    <path d="M4 5.5h16M4 10h11M4 14.5h14M4 19h8" />
  </>,
);

const ClayLogo = strokeLogo(
  <>
    <path d="M3 19a9 9 0 0 1 18 0zM1.5 19h21" />
  </>,
);

const CloseLogo = strokeLogo(
  <>
    <ellipse cx="12" cy="12" rx="9" ry="3.8" />
    <ellipse cx="12" cy="12" rx="9" ry="3.8" transform="rotate(60 12 12)" />
    <ellipse cx="12" cy="12" rx="9" ry="3.8" transform="rotate(120 12 12)" />
  </>,
);

const DocusignLogo = strokeLogo(
  <>
    <path d="M14.5 5.5 18.5 9.5 8 20H4v-4L14.5 5.5zM12.5 7.5l4 4" />
    <path d="M4 21.5h16" />
  </>,
);

const HunterLogo = strokeLogo(
  <>
    <circle cx="12" cy="12" r="7" />
    <path d="M12 2.5V6M12 18v3.5M2.5 12H6M18 12h3.5" />
    <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
  </>,
);

const PlugLogo = strokeLogo(
  <>
    <path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7zM12 16v5" />
  </>,
);

/** Neutral fallback for unknown / empty logo ids. */
export const FALLBACK: ConnectorRegistryEntry = { label: "Connector", logo: PlugLogo };

export const CONNECTORS: Record<string, ConnectorRegistryEntry> = {
  // Real brand marks from simple-icons.
  asana: brand(siAsana),
  box: brand(siBox),
  clickup: brand(siClickup),
  confluence: brand(siConfluence),
  datadog: brand(siDatadog),
  discord: brand(siDiscord),
  dropbox: brand(siDropbox),
  figma: brand(siFigma),
  github: brand(siGithub),
  gitlab: brand(siGitlab),
  gmail: brand(siGmail),
  google_calendar: brand(siGooglecalendar),
  google_drive: brand(siGoogledrive),
  hubspot: brand(siHubspot),
  jira: brand(siJira),
  linear: brand(siLinear),
  mixpanel: brand(siMixpanel),
  notion: brand(siNotion),
  pagerduty: brand(siPagerduty),
  posthog: brand(siPosthog),
  quickbooks: brand(siQuickbooks),
  stripe: brand(siStripe),
  telegram: brand(siTelegram),
  whatsapp: brand(siWhatsapp),
  zendesk: brand(siZendesk),
  // Real brand marks vendored from simple-icons v9.
  slack: { label: "Slack", logo: pathLogo(SLACK_PATH) },
  salesforce: { label: "Salesforce", logo: pathLogo(SALESFORCE_PATH) },
  outlook: { label: "Outlook", logo: pathLogo(OUTLOOK_PATH) },
  canva: { label: "Canva", logo: pathLogo(CANVA_PATH) },
  // No published monochrome mark — custom glyphs, tinted with the real brand color.
  attio: { label: "Attio", logo: AttioLogo },
  monday: { label: "monday.com", logo: MondayLogo },
  tinyfish: { label: "TinyFish", logo: TinyFishLogo },
  descript: { label: "Descript", logo: DescriptLogo },
  clay: { label: "Clay", logo: ClayLogo },
  close: { label: "Close", logo: CloseLogo },
  docusign: { label: "Docusign", logo: DocusignLogo },
  amplitude: { label: "Amplitude", logo: AmplitudeLogo },
  apollo: { label: "Apollo.io", logo: ApolloLogo },
  hunter: { label: "Hunter", logo: HunterLogo },
  // Non-brand utilities.
  email: { label: "Email", logo: EmailLogo },
  browser: { label: "Browser", logo: BrowserLogo },
  mcp: { label: "MCP", logo: McpLogo },
};

/**
 * Resolve a logo id to its registry entry plus the matched key. Unknown / empty ids return the
 * FALLBACK entry with key `"fallback"` (so callers and tests can distinguish a hit from a miss).
 */
export function resolveConnector(logo?: string): { key: string; entry: ConnectorRegistryEntry } {
  const id = (logo ?? "").trim();
  if (id && Object.prototype.hasOwnProperty.call(CONNECTORS, id)) {
    return { key: id, entry: CONNECTORS[id] };
  }
  return { key: "fallback", entry: FALLBACK };
}
