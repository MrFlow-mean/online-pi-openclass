const fallbackOrigin = "https://open-classes.com";

export function publicSiteOrigin() {
  const configured = process.env.NEXT_PUBLIC_SITE_URL?.trim() || process.env.OPENCLASS_PUBLIC_ORIGIN?.trim();
  if (!configured) {
    return fallbackOrigin;
  }
  try {
    return new URL(configured).origin;
  } catch {
    return fallbackOrigin;
  }
}

export function publicContactEmail() {
  return process.env.NEXT_PUBLIC_CONTACT_EMAIL?.trim() || "hello@open-classes.com";
}
