import React from "react";
import ReactDOM from "react-dom/client";
import { GoogleOAuthProvider } from "@react-oauth/google";
import App from "./App";
import LoginScreen from "./components/LoginScreen";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { BRAND_FULL } from "./brand";
import "./styles.css";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

// Brand the browser tab from the configured name (default Posterly).
document.title = `${BRAND_FULL} — photo collage maker`;

/** Gate: resolve the session, then show the login screen or the tool. */
function Gate() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="app-loading">
        <div className="spinner" />
        <p>Loading…</p>
      </div>
    );
  }
  return user ? <App /> : <LoginScreen />;
}

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found");
}

const tree = (
  <AuthProvider>
    <Gate />
  </AuthProvider>
);

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    {GOOGLE_CLIENT_ID ? (
      <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>{tree}</GoogleOAuthProvider>
    ) : (
      tree
    )}
  </React.StrictMode>,
);
