export type Language = 'en' | 'mk';

// Vocabulary: a *folder* in the UI, because that is the word on every file
// manager a user has already met; `directory` stays in the code and in the
// contract. `reasons` carries exactly one entry per member of `END_REASONS` in
// `lib/end-reasons.ts`, and `files.errors` exactly one per `ContentsErrorKind`
// in `lib/contents-api.ts`, which is what `protocol-i18n.test.ts` checks.

const en = {
  actions: {
    cancel: 'Cancel',
    dismiss: 'Dismiss',
    download: 'Download files',
    downloadAll: 'Download files',
    downloadTgz: 'tar.gz archive',
    downloadZip: 'ZIP archive',
    theme: 'Change theme',
    upload: 'Upload files',
  },
  brand: 'FINKI Hub',
  files: {
    close: 'Close the file panel',
    columnModified: 'Modified',
    columnName: 'Name',
    columnSize: 'Size',
    create: 'Create',
    delete: 'Delete',
    deleteBody:
      'This cannot be undone. A folder is deleted with everything inside it.',
    deleteConfirm: 'Delete',
    deleteTitle: 'Delete this?',
    download: 'Download',
    downloadAll: 'Download everything',
    empty: 'This folder is empty.',
    errors: {
      aborted: 'The transfer was interrupted.',
      'bad-request': 'The request was not valid.',
      conflict: 'Something with that name is already there.',
      forbidden: 'The request was refused.',
      gone: 'The environment is no longer available.',
      'insufficient-storage': 'There is not enough space left.',
      malformed: 'The environment returned an invalid response.',
      network: 'The connection was lost.',
      'not-found': 'That file or folder no longer exists.',
      'rate-limited': 'Too many requests — retry in a minute.',
      'too-large': 'The file is too large.',
      unavailable: 'The environment is temporarily unavailable.',
    },
    home: 'Home',
    invalidName: 'A name cannot be empty or contain a slash.',
    loading: 'Loading…',
    name: 'Name',
    newFolder: 'New folder',
    open: 'Open folder',
    parent: 'Up one level',
    refresh: 'Refresh the list',
    rename: 'Rename',
    title: 'Files',
    toggle: 'File panel',
    uploadCancel: 'Cancel this upload',
    uploadClear: 'Clear finished uploads',
    uploaded: 'Uploaded',
    uploads: 'Uploads',
  },
  reasons: {
    capacity: 'No capacity for a new environment. Try again later.',
    'challenge-blocked':
      'Verification could not load: something on this network or in this browser is blocking challenges.cloudflare.com.',
    'challenge-failed': 'Verification failed. Try again.',
    'challenge-required':
      'The session ended: a verification check went unanswered. Your files are untouched.',
    'challenge-unanswered':
      'The verification was not completed in time. Nothing was lost — try again.',
    'connection-lost': 'The connection to the environment was lost.',
    'environment-expired':
      'This environment no longer exists. A new one will be started.',
    idle: 'The session ended due to inactivity. Your files are untouched.',
    'shell-exited': 'The terminal exited. Your files are untouched.',
    'start-failed': 'The environment could not be started.',
    'terminal-limit': 'Tab limit reached. Close another tab first.',
    unreachable: 'The server could not be reached. Check your connection.',
  },
  session: {
    challengeStartBody: 'Verify you are human to start an environment.',
    challengeTitle: 'Verification',
    challengeVerifying: 'Verifying…',
    connecting: 'connecting…',
    ended: 'ended',
    newEnvironmentBody:
      'This deletes the current environment and everything in it. Download anything you want to keep first.',
    newEnvironmentConfirm: 'Start a new one',
    newEnvironmentNotDownloaded: 'The old environment was deleted.',
    newEnvironmentTitle: 'Start a new environment?',
    resumed: 'Connected',
    running: 'running',
    starting: 'starting…',
    // The end screen's second button, for the reasons no reconnect can fix.
    startNew: 'Start a new environment',
    tryAgain: 'Try again',
  },
  storage: {
    files: 'files',
    full: 'Almost out of space — new files may fail to save. Delete something to make space.',
    title: 'Home directory usage',
  },
  title: 'Shell',
  upload: {
    dropHere: 'Drop files to upload them into the open folder',
    progress: 'Upload progress',
  },
} as const;

