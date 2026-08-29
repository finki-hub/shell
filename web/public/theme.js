(() => {
  const storageKey = 'theme';
  const isTheme = (value) => value === 'dark' || value === 'light';
  let theme;

  try {
    const stored = localStorage.getItem(storageKey);
    if (isTheme(stored)) {
      theme = stored;
    }
  } catch {
    theme = undefined;
  }

  if (theme === undefined) {
    try {
      theme = matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light';
    } catch {
      theme = 'light';
    }
  }

  document.documentElement.dataset.kbTheme = theme;
})();
