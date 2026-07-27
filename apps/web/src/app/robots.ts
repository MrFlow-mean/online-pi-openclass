import type { MetadataRoute } from "next";

import { publicSiteOrigin } from "@/lib/public-site";

export default function robots(): MetadataRoute.Robots {
  const origin = publicSiteOrigin();
  return {
    rules: { userAgent: "*", allow: ["/", "/privacy", "/terms", "/security", "/trending", "/community", "/courses/shared/"], disallow: ["/api/", "/admin", "/profile", "/studio", "/wallet", "/auth/"] },
    sitemap: `${origin}/sitemap.xml`,
    host: origin,
  };
}
