import type { MetadataRoute } from "next";

/**
 * The web app manifest (M18.3 §9).
 *
 * The Core is meant to be left open — on a second screen, on a tablet against
 * a wall — and a browser's own chrome around a full-viewport living presence
 * is exactly the thing the owner asked to be rid of. `standalone` lets the
 * owner install it and get the stage without the address bar.
 *
 * Three deliberate limits:
 *
 * - **`start_url` is `/core`, not `/`.** Installing this installs the Core.
 * - **No `display_override: ["fullscreen"]`.** Fullscreen stays something the
 *   owner asks for with a gesture, on a control that says how to leave; a
 *   manifest that opened fullscreen on launch would be the same bypass by
 *   another route.
 * - **No service worker, no offline cache.** The Core's whole claim is that it
 *   shows what is true NOW; a cached shell that renders yesterday's state
 *   would be the most expensive lie in the product. When the API cannot be
 *   reached the page says so, which is the honest offline behaviour.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Personal Agent OS — Çekirdek",
    short_name: "Çekirdek",
    description:
      "Tek sahipli kişisel ajan işletim sisteminin canlı çekirdeği. Yalnızca bildirilen durumu gösterir.",
    start_url: "/core",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#06050a",
    theme_color: "#06050a",
    lang: "tr",
    dir: "ltr",
    categories: ["productivity", "utilities"],
    icons: [
      {
        src: "/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
        purpose: "any",
      },
      {
        src: "/icon-maskable.svg",
        sizes: "any",
        type: "image/svg+xml",
        purpose: "maskable",
      },
    ],
  };
}
