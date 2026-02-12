/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_API_USER: string;
  readonly VITE_API_PASS: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
