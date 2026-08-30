import { useLanguage } from '@/hooks/useLanguage';

// Use native progress semantics; vendor styling is in index.css.
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
