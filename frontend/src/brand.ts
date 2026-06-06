// App branding. Defaults to "Posterly"; a deployment overrides it by setting
// BRAND_NAME / BRAND_TAGLINE in .env (compose passes them as VITE_* build args,
// baked into the SPA at build time -- like VITE_GOOGLE_CLIENT_ID). Rebuild the
// image after changing them.
export const BRAND_NAME = (import.meta.env.VITE_BRAND_NAME || "Posterly").trim();
export const BRAND_TAGLINE = (import.meta.env.VITE_BRAND_TAGLINE || "").trim();

/** e.g. "Posterly" or "Posterly Studio" — name plus optional tagline. */
export const BRAND_FULL = BRAND_TAGLINE
  ? `${BRAND_NAME} ${BRAND_TAGLINE}`
  : BRAND_NAME;
