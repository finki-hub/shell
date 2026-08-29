import { useLanguage } from '@/hooks/useLanguage';

// A native <progress>, so the value reaches assistive technology and the
// platform's own semantics without a div pretending to be a control. The bar
// itself is styled in index.css, which is the only place the vendor
// pseudo-elements can be reached.
export const UploadProgress = ({ value }: { readonly value: number }) => {
  const { t } = useLanguage();

  return (
    <progress
      aria-label={t.upload.progress}
      className="upload-progress"
      max={100}
      value={Math.round(value * 100)}
    />
  );
};
