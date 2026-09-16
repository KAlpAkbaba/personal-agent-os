"use client";

/**
 * B23 req 685: the six pages, on every page.
 *
 * `layout.tsx` was `<body>{children}</body>` and nothing else, so the only way between
 * pages was whatever links a page happened to carry. Measured before this batch:
 * `/` linked to all five, `/research` linked to `/artifacts`, `/voice` linked to `/core`,
 * and `/artifacts`, `/core` and `/core/cockpit` linked NOWHERE. The matrix calls
 * `/artifacts` a dead end and that is exactly what it was — the page an owner reaches from
 * a notification, with no way back that is not the browser's own button. Installed as a
 * PWA (req 716) there is no browser button: `start_url` is `/core`, and the owner arrived
 * in a window with no address bar and no links.
 *
 * **One list, rendered once.** The destinations live in `NAV` and every surface reads it,
 * so a page added later is added in one place and cannot be half-linked. The test asserts
 * the count, which is what makes "hiçbir sayfa çıkmaz sokak olmasın" a property rather
 * than a habit.
 *
 * **The Core is not framed.** The manifest's own rule is that the browser's chrome around
 * a full-viewport living presence is the thing the owner asked to be rid of, so on `/core`
 * and `/core/cockpit` this renders as a single quiet affordance that opens the list, not
 * as a bar across the top. Same links, same component, different weight — the alternative
 * (no nav on the Core) is how the Core became a dead end in the first place.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

export type NavItem = {
  href: string;
  label: string;
  /** What this page is for, in one phrase — the title attribute and the wide layout. */
  hint: string;
  /**
   * B24. `main` is the shell an owner moves through; `family` is one subsystem's own
   * page. Two rows rather than one list of fourteen — but ONE list in the source, so the
   * property B23 pinned ("no page is a dead end") still holds by construction and a
   * fifteenth page is still added in exactly one place.
   */
  group: "main" | "family";
};

/** The product's pages, in the order an owner meets them. */
export const NAV: readonly NavItem[] = [
  { href: "/", label: "Giriş", hint: "Durum ve hızlı bağlantılar", group: "main" },
  { href: "/core", label: "Çekirdek", hint: "Canlı çekirdek — sade", group: "main" },
  { href: "/core/cockpit", label: "Kokpit", hint: "Paneller ve ayrıntı", group: "main" },
  { href: "/voice", label: "Ses", hint: "Sesli oturum ve tanılama", group: "main" },
  { href: "/research", label: "Araştırma", hint: "Araştırma başlat ve izle", group: "main" },
  { href: "/artifacts", label: "Belgeler", hint: "Raporlar ve çıktılar", group: "main" },
  { href: "/memory", label: "Hafıza", hint: "Hatırlananlar ve hafıza kaydı", group: "family" },
  { href: "/routines", label: "Rutinler", hint: "Kendi başına yaptıkları", group: "family" },
  { href: "/alarms", label: "Alarmlar", hint: "Kurulu alarmlar ve gerçekte ne oldu", group: "family" },
  { href: "/notifications", label: "Bildirimler", hint: "Size ulaşan ve ulaşamayan", group: "family" },
  { href: "/security", label: "Güvenlik", hint: "Yetkili varlıklar, bulgular, retler", group: "family" },
  { href: "/selfdev", label: "Gelişim", hint: "Kendini geliştirme boru hattı", group: "family" },
  { href: "/settings", label: "Ayarlar", hint: "Bu cihaz ve çalışan kurallar", group: "family" },
  { href: "/availability", label: "Özellik durumu", hint: "Ne var, ne çalışıyor, nasıl kanıtlandı", group: "family" },
];

/** The Core's own routes, where the nav must not become chrome. */
export function isCoreRoute(pathname: string): boolean {
  return pathname === "/core" || pathname.startsWith("/core/");
}

/**
 * Which entry is "here". A prefix match would light both `/core` and `/core/cockpit` on
 * the cockpit; the longest matching href wins instead, so exactly one is current.
 */
export function currentHref(pathname: string): string | null {
  const matches = NAV.filter(
    (item) => pathname === item.href || (item.href !== "/" && pathname.startsWith(`${item.href}/`)),
  );
  if (matches.length === 0) return null;
  return matches.reduce((best, item) => (item.href.length > best.href.length ? item : best)).href;
}

export default function SiteNav() {
  const pathname = usePathname() || "/";
  const here = currentHref(pathname);
  const quiet = isCoreRoute(pathname);

  return (
    <nav
      className={quiet ? "site-nav site-nav-quiet" : "site-nav"}
      data-site-nav
      data-nav-variant={quiet ? "quiet" : "bar"}
      aria-label="Site gezinmesi"
    >
      {(["main", "family"] as const).map((group) => (
        <ul key={group} className={`site-nav-list site-nav-${group}`} data-nav-group={group}>
          {NAV.filter((item) => item.group === group).map((item) => {
            const current = item.href === here;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  title={item.hint}
                  data-nav-item={item.href}
                  data-nav-current={current ? "yes" : "no"}
                  aria-current={current ? "page" : undefined}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      ))}
    </nav>
  );
}
