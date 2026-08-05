import type { UserView } from "@/types";

const SYNTHETIC_EMAIL_SUFFIXES = ["@phone.openclass.local", "@oauth.openclass.local", "@guest.openclass.local"];

export function isSyntheticEmail(email: string | null | undefined) {
  return Boolean(email && SYNTHETIC_EMAIL_SUFFIXES.some((suffix) => email.endsWith(suffix)));
}

export function userPublicEmail(user: UserView | null | undefined) {
  return user?.email && !isSyntheticEmail(user.email) ? user.email : "";
}

export function userAccountLabel(user: UserView | null | undefined) {
  if (!user) {
    return "account";
  }
  if (user.role === "guest") {
    return "guest mode";
  }
  if (user.phone) {
    return user.phone;
  }
  const publicEmail = userPublicEmail(user);
  if (publicEmail) {
    return publicEmail;
  }
  const oauthIdentity = user.auth_identities.find((identity) => identity.provider !== "email");
  return oauthIdentity?.display_name || user.display_name?.trim() || oauthIdentity?.provider_label || user.email;
}

export function userDisplayName(user: UserView | null | undefined) {
  if (!user) {
    return "OpenClass User";
  }
  if (user.role === "guest") {
    return "tourists";
  }
  if (user.display_name?.trim()) {
    return user.display_name.trim();
  }
  if (user.phone) {
    return user.phone;
  }
  const publicEmail = userPublicEmail(user);
  return publicEmail ? publicEmail.split("@", 1)[0] : userAccountLabel(user);
}

export function userInitial(user: UserView | null | undefined) {
  return userDisplayName(user).trim().slice(0, 1).toUpperCase();
}
