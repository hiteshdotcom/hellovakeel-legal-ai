import { useState } from "react";
import { motion } from "framer-motion";
import { api, ApiError } from "@/lib/api";
import { useAuth, clerkGoogleSignIn } from "@/store/auth";
import { Scales, GoogleLogo, SpinnerGap } from "@/lib/icons";
import { Button } from "@/components/ui";
import { cn } from "@/lib/cn";

type Mode = "login" | "signup";

export default function AuthScreen() {
  const { providers, authError, clerkBusy, setUser, setAuthError } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const signup = mode === "signup";
  const googleReady = !!(providers?.clerk || providers?.google);

  async function submit() {
    if (busy) return;
    if (!email.trim() || !password) {
      setAuthError("Enter your email and password.");
      return;
    }
    if (signup && password.length < 8) {
      setAuthError("Password must be at least 8 characters.");
      return;
    }
    setAuthError("");
    setBusy(true);
    try {
      const { user } = signup
        ? await api.signup(email.trim(), password, name.trim())
        : await api.login(email.trim(), password);
      setUser(user);
    } catch (e) {
      setAuthError(
        e instanceof ApiError ? e.message : "Network error. Is the server running?",
      );
    } finally {
      setBusy(false);
    }
  }

  function onGoogle() {
    if (providers?.clerk) clerkGoogleSignIn();
    else if (providers?.google) window.location.href = api.googleLoginUrl();
  }

  if (clerkBusy) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center px-5">
        <div className="flex flex-col items-center gap-3.5 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-[13px] bg-navy">
            <Scales size={26} className="text-white" />
          </div>
          <div className="font-serif text-lg font-semibold">Completing sign-in…</div>
          <SpinnerGap size={22} className="animate-spin text-ink-3" />
        </div>
      </div>
    );
  }

  const field = (
    label: string,
    type: string,
    value: string,
    set: (v: string) => void,
    ph: string,
    autoComplete: string,
  ) => (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-semibold text-ink-2">{label}</span>
      <input
        type={type}
        value={value}
        placeholder={ph}
        autoComplete={autoComplete}
        onChange={(e) => set(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        className="w-full rounded-xl border border-divider bg-surface px-3.5 py-3 text-sm text-ink outline-none focus:border-gold"
      />
    </label>
  );

  return (
    <div className="flex min-h-[100dvh] items-center justify-center px-5 py-10">
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
        className="flex w-full max-w-[400px] flex-col gap-4 rounded-[18px] border border-divider bg-surface p-7 shadow-pop"
      >
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-[13px] bg-navy">
            <Scales size={26} className="text-white" />
          </div>
          <div>
            <div className="font-serif text-2xl font-semibold tracking-tight">
              Nyaya<span className="text-accent-ink">.AI</span>
            </div>
            <div className="mt-1 text-[13px] text-ink-2">
              {signup ? "Create your account" : "Sign in to your legal memory"}
            </div>
          </div>
        </div>

        <Button
          variant="secondary"
          onClick={onGoogle}
          disabled={!googleReady}
          className="h-11"
          ariaLabel="Continue with Google"
        >
          <GoogleLogo size={18} weight="bold" />
          Continue with Google
        </Button>
        {!googleReady && (
          <div className="-mt-1.5 text-center text-[11px] text-ink-3">
            Google sign-in activates once Clerk (or Google) is configured.
          </div>
        )}

        <div className="flex items-center gap-2.5 text-xs text-ink-3">
          <span className="h-px flex-1 bg-divider" />
          or
          <span className="h-px flex-1 bg-divider" />
        </div>

        <div className="flex flex-col gap-3">
          {signup && field("Name", "text", name, setName, "Your name", "name")}
          {field("Email", "email", email, setEmail, "you@example.com", "email")}
          {field(
            "Password",
            "password",
            password,
            setPassword,
            signup ? "At least 8 characters" : "Your password",
            signup ? "new-password" : "current-password",
          )}
        </div>

        {authError && (
          <div
            role="alert"
            className="rounded-lg border border-bad-bd bg-bad-bg px-3 py-2.5 text-[12.5px] leading-snug text-bad"
          >
            {authError}
          </div>
        )}

        <Button onClick={submit} disabled={busy} className="h-[46px] text-[15px]">
          {busy ? "Please wait…" : signup ? "Create account" : "Sign in"}
        </Button>

        <div className="text-center text-[13px] text-ink-2">
          {signup ? "Already have an account? " : "New here? "}
          <button
            className={cn("font-bold text-accent-ink", "hover:underline")}
            onClick={() => {
              setMode(signup ? "login" : "signup");
              setAuthError("");
            }}
          >
            {signup ? "Sign in" : "Create an account"}
          </button>
        </div>
        <div className="text-center text-[11px] leading-relaxed text-ink-3">
          Your judgments memory is private to your account.
        </div>
      </motion.div>
    </div>
  );
}
