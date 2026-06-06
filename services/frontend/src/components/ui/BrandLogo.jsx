// Disabled for text-only branding pass:
// import logoBlack from '../../styles/images/black.svg';
// import logoWhite from '../../styles/images/white.svg';

export default function BrandLogo({ className = '', alt = 'VISUS VidLab logo' }) {
  return (
    <span className={className} aria-label={alt}>
      VV
    </span>
  );
}
