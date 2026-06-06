import { useState } from "react";
import type { FormEvent } from "react";
import { GoogleLogin } from "@react-oauth/google";
import logoUrl from "../assets/mark.svg";
import * as api from "../api";
import { ApiError } from "../api";
import { useAuth } from "../auth/AuthContext";
import { BRAND_FULL } from "../brand";
import Turnstile from "./Turnstile";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;
const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;

/** Sign-in screen: Google one-click + passwordless email OTP (Resend). */
export default function LoginScreen() {
  const { setUser } = useAuth();
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  // Cloudflare Turnstile token (when configured). tsKey remounts the widget to
  // get a fresh token after one is consumed/failed (tokens are single-use).
  const [tsToken, setTsToken] = useState<string | null>(null);
  const [tsKey, setTsKey] = useState(0);
  const needTurnstile = !!TURNSTILE_SITE_KEY;

  async function handleGoogle(credential?: string) {
    if (!credential) return;
    if (needTurnstile && !tsToken) {
      setError("Please complete the anti-bot check first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { email: em } = await api.googleLogin(credential, tsToken);
      setUser(em);
    } catch (e) {
      setError(describe(e));
      setTsToken(null);
      setTsKey((k) => k + 1);
    } finally {
      setBusy(false);
    }
  }

  async function requestCode(e: FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    if (needTurnstile && !tsToken) {
      setError("Please complete the anti-bot check first.");
      return;
    }
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      await api.otpRequest(email.trim(), tsToken);
      setStep("code");
      setInfo("If that email is allowed, an 8-digit code is on its way.");
    } catch (e) {
      setError(describe(e));
      setTsToken(null);
      setTsKey((k) => k + 1);
    } finally {
      setBusy(false);
    }
  }

  async function verifyCode(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { email: em } = await api.otpVerify(email.trim(), code.trim());
      setUser(em);
    } catch {
      setError("Invalid or expired code.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <img className="login-logo" src={logoUrl} alt="" />
        <h1 className="login-title">{BRAND_FULL}</h1>
        <p className="login-sub">Sign in to create and keep your collages.</p>

        {needTurnstile && step === "email" ? (
          <Turnstile
            key={tsKey}
            siteKey={TURNSTILE_SITE_KEY as string}
            onToken={setTsToken}
          />
        ) : null}

        {GOOGLE_CLIENT_ID ? (
          <div className="login-google">
            <GoogleLogin
              onSuccess={(res) => handleGoogle(res.credential)}
              onError={() => setError("Google sign-in failed.")}
              theme="filled_blue"
              shape="pill"
              width="280"
            />
          </div>
        ) : null}

        {GOOGLE_CLIENT_ID ? (
          <div className="login-divider">
            <span>or</span>
          </div>
        ) : null}

        {step === "email" ? (
          <form onSubmit={requestCode} className="login-form">
            <input
              type="email"
              autoComplete="email"
              placeholder="you@email.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <button
              className="btn btn-primary login-submit"
              disabled={busy || (needTurnstile && !tsToken)}
            >
              {busy ? "Sending…" : "Email me a code"}
            </button>
          </form>
        ) : (
          <form onSubmit={verifyCode} className="login-form">
            <input
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={8}
              autoComplete="one-time-code"
              placeholder="8-digit code"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              required
            />
            <button className="btn btn-primary login-submit" disabled={busy}>
              {busy ? "Verifying…" : "Sign in"}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setStep("email");
                setCode("");
                setInfo(null);
              }}
            >
              Use a different email
            </button>
          </form>
        )}

        {info && <p className="login-info">{info}</p>}
        {error && <p className="login-error">{error}</p>}
      </div>
    </div>
  );
}

function describe(e: unknown): string {
  if (e instanceof ApiError && e.status === 403) {
    return "This email isn't allowed to sign in.";
  }
  return "Sign-in failed. Please try again.";
}
