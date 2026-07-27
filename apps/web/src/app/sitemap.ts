import type { MetadataRoute } from "next";

import { publicSiteOrigin } from "@/lib/public-site";

export default function sitemap(): MetadataRoute.Sitemap {
  const origin = publicSiteOrigin();
  return [
    { url: `${origin}/trending`, changeFrequency: "daily", priority: 0.8 },
    { url: `${origin}/community`, changeFrequency: "daily", priority: 0.8 },
    { url: `${origin}/privacy`, changeFrequency: "yearly", priority: 0.4 },
    { url: `${origin}/terms`, changeFrequency: "yearly", priority: 0.4 },
    { url: `${origin}/security`, changeFrequency: "monthly", priority: 0.5 },
  ];
}