const mk = {
  actions: {
    cancel: 'Откажи',
    dismiss: 'Затвори',
    download: 'Преземи ги датотеките',
    downloadAll: 'Преземи ги датотеките',
    downloadTgz: 'tar.gz архива',
    downloadZip: 'ZIP архива',
    theme: 'Промени тема',
    upload: 'Прикачи датотеки',
  },
  brand: 'ФИНКИ Хаб',
  files: {
    close: 'Затвори го панелот со датотеки',
    columnModified: 'Изменето',
    columnName: 'Име',
    columnSize: 'Големина',
    create: 'Создај',
    delete: 'Избриши',
    deleteBody:
      'Ова не може да се врати. Папката се брише заедно со сè во неа.',
    deleteConfirm: 'Избриши',
    deleteTitle: 'Да се избрише ова?',
    download: 'Преземи',
    downloadAll: 'Преземи сè',
    empty: 'Оваа папка е празна.',
    errors: {
      aborted: 'Преносот беше прекинат.',
      'bad-request': 'Барањето не е валидно.',
      conflict: 'Веќе постои нешто со тоа име.',
      forbidden: 'Барањето беше одбиено.',
      gone: 'Околината повеќе не е достапна.',
      'insufficient-storage': 'Нема доволно преостанат простор.',
      malformed: 'Околината врати невалиден одговор.',
      network: 'Врската беше изгубена.',
      'not-found': 'Таа датотека или папка повеќе не постои.',
      'rate-limited': 'Премногу барања — обидете се повторно за минута.',
      'too-large': 'Датотеката е преголема.',
      unavailable: 'Околината е привремено недостапна.',
    },
    home: 'Дома',
    invalidName: 'Името не смее да биде празно ниту да содржи коса црта.',
    loading: 'Се вчитува…',
    name: 'Име',
    newFolder: 'Нова папка',
    open: 'Отвори ја папката',
    parent: 'Едно ниво погоре',
    refresh: 'Освежи ја листата',
    rename: 'Преименувај',
    title: 'Датотеки',
    toggle: 'Панел со датотеки',
    uploadCancel: 'Откажи го ова прикачување',
    uploadClear: 'Исчисти ги завршените прикачувања',
    uploaded: 'Прикачено',
    uploads: 'Прикачувања',
  },
  reasons: {
    capacity: 'Нема капацитет за нова околина. Обидете се подоцна.',
    'challenge-blocked':
      'Проверката не може да се вчита: нешто на мрежата или во прелистувачот го блокира challenges.cloudflare.com.',
    'challenge-failed': 'Проверката не успеа. Обидете се повторно.',
    'challenge-required':
      'Сесијата заврши: проверката остана без одговор. Датотеките се недопрени.',
    'challenge-unanswered':
      'Проверката не беше завршена навреме. Ништо не е изгубено — обидете се повторно.',
    'connection-lost': 'Врската со околината беше изгубена.',
    'environment-expired':
      'Оваа околина повеќе не постои. Ќе биде подигната нова.',
    idle: 'Сесијата заврши поради неактивност. Датотеките се недопрени.',
    'shell-exited': 'Терминалот заврши. Датотеките се недопрени.',
    'start-failed': 'Околината не можеше да се подигне.',
    'terminal-limit':
      'Достигнат е лимитот на јазичиња. Прво затворете едно од другите.',
    unreachable: 'Серверот е недостапен. Проверете ја врската.',
  },
  session: {
    challengeStartBody: 'Потврдете дека сте човек за да започне околина.',
    challengeTitle: 'Потврда',
    challengeVerifying: 'Се потврдува…',
    connecting: 'поврзување…',
    ended: 'завршена',
    newEnvironmentBody:
      'Ова ја брише тековната околина и сè во неа. Прво преземете го тоа што сакате да го задржите.',
    newEnvironmentConfirm: 'Започни нова',
    newEnvironmentNotDownloaded: 'Старата околина е избришана.',
    newEnvironmentTitle: 'Да започне нова околина?',
    resumed: 'Поврзано',
    running: 'активна',
    starting: 'подигање…',
    startNew: 'Започни нова околина',
    tryAgain: 'Обиди се повторно',
  },
  storage: {
    files: 'датотеки',
    full: 'Речиси нема простор — новите датотеки може да не се зачуваат. Избришете нешто за да ослободите простор.',
    title: 'Искористеност на домашниот директориум',
  },
  title: 'Школка',
  upload: {
    dropHere: 'Пуштете датотеки за да ги прикачите во отворената папка',
    progress: 'Напредок на прикачувањето',
  },
} as const;

export const translations = { en, mk } as const;

export type Translations = (typeof translations)[Language];
