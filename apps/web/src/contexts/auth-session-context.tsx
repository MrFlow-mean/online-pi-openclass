"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { UserView } from "@/types";

const AuthSessionContext = createContext<UserView | null>(null);

export function AuthSessionProvider({
  user,
  children,
}: {
  user: UserView;
  children: ReactNode;
}) {
  return (
    <AuthSessionContext.Provider value={user}>
      {children}
    </AuthSessionContext.Provider>
  );
}

export function useAuthenticatedUser() {
  const user = useContext(AuthSessionContext);
  if (!user) {
    throw new Error("useAuthenticatedUser must be used inside AuthGate");
  }
  return user;
}
