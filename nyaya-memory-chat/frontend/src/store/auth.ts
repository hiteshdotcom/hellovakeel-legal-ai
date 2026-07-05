import { create } from "zustand";
import { api } from "@/lib/api";
import type { Providers, User } from "@/lib/types";

interface AuthState {
  user: User | null;
  providers: Providers | null;
  checked: boolean; // initial /me + /providers resolved
  clerkBusy: boolean;
  authError: string;
  setUser: (u: User | null) => void;
  setAuthError: (e: string) => void;
  /** Boot: read providers + existing session, finalize Clerk redirect if present. */
  init: () => Promise<void>;
  logout: () => Promise<void>;
}

async function finalizeClerkRedirect(providers: Providers | null): Promise<User | null> {
  // Clerk owns the Google button; after redirect we exchange its session for our cookie.
  const onCallback = /\/sso-callback\/?$/.test(location.pathname);
  if (!providers?.clerk || !providers.clerk_frontend_api || !providers.clerk_publishable_key) {
    return null;
  }
  try {
    await loadClerk(providers);
    const Clerk = (window as unknown as { Clerk?: ClerkGlobal }).Clerk;
    if (!Clerk) return null;
    await Clerk.load();
    if (onCallback) {
      try {
        await Clerk.handleRedirectCallback({});
      } catch {
        /* fall through */
      }
    }
    if (Clerk.user && Clerk.session) {
      const token = await Clerk.session.getToken();
      if (token) {
        const { user } = await api.exchangeClerk(token);
        return user;
      }
    }
  } catch {
    /* Clerk optional — email/password still works */
  }
  return null;
}

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  providers: null,
  checked: false,
  clerkBusy: false,
  authError: "",
  setUser: (user) => set({ user }),
  setAuthError: (authError) => set({ authError }),

  init: async () => {
    // surface an OAuth error bounced back on the query string
    try {
      const ae = new URLSearchParams(location.search).get("auth_error");
      if (ae) {
        set({ authError: ae });
        history.replaceState({}, document.title, location.pathname);
      }
    } catch {
      /* ignore */
    }

    let providers: Providers | null = null;
    try {
      providers = await api.getProviders();
    } catch {
      /* providers optional */
    }
    set({ providers });

    // already signed in?
    try {
      const { user } = await api.getMe();
      set({ user, checked: true });
      return;
    } catch {
      /* not signed in — maybe a Clerk redirect is in flight */
    }

    const onCallback = /\/sso-callback\/?$/.test(location.pathname);
    if (providers?.clerk) {
      set({ clerkBusy: onCallback });
      const user = await finalizeClerkRedirect(providers);
      if (user) {
        set({ user, clerkBusy: false, checked: true });
        if (onCallback) history.replaceState({}, document.title, "/");
        return;
      }
    }
    set({ clerkBusy: false, checked: true });
  },

  logout: async () => {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    set({ user: null, authError: "" });
    void get; // keep signature parity
    // Hard-reload to a clean origin so the app re-boots and re-checks /me against
    // the server (the session is now invalidated). Guarantees logout "sticks"
    // even if any client state or a stale cookie lingers.
    try {
      window.location.assign("/");
    } catch {
      /* non-browser env (tests) */
    }
  },
}));

// ---- Clerk loader (only used when configured) ----
interface ClerkGlobal {
  load: () => Promise<void>;
  handleRedirectCallback: (opts: Record<string, unknown>) => Promise<void>;
  user?: unknown;
  session?: { getToken: () => Promise<string | null> };
  client?: {
    signIn: {
      authenticateWithRedirect: (o: {
        strategy: string;
        redirectUrl: string;
        redirectUrlComplete: string;
      }) => Promise<void>;
    };
  };
}

let clerkPromise: Promise<void> | null = null;
export function loadClerk(providers: Providers): Promise<void> {
  if ((window as unknown as { Clerk?: ClerkGlobal }).Clerk) return Promise.resolve();
  if (clerkPromise) return clerkPromise;
  clerkPromise = new Promise((resolve, reject) => {
    const sc = document.createElement("script");
    sc.async = true;
    sc.setAttribute("crossorigin", "anonymous");
    sc.setAttribute("data-clerk-publishable-key", providers.clerk_publishable_key);
    sc.src = `https://${providers.clerk_frontend_api}/npm/@clerk/clerk-js@5/dist/clerk.browser.js`;
    const to = setTimeout(() => reject(new Error("clerk load timeout")), 7000);
    sc.onload = () => {
      clearTimeout(to);
      resolve();
    };
    sc.onerror = () => {
      clearTimeout(to);
      reject(new Error("failed to load Clerk"));
    };
    document.head.appendChild(sc);
  });
  return clerkPromise;
}

export function clerkGoogleSignIn(): void {
  const Clerk = (window as unknown as { Clerk?: ClerkGlobal }).Clerk;
  const setErr = useAuth.getState().setAuthError;
  if (!Clerk?.client) {
    setErr("Google sign-in is still loading — try again in a moment.");
    return;
  }
  const back = location.origin + "/sso-callback";
  const done = location.origin + "/";
  Clerk.client.signIn
    .authenticateWithRedirect({
      strategy: "oauth_google",
      redirectUrl: back,
      redirectUrlComplete: done,
    })
    .catch((err: unknown) =>
      setErr("Could not start Google sign-in: " + (err instanceof Error ? err.message : String(err))),
    );
}
