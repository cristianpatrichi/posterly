import logoUrl from "../assets/mark.svg";
import { BRAND_NAME, BRAND_TAGLINE, BRAND_FULL } from "../brand";

export default function BrandLogo() {
  return (
    <div className="brand" aria-label={BRAND_FULL}>
      <img src={logoUrl} alt="" className="brand-mark" aria-hidden="true" />
      <span className="brand-text">
        <span className="brand-name">{BRAND_NAME}</span>
        {BRAND_TAGLINE && <span className="brand-tagline">{BRAND_TAGLINE}</span>}
      </span>
    </div>
  );
}
